"""小哥「開布林」(R01-R04) 事件檢定。唯讀 DB。PYTHONPATH=src nice python this.py
預註冊定義見 docs/warrant-xiaoge/validation/bollinger_open.md 開頭。"""
import sqlite3, os, sys, json
import numpy as np, pandas as pd

DB = os.path.expanduser("~/goldenstocks-data/data/stocks.db")
COST = 0.0044
HS = (1, 5, 10)
OOS_START = "2020-01-01"
MIN_PRICE, MIN_MED_AMT = 10.0, 10e6

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.execute("PRAGMA query_only=1")
df = pd.read_sql("""select stock_id,trade_date,open,high,low,close,volume,source from stock_daily_bars
 where stock_id glob '[1-9][0-9][0-9][0-9]' and trade_date>='2004-01-01' and close>0""", con)
con.close()
pr = {"twse_mi_index": 0, "tpex_daily": 0, "finmind": 1, "yfinance": 2}
df["p"] = df.source.map(pr).fillna(3)
df = df.sort_values(["stock_id", "trade_date", "p"]).drop_duplicates(["stock_id", "trade_date"])
dates = sorted(df.trade_date.unique())
di = {d: i for i, d in enumerate(dates)}
def wide(c): return df.pivot(index="trade_date", columns="stock_id", values=c).reindex(dates)
O, H, L, C, V = (wide(c) for c in ["open", "high", "low", "close", "volume"])
print("panel", C.shape, file=sys.stderr)

ma = C.rolling(20, min_periods=20).mean()
sd = C.rolling(20, min_periods=20).std(ddof=0)
UB, LB = ma + 2 * sd, ma - 2 * sd
BW = UB / LB - 1
amt_med = (C * V).rolling(20, min_periods=15).median()
ok_u = (C >= MIN_PRICE) & (amt_med >= MIN_MED_AMT) & BW.notna()
# 資料異常：單日收盤比超出 [0.85,1.12]
r1 = C / C.shift(1)
bad = ((r1 < 0.85) | (r1 > 1.12)).astype(float)

breakout = (C > UB)
prior10 = breakout.shift(1).astype(float).rolling(10, min_periods=1).sum().fillna(0)
first_bo = breakout & (prior10 == 0)

bwp = BW.shift(1)
tier = pd.DataFrame(np.select([bwp < .05, bwp < .10, bwp < .20, bwp >= .20], [1, 2, 3, 4], 0), index=BW.index, columns=BW.columns)
tier = tier.where(bwp.notna())

# 進場 T+1 開盤；無法成交（T+1 開盤漲停鎖死 open==high==low 且 >= 前收*1.095）排除
Oe = O.shift(-1)
lock = (Oe >= C * 1.095) & (O.shift(-1) == H.shift(-1)) & (H.shift(-1) == L.shift(-1))
rets = {}
for h in HS:
    ex = C.shift(-h)
    ret = ex / Oe - 1 - COST
    badwin = bad.shift(-1).rolling(h, min_periods=1).sum().shift(-(h - 1))  # T+1..T+h
    ret = ret.where((badwin == 0) & ~lock & ok_u & Oe.notna() & ex.notna())
    gross = ex / Oe - 1
    gross = gross.where(ret.notna())
    # 市場等權基準（同一視窗、同一合格集合）
    bench = gross.mean(axis=1)
    rets[h] = (ret, gross, bench)

def stack(h):
    ret, gross, bench = rets[h]
    s = ret.stack().rename("net"); g = gross.stack().rename("gross")
    x = pd.concat([s, g], axis=1).reset_index()
    x.columns = ["date", "stock", "net", "gross"]
    x["bench"] = x.date.map(bench)
    x["ex"] = x.net - x.bench
    return x

def cl_stats(x, col):
    n = len(x)
    if n < 30: return dict(n=n)
    e = x[col].values; m = e.mean()
    d = (x[col] - m).groupby(x["date"].values).sum()
    se = np.sqrt((d ** 2).sum()) / n
    return dict(n=n, ndates=x.date.nunique(), mean=m, med=np.median(e), win=(e > 0).mean(), t=m / se if se > 0 else np.nan)

groups = {}
sel_ev = first_bo & ok_u
sel_nobo = (~breakout) & ok_u
sel_any = ok_u
def mask_to_df(mask, name, tiers):
    m = mask & tier.isin(tiers)
    return m
defs = {
 "A1 首次突破 BW<5%": (sel_ev, [1]),
 "A2 首次突破 BW5-10%": (sel_ev, [2]),
 "A3 首次突破 BW10-20%": (sel_ev, [3]),
 "A4 首次突破 BW>=20%": (sel_ev, [4]),
 "B1 窄帶未突破 BW<5%": (sel_nobo, [1]),
 "B2 窄帶未突破 BW5-10%": (sel_nobo, [2]),
 "C 任意日": (sel_any, [1, 2, 3, 4]),
}
rows = []
for h in HS:
    x = stack(h)
    key = x.set_index(["date", "stock"]).index
    for name, (mask, tiers) in defs.items():
        m = (mask & tier.isin(tiers)).stack()
        m = m[m]
        idx = m.index
        y = x[pd.MultiIndex.from_frame(x[["date", "stock"]]).isin(idx)]
        for per, yy in [("ALL", y), ("IS(<2020)", y[y.date < OOS_START]), ("OOS(>=2020)", y[y.date >= OOS_START])]:
            for col in ["net", "ex"]:
                s = cl_stats(yy, col)
                rows.append(dict(h=h, group=name, period=per, metric=col, **s))
    print("h", h, file=sys.stderr)
res = pd.DataFrame(rows)
res.to_csv(os.path.expanduser("~/goldenstocks/docs/warrant-xiaoge/validation/bollinger_open_results.csv"), index=False)
# 敏感度：A1 含鎖死漲停(不剔除) 不另跑；事件年分佈
ev = (first_bo & ok_u & tier.isin([1])).stack(); ev = ev[ev]
print(pd.Series([d[:4] for d, _ in ev.index]).value_counts().sort_index().to_dict())
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
for met in ["net", "ex"]:
    print(met); print(res[res.metric == met].drop(columns="metric").round(4).to_string())
# 鎖死比例
evm = (first_bo & ok_u & tier.isin([1, 2]))
print("A1/A2 事件中 T+1 開盤漲停鎖死比例", (evm & lock).sum().sum(), evm.sum().sum())
