#!/usr/bin/env python3
"""盲點檢定:MFE/MAE + 事件去重(2026-10-03)。

八輪盤中研究全部只看「固定持有 N 分鐘的平均超額」。兩個可能的盲點:

**盲點一 —— 只看平均、沒看分布**
在接近效率的市場,任何狀態的平均報酬本來就接近 0。交易賺的是非對稱性:
平均 +5 bps 但 MFE +80 bps 的狀態是可交易的(配停利),平均 +5 bps 而 MFE +15 bps 的不是。
固定持有等於強制在固定時點平倉,把中途的機會全抹平。本檔補算:
  MFE  進場後 N 分鐘內的最大有利變動(做多視角)
  MAE  同期間最大不利變動
  以及 MFE/|MAE| 比、最終報酬佔 MFE 的比例

**盲點二 —— 每 10 秒取樣造成重疊**
174 萬取樣點不是 174 萬獨立事件,同一波行情被連續取樣幾十次 → n 虛高、
前後半的翻車可能是少數事件日主導。本檔同時用**事件去重**(同一檔的連續訊號
只取首次、且 30 分鐘內不重複觸發)重算,比較去重前後的 n 與穩定性。

測試對象是前八輪留下的三個格子:
  G1 盤整 ∧ 散戶退場            (+60 分 +4.24)
  G2 窄帶 乖離 16~24% ∧ 今日≥0  (+60 分 -30.64,但前後半翻車)
  G3 線上 ∧ 今漲 ∧ 月線↓        (+60 分 -8.53,唯一前後半同號)

用法:PYTHONPATH=src .venv/bin/python scripts/research/mfe_mae_event_probe.py [--days 20]
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
HORIZON = 3600          # MFE/MAE 觀察窗:60 分鐘
DEDUP_S = 1800          # 事件去重:同一檔 30 分鐘內只算一次


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


def load_daily(sids, dates):
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
            ma[i] = cl[i - 20:i].mean()
        for i in range(26, len(a)):
            if np.isfinite(ma[i]) and np.isfinite(ma[i - 5]) and ma[i] > 0 and ma[i - 5] > 0:
                out[(sid, a[i][0])] = (float(ma[i]), cl[i - 1], (ma[i] / ma[i - 5] - 1) * 100)
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

    recs = {k: [] for k in ("g1", "g2", "g3", "mfe", "mae", "ret60", "day", "first")}
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
            t, px, vol = tk
            amt = px * vol * LOT
            big = amt >= BIG_NTD
            retail = (vol == 1) & ~big
            rs5, _ = ratio(t, amt, retail, 300); rs30, _ = ratio(t, amt, retail, 1800)
            _r1, n1 = ratio(t, amt, (amt >= 0), 60)       # 只要筆數
            cp = compress(t, px, 300)
            dist = (px / ma - 1) * 100
            today = (px / prev - 1) * 100
            keep = np.concatenate([[True], np.diff(np.floor(t / 10)) > 0])
            idx = np.where(keep & np.isfinite(cp) & np.isfinite(rs5) & (n1 >= 10))[0]
            if not len(idx):
                continue
            # 三個格子
            d_rs = rs5 - rs30
            g1 = (cp[idx] <= 32.2) & (d_rs[idx] <= -0.05)
            g2 = (today[idx] >= 0) & (dist[idx] > 16) & (dist[idx] <= 24)
            g3 = (dist[idx] > 5) & (today[idx] > 0) & (slope < 0)
            # MFE/MAE:進場後 HORIZON 內
            end = np.searchsorted(t, t[idx] + HORIZON, side="left")
            mfe = np.full(len(idx), np.nan); mae = np.full(len(idx), np.nan); r60 = np.full(len(idx), np.nan)
            for i, (a, b) in enumerate(zip(idx, end)):
                if b <= a + 1:
                    continue
                seg = px[a:b]
                p0 = px[a]
                mfe[i] = (seg.max() / p0 - 1) * 1e4
                mae[i] = (seg.min() / p0 - 1) * 1e4
                r60[i] = (seg[-1] / p0 - 1) * 1e4
            # 事件去重:每個格子各自做,同一檔 DEDUP_S 內只留首次
            firsts = np.zeros(len(idx), bool)
            last = -1e9
            for i, ti in enumerate(t[idx]):
                if ti - last >= DEDUP_S:
                    firsts[i] = True; last = ti
            for k, v in (("g1", g1), ("g2", g2), ("g3", g3), ("mfe", mfe), ("mae", mae),
                         ("ret60", r60), ("first", firsts)):
                recs[k].append(v)
            recs["day"].append(np.full(len(idx), dates.index(d), dtype=np.int16))
    X = {k: np.concatenate(v) for k, v in recs.items()}
    half = len(dates) // 2
    first_half = X["day"] < half
    ok = np.isfinite(X["mfe"]) & np.isfinite(X["ret60"])
    print(f"取樣點 {len(ok):,}（可算 MFE 的 {ok.sum():,}）\n")

    print("==== 1. MFE / MAE:平均接近 0 的格子是不是其實有肥右尾 ====")
    print(f"   {'格子':<26}{'n':>8}{'最終':>8}{'MFE':>8}{'MAE':>8}{'MFE/|MAE|':>10}{'最終/MFE':>9}{'勝率':>7}")
    for lab, m in (("全樣本（基準）", ok),
                   ("G1 盤整∧散戶退場", ok & X["g1"]),
                   ("G2 窄帶16~24%∧今≥0", ok & X["g2"]),
                   ("G3 線上∧今漲∧月線↓", ok & X["g3"])):
        if m.sum() < 500:
            continue
        r, f_, a = X["ret60"][m], X["mfe"][m], X["mae"][m]
        ratio_fa = np.nanmean(f_) / abs(np.nanmean(a)) if np.nanmean(a) != 0 else np.nan
        print(f"   {lab:<26}{m.sum():>8,}{np.nanmean(r):>8.1f}{np.nanmean(f_):>8.1f}{np.nanmean(a):>8.1f}"
              f"{ratio_fa:>10.2f}{np.nanmean(r) / np.nanmean(f_) * 100 if np.nanmean(f_) else np.nan:>8.0f}%"
              f"{np.mean(r > 0) * 100:>7.0f}%")
    print("\n   MFE 遠大於最終報酬 ⇒ 固定持有抓不到的機會;MFE/|MAE| > 1 ⇒ 配停利停損有機會")

    print("\n\n==== 2. 報酬分布（不是只有平均）====")
    print(f"   {'格子':<26}" + "".join(f"{f'p{q}':>9}" for q in (5, 25, 50, 75, 95)) + f"{'偏態':>8}")
    for lab, m in (("全樣本（基準）", ok), ("G1 盤整∧散戶退場", ok & X["g1"]),
                   ("G2 窄帶16~24%∧今≥0", ok & X["g2"]), ("G3 線上∧今漲∧月線↓", ok & X["g3"])):
        if m.sum() < 500:
            continue
        r = X["ret60"][m]
        sk = float(((r - r.mean()) ** 3).mean() / (r.std() ** 3)) if r.std() > 0 else np.nan
        print(f"   {lab:<26}" + "".join(f"{np.nanpercentile(r, q):>9.1f}" for q in (5, 25, 50, 75, 95))
              + f"{sk:>8.2f}")

    print("\n\n==== 3. 事件去重:n 掉多少、前後半是否就穩定了 ====")
    print(f"   {'格子':<26}{'原 n':>9}{'去重 n':>8}{'膨脹':>7}{'原 前/後':>18}{'去重 前/後':>18}")
    for lab, g in (("G1 盤整∧散戶退場", X["g1"]), ("G2 窄帶16~24%∧今≥0", X["g2"]),
                   ("G3 線上∧今漲∧月線↓", X["g3"])):
        m = ok & g
        md = m & X["first"]
        if md.sum() < 100:
            print(f"   {lab:<26}{m.sum():>9,}{md.sum():>8,}  去重後 n<100"); continue
        def fb(mask):
            a, b = mask & first_half, mask & ~first_half
            va = np.nanmean(X["ret60"][a]) if a.sum() >= 50 else np.nan
            vb = np.nanmean(X["ret60"][b]) if b.sum() >= 50 else np.nan
            return f"{va:+7.1f} / {vb:+7.1f}"
        print(f"   {lab:<26}{m.sum():>9,}{md.sum():>8,}{m.sum() / max(md.sum(), 1):>7.1f}x"
              f"{fb(m):>18}{fb(md):>18}")
    print("\n   膨脹倍數 = 原始取樣點 / 獨立事件數。若去重後前後半才一致，先前的翻車就是取樣假象。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
