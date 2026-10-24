#!/usr/bin/env python3
"""組合技 · 加入『自營商避險買賣超』（官方 T86／櫃買拆分，xiaoge_dealer_split.py）後重測（唯讀）。
特徵（T 日收盤後公布，PIT）：hedge_r（避險買賣超÷成交股數 %）、self_r（自行買賣）、hedge_buy_r（避險買進÷成交股數 %）、hedge_r2（T、T−1 合計）、hedge_pos（避險買超且占比≥2%）。
1) 單因子 rank-IC；2) 走動式：基準、＋合計自營（上一輪）、＋避險拆分；3) 小哥式規則（事前寫死）：避險買超占比≥2% 且 有隔日沖慣犯 且 外資投信未買；
   與『自營商合計≥2%』版本對照，看拆出避險是否更接近小哥的自營比。
"""
import sys, sqlite3, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import xiaoge_combo_study as C
from stock_db import DATA_DIR, DEFAULT_DB_PATH

s = pd.read_parquet(C.OUT / "combo_dataset.parquet")
parts = [pd.read_parquet(p) for p in sorted((DATA_DIR / "chip" / "dealer_split").glob("20*.parquet"))]
for p, f in zip(sorted((DATA_DIR / "chip" / "dealer_split").glob("20*.parquet")), parts):
    f["d"] = p.stem
ds = pd.concat(parts, ignore_index=True).drop_duplicates(["stock_id", "d"])
c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
vol = pd.read_sql_query("select stock_id, trade_date d, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date between '2024-09-01' and '2026-07-31' and length(stock_id)=4", c).drop_duplicates(["stock_id", "d"])
inst = pd.read_sql_query("select stock_id, trade_date d, foreign_net, investment_trust_net trust_net from stock_institutional_daily where source in ('twse_t86','tpex_insti') and trade_date between '2024-09-01' and '2026-07-31' and length(stock_id)=4", c).drop_duplicates(["stock_id", "d"])
ds = ds.merge(vol, on=["stock_id", "d"], how="left").merge(inst, on=["stock_id", "d"], how="left").sort_values(["stock_id", "d"])
for k, col in (("hedge_r", "dealer_hedge_net"), ("self_r", "dealer_self_net"), ("total_r", "dealer_total_net"), ("hedge_buy_r", "dealer_hedge_buy"), ("foreign_r", "foreign_net"), ("trust_r", "trust_net")):
    ds[k] = ds[col] / ds.volume * 100
ds["hedge_r2"] = ds.groupby("stock_id").hedge_r.transform(lambda x: x + x.shift(1))
s = s.merge(ds[["stock_id", "d", "hedge_r", "self_r", "total_r", "hedge_buy_r", "hedge_r2", "foreign_r", "trust_r"]], on=["stock_id", "d"], how="left")
print(f"樣本 {len(s)}；有避險拆分 {s.hedge_r.notna().mean():.1%}；避險買超中位 {s.hedge_r.median():.2f}%，避險買超≥2% 占 {(s.hedge_r >= 2).mean():.1%}；合計≥2% 占 {(s.total_r >= 2).mean():.1%}")
H = ["hedge_r", "self_r", "hedge_buy_r", "hedge_r2"]
for k in H + ["total_r"]:
    ic, t, nd = C.rank_ic(s.dropna(subset=[k]), k); print(f"單因子 {k:<12} rank-IC {ic:+.3f} (t {t:+.1f}, {nd}天)")
for name, feats, kind in (("基準 樹", C.FEATS_PRE, "gbm"), ("＋避險拆分 樹", C.FEATS_PRE + H, "gbm"), ("基準 加法", C.FEATS_PRE, "ridge"), ("＋避險拆分 加法", C.FEATS_PRE + H, "ridge")):
    p = C.walk_forward(s, feats, kind); e = C.evaluate(s, p, name)
    print(f"{name:<14} IC {e['rank_ic']:+.3f} (t {e['ic_t']:+.1f}) | 最低20% 超額 {e['q_low_excess_pct']:+.2f}% (t {e['q_low_t']:+.1f}) | 做空淨(0.3%) {e['short_net_pct_per_day']:+.2f}%/日 t {e['short_net_t']:+.1f} 勝日 {e['short_hit_days']:.0%}", flush=True)
r = s.dropna(subset=["hedge_r", "total_r", "foreign_r", "trust_r"])
def grp(m):
    g = r[m]
    if len(g) < 5: return f"n={len(g)}"
    dm = g.groupby("d").y.mean(); t = dm.mean() / (dm.std(ddof=1) / np.sqrt(len(dm))) if len(dm) > 3 else float("nan")
    return f"n={len(g):<5} 天={len(dm):<4} oc {g.oc_pct.mean():+.2f}% 超額 {g.y.mean():+.2f}% (日均t {t:+.1f}) 開→收<0 {(g.oc_pct<0).mean():.0%}"
print("\n小哥式規則（事前寫死；T 日漲≥5%、流動性足）")
fi = (r.foreign_r <= 0) & (r.trust_r <= 0)
for k, m in {"避險買超≥2%": r.hedge_r >= 2, "＋隔日沖慣犯": (r.hedge_r >= 2) & (r.n_habit >= 1), "＋外資投信未買(完整)": (r.hedge_r >= 2) & (r.n_habit >= 1) & fi,
             "合計≥2%＋慣犯＋外資投信未買(上輪版)": (r.total_r >= 2) & (r.n_habit >= 1) & fi, "避險買超≥1%＋慣犯＋外資投信未買": (r.hedge_r >= 1) & (r.n_habit >= 1) & fi,
             "避險買超≥2% 且 外資投信未買(不看慣犯)": (r.hedge_r >= 2) & fi, "避險賣超≤−2%": r.hedge_r <= -2}.items():
    print(f"  {k:<28} {grp(m)}")
