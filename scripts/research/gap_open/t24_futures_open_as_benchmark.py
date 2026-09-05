import pandas as pd, numpy as np, glob, json, os, sys, sqlite3
sys.path.insert(0,"src"); import stock_db
from stock_db import DATA_DIR
OUT=os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
SIDS={str(r["sid"]) for r in uni}; NAME={str(r["sid"]):r["name"] for r in uni}
TB={str(r["sid"]):r["tick_bps"] for r in uni}

# ---- 期貨日線（open = 08:45 日盤開盤）
rows=[]
for f in glob.glob("/Users/jackm4/goldenstocks-data/cache/stock_futures_daily/*.parquet"):
    d=pd.read_parquet(f)
    if len(d) and str(d.stock_id.iloc[0]) in SIDS: rows.append(d)
fut=pd.concat(rows)
fut=fut[fut.trading_session=="position"].sort_values(["stock_id","date","volume"])
fut=fut.groupby(["stock_id","date"],as_index=False).last().sort_values(["stock_id","date"])
for c_,n_ in [("close","prev_c"),("contract_date","prev_ct"),("date","prev_d")]:
    fut[n_]=fut.groupby("stock_id")[c_].shift(1)
fut=fut[fut.contract_date==fut.prev_ct]
fut["fgap"]=fut.open/fut.prev_c-1
fut["f_o2c"]=fut.close/fut.open-1
fut=fut[["stock_id","date","fgap","f_o2c","open","prev_c","volume","prev_d"]].rename(
    columns={"open":"f_open","volume":"f_vol"})

# ---- 現貨 1 分鐘
meta=pd.read_pickle(f"{OUT}/min_meta.pkl"); close=np.load(f"{OUT}/min_close.npy")
sp=pd.DataFrame({"stock_id":meta.index.get_level_values(0).astype(str),
                 "date":meta.index.get_level_values(1),"base":meta.base.values,
                 "open0":meta.open0.values,"prev_close":meta.prev_close.values,
                 "adv20":meta.adv20.values,"d_close":meta.d_close.values,
                 "c1":close[:,0],"c2":close[:,1],"c5":close[:,4],"c15":close[:,14],"c30":close[:,29]})
sp=sp[sp.stock_id.isin(SIDS)]
sp["sgap"]=sp.open0/sp.base-1
sp=sp[(sp.sgap.abs()<=0.105)&(sp.prev_close>=10)&(sp.adv20>=5e7)].dropna(subset=["c1"])
d=sp.merge(fut,on=["stock_id","date"],how="inner")
d["m1"]=d.c1/d.open0-1; d["m5"]=d.c5/d.open0-1; d["m15"]=d.c15/d.open0-1; d["m30"]=d.c30/d.open0-1
d["m1_2"]=d.c2/d.c1-1
d["resid"]=d.sgap-d.fgap          # 現貨開盤相對期貨 08:45 開盤 多跳了幾 bps（不擬合、無前視）
print(f"[樣本] {len(d)} 股日 / {d.stock_id.nunique()} 檔 / {d.date.nunique()} 日 / {d.date.min()}~{d.date.max()}")
print(f"resid 分佈 bps: sd={d.resid.std()*1e4:.0f}  |中位|={d.resid.abs().median()*1e4:.0f}  "
      f"p10={d.resid.quantile(.1)*1e4:.0f} p90={d.resid.quantile(.9)*1e4:.0f}")

def clt(s,col):
    g=s.groupby("date")[col].mean()
    if len(g)<3: return np.nan,np.nan,len(s)
    return g.mean()*1e4, g.mean()/(g.std(ddof=1)/np.sqrt(len(g))), len(s)

q=pd.qcut(d.resid,7,labels=False)
print("\n### 現貨開盤 vs 期貨08:45開盤 的偏離(resid) → 現貨開盤後報酬（bps，日 cluster t）")
print(f"{'resid 桶':<14}{'n':>6}{'resid':>8}{'→09:01':>13}{'→09:05':>13}{'→09:15':>13}{'→09:30':>13}")
for i in range(7):
    s=d[q==i]
    out=f"{f'Q{i+1}':<14}{len(s):>6}{s.resid.mean()*1e4:>8.0f}"
    for c_ in ("m1","m5","m15","m30"):
        m,t,_=clt(s,c_); out+=f"{m:>8.1f}({t:>4.1f})"
    print(out)
