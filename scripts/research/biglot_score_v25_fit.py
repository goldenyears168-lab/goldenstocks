#!/usr/bin/env python3
"""盤中分 V2.5 = V2.4 + 「竭盡 × 狀態」組合項(jack 2026-09-24:竭盡不能單獨用,要搭配)。

biglot_exhaust_combo_test.py 的結論(IS/OOS 同號、聯合 |t|≥2):
  · 「賣盤竭盡(30秒主動賣≤40%)」單獨是**負**的(−5.5/−5.1):桶收時賣壓已退=反彈已在桶內發生,你是晚到的。
  · 反轉的指紋是「賣壓還在」:急跌∧主動賣≥80% +6.2/+4.5;急跌∧末30秒還在跌 +6.8/+6.6。
  · 竭盡∧末30秒仍在跌 = 沒人主動賣價格卻掉 = 買盤抽走的真空 → +10/+11(唯一竭盡為正的格)。
  · 竭盡∧已止跌 −7/−10、竭盡∧散戶接跌 −9/−7、竭盡∧大戶賣 −8/−5、竭盡∧量塌 −9/−6。
本檔把這些收成互斥狀態格,與 V2.4 項一起做 IS 聯合 OLS(0.7 收縮、t<2 歸零),OOS 不參與。
需要 pit100_bucket5_exhaust_2026-03_08.csv(biglot_exhaust_tick_features.py)。
用法:PYTHONPATH=src .venv/bin/python scripts/research/biglot_score_v25_fit.py
"""
from __future__ import annotations
import numpy as np, pandas as pd
from pathlib import Path
from biglot_score_v23_fit import build, ols_cluster, evaluate, SHRINK, TMIN
from biglot_score_v24_fit import items24

EX = Path.home() / "goldenstocks-data/scratch/biglot_panels/pit100_bucket5_exhaust_2026-03_08.csv"
V24 = {"散戶虛拉": -4.5, "勿追5m": -3.0, "散戶接跌": 2.5, "過熱200-300": -5.0, "過熱300-400": -8.5, "過熱400-600": -8.5,
       "急跌≤−600": 40.0, "逆弱20-50": 1.5, "逆弱50-100": 1.5, "逆弱≤−100": 5.5, "純機構": 9.0, "蓄勢": 4.5, "蓄勢深": 5.0, "倒貨": -2.5}
NEW = ["急跌·賣壓未竭", "急跌·真空", "急跌·竭盡已止跌", "急跌·竭盡散戶接", "急跌·大戶接∧未竭", "急跌·末30秒續跌",
       "急拉·買壓未竭", "急拉·買壓竭盡", "急拉·末30秒續漲", "急拉·散戶追∧未竭"]


def items25(d):
    it = items24(d)
    w5, ss, r30s, b5n, rb5 = d["w5"], d["sell30s"], d["r30s"], d["b5n"], d["rbuy5"]
    dip, rip = w5 <= -20, w5 >= 20
    exh, notexh = ss <= 0.40, ss >= 0.60
    it.update({
        "急跌·賣壓未竭":   (dip & notexh, 0),
        "急跌·真空":       (dip & exh & (r30s <= -10), 0),
        "急跌·竭盡已止跌": (dip & exh & (r30s > 0), 0),
        "急跌·竭盡散戶接": (dip & exh & (rb5 >= 5), 0),
        "急跌·大戶接∧未竭": (dip & (b5n > 5) & ~exh, 0),
        "急跌·末30秒續跌": (dip & (r30s <= -10) & ~exh, 0),
        "急拉·買壓未竭":   (rip & exh, 0),            # 30秒主動賣≤40% = 買方仍主動
        "急拉·買壓竭盡":   (rip & notexh & (r30s < 0), 0),
        "急拉·末30秒續漲": (rip & (r30s >= 10), 0),
        "急拉·散戶追∧未竭": (rip & (rb5 >= 5) & exh, 0),
    })
    return it


def main():
    d = build().merge(pd.read_csv(EX, dtype={"sid": str}), on=["sid", "date", "bucket"], how="left")
    it = items25(d); names = list(it)
    X = np.column_stack([it[k][0].fillna(False).astype(float).values for k in names])
    y, dates = d["ex60"].values, d["date"].values
    is_ = (d["date"] <= "2026-06-30").values
    keep = [i for i, k in enumerate(names) if V24.get(k) or k in NEW]
    b, se = ols_cluster(X[is_][:, keep], y[is_], dates[is_])
    W25 = dict(V24)
    print("==== V2.5 IS 聯合 OLS(V2.4 項 + 竭盡狀態格)====")
    print(f"{'項':14s}{'n(IS)':>7s}{'係數':>8s}{'t':>6s}{'→權重':>8s}{'V2.4':>7s}")
    for j, i in enumerate(keep):
        k = names[i]; w = round(SHRINK * b[j] * 2) / 2 if abs(b[j] / se[j]) >= TMIN else 0.0
        if k in NEW or k == "散戶接跌":
            W25[k] = w
        print(f"{k:14s}{int(X[is_][:, i].sum()):7d}{b[j]:+8.1f}{b[j]/se[j]:+6.1f}{w:+8.1f}{V24.get(k, 0):+7.1f}")
    W25["急跌≤−600"] = 40.0
    print("\nV25_W =", {k: v for k, v in W25.items() if v})
    s24 = np.clip(X @ np.array([V24.get(k, 0.0) for k in names]), -40, 40)
    s25 = np.clip(X @ np.array([W25.get(k, 0.0) for k in names]), -40, 40)
    for lab, m in (("IS", is_), ("OOS", ~is_)):
        print(f"\n======== {lab} ========")
        print(evaluate(d[m], s24[m], f"V2.4 {lab}"))
        print(evaluate(d[m], s25[m], f"V2.5 {lab}(竭盡狀態格)"))


if __name__ == "__main__":
    main()
