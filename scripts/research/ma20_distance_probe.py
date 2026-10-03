#!/usr/bin/env python3
"""距離 20 日均線的位階與方向(2026-10-03)。

jack 問四件事:要找離 MA20 比較遠的、還是還沒那麼遠的?要找正在靠近的、還是正在遠離的?
本檔把這四種拆成「水準 × 方向」的 2×2 來測。

PIT:MA20 用 **≤ 前一交易日** 的 20 根收盤算,不含當日,避免用到未來資訊。
基準:超額仍用全市場同「日×分鐘」格 —— 族群基準做不出來(見下)。

⚠ 族群基準的資料限制(2026-10-03 查核):tick 宇宙 100 檔只有 40 檔有 SUBCAT 分類,
  分成 31 個族群、**最大的族群只有 3 檔**、沒有任何族群 ≥8 檔。用 1~3 檔當基準,
  噪音遠大於訊號。另 [[tw36-residual-clusters-us-link]] 已證「半導體業標籤無效」
  (扣大盤後平均相關掉 61%)。故本輪維持全市場基準。

用法:PYTHONPATH=src .venv/bin/python scripts/research/ma20_distance_probe.py [--days 20]
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

from stock_db import DEFAULT_DB_PATH

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OPEN_S, CLOSE_S = 9 * 3600, 13 * 3600 + 25 * 60
BIG_NTD, LOT = 1_000_000.0, 1000.0
HOLD = (5, 10, 30, 60)


def _sec(t):
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + float(t[6:])


def load(sid, date):
    f = TICK / f"{sid}_{date}.json"
    if not f.exists():
        return None
    try:
        arr = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    t, px, vol, ty = [], [], [], []
    for x in arr:
        tt = x.get("Time") or ""
        k = str(x.get("TickType", "0"))
        if len(tt) < 8 or k not in ("1", "2"):
            continue
        s = _sec(tt)
        if s < OPEN_S or s > CLOSE_S:
            continue
        t.append(s); px.append(float(x["deal_price"])); vol.append(float(x.get("volume") or 0))
        ty.append(1 if k == "1" else 2)
    if len(t) < 300:
        return None
    t = np.asarray(t); o = np.argsort(t, kind="stable")
    return t[o], np.asarray(px)[o], np.asarray(vol)[o], np.asarray(ty, dtype=np.int8)[o]


def ratio(t, amt, sel, win):
    cs = np.concatenate([[0.0], np.cumsum(np.where(sel, amt, 0.0))])
    ca = np.concatenate([[0.0], np.cumsum(amt)])
    lo = np.searchsorted(t, t - win, side="left"); hi = np.arange(len(t)) + 1
    tot = ca[hi] - ca[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(tot > 0, (cs[hi] - cs[lo]) / tot, np.nan), hi - lo


def compress(t, px, win):
    lo = np.searchsorted(t, t - win, side="left"); hi = np.arange(len(t)) + 1
    out = np.full(len(t), np.nan)
    for i in range(len(t)):
        a, b = lo[i], hi[i]
        if b - a < 3:
            continue
        seg = px[a:b]; mx, mn = seg.max(), seg.min(); mid = (mx + mn) / 2
        if mid > 0:
            out[i] = (mx - mn) / mid * 1e4
    return out


def load_ma20(sids, dates):
    """PIT 的 MA20 與前一日收盤:只用 < 當日 的 20 根。回傳 {(sid,date): (ma20, prev_close)}。"""
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    qs = ",".join("?" * len(sids))
    rows = con.execute(
        f"select stock_id,trade_date,close from stock_daily_bars where stock_id in ({qs}) "
        f"and trade_date>=date(?, '-70 day') and trade_date<=? order by stock_id,trade_date",
        (*sids, dates[0], dates[-1])).fetchall()
    series: dict[str, list] = {}
    for sid, d, c in rows:
        if c and c > 0:
            arr = series.setdefault(sid, [])
            if not arr or arr[-1][0] != d:
                arr.append((d, float(c)))
    out = {}
    for sid, arr in series.items():
        for i, (d, _c) in enumerate(arr):
            if i >= 20:
                prev = [c for _d, c in arr[i - 20:i]]
                out[(sid, d)] = (float(np.mean(prev)), arr[i - 1][1])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=20)
    args = ap.parse_args()
    files = sorted(TICK.glob("*_2026-*.json"))
    dates = sorted({f.name.split("_")[-1][:-5] for f in files})[-args.days:]
    sids = sorted({f.name.split("_")[0] for f in files})
    ma = load_ma20(sids, dates)
    print(f"取樣 {len(dates)} 日 × {len(sids)} 檔;有 PIT MA20 的 (檔,日) 組合 {len(ma):,}", flush=True)

    keys = ("dist", "move", "rs5", "d_rs", "cp", "n1", "day", "sec")
    cols = {k: [] for k in keys}
    FWD = {m: [] for m in HOLD}
    pairs = 0
    for d in dates:
        for sid in sids:
            key = (sid, d)
            if key not in ma:
                continue
            tk = load(sid, d)
            if tk is None:
                continue
            ma20, prev_close = ma[key]
            if ma20 <= 0 or prev_close <= 0:
                continue
            pairs += 1
            t, px, vol, ty = tk
            amt = px * vol * LOT
            big = amt >= BIG_NTD
            retail = (vol == 1) & ~big
            rs5, _ = ratio(t, amt, retail, 300); rs30, _ = ratio(t, amt, retail, 1800)
            _r1, n1 = ratio(t, amt, ty == 1, 60)
            cp = compress(t, px, 300)
            dist = (px / ma20 - 1) * 100            # 距 MA20 %，正=在線上
            today = (px / prev_close - 1) * 100     # 當日至今漲跌 %
            # 方向:同號=遠離(往原方向走),異號=靠近(往線回走)
            move = np.sign(dist) * np.sign(today)
            keep = np.concatenate([[True], np.diff(np.floor(t / 10)) > 0])
            m = keep & np.isfinite(cp) & np.isfinite(rs5)
            idx = np.where(m)[0]
            if not len(idx):
                continue
            for k, v in (("dist", dist), ("move", move), ("rs5", rs5), ("d_rs", rs5 - rs30),
                         ("cp", cp), ("n1", n1), ("sec", t)):
                cols[k].append(v[m])
            cols["day"].append(np.full(len(idx), dates.index(d), dtype=np.int16))
            for mins in HOLD:
                tgt = np.searchsorted(t, t[idx] + mins * 60, side="left")
                ok = tgt < len(t)
                f = np.full(len(idx), np.nan)
                f[ok] = (px[tgt[ok]] / px[idx[ok]] - 1) * 1e4
                FWD[mins].append(f)
    X = {k: np.concatenate(v) for k, v in cols.items()}
    fwd = {k: np.concatenate(v) for k, v in FWD.items()}
    key = X["day"].astype(np.int64) * 100000 + (X["sec"] // 60).astype(np.int64)
    exf = {}
    for mins, v in fwd.items():
        ok = np.isfinite(v)
        o = np.argsort(key[ok], kind="stable")
        k2 = key[ok][o]; v2 = v[ok][o]
        b = np.concatenate([[0], np.flatnonzero(np.diff(k2)) + 1, [len(k2)]])
        mu = np.empty(len(k2))
        for i in range(len(b) - 1):
            mu[b[i]:b[i + 1]] = v2[b[i]:b[i + 1]].mean()
        base = np.full(len(v), np.nan)
        base[np.flatnonzero(ok)[o]] = mu
        exf[mins] = v - base
    liq = X["n1"] >= 10
    q_cp = np.nanpercentile(X["cp"][liq], 25)
    quiet = liq & (X["cp"] <= q_cp)
    print(f"檔日 {pairs}、取樣點 {len(liq):,}（活躍 {liq.sum():,}、盤整 {quiet.sum():,}）\n")

    print("==== A. 距 MA20 的「水準」：遠 vs 近（活躍樣本，超額 bps）====")
    print(f"   {'距 MA20 %':<20}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lo, hi_ in ((-1e9, -10), (-10, -5), (-5, 0), (0, 5), (5, 10), (10, 20), (20, 1e9)):
        m = liq & (X["dist"] > lo) & (X["dist"] <= hi_)
        if m.sum() < 500:
            continue
        print(f"   ({lo:+.0f},{hi_:+.0f}]".ljust(23) + f"{m.sum():>9,}"
              + "".join(f"{np.nanmean(exf[mins][m]):>9.2f}" for mins in HOLD))

    print("\n==== B. 「方向」：正在靠近 vs 正在遠離 ====")
    for lab, mv in (("遠離（同號：往原方向走）", 1), ("靠近（異號：往線回走）", -1)):
        m = liq & (X["move"] == mv)
        print(f"   {lab:<24} n={m.sum():>9,}  " + "".join(f"{np.nanmean(exf[mins][m]):>9.2f}" for mins in HOLD))

    print("\n==== C. 2×2：水準 × 方向（活躍樣本）====")
    print(f"   {'象限':<30}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lab, m in (("線上(>0) ∧ 遠離（續漲）", liq & (X["dist"] > 0) & (X["move"] == 1)),
                   ("線上(>0) ∧ 靠近（回測）", liq & (X["dist"] > 0) & (X["move"] == -1)),
                   ("線下(<0) ∧ 遠離（續跌）", liq & (X["dist"] < 0) & (X["move"] == 1)),
                   ("線下(<0) ∧ 靠近（反彈）", liq & (X["dist"] < 0) & (X["move"] == -1))):
        if m.sum() < 500:
            continue
        print(f"   {lab:<30}{m.sum():>9,}" + "".join(f"{np.nanmean(exf[mins][m]):>9.2f}" for mins in HOLD))

    print("\n==== D. 疊到前一輪最好的組合上（盤整 ∧ 散戶退場）====")
    rs_dn = X["d_rs"] <= np.nanpercentile(X["d_rs"][quiet], 20)
    base_m = quiet & rs_dn
    print(f"   {'組合':<34}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lab, m in (("盤整 ∧ 散戶退場（前輪基準）", base_m),
                   ("  + 線上 ∧ 靠近", base_m & (X["dist"] > 0) & (X["move"] == -1)),
                   ("  + 線上 ∧ 遠離", base_m & (X["dist"] > 0) & (X["move"] == 1)),
                   ("  + 線下 ∧ 靠近", base_m & (X["dist"] < 0) & (X["move"] == -1)),
                   ("  + 距線 0~5%", base_m & (X["dist"] > 0) & (X["dist"] <= 5)),
                   ("  + 距線 >10%", base_m & (X["dist"] > 10))):
        if m.sum() < 300:
            print(f"   {lab:<34}{m.sum():>9,}  n<300"); continue
        print(f"   {lab:<34}{m.sum():>9,}" + "".join(f"{np.nanmean(exf[mins][m]):>9.2f}" for mins in HOLD))
    print("\n   成本:掛買一等 22 bps;不排隊吃賣一約 41 bps。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
