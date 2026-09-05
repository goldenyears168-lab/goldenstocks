"""t28 修正版：先扣掉「期貨-現貨系統性價差」再談延續/回檔。
sgap 平均比 fgap 高 ~27 bps（期貨 13:45 收 vs 現貨 13:30 收、基差/股利）。
沒扣掉的話 sign(fgap)×chg 會自動長出「漲空延續、跌空回檔」的假不對稱。
"""
import pandas as pd, numpy as np, glob, json, os, sys
sys.path.insert(0,"src"); from stock_db import DATA_DIR
rng=np.random.default_rng(0)
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
SIDS={str(r["sid"]) for r in uni}; NAME={str(r["sid"]):r["name"] for r in uni}
rows=[]
for f in glob.glob("/Users/jackm4/goldenstocks-data/cache/stock_futures_daily/*.parquet"):
    x=pd.read_parquet(f)
    if len(x) and str(x.stock_id.iloc[0]) in SIDS: rows.append(x)
fut=pd.concat(rows); fut=fut[fut.trading_session=="position"]
fut=fut.sort_values(["stock_id","date","volume"]).groupby(["stock_id","date"],as_index=False).last()
fut=fut.sort_values(["stock_id","date"])
fut["prev_c"]=fut.groupby("stock_id").close.shift(1)
fut["prev_ct"]=fut.groupby("stock_id").contract_date.shift(1)
fut=fut[fut.contract_date==fut.prev_ct]
fut["fgap"]=fut.open/fut.prev_c-1
fut=fut[["stock_id","date","fgap","volume"]].rename(columns={"volume":"f_vol"})
OUT=os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
sp=pd.read_pickle(f"{OUT}/daily_filtered.pkl")[["stock_id","trade_date","gap","adv20","is_ex","prev_close"]]
sp.columns=["stock_id","date","sgap","adv20","is_ex","s_prevc"]
d=fut.merge(sp,on=["stock_id","date"],how="inner")
d=d[(d.fgap.abs()<=0.105)&(d.sgap.abs()<=0.105)&(d.s_prevc>=10)&(d.adv20>=5e7)&(~d.is_ex)].copy()
d["chg_raw"]=d.sgap-d.fgap
d["ym"]=d.date.str.slice(0,7)
print(f"樣本 {len(d)} 股日 / {d.stock_id.nunique()} 檔 / {d.date.nunique()} 日")
print(f"[系統性價差] chg 全樣本平均 {d.chg_raw.mean()*1e4:+.1f} bps、中位 {d.chg_raw.median()*1e4:+.1f} bps")
d["chg"]=d.chg_raw-d.groupby(["stock_id","ym"]).chg_raw.transform("median")   # 逐檔逐月扣基差
print(f"[扣掉逐檔逐月基差後] 平均 {d.chg.mean()*1e4:+.1f} bps")
d["vq"]=pd.qcut(d.f_vol,3,labels=["薄","中","厚"])
BK=[-0.105,-0.05,-0.03,-0.02,-0.01,-0.005,0.005,0.01,0.02,0.03,0.05,0.105]
LB=["跌<-5%","跌-5~-3%","跌-3~-2%","跌-2~-1%","跌-1~-0.5%","±0.5%",
    "漲+0.5~1%","漲+1~2%","漲+2~3%","漲+3~5%","漲>+5%"]
d["lb"]=pd.cut(d.fgap,BK,labels=LB)
def clt(s,c):
    g=s.groupby("date")[c].mean()
    if len(g)<3: return np.nan,np.nan
    return g.mean()*1e4, g.mean()/(g.std(ddof=1)/np.sqrt(len(g)))
def mc_null(s,nsim=300):
    bf=np.polyfit(s.fgap,s.sgap,1)[0]; br=np.polyfit(s.sgap,s.fgap,1)[0]
    vf_g=max(1/bf-1,0); vh_g=max(1/br-1,0); vg=np.var(s.fgap)/(1+vf_g)
    out={l:[] for l in LB}; g0=s.fgap.values*np.sqrt(vg/np.var(s.fgap))
    for _ in range(nsim):
        g=rng.permutation(g0)
        f=g+rng.normal(0,np.sqrt(vg*vf_g),len(g)); sg=g+rng.normal(0,np.sqrt(vg*vh_g),len(g))
        lb=pd.cut(f,BK,labels=LB); ch=sg-f
        for l in LB:
            msk=(lb==l)
            if msk.sum(): out[l].append(np.sign(f[msk]).dot(ch[msk])/msk.sum())
    return {l:(np.mean(v)*1e4 if v else np.nan) for l,v in out.items()}, vf_g
for tier in ["厚","中","薄"]:
    s0=d[d.vq==tier].copy(); s0["sx"]=np.sign(s0.fgap)*s0.chg
    null,vf_g=mc_null(s0)
    print(f"\n{'='*88}\n### {tier}合約 n={len(s0)} 口數中位={s0.f_vol.median():.0f} "
          f"隱含 var(ε_f)/var(g)={vf_g:.3f}   (正=延續 負=回檔,已扣基差)")
    print(f"{'08:45 期貨跳空':<13}{'n':>6}{'跳空bps':>9}{'實測':>9}{'t':>7}{'虛無':>8}{'實測−虛無':>10}{'同向率':>8}")
    for l in LB:
        s=s0[s0.lb==l]
        if len(s)<25: continue
        m,t=clt(s,"sx")
        cont=float((np.sign(s.chg)==np.sign(s.fgap)).mean())*100
        print(f"{l:<13}{len(s):>6}{s.fgap.mean()*1e4:>9.0f}{m:>9.1f}{t:>7.2f}{null.get(l,np.nan):>8.1f}"
              f"{m-null.get(l,np.nan):>10.1f}{'' if l=='±0.5%' else f'{cont:>7.1f}%'}")
    for nm,sel in [("跌空 <-1%",s0[s0.fgap<-0.01]),("漲空 >+1%",s0[s0.fgap>0.01]),
                   ("跌空 <-3%",s0[s0.fgap<-0.03]),("漲空 >+3%",s0[s0.fgap>0.03])]:
        if len(sel)<25: continue
        m,t=clt(sel,"sx"); print(f"   {nm:<10} n={len(sel):<5} {m:+7.1f} bps  t={t:+5.2f}")

