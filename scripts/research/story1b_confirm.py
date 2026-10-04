#!/usr/bin/env python3
"""故事 1B 最佳格的拆解與壓力測試（增量 / 交互 / leave-one-day-out / 每分鐘 long-short）。"""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, "/Users/jackm4/goldenstocks/scripts/research")
import numpy as np, pandas as pd
import story1b_lib as L

OUT = pathlib.Path.home() / "goldenstocks-data/data/scratch"
p, core = L.load()
FC = "x_fwd_close"
rows = []

print("### A. 最佳格的成分拆解（去重口徑, y=fwd_close 與 fwd60）")
down, quiet, bigbuy = p.r30 < 0, p.amt5_z <= -0.5, p.big_30m > 0
comp = {
    "①自身30m<0": down,
    "②大戶30m>0": bigbuy,
    "③量縮z<=-0.5": quiet,
    "①∧②": down & bigbuy,
    "①∧③": down & quiet,
    "②∧③": bigbuy & quiet,
    "①∧②∧③ = 洗盤格": down & bigbuy & quiet,
    "鏡像:自身30m>0∧②∧③": (p.r30 > 0) & bigbuy & quiet,
    "鏡像:①∧大戶30m<=0∧③": down & (~bigbuy) & quiet,
    "鏡像:①∧②∧爆量z>=1": down & bigbuy & (p.amt5_z >= 1),
}
for nm, m in comp.items():
    for c in ("x_fwd60", FC):
        r = L.evaluate(p, m, nm, col=c, B=600, verbose=False); rows.append(r)
        print(f"  {nm:24s} {c:12s} trips={r['n_trips']:3d} excess={r['excess']:+.3f}% "
              f"xwin={r['xwin']:5.1f}% t_day={r['t_day']:+.2f} 選股後α={r['alpha_timing']:+.3f}"
              f"(t={r['t_timing']:+.2f}) maxsid={r['max_sid_share']:+.0%}({r['max_sid']}) "
              f"剔最大={r['ex_top_sid']:+.3f} 剔前2大={r['drop_top2']:+.3f} "
              f"日正={r['days_pos']}/{r['n_days']} p99={r['null_p99']:+.3f} pass={r['pass_p99']}")

print("\n### B. 洗盤格 leave-one-day-out（4 天各剔一天）")
m = (down & bigbuy & quiet).fillna(False) & p.is_core
tr = L.dedup(p[m & p[FC].notna()])
for d in sorted(tr.day.unique()):
    s = tr[tr.day != d]
    print(f"  剔除 {d}: n={len(s):3d} excess={s[FC].mean():+.3f}% xwin={100*(s[FC]>0).mean():.1f}%")
print("  各日: " + "  ".join(f"{d}={g[FC].mean():+.3f}%(n={len(g)})" for d, g in tr.groupby('day')))
print("  各檔貢獻 top5: " + ", ".join(f"{k}={v:+.2f}" for k, v in
      tr.groupby('sid')[FC].sum().sort_values(ascending=False).head(5).items()))

print("\n### C. 每分鐘橫斷面 long-short（不受去重影響的口徑）：大戶30m>0 分鐘內,量縮 − 爆量")
Cc = p[p.is_core].copy()
for ycol in ("x_fwd30", "x_fwd60", FC):
    for nm, sub in [("①自身30m<0∧②大戶30m>0", (Cc.r30 < 0) & (Cc.big_30m > 0)),
                    ("自身30m>0∧②大戶30m>0", (Cc.r30 > 0) & (Cc.big_30m > 0)),
                    ("①自身30m<0∧大戶30m<=0", (Cc.r30 < 0) & (Cc.big_30m <= 0))]:
        g = Cc[sub & Cc[ycol].notna()]
        recs = []
        for k, gg in g.groupby(["day", "hm"]):
            q = gg[gg.amt5_z <= -0.5][ycol].mean(); e = gg[gg.amt5_z >= 0][ycol].mean()
            if np.isnan(q) or np.isnan(e): continue
            recs.append({"day": k[0], "sp": q - e})
        s = pd.DataFrame(recs)
        if len(s) < 5: print(f"  {ycol} {nm}: 分鐘不足{len(s)}"); continue
        dm = s.groupby("day").sp.mean()
        print(f"  {ycol:12s} {nm:24s} 分鐘={len(s):4d} 量縮−放量={s.sp.mean():+.3f}% "
              f"日聚類t={dm.mean()/(dm.std(ddof=1)/np.sqrt(len(dm))):+.2f} 日正={int((dm>0).sum())}/{len(dm)}")

print("\n### D. 洗盤格 vs 故事九 的重疊")
s9 = ((p.big_30m > 0) & (p.ret_30m < 0) & (p["距當日高%"] <= -1) & (p.er >= 0.143) & (p.recover == 0)).fillna(False) & p.is_core
a = set(map(tuple, L.dedup(p[m & p[FC].notna()])[["day", "sid"]].to_numpy()))
b = set(map(tuple, L.dedup(p[s9 & p[FC].notna()])[["day", "sid"]].to_numpy()))
print(f"  洗盤格 {len(a)} 趟 / 故事九 {len(b)} 趟 / 交集 {len(a & b)} 趟")
rr = L.evaluate(p, m & ~s9, "洗盤格剔掉故事九分鐘", col=FC, B=600, verbose=False)
print(f"  洗盤格去掉與故事九重疊的列: trips={rr['n_trips']} excess={rr['excess']:+.3f}% t_day={rr['t_day']:+.2f}")

pd.DataFrame(rows).to_csv(OUT / "story1b_confirm.csv", index=False)
print(f"\nwrote {OUT/'story1b_confirm.csv'}")
