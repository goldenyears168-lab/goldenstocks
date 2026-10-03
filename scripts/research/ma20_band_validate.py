#!/usr/bin/env python3
"""驗證「乖離 16~24% ∧ 今日≥0 → 做空」這個窄帶(2026-10-03)。

前一輪(ma20_quadrant_probe.py)找到本系列首個「效應超成本 ∧ 執行面無硬障礙」的格子:
  高乖離 16~24% ∧ 今日≥0,n=57,271,漲停佔 10.0%,+60 分超額 **-30.64 bps**
但有三個未解問題,本檔逐一檢定:

1. **穩定性** —— 前後半是否同號(前一輪的倒 U 就在這裡翻過車:+11.18 → -0.46)
2. **鎖死污染** —— >24% 那格翻正(+7.97)且漲停佔比從 10.0% 跳到 20.5%。
   懷疑是漲停鎖死讓價格不動、前瞻報酬被壓成 0,把平均拉上來。
   檢定法:剔除「未來 60 分內價格完全不動」的樣本後重算,看翻正是否消失。
3. **多重比較** —— 4 象限 × 5 分位 = 20 格挑出來的。用隨機門檻做安慰劑:
   在同一批樣本上隨機抽同樣大小的子集 300 次,看 -30.64 落在哪個百分位。

用法:PYTHONPATH=src .venv/bin/python scripts/research/ma20_band_validate.py [--days 20]
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
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    qs = ",".join("?" * len(sids))
    rows = con.execute(
        f"select stock_id,trade_date,close from stock_daily_bars where stock_id in ({qs}) "
        f"and trade_date>=date(?, '-120 day') and trade_date<=? order by stock_id,trade_date",
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
        for i in range(21, len(a)):
            ma = float(cl[i - 20:i].mean())
            if ma > 0:
                out[(sid, a[i][0])] = (ma, cl[i - 1])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=20)
    args = ap.parse_args()
    files = sorted(TICK.glob("*_2026-*.json"))
    dates = sorted({f.name.split("_")[-1][:-5] for f in files})[-args.days:]
    sids = sorted({f.name.split("_")[0] for f in files})
    daily = load_daily(sids, dates)
    print(f"取樣 {len(dates)} 日 × {len(sids)} 檔", flush=True)

    cols = {k: [] for k in ("dist", "today", "day", "sec", "frozen")}
    FWD = {m: [] for m in HOLD}
    for d in dates:
        for sid in sids:
            key = (sid, d)
            if key not in daily:
                continue
            tk = load(sid, d)
            if tk is None:
                continue
            ma, prev = daily[key]
            if ma <= 0 or prev <= 0:
                continue
            t, px = tk
            keep = np.concatenate([[True], np.diff(np.floor(t / 10)) > 0])
            idx = np.where(keep)[0]
            if not len(idx):
                continue
            cols["dist"].append(((px / ma - 1) * 100)[keep])
            cols["today"].append(((px / prev - 1) * 100)[keep])
            cols["sec"].append(t[keep])
            cols["day"].append(np.full(len(idx), dates.index(d), dtype=np.int16))
            # 鎖死代理:往後 60 分鐘內價格完全沒變過
            tgt60 = np.searchsorted(t, t[idx] + 3600, side="left")
            fz = np.zeros(len(idx), bool)
            for i, (a, b) in enumerate(zip(idx, tgt60)):
                if b > a and b <= len(px):
                    seg = px[a:b]
                    fz[i] = seg.max() == seg.min()
            cols["frozen"].append(fz)
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
    dist, today, frozen = X["dist"], X["today"], X["frozen"]
    shortable = today >= 0
    band = shortable & (dist > 16) & (dist <= 24)
    beyond = shortable & (dist > 24)
    print(f"取樣點 {len(dist):,};窄帶 n={band.sum():,}、>24% n={beyond.sum():,}\n")

    print("==== 1. 穩定性：前後半 ====")
    half = len(dates) // 2
    first = X["day"] < half
    print(f"   {'切分':<28}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lab, m in ((f"窄帶 前半 {dates[0]}~{dates[half - 1]}", band & first),
                   (f"窄帶 後半 {dates[half]}~{dates[-1]}", band & ~first)):
        if m.sum() < 300:
            print(f"   {lab:<28}{m.sum():>9,}  n<300"); continue
        print(f"   {lab:<28}{m.sum():>9,}" + "".join(f"{np.nanmean(exf[mm][m]):>9.2f}" for mm in HOLD))
    print("\n   逐日（+60 分，負=做空獲利）：")
    pos = neg = 0
    for di in range(len(dates)):
        m = band & (X["day"] == di)
        if m.sum() < 200:
            continue
        v = np.nanmean(exf[60][m])
        pos += v > 0; neg += v < 0
        print(f"     {dates[di]}  n={m.sum():>6,}  {v:>8.2f}")
    print(f"   → 負（做空獲利）{neg} 日 / 正 {pos} 日")

    print("\n\n==== 2. 鎖死污染：剔除「未來 60 分價格完全不動」後重算 ====")
    print(f"   {'子集':<30}{'n':>9}{'鎖死%':>8}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lab, m in (("窄帶 16~24%（全部）", band), ("窄帶 16~24%（剔除鎖死）", band & ~frozen),
                   (">24%（全部）", beyond), (">24%（剔除鎖死）", beyond & ~frozen)):
        if m.sum() < 300:
            print(f"   {lab:<30}{m.sum():>9,}  n<300"); continue
        fz = np.mean(frozen[m]) * 100
        print(f"   {lab:<30}{m.sum():>9,}{fz:>8.1f}"
              + "".join(f"{np.nanmean(exf[mm][m]):>9.2f}" for mm in HOLD))
    print("\n   剔除鎖死後若 >24% 仍翻正，代表翻號不是鎖死造成的（關係真的非單調）；")
    print("   若翻正消失、變回負值，則原本的單調關係被鎖死污染了尾端。")

    print("\n\n==== 3. 多重比較：安慰劑（同樣大小的隨機子集，300 次）====")
    rng = np.random.default_rng(97)
    pool = np.flatnonzero(shortable & np.isfinite(exf[60]))
    k = int((band & np.isfinite(exf[60])).sum())
    real = np.nanmean(exf[60][band])
    sims = np.array([np.nanmean(exf[60][rng.choice(pool, k, replace=False)]) for _ in range(300)])
    print(f"   真實 {real:+.2f}  vs  隨機 {sims.mean():+.2f} "
          f"[p1 {np.percentile(sims, 1):+.2f}, p5 {np.percentile(sims, 5):+.2f}, p95 {np.percentile(sims, 95):+.2f}]")
    print(f"   百分位 {(sims < real).mean() * 100:.1f}%（越低代表越難用隨機抽樣複製出這個負值）")

    print("\n\n==== 4. 成本對照（窄帶，剔除鎖死）====")
    m = band & ~frozen
    for mins in HOLD:
        v = exf[mins][m]
        n = np.isfinite(v).sum()
        sd = np.nanstd(v)
        mu = np.nanmean(v)
        print(f"   +{mins:>3} 分  n={n:>7,}  超額 {mu:>8.2f}  sd {sd:>6.1f}  "
              f"MDE(t=2) {2 * sd / np.sqrt(n):>5.2f}  做空扣22 {-mu - 22:>7.2f}  扣41 {-mu - 41:>7.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
