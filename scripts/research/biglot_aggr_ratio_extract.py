#!/usr/bin/env python3
"""外盤比(內外盤比)條件式進場研究 — 階段 1:逐筆特徵抽取(2026-09-30)。

問題:V2.5≥15 訊號當下,若「外盤比」(主動買金額占比)明顯偏高,是否該放棄掛買一、直接吃賣一?
動機:2026-09-29 紙上帳 4 筆掛買一未成交、30 秒內平均跑掉 +45 bps(排隊逆選擇)。

PIT 紀律:訊號時刻 t_sig = 桶起點 + 300 秒(桶收),外盤比只用 t <= t_sig 的逐筆。
TickType:1=買方主動(成交在賣一)、2=賣方主動(成交在買一)、0=競價(丟棄)。

輸出 ~/goldenstocks-data/scratch/biglot_aggr_ratio_events_2026-09-30.csv
用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_aggr_ratio_extract.py
"""
from __future__ import annotations
import json, sys
from bisect import bisect_left, bisect_right
from pathlib import Path

import numpy as np
import pandas as pd

from biglot_hold_lab import load_lab, make_events, simulate, exit_score_le

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OUT = Path.home() / "goldenstocks-data/scratch/biglot_aggr_ratio_events_2026-09-30.csv"
BUY_WAIT = 30.0          # 掛買一等待秒數(對齊 PAPER_BUY_WAIT)


def _sec(t: str) -> float:
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + float(t[6:])


def load_ticks(sid, date):
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
        if len(tt) < 8:
            continue
        k = str(x.get("TickType", "0"))
        if k not in ("1", "2"):
            continue
        s = _sec(tt)
        if s < 9 * 3600 or s > 13 * 3600 + 30 * 60:
            continue
        t.append(s); px.append(float(x["deal_price"])); vol.append(float(x.get("volume") or 0)); ty.append(1 if k == "1" else 2)
    if len(t) < 100:
        return None
    t = np.array(t); o = np.argsort(t, kind="stable")
    return t[o], np.array(px)[o], np.array(vol)[o], np.array(ty, dtype=np.int8)[o]


def bsec(b):
    return int(b[:2]) * 3600 + int(b[3:5]) * 60


def aggr(tk, t0, win):
    """[t0-win, t0] 的外盤比(主動買金額 / 總金額)與總金額。PIT:不含 t0 之後。"""
    t, px, vol, ty = tk
    lo, hi = bisect_right(t, t0 - win), bisect_right(t, t0)
    if hi <= lo:
        return np.nan, 0.0, 0
    amt = px[lo:hi] * vol[lo:hi]; k = ty[lo:hi]
    tot = amt.sum()
    if tot <= 0:
        return np.nan, 0.0, hi - lo
    return float(amt[k == 1].sum() / tot), float(tot), hi - lo


def last_before(tk, t0, side):
    """t0 之前最後一筆指定 side 的成交價(1→賣一代理、2→買一代理)。"""
    t, px, _v, ty = tk
    i = bisect_right(t, t0) - 1
    while i >= 0 and t[i] >= t0 - 300:
        if ty[i] == side:
            return float(px[i])
        i -= 1
    return np.nan


def first_after(tk, t0, side=None, within=120.0):
    t, px, _v, ty = tk
    i = bisect_left(t, t0)
    while i < len(t) and t[i] <= t0 + within:
        if side is None or ty[i] == side:
            return float(t[i]), float(px[i])
        i += 1
    return None, None


def fill_bid(tk, t0, limit, wait):
    """掛買一 limit:t0 後 wait 秒內出現賣方主動(ty=2)成交 <=limit(樂觀)/<limit(嚴格)。"""
    t, px, _v, ty = tk
    i = bisect_left(t, t0); ts_o = ts_s = None
    while i < len(t) and t[i] <= t0 + wait:
        if ty[i] == 2:
            if px[i] <= limit and ts_o is None:
                ts_o = float(t[i])
            if px[i] < limit and ts_s is None:
                ts_s = float(t[i])
            if ts_o is not None and ts_s is not None:
                break
        i += 1
    return ts_s, ts_o


