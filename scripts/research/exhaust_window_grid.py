#!/usr/bin/env python3
"""窗長 W∈{2,3,4,5,6} 秒 × RVOL(近30秒)∈{2,2.5,3}× × 連次N × 累計連量，目標：每檔每天多空合計約 20 個。前半日期選、後半驗證。"""
import itertools, os
import numpy as np, pandas as pd
H = os.path.expanduser("~/goldenstocks-data/research/")
E = pd.read_pickle(H + "exhaust_window_events.pkl"); SD = int(open(H + "exhaust_window_nsd.txt").read())
dates = sorted(E.date.unique()); tr = set(dates[:len(dates) // 2]); te = set(dates[len(dates) // 2:])
nsd = {"all": SD, "tr": SD * len(tr) / len(dates), "te": SD * len(te) / len(dates)}
def st(x, ns):
    y = x.dropna(subset=["r180"])
    if len(y) < 80: return None
    g = y.groupby("date")["r180"].mean()
    return dict(per=len(x) / ns, r1=x.r60.mean(), r3=y.r180.mean(), r5=x.r300.mean(), t=g.mean() / (g.std(ddof=1) / np.sqrt(len(g))) if len(g) > 4 else np.nan, hit=(y.r180[y.r180 != 0] > 0).mean(), buy=(x.dir > 0).mean())
rows = []
for W, RV, N, S2 in itertools.product([2, 3, 4, 5, 6], [2, 2.5, 3], [3, 4, 5, 6, 8, 10, 12], [0, 0.5, 1, 2, 3]):
    x = E[(E.W == W) & (E.S1 >= RV) & (E.n >= N) & (E.S2 >= S2)]
    a, b, c = st(x[x.date.isin(tr)], nsd["tr"]), st(x[x.date.isin(te)], nsd["te"]), st(x, nsd["all"])
    if a and b and c: rows.append(dict(W=W, RV=RV, N=N, S2=S2, per=c["per"], r1=c["r1"], r3=c["r3"], r5=c["r5"], t=c["t"], hit=c["hit"], buy=c["buy"], r3_tr=a["r3"], r3_te=b["r3"], per_tr=a["per"]))
R = pd.DataFrame(rows); R.to_csv(H + "exhaust_window_grid.csv", index=False)
pd.set_option("display.width", 230); pd.set_option("display.max_rows", 200)
T = R[(R.per >= 17) & (R.per <= 23)]
print("[每個 (窗長, RVOL) 在『每檔每天 17～23 個（多空合計）』內，3 分鐘含零平均最高的組合]")
best = T.sort_values("r3", ascending=False).groupby(["W", "RV"]).head(1).sort_values(["W", "RV"])
print(best[["W", "RV", "N", "S2", "per", "buy", "r1", "r3", "r5", "t", "hit", "r3_tr", "r3_te"]].round(2).to_string(index=False))
print("\n[窗長比較：各窗長在 17～23 個/日內的最佳（任一 RVOL）]")
b2 = T.sort_values("r3", ascending=False).groupby("W").head(1).sort_values("W")
print(b2[["W", "RV", "N", "S2", "per", "buy", "r1", "r3", "r5", "t", "r3_tr", "r3_te"]].round(2).to_string(index=False))
print("\n[總排名前 10（17～23 個/日）]")
print(T.sort_values("r3", ascending=False).head(10)[["W", "RV", "N", "S2", "per", "buy", "r1", "r3", "r5", "t", "r3_tr", "r3_te"]].round(2).to_string(index=False))
print("\n[對照：W=5 秒（官方）的 RVOL 2/2.5/3 倍、不加連次/累計條件(N≥3,S2≥0) 的訊號數與報酬]")
print(R[(R.W == 5) & (R.N == 3) & (R.S2 == 0)][["RV", "per", "r1", "r3", "r5", "t", "hit"]].round(2).to_string(index=False))
print("\n[各窗長 RVOL≥2.5×、N≥3 的訊號數與報酬(看窗長本身的影響)]")
print(R[(R.RV == 2.5) & (R.N == 3) & (R.S2 == 0)][["W", "per", "r1", "r3", "r5", "t", "hit"]].round(2).to_string(index=False))
