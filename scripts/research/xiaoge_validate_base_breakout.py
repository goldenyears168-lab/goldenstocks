"""小哥「打底完第一根」（盤整後長紅突破；V16 3:19–4:26 畫面）檢定。定義見 docs/warrant-xiaoge/validation/base_breakout.md。
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
START = "2005-01-01"
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

ret1 = C / C.shift(1) - 1
bad = ((ret1 < -0.115) | (ret1 > 0.115)).astype(float)
badwin = bad.rolling(60, min_periods=1).max().shift(-10).fillna(0).astype(bool) | bad.rolling(60, min_periods=1).max().astype(bool)
MA20 = C.rolling(20, min_periods=20).mean()
TURN = (C * V).rolling(20, min_periods=20).mean()
liq = (TURN >= MIN_TURNOVER) & (C >= MIN_PRICE)
NB = 15
hiB = H.shift(1).rolling(NB, min_periods=NB).max()          # 盤整窗 T-15..T-1 最高
loB = L.shift(1).rolling(NB, min_periods=NB).min()
rng = hiB / loB - 1                                          # 盤整區間寬度
absr = ret1.abs()
volB = absr.shift(1).rolling(NB, min_periods=NB).mean()      # 盤整窗平均日波動
volP = absr.shift(1 + NB).rolling(30, min_periods=30).mean() # 其前 30 日平均日波動
conv = volB <= 0.8 * volP                                    # 波動收斂
prior_dn = (C.shift(NB + 1) / C.shift(NB + 61) - 1) <= -0.10  # 盤整前曾下跌 ≥10%（「打底」變體）
body = (C - O) / O
brk = C > hiB                                                # 收盤突破盤整區間
vmult = V / V.shift(1).rolling(20, min_periods=20).mean()

On, Cn = O.shift(-1), {h: C.shift(-h) for h in (1, 5, 10)}
locked = (H.shift(-1) == L.shift(-1))                       # T+1 一字（買不到）
bo_s, bc_s = bo.reindex(cal), bc.reindex(cal)
bm = {h: bc_s.shift(-h) / bo_s.shift(-1) - 1 for h in (1, 5, 10)}
idx = {d: i for i, d in enumerate(cal)}


def events(mask):
    s = mask.stack(); s = s[s]
    rows, last = [], {}
    for (d, sid) in s.index:
        i = idx[d]
        if sid in last and i - last[sid] < 10:
            continue
        e = On.at[d, sid]
        if not np.isfinite(e) or locked.at[d, sid]:
            continue
        r = {h: Cn[h].at[d, sid] / e - 1 for h in (1, 5, 10)}
        if not all(np.isfinite(v) for v in r.values()):
            continue
        last[sid] = i
        rows.append((cal[i + 1] if i + 1 < len(cal) else d, sid, rng.at[d, sid], body.at[d, sid],
                     r[1], r[5], r[10], bm[1].get(d, np.nan), bm[5].get(d, np.nan), bm[10].get(d, np.nan)))
    return pd.DataFrame(rows, columns=["ent", "sid", "rng", "body", "r1", "r5", "r10", "b1", "b5", "b10"])


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
    for h in (1, 5, 10):
        if len(t) == 0:
            continue
        net = t[f"r{h}"] - COST
        exc = net - t[f"b{h}"]
        o[f"h{h}_gross_win"] = float((t[f"r{h}"] > 0).mean())
        o[f"h{h}_net_mean"], o[f"h{h}_net_t"] = cluster_t(net, t.ent)
        o[f"h{h}_exc_mean"], o[f"h{h}_exc_t"] = cluster_t(exc, t.ent)
        o[f"h{h}_net_med"] = float(net.median())
    return o


base_ok = liq & ~badwin & brk & (body >= 0.05) & hiB.notna() & conv.notna()
V_ = {
    "A 主定義: 盤整幅≤12%∧波動收斂∧長紅≥5%突破": base_ok & (rng <= 0.12) & conv,
    "A2 長紅≥7%": base_ok & (rng <= 0.12) & conv & (body >= 0.07),
    "A3 盤整幅≤10%": base_ok & (rng <= 0.10) & conv,
    "A4 盤整幅≤15%": base_ok & (rng <= 0.15) & conv,
    "A5 主定義+量≥1.5×": base_ok & (rng <= 0.12) & conv & (vmult >= 1.5),
    "A6 主定義+盤整前下跌≥10%(打底)": base_ok & (rng <= 0.12) & conv & prior_dn,
    "C1 對照: 長紅≥5%突破但盤整幅>25%": base_ok & (rng > 0.25),
    "C2 對照: 盤整幅≤12%但波動未收斂": base_ok & (rng <= 0.12) & ~conv,
    "C3 對照: 任何長紅≥5%(不要求突破)": liq & ~badwin & (body >= 0.05) & hiB.notna(),
}
res = {k: events(m) for k, m in V_.items()}
rnd = np.random.default_rng(0)
allm = liq & ~badwin & hiB.notna()
ss = allm.stack(); ss = ss[ss].index
pick = rnd.choice(len(ss), size=40000, replace=False)
rm = pd.DataFrame(False, index=cal, columns=C.columns)
for k in pick:
    rm.at[ss[k][0], ss[k][1]] = True
res["R 隨機日"] = events(rm)
rows = []
for k, t in res.items():
    rows.append(summ(t, f"{k}|ALL")); rows.append(summ(t[t.ent < OOS_FROM], f"{k}|IS<2021")); rows.append(summ(t[t.ent >= OOS_FROM], f"{k}|OOS>=2021"))
sd = pd.DataFrame(rows)
outdir = Path("/Users/jackm4/goldenstocks/docs/warrant-xiaoge/validation")
sd.to_csv(outdir / "base_breakout_summary.csv", index=False)
pd.set_option("display.width", 260); pd.set_option("display.max_columns", 40)
cols = ["label", "n", "n_dates", "h1_gross_win", "h1_net_mean", "h1_exc_mean", "h1_exc_t", "h5_gross_win", "h5_net_mean", "h5_exc_mean", "h5_exc_t", "h10_gross_win", "h10_net_mean", "h10_exc_mean", "h10_exc_t", "h10_net_med"]
print(sd[cols].round(4).to_string())
A = res["A 主定義: 盤整幅≤12%∧波動收斂∧長紅≥5%突破"]
A.to_csv(outdir / "base_breakout_trades_A.csv", index=False)
print(A.groupby("ent").size().sort_values(ascending=False).head(5).to_string())
