#!/usr/bin/env python3
"""故事 1B 深層診斷：拆開「去重假象 / 選股 vs 選時機 / 絕對 vs 相對 / 量的交互作用」。"""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, "/Users/jackm4/goldenstocks/scripts/research")
import numpy as np, pandas as pd
import story1b_lib as L

OUT = pathlib.Path.home() / "goldenstocks-data/data/scratch"
p, core = L.load()
C = p[p.is_core].copy()
FC = "x_fwd_close"

def dayt(g, col):
    dm = g.groupby("day")[col].mean().dropna()
    return (dm.mean(), dm.mean() / (dm.std(ddof=1) / np.sqrt(len(dm))), len(dm)) if len(dm) > 1 else (np.nan,)*3

print("### 1. 去重假象檢查：只用「市場狀態」當門檻（完全沒有選股條件）")
for nm, m in [("市場跌 Z15<=-1", p.mkt15_z <= -1), ("市場跌 Z15<=-1.5", p.mkt15_z <= -1.5),
              ("市場平盤 |Z15|<=0.3", p.mkt15_z.abs() <= 0.3), ("市場漲 Z15>=1", p.mkt15_z >= 1),
              ("全樣本", pd.Series(True, index=p.index))]:
    r = L.evaluate(p, m, nm, col=FC, B=300, verbose=False)
    print(f"  {nm:22s} rows={r['n_rows']:5d} trips={r['n_trips']:3d} "
          f"excess={r['excess']:+.3f}% xwin={r['xwin']:.1f}% t_day={r['t_day']:+.2f} "
          f"null_mean={r['null_mean']:+.3f} p99={r['null_p99']:+.3f}")

print("\n### 2. 獨立 episode：市場下跌段 (連續 z15<=-1)")
mk = p.groupby(["day", "hm"], as_index=False).agg(z15=("mkt15_z", "first"), m15=("mkt15", "first"))
mk = mk.sort_values(["day", "hm"]).reset_index(drop=True)
f = (mk.z15 <= -1).to_numpy()
ep = np.cumsum(f & ~np.r_[False, f[:-1]])
mk["epi"] = np.where(f, ep, 0)
print(mk[mk.epi > 0].groupby(["day", "epi"]).agg(
    起=("hm", "min"), 迄=("hm", "max"), 分鐘=("hm", "size"), 最低15分市場報酬=("m15", "min")).round(3).to_string())
p = p.merge(mk[["day", "hm", "epi"]], on=["day", "hm"], how="left")
C = p[p.is_core].copy()

print("\n### 3. 每分鐘橫斷面 long-short：res15 前五分位 − 後五分位 的前瞻超額")
def ls_spread(d, rankcol, ycol, q=20):
    rows = []
    for k, g in d.groupby(["day", "hm"]):
        g = g[g[ycol].notna() & g[rankcol].notna()]
        if len(g) < 12:
            continue
        hi = g[g[rankcol] >= g[rankcol].quantile(1 - q/100)][ycol].mean()
        lo = g[g[rankcol] <= g[rankcol].quantile(q/100)][ycol].mean()
        rows.append({"day": k[0], "hm": k[1], "spread": hi - lo, "hi": hi, "lo": lo})
    return pd.DataFrame(rows)

states = {"市場跌 z15<=-1": C.mkt15_z <= -1, "市場跌 z15<=-1.5": C.mkt15_z <= -1.5,
          "市場平 |z15|<=0.3": C.mkt15_z.abs() <= 0.3, "市場漲 z15>=1": C.mkt15_z >= 1,
          "全部分鐘": pd.Series(True, index=C.index)}
for ycol in ("x_fwd30", "x_fwd60", "x_fwd_close"):
    print(f"-- y={ycol}")
    for nm, m in states.items():
        s = ls_spread(C[m], "res15", ycol)
        if len(s) < 5:
            print(f"   {nm:20s} 分鐘數不足 ({len(s)})"); continue
        mu, t, nd = dayt(s, "spread")
        print(f"   {nm:20s} 分鐘={len(s):4d} spread={mu:+.3f}% 日聚類t={t:+.2f} "
              f"(hi={s.hi.mean():+.3f} lo={s.lo.mean():+.3f}) 日一致={int((s.groupby('day').spread.mean()>0).sum())}/{nd}")

print("\n### 4. 相對強度 vs 絕對報酬：市場下跌分鐘內的 2x2（控制自身絕對 r15）")
d = C[(C.mkt15_z <= -1) & C.x_fwd_close.notna()]
d = d.assign(自身方向=np.where(d.r15 > 0, "自身漲", np.where(d.r15 < 0, "自身跌", "自身平")),
             相對=np.where(d.res15_pct >= 80, "相對強", np.where(d.res15_pct <= 20, "相對弱", "中")))