print("\n多空價差 Q1(現貨開太低) − Q7(現貨開太高):")
for c_ in ("m1","m5","m15","m30"):
    a=d[q==0].copy(); b=d[q==6].copy()
    a["x"]=a[c_]; b["x"]=-b[c_]
    both=pd.concat([a[["date","x"]],b[["date","x"]]])
    g=both.groupby("date").x.mean()
    print(f"  {c_:<4} {g.mean()*2e4:+7.1f} bps  t={g.mean()/(g.std(ddof=1)/np.sqrt(len(g))):+5.2f}  n_day={len(g)}")

print("\n### 對照：同一樣本上,單純用現貨跳空 sgap 分桶（前一題的訊號）")
q2=pd.qcut(d.sgap,7,labels=False)
print(f"{'sgap 桶':<14}{'n':>6}{'sgap':>8}{'→09:01':>13}")
for i in range(7):
    s=d[q2==i]; m,t,_=clt(s,"m1")
    print(f"{f'Q{i+1}':<14}{len(s):>6}{s.sgap.mean()*1e4:>8.0f}{m:>8.1f}({t:>4.1f})")

print("\n### 雙變量：控制 sgap 後 resid 還有沒有增量（OLS，日 cluster t）")
import numpy.linalg as la
X=np.column_stack([np.ones(len(d)),d.sgap.values,d.resid.values])
y=d.m1.values
b=la.lstsq(X,y,rcond=None)[0]
e=y-X@b
# cluster by date
codes=pd.factorize(d.date.values)[0]
XtX_inv=la.inv(X.T@X); meat=np.zeros((3,3))
for g_ in range(codes.max()+1):
    m_=codes==g_; xg=X[m_]; eg=e[m_]; s_=xg.T@eg; meat+=np.outer(s_,s_)
V=XtX_inv@meat@XtX_inv; se=np.sqrt(np.diag(V))
for nm,bb,ss in zip(["const","sgap","resid"],b,se):
    print(f"  {nm:<6} beta={bb:+8.4f}  t={bb/ss:+6.2f}")
print("\n### 逐年 resid 多空價差 →09:01")
d["yr"]=d.date.str.slice(0,4)
for y,g_ in d.groupby("yr"):
    qq=pd.qcut(g_.resid,5,labels=False,duplicates="drop")
    a=g_[qq==0].copy(); b_=g_[qq==qq.max()].copy()
    a["x"]=a.m1; b_["x"]=-b_.m1
    both=pd.concat([a[["date","x"]],b_[["date","x"]]]).groupby("date").x.mean()
    if len(both)<3: continue
    print(f"  {y}  n={len(a)+len(b_):<5} {both.mean()*2e4:+7.1f} bps  t={both.mean()/(both.std(ddof=1)/np.sqrt(len(both))):+5.2f}")
print("\n### 延遲檢定：09:01 收盤才進場 → 09:02 / 09:15")
for c_ in ("m1_2",):
    a=d[q==0].copy(); b=d[q==6].copy()
    a["x"]=a[c_]; b["x"]=-b[c_]
    g=pd.concat([a[["date","x"]],b[["date","x"]]]).groupby("date").x.mean()
    print(f"  {c_:<6} {g.mean()*2e4:+7.1f} bps  t={g.mean()/(g.std(ddof=1)/np.sqrt(len(g))):+5.2f}")
d["m1_15"]=d.c15/d.c1-1
a=d[q==0].copy(); b=d[q==6].copy(); a["x"]=a.m1_15; b["x"]=-b.m1_15
g=pd.concat([a[["date","x"]],b[["date","x"]]]).groupby("date").x.mean()
print(f"  m1_15  {g.mean()*2e4:+7.1f} bps  t={g.mean()/(g.std(ddof=1)/np.sqrt(len(g))):+5.2f}")
