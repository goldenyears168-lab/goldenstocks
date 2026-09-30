#!/usr/bin/env python3
"""出場規則研究 — 階段 2:規則重放(2026-09-30)。

進場固定(掛買一 30 秒,可成交價),只換出場規則。成本 22 bps,出場價兩口徑:
  b[k]=桶邊界後首筆買一成交(悲觀,對應「掛賣一打不到就打買一」)、c[k]=桶收(樂觀)。
口徑「每成交筆」與「每訊號(未成交=0)」都報;IS/OOS 分割 + 日聚類 t。

用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_exit_rules_eval.py
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
    S = np.column_stack([d[f"s{k}"].values for k in range(H + 1)])
    # 桶內無成交 → 價格路徑 forward-fill(出場時用最近可得價,符合實務)
    C = pd.DataFrame(np.column_stack([d[f"c{k}"].values for k in range(H + 1)])).ffill(axis=1).bfill(axis=1).values
    B = pd.DataFrame(np.column_stack([d[f"b{k}"].values for k in range(H + 1)])).ffill(axis=1).bfill(axis=1).values
    U = np.column_stack([d[f"u{k}"].values for k in range(H + 1)])
    X = {m: np.column_stack([d[f"x{m}_{k}"].values for k in range(H + 1)]).astype(bool) for m in range(len(BAD))}
    return d, S, C, B, U, X


def ret(d, kx, P, U):
    """kx:每列出場桶 index。回傳淨超額 bps。"""
    r = np.take_along_axis(P, kx[:, None], 1).ravel()
    u = np.take_along_axis(U, kx[:, None], 1).ravel()
    return (r / d["entry"].values - 1) * 1e4 - COST - u


def rep(lab, v, d, isb, width=34):
    o = f"{lab:<{width}}"
    for sl, m in (("IS", isb), ("OOS", ~isb)):
        mu, t = cl_t(v[m], d["date"].values[m])
        o += f" | {sl} {mu:+7.2f}(t{t:+5.2f}) 勝{np.mean(v[m] > 0) * 100:3.0f}%"
    return o


def exit_k(S, X, bad_mask, th=0.0, consec=1, min_hold=0, use_bad=True, cap=H):
    """回傳每列出場桶(1..cap)。分數 <= th 連 consec 桶 或 壞標籤 → 出;都沒有則 cap。"""
    n = S.shape[0]; out = np.full(n, cap)
    lowrun = np.zeros(n, dtype=int); done = np.zeros(n, dtype=bool)
    for k in range(1, cap + 1):
        lowrun = np.where(S[:, k] <= th, lowrun + 1, 0)
        hit = (lowrun >= consec)
        if use_bad:
            hit = hit | bad_mask[:, k]
        hit = hit & (k >= min_hold) & ~done
        out[hit] = k; done |= hit
    return out


def main():
    d, S, C, B, U, X = load()
    isb = d["is"].values.astype(bool)
    bad_all = np.logical_or.reduce([X[m] for m in range(len(BAD))])
    print(f"樣本 n={len(d)}(掛買一成交) IS {isb.sum()} / OOS {(~isb).sum()} 日 {d['date'].nunique()}")
    print(f"壞標籤子項: {list(BAD)}")
    print(f"成本 {COST} bps。以下均為每成交筆淨超額。\n")

    for P, plab in ((B, "出場價=買一(悲觀)"), (C, "出場價=桶收(樂觀)")):
        print(f"================ {plab} ================")
        print("---- A. 純時間出場曲線(不看分數/標籤,固定持有 k 桶) ----")
        for k in (1, 2, 3, 4, 6, 8, 10, 12):
            kx = np.full(len(d), k)
            print("  " + rep(f"固定持有 {k*5:3d} 分", ret(d, kx, P, U), d, isb))
        print("---- B. 現行規格拆解 ----")
        cur = exit_k(S, X, bad_all, 0.0, 1, 0, True)
        print("  " + rep("現行(分數≤0 或 壞標籤 或 60分)", ret(d, cur, P, U), d, isb))
        print(f"      平均持有 {cur.mean()*5:.1f} 分,中位 {np.median(cur)*5:.0f} 分")
        only_s = exit_k(S, X, bad_all, 0.0, 1, 0, False)
        print("  " + rep("只用分數≤0(移除壞標籤)", ret(d, only_s, P, U), d, isb))
        print(f"      平均持有 {only_s.mean()*5:.1f} 分")
        onlyb = np.full(len(d), H)
        for k in range(1, H + 1):
            hit = bad_all[:, k] & (onlyb == H)
            onlyb[hit] = k
        print("  " + rep("只用壞標籤(移除分數)", ret(d, onlyb, P, U), d, isb))
        print("---- C. 壞標籤 leave-one-out(移除該項,其餘照舊) ----")
        for m, nm in enumerate(BAD):
            others = np.logical_or.reduce([X[j] for j in range(len(BAD)) if j != m])
            kx = exit_k(S, X, others, 0.0, 1, 0, True)
            n_trig = int((np.take_along_axis(X[m], cur[:, None], 1).ravel() & (cur < H)).sum())
            print("  " + rep(f"移除「{nm}」(原觸發{n_trig}筆)", ret(d, kx, P, U), d, isb))
        print("---- D. 最短持有期(現行規則 + 前 m 桶不准出) ----")
        for mh in (0, 1, 2, 3, 4, 6):
            kx = exit_k(S, X, bad_all, 0.0, 1, mh, True)
            print("  " + rep(f"最短持有 {mh*5:2d} 分", ret(d, kx, P, U), d, isb) + f"  均持{kx.mean()*5:.0f}分")
        print("---- E. 分數出場門檻 × 連續確認(不含壞標籤) ----")
        for th in (5.0, 0.0, -5.0, -10.0):
            for cc in (1, 2):
                kx = exit_k(S, X, bad_all, th, cc, 0, False)
                print("  " + rep(f"分數≤{th:+.0f} 連{cc}桶", ret(d, kx, P, U), d, isb) + f"  均持{kx.mean()*5:.0f}分")
        print()

    print("================ F. 每訊號口徑(未成交=0,出場價=買一) ================")
    full = pd.read_parquet(SRC)
    full = full[np.isfinite(full["entry"].values) & (full["entry"].values > 0)].reset_index(drop=True)
    fisb = full["is"].values.astype(bool); ok = full["filled"].values
    Sf = np.column_stack([full[f"s{k}"].values for k in range(H + 1)])
    Bf = pd.DataFrame(np.column_stack([full[f"b{k}"].values for k in range(H + 1)])).ffill(axis=1).bfill(axis=1).values
    Uf = np.column_stack([full[f"u{k}"].values for k in range(H + 1)])
    Xf = {m: np.column_stack([full[f"x{m}_{k}"].values for k in range(H + 1)]).astype(bool) for m in range(len(BAD))}
    badf = np.logical_or.reduce([Xf[m] for m in range(len(BAD))])
    def per_sig(kx):
        v = np.zeros(len(full))
        r = np.take_along_axis(Bf, kx[:, None], 1).ravel(); u = np.take_along_axis(Uf, kx[:, None], 1).ravel()
        v[ok] = ((r / full["entry"].values - 1) * 1e4 - COST - u)[ok]
        return v
    print("  " + rep("現行規格", per_sig(exit_k(Sf, Xf, badf, 0.0, 1, 0, True)), full, fisb))
    for mh in (1, 2, 3):
        print("  " + rep(f"+最短持有 {mh*5} 分", per_sig(exit_k(Sf, Xf, badf, 0.0, 1, mh, True)), full, fisb))
    print("  " + rep("只用分數≤0", per_sig(exit_k(Sf, Xf, badf, 0.0, 1, 0, False)), full, fisb))
    for k in (3, 6, 12):
        print("  " + rep(f"純時間 {k*5} 分", per_sig(np.full(len(full), k)), full, fisb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
