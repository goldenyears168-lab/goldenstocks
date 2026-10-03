#!/usr/bin/env python3
"""距 MA20 的**標準化**位階與「舊乖離 vs 今日變動」拆解(2026-10-03)。

修正前一輪(ma20_distance_probe.py)的兩個方法論問題,由 jack 指出:

1. **固定 % 門檻跨個股不可比** —— 日波動 1% 的股票走到 +20% 乖離是極端事件,
   日波動 5% 的只是普通一週。這正是 [[zscore-normalized-spike-reversion]] 記過的
   「真盲點=固定%門檻跨時段不可比」。本檔用三種標準化重測:
     z_sd   dist / (前 20 日日報酬 sd × sqrt(20))
     z_atr  dist / (ATR20 / 價格)
     pctile dist 在該檔前 60 日 dist 分布中的分位
2. **當下乖離混了「舊的」與「新的」** —— dist(當下) ≈ dist(昨收) + 今日至今漲跌。
   昨收就 +20% 今天盤整(舊乖離) vs 昨收 +10% 今天漲停(新乖離),性質完全不同,
   而且後者很可能正在漲停鎖死、**根本放空不了**。本檔拆成 2D 並標出漲停附近比例。

MA20/sd/ATR 全部 PIT:只用 ≤ 前一交易日的資料。
用法:PYTHONPATH=src .venv/bin/python scripts/research/ma20_normalized_probe.py [--days 20]
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
HOLD = (5, 10, 30, 60)
LIMIT_UP = 9.5          # 今日漲跌 ≥ 此值視為漲停附近(台股 ±10%)


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
    t, px, vol = [], [], []
    for x in arr:
        tt = x.get("Time") or ""
        if len(tt) < 8 or str(x.get("TickType", "0")) not in ("1", "2"):
            continue
        s = _sec(tt)
        if s < OPEN_S or s > CLOSE_S:
            continue
        t.append(s); px.append(float(x["deal_price"])); vol.append(float(x.get("volume") or 0))
    if len(t) < 300:
        return None
    t = np.asarray(t); o = np.argsort(t, kind="stable")
    return t[o], np.asarray(px)[o], np.asarray(vol)[o]


def load_daily(sids, dates):
    """PIT:每個 (sid,date) 回傳 MA20、前一日收盤、前 20 日日報酬 sd、ATR20、前 60 日 dist 分布。"""
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    qs = ",".join("?" * len(sids))
    rows = con.execute(
        f"select stock_id,trade_date,open,high,low,close from stock_daily_bars where stock_id in ({qs}) "
        f"and trade_date>=date(?, '-200 day') and trade_date<=? order by stock_id,trade_date",
        (*sids, dates[0], dates[-1])).fetchall()
    ser: dict[str, list] = {}
    for sid, d, o_, h, lo_, c in rows:
        if c and c > 0 and h and lo_:
            a = ser.setdefault(sid, [])
            if not a or a[-1][0] != d:
                a.append((d, float(o_ or c), float(h), float(lo_), float(c)))
    out = {}
    for sid, a in ser.items():
        cl = np.array([x[4] for x in a]); hi = np.array([x[2] for x in a]); lo = np.array([x[3] for x in a])
        ret = np.diff(cl) / cl[:-1]
        for i in range(len(a)):
            if i < 60:
                continue
            prev = cl[i - 20:i]
            ma = float(prev.mean())
            if ma <= 0:
                continue
            sd = float(np.std(ret[i - 20:i], ddof=1))                     # 前 20 日日報酬 sd
            tr = np.maximum(hi[i - 20:i] - lo[i - 20:i],
                            np.maximum(np.abs(hi[i - 20:i] - cl[i - 21:i - 1]),
                                       np.abs(lo[i - 20:i] - cl[i - 21:i - 1])))
            atr = float(tr.mean())
            # 前 60 日每天的收盤乖離(各自對當時的 MA20),當作該檔 dist 的歷史分布
            hist = []
            for j in range(i - 60, i):
                if j >= 20:
                    m2 = cl[j - 20:j].mean()
                    if m2 > 0:
                        hist.append(cl[j] / m2 - 1)
            out[(sid, a[i][0])] = (ma, cl[i - 1], sd, atr, np.array(hist) if hist else None)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=20)
    args = ap.parse_args()
    files = sorted(TICK.glob("*_2026-*.json"))
    dates = sorted({f.name.split("_")[-1][:-5] for f in files})[-args.days:]
    sids = sorted({f.name.split("_")[0] for f in files})
    daily = load_daily(sids, dates)
    print(f"取樣 {len(dates)} 日 × {len(sids)} 檔;有 PIT 日線特徵的 (檔,日) {len(daily):,}", flush=True)

    keys = ("dist", "z_sd", "z_atr", "pct", "d_prev", "today", "day", "sec")
    cols = {k: [] for k in keys}
    FWD = {m: [] for m in HOLD}
    pairs = 0
    for d in dates:
        for sid in sids:
            key = (sid, d)
            if key not in daily:
                continue
            tk = load(sid, d)
            if tk is None:
                continue
            ma, prev_close, sd, atr, hist = daily[key]
            if ma <= 0 or prev_close <= 0 or sd <= 0 or atr <= 0:
                continue
            pairs += 1
            t, px, _vol = tk
            dist = px / ma - 1
            keep = np.concatenate([[True], np.diff(np.floor(t / 10)) > 0])
            idx = np.where(keep)[0]
            if not len(idx):
                continue
            z_sd = dist / (sd * np.sqrt(20))            # 乖離換算成「幾個 20 日 σ」
            z_atr = dist / (atr / prev_close)           # 乖離換算成「幾個 ATR」
            if hist is not None and len(hist) >= 30:
                pct = np.searchsorted(np.sort(hist), dist) / len(hist) * 100
            else:
                pct = np.full(len(dist), np.nan)
            for k, v in (("dist", dist * 100), ("z_sd", z_sd), ("z_atr", z_atr), ("pct", pct),
                         ("d_prev", np.full(len(dist), (prev_close / ma - 1) * 100)),
                         ("today", (px / prev_close - 1) * 100), ("sec", t)):
                cols[k].append(v[keep])
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
    n = len(X["dist"])
    print(f"檔日 {pairs}、取樣點 {n:,}\n")

    def table(nm, var, edges, fmt="{:+.1f}"):
        print(f"\n-- {nm}")
        print(f"   {'區間':<22}{'n':>9}{'漲停附近%':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
        for i in range(len(edges) - 1):
            m = np.isfinite(var) & (var > edges[i]) & (var <= edges[i + 1])
            if m.sum() < 500:
                continue
            lim = np.mean(X["today"][m] >= LIMIT_UP) * 100
            lab = f"({fmt.format(edges[i])},{fmt.format(edges[i + 1])}]"
            print(f"   {lab:<22}{m.sum():>9,}{lim:>9.1f}"
                  + "".join(f"{np.nanmean(exf[mm][m]):>9.2f}" for mm in HOLD))

    print("==== A. 原始 % 門檻（前一輪口徑，當對照）====")
    table("dist %", X["dist"], [-1e9, -10, -5, 0, 5, 10, 20, 1e9])

    print("\n\n==== B. 三種標準化後 ====")
    table("z_sd（幾個 20 日 σ）", X["z_sd"], [-1e9, -2, -1, 0, 1, 2, 3, 1e9], "{:+.0f}")
    table("z_atr（幾個 ATR）", X["z_atr"], [-1e9, -4, -2, 0, 2, 4, 6, 1e9], "{:+.0f}")
    table("pct（自身前 60 日分位 %）", X["pct"], [-1, 10, 30, 50, 70, 90, 100], "{:.0f}")

    print("\n\n==== C. 拆解：舊乖離（昨收對 MA20）× 今日變動 ====")
    print(f"   {'昨收乖離':<14}{'今日變動':<14}{'n':>9}{'漲停附近%':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for plo, phi, pl in ((15, 1e9, ">+15%"), (5, 15, "+5~15%"), (-5, 5, "-5~+5%"), (-1e9, -5, "<-5%")):
        for tlo, thi, tl in ((5, 1e9, ">+5%"), (1, 5, "+1~5%"), (-1, 1, "±1%"), (-1e9, -1, "<-1%")):
            m = (X["d_prev"] > plo) & (X["d_prev"] <= phi) & (X["today"] > tlo) & (X["today"] <= thi)
            if m.sum() < 500:
                continue
            lim = np.mean(X["today"][m] >= LIMIT_UP) * 100
            print(f"   {pl:<14}{tl:<14}{m.sum():>9,}{lim:>9.1f}"
                  + "".join(f"{np.nanmean(exf[mm][m]):>9.2f}" for mm in HOLD))

    print("\n\n==== D. 前一輪的 -26.43 到底是誰貢獻的（dist > +20% 內部拆解）====")
    hot = X["dist"] > 20
    print(f"   dist>+20% 全體 n={hot.sum():,}，漲停附近占 {np.mean(X['today'][hot] >= LIMIT_UP) * 100:.1f}%")
    print(f"   {'子群':<30}{'n':>9}{'漲停附近%':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lab, m in (("舊乖離為主（今日 ≤ +1%）", hot & (X["today"] <= 1)),
                   ("今日推升 +1~5%", hot & (X["today"] > 1) & (X["today"] <= 5)),
                   ("今日推升 > +5%", hot & (X["today"] > 5)),
                   ("今日漲停附近（≥9.5%）", hot & (X["today"] >= LIMIT_UP))):
        if m.sum() < 300:
            print(f"   {lab:<30}{m.sum():>9,}  n<300"); continue
        lim = np.mean(X["today"][m] >= LIMIT_UP) * 100
        print(f"   {lab:<30}{m.sum():>9,}{lim:>9.1f}"
              + "".join(f"{np.nanmean(exf[mm][m]):>9.2f}" for mm in HOLD))
    print("\n\n==== E. 「高檔盤整」子群的穩定性與可執行性 ====")
    core = (X["dist"] > 20) & (X["today"] <= 1)
    half = len(dates) // 2
    first = X["day"] < half
    print(f"   核心子群 n={core.sum():,}")
    print(f"   {'切分':<20}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lab, m in ((f"前半 {dates[0]}~{dates[half - 1]}", core & first),
                   (f"後半 {dates[half]}~{dates[-1]}", core & ~first)):
        if m.sum() < 300:
            print(f"   {lab:<20}{m.sum():>9,}  n<300"); continue
        print(f"   {lab:<20}{m.sum():>9,}" + "".join(f"{np.nanmean(exf[mm][m]):>9.2f}" for mm in HOLD))
    print("\n   逐日（+60 分超額，負=做空獲利）：")
    for di in range(len(dates)):
        m = core & (X["day"] == di)
        if m.sum() < 100:
            continue
        print(f"     {dates[di]}  n={m.sum():>6,}  {np.nanmean(exf[60][m]):>8.2f}")
    print("\n   可執行性檢查（核心子群的今日漲跌分布）：")
    td = X["today"][core]
    for lo, hi_, lab in ((-1e9, -3, "跌 >3%"), (-3, -1, "跌 1~3%"), (-1, 0, "跌 0~1%"),
                         (0, 1, "漲 0~1%")):
        m = (td > lo) & (td <= hi_)
        if m.sum() < 100:
            continue
        sub = core.copy(); sub[core] = m
        print(f"     {lab:<10} {m.sum():>7,} 筆（{m.mean() * 100:4.1f}%）  +60 分 {np.nanmean(exf[60][sub]):>8.2f}")
    print("     ⚠ 平盤下不得放空:今日為跌的那幾格,現股放空會被擋,只能用個股期貨或借券。")
    print("\n   成本:掛買一等 22 bps;不排隊吃賣一約 41 bps。做空另有平盤下不得放空與融券限制。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
