#!/usr/bin/env python3
"""期貨自己的 08:45→08:59：延續還是回檔（分鐘級，單一商品內部量測）。

跟 t27~t30 的差別：那些量的是 sgap−fgap（期貨開盤 vs 現貨開盤，跨商品），
無法與量測噪音分離。這裡整段都在同一口期貨上量，而且主結果用
「08:46 收盤價 → 08:59 收盤價」——與 gap 的定義價（08:45 首筆）不共用任何價格，
所以不會有「對噪音取符號」的假回檔。
"""
from __future__ import annotations
import json, os, sys, glob
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0,"src")
from stock_db import DATA_DIR
OUT=Path(os.path.expanduser("~/goldenstocks-data/data/cache/gap_open"))
TICK=OUT/"futtick"
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
NAME={str(r["sid"]):r["name"] for r in uni}
ref=pd.read_pickle(OUT/"futtick_daily_ref.pkl")
ref=ref.set_index(["futures_id","date"])

rows=[]
for p in sorted(TICK.glob("*.json")):
    fid,day=p.stem.split("_")
    try: d=json.loads(p.read_text())
    except Exception: continue
    if not d: continue
    try: r=ref.loc[(fid,day)]
    except KeyError: continue
    cd=str(r.contract_date)
    t=[(x["date"][11:19],float(x["price"]),float(x["volume"])) for x in d
       if str(x.get("contract_date"))==cd]
    t.sort()
    win=[x for x in t if "08:45:00"<=x[0]<"09:00:00"]
    if len(win)<5: continue
    p_open=win[0][1]
    m1=[x for x in win if x[0]<"08:46:00"]
    p_m1=m1[-1][1] if m1 else np.nan
    p_end=win[-1][1]; t_end=win[-1][0]
    nmin=len({x[0][:5] for x in win})
    rows.append(dict(fid=fid,sid=str(r.stock_id),date=day,prev_c=float(r.prev_c),
                     p_open=p_open,p_m1=p_m1,p_end=p_end,t_end=t_end,
                     ntk=len(win),vol=sum(x[2] for x in win),nmin=nmin,
                     f_vol=float(r.volume),d_open=float(r.open)))
b=pd.DataFrame(rows)
print(f"讀入 {len(b)} symbol-day / {b.sid.nunique()} 檔 / {b.date.nunique()} 日 / {b.date.min()}~{b.date.max()}")
print(f"[驗證] 逐筆 08:45 首筆 vs 日線 open：完全相等 "
      f"{float((b.p_open==b.d_open).mean())*100:.1f}%，|差| 中位 "
      f"{float((b.p_open/b.d_open-1).abs().median()*1e4):.1f} bps")
raw=b.copy()
print("\n### 覆蓋率：08:45~09:00 這 15 分鐘,期貨到底有沒有在交易")
cov=raw.groupby("sid").agg(n=("date","count"),ntk=("ntk","median"),nmin=("nmin","median"),
                           vol=("vol","median"),fvol=("f_vol","median"),
                           late=("t_end",lambda x:float((x>="08:55:00").mean())))
cov["name"]=[NAME.get(i,"") for i in cov.index]
cov=cov.sort_values("fvol",ascending=False)
print(cov.to_string(float_format=lambda x:f"{x:.2f}"))
b=b.dropna(subset=["p_m1"])
b=b[(b.t_end>="08:55:00")]          # 收尾太早=最後 5 分鐘沒成交,價格是舊的
print(f"\n要求 08:46 有價 且 08:55 後仍有成交 → 剩 {len(b)} / {len(raw)} ({len(b)/max(len(raw),1)*100:.0f}%)")
b["gap"]=b.p_open/b.prev_c-1
b=b[b.gap.abs()<=0.105]
b["r_open_end"]=b.p_end/b.p_open-1
b["r_m1_end"]=b.p_end/b.p_m1-1        # 主結果：與 gap 定義價不共用
b["r_open_m1"]=b.p_m1/b.p_open-1
for c in ("r_open_end","r_m1_end","r_open_m1"):
    b["s_"+c]=np.sign(b.gap)*b[c]

