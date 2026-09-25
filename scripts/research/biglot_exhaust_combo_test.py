#!/usr/bin/env python3
"""竭盡候選 × 其他因子 的組合檢定(jack 2026-09-24:竭盡不能單獨用,要找搭配)。

資料:pit100 5 分桶面板 + biglot_exhaust_tick_features.py 的桶收竭盡特徵。y = 未來 60 分對宇宙等權超額(bps)。
賣盤竭盡 E_sell = 近5分 ≤−0.2% ∧ 桶收前30秒主動賣占比 ≤40%(儀表板定義;歷史面板無五檔,「買深≥3分鐘量」那半邊測不到)
買盤竭盡 E_buy  = 近5分 ≥+0.2% ∧ 30秒主動買 ≥60%
報告:
  A. 單獨(對照)與 30 秒主動賣占比的劑量(只在急跌 5 分內)
  B. E × 其他因子 的 2×2:各格邊際 + 聯合(控 V2.4 全部項)增量,IS/OOS
  C. 通過門檻(IS/OOS 聯合 |t|≥2 同號)的組合 → 建議權重 0.7×IS 係數
用法:PYTHONPATH=src .venv/bin/python scripts/research/biglot_exhaust_combo_test.py
"""
from __future__ import annotations
import numpy as np, pandas as pd
from pathlib import Path
from biglot_score_v23_fit import build, ols_cluster, cl_t, SHRINK
from biglot_score_v24_fit import items24

EX = Path.home() / "goldenstocks-data/scratch/biglot_panels/pit100_bucket5_exhaust_2026-03_08.csv"
V24 = {"散戶虛拉": -4.5, "勿追5m": -3.0, "散戶接跌": 2.5, "過熱200-300": -5.0, "過熱300-400": -8.5, "過熱400-600": -8.5,
       "急跌≤−600": 40.0, "逆弱20-50": 1.5, "逆弱50-100": 1.5, "逆弱≤−100": 5.5, "純機構": 9.0, "蓄勢": 4.5, "蓄勢深": 5.0, "倒貨": -2.5}