print(d.pivot_table(index="自身方向", columns="相對", values="x_fwd_close",
                    aggfunc=["mean", "size"]).round(3).to_string())

print("\n### 5. 假說 B：自身方向 × 自身成交額 z（sid×時段內）的前瞻超額矩陣")
b = C[C.x_fwd_close.notna()].copy()
b["自身30m"] = pd.cut(b.r30, [-99, -0.3, 0, 0.3, 99], labels=["跌>0.3%", "小跌", "小漲", "漲>0.3%"])
b["量z桶"] = pd.cut(b.amt5_z, [-9, -1, -0.5, 0.5, 1, 9], labels=["急凍<=-1", "量縮-1~-0.5", "中", "放量0.5~1", "爆量>=1"])
pt = b.pivot_table(index="自身30m", columns="量z桶", values="x_fwd_close", aggfunc=["mean", "size"], observed=True)
print(pt.round(3).to_string())

print("\n### 6. B 的核心對照：下跌分鐘內 量縮 − 爆量 的每分鐘 spread（去 day×sid 均值前後）")
for ycol in ("x_fwd30", "x_fwd60", "x_fwd_close"):
    for dn, dm in [("自身30m<0", b.r30 < 0), ("自身30m>=0", b.r30 >= 0)]:
        g = b[dm]
        rows = []
        for k, gg in g.groupby(["day", "hm"]):
            q = gg[gg.amt5_z <= -0.5][ycol].mean()
            e = gg[gg.amt5_z >= 1.0][ycol].mean()
            if np.isnan(q) or np.isnan(e):
                continue
            rows.append({"day": k[0], "spread": q - e, "quiet": q, "loud": e})
        s = pd.DataFrame(rows)
        if len(s) < 5:
            print(f"  {ycol} {dn}: 分鐘數不足 {len(s)}"); continue
        mu, t, nd = dayt(s, "spread")
        print(f"  {ycol} {dn}: 分鐘={len(s):4d} 量縮−爆量={mu:+.3f}% 日聚類t={t:+.2f} "
              f"(量縮{s.quiet.mean():+.3f} 爆量{s.loud.mean():+.3f}) 日一致={int((s.groupby('day').spread.mean()>0).sum())}/{nd}")

print("\n### 7. 純選時機（去 day×sid 均值後）：下跌分鐘內 量 z 的斜率")
for dn, dm in [("自身30m<0", b.r30 < 0), ("自身30m>=0", b.r30 >= 0), ("全部", pd.Series(True, index=b.index))]:
    g = b[dm & b.d_fwd_close.notna() & b.amt5_z.notna()]
    if len(g) < 50: continue
    # 日聚類：每天各自迴歸斜率
    sl = []
    for day, gg in g.groupby("day"):
        x = gg.amt5_z - gg.amt5_z.mean(); y = gg.d_fwd_close
        sl.append((x*y).sum()/(x*x).sum())
    sl = np.array(sl)
    print(f"  {dn:12s} n={len(g):5d} 斜率(每+1 z) 日均={sl.mean():+.4f}% "
          f"日聚類t={sl.mean()/(sl.std(ddof=1)/np.sqrt(len(sl))):+.2f} 日符號={ (sl<0).sum() }負/{len(sl)}")

print("\n### 8. A 的 episode 級去重：每個市場下跌 episode × sid 只取一次")
for nm, m in [("res15前10%", (p.res15_pct >= 90)), ("res15前10%∧大戶day前30%", (p.res15_pct >= 90) & (p.big_day_pct >= 70)),
              ("res15後20%(對照)", (p.res15_pct <= 20))]:
    sel = p[(p.epi > 0) & m & p.is_core & p.x_fwd_close.notna()]
    tr = sel.sort_values("t").groupby(["day", "epi", "sid"], as_index=False).first()
    mu, t, nd = dayt(tr, FC)
    contrib = tr.groupby("sid")[FC].sum()
    print(f"  {nm:26s} n={len(tr):3d} (episode={tr.epi.nunique()}) excess={tr[FC].mean():+.3f}% "
          f"xwin={100*(tr[FC]>0).mean():.1f}% 日聚類t={t:+.2f} "
          f"maxsid={contrib.max()/tr[FC].sum() if tr[FC].sum() else np.nan:.0%}({contrib.idxmax()}) "
          f"剔最大={tr[tr.sid!=contrib.abs().idxmax()][FC].mean():+.3f}%")

b[["day","hm","sid","r30","amt5_z","x_fwd_close","mkt15_z","res15_pct"]].to_csv(OUT/"story1b_rows.csv", index=False)
print(f"\nwrote {OUT/'story1b_rows.csv'}")
