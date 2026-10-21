#!/usr/bin/env python3
"""組合技 · 加入三大法人（官方 T86／櫃買）後重測（唯讀）。T86 全市場資料 DB 已有（twse_t86＋tpex_insti，2024-06 起，單位股），
自營商買賣超＝官方總額（含避險）→ 『自營比』代理＝自營商買賣超 ÷ 當日成交股數。
特徵（T 日收盤後公布，T+1 開盤前可得，PIT）：dealer_r、foreign_r、trust_r、three_r（皆 ÷成交量），dealer_r2（T 與 T−1 合計）。
1) 走動式樣本外：基準特徵 vs ＋法人特徵（加法 ridge、樹深 2、樹深 3）。
2) 小哥式組合規則（事前寫死，不挖掘）：自營比 ≥2% 且 有隔日沖慣犯(n_habit≥1) 且 外資、投信未買超(≤0)。看 T+1 開→收（扣同日均值）。
"""
import sys, sqlite3, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import xiaoge_combo_study as C
from stock_db import DEFAULT_DB_PATH

s = pd.read_parquet(C.OUT / "combo_dataset.parquet")
c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
inst = pd.read_sql_query("select stock_id, trade_date d, foreign_net, investment_trust_net trust_net, dealer_self_net dealer_net, three_institution_net three_net from stock_institutional_daily "
                         "where source in ('twse_t86','tpex_insti') and trade_date between '2024-09-01' and '2026-07-31' and length(stock_id)=4", c).drop_duplicates(["stock_id", "d"])
vol = pd.read_sql_query("select stock_id, trade_date d, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date between '2024-09-01' and '2026-07-31' and length(stock_id)=4", c).drop_duplicates(["stock_id", "d"])
inst = inst.merge(vol, on=["stock_id", "d"], how="left").sort_values(["stock_id", "d"])
for k in ("foreign", "trust", "dealer", "three"):
    inst[f"{k}_r"] = inst[f"{k}_net"] / inst.volume * 100
inst["dealer_r2"] = inst.groupby("stock_id").dealer_r.transform(lambda x: x + x.shift(1))
s = s.merge(inst[["stock_id", "d", "foreign_r", "trust_r", "dealer_r", "three_r", "dealer_r2"]], on=["stock_id", "d"], how="left")
print(f"樣本 {len(s)}；有官方法人資料 {s.dealer_r.notna().mean():.1%}；自營比中位 {s.dealer_r.median():.2f}%，≥2% 占 {(s.dealer_r >= 2).mean():.1%}")
INST = ["dealer_r", "foreign_r", "trust_r", "three_r", "dealer_r2"]
for k in INST:
    ic, t, nd = C.rank_ic(s.dropna(subset=[k]), k); print(f"單因子 {k:<10} rank-IC {ic:+.3f} (t {t:+.1f}, {nd}天)")
res = []
for name, feats, kind in (("基準 加法", C.FEATS_PRE, "ridge"), ("＋法人 加法", C.FEATS_PRE + INST, "ridge"),
                          ("基準 樹", C.FEATS_PRE, "gbm"), ("＋法人 樹", C.FEATS_PRE + INST, "gbm"), ("只用法人＋籌碼 樹", C.CHIP_ONLY + INST, "gbm")):
    p = C.walk_forward(s, feats, kind); e = C.evaluate(s, p, name)
    print(f"{name:<14} IC {e['rank_ic']:+.3f} (t {e['ic_t']:+.1f}) | 最低20% 超額 {e['q_low_excess_pct']:+.2f}% (t {e['q_low_t']:+.1f}) | 做空淨(0.3%) {e['short_net_pct_per_day']:+.2f}%/日 t {e['short_net_t']:+.1f} 勝日 {e['short_hit_days']:.0%}", flush=True)
# 小哥式組合規則
r = s.dropna(subset=["dealer_r", "foreign_r", "trust_r"])
rule = (r.dealer_r >= 2) & (r.n_habit >= 1) & (r.foreign_r <= 0) & (r.trust_r <= 0)
parts = {"全部": r.index == r.index, "自營比≥2%": r.dealer_r >= 2, "＋隔日沖慣犯": (r.dealer_r >= 2) & (r.n_habit >= 1), "＋外資投信未買(完整規則)": rule,
         "自營比<2% 的其餘": r.dealer_r < 2, "完整規則缺『隔日沖慣犯』": (r.dealer_r >= 2) & (r.foreign_r <= 0) & (r.trust_r <= 0)}
print("\n小哥式組合規則（T 日漲≥5%、流動性足）：T+1 開→收 與 超額(扣當日均值)")
for k, m in parts.items():
    g = r[m]
    if len(g) < 5: print(f"  {k:<26} n={len(g)}"); continue
    dm = g.groupby("d").y.mean(); n_d = len(dm)
    t = dm.mean() / (dm.std(ddof=1) / np.sqrt(n_d)) if n_d > 3 else float("nan")
    print(f"  {k:<26} n={len(g):<5} 天={n_d:<4} oc {g.oc_pct.mean():+.2f}% 超額 {g.y.mean():+.2f}% (日均t {t:+.1f}) 開→收<0占比 {(g.oc_pct < 0).mean():.0%} gap {g.gap_pct.mean():+.2f}%")