def main():
    d = build()
    ex = pd.read_csv(EX, dtype={"sid": str})
    d = d.merge(ex, on=["sid", "date", "bucket"], how="left")
    print(f"面板 {len(d)} 桶,竭盡特徵覆蓋 {d['sell30s'].notna().mean()*100:.1f}%;px 對齊誤差中位 {((d['px_end']/d['last_px']-1).abs()*1e4).median():.2f} bps")
    it = items24(d); names = list(it)
    X = np.column_stack([it[k][0].fillna(False).astype(float).values for k in names])
    Xc = X[:, [i for i, k in enumerate(names) if V24.get(k)]]           # 控制項
    y, dates = d["ex60"].values, d["date"].values
    is_ = (d["date"] <= "2026-06-30").values
    w5, r30, ss, act = d["w5"], d["r30"], d["sell30s"], d["act30s"]
    dip = w5 <= -20; rip = w5 >= 20
    E_sell = dip & (ss <= 0.40); E_buy = rip & (ss >= 0.60)

    def test(mask, lab, out):
        mv = mask.fillna(False).values.astype(float); row = {"格": lab}
        for sl, m in (("IS", is_), ("OOS", ~is_)):
            n = int(mv[m].sum())
            if n < 40:
                row[f"{sl} n"] = n; row[f"{sl} 邊際"] = row[f"{sl} 聯合"] = ""; continue
            mu, t = cl_t(y[m & (mv > 0)], dates[m & (mv > 0)])
            b, se = ols_cluster(np.column_stack([Xc[m], mv[m]]), y[m], dates[m])
            row[f"{sl} n"] = n; row[f"{sl} 邊際"] = f"{mu:+.1f}({t:+.1f})"; row[f"{sl} 聯合"] = f"{b[-1]:+.1f}({b[-1]/se[-1]:+.1f})"
            row[f"_{sl}"] = (b[-1], b[-1] / se[-1])
        out.append(row)

    print("\n==== A. 單獨 / 30 秒主動賣占比劑量(近5分 ≤−0.2% 內)====")
    A = []
    test(E_sell, "賣盤竭盡(≤40%)", A); test(E_buy, "買盤竭盡(≥60%)", A)
    for lo, hi in ((0, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .8), (.8, 1.01)):
        test(dip & (ss >= lo) & (ss < hi), f"急跌∧主動賣 {lo:.0%}~{hi:.0%}", A)
    for lo, hi in ((0, .5), (.5, 1), (1, 2), (2, 99)):
        test(dip & (act >= lo) & (act < hi), f"急跌∧末30秒量能 {lo}~{hi}x", A)
    print(pd.DataFrame(A).drop(columns=[c for c in ("_IS", "_OOS")], errors="ignore").to_string(index=False))

    print("\n==== B. 賣盤竭盡 × 其他因子(格 = 兩者同時成立)====")
    B = []
    c, b30, b5n, rb5 = d["cmp"], d["bigsh30"], d["b5n"], d["rbuy5"]
    combos = {
        "×急跌≤−200": r30 <= -200, "×急跌≤−300": r30 <= -300, "×急跌≤−600": r30 <= -600,
        "×非急跌(r30>−200)": r30 > -200,
        "×散戶接跌(散買≥5%)": rb5 >= 5, "×散戶不接(<5%)": rb5 < 5,
        "×大戶接(b5n>5)": b5n > 5, "×大戶賣(b5n<−5)": b5n < -5, "×都不接": (b5n <= 5) & (rb5 < 5),
        "×蓄勢(壓縮<0∧大戶5~40)": (c < 0) & (b30 >= 5) & (b30 < 40), "×已彈開(壓縮>0.5)": c > 0.5,
        "×逆弱(市場≥5∧r30≤−20)": (d["mkt30"] >= 5) & (r30 <= -20), "×順跌(市場≤−5)": d["mkt30"] <= -5,
        "×末30秒量塌(act<0.5)": act < 0.5, "×末30秒量增(act≥1)": act >= 1,
        "×末30秒還在跌(r30s≤−10)": d["r30s"] <= -10, "×末30秒已止跌(r30s>0)": d["r30s"] > 0,
        "×09:30–10:00": d["bucket"] < "10:00", "×10:00–12:00": (d["bucket"] >= "10:00") & (d["bucket"] < "12:00"), "×12:00後": d["bucket"] >= "12:00",
    }
    for k, m in combos.items():
        test(E_sell & m, "賣竭盡" + k, B)
        test(dip & ~(ss <= 0.40) & m, "急跌無竭盡" + k, B)      # 對照:同條件但主動賣>40%
    dfB = pd.DataFrame(B)
    print(dfB.drop(columns=[c for c in ("_IS", "_OOS")], errors="ignore").to_string(index=False))

    print("\n==== C. 通過(IS/OOS 聯合 |t|≥2 且同號)→ 建議權重(0.7×IS 係數)====")
    ok = []
    for r in A + B:
        i, o = r.get("_IS"), r.get("_OOS")
        if i and o and abs(i[1]) >= 2 and abs(o[1]) >= 2 and np.sign(i[0]) == np.sign(o[0]):
            ok.append((r["格"], round(SHRINK * i[0] * 2) / 2, i, o))
    if not ok: print("  無")
    for k, w, i, o in ok:
        print(f"  {k:32s} IS {i[0]:+.1f}(t{i[1]:+.1f})  OOS {o[0]:+.1f}(t{o[1]:+.1f})  → {w:+.1f}")
    print("\n==== D. 買盤竭盡 × 過熱/散戶虛拉(空方對稱)====")
    Dd = []
    for k, m in {"×過熱≥200": r30 >= 200, "×過熱≥300": r30 >= 300, "×散戶虛拉(散買≥5)": rb5 >= 5, "×大戶賣(b5n<−5)": b5n < -5,
                 "×已彈開(壓縮>0.5)": c > 0.5, "×末30秒量塌": act < 0.5, "×末30秒還在漲(r30s≥10)": d["r30s"] >= 10}.items():
        test(E_buy & m, "買竭盡" + k, Dd); test(rip & ~(ss >= 0.60) & m, "急拉無竭盡" + k, Dd)
    print(pd.DataFrame(Dd).drop(columns=[c for c in ("_IS", "_OOS")], errors="ignore").to_string(index=False))


if __name__ == "__main__":
    main()
