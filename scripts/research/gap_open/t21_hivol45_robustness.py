import json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0,"src")
from stock_db import DATA_DIR
OUT=os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
SIDS=[str(r["sid"]) for r in uni]; NAME={str(r["sid"]):r["name"] for r in uni}
TB={str(r["sid"]):r["tick_bps"] for r in uni}

meta=pd.read_pickle(f"{OUT}/min_meta.pkl"); close=np.load(f"{OUT}/min_close.npy")
high=np.load(f"{OUT}/min_high.npy")
df=pd.DataFrame({"sid":meta.index.get_level_values(0).astype(str),
                 "date":meta.index.get_level_values(1),
                 "base":meta.base.values,"open0":meta.open0.values,
                 "prev_close":meta.prev_close.values,"adv20":meta.adv20.values,
                 "atr20":meta.atr20.values,"d_close":meta.d_close.values,
                 "c1":close[:,0],"c2":close[:,1],"h1":high[:,0]})
df=df[df.sid.isin(SIDS)]
df["gap"]=df.open0/df.base-1
df=df[(df.gap.abs()<=0.105)&(df.prev_close>=10)&(df.adv20>=5e7)].dropna(subset=["c1"])
df["m1"]=df.c1/df.open0-1
# 漲停價（近似：base*1.10，取兩位小數的 tick 容忍）
df["lim_up"]=df.base*1.10
df["at_lim"]=(df.h1>=df.lim_up*0.9985)
BASE=df[df.gap.abs()<=0.0025].groupby("date").m1.mean().mean()

def clt(s):
    g=s.groupby("date").sx.mean()
    return g.mean()*1e4, g.mean()/(g.std(ddof=1)/np.sqrt(len(g))), len(s), len(g)

def rep(tag, s):
    if len(s)<10: print(f"{tag:<28} n={len(s)} 太少"); return
    s=s.copy(); s["sx"]=np.sign(s.gap)*(s.m1-BASE)
    m,t,n,nd=clt(s)
    tr=s.sx.clip(s.sx.quantile(.01),s.sx.quantile(.99))
    print(f"{tag:<28} n={n:<5} 超額{m:+7.1f}bps t={t:+5.2f}  1%截尾{tr.mean()*1e4:+7.1f}  "
          f"中位{s.sx.median()*1e4:+6.1f}  勝率{(s.sx>0).mean()*100:5.1f}%")

print(f"零跳空基準 {BASE*1e4:+.1f} bps\n")
print("=== 1) 漲停成分拆解（gap>+2%）===")
up=df[df.gap>0.02]
rep("全部 gap>+2%", up)
rep("  其中第一分鐘觸漲停", up[up.at_lim])
rep("  未觸漲停", up[~up.at_lim])
print("\n=== 2) 跌空 gap<-3% 拆解 ===")
dn=df[df.gap<-0.03]
df["lim_dn"]=df.base*0.90
dn=dn.assign(at_ld=dn.c1<=dn.base*0.90*1.0015)
rep("全部 gap<-3%", dn)
rep("  開盤跌停附近", dn[dn.at_ld])
rep("  非跌停", dn[~dn.at_ld])

print("\n=== 3) 逐年（gap>+2% 未觸漲停 / gap<-3%）===")
for tag,s in [("gap>+2%",up),("gap>+2% 未觸漲停",up[~up.at_lim]),("gap<-3%",dn)]:
    print(f"-- {tag}")
    for y,g in s.assign(yr=s.date.str.slice(0,4)).groupby("yr"):
        rep(f"   {y}", g)

print("\n=== 4) 逐檔一致性（gap>+2%，超額 bps）===")
u=up.copy(); u["sx"]=np.sign(u.gap)*(u.m1-BASE)
tab=u.groupby("sid").sx.agg(["count","mean"]).query("count>=20").sort_values("mean",ascending=False)
tab["mean"]=tab["mean"]*1e4
pos=(tab["mean"]>0).sum()
print(tab.assign(name=[NAME.get(i,"") for i in tab.index]).to_string(float_format=lambda x:f"{x:.1f}"))
print(f"→ {pos}/{len(tab)} 檔為正")

print("\n=== 5) 成本對照 ===")
tb=pd.Series(TB)
print(f"45 檔一 tick 中位 {tb.median():.1f} bps、平均 {tb.mean():.1f} bps；"
      f"現股當沖來回稅費 ≈24 bps，個股期貨來回手續費 ≈2-4 bps + 價差")
