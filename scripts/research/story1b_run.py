#!/usr/bin/env python3
"""故事 1B：假說 A（護盤式卡位）/ 假說 B（下跌時量縮 vs 爆量）檢定主程式。"""
from __future__ import annotations
import sys, pathlib, itertools
sys.path.insert(0, "/Users/jackm4/goldenstocks/scripts/research")
import numpy as np, pandas as pd
import story1b_lib as L

OUT = pathlib.Path.home() / "goldenstocks-data/data/scratch"
p, core = L.load()
print(f"panel={len(p)} CORE={len(core)}")

# ---- 市場下跌「episode」計數（獨立樣本數的真實上限）
mk = p.groupby(["day", "hm"], as_index=False).agg(
    m15=("mkt15", "first"), z15=("mkt15_z", "first"), z5=("mkt5_z", "first"))
mk = mk.sort_values(["day", "hm"])
for th in (-1.0, -1.5):
    f = (mk.z15 <= th).to_numpy()
    ep = ((f) & (~np.r_[False, f[:-1]])).sum()
    print(f"z15<={th}: {f.sum()} 分鐘 / {ep} 段獨立 episode")

rows = []
def ev(mask, name, cols=("x_fwd30", "x_fwd60", "x_fwd_close"), B=300):
    for c in cols:
        r = L.evaluate(p, mask, name, col=c, B=B)
        rows.append(r)

print("\n================ 假說 A：護盤式卡位 ================")
mkt_down = {"Z15<=-1": p.mkt15_z <= -1.0, "Z15<=-1.5": p.mkt15_z <= -1.5,
            "Z5<=-1": p.mkt5_z <= -1.0}
strong = {"res15前10%": p.res15_pct >= 90, "res15前20%": p.res15_pct >= 80,
          "逆勢收紅r15>=0": p.r15 >= 0, "逆勢收紅且res前20%": (p.r15 >= 0) & (p.res15_pct >= 80)}
bigpos = {"無": pd.Series(True, index=p.index), "大戶day前30%": p.big_day_pct >= 70,
          "大戶day前15%": p.big_day_pct >= 85, "大戶30m>0": p.big_30m > 0}
for (dn, dm), (sn, sm), (bn, bm) in itertools.product(
        mkt_down.items(), strong.items(), bigpos.items()):
    ev(dm & sm & bm, f"A|{dn}|{sn}|大戶:{bn}")

print("\n--- A 的對照組：同樣市場下跌,但它跟著跌（res15 後 20%）---")
for dn, dm in mkt_down.items():
    ev(dm & (p.res15_pct <= 20), f"A對照|{dn}|res15後20%|大戶:無")
    ev(dm & (p.res15_pct <= 20) & (p.big_day_pct >= 70), f"A對照|{dn}|res15後20%|大戶day前30%")

print("\n--- A 的安慰劑：市場沒在跌時的同一個相對強度條件 ---")
for sn, sm in strong.items():
    ev((p.mkt15_z >= 1.0) & sm, f"A安慰劑|市場上漲Z15>=1|{sn}|大戶:無")
    ev((p.mkt15_z.abs() <= 0.3) & sm, f"A安慰劑|市場平盤|{sn}|大戶:無")

print("\n================ 假說 B：下跌時量縮 vs 爆量 ================")
down = {"自身30m<0": p.r30 < 0, "自身15m<0": p.r15 < 0,
        "距高<=-1%且30m<0": (p["距當日高%"] <= -1) & (p.r30 < 0)}
vol = {"量縮amt5z<=-0.5": p.amt5_z <= -0.5, "量縮amt5z<=-1": p.amt5_z <= -1.0,
       "爆量amt5z>=1": p.amt5_z >= 1.0, "爆量amt5z>=1.5": p.amt5_z >= 1.5,
       "量縮amt30z<=-0.5": p.amt30_z <= -0.5, "爆量amt30z>=1": p.amt30_z >= 1.0}
for (dn, dm), (vn, vm) in itertools.product(down.items(), vol.items()):
    ev(dm & vm, f"B|{dn}|{vn}")
print("\n--- B 的交互作用對照：上漲時的同一個量門檻（若「下跌時」不重要,兩邊會一樣）---")
up = {"自身30m>0": p.r30 > 0}
for (dn, dm), (vn, vm) in itertools.product(up.items(), vol.items()):
    ev(dm & vm, f"B交互|{dn}|{vn}")
print("\n--- B 的無條件對照（另一 agent 的結論） ---")
for vn, vm in vol.items():
    ev(vm, f"B無條件|{vn}")
print("\n--- B 加洗盤版：量縮下跌 + 大戶沒跑 + 日均價頂住（日線均線的弱代理） ---")
cheap = (p.cross_vwap == 1)
for vn, vm in [("量縮amt5z<=-0.5", p.amt5_z <= -0.5), ("量縮amt5z<=-1", p.amt5_z <= -1.0)]:
    ev((p.r30 < 0) & vm & (p.big_30m > 0), f"B洗盤|自身30m<0|{vn}|大戶30m>0")
    ev((p.r30 < 0) & vm & (p.big_day_pct >= 70), f"B洗盤|自身30m<0|{vn}|大戶day前30%")
    ev((p.r30 < 0) & vm & (p.recover == 1), f"B洗盤|自身30m<0|{vn}|recover=1")

print("\n================ 對照：故事九 ================")
s9 = (p.big_30m > 0) & (p.ret_30m < 0) & (p["距當日高%"] <= -1) & (p.er >= 0.143) & (p.recover == 0)
ev(s9, "故事九")

df = pd.DataFrame(rows)
df.to_csv(OUT / "story1b_results.csv", index=False)
print(f"\nwrote {OUT/'story1b_results.csv'}  rows={len(df)}")
d = df[df.col == "x_fwd_close"]
print(f"\n掃了 {len(d)} 組 (fwd_close 口徑): 去重後超額為正 {int((d.excess>0).sum())} 組, "
      f"過 p99 {int(d.pass_p99.fillna(False).sum())} 組, "
      f"過 p99 且剔最大貢獻檔仍為正 {int((d.pass_p99.fillna(False)&(d.ex_top_sid>0)).sum())} 組")
