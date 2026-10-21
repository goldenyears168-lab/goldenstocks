#!/usr/bin/env python3
"""組合技檢定 · 跟進（唯讀）：1) 樹深度 1（加法）vs 3 vs 4 的樣本外對比＝交互的價值；2) 分季穩定度；3) 成本／止損敏感度；
4) 最差預測五分位的特徵輪廓；5) 全域置換重要性（切 60/40）。依賴 combo_dataset.parquet（xiaoge_combo_study.py 產生）。
"""
import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import xiaoge_combo_study as C
from sklearn.ensemble import HistGradientBoostingRegressor

s = pd.read_parquet(C.OUT / "combo_dataset.parquet")
F = C.FEATS_PRE


def wf(depth, feats, iters=150, leaf=60):
    days = sorted(s.d.unique()); months = sorted({d[:7] for d in days}); pred = pd.Series(np.nan, index=s.index)
    for m in months[6:]:
        te = s[s.d.str[:7] == m]
        if te.empty: continue
        tr = s[s.d < days[max(0, days.index(te.d.min()) - 5)]]
        if len(tr) < 800: continue
        X, Xt = tr[feats].to_numpy(float), te[feats].to_numpy(float)
        lo, hi = np.nanpercentile(X, 1, axis=0), np.nanpercentile(X, 99, axis=0)
        mdl = HistGradientBoostingRegressor(max_depth=depth, learning_rate=0.04, max_iter=iters, min_samples_leaf=leaf, l2_regularization=10.0, random_state=0).fit(np.clip(X, lo, hi), tr.y)
        pred[te.index] = mdl.predict(np.clip(Xt, lo, hi))
    return pred


def daily_net(t, cost, stop=None, q=5):
    rows = []
    for d, g in t.groupby("d"):
        if len(g) < 10: continue
        gg = g.nsmallest(max(2, len(g) // q), "p")
        pnl = -gg.oc_pct.to_numpy()
        if stop is not None:
            pnl = np.where(gg.adv.to_numpy() * 100 >= stop, -stop, pnl)
        rows.append((d, pnl.mean() - cost, len(gg)))
    return pd.DataFrame(rows, columns=["d", "net", "k"])


def tstat(x): x = np.asarray(x); return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


preds = {}
for depth in (1, 2, 3, 4):
    p = wf(depth, F); preds[depth] = p
    e = C.evaluate(s, p, f"depth{depth}")
    print(f"深度{depth}: IC {e['rank_ic']:+.3f} (t {e['ic_t']:+.1f}) | 最低20% oc {e['q_low_oc_pct']:+.2f}% 超額 {e['q_low_excess_pct']:+.2f}% (t {e['q_low_t']:+.1f}) | 做空淨 {e['short_net_pct_per_day']:+.2f}%/日 t {e['short_net_t']:+.1f} 勝日 {e['short_hit_days']:.0%}", flush=True)
t = s.assign(p=preds[3]).dropna(subset=["p"])
print("\n[分季穩定度] 深度3 最低20% 做空淨(扣0.3%)：")
t["q"] = pd.PeriodIndex(pd.to_datetime(t.d), freq="Q").astype(str)
for q, g in t.groupby("q"):
    dn = daily_net(g, 0.3); print(f"  {q}: {dn.net.mean():+.2f}%/日 (t {tstat(dn.net):+.1f}, {len(dn)} 天, 勝日 {(dn.net>0).mean():.0%})")
print("\n[成本與止損敏感度]")
for cost in (0.3, 0.45, 0.6):
    for stop in (None, 5.0):
        dn = daily_net(t, cost, stop); print(f"  成本{cost}% 止損{'無' if stop is None else '+5%強停'}: {dn.net.mean():+.2f}%/日 t {tstat(dn.net):+.1f} 勝日 {(dn.net>0).mean():.0%} 最差日 {dn.net.min():+.1f}%")
low = t.groupby("d", group_keys=False).apply(lambda g: g.nsmallest(max(2, len(g) // 5), "p"))
rest = t.drop(low.index)
print("\n[最差預測五分位的特徵輪廓：低組均值 vs 其餘；標準化差]")
prof = []
for f in F + ["gap_pct"]:
    sd = t[f].std()
    if sd and sd == sd: prof.append((f, low[f].mean(), rest[f].mean(), (low[f].mean() - rest[f].mean()) / sd))
for f, a, b, z in sorted(prof, key=lambda r: -abs(r[3]))[:10]:
    print(f"  {f:<15} 低組 {a:>9.3f} 其餘 {b:>9.3f} 差 {z:+.2f}σ")
print(f"  低組 T+1 向上不利(adv≥5%) 占比 {(low.adv>=0.05).mean():.1%}，其餘 {(rest.adv>=0.05).mean():.1%}；低組 T+1 漲停鎖死(收盤=漲停)近似 {(low.cc>=0.095).mean():.1%}")
# 置換重要性（前 60% 日期學、後 40% 測）
days = sorted(s.d.unique()); cut = days[int(len(days) * 0.6)]
tr, te = s[s.d < cut], s[s.d >= cut]
lo, hi = np.nanpercentile(tr[F].to_numpy(float), 1, axis=0), np.nanpercentile(tr[F].to_numpy(float), 99, axis=0)
mdl = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.04, max_iter=150, min_samples_leaf=60, l2_regularization=10.0, random_state=0).fit(np.clip(tr[F].to_numpy(float), lo, hi), tr.y)
Xt = np.clip(te[F].to_numpy(float), lo, hi); base = np.corrcoef(mdl.predict(Xt), te.y)[0, 1]
rng = np.random.default_rng(0); imp = []
for j, f in enumerate(F):
    drops = []
    for _ in range(5):
        X2 = Xt.copy(); X2[:, j] = rng.permutation(X2[:, j]); drops.append(base - np.corrcoef(mdl.predict(X2), te.y)[0, 1])
    imp.append((f, float(np.mean(drops))))
print(f"\n[置換重要性（後40%，基準相關 {base:+.3f}）]")
for f, v in sorted(imp, key=lambda r: -r[1])[:10]: print(f"  {f:<15} {v:+.4f}")
