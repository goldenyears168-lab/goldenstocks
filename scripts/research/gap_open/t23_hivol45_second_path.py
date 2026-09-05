import json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0,"src")
from stock_db import DATA_DIR
OUT=os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
SIDS=[str(r["sid"]) for r in uni]
meta=pd.DataFrame(pd.read_pickle(f"{OUT}/tick_meta.pkl")); z=np.load(f"{OUT}/tick_arrs.npz")
px=z["px"].astype("float64")
k=meta.sid.isin(SIDS).values; meta=meta[k].reset_index(drop=True); px=px[k]
fv=np.isnan(px).argmin(axis=1)
for i in range(px.shape[0]): px[i,:fv[i]]=meta.open_px.iloc[i]
px=pd.DataFrame(px).ffill(axis=1).to_numpy()
daily=pd.read_pickle(f"{OUT}/daily_panel.pkl")[["stock_id","trade_date","base","prev_close"]]
daily.columns=["sid","date","base","prev_close"]
m=meta.merge(daily,on=["sid","date"],how="left")
ok=m.base.notna().to_numpy(); m=m[ok].reset_index(drop=True); px=px[ok]
m["gap"]=m.open_px/m.base-1
s=((m.gap.abs()<=0.105)&(m.prev_close>=10)).to_numpy(); m=m[s].reset_index(drop=True); px=px[s]
ret=px/m.open_px.to_numpy()[:,None]-1
sgn=np.sign(m.gap.to_numpy())[:,None]
base=(m.gap.abs()<=0.0025).to_numpy()
TS=[5,10,15,20,30,45,60,90,120,180,300,600]
groups=[("跌空 <-2%",(m.gap<-0.02).to_numpy()),("跌空 -2~-1%",((m.gap<=-0.01)&(m.gap>=-0.02)).to_numpy()),
        ("漲空 +1~2%",((m.gap>=0.01)&(m.gap<=0.02)).to_numpy()),("漲空 >+2%",(m.gap>0.02).to_numpy())]
print("開盤價起算的『跳空方向』累積超額 bps（正=延續 負=回檔），已扣零跳空同秒基準；t 為日 cluster")
print(f"{'秒':>5}"+"".join(f"{g[0]:>18}" for g in groups))
bl=ret[base].mean(axis=0)
for t in TS:
    line=f"{t:>5}"
    for nm,sel in groups:
        x=sgn[sel,0]*(ret[sel,t]-bl[t])
        dd=pd.DataFrame({"d":m.date[sel].values,"x":x}).groupby("d").x.mean()
        tt=dd.mean()/(dd.std(ddof=1)/np.sqrt(len(dd)))
        line+=f"{dd.mean()*1e4:>11.1f}({tt:>4.1f})"
    print(line)
print("\n各桶樣本數:", {nm:int(sel.sum()) for nm,sel in groups}, "零跳空基準 n=",int(base.sum()))

print("\n=== 進場時點 × 出場 60s（跳空方向超額 bps，日 cluster t）===")
print(f"{'進場':>8}{'跌空<-2%':>20}{'漲空>+2%':>20}")
for t_in in [0,5,10,20,30,45]:
    line=f"{t_in:>6}s "
    for nm,sel in [("d",(m.gap<-0.02).to_numpy()),("u",(m.gap>0.02).to_numpy())]:
        r=px[sel,60]/px[sel,t_in]-1
        b=(px[base,60]/px[base,t_in]-1).mean()
        x=np.sign(m.gap.to_numpy()[sel])*(r-b)
        dd=pd.DataFrame({"d":m.date[sel].values,"x":x}).groupby("d").x.mean()
        tt=dd.mean()/(dd.std(ddof=1)/np.sqrt(len(dd)))
        line+=f"{dd.mean()*1e4:>13.1f}({tt:>4.1f})"
    print(line)
