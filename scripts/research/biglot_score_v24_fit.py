#!/usr/bin/env python3
"""盤中分 V2.4 = V2.3 + 分級蓄勢(壓縮程度 × 大戶方向/劑量),統一 60 分時距(jack 2026-09-24)。

同 V2.3 流程:pit100×127 日 5 分桶面板,只用 IS 做區間指標聯合 OLS(按日聚類),權重 0.7×係數、|t|<2 歸零,
OOS 不參與。蓄勢劑量表(v23_fit_2026-09-24.txt)顯示:符號由壓縮正負決定、程度在壓縮軸單調、大戶軸 5~40% 最好、
≥40% 反而差 → 新項目:
  蓄勢深   cmp<−0.5 ∧ 大戶30分 5~40%       蓄勢   −0.5≤cmp<0 ∧ 大戶 5~40%
  蓄勢鉅   cmp<0 ∧ 大戶 ≥40%(鉅額/單筆,獨立估)
  彈開     0.5≤cmp<1.5(不分大戶)           彈開高  cmp≥1.5(不分大戶)   ← 壓縮的負側,讓 OLS 與過熱分帳
  倒貨     0<cmp≤0.5 ∧ 大戶 ≤−10%          倒貨高  cmp>0.5 ∧ 大戶 ≤−10%
  大戶賣∧壓縮<−0.5(投降候選;劑量表 IS 正 OOS 負,預期歸零)
用法:PYTHONPATH=src .venv/bin/python scripts/research/biglot_score_v24_fit.py
"""
from __future__ import annotations
import numpy as np
from biglot_score_v23_fit import build, items as items23, ols_cluster, evaluate, V23_FINAL, SHRINK, TMIN


def items24(d):
    it = items23(d)
    it.pop("蓄勢"); it.pop("倒貨")
    c, b = d["cmp"], d["bigsh30"]
    it.update({
        "蓄勢深":   (((c < -0.5) & (b >= 5) & (b < 40)), 0),
        "蓄勢":     (((c >= -0.5) & (c < 0) & (b >= 5) & (b < 40)), 0),
        "蓄勢鉅":   (((c < 0) & (b >= 40)), 0),
        "彈開":     (((c >= 0.5) & (c < 1.5)), 0),
        "彈開高":   ((c >= 1.5), 0),
        "倒貨":     (((c > 0) & (c <= 0.5) & (b <= -10)), 0),
        "倒貨高":   (((c > 0.5) & (b <= -10)), 0),
        "大戶賣∧壓": (((c < -0.5) & (b <= -10)), 0),
    })
    return it


def main():
    d = build(); it = items24(d); names = list(it)
    X = np.column_stack([it[k][0].fillna(False).astype(float).values for k in names])
    y, dates = d["ex60"].values, d["date"].values
    is_ = (d["date"] <= "2026-06-30").values
    # 只擬合 V2.3 已存活項 + 新項(其餘固定 0,避免重新給歸零項機會)
    keep = [i for i, k in enumerate(names) if V23_FINAL.get(k) or k in ("蓄勢深", "蓄勢", "蓄勢鉅", "彈開", "彈開高", "倒貨", "倒貨高", "大戶賣∧壓")]
    b, se = ols_cluster(X[is_][:, keep], y[is_], dates[is_])
    W24 = dict(V23_FINAL)
    print("==== V2.4 IS 聯合 OLS(V2.3 存活項 + 分級蓄勢) ====")
    print(f"{'項':12s}{'係數':>8s}{'t':>6s}{'→權重':>8s}{'V2.3':>7s}")
    for j, i in enumerate(keep):
        k = names[i]; w = round(SHRINK * b[j] * 2) / 2 if abs(b[j] / se[j]) >= TMIN else 0.0
        if k in ("蓄勢深", "蓄勢", "蓄勢鉅", "彈開", "彈開高", "倒貨", "倒貨高", "大戶賣∧壓"):
            W24[k] = w
        print(f"{k:12s}{b[j]:+8.1f}{b[j]/se[j]:+6.1f}{w:+8.1f}{V23_FINAL.get(k, 0):+7.1f}")
    # 急跌≤−600 維持上限 40;過熱400-600/逆弱50-100 維持單調補格
    W24["急跌≤−600"] = 40.0
    print("\nV24_W =", {k: v for k, v in W24.items() if v})
    s23 = np.clip(X @ np.array([V23_FINAL.get(k, 0.0) for k in names]), -40, 40)
    s24 = np.clip(X @ np.array([W24.get(k, 0.0) for k in names]), -40, 40)
    for lab, m in (("IS", is_), ("OOS", ~is_)):
        print(f"\n======== {lab} ========")
        print(evaluate(d[m], s23[m], f"V2.3 {lab}"))
        print(evaluate(d[m], s24[m], f"V2.4 {lab}(分級蓄勢,60分)"))


if __name__ == "__main__":
    main()
