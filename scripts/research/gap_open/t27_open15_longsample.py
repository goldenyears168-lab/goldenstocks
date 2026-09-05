"""08:45 期貨開盤 → 09:00 現貨開盤 這 15 分鐘：延續 or 回檔（長樣本版）
gap 空間：fgap=期貨08:45開/期貨前收-1、sgap=現貨09:00開/現貨前收-1
15 分鐘的淨變化 = sgap − fgap
"""
import pandas as pd, numpy as np, glob, json, os, sys
sys.path.insert(0,"src"); from stock_db import DATA_DIR
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
SIDS={str(r["sid"]) for r in uni}; NAME={str(r["sid"]):r["name"] for r in uni}
rows=[]
for f in glob.glob("/Users/jackm4/goldenstocks-data/cache/stock_futures_daily/*.parquet"):
    d=pd.read_parquet(f)
    if len(d) and str(d.stock_id.iloc[0]) in SIDS: rows.append(d)
fut=pd.concat(rows); fut=fut[fut.trading_session=="position"]
fut=fut.sort_values(["stock_id","date","volume"]).groupby(["stock_id","date"],as_index=False).last()
fut=fut.sort_values(["stock_id","date"])
fut["prev_c"]=fut.groupby("stock_id").close.shift(1)
fut["prev_ct"]=fut.groupby("stock_id").contract_date.shift(1)
fut=fut[fut.contract_date==fut.prev_ct]
fut["fgap"]=fut.open/fut.prev_c-1
fut=fut[["stock_id","date","fgap","volume","open","prev_c"]].rename(columns={"volume":"f_vol"})
OUT=os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
sp=pd.read_pickle(f"{OUT}/daily_filtered.pkl")[["stock_id","trade_date","gap","open","base","adv20","is_ex","prev_close"]]
sp.columns=["stock_id","date","sgap","s_open","s_base","adv20","is_ex","s_prevc"]
d=fut.merge(sp,on=["stock_id","date"],how="inner")
d=d[(d.fgap.abs()<=0.105)&(d.sgap.abs()<=0.105)&(d.s_prevc>=10)&(d.adv20>=5e7)&(~d.is_ex)]
d["chg"]=d.sgap-d.fgap                       # 15 分鐘淨變化
d["sx"]=np.sign(d.fgap)*d.chg                # 正=延續 負=回檔
d["sx_rev"]=np.sign(d.sgap)*(d.fgap-d.sgap)  # 反向對照（診斷 EIV）
print(f"樣本 {len(d)} 股日 / {d.stock_id.nunique()} 檔 / {d.date.nunique()} 日 / {d.date.min()}~{d.date.max()}")

def clt(s,c):
    g=s.groupby("date")[c].mean()
    if len(g)<3: return np.nan,np.nan
    return g.mean()*1e4, g.mean()/(g.std(ddof=1)/np.sqrt(len(g)))

print("\n### A) 依 08:45 期貨跳空大小分桶：這 15 分鐘往哪走（正=延續 負=回檔）")
B=[0,.005,.01,.02,.03,.05,.105]; L=["0~0.5%","0.5~1%","1~2%","2~3%","3~5%",">5%"]
d["ab"]=pd.cut(d.fgap.abs(),B,labels=L)
print(f"{'|08:45跳空|':<11}{'n':>6}{'跳空bps':>9}{'15分變化':>11}{'t':>7}{'同向率':>8}{'相對縮放':>9}")
for l in L:
    s=d[d.ab==l]
    if not len(s): continue
    m,t=clt(s,"sx")
    ratio=float((s.sgap/s.fgap).median())
    print(f"{l:<11}{len(s):>6}{s.fgap.abs().mean()*1e4:>9.0f}{m:>11.1f}{t:>7.2f}"
          f"{float((s.sx>0).mean())*100:>7.1f}%{ratio:>9.2f}")
for nm,s in [("全部",d),("|跳空|>1%",d[d.fgap.abs()>0.01]),("|跳空|>2%",d[d.fgap.abs()>0.02])]:
    m,t=clt(s,"sx"); print(f"  {nm:<10} n={len(s):<5} {m:+7.1f} bps  t={t:+5.2f}")

