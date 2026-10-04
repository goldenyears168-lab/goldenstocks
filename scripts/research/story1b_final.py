#!/usr/bin/env python3
"""故事 1B 最後一關：用「同分鐘橫斷面 long-short」口徑驗每個條件的真增量（不受去重選點影響）。"""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, "/Users/jackm4/goldenstocks/scripts/research")
import numpy as np, pandas as pd
import story1b_lib as L
p, core = L.load(); C = p[p.is_core].copy()

def ls(d, hi, lo, ycol, minn=1):
    recs = []
    for k, g in d.groupby(["day", "hm"]):
        a = g[hi.reindex(g.index).fillna(False)][ycol]
        b = g[lo.reindex(g.index).fillna(False)][ycol]
        if len(a) < minn or len(b) < minn: continue
        recs.append({"day": k[0], "sp": a.mean() - b.mean(), "hi": a.mean(), "lo": b.mean()})
    s = pd.DataFrame(recs)
    if len(s) < 5: return None
    dm = s.groupby("day").sp.mean()
    return (len(s), s.sp.mean(), dm.mean()/(dm.std(ddof=1)/np.sqrt(len(dm))), int((dm>0).sum()), len(dm), s.hi.mean(), s.lo.mean())

tests = {
 "【B】量縮−放量 | 自身30m<0": (C.r30 < 0, C.amt5_z <= -0.5, C.amt5_z >= 0),
 "【B】量縮−放量 | 自身30m>0": (C.r30 > 0, C.amt5_z <= -0.5, C.amt5_z >= 0),
 "【B】急凍−爆量 | 自身30m<0": (C.r30 < 0, C.amt5_z <= -1, C.amt5_z >= 1),
 "【B】急凍−爆量 | 自身30m>0": (C.r30 > 0, C.amt5_z <= -1, C.amt5_z >= 1),
 "【B】量縮−放量 | 市場跌z15<=-1∧自身30m<0": ((C.mkt15_z<=-1)&(C.r30<0), C.amt5_z<=-0.5, C.amt5_z>=0),
 "【核心】大戶30m>0 − <=0 | 自身30m<0": (C.r30 < 0, C.big_30m > 0, C.big_30m <= 0),
 "【核心】大戶30m>0 − <=0 | 自身30m>0": (C.r30 > 0, C.big_30m > 0, C.big_30m <= 0),
 "【核心】大戶30m>0 − <=0 | 全部分鐘": (pd.Series(True,index=C.index), C.big_30m > 0, C.big_30m <= 0),
 "【A】res15前20%−後20% | 市場跌z15<=-1": (C.mkt15_z<=-1, C.res15_pct>=80, C.res15_pct<=20),
 "【A】res15前20%−後20% | 市場跌z15<=-1∧大戶day前30%": ((C.mkt15_z<=-1)&(C.big_day_pct>=70), C.res15_pct>=80, C.res15_pct<=20),
 "【A】res15前20%−後20% | 市場漲z15>=1": (C.mkt15_z>=1, C.res15_pct>=80, C.res15_pct<=20),
 "【A】res15前20%−後20% | 市場平": (C.mkt15_z.abs()<=0.3, C.res15_pct>=80, C.res15_pct<=20),
 "【A】自身r15>=0 − r15<0 | 市場跌z15<=-1": (C.mkt15_z<=-1, C.r15>=0, C.r15<0),
 "【A】自身r15>=0 − r15<0 | 市場跌z15<=-1.5": (C.mkt15_z<=-1.5, C.r15>=0, C.r15<0),
}
for ycol in ("x_fwd30", "x_fwd60", "x_fwd_close"):
    print(f"\n==== y={ycol} （同分鐘 long-short, 日聚類）")
    for nm, (sub, hi, lo) in tests.items():
        d = C[sub.fillna(False) & C[ycol].notna()]
        r = ls(d, hi, lo, ycol)
        if r is None: print(f"  {nm:46s} 分鐘不足"); continue
        n, sp, t, dp, nd, h, l = r
        print(f"  {nm:46s} 分鐘={n:4d} spread={sp:+.3f}% t={t:+.2f} 日正={dp}/{nd} (高{h:+.3f} 低{l:+.3f})")