print("\n\n### 極端桶（|08:45 期貨跳空|>5%）的漲跌停牆檢查")
ex=d[d.fgap.abs()>0.05].copy()
ex["s_lim"]=ex.sgap.abs()>=0.095
ex["f_lim"]=ex.fgap.abs()>=0.095
print(f"n={len(ex)}  現貨開盤即觸停板 {ex.s_lim.mean()*100:.1f}%  期貨開盤即觸停板 {ex.f_lim.mean()*100:.1f}%")
print(f"平均剩餘空間: 跳空同向只剩 {(0.10-ex.fgap.abs()).mean()*1e4:.0f} bps,反向有 {(0.10+ex.fgap.abs()).mean()*1e4:.0f} bps")
for tier in ["厚","中","薄"]:
    s=ex[ex.vq==tier].copy()
    if len(s)<30: continue
    s["sx"]=np.sign(s.fgap)*s.chg
    a,ta=clt(s,"sx"); b=s[~s.s_lim]; m2,t2=clt(b,"sx")
    print(f"  {tier} n={len(s):<4} 全部 {a:+7.1f}(t={ta:+5.2f})   剔除現貨觸停板後 n={len(b):<4} {m2:+7.1f}(t={t2:+5.2f})")
print("\n### 同樣的極端桶,把「剩餘空間」做成對照：只留 |fgap|<5% 但 >3% 的（牆還遠）")
mid=d[(d.fgap.abs()>0.03)&(d.fgap.abs()<=0.05)].copy(); mid["sx"]=np.sign(mid.fgap)*mid.chg
for tier in ["厚","中","薄"]:
    s=mid[mid.vq==tier]
    if len(s)<30: continue
    m,t=clt(s,"sx"); print(f"  {tier} n={len(s):<4} {m:+7.1f} bps t={t:+5.2f}")
print("\n### 厚合約：把中間各桶合併成一句話")
th=d[d.vq=="厚"].copy(); th["sx"]=np.sign(th.fgap)*th.chg
for nm,s in [("0.5~3% 中等跳空",th[(th.fgap.abs()>0.005)&(th.fgap.abs()<=0.03)]),
             ("3~5%",th[(th.fgap.abs()>0.03)&(th.fgap.abs()<=0.05)]),
             (">5%",th[th.fgap.abs()>0.05]),
             (">5% 且現貨沒觸停板",th[(th.fgap.abs()>0.05)&(th.sgap.abs()<0.095)])]:
    if len(s)<25: continue
    m,t=clt(s,"sx"); print(f"  {nm:<22} n={len(s):<5} {m:+7.1f} bps  t={t:+5.2f}  "
                           f"同向率 {float((np.sign(s.chg)==np.sign(s.fgap)).mean())*100:.1f}%")

print("\n\n### 極端桶的對稱診斷：改用『現貨跳空』分桶（若是 EIV 假象,兩邊都會是負）")
d["sx_f"]=np.sign(d.fgap)*d.chg          # 以期貨跳空定義：負=現貨開盤比期貨更靠近前收
d["sx_s"]=np.sign(d.sgap)*(-d.chg)       # 以現貨跳空定義：負=期貨開盤比現貨更靠近前收
th=d[d.vq=="厚"]
for nm,sel,col in [("|期貨跳空|>5% → 現貨開在哪",th[th.fgap.abs()>0.05],"sx_f"),
                   ("|現貨跳空|>5% → 期貨開在哪",th[th.sgap.abs()>0.05],"sx_s")]:
    m,t=clt(sel,col)
    print(f"  {nm:<28} n={len(sel):<5} {m:+8.1f} bps  t={t:+5.2f}")
print("  → 若一負一正 = 期貨 08:45 真的開過頭；若兩者皆負 = 純測量噪音")

print("\n### 厚合約 |期貨跳空|>5%：逐年 / 漲跌分開 / 逐檔")
s=th[th.fgap.abs()>0.05].copy(); s["sx"]=s.sx_f
s["yr"]=s.date.str.slice(0,4)
for y,g in s.groupby("yr"):
    m,t=clt(g,"sx"); print(f"   {y} n={len(g):<4} {m:+8.1f} bps t={t:+5.2f}")
for nm,g in [("漲空 >+5%",s[s.fgap>0]),("跌空 <-5%",s[s.fgap<0])]:
    m,t=clt(g,"sx"); print(f"   {nm} n={len(g):<4} {m:+8.1f} bps t={t:+5.2f}  同向率 "
                           f"{float((np.sign(g.chg)==np.sign(g.fgap)).mean())*100:.1f}%")
tab=s.groupby("stock_id").sx.agg(["count","mean"]).query("count>=8").sort_values("mean")
tab["mean"]*=1e4; tab["name"]=[NAME.get(i,"") for i in tab.index]
print(tab.to_string(float_format=lambda x:f"{x:.0f}"))
print(f"   → {(tab['mean']<0).sum()}/{len(tab)} 檔為負")
