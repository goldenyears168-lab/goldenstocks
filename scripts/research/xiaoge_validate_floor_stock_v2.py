"""小哥地板股 SOP v2（依官方投影片 2026-10-18 截圖重做；定義見 docs/warrant-xiaoge/validation/floor_stock_v2.md）。
唯讀 DB。用法: PYTHONPATH=src nice .venv/bin/python scripts/research/xiaoge_validate_floor_stock_v2.py
"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DB = Path.home() / "goldenstocks-data/data/stocks.db"
COST = 0.0044
QS = [0.90, 0.95, 0.98]
PRIMARY_Q = 0.95
VOL_MULT = 2.0
DOJI = 0.005
MIN_TURNOVER = 5e7   # 20日均成交值(元)
MIN_PRICE = 10.0
HOLD = 5
WIN = 504
START = "2004-03-01"
OOS_FROM = "2021-01-01"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql_query(
    "select stock_id, trade_date, open, high, low, close, volume from stock_daily_bars "
    "where source in ('twse_mi_index','tpex_daily') and trade_date>=? "
    "and (stock_id glob '[1-9][0-9][0-9][0-9]' or stock_id='0050')", con, params=(START,))
df = df.drop_duplicates(["stock_id", "trade_date"])
df = df[(df.close > 0) & (df.volume > 0) & df.open.notna() & (df.open > 0)]
bench = df[df.stock_id == "0050"].set_index("trade_date")
df = df[df.stock_id != "0050"]
cnt = df.groupby("trade_date").stock_id.count()
cal = cnt[cnt >= 300].index.sort_values()
df = df[df.trade_date.isin(cal)]


def W(c):
    return df.pivot(index="trade_date", columns="stock_id", values=c).reindex(cal)


O, H, L, C, V = W("open"), W("high"), W("low"), W("close"), W("volume")
# 0050 (處理 2025-06 一拆四: 單日跌逾 50% 視為拆分調整)
b = bench.reindex(cal)
bo, bc = b.open.copy(), b.close.copy()
r = bc / bc.shift(1)
for d in r.index[(r < 0.5).fillna(False)]:
    i = cal.get_loc(d)
    bo.iloc[:i] = bo.iloc[:i] / 4.0
    bc.iloc[:i] = bc.iloc[:i] / 4.0

MA = C.rolling(20, min_periods=20).mean()
DEV = C / MA - 1                                          # 對月線乖離（負＝低於月線）
# 地板價位 = MA20 × (1 − k)；k = 該股「歷史上 DEV 的 5% 分位」之絕對值（PIT：只用 ≤ 前一日的全部歷史；以聯發科 2454 反推，見 v2 報告）
dev_arr = DEV.shift(1)
K = (-dev_arr.expanding(min_periods=250).quantile(0.05))
FLOOR = MA * (1 - K)
VMA = V.rolling(20, min_periods=20).mean().shift(1)
TURN = (C * V).rolling(20, min_periods=20).mean()
ret1 = C / C.shift(1) - 1
bad = (ret1 < -0.115) | (ret1 > 0.115)
badwin = bad.rolling(12, min_periods=1).max().astype(bool) | bad.shift(-1).rolling(7, min_periods=1).max().fillna(0).astype(bool)

NEAR = 1.02          # 「小於或近於地板」：收盤 ≤ 地板價位 × 1.02
DROP2 = -0.03        # Day2 殺低：較前收下跌 > 3%
TURN_MIN = 1e8       # 「股本越大越好」代理：20 日均成交值 ≥ 1 億（股本欄位歷史缺漏）
EXIT_N = 5           # 進場後 5 個交易日

liq = (TURN >= TURN_MIN) & (C >= MIN_PRICE)
day1_vol = V >= VOL_MULT * VMA
black_prev = C < C.shift(1)           # 昨黑K：收盤低於前收（台股紅綠依前收；另測 收<開）
black_body = C < O
idx = {d: i for i, d in enumerate(cal)}
arr = {k: v.values for k, v in dict(O=O, H=H, L=L, C=C, V=V).items()}
cols = list(C.columns)
cidx = {s: j for j, s in enumerate(cols)}
bo_a, bc_a = bo.reindex(cal).values, bc.reindex(cal).values


def run(day1_mask, label, entry_mode="close"):
    s = day1_mask.stack(); s = s[s]
    rows, last = [], {}
    for (d1, sid) in s.index:
        i1 = idx[d1]; j = cidx[sid]
        i2, i3 = i1 + 1, i1 + 2
        if i2 + EXIT_N >= len(cal):
            continue
        O2, H2, L2, C2, V2 = arr["O"][i2, j], arr["H"][i2, j], arr["L"][i2, j], arr["C"][i2, j], arr["V"][i2, j]
        C1 = arr["C"][i1, j]
        if not np.isfinite([O2, H2, L2, C2, C1]).all():
            continue
        if not (C2 / C1 - 1 <= DROP2 and C2 < O2):        # Day2：下跌>3% 且 買黑不買紅
            continue
        if H2 == L2:                                       # 一字跌停買不到
            continue
        if sid in last and i2 - last[sid] < EXIT_N + 1:
            continue
        last[sid] = i2
        e = C2 if entry_mode == "close" else (L2 + C2) / 2
        O3 = arr["O"][i3, j]; H3, L3 = arr["H"][i3, j], arr["L"][i3, j]
        gap_up = np.isfinite(O3) and O3 > C2 and not (H3 == L3)   # 開高且非一字鎖死
        xF = arr["C"][i2 + EXIT_N, j]
        if not np.isfinite(xF):
            continue
        xS, dS = (O3, i3) if gap_up else (xF, i2 + EXIT_N)
        bF = bc_a[i2 + EXIT_N] / bc_a[i2] - 1
        bS = (bo_a[i3] / bc_a[i2] - 1) if gap_up else bF
        rows.append((cal[i2], sid, xF / e - 1, xS / e - 1, bF, bS, gap_up, C2 / C1 - 1))
    return pd.DataFrame(rows, columns=["ent", "sid", "rF", "rS", "bF", "bS", "gap", "d2"])


def cluster_t(x, g):
    x = pd.Series(np.asarray(x, float)); g = pd.Series(np.asarray(g))
    ok = x.notna(); x, g = x[ok], g[ok]
    n = len(x)
    if n < 5:
        return np.nan, np.nan
    m = x.mean()
    se = np.sqrt((((x - m).groupby(g).sum()) ** 2).sum()) / n
    return float(m), float(m / se) if se > 0 else np.nan


def summ(t, label):
    o = {"label": label, "n": len(t), "n_dates": int(t.ent.nunique()) if len(t) else 0}
    if len(t) == 0:
        return o
    o["gap_rate"] = float(t.gap.mean())
    for k, rc, bc in (("F", "rF", "bF"), ("S", "rS", "bS")):
        net = t[rc] - COST
        exc = net - t[bc]
        o[f"{k}_gross_win"] = float((t[rc] > 0).mean())
        o[f"{k}_net_win"] = float((net > 0).mean())
        o[f"{k}_net_mean"], o[f"{k}_net_t"] = cluster_t(net, t.ent)
        o[f"{k}_net_med"] = float(net.median())
        o[f"{k}_exc_mean"], o[f"{k}_exc_t"] = cluster_t(exc, t.ent)
    return o


valid = FLOOR.notna() & ~badwin & liq & day1_vol & (O > 0)
near = C <= FLOOR * NEAR
variants = {
    "SOP|near_floor+black_prev": valid & near & black_prev,
    "SOP|near_floor+black_body": valid & near & black_body,
    "SOP|strict_below_floor": valid & (C <= FLOOR) & black_prev,
    "ctl|no_floor(black_prev+vol)": valid & black_prev,
}
res = {k: run(m, k) for k, m in variants.items()}
res["SOP|near_floor+black_prev|entry=mid(low,close)"] = run(variants["SOP|near_floor+black_prev"], "mid", "mid")
rows = []
for k, t in res.items():
    rows.append(summ(t, f"{k}|ALL"))
    rows.append(summ(t[t.ent < OOS_FROM], f"{k}|IS<2021"))
    rows.append(summ(t[t.ent >= OOS_FROM], f"{k}|OOS>=2021"))
sd = pd.DataFrame(rows)
outdir = Path("/Users/jackm4/goldenstocks/docs/warrant-xiaoge/validation")
sd.to_csv(outdir / "floor_stock_v2_summary.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 50)
print(sd.round(4).to_string())
p = res["SOP|near_floor+black_prev"]
print(p.groupby("ent").size().sort_values(ascending=False).head(8).to_string())
print(summ(p[~p.ent.isin(p.groupby("ent").size().sort_values(ascending=False).index[:3])], "SOP excl top3 dates"))
p.to_csv(outdir / "floor_stock_v2_trades.csv", index=False)
# 2454 反推檢查
for d in ("2025-04-08", "2025-04-09"):
    print("2454", d, "MA20", round(MA.at[d, "2454"], 2), "K", round(K.at[d, "2454"], 4), "FLOOR", round(FLOOR.at[d, "2454"], 2))
