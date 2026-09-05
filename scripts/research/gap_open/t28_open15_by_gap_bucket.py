"""08:45→09:00：按跳空「大小 × 方向」再分層，並且按期貨流動性分開看。
關鍵：對 fgap 取符號/分桶會選到 fgap 自己的噪音 ε_f，製造假回檔。
   模型  fgap = g + ε_f ,  sgap = g + η(15分鐘真實innovation)
   β_fwd = var_g/(var_g+var_f) , β_rev = var_g/(var_g+var_η)
   → 用這兩個 β 反推噪音比例,再用 Monte-Carlo 算「純假象」該有多大,和實測比。
"""
import pandas as pd, numpy as np, glob, json, os, sys
sys.path.insert(0,"src"); from stock_db import DATA_DIR
rng=np.random.default_rng(0)
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
fut=fut[["stock_id","date","fgap","volume"]].rename(columns={"volume":"f_vol"})
OUT=os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
sp=pd.read_pickle(f"{OUT}/daily_filtered.pkl")[["stock_id","trade_date","gap","adv20","is_ex","prev_close"]]
sp.columns=["stock_id","date","sgap","adv20","is_ex","s_prevc"]
d=fut.merge(sp,on=["stock_id","date"],how="inner")
d=d[(d.fgap.abs()<=0.105)&(d.sgap.abs()<=0.105)&(d.s_prevc>=10)&(d.adv20>=5e7)&(~d.is_ex)].copy()
d["chg"]=d.sgap-d.fgap
d["vq"]=pd.qcut(d.f_vol,3,labels=["薄","中","厚"])

BK=[-0.105,-0.05,-0.03,-0.02,-0.01,-0.005,0.005,0.01,0.02,0.03,0.05,0.105]
LB=["跌<-5%","跌-5~-3%","跌-3~-2%","跌-2~-1%","跌-1~-0.5%","±0.5%",
    "漲+0.5~1%","漲+1~2%","漲+2~3%","漲+3~5%","漲>+5%"]
d["lb"]=pd.cut(d.fgap,BK,labels=LB)

def clt(s,c):
    g=s.groupby("date")[c].mean()
    if len(g)<3: return np.nan,np.nan
    return g.mean()*1e4, g.mean()/(g.std(ddof=1)/np.sqrt(len(g)))

def mc_null(s,nsim=400):
    """在 fgap=g+ε_f, sgap=g+η 的無方向性虛無下,同樣分桶會看到多少 bps"""
    bf=np.polyfit(s.fgap,s.sgap,1)[0]; br=np.polyfit(s.sgap,s.fgap,1)[0]
    vf_g=max(1/bf-1,0); vh_g=max(1/br-1,0)          # var_f/var_g , var_η/var_g
    vg=np.var(s.fgap)/(1+vf_g)
    out={l:[] for l in LB}
    g0=s.fgap.values*np.sqrt(vg/np.var(s.fgap))     # 保留厚尾形狀,縮到 var_g
    for _ in range(nsim):
        g=rng.permutation(g0)
        f=g+rng.normal(0,np.sqrt(vg*vf_g),len(g))
        sg=g+rng.normal(0,np.sqrt(vg*vh_g),len(g))
        lb=pd.cut(f,BK,labels=LB); ch=sg-f
        for l in LB:
            m=(lb==l)
            if m.sum(): out[l].append(np.sign(f[m]).dot(ch[m])/m.sum())
    return {l:(np.mean(v)*1e4 if v else np.nan) for l,v in out.items()}, bf, br, vf_g

for tier in ["厚","中","薄"]:
    s0=d[d.vq==tier]
    null,bf,br,vf_g = mc_null(s0)
    print(f"\n{'='*92}\n### {tier}合約  n={len(s0)}  口數中位={s0.f_vol.median():.0f}  "
          f"β_fwd={bf:.3f} β_rev={br:.3f}  隱含 var(ε_f)/var(g)={vf_g:.3f}")
    print(f"{'08:45 期貨跳空':<13}{'n':>6}{'跳空bps':>9}{'實測15分':>10}{'t':>7}{'虛無假象':>10}{'實測−虛無':>11}{'同向率':>8}")
    for l in LB:
        s=s0[s0.lb==l]
        if len(s)<25: continue
        m,t=clt(s,"chg")
        sx=float((np.sign(s.fgap)*s.chg).mean()*1e4)
        cont=float((np.sign(s.chg)==np.sign(s.fgap)).mean())*100 if l!="±0.5%" else np.nan
        nl=null.get(l,np.nan)
        print(f"{l:<13}{len(s):>6}{s.fgap.mean()*1e4:>9.0f}{sx:>10.1f}{t*np.sign(s.fgap.mean()):>7.2f}"
              f"{nl:>10.1f}{sx-nl:>11.1f}"
              f"{'' if cont!=cont else f'{cont:>7.1f}%'}")
    # 漲/跌各自合併
    for nm,sel in [("所有跌空 <-1%",s0[s0.fgap<-0.01]),("所有漲空 >+1%",s0[s0.fgap>0.01]),
                   ("大跌空 <-3%",s0[s0.fgap<-0.03]),("大漲空 >+3%",s0[s0.fgap>0.03])]:
        if len(sel)<25: continue
        sel=sel.copy(); sel["sx"]=np.sign(sel.fgap)*sel.chg
        m,t=clt(sel,"sx")
        print(f"   {nm:<14} n={len(sel):<5} {m:+7.1f} bps  t={t:+5.2f}")