print("\n### B) EIV 診斷：把兩邊角色對調（若兩者同號為負 = 純測量誤差的均值回歸假象）")
for nm,c in [("sign(期貨跳空)×(現貨−期貨)  正=延續","sx"),
             ("sign(現貨跳空)×(期貨−現貨)  正=延續","sx_rev")]:
    m,t=clt(d,c); print(f"  {nm:<38} {m:+7.1f} bps  t={t:+5.2f}")
bf=np.polyfit(d.fgap,d.sgap,1)[0]; br=np.polyfit(d.sgap,d.fgap,1)[0]
print(f"  正迴歸 β(sgap~fgap)={bf:.3f}   逆迴歸 β(fgap~sgap)={br:.3f}   "
      f"幾何平均 √(βf/βr 的隱含真值)={np.sqrt(bf/br):.3f}  (=1 表示兩邊噪音對稱、無方向性)")

print("\n### C) 移除當日大盤共同成分（同日跨檔 demean）後")
for c in ("sx","sx_rev"):
    d[c+"_dm"]=d[c]-d.groupby("date")[c].transform("mean")
m,t=clt(d,"sx_dm"); print(f"  demean 後 sx {m:+7.1f} bps t={t:+5.2f}（依定義接近 0，看的是 t）")
g=d.groupby("date").sx.mean()
print(f"  當日平均 sx 的時間序列: 平均 {g.mean()*1e4:+.1f} bps, t={g.mean()/(g.std(ddof=1)/np.sqrt(len(g))):+.2f}, n_day={len(g)}")

print("\n### D) 逐年")
d["yr"]=d.date.str.slice(0,4)
for y,s in d.groupby("yr"):
    m,t=clt(s,"sx"); m2,t2=clt(s[s.fgap.abs()>0.01],"sx")
    print(f"  {y} n={len(s):<5} 全部 {m:+7.1f}(t={t:+5.2f})   |跳空|>1% n={len(s[s.fgap.abs()>0.01]):<4} {m2:+7.1f}(t={t2:+5.2f})")

print("\n### E) 期貨流動性分層（f_vol 為當日期貨口數；薄的合約 08:45 開盤價噪音大）")
d["vq"]=pd.qcut(d.f_vol,3,labels=["薄","中","厚"])
for v in ["薄","中","厚"]:
    s=d[d.vq==v]; m,t=clt(s,"sx")
    print(f"  {v} n={len(s):<5} 口數中位 {s.f_vol.median():>6.0f}  sx {m:+7.1f} t={t:+5.2f}  "
          f"|15分變化|中位 {s.chg.abs().median()*1e4:>5.0f} bps")

print("\n### F) 回頭修正上一輪：resid(現貨開−期貨開) 對 09:01 的預測力，是否只存在薄合約")
meta=pd.read_pickle(f"{OUT}/min_meta.pkl"); close=np.load(f"{OUT}/min_close.npy")
mm=pd.DataFrame({"stock_id":meta.index.get_level_values(0).astype(str),
                 "date":meta.index.get_level_values(1),"open0":meta.open0.values,
                 "c1":close[:,0],"c15":close[:,14]})
e=d.merge(mm,on=["stock_id","date"],how="inner").dropna(subset=["c1"])
e["m1"]=e.c1/e.open0-1
e["resid"]=e.chg
print(f"  子樣本 {len(e)} 股日 / {e.stock_id.nunique()} 檔")
import numpy.linalg as la
for v in ["薄","中","厚","全部"]:
    s=e if v=="全部" else e[e.vq==v]
    if len(s)<200: print(f"  {v} n={len(s)} 太少"); continue
    X=np.column_stack([np.ones(len(s)),s.sgap.values,s.resid.values]); y=s.m1.values
    b=la.lstsq(X,y,rcond=None)[0]; err=y-X@b
    codes=pd.factorize(s.date.values)[0]; XtX=la.inv(X.T@X); meat=np.zeros((3,3))
    for g_ in range(codes.max()+1):
        msk=codes==g_; xg=X[msk]; sg=xg.T@err[msk]; meat+=np.outer(sg,sg)
    se=np.sqrt(np.diag(XtX@meat@XtX))
    print(f"  {v:<3} n={len(s):<5} beta_resid={b[2]:+8.4f} t={b[2]/se[2]:+6.2f}   "
          f"beta_sgap={b[1]:+8.4f} t={b[1]/se[1]:+6.2f}")