def main():
    L = load_lab()
    ev = make_events(L, "score", 15)
    sim = simulate(L, ev, exit_score_le(0))
    sim = sim[sim["filled"]].rename(columns={"is": "is_"})
    print(f"事件 {len(sim)} (IS {int(sim['is_'].sum())} / OOS {int((~sim['is_']).sum())})", flush=True)
    buckets = L["buckets"]; PX = L["px"]; U = L["uidx"]
    rows = []
    for n, e in enumerate(sim.itertuples()):
        if n % 200 == 0:
            print(f"  ...{n}/{len(sim)}", flush=True)
        tk = load_ticks(e.sid, e.date)
        if tk is None:
            continue
        i0 = buckets.index(e.bucket); t_sig = bsec(e.bucket) + 300
        kx = int(e.k_exit); t_exit = bsec(buckets[i0 + kx]) + 300
        close0 = PX.loc[(e.sid, e.date)].iloc[i0]; closex = PX.loc[(e.sid, e.date)].iloc[i0 + kx]
        u = U.loc[e.date]; uret = (u.iloc[i0 + kx] / u.iloc[i0] - 1) * 1e4
        if not np.isfinite(close0) or close0 <= 0:
            continue
        rec = {"sid": e.sid, "date": e.date, "bucket": e.bucket, "is": bool(e.is_), "score": float(e.s0),
               "k_exit": kx, "hold_min": kx * 5, "uret": uret, "close0": float(close0), "closex": float(closex)}
        # --- PIT 外盤比(多視窗) ---
        for w, tag in ((30, "30"), (60, "60"), (300, "300")):
            r, amt, nn = aggr(tk, t_sig, w)
            rec[f"out{tag}"] = r; rec[f"amt{tag}"] = amt; rec[f"ntk{tag}"] = nn
        r_day, amt_day, _ = aggr(tk, t_sig, t_sig - 9 * 3600)
        rec["out_day"] = r_day; rec["amt_day"] = amt_day
        # --- 盤口代理 ---
        bid = last_before(tk, t_sig, 2); ask = last_before(tk, t_sig, 1)
        rec["bid"] = bid; rec["ask"] = ask
        rec["spread_bps"] = (ask / bid - 1) * 1e4 if (np.isfinite(bid) and np.isfinite(ask) and bid > 0) else np.nan
        # --- A 路:掛買一 30 秒 ---
        if np.isfinite(bid) and bid > 0:
            ts_s, ts_o = fill_bid(tk, t_sig, bid, BUY_WAIT)
            rec["A_fill_opt"] = ts_o is not None; rec["A_fill_strict"] = ts_s is not None
            rec["A_entry"] = bid
            rec["A_wait_s"] = (ts_o - t_sig) if ts_o is not None else np.nan
        else:
            rec["A_fill_opt"] = rec["A_fill_strict"] = False; rec["A_entry"] = np.nan; rec["A_wait_s"] = np.nan
        # 未成交後 30 秒跑掉多少(逆選擇量測)
        _tt, p30 = first_after(tk, t_sig + BUY_WAIT, None, 60.0)
        rec["px_after_wait"] = p30 if p30 else np.nan
        rec["run_bps"] = ((p30 / bid - 1) * 1e4) if (p30 and np.isfinite(bid) and bid > 0) else np.nan
        # --- B 路:吃賣一(t_sig 後第一筆買方主動) ---
        _tb, pb = first_after(tk, t_sig, 1, 120.0)
        rec["B_entry"] = pb if pb else np.nan
        # --- 出場價(兩口徑,A/B 共用) ---
        _te, bid_x = first_after(tk, t_exit, 2, 120.0)
        rec["exit_close"] = float(closex)
        rec["exit_bid"] = float(bid_x) if bid_x else float(closex)
        rows.append(rec)
    d = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(OUT, index=False)
    print(f"\n寫出 {OUT}  n={len(d)}  IS {int(d['is'].sum())} / OOS {int((~d['is']).sum())}")
    print(f"A 路樂觀成交率 {d['A_fill_opt'].mean()*100:.1f}%  嚴格 {d['A_fill_strict'].mean()*100:.1f}%")
    print(f"外盤比30s 中位 {d['out30'].median():.3f}  價差中位 {d['spread_bps'].median():.1f} bps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
