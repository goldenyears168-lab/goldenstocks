#!/usr/bin/env python3
"""讀 clone_agent_tri_filter_analysis 產生的 X.pkl:最差紀錄、閘門必要性、篩選器同母體保留 vs 剔除、置換檢定。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

S = Path.home() / "goldenstocks-data/data/scratch/w5t"
X = pd.read_pickle(S / "X.pkl")
pd.set_option("display.width", 250, "display.max_columns", 60, "display.max_rows", 300)
rng = np.random.default_rng(7)
P = X[X.inpop].copy()
P["cl"] = P.day + "_" + P.sid.astype(str)


def cboot(v, cl, n=2000):
    g = pd.DataFrame({"v": v.values, "c": cl.values}).groupby("c").v.agg(["sum", "count"])
    s, c = g["sum"].values, g["count"].values
    idx = rng.integers(0, len(g), (n, len(g)))
    mu = s[idx].sum(1) / c[idx].sum(1)
    return np.percentile(mu, [5, 95])


def line(name, d):
    if len(d) == 0:
        return dict(組=name, n=0)
    lo, hi = cboot(d.gross_bps, d.cl)
    return dict(組=name, n=len(d), 簇=d.cl.nunique(), 勝率=round(100 * (d.net > 0).mean()), 毛bps=round(d.gross_bps.mean(), 1),
                超額bps=round(d.excess_bps.mean(), 1), 每筆淨=round(d.net.mean()), 毛90CI=f"{lo:.0f}~{hi:.0f}")


print("===== A. 母體(三尺度T+冷卻,拿掉距高/成交額閘門) =====")
rows = [line("母體 全部", P), line("母體 ∧ 舊寬門檻(距高≤-0.5 ∧ 成交額≥30)", P[P.old_gate]), line("母體 ∖ 舊寬門檻", P[~P.old_gate])]
print(pd.DataFrame(rows).to_string(index=False))

print("\n===== B. 閘門必要性(母體內分桶) =====")
rows = []
for nm, f in (("距高 ≤-1%", P.dist <= -1), ("距高 -1~-0.5%", (P.dist > -1) & (P.dist <= -0.5)), ("距高 -0.5~0%", P.dist > -0.5),
              ("成交額 <30百分位", P.amtp < 30), ("成交額 30~70", (P.amtp >= 30) & (P.amtp < 70)), ("成交額 ≥70", P.amtp >= 70)):
    rows.append(line(nm, P[f]))
print(pd.DataFrame(rows).to_string(index=False))

print("\n===== C. 最差 10 筆(母體∧舊寬門檻;依淨損益) =====")
G = P[P.old_gate].sort_values("net")
cols = ["day", "time", "sid", "name", "net", "gross_bps", "bs5", "bs30", "bsd", "rs5", "rs30", "r_big30", "r_ret30", "r_bigd", "chg", "amtp", "dist", "hh", "mate_T", "g_rank_big30"]
print(G[cols].head(10).round(3).to_string(index=False))

FILT = {
    "大戶5分淨額>0(排除0)": lambda d: d.big5_net > 0,
    "大戶30分佔比排名≤35%": lambda d: d.r_big30 <= 0.35,
    "散戶30分賣超排名≤35%": lambda d: d.r_ret30 <= 0.35,
    "大−散30分排名≤35%": lambda d: d.r_diff30 <= 0.35,
    "全日大戶佔比排名≤35%": lambda d: d.r_bigd <= 0.35,
    "5分大戶佔比排名≤35%": lambda d: d.r_big5 <= 0.35,
    "5分散戶賣超排名≤35%": lambda d: d.r_ret5 <= 0.35,
    "族群內漲幅≤50(嚴④)": lambda d: d.chg.notna() & (d.chg <= 50),
    "成交額≥70(嚴②)": lambda d: d.amtp >= 70,
    "距高≤-1%(嚴③)": lambda d: d.dist <= -1,
    "11:15前": lambda d: d.hh < 11.25,
    "10:00後": lambda d: d.hh >= 10,
    "族群內另有≥1檔也T": lambda d: d.mate_T >= 1,
    "族群內大戶30分佔比排名前半": lambda d: d.g_rank_big30 <= 0.5,
}


def eval_filters(D, label):
    base = D.gross_bps.mean()
    rows = []
    for nm, f in FILT.items():
        k = f(D).fillna(False)
        K, Dr = D[k], D[~k]
        if len(K) < 5 or len(Dr) < 5:
            continue
        rows.append(dict(篩選器=nm, 保留n=len(K), 保留毛=round(K.gross_bps.mean(), 1), 保留超額=round(K.excess_bps.mean(), 1), 保留勝率=round(100 * (K.net > 0).mean()),
                         剔除n=len(Dr), 剔除毛=round(Dr.gross_bps.mean(), 1), 剔除超額=round(Dr.excess_bps.mean(), 1),
                         改善毛=round(K.gross_bps.mean() - base, 1), 改善超額=round(K.excess_bps.mean() - D.excess_bps.mean(), 1)))
    R = pd.DataFrame(rows).sort_values("改善毛", ascending=False)
    print(f"\n===== {label}(母體 n={len(D)},基準 毛{base:.1f} 超額{D.excess_bps.mean():.1f}) =====")
    print(R.to_string(index=False))
    return R


R1 = eval_filters(P, "D. 篩選器:母體全部")
R2 = eval_filters(P[P.old_gate], "E. 篩選器:母體∧舊寬門檻")

print("\n===== F. 置換檢定:14個篩選器裡『最佳改善』偶然達到的機率(整簇置換) =====")
for label, D in (("母體全部", P), ("母體∧舊寬門檻", P[P.old_gate])):
    D = D.reset_index(drop=True)
    masks = {nm: f(D).fillna(False).values for nm, f in FILT.items()}
    masks = {k: v for k, v in masks.items() if 5 <= v.sum() <= len(D) - 5}
    y = D.gross_bps.values
    obs = max(y[v].mean() - y.mean() for v in masks.values())
    cl = D.cl.values
    ucl = np.unique(cl)
    best = []
    for _ in range(3000):
        # 簇層級打亂:整簇的結果向量對調到別的簇(簇內順序保留,長度不同者截斷/循環補齊)
        perm = rng.permutation(len(ucl))
        yy = y.copy()
        for a, b in zip(ucl, ucl[perm]):
            ia, ib = np.where(cl == a)[0], y[cl == b]
            yy[ia] = np.resize(ib, len(ia))
        best.append(max(yy[v].mean() - yy.mean() for v in masks.values()))
    print(f"{label}: 觀察到最佳改善 {obs:.1f} bps;置換後最佳改善的中位 {np.median(best):.1f}、95百分位 {np.percentile(best,95):.1f};p={np.mean(np.array(best) >= obs):.3f}")
