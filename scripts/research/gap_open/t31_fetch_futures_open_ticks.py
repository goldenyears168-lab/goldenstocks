#!/usr/bin/env python3
"""抓 45 檔池內個股期貨的 08:40~09:05 逐筆（FinMind TaiwanFuturesTick，一次一天一檔）。

用途：回答「期貨 08:45 開盤 → 現貨 09:00 開盤這 15 分鐘，期貨自己是延續還是回檔」——
先前只能用 sgap−fgap（跨商品、無法與量測噪音分離）。TAIFEX 免費逐筆只留前 30 個交易日
（已驗證：Daily_2026_08_01.zip 以前回傳 0 bytes），FinMind 這條可回溯到 2023-12。

目標清單：|08:45 期貨跳空|>2% 全收 + 小跳空隨機對照組。
每個 symbol-day 存一個 json（只留 08:40~09:05 的列），可重跑、已存的跳過。
"""
from __future__ import annotations
import json, os, sys, time, random, glob
from pathlib import Path
import pandas as pd, numpy as np
sys.path.insert(0,"src")
from finmind_client import fetch_finmind_json
from stock_db import DATA_DIR

OUT = Path(os.path.expanduser("~/goldenstocks-data/data/cache/gap_open/futtick"))
OUT.mkdir(parents=True, exist_ok=True)
SLEEP = float(os.environ.get("FT_SLEEP", "0.7"))
N_CTRL = int(os.environ.get("FT_CTRL", "2500"))

# v3 宇宙（期交所官方對照表選出的「量大的那個契約」；舊 _live_calib 對 8 檔標的用到的是
# 已經萎縮到 100 口/日的舊契約，那個 08:45 開盤價本身就是雜訊）
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_hivol_universe_v3.json").read_text())["universe"]
FIDS={r["futures_id"] for r in uni}
rows=[]
for f in glob.glob("/Users/jackm4/goldenstocks-data/cache/stock_futures_daily/*.parquet"):
    x=pd.read_parquet(f)
    if len(x) and str(x.futures_id.iloc[0]) in FIDS: rows.append(x)
fut=pd.concat(rows); fut=fut[fut.trading_session=="position"]
fut=fut.sort_values(["stock_id","date","volume"]).groupby(["stock_id","date"],as_index=False).last()
fut=fut.sort_values(["stock_id","date"])
fut["prev_c"]=fut.groupby("stock_id").close.shift(1)
fut["prev_ct"]=fut.groupby("stock_id").contract_date.shift(1)
fut=fut[(fut.contract_date==fut.prev_ct)]
fut["fgap"]=fut.open/fut.prev_c-1
fut=fut[fut.fgap.abs()<=0.105]
fut.to_pickle(OUT.parent/"futtick_daily_ref.pkl")

big=fut[fut.fgap.abs()>0.02]
small=fut[fut.fgap.abs()<=0.02]
random.seed(11)
ctrl=small.sample(min(N_CTRL,len(small)),random_state=11)
tgt=pd.concat([big,ctrl]).sort_values(["date","stock_id"])
print(f"目標 {len(tgt)} symbol-day（大跳空 {len(big)} + 對照 {len(ctrl)}）",flush=True)

done=0; hit=0; fail=0; t0=time.time()
for i,(_,r) in enumerate(tgt.iterrows()):
    fid=r.futures_id; day=r.date
    p=OUT/f"{fid}_{day}.json"
    if p.exists(): done+=1; continue
    try:
        j=fetch_finmind_json({"dataset":"TaiwanFuturesTick","data_id":fid,"start_date":day})
        d=j.get("data") or []
        keep=[x for x in d if "08:40"<=str(x.get("date",""))[11:16]<="09:05"]
        p.write_text(json.dumps(keep))
        hit+=1 if keep else 0
    except Exception as e:
        fail+=1
        if fail<5: print(f"  fail {fid} {day}: {type(e).__name__} {e}",flush=True)
    done+=1
    time.sleep(SLEEP)
    if done%200==0:
        el=time.time()-t0
        print(f"  {done}/{len(tgt)}  有開盤資料 {hit}  失敗 {fail}  已跑 {el/60:.1f} 分",flush=True)
print(f"完成 {done}/{len(tgt)}  有開盤資料 {hit}  失敗 {fail}  耗時 {(time.time()-t0)/60:.1f} 分")
