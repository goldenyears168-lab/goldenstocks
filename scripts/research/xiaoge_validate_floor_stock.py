"""小哥地板股 SOP (R16/S5) 本機驗證。預註冊定義見 docs/warrant-xiaoge/validation/floor_stock.md。
唯讀 DB。用法: PYTHONPATH=src nice .venv/bin/python scripts/research/xiaoge_validate_floor_stock.py
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
START = "2012-01-01"
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
bias = (MA - C) / MA
VMA = V.rolling(20, min_periods=20).mean().shift(1)
TURN = (C * V).rolling(20, min_periods=20).mean()
ret1 = C / C.shift(1) - 1
bad = (ret1 < -0.115) | (ret1 > 0.115)       # 疑似拆分/減資
badwin = bad.rolling(HOLD + 25, min_periods=1).max().shift(-HOLD - 1).fillna(0).astype(bool) | \
    bad.rolling(25, min_periods=1).max().astype(bool)

black = (C < O) | ((C - O).abs() / C <= DOJI)
vol_ok = V >= VOL_MULT * VMA
liq = (TURN >= MIN_TURNOVER) & (C >= MIN_PRICE)
base = black & vol_ok & liq & ~badwin

# 事前條件：PIT θ = 該股過去 504 日(不含當日) bias 的 q 分位
thetas = {q: bias.rolling(WIN, min_periods=400).quantile(q).shift(1) for q in QS}

# 進場 T+1 開盤；出場 B(SOP): 進場日起 5 日內首個收盤 > 進場價，否則第 5 日收盤；A: 固定第 5 日收盤
idx = {d: i for i, d in enumerate(cal)}
On, Hn, Ln = O.shift(-1), H.shift(-1), L.shift(-1)
locked = (Hn == Ln)
entry = On
Cn = [C.shift(-k) for k in range(1, HOLD + 1)]   # k=1 -> 進場日收盤 ... k=5 -> 第5日收盤


def returns(mask):
    s = mask.stack()
    s = s[s]
    rows = []
    for (d, sid) in s.index:
        i = idx[d]
        if i + HOLD >= len(cal):
            continue
        e = entry.at[d, sid]
        if not np.isfinite(e) or locked.at[d, sid]:
            continue
        cl = np.array([Cn[k].at[d, sid] for k in range(HOLD)])
        if not np.isfinite(cl).all():
            continue
        ed = cal[i + 1]
        xA = cl[-1]
        hit = np.where(cl > e)[0]
        kB = hit[0] if len(hit) else HOLD - 1
        xB = cl[kB]
        d_end_A, d_end_B = cal[i + HOLD], cal[i + 1 + kB]
        def bm(dend):
            if pd.isna(bo.get(ed)) or pd.isna(bc.get(dend)):
                return np.nan
            return bc[dend] / bo[ed] - 1
        rows.append((d, sid, ed, xA / e - 1, xB / e - 1, bm(d_end_A), bm(d_end_B), kB + 1, bool(len(hit))))
    return pd.DataFrame(rows, columns=["sig", "sid", "ent", "rA", "rB", "bA", "bB", "daysB", "hit"])


def dedupe(t):
    t = t.sort_values(["sid", "ent"])
    keep, last = [], {}
    for k, (sid, d) in enumerate(zip(t.sid, t.ent)):
        i = idx[d]
        if sid in last and i - last[sid] < HOLD:
            continue
        last[sid] = i
        keep.append(k)
    return t.iloc[keep]


def cluster_t(x, g):
    x = pd.Series(np.asarray(x, float)); g = pd.Series(np.asarray(g))
    ok = x.notna(); x, g = x[ok], g[ok]
    n = len(x)
    if n < 5:
        return np.nan, np.nan
    m = x.mean()
    s = ((x - m).groupby(g).sum() ** 2).sum()
    se = np.sqrt(s) / n
    return m, (m / se if se > 0 else np.nan)


def summarize(t, label):
    out = {"label": label, "n": len(t)}
    if len(t) == 0:
        return out
    out["n_dates"] = t.ent.nunique()
    for ex, rc, bc_ in (("A", "rA", "bA"), ("B", "rB", "bB")):
        net = t[rc] - COST
        exc = net - t[bc_]
        out[f"{ex}_gross_win"] = float((t[rc] > 0).mean())
        out[f"{ex}_net_win"] = float((net > 0).mean())
        out[f"{ex}_net_mean"], out[f"{ex}_net_t"] = cluster_t(net, t.ent)
        out[f"{ex}_net_med"] = float(net.median())
        out[f"{ex}_exc_mean"], out[f"{ex}_exc_t"] = cluster_t(exc, t.ent)
        out[f"{ex}_exc_med"] = float(exc.median())
        out[f"{ex}_exc_win"] = float((exc > 0).mean())
    out["touch_rate"] = float(t.hit.mean())
    out["daysB_med"] = float(t.daysB.median())
    return out


res = {}
for q in QS:
    th = thetas[q]
    m = base & (bias >= th) & (bias > 0) & th.notna()
    t = dedupe(returns(m))
    res[f"q{q}"] = t
# 對照組 1: 無乖離條件(只有黑K/十字+爆量+流動性)；對照組 2: 無任何訊號之隨機基準(同流動性、全日、抽樣)
t0 = dedupe(returns(base & bias.notna() & thetas[0.95].notna()))
res["no_bias"] = t0
rng = np.random.default_rng(0)
allm = liq & ~badwin & bias.notna() & thetas[0.95].notna()
s = allm.stack(); s = s[s].index
pick = rng.choice(len(s), size=min(40000, len(s)), replace=False)
rm = pd.DataFrame(False, index=cal, columns=C.columns)
for k in pick:
    rm.at[s[k][0], s[k][1]] = True
res["random"] = returns(rm)

rows = []
for name, t in res.items():
    t = t.copy()
    rows.append(summarize(t, f"{name}|ALL"))
    rows.append(summarize(t[t.ent < OOS_FROM], f"{name}|IS<2021"))
    rows.append(summarize(t[t.ent >= OOS_FROM], f"{name}|OOS>=2021"))
sumdf = pd.DataFrame(rows)
pt = res["q0.95"].copy()
pt["year"] = pt.ent.str[:4]
yr = pt.groupby("year").apply(lambda g: pd.Series({"n": len(g), "A_net_win": ((g.rA - COST) > 0).mean(),
                                                    "A_net_mean": (g.rA - COST).mean(),
                                                    "A_exc_mean": (g.rA - COST - g.bA).mean(),
                                                    "B_net_win": ((g.rB - COST) > 0).mean(),
                                                    "touch": g.hit.mean()}), include_groups=False)
# 日期集中度
top = pt.groupby("ent").size().sort_values(ascending=False).head(8)
# 排除最大單日叢集後
big = top.index[:3]
ex3 = summarize(pt[~pt.ent.isin(big)], "q0.95|excl_top3_dates")
outdir = Path("/Users/jackm4/goldenstocks/docs/warrant-xiaoge/validation")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 50)
sumdf.to_csv(outdir / "floor_stock_summary.csv", index=False)
print(sumdf.round(4).to_string())
print(yr.round(4).to_string())
print(top.to_string())
print(json.dumps(ex3, default=float, indent=1))
print("calendar", cal[0], cal[-1], len(cal), "signals-base-count", int(base.sum().sum()))
pt.to_csv(outdir / "floor_stock_trades_q0.95.csv", index=False)
