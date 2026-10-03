#!/usr/bin/env python3
"""MA20 八象限:加入「月線斜率」當第三維度(2026-10-03)。

jack:四象限不夠,應該有八個。第三個維度取 **MA20 自己的斜率** —— 同樣是「線上 +20%」,
月線在上升(強勢延續)與在下降(反彈到頂)是完全不同的狀態。前例:[[biglot-double-blade]]
的「月斜」是坐牢中面板唯一有單調性的欄位(低 +3.18 / 中 +3.56 / 高 +8.27)。

本檔同時檢定一個假設:**前一輪的前後半翻車(-49.36 → +0.73)是不是因為月線斜率分布變了**。
如果前半多數個股月線在上升、後半轉為下降,那同一個「高乖離」在兩段期間本來就是不同的東西,
翻車就不是訊號衰減而是我把兩種狀態混在一起。

八象限 = 乖離(線上/線下) × 今日(漲/跌) × 月線斜率(上/下)。
斜率定義(PIT):MA20(前一日) / MA20(前 6 日) − 1,即月線近一週的走向。

用法:PYTHONPATH=src .venv/bin/python scripts/research/ma20_octant_probe.py [--days 20]
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
LIMIT_UP = 9.5


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
    t, px = [], []
    for x in arr:
        tt = x.get("Time") or ""
        if len(tt) < 8 or str(x.get("TickType", "0")) not in ("1", "2"):
            continue
        s = _sec(tt)
        if s < OPEN_S or s > CLOSE_S:
            continue
        t.append(s); px.append(float(x["deal_price"]))
    if len(t) < 300:
        return None
    t = np.asarray(t); o = np.argsort(t, kind="stable")
    return t[o], np.asarray(px)[o]


def load_daily(sids, dates):
    """PIT:MA20、前一日收盤、月線斜率(MA20 近 5 個交易日的變化 %)。"""
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    qs = ",".join("?" * len(sids))
    rows = con.execute(
        f"select stock_id,trade_date,close from stock_daily_bars where stock_id in ({qs}) "
        f"and trade_date>=date(?, '-150 day') and trade_date<=? order by stock_id,trade_date",
        (*sids, dates[0], dates[-1])).fetchall()
    ser: dict[str, list] = {}
    for sid, d, c in rows:
        if c and c > 0:
            a = ser.setdefault(sid, [])
            if not a or a[-1][0] != d:
                a.append((d, float(c)))
    out = {}
    for sid, a in ser.items():
        cl = np.array([x[1] for x in a])
        ma = np.full(len(cl), np.nan)
        for i in range(20, len(cl)):
            ma[i] = cl[i - 20:i].mean()          # 不含當日
        for i in range(26, len(a)):
            if not (np.isfinite(ma[i]) and np.isfinite(ma[i - 5]) and ma[i] > 0 and ma[i - 5] > 0):
                continue
            slope = (ma[i] / ma[i - 5] - 1) * 100
            out[(sid, a[i][0])] = (float(ma[i]), cl[i - 1], slope)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=20)
    args = ap.parse_args()
    files = sorted(TICK.glob("*_2026-*.json"))
    dates = sorted({f.name.split("_")[-1][:-5] for f in files})[-args.days:]
    sids = sorted({f.name.split("_")[0] for f in files})
    daily = load_daily(sids, dates)
    print(f"取樣 {len(dates)} 日 × {len(sids)} 檔;PIT 日線特徵 {len(daily):,} 組", flush=True)

    cols = {k: [] for k in ("dist", "today", "slope", "day", "sec")}
    FWD = {m: [] for m in HOLD}
    for d in dates:
        for sid in sids:
            key = (sid, d)
            if key not in daily:
                continue
            tk = load(sid, d)
            if tk is None:
                continue
            ma, prev, slope = daily[key]
            if ma <= 0 or prev <= 0:
                continue
            t, px = tk
            keep = np.concatenate([[True], np.diff(np.floor(t / 10)) > 0])
            idx = np.where(keep)[0]
            if not len(idx):
                continue
            cols["dist"].append(((px / ma - 1) * 100)[keep])
            cols["today"].append(((px / prev - 1) * 100)[keep])
            cols["slope"].append(np.full(len(idx), slope))
            cols["sec"].append(t[keep])
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
    dist, today, slope = X["dist"], X["today"], X["slope"]
    half = len(dates) // 2
    first = X["day"] < half
    print(f"取樣點 {len(dist):,}\n")

    print("==== 0. 關鍵檢定:前後半的月線斜率分布是否改變 ====")
    for lab, m in ((f"前半 {dates[0]}~{dates[half - 1]}", first), (f"後半 {dates[half]}~{dates[-1]}", ~first)):
        s = slope[m]
        print(f"   {lab:<28} 斜率中位 {np.nanmedian(s):+6.2f}%  上升占比 {np.mean(s > 0) * 100:5.1f}%  "
              f"p25 {np.nanpercentile(s, 25):+6.2f}  p75 {np.nanpercentile(s, 75):+6.2f}")
    print("   → 若上升占比明顯下降,前一輪的翻車就有環境面的解釋,而非單純訊號衰減。")

    HI, UP, SU = dist > 5, today > 0, slope > 0
    octs = []
    for a, al in ((HI, "線上"), (~HI, "線下")):
        for b, bl in ((UP, "今漲"), (~UP, "今跌")):
            for c, cl_ in ((SU, "月線↑"), (~SU, "月線↓")):
                octs.append((f"{al} ∧ {bl} ∧ {cl_}", a & b & c))

    print("\n\n==== 1. 八象限總覽 ====")
    print(f"   {'象限':<26}{'n':>9}{'可空%':>7}{'漲停%':>7}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lab, m in octs:
        if m.sum() < 1000:
            print(f"   {lab:<26}{m.sum():>9,}  n<1000"); continue
        print(f"   {lab:<26}{m.sum():>9,}{np.mean(today[m] >= 0) * 100:>7.1f}"
              f"{np.mean(today[m] >= LIMIT_UP) * 100:>7.1f}"
              + "".join(f"{np.nanmean(exf[mm][m]):>9.2f}" for mm in HOLD))

    print("\n\n==== 2. 八象限的前後半穩定性（+60 分）====")
    print(f"   {'象限':<26}{'前半 n':>9}{'前半':>9}{'後半 n':>9}{'後半':>9}{'同號':>6}")
    for lab, m in octs:
        a, b = m & first, m & ~first
        if a.sum() < 500 or b.sum() < 500:
            continue
        va, vb = np.nanmean(exf[60][a]), np.nanmean(exf[60][b])
        print(f"   {lab:<26}{a.sum():>9,}{va:>9.2f}{b.sum():>9,}{vb:>9.2f}"
              f"{'  ✓' if va * vb > 0 else '  ✗':>6}")

    print("\n\n==== 3. 前一輪窄帶(16~24% ∧ 今日≥0)依月線斜率再拆 ====")
    band = (today >= 0) & (dist > 16) & (dist <= 24)
    print(f"   {'子群':<26}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD) + "   前半/後半(+60)")
    for lab, m in (("窄帶 ∧ 月線↑", band & SU), ("窄帶 ∧ 月線↓", band & ~SU)):
        if m.sum() < 500:
            print(f"   {lab:<26}{m.sum():>9,}  n<500"); continue
        a, b = m & first, m & ~first
        fa = np.nanmean(exf[60][a]) if a.sum() >= 300 else float("nan")
        fb = np.nanmean(exf[60][b]) if b.sum() >= 300 else float("nan")
        print(f"   {lab:<26}{m.sum():>9,}" + "".join(f"{np.nanmean(exf[mm][m]):>9.2f}" for mm in HOLD)
              + f"   {fa:+7.1f} / {fb:+7.1f}")
    print("\n   成本:掛買一等 22 bps;不排隊吃賣一約 41 bps。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