def clt(s,c):
    g=s.groupby("date")[c].mean()
    if len(g)<3: return np.nan,np.nan
    return g.mean()*1e4, g.mean()/(g.std(ddof=1)/np.sqrt(len(g)))

B=[0,.005,.01,.02,.03,.05,.105]; L=["0~0.5%","0.5~1%","1~2%","2~3%","3~5%",">5%"]
b["ab"]=pd.cut(b.gap.abs(),B,labels=L)
b["vq"]=pd.qcut(b.f_vol,3,labels=["薄","中","厚"])
print(f"\n窗內成交筆數中位 {b.ntk.median():.0f}、有成交的分鐘數中位 {b.nmin.median():.0f}/15")

for tier in ["全部","厚","中"]:
    s0=b if tier=="全部" else b[b.vq==tier]
    print(f"\n{'='*86}\n### {tier}  n={len(s0)}  (正=延續 08:45 跳空,負=回檔;日 cluster t)")
    print(f"{'|08:45跳空|':<10}{'n':>6}{'跳空bps':>9}{'08:46→08:59':>13}{'t':>7}"
          f"{'08:45→08:59':>13}{'t':>7}{'同向率':>8}")
    for l in L:
        s=s0[s0.ab==l]
        if len(s)<25: continue
        m1,t1=clt(s,"s_r_m1_end"); m2,t2=clt(s,"s_r_open_end")
        print(f"{l:<10}{len(s):>6}{s.gap.abs().mean()*1e4:>9.0f}{m1:>13.1f}{t1:>7.2f}"
              f"{m2:>13.1f}{t2:>7.2f}{float((s.s_r_m1_end>0).mean())*100:>7.1f}%")
    for nm,s in [("跌空 <-2%",s0[s0.gap<-0.02]),("漲空 >+2%",s0[s0.gap>0.02]),
                 ("跌空 <-5%",s0[s0.gap<-0.05]),("漲空 >+5%",s0[s0.gap>0.05])]:
        if len(s)<25: continue
        m1,t1=clt(s,"s_r_m1_end")
        print(f"   {nm:<10} n={len(s):<5} 08:46→08:59 {m1:+7.1f} bps t={t1:+5.2f}")
print("\n### 分解：08:45→08:46（含首筆噪音）vs 08:46→08:59（乾淨）")
for l in L:
    s=b[b.ab==l]
    if len(s)<25: continue
    a,ta=clt(s,"s_r_open_m1"); c,tc=clt(s,"s_r_m1_end")
    print(f"  {l:<9} n={len(s):>5}  第一分鐘 {a:+7.1f}(t={ta:+5.2f})   之後14分 {c:+7.1f}(t={tc:+5.2f})")
print("\n### 逐年（|跳空|>2%，08:46→08:59）")
b["yr"]=b.date.str.slice(0,4)
for y,g in b[b.gap.abs()>0.02].groupby("yr"):
    m,t=clt(g,"s_r_m1_end"); print(f"  {y} n={len(g):<5} {m:+7.1f} bps t={t:+5.2f}")
print("\n### 放寬過濾（只要窗內 >=5 筆成交,不管收尾多早）作為穩健性對照")
r2=raw.dropna(subset=["p_m1"]).copy()
r2["gap"]=r2.p_open/r2.prev_c-1; r2=r2[r2.gap.abs()<=0.105]
r2["s_r_m1_end"]=np.sign(r2.gap)*(r2.p_end/r2.p_m1-1)
r2["ab"]=pd.cut(r2.gap.abs(),B,labels=L)
for l in L:
    s_=r2[r2.ab==l]
    if len(s_)<25: continue
    m,t=clt(s_,"s_r_m1_end")
    print(f"  {l:<9} n={len(s_):>5}  {m:+7.1f} bps  t={t:+5.2f}")
b.to_pickle(OUT/"futopen15_minute.pkl")
