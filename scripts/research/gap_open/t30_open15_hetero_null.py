import pandas as pd, numpy as np, glob, json, os, sys
sys.path.insert(0,"src"); from stock_db import DATA_DIR
rng=np.random.default_rng(7)
# 重用 t29 的資料建構（只取到分層前那一段）；不要指到 /tmp，會隨開機消失
_t29 = open("scripts/research/gap_open/t29_open15_basis_adjusted.py").read()
_ns: dict = {}
exec(_t29.split('d["vq"]=')[0], _ns)   # noqa: S102
d = _ns["d"]; pd = _ns["pd"]; np = _ns["np"]; clt = _ns.get("clt")
d["vq"]=pd.qcut(d.f_vol,3,labels=["薄","中","厚"])
th=d[d.vq=="厚"].copy()
bf=np.polyfit(th.fgap,th.sgap,1)[0]; br=np.polyfit(th.sgap,th.fgap,1)[0]
vf_g=1/bf-1; vh_g=1/br-1; vg=np.var(th.fgap)/(1+vf_g)
g0=th.fgap.values*np.sqrt(vg/np.var(th.fgap))
med=np.median(np.abs(g0))
print(f"厚合約 var(ε_f)/var(g)={vf_g:.3f}  var(η)/var(g)={vh_g:.3f}")
def sim(hetero, nsim=600):
    res={"3~5%":[], ">5%":[]}
    for _ in range(nsim):
        g=rng.permutation(g0)
        w=(np.abs(g)/med) if hetero else np.ones_like(g)
        w=w/np.sqrt(np.mean(w**2))                       # 保持總變異不變
        f=np.clip(g+rng.normal(0,np.sqrt(vg*vf_g),len(g))*w,-.0999,.0999)
        s=np.clip(g+rng.normal(0,np.sqrt(vg*vh_g),len(g))*w,-.0999,.0999)
        ch=s-f; a=np.abs(f)
        for k,msk in [("3~5%",(a>.03)&(a<=.05)),(">5%",a>.05)]:
            if msk.sum(): res[k].append(np.sign(f[msk]).dot(ch[msk])/msk.sum())
    return {k:np.mean(v)*1e4 for k,v in res.items()}
print("虛無假象 (同質噪音):", {k:f"{v:.0f}" for k,v in sim(False).items()})
print("虛無假象 (噪音隨跳空放大):", {k:f"{v:.0f}" for k,v in sim(True).items()})
th["sx"]=np.sign(th.fgap)*th.chg
for k,msk in [("3~5%",(th.fgap.abs()>.03)&(th.fgap.abs()<=.05)),(">5%",th.fgap.abs()>.05)]:
    s=th[msk]; g=s.groupby("date").sx.mean()
    print(f"實測 {k}: n={len(s)} {g.mean()*1e4:+.0f} bps t={g.mean()/(g.std(ddof=1)/np.sqrt(len(g))):+.2f}")
print("\n### L2 直接量測樣本裡有沒有大跳空日")
b=pd.read_pickle("/tmp/open15.pkl")
print(f"  n={len(b)}  |08:45跳空| >3% 的:{int((b.fgap_0845.abs()>0.03).sum())} 筆, "
      f">2%:{int((b.fgap_0845.abs()>0.02).sum())} 筆, >1%:{int((b.fgap_0845.abs()>0.01).sum())} 筆")
