#!/usr/bin/env python3
"""MA20 乖離的四象限分類 + 象限內校正(2026-10-03)。

jack 的方法論修正:前一輪對全樣本做 σ/ATR/分位標準化,結果三種都削弱訊號、最極端格
還翻號。原因是**校正前沒分類** —— 把「高乖離∧今天還在噴」(+7.26)和「高乖離∧今天在跌」
(-60.72)除以同一個尺再混進同一桶,兩者符號相反、互相抵銷。

本檔先分四象限,再在**每個象限內部**對乖離做分位,問的是「在這一類狀態裡,乖離多大才算極端」。

四象限(乖離對 MA20 × 今日至今變動):
  A 高乖離 ∧ 今日漲   續噴
  B 高乖離 ∧ 今日跌   回落  ← 前一輪最強(-60.72)但 83.5% 被平盤下不得放空擋住
  C 低乖離 ∧ 今日漲   反彈
  D 低乖離 ∧ 今日跌   續跌
每格另標「現股可放空比例」(今日 ≥0 才不受平盤下限制)與漲停附近比例。

用法:PYTHONPATH=src .venv/bin/python scripts/research/ma20_quadrant_probe.py [--days 20]
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
    print(f"取樣 {len(dates)} 日 × {len(sids)} 檔;PIT 日線特徵 {len(daily):,} 組", flush=True)

    cols = {k: [] for k in ("dist", "today", "day", "sec")}
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
    dist, today = X["dist"], X["today"]
    print(f"取樣點 {len(dist):,}\n")

    HI = dist > 5          # 高乖離門檻：線上 5% 以上
    LO = dist < -5
    UP = today > 0
    quads = (("A 高乖離 ∧ 今日漲（續噴）", HI & UP),
             ("B 高乖離 ∧ 今日跌（回落）", HI & ~UP),
             ("C 低乖離 ∧ 今日漲（反彈）", LO & UP),
             ("D 低乖離 ∧ 今日跌（續跌）", LO & ~UP))

    print("==== A. 四象限總覽 ====")
    print(f"   {'象限':<28}{'n':>9}{'可空%':>7}{'漲停%':>7}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lab, m in quads:
        if m.sum() < 500:
            continue
        shortable = np.mean(today[m] >= 0) * 100
        lim = np.mean(today[m] >= LIMIT_UP) * 100
        print(f"   {lab:<28}{m.sum():>9,}{shortable:>7.1f}{lim:>7.1f}"
              + "".join(f"{np.nanmean(exf[mm][m]):>9.2f}" for mm in HOLD))

    print("\n\n==== B. 象限內校正：各象限內部對乖離做五分位 ====")
    for lab, m in quads:
        if m.sum() < 2000:
            continue
        v = np.abs(dist[m])
        qs = np.nanpercentile(v, [20, 40, 60, 80])
        print(f"\n-- {lab}（n={m.sum():,}；分位切在 |乖離| = "
              + ", ".join(f"{q:.1f}%" for q in qs) + "）")
        print(f"   {'象限內分位':<16}{'n':>9}{'可空%':>7}{'漲停%':>7}" + "".join(f"{f'+{m2}分':>9}" for m2 in HOLD))
        edges = [0, *qs, 1e9]
        for i in range(5):
            sub = m.copy()
            sub[m] = (v > edges[i]) & (v <= edges[i + 1])
            if sub.sum() < 300:
                continue
            sh = np.mean(today[sub] >= 0) * 100
            lim = np.mean(today[sub] >= LIMIT_UP) * 100
            print(f"   Q{i + 1}（{edges[i]:.1f}~{edges[i + 1]:.0f}%）".ljust(19) + f"{sub.sum():>9,}{sh:>7.1f}{lim:>7.1f}"
                  + "".join(f"{np.nanmean(exf[mm][sub]):>9.2f}" for mm in HOLD))

    print("\n\n==== C. 可執行子集：只看今日 ≥0（現股放空不受平盤下限制）====")
    print(f"   {'象限內分位':<26}{'n':>9}{'漲停%':>7}" + "".join(f"{f'+{m2}分':>9}" for m2 in HOLD))
    mA = HI & (today >= 0)
    v = np.abs(dist[mA])
    qs = np.nanpercentile(v, [50, 80, 95])
    edges = [0, *qs, 1e9]
    for i in range(4):
        sub = mA.copy()
        sub[mA] = (v > edges[i]) & (v <= edges[i + 1])
        if sub.sum() < 300:
            continue
        lim = np.mean(today[sub] >= LIMIT_UP) * 100
        print(f"   高乖離 {edges[i]:.1f}~{edges[i + 1]:.0f}%".ljust(29) + f"{sub.sum():>9,}{lim:>7.1f}"
              + "".join(f"{np.nanmean(exf[mm][sub]):>9.2f}" for mm in HOLD))
    print("\n   成本:掛買一等 22 bps;不排隊吃賣一約 41 bps。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
