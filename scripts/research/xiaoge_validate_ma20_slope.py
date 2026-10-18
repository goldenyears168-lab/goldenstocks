"""小哥 R19 月線斜率驗證。唯讀 DB。報告 docs/warrant-xiaoge/validation/ma20_slope.md 由人讀；此腳本輸出結果表。
預註冊定義見報告開頭。用法: PYTHONPATH=src nice .venv/bin/python scripts/research/xiaoge_validate_ma20_slope.py
"""
import os, sqlite3, numpy as np, pandas as pd
DB = f"file:{os.path.expanduser('~/goldenstocks-data/data/stocks.db')}?mode=ro"
COST = 0.0044; HS = (1, 5, 10); OOS = "2025-01-01"
con = sqlite3.connect(DB, uri=True)
df = pd.read_sql("select stock_id,trade_date d,open o,high h,close c,volume v from stock_daily_bars "
                 "where trade_date>='2018-06-01' and source in ('twse_mi_index','tpex_daily') and close>0 "
                 "and (length(stock_id)=4 or stock_id='0050')", con)
df = df.drop_duplicates(["stock_id", "d"]).sort_values(["stock_id", "d"])
b50 = df[df.stock_id == "0050"].set_index("d")
df = df[(df.stock_id != "0050") & df.stock_id.str.match(r"^[1-9]\d{3}$")]
dates = sorted(df.d.unique()); di = {d: i for i, d in enumerate(dates)}
rows = []
for sid, g in df.groupby("stock_id", sort=False):
    g = g.set_index("d").reindex(dates); 
    if g.c.notna().sum() < 300: continue
    c, o, hi, v = g.c, g.o, g.h, g.v
    ma = c.rolling(20, min_periods=20).mean()
    S = ma / ma.shift(1) - 1
    ret1 = c.pct_change(fill_method=None)
    bad = (ret1.abs() > 0.115).astype(float).rolling(21, min_periods=1).max() > 0  # 近21日疑似分割/除權
    liq = (c * v).rolling(20).mean() >= 3e7
    dev = c / ma - 1
    pull = c <= 0.97 * c.rolling(5).max()
    o1 = o.shift(-1)
    gap_up_lim = o1 / c >= 1.095
    out = pd.DataFrame({"sid": sid, "T": dates, "S": S.values, "dev": dev.values, "pull": pull.values,
                        "ok": (liq & ~bad & o1.notna() & ~gap_up_lim).values, "o1": o1.values})
    for h in HS:
        out[f"x{h}"] = c.shift(-h).values
    rows.append(out)
a = pd.concat(rows); a = a[a.ok & a.S.notna()].copy()
bo, bc = b50.o.reindex(dates), b50.c.reindex(dates)
for h in HS:
    bo1 = bo.shift(-1); bx = bc.shift(-h)
    bret = (bx / bo1 - 1); bret[(bx / bo1 - 1).abs() > 0.2] = np.nan  # 0050 2025-06 分割
    a[f"r{h}"] = a[f"x{h}"] / a.o1 - 1 - COST
    a[f"e{h}"] = a[f"r{h}"] - a["T"].map(bret.set_axis(dates))
    a.loc[a[f"r{h}"].abs() > 0.6, [f"r{h}", f"e{h}"]] = np.nan
a["bin"] = pd.cut(a.S, [-9, 0, .01, .025, .035, 9], labels=["<0", "0-1%", "1-2.5%", "2.5-3.5%", ">3.5%"], right=False)
a["split"] = np.where(a["T"] >= OOS, "OOS", "IS")

def nw_t(x, L):
    x = x.dropna().values; n = len(x)
    if n < 30: return np.nan
    m = x.mean(); u = x - m; s = (u @ u) / n
    for k in range(1, L + 1):
        s += 2 * (1 - k / (L + 1)) * (u[k:] @ u[:-k]) / n
    return m / np.sqrt(s / n)

def stat(sub, h):
    r = sub[f"r{h}"].dropna(); e = sub[f"e{h}"]
    dm = sub.groupby("T")[f"e{h}"].mean()
    return dict(n=len(r), days=dm.notna().sum(), med_net=r.median()*100, win=(r > 0).mean()*100,
                mean_ex=e.mean()*100, med_ex=e.median()*100, t=nw_t(dm, h))
res = []
def run(name, d):
    for sp in ("ALL", "IS", "OOS"):
        s = d if sp == "ALL" else d[d.split == sp]
        for h in HS:
            if len(s): res.append(dict(group=name, split=sp, h=h, **stat(s, h)))
for k in ["<0", "0-1%", "1-2.5%", "2.5-3.5%", ">3.5%"]: run(f"S {k}", a[a.bin == k])
run("S>1% (合併)", a[a.S >= .01]); run("S all (基準)", a)
near = a.dev.between(-0.01, 0.03) & a.pull
run("回檔近月線 & S>1%", a[near & (a.S >= .01)])
run("回檔近月線 & S 0-1%", a[near & a.S.between(0, .01, inclusive="left")])
run("回檔近月線 & S<0", a[near & (a.S < 0)])
run("回檔近月線 & S 2.5-3.5%", a[near & a.S.between(.025, .035, inclusive="left")])
run("回檔近月線 & S 1-2.5%", a[near & a.S.between(.01, .025, inclusive="left")])
run("非回檔 & S>1%", a[~a.pull & (a.S >= .01)])
R = pd.DataFrame(res)
pd.set_option("display.width", 250, "display.max_rows", 500)
print(f"樣本 stock-days={len(a)} 股票={a.sid.nunique()} 日期 {a['T'].min()}~{a['T'].max()}")
print(R.round(2).to_string(index=False))
R.round(3).to_csv(os.path.expanduser("~/goldenstocks/scripts/research/_xiaoge_ma20_slope_out.csv"), index=False)
