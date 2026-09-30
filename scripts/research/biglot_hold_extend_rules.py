#!/usr/bin/env python3
"""「什麼情況可以抱久一點」— 階段 2:持倉期間的條件式延長規則(2026-09-30)。

階段 1(biglot_hold_extend_scan.py)證明:用**進場當下**的條件找不到持有期斜率為正的子群。
本檔改問:持倉**過程中**觀察到什麼,才值得推翻現行出場(分數≤0 ∨ 壞標籤 ∨ 60 分)?

規則族(全部只用當下可觀測資訊,無前視):
  BASE  現行
  E1(x) 分數≤0 時,若當下浮盈 >= x bps 則不出(讓賺錢的跑),直到浮盈跌破 x 或 60 分
  E2(x) 同 E1,但壞標籤也一併延後
  E3(x) 第 1 桶(5 分)浮盈 >= x → 整筆改用寬鬆門檻(分數≤−5 才出)
  E4    分數≤0 但分數仍在回升(s[k] > s[k−1])則再等一桶
  E5(x) 浮盈 >= x 後改成「回吐一半才出」(利潤保護,非移動停利)
損益一律「毛 − 22 bps」(現股單邊做多實際口徑),出場價=桶邊界後首筆買一(悲觀)。

用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_hold_extend_rules.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from biglot_score_v23_fit import cl_t
from biglot_hold_lab import BAD

SRC = Path.home() / "goldenstocks-data/scratch/biglot_exit_paths_2026-09-30.parquet"
COST = 22.0
H = 12


def load():
    d = pd.read_parquet(SRC)
    d = d[d["filled"].values & np.isfinite(d["entry"].values) & (d["entry"].values > 0)].reset_index(drop=True)
    ff = lambda m: pd.DataFrame(m).ffill(axis=1).bfill(axis=1).values          # noqa: E731
    B = ff(np.column_stack([d[f"b{k}"].values for k in range(H + 1)]))
    C = ff(np.column_stack([d[f"c{k}"].values for k in range(H + 1)]))
    S = np.column_stack([d[f"s{k}"].values for k in range(H + 1)])
    bad = np.logical_or.reduce([np.column_stack([d[f"x{m}_{k}"].values for k in range(H + 1)]).astype(bool)
                                for m in range(len(BAD))])
    R = (C / d["entry"].values[:, None] - 1) * 1e4      # 浮盈用桶收價(決策當下看得到的)
    X = (B / d["entry"].values[:, None] - 1) * 1e4      # 出場報酬用買一(悲觀)
    return d, R, X, S, bad


def run(S, bad, R, rule, **kw):
    """回傳每列出場桶 k(1..H)。"""
    n = S.shape[0]; out = np.full(n, H); done = np.zeros(n, bool)
    x = kw.get("x", 0.0)
    armed = np.zeros(n, bool)          # E3/E5 用
    peak = np.zeros(n)
    for k in range(1, H + 1):
        low = S[:, k] <= 0
        hit = low | bad[:, k]
        if rule == "BASE":
            pass
        elif rule == "E1":
            hit = (low & (R[:, k] < x)) | bad[:, k]
        elif rule == "E2":
            hit = (low | bad[:, k]) & (R[:, k] < x)
        elif rule == "E3":
            if k == 1:
                armed = R[:, 1] >= x
            hit = np.where(armed, (S[:, k] <= -5) | bad[:, k], hit)
        elif rule == "E4":
            rising = (k >= 2) & (S[:, k] > S[:, k - 1])
            hit = (low & ~rising) | bad[:, k]
        elif rule == "E5":
            peak = np.maximum(peak, R[:, k])
            armed = armed | (R[:, k] >= x)
            hit = np.where(armed, (R[:, k] <= peak / 2) | bad[:, k], hit)
        h = hit & ~done
        out[h] = k; done |= h
    return out


def rep(lab, v, d, isb, extra=""):
    o = f"  {lab:<26}"
    for sl, m in (("IS", isb), ("OOS", ~isb)):
        mu, t = cl_t(v[m], d["date"].values[m])
        o += f" | {sl} {mu:+7.2f}(t{t:+5.2f}) 勝{np.mean(v[m] > 0)*100:3.0f}%"
    return o + extra


def main():
    d, R, X, S, bad = load()
    isb = d["is"].values.astype(bool)
    print(f"樣本 n={len(d)} IS {isb.sum()} / OOS {(~isb).sum()}  損益=毛−{COST:.0f}bps,出場價=買一\n")
    base_k = run(S, bad, R, "BASE")
    base_v = np.take_along_axis(X, base_k[:, None], 1).ravel() - COST
    print("==== 基準 ====")
    print(rep("BASE 現行", base_v, d, isb, f"  均持{base_k.mean()*5:.1f}分"))

    print("\n==== E1:分數≤0 但浮盈 >= x 就不出(壞標籤照出) ====")
    for x in (0, 10, 20, 30, 50):
        kx = run(S, bad, R, "E1", x=x)
        v = np.take_along_axis(X, kx[:, None], 1).ravel() - COST
        print(rep(f"E1 x={x:+3d}bps", v, d, isb, f"  均持{kx.mean()*5:.1f}分 Δ持{(kx-base_k).mean()*5:+.1f}分"))

    print("\n==== E2:分數≤0 或壞標籤,都要浮盈 < x 才出 ====")
    for x in (0, 10, 20, 30, 50):
        kx = run(S, bad, R, "E2", x=x)
        v = np.take_along_axis(X, kx[:, None], 1).ravel() - COST
        print(rep(f"E2 x={x:+3d}bps", v, d, isb, f"  均持{kx.mean()*5:.1f}分"))

    print("\n==== E3:第 1 桶浮盈 >= x → 整筆改寬鬆門檻(分數≤−5 才出) ====")
    for x in (0, 20, 50, 100):
        kx = run(S, bad, R, "E3", x=x)
        v = np.take_along_axis(X, kx[:, None], 1).ravel() - COST
        n_arm = int((R[:, 1] >= x).sum())
        print(rep(f"E3 x={x:+4d}bps", v, d, isb, f"  觸發{n_arm}筆 均持{kx.mean()*5:.1f}分"))

    print("\n==== E4:分數≤0 但仍在回升(s[k]>s[k−1])再等一桶 ====")
    kx = run(S, bad, R, "E4")
    v = np.take_along_axis(X, kx[:, None], 1).ravel() - COST
    print(rep("E4", v, d, isb, f"  均持{kx.mean()*5:.1f}分"))

    print("\n==== E5:浮盈達 x 後改「回吐一半才出」 ====")
    for x in (20, 50, 100):
        kx = run(S, bad, R, "E5", x=x)
        v = np.take_along_axis(X, kx[:, None], 1).ravel() - COST
        print(rep(f"E5 x={x:+4d}bps", v, d, isb, f"  均持{kx.mean()*5:.1f}分"))

    print("\n==== 配對檢定:每個變體 vs BASE(同一筆相減,日聚類) ====")
    cands = [("E1 x=0", run(S, bad, R, "E1", x=0)), ("E1 x=20", run(S, bad, R, "E1", x=20)),
             ("E1 x=50", run(S, bad, R, "E1", x=50)), ("E2 x=20", run(S, bad, R, "E2", x=20)),
             ("E3 x=50", run(S, bad, R, "E3", x=50)), ("E4", run(S, bad, R, "E4")),
             ("E5 x=50", run(S, bad, R, "E5", x=50))]
    for lab, kx in cands:
        v = np.take_along_axis(X, kx[:, None], 1).ravel() - COST
        dd = v - base_v
        chg = (kx != base_k)
        o = f"  {lab:<10} 改變{chg.sum():4d}筆"
        for sl, m in (("IS", isb), ("OOS", ~isb)):
            mu, t = cl_t(dd[m], d["date"].values[m])
            o += f" | {sl} Δ{mu:+7.2f}(t{t:+5.2f})"
        if chg.sum() >= 20:
            mu2, t2 = cl_t(dd[chg], d["date"].values[chg])
            o += f" | 只看改變的 {mu2:+7.2f}(t{t2:+5.2f})"
        print(o)
    return 0


if __name__ == "__main__":
    sys.exit(main())
