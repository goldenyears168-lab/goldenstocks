#!/usr/bin/env python3
"""轉色訊號的下單延遲與可執行性（唯讀研究）。事件＝exhaust_stall_test 的轉色（連次≥10 後反向主導窗收盤，PIT）。
組：A 全部轉色；B 累計連量≥2 分；C = B ∧ 爆量(最後6窗≥3×均速) ∧ 漲不動(最後6窗未創段內新高)（小哥口訣『爆量漲不動』）。
進場延遲 δ∈{0,2,5,10,30,60,120} 秒（訊號後 δ 秒的第一筆有效報價），持有 h∈{60,180,300} 秒。
可執行損益：買盤竭盡＝放空，進場賣在 bid、出場買在 ask；賣盤竭盡＝做多，進場買在 ask、出場賣在 bid。另報中價毛損益與進場時價差。
"""
import glob, sys
from bisect import bisect_left
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

E = pd.read_pickle(Path.home() / "goldenstocks-data/research/exhaust_stall_events.pkl")
E = E.dropna(subset=["stall6"]).copy()
E["B"] = E.cum_x >= 2; E["C"] = E.B & (E.stall6 == 1) & (E.heavy6 == 1)
DL = [0, 2, 5, 10, 30, 60, 120]; HH = [60, 180, 300]
rows = []
for d, ed in E.groupby("date"):
    L.BIN = 5.0; pan = L.load_day(N.P, d)
    for s, es in ed.groupby("sym"):
        if s not in pan: continue
        a = pan[s]; side, bid, ask = L.classify(a); c = a[:, 5] == 0
        t = a[c, 0]; b, k_ = bid[c], ask[c]
        ok = np.isfinite(b) & np.isfinite(k_) & (b > 0) & (k_ > b)
        if ok.sum() < 100: continue
        tt, bb, aa = t[ok], b[ok], k_[ok]
        def q(x):
            i = min(bisect_left(tt, x), len(tt) - 1)
            return bb[i], aa[i], tt[i]
        for r in es.itertuples():
            for dl in DL:
                b0, a0, t0 = q(r.t + dl)
                if t0 - (r.t + dl) > 20: continue
                for h in HH:
                    b1, a1, t1 = q(r.t + dl + h)
                    if t1 - (r.t + dl + h) > 20 or r.t + dl + h > L.CLOSE_T: continue
                    m0, m1 = (b0 + a0) / 2, (b1 + a1) / 2
                    if r.dir > 0:   # 買盤竭盡→放空
                        net = (b0 - a1) / b0 * 1e4; gross = (m0 - m1) / m0 * 1e4
                    else:           # 賣盤竭盡→做多
                        net = (b1 - a0) / a0 * 1e4; gross = (m1 - m0) / m0 * 1e4
                    rows.append({"date": d, "sym": s, "A": True, "B": r.B, "C": r.C, "dl": dl, "h": h, "net": net, "gross": gross, "spr": (a0 - b0) / m0 * 1e4})
R = pd.DataFrame(rows)
pd.set_option("display.width", 200); pd.set_option("display.max_rows", 300)
def tstat(x):
    g = x.groupby("date").mean() if isinstance(x, pd.Series) else x
    return g.mean() / (g.std(ddof=1) / np.sqrt(len(g))) if len(g) > 4 else np.nan
for lab in ("A", "B", "C"):
    print(f"\n=== 組 {lab}：{ {'A': '全部轉色', 'B': '累計連量≥2分', 'C': '累計≥2分∧爆量∧漲不動'}[lab] }")
    out = []
    for (dl, h), g in R[R[lab]].groupby(["dl", "h"]):
        out.append({"延遲s": dl, "持有s": h, "n": len(g), "中價毛bps": g.gross.mean(), "可執行淨bps": g.net.mean(), "t(淨)": tstat(g.set_index("date").net), "價差中位bps": g.spr.median(), "淨>0占比": (g.net > 0).mean()})
    print(pd.DataFrame(out).round(2).to_string(index=False))
# 價差分層（組 B、δ=2s、h=180）：價差小的股票是否可執行
x = R[R.B & (R.dl == 2) & (R.h == 180)].copy()
x["spr_bin"] = pd.cut(x.spr, [0, 8, 12, 18, 30, 1000], labels=["≤8", "8-12", "12-18", "18-30", ">30"])
print("\n=== 價差分層（組 B；延遲 2 秒；持有 3 分）")
print(x.groupby("spr_bin", observed=True).agg(n=("net", "size"), gross=("gross", "mean"), net=("net", "mean"), hit=("net", lambda v: (v > 0).mean())).round(2).to_string())
