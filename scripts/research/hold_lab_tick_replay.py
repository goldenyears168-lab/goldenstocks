#!/usr/bin/env python3
"""tick 重放進場定價檢定(2026-09-25):V2.5 ≥15 首次進入事件,用 pit_universe_tick 逐筆(FinMind TickType 1=買方主動=成交在賣一)
(a) 訊號桶收後延遲 d 秒、以「下一筆買方主動成交價」(=賣一)進場,出場依分數 ≤0(桶級),出場價=桶收 / 下一筆賣方主動成交(=買一,悲觀)
(b) 深單:訊號桶收價 ×(1−x bps) 掛 30 分,嚴格穿越(有成交價 < 限價)才算成交(FIFO 保守)/ ≤ 限價(樂觀);成交後依分數 ≤0 出、上限 60 分
輸出 ~/goldenstocks-data/scratch/hold_lab_tick_replay_2026-09-25.txt
用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/hold_lab_tick_replay.py
"""
from __future__ import annotations
import json, sys
import numpy as np, pandas as pd
from bisect import bisect_left
from pathlib import Path
from biglot_hold_lab import load_lab, make_events, simulate, exit_score_le, cl_t

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
COST = 25.0


def _sec(t: str) -> float:
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + float(t[6:])


def load_ticks(sid, date):
    f = TICK / f"{sid}_{date}.json"
    if not f.exists(): return None
    try: arr = json.loads(f.read_text(encoding="utf-8"))
    except Exception: return None
    t, px, ty = [], [], []
    for x in arr:
        tt = x.get("Time") or ""
        if len(tt) < 8: continue
        s = _sec(tt)
        if s < 9 * 3600 or s > 13 * 3600 + 30 * 60: continue
        t.append(s); px.append(float(x["deal_price"])); ty.append(str(x.get("TickType", "0")))
    if len(t) < 100: return None
    o = np.argsort(np.array(t), kind="stable")
    return np.array(t)[o], np.array(px)[o], np.array(ty)[o]


def first_after(tk, t0, side=None, within=120):
    t, px, ty = tk; i = bisect_left(t, t0)
    while i < len(t) and t[i] <= t0 + within:
        if side is None or ty[i] == side: return t[i], px[i]
        i += 1
    return None, None


def bsec(b):  # 桶起點秒
    return int(b[:2]) * 3600 + int(b[3:5]) * 60


