import sqlite3, sys, json, os
import pandas as pd, numpy as np
sys.path.insert(0,"src"); import stock_db
from stock_db import DATA_DIR
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
SIDS=[str(r["sid"]) for r in uni]; NAME={str(r["sid"]):r["name"] for r in uni}
c=sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro",uri=True)
q=("SELECT stock_id,trade_date,poll_ts,last_trial_price,last_trial_size,last_trade_price "
   "FROM fubon_premarket_quote_snapshot WHERE stock_id IN (%s)"%",".join("?"*len(SIDS)))
d=pd.read_sql(q,c,params=SIDS)
print("rows",len(d),"檔",d.stock_id.nunique(),"日",sorted(d.trade_date.unique()))
d["t"]=d.poll_ts.str.slice(11,19)
print("poll 時間範圍",d.t.min(),d.t.max(),"每日 poll 次數中位",
      int(d.groupby(["trade_date","stock_id"]).size().median()))
pre=d[(d.t<"09:00:00")&d.last_trial_price.notna()]
print("09:00 前有試撮價的列",len(pre))
last=pre.sort_values("poll_ts").groupby(["stock_id","trade_date"],as_index=False).last()
print("有 08:59 試撮的股日",len(last), "覆蓋檔數",last.stock_id.nunique())
# 各時點的試撮價 vs 實際開盤
OUT=os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
sp=pd.read_pickle(f"{OUT}/daily_filtered.pkl")[["stock_id","trade_date","open","base","gap"]]
m=last.merge(sp,on=["stock_id","trade_date"],how="inner")
m["err"]=m.last_trial_price/m.open-1
print(f"\n[08:59 最後一筆試撮價 vs 09:00 實際開盤] n={len(m)} 股日 / {m.stock_id.nunique()} 檔")
print(f"  平均偏誤 {m.err.mean()*1e4:+.1f} bps   |偏誤| 中位 {m.err.abs().median()*1e4:.1f} bps"
      f"   p90 {m.err.abs().quantile(.9)*1e4:.0f} bps   corr(試撮跳空,實際跳空)="
      f"{np.corrcoef(m.last_trial_price/m.base-1, m.gap)[0,1]:.4f}")
print(f"  完全相等比例 {float((m.err.abs()<1e-9).mean())*100:.1f}%")
# 分時點
for cut in ["08:35","08:40","08:45","08:50","08:55","08:58","08:59"]:
    s=d[(d.t<cut+":59")&d.last_trial_price.notna()].sort_values("poll_ts").groupby(
        ["stock_id","trade_date"],as_index=False).last().merge(sp,on=["stock_id","trade_date"])
    if len(s)==0: continue
    e=s.last_trial_price/s.open-1
    print(f"  ≤{cut}  n={len(s):<4} |偏誤|中位 {e.abs().median()*1e4:6.1f} bps  p90 {e.abs().quantile(.9)*1e4:6.0f} bps")
