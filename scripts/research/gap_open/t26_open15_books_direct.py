"""期貨 08:45 開盤 → 08:59 → 現貨 09:00 開盤：這 15 分鐘是延續還是回檔。
資料：cache/<root>_books/*.jsonl（富邦 ws 五檔推播，取最佳買賣中價）
"""
import json, glob, os, sys, collections
import numpy as np, pandas as pd
sys.path.insert(0,"src"); from stock_db import DATA_DIR
R="/Users/jackm4/goldenstocks-data/cache"
uni=json.loads((DATA_DIR/"cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
SID45={str(r["sid"]) for r in uni}; NAME={str(r["sid"]):r["name"] for r in uni}
TB={str(r["sid"]):r["tick_bps"] for r in uni}

mp={}; fut_daily=[]
for f in glob.glob(R+"/stock_futures_daily/*.parquet"):
    d=pd.read_parquet(f)
    if len(d):
        mp[str(d.futures_id.iloc[0])]=str(d.stock_id.iloc[0]); fut_daily.append(d)
fd=pd.concat(fut_daily)
fd=fd[fd.trading_session=="position"].sort_values(["stock_id","date","volume"]).groupby(
    ["stock_id","date"],as_index=False).last().sort_values(["stock_id","date"])
fd["prev_c"]=fd.groupby("stock_id").close.shift(1)
fd["prev_ct"]=fd.groupby("stock_id").contract_date.shift(1)
fd=fd[["stock_id","date","open","prev_c","contract_date","prev_ct","volume"]].rename(
    columns={"open":"f_open_daily","volume":"f_vol"})

def mids(path):
    """回傳 [(t_str, mid, spread_bps)]，只取 08:44~09:00"""
    out=[]
    for line in open(path):
        i=line.find('"ts"')
        if i<0: continue
        t=line[i+7:i+7+23]
        if not ("08:44"<=t[11:16]<"09:00"): continue
        try: r=json.loads(line)
        except: continue
        b=r.get("bids") or []; a=r.get("asks") or []
        if not b or not a: continue
        bb=b[0]["price"]; aa=a[0]["price"]
        if not (bb and aa) or aa<=bb: continue
        m=(bb+aa)/2
        out.append((t[11:23], m, (aa-bb)/m*1e4))
    out.sort()
    return out

rows=[]
for dd in sorted(glob.glob(R+"/*_books")):
    root=os.path.basename(dd).replace("_books","").upper()
    fid=root+"F" if len(root)==2 else root
    sid=mp.get(fid)
    if sid not in SID45: continue
    for f in sorted(glob.glob(dd+"/*.jsonl")):
        import re as _re; date=_re.search(r"(\d{4}-\d{2}-\d{2})",os.path.basename(f)).group(1)
        M=mids(f)
        if len(M)<30: continue
        o=[x for x in M if x[0]>="08:45:00"]
        if not o: continue
        p_open=o[0][1]; t_open=o[0][0]
        pre=[x for x in M if x[0]<"08:45:00"]
        p_0859=M[-1][1]; t_last=M[-1][0]
        if t_last<"08:58:00": continue
        rows.append(dict(root=root,sid=sid,date=date,p_open=p_open,t_open=t_open,
                         p_0859=p_0859,t_last=t_last,n=len(M),
                         spr=np.median([x[2] for x in M])))
b=pd.DataFrame(rows)
print("root-day",len(b),"檔",b.sid.nunique(),"日",b.date.nunique(),b.date.min(),b.date.max())

b=b.merge(fd,left_on=["sid","date"],right_on=["stock_id","date"],how="left")
OUT=os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
sp=pd.read_pickle(f"{OUT}/daily_filtered.pkl")[["stock_id","trade_date","open","base","gap","o2c","close"]]
sp.columns=["sid","date","s_open","s_base","sgap","s_o2c","s_close"]
b=b.merge(sp,on=["sid","date"],how="left")
print("併上期貨日線/現貨日線後有效:", b.dropna(subset=["prev_c","s_open"]).shape[0])
b=b.dropna(subset=["prev_c","s_open"])
b=b[b.contract_date==b.prev_ct]
b["fgap_0845"]=b.p_open/b.prev_c-1
b["fgap_0859"]=b.p_0859/b.prev_c-1
b["fut15"]=b.p_0859/b.p_open-1
b["s_vs_f"]=b.s_open/b.p_0859-1
print("\n[驗證] 書中 08:45 首筆中價 vs 期貨日線 open：",
      f"|差| 中位 {float((b.p_open/b.f_open_daily-1).abs().median()*1e4):.0f} bps, "
      f"corr={np.corrcoef(b.p_open,b.f_open_daily)[0,1]:.4f}")
print(f"首筆時戳 p50={sorted(b.t_open)[len(b)//2]}，最後一筆 p50={sorted(b.t_last)[len(b)//2]}，"
      f"五檔價差中位 {b.spr.median():.0f} bps")
b.to_pickle("/tmp/open15.pkl")

def clt(s,col,by="date"):
    g=s.groupby(by)[col].mean()
    if len(g)<3: return np.nan,np.nan
    return g.mean()*1e4, g.mean()/(g.std(ddof=1)/np.sqrt(len(g)))

print(f"\n=== 樣本 {len(b)} root-day / {b.sid.nunique()} 檔 / {b.date.nunique()} 日 ===")
print(f"08:45 期貨跳空 |中位|={b.fgap_0845.abs().median()*1e4:.0f} bps；"
      f"08:45→08:59 期貨波動 sd={b.fut15.std()*1e4:.0f} bps、|中位|={b.fut15.abs().median()*1e4:.0f} bps")

print("\n### A) 08:45→08:59 這 15 分鐘，延續還是回檔 08:45 的跳空？")
b["sx15"]=np.sign(b.fgap_0845)*b.fut15
q=pd.qcut(b.fgap_0845,4,labels=False)
print(f"{'08:45跳空桶':<14}{'n':>5}{'跳空bps':>9}{'15分報酬':>11}{'t':>7}{'同向率':>8}")
for i in range(4):
    s=b[q==i]; m,t=clt(s,"fut15")
    print(f"{f'Q{i+1}':<14}{len(s):>5}{s.fgap_0845.mean()*1e4:>9.0f}{m:>11.1f}"
          f"{t if t==t else float('nan'):>7.2f}{float((np.sign(s.fut15)==np.sign(s.fgap_0845)).mean())*100:>7.1f}%")
for nm,s in [("全部",b),("|跳空|>1%",b[b.fgap_0845.abs()>0.01]),("|跳空|>2%",b[b.fgap_0845.abs()>0.02])]:
    m,t=clt(s,"sx15")
    print(f"  {nm:<10} n={len(s):<4} 跳空方向報酬 {m:+7.1f} bps  t={t:+5.2f}  "
          f"同向率 {float((s.sx15>0).mean())*100:.1f}%")
bb=np.polyfit(b.fgap_0845,b.fut15,1)
print(f"  迴歸 fut15 = {bb[0]:+.3f} × fgap_0845 + {bb[1]*1e4:+.1f} bps  (β<0 = 回檔)")

print("\n### B) 現貨 09:00 開盤 vs 期貨 08:59 —— 現貨接著延續嗎？")
b["sxs"]=np.sign(b.fgap_0845)*b.s_vs_f
for nm,s in [("全部",b),("|跳空|>1%",b[b.fgap_0845.abs()>0.01])]:
    m,t=clt(s,"s_vs_f"); m2,t2=clt(s,"sxs")
    print(f"  {nm:<10} n={len(s):<4} 現貨開盤−期貨08:59 = {m:+7.1f} bps (t={t:+5.2f})；"
          f"跳空方向 {m2:+7.1f} bps (t={t2:+5.2f})")
print("\n### C) 拆解：整段 08:45→現貨09:00 的跳空變化")
b["dgap"]=b.sgap-b.fgap_0845
for nm,s in [("全部",b),("|跳空|>1%",b[b.fgap_0845.abs()>0.01])]:
    s=s.copy(); s["sd"]=np.sign(s.fgap_0845)*s.dgap
    m,t=clt(s,"sd")
    print(f"  {nm:<10} n={len(s):<4} sgap−fgap0845 跳空方向 {m:+7.1f} bps  t={t:+5.2f}")
print("\n### D) 逐檔（|跳空|>0.5%，15 分跳空方向報酬 bps）")
s=b[b.fgap_0845.abs()>0.005]
tab=s.groupby("sid").sx15.agg(["count","mean"]).query("count>=4").sort_values("mean")
tab["mean"]*=1e4
tab["name"]=[NAME.get(i,"") for i in tab.index]
print(tab.to_string(float_format=lambda x:f"{x:.1f}"))
print(f"→ {(tab['mean']<0).sum()}/{len(tab)} 檔為負（回檔）")