def main():
    L = load_lab(); ev = make_events(L, "score", 15); buckets = L["buckets"]
    base = simulate(L, ev, exit_score_le(0)); base = base[base["filled"]].rename(columns={"is": "is_"})
    print(f"事件 {len(base)}(IS {base['is_'].sum()} / OOS {(~base['is_']).sum()})", flush=True)
    S = L["paths"]["s"]; PX = L["px"]; U = L["uidx"]
    rows = []
    for e in base.itertuples():
        tk = load_ticks(e.sid, e.date)
        if tk is None: continue
        i0 = buckets.index(e.bucket); t_sig = bsec(e.bucket) + 300
        kx = int(e.k_exit); bx = buckets[i0 + kx]; t_exit = bsec(bx) + 300
        close0 = PX.loc[(e.sid, e.date)].iloc[i0]; closex = PX.loc[(e.sid, e.date)].iloc[i0 + kx]
        u = U.loc[e.date]; uret = (u.iloc[i0 + kx] / u.iloc[i0] - 1) * 1e4
        _, bid_x = first_after(tk, t_exit, "2"); _, any_x = first_after(tk, t_exit, None)
        rec = {"sid": e.sid, "date": e.date, "bucket": e.bucket, "is": e.is_, "k_exit": kx, "uret": uret, "close0": close0, "closex": closex,
               "exit_bid": bid_x if bid_x else closex, "exit_any": any_x if any_x else closex}
        for d in (0, 1, 5, 30, 60):
            _, pa = first_after(tk, t_sig + d, "1"); _, pn = first_after(tk, t_sig + d, None)
            rec[f"ask_d{d}"] = pa; rec[f"any_d{d}"] = pn
        # 深單
        spath = S.loc[(e.sid, e.date)].values if (e.sid, e.date) in S.index else None
        t, px, ty = tk
        for x in (30, 50, 100):
            lim = close0 * (1 - x / 1e4)
            for mode, cond in (("strict", px < lim), ("opt", px <= lim)):
                m = cond & (t > t_sig) & (t <= t_sig + 1800)
                key = f"dip{x}_{mode}"
                if not m.any():
                    rec[key] = None; continue
                tf = t[np.argmax(m)]; fill_px = lim
                # 出場:成交後第一個桶邊界起,分數 ≤0 的桶;上限 60 分
                kf = None
                for k in range(i0 + 1, min(len(buckets), i0 + 13)):
                    if bsec(buckets[k]) + 300 <= tf: continue
                    if spath is not None and spath[k] <= 0: kf = k; break
                    if bsec(buckets[k]) + 300 >= tf + 3600: kf = k; break
                if kf is None: kf = min(len(buckets) - 1, i0 + 12)
                tx = bsec(buckets[kf]) + 300; _, bidk = first_after(tk, tx, "2"); ck = PX.loc[(e.sid, e.date)].iloc[kf]
                rec[key] = {"fill_px": fill_px, "t_fill": tf, "exit_close": ck, "exit_bid": bidk if bidk else ck, "uret": (u.iloc[kf] / u.iloc[i0] - 1) * 1e4, "hold": (tx - tf) / 60}
        rows.append(rec)
    d = pd.DataFrame(rows); print(f"有逐筆的事件 {len(d)}")
    def rep(lab, ret, ex, mask):
        out = f"{lab:44s}"
        for sl, m in (("IS", d["is"]), ("OOS", ~d["is"])):
            mm = m & mask
            if mm.sum() < 20: out += f" | {sl} n<20"; continue
            mu, t = cl_t(ex[mm].values, d.loc[mm, "date"].values); r = ret[mm].mean()
            out += f" | {sl} n={int(mm.sum()):4d} 超額 {mu:+6.1f} (t{t:+4.1f}) 原始 {r:+6.1f} 扣25 {r-COST:+6.1f} 勝 {(ret[mm]>0).mean()*100:3.0f}%"
        return out
    print("\n==== (a) 延遲 d 秒後以賣一(下一筆買方主動)進,分數≤0 出 ====")
    for exit_lab, exit_col in (("出場=桶收", "closex"), ("出場=買一(悲觀)", "exit_bid")):
        print(f"-- {exit_lab}")
        ret0 = (d["closex"] / d["close0"] - 1) * 1e4 if exit_col == "closex" else (d["exit_bid"] / d["close0"] - 1) * 1e4
        print(rep("桶收價進(面板口徑)", ret0, ret0 - d["uret"], d["close0"].notna()))
        for dd in (0, 1, 5, 30, 60):
            for side, col in (("賣一", f"ask_d{dd}"), ("任一筆", f"any_d{dd}")):
                p = d[col]; ret = (d[exit_col] / p - 1) * 1e4; mask = p.notna()
                print(rep(f"延遲 {dd:2d}s {side}進", ret, ret - d["uret"], mask))
    print("\n==== (b) 深單(桶收價 −x bps,掛 30 分),成交後分數≤0 出(上限 60 分) ====")
    for x in (30, 50, 100):
        for mode, lab in (("strict", "嚴格穿越"), ("opt", "≤限價(樂觀)")):
            key = f"dip{x}_{mode}"; sub = d[key].notna()
            if sub.sum() < 20: print(f"−{x} {lab}: 成交 {sub.sum()} 筆 n<20"); continue
            g = d[sub]; fill = pd.DataFrame(list(g[key]))
            for exit_lab, ecol in (("出場=桶收", "exit_close"), ("出場=買一", "exit_bid")):
                ret = (fill[ecol].values / fill["fill_px"].values - 1) * 1e4; ex = ret - fill["uret"].values
                out = f"−{x:3d} {lab:10s} {exit_lab:8s} 成交率 {sub.mean()*100:3.0f}% 均持 {fill['hold'].mean():4.0f}分"
                for sl, m in (("IS", g["is"].values), ("OOS", ~g["is"].values)):
                    if m.sum() < 20: out += f" | {sl} n<20"; continue
                    mu, t = cl_t(ex[m], g.loc[m, "date"].values); out += f" | {sl} n={int(m.sum()):4d} 超額 {mu:+6.1f} (t{t:+4.1f}) 原始 {ret[m].mean():+6.1f} 扣25 {ret[m].mean()-COST:+6.1f}"
                print(out)
            un = d[~sub]; r60 = ((un["closex"] / un["close0"] - 1) * 1e4)
            print(f"      未成交 {len(un)} 筆:依分數出場的桶收報酬均 {r60.mean():+.1f}")


if __name__ == "__main__":
    sys.exit(main())
