import pandas as pd, numpy as np, glob, json, sys, os
sys.path.insert(0,"src")
from stock_db import DATA_DIR
import sqlite3, stock_db
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
SIDS={str(r["sid"]) for r in uni}; NAME={str(r["sid"]):r["name"] for r in uni}
R="/Users/jackm4/goldenstocks-data/cache/stock_futures_daily"
fs=glob.glob(R+"/*.parquet")
rows=[]
for f in fs:
    d=pd.read_parquet(f)
    if d.empty: continue
    if str(d.stock_id.iloc[0]) in SIDS: rows.append(d)
fut=pd.concat(rows)
print("命中期貨 roots", fut.futures_id.nunique(), "檔標的", fut.stock_id.nunique(),
      "sessions", fut.trading_session.unique(), fut.date.min(), fut.date.max())
fut=fut[fut.trading_session=="position"]
# 取每日成交量最大的月份合約（混月污染：memory 已記錄必按日取最大量月）
fut=fut.sort_values(["stock_id","date","volume"]).groupby(["stock_id","date"],as_index=False).last()
fut=fut.sort_values(["stock_id","date"])
fut["prev_c"]=fut.groupby("stock_id").close.shift(1)
fut["prev_ct"]=fut.groupby("stock_id").contract_date.shift(1)
fut["prev_d"]=fut.groupby("stock_id").date.shift(1)
fut=fut[(fut.contract_date==fut.prev_ct)]          # 同一合約才算跳空,避免換月
fut["fgap"]=fut.open/fut.prev_c-1
fut["fo2c"]=fut.close/fut.open-1

# 現貨日線
c=sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro",uri=True)
OUT=os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
sp=pd.read_pickle(f"{OUT}/daily_filtered.pkl")[["stock_id","trade_date","gap","open","base","o2c","prev_date"]]
sp.columns=["stock_id","date","sgap","s_open","s_base","s_o2c","s_prevd"]
m=fut.merge(sp,left_on=["stock_id","date"],right_on=["stock_id","date"],how="inner")
m=m[(m.fgap.abs()<=0.105)&(m.sgap.abs()<=0.105)&(m.prev_d==m.s_prevd)]
print(f"\n[期貨 vs 現貨跳空] n={len(m)} 股日 / {m.stock_id.nunique()} 檔 / {m.date.min()}~{m.date.max()}")
b=np.polyfit(m.fgap,m.sgap,1); r=np.corrcoef(m.fgap,m.sgap)[0,1]
print(f"現貨跳空 = {b[0]:.3f} × 期貨08:45跳空 + {b[1]*1e4:.1f}bps   corr={r:.3f}  R²={r*r:.3f}")
print(f"期貨跳空平均絕對值 {m.fgap.abs().mean()*1e4:.0f} bps；現貨 {m.sgap.abs().mean()*1e4:.0f} bps")
print(f"同號比例 {(np.sign(m.fgap)==np.sign(m.sgap)).mean()*100:.1f}%")
# 期貨在 08:45 已經走掉多少 = fgap / sgap
big=m[m.sgap.abs()>0.02]
print(f"|現貨跳空|>2% 時：期貨開盤已反映 {np.median(big.fgap/big.sgap)*100:.0f}%（中位）")
