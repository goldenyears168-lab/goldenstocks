#!/usr/bin/env python
"""個股期貨『校正合理價』狀態空間模型（Hasbrouck 1993 / Menkveld-Koopman-Lucas 2007 式）。

規格與驗證標準：docs/futures-fairvalue-prereg-20261020.md（先讀它，不要事後改門檻）。

模型（單位 bps，1 秒網格，沒觀測的秒只預測不更新）：
  狀態 z=[x, b]；x=期貨有效價 ln，b=期貨減現股的基差。
  x_t = x_{t-1} + beta*dlnTX_t + eta(q)      b_t = b_{t-1} + eps(qb)
  觀測1: 買賣簿中價 = x + e(R)               觀測2: 現股價 = x - b + e(R2)
參數 (beta,q,R,qb,R2) 逐檔最大概似估計（預測誤差分解）。

子命令：
  fit      用截止日(含)以前的資料重估並凍結參數 → $DATA/cache/fairvalue/params_<asof>.json
  forward  對凍結日之後的日盤做前向驗證，印預註冊的通過條件（觸發：台指自上次報價≥4bps 且空窗≥10s）
唯讀，不碰 DB / 下單；只讀 cache/ 的逐筆檔。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from multiprocessing import Pool

import numpy as np

CACHE = os.path.join(os.path.expanduser("~/goldenstocks-data"), "cache")
TZ = timezone(timedelta(hours=8))
TRIGGER_BPS = 4.0
MIN_GAP = 10
N_SEC = 4 * 3600 + 24 * 60 + 45  # 09:00:15 – 13:24:59
PARAM_NAMES = ("beta", "q", "R", "qb", "R2")


def universe() -> dict[str, str]:
    """sid -> 期貨逐筆目錄 root（與 biglot_dashboard.FUT_ROOT 同邏輯）。"""
    calib = json.load(open(os.path.join(CACHE, "..", "data", "cache", "pit_universe_tick", "_live_calib.json")))
    have = {os.path.basename(p)[: -len("_books")] for p in glob.glob(f"{CACHE}/*_books")}
    out = {}
    for r in calib["universe"]:
        fc = r.get("fut_code")
        if not fc:
            continue
        cands = [fc.lower()] + ([fc[:-1].lower()] if fc.endswith("F") and len(fc) == 3 else [])
        root = "sf" if fc == "SFF" else next((c for c in cands if c in have), None)
        if root:
            out[r["sid"]] = root
    return out


def _last_per_sec(ts, vs, t0):
    a = np.full(N_SEC, np.nan)
    for t, v in zip(ts, vs):
        i = int(t - t0)
        if 0 <= i < N_SEC:
            a[i] = v
    return a


def load_day(day: str, sids: dict[str, str]):
    """回傳 {sid: (tx_ln, mid_ln, spot_ln)}，皆為 N_SEC 長 1 秒網格（mid/spot 無觀測為 nan）。"""
    t0 = datetime.fromisoformat(day).replace(tzinfo=TZ, hour=9, minute=0, second=15).timestamp()
    p = f"{CACHE}/txf_trades/txf_trades_{day}.jsonl"
    if not os.path.exists(p):
        return {}
    tx = []
    for line in open(p):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = r.get("trade_time")
        if r.get("quote_type") == "FUTURE_AH" or not isinstance(t, (int, float)):
            continue
        b, a = r.get("bid"), r.get("ask")
        tx.append((t / 1e6, (b + a) / 2 if b and a and a >= b else r["price"]))
    tx.sort()
    if len(tx) < 1000:
        return {}
    tt = np.array([x[0] for x in tx]); tm = np.array([x[1] for x in tx])
    idx = np.searchsorted(tt, t0 + np.arange(N_SEC), side="right") - 1
    if (idx < 0).any():
        return {}
    txln = np.log(tm[idx])
    spot: dict[str, list] = {s: [] for s in sids}
    rawp = f"{CACHE}/biglot_live_watch/raw_{day}.jsonl"
    if os.path.exists(rawp):
        for line in open(rawp):
            if '"EQUITY"' not in line or '"trades"' not in line:
                continue
            try:
                d = (json.loads(line).get("payload") or {}).get("data") or {}
            except ValueError:
                continue
            s = d.get("symbol")
            if s in spot and not d.get("isTrial") and d.get("price") and d.get("time"):
                spot[s].append((d["time"] / 1e6, math.log(d["price"])))
    out = {}
    for sid, root in sids.items():
        bp = f"{CACHE}/{root}_books/{root}_books_{day}.jsonl"
        if not os.path.exists(bp):
            continue
        rows, sym = [], None
        for line in open(bp):
            if '"day"' not in line or '"stale": true' in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b, a, t = r.get("bids"), r.get("asks"), r.get("book_time")
            if r.get("session") != "day" or r.get("stale") or not b or not a or not t:
                continue
            sym = sym or r["symbol"]
            if r["symbol"] != sym or b[0]["price"] >= a[0]["price"] or b[0]["size"] <= 0 or a[0]["size"] <= 0:
                continue
            rows.append((t / 1e6, math.log((b[0]["price"] + a[0]["price"]) / 2)))
        if len(rows) < 50:
            continue
        rows.sort()
        sp = sorted(spot[sid])
        out[sid] = (txln, _last_per_sec([r[0] for r in rows], [r[1] for r in rows], t0),
                    _last_per_sec([x[0] for x in sp], [x[1] for x in sp], t0))
    return out


class Filter:
    """K2 逐秒前進的狀態空間濾波（離線 run() 與儀表板即時顯示共用，單位 bps）。"""

    def __init__(self, par):
        self.beta, self.q, self.R, self.qb, self.R2 = par
        self.x = None
        self.p11, self.p12, self.p22, self.b, self.bset = 1e4, 0.0, 1e4, 0.0, False
        self.last = -10**9      # 最近一次買賣簿觀測的秒索引
        self.prev_mid = 0.0     # 最近一次買賣簿中價(bps ln)
        self.tx_at = 0.0        # 該次觀測時的台指 ln

    def step(self, i, dtx_bps, tx_ln, m, s):
        """前進一秒。m/s=該秒買賣簿中價/現股價的 ln（無觀測為 nan）。
        回傳 (event, 對數概似增量)；event=(空窗秒, 預測誤差, 與上次中價差, 台指累計變動bps) 僅在買賣簿更新時給。"""
        ev = None; ll = 0.0
        if self.x is not None:
            self.x += self.beta * dtx_bps; self.p11 += self.q; self.p22 += self.qb
        if m == m:
            m *= 1e4
            if self.x is None:
                self.x = m; self.p11 = self.R
            else:
                S = self.p11 + self.R; v = m - self.x
                ev = (i - self.last, v, m - self.prev_mid, (tx_ln - self.tx_at) * 1e4)
                ll -= 0.5 * (math.log(S) + v * v / S)
                k1 = self.p11 / S; k2 = self.p12 / S
                self.x += k1 * v; self.p22 -= k2 * self.p12; self.p12 -= k2 * self.p11; self.p11 -= k1 * self.p11
            self.last = i; self.prev_mid = m; self.tx_at = tx_ln
        if self.x is not None and s == s:
            s *= 1e4
            if not self.bset:
                self.b = self.x - s; self.p22 = self.R2 + self.p11; self.p12 = 0.0; self.bset = True
            else:
                v = s - (self.x - self.b); S = self.p11 - 2 * self.p12 + self.p22 + self.R2
                ll -= 0.5 * (math.log(S) + v * v / S)
                a1 = (self.p11 - self.p12) / S; a2 = (self.p12 - self.p22) / S
                self.x += a1 * v; self.b += a2 * v
                self.p11, self.p12, self.p22 = (self.p11 - a1 * a1 * S, self.p12 - a1 * a2 * S, self.p22 - a2 * a2 * S)
        return ev, ll


def run(par, tx, mid, sp, collect=False):
    """離線一遍。collect=True 時回傳『空窗>=5s 後的買賣簿更新』事件。"""
    f = Filter(par); ll = 0.0; out = []
    for i in range(1, len(tx)):
        ev, d = f.step(i, (tx[i] - tx[i - 1]) * 1e4, tx[i], mid[i], sp[i])
        ll += d
        if collect and ev and ev[0] >= 5:
            out.append(ev)
    return ll, out


def fit_one(args):
    from scipy.optimize import minimize
    sid, grids, init = args
    def nll(lp):
        par = (lp[0], *(math.exp(v) for v in lp[1:]))
        return -sum(run(par, *g)[0] for g in grids)
    best = None
    for x0 in init:
        lp0 = [x0[0], *(math.log(v) for v in x0[1:])]
        r = minimize(nll, lp0, method="Nelder-Mead", options=dict(maxiter=100, xatol=1e-2, fatol=1e-1))
        if best is None or r.fun < best.fun:
            best = r
    return sid, [best.x[0], *(math.exp(v) for v in best.x[1:])], float(best.fun)


def _days_upto(asof: str):
    ds = sorted(os.path.basename(p)[len("txf_trades_"): -len(".jsonl")] for p in glob.glob(f"{CACHE}/txf_trades/txf_trades_20??-??-??.jsonl"))
    return [d for d in ds if d >= "2026-09-03" and datetime.fromisoformat(d).weekday() < 5]


def cmd_fit(asof: str, ndays: int, workers: int):
    sids = universe()
    days = [d for d in _days_upto(asof) if d <= asof]
    pick = [days[int(i)] for i in np.linspace(0, len(days) - 1, ndays)]
    per = {s: [] for s in sids}
    for d in pick:
        for sid, g in load_day(d, sids).items():
            per[sid].append(g)
        print("loaded", d, flush=True)
    init = [(0.4, 0.1, 4.0, 0.01, 25.0), (0.76, 12.0, 9.0, 3.0, 55.0)]
    jobs = [(s, g, init) for s, g in per.items() if len(g) >= 2]
    with Pool(workers) as p:
        res = p.map(fit_one, jobs, chunksize=1)
    params = {sid: dict(zip(PARAM_NAMES, v), nll=n, days=len(per[sid]), lowconf=False) for sid, v, n in res}
    med = {k: float(np.median([p[k] for p in params.values()])) for k in PARAM_NAMES}
    for sid in sids:
        if sid not in params:
            params[sid] = dict(med, nll=None, days=0, lowconf=True)
    out = os.path.join(CACHE, "fairvalue"); os.makedirs(out, exist_ok=True)
    path = f"{out}/params_{asof}.json"
    json.dump(dict(asof=asof, fit_days=pick, median=med, params=params), open(path, "w"), indent=1)
    print("saved", path, "median", med)


def _fwd_day(args):
    day, params = args
    sids = universe(); rows = []
    data = load_day(day, sids)
    for sid, (tx, mid, sp) in data.items():
        p = params["params"][sid]
        _, o = run([p[k] for k in PARAM_NAMES], tx, mid, sp, collect=True)
        for gap, v, a, dtx in o:
            if gap >= MIN_GAP and abs(dtx) >= TRIGGER_BPS and abs(a) < 300:
                rows.append((day, sid, gap, v, a, p["lowconf"]))
    return rows, bool(data)


def cmd_forward(params_path: str, workers: int):
    params = json.load(open(params_path))
    days = [d for d in _days_upto("9999") if d > params["asof"]]
    if not days:
        print("凍結日之後還沒有日盤資料"); return
    with Pool(workers) as p:
        byday = p.map(_fwd_day, [(d, params) for d in days])
    days = [d for d, (rs, ok) in zip(days, byday) if ok]   # 只算真的有日盤資料的日子
    allr = [r for rs, _ in byday for r in rs]
    if not days:
        print("凍結日之後還沒有日盤資料"); return
    print(f"凍結日 {params['asof']}；前向日盤 {len(days)} 個（預註冊要求 ≥10）：{days[0]} ~ {days[-1]}；觸發事件 {len(allr)}")
    if not allr:
        return
    rm = lambda e: math.sqrt(float(np.mean(np.square(e))))
    kal = np.array([r[3] for r in allr]); base = np.array([r[4] for r in allr])
    pooled = 1 - rm(kal) / rm(base)
    print(f"池化 RMSE：買賣簿中價(不校正) {rm(base):.2f} bps → K2 {rm(kal):.2f} bps，降幅 {pooled:+.1%}（門檻 ≥10%）")
    per = {}
    for r in allr:
        per.setdefault(r[1], []).append((r[3], r[4]))
    wins = tot = 0
    for sid, v in sorted(per.items()):
        if len(v) < 30:
            continue
        k, a = np.array(v).T; tot += 1; wins += rm(k) < rm(a)
    frac = wins / tot if tot else float("nan")
    print(f"有 ≥30 觸發事件的檔 {tot} 檔，改善 {wins} 檔（{frac:.0%}；門檻 ≥60%）")
    ok = pooled >= 0.10 and tot > 0 and frac >= 0.60
    print("判決：", "通過（可上儀表板，標『前向驗證 %d 日』）" % len(days) if ok else "未通過（只顯示買賣簿中價與偏離 bps）")
    if len(days) < 10:
        print("⚠ 日盤不足 10 個，只是中途檢視，不得當作通過/失敗的最終判決")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit"); f.add_argument("--asof", required=True); f.add_argument("--ndays", type=int, default=5); f.add_argument("--workers", type=int, default=3)
    w = sub.add_parser("forward"); w.add_argument("--params", required=True); w.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    cmd_fit(a.asof, a.ndays, a.workers) if a.cmd == "fit" else cmd_forward(a.params, a.workers)
