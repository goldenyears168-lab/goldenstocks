#!/usr/bin/env python3
"""外盤比條件式進場研究 — 階段 2:門檻搜尋與回測(2026-09-30)。

三個策略,口徑一律「每個訊號」(未成交記 0,避免只看成交筆的選擇偏誤):
  S_A 現行:掛買一等 30 秒,成交才做
  S_B 對照:每個訊號都吃賣一(下一筆買方主動成交價)
  S_C(θ):外盤比 >= θ → 吃賣一;否則掛買一(未成交則不做)
  S_E(θ):掛買一,未成交且外盤比 >= θ → 30 秒後追吃(近似價=t+30 後第一筆)
成本 22 bps。出場桶與規格相同(分數≤0),出場價兩口徑:桶收 / 買一(悲觀)。
IS ≤2026-06-30 選門檻,OOS 07-01~ 驗;t 值用日聚類。附安慰劑(隨機同比例分派)。

用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_aggr_ratio_eval.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from biglot_score_v23_fit import cl_t

SRC = Path.home() / "goldenstocks-data/scratch/biglot_aggr_ratio_events_2026-09-30.csv"
COST = 22.0
RATIO = __import__("os").environ.get("AGGR_WIN", "out30")


def stat(vals, dates, label, width=30):
    vals = np.asarray(vals, dtype=float)
    if len(vals) < 20:
        return f"{label:<{width}} n={len(vals):4d} (n<20)"
    mu, t = cl_t(vals, np.asarray(dates))
    return f"{label:<{width}} n={len(vals):4d}  {mu:+7.2f} bps (t{t:+5.2f})  勝{np.mean(vals>0)*100:3.0f}%"


def legs(d, exit_col):
    """回傳每訊號的 A/B 淨超額(未成交=0)。"""
    ex_px = d[exit_col].values
    u = d["uret"].values
    # A:掛買一
    a = np.zeros(len(d)); ok_a = d["A_fill_opt"].values & np.isfinite(d["A_entry"].values)
    ra = (ex_px[ok_a] / d["A_entry"].values[ok_a] - 1) * 1e4 - COST - u[ok_a]
    a[ok_a] = ra
    # A 嚴格
    a_s = np.zeros(len(d)); ok_as = d["A_fill_strict"].values & np.isfinite(d["A_entry"].values)
    a_s[ok_as] = (ex_px[ok_as] / d["A_entry"].values[ok_as] - 1) * 1e4 - COST - u[ok_as]
    # B:吃賣一
    b = np.zeros(len(d)); ok_b = np.isfinite(d["B_entry"].values) & (d["B_entry"].values > 0)
    b[ok_b] = (ex_px[ok_b] / d["B_entry"].values[ok_b] - 1) * 1e4 - COST - u[ok_b]
    # E:未成交後追吃(近似)
    e = a.copy(); chase = (~ok_a) & np.isfinite(d["px_after_wait"].values) & (d["px_after_wait"].values > 0)
    e[chase] = (ex_px[chase] / d["px_after_wait"].values[chase] - 1) * 1e4 - COST - u[chase]
    return {"A": a, "A_strict": a_s, "B": b, "E": e, "ok_a": ok_a, "ok_b": ok_b, "chase": chase}


def main():
    d = pd.read_csv(SRC, dtype={"sid": str})
    d = d[np.isfinite(d["close0"]) & np.isfinite(d[RATIO])].reset_index(drop=True)
    isb = d["is"].values.astype(bool)
    print(f"樣本 n={len(d)}  IS {isb.sum()} / OOS {(~isb).sum()}  日數 {d['date'].nunique()}")
    print("\n==== 0. 描述 ====")
    print(f"外盤比(30s,PIT) 中位 {d[RATIO].median():.3f}  p25 {d[RATIO].quantile(.25):.3f}  p75 {d[RATIO].quantile(.75):.3f}")
    print(f"  >=0.4 {(d[RATIO]>=.4).mean()*100:.1f}%   >=0.5 {(d[RATIO]>=.5).mean()*100:.1f}%   >=0.6 {(d[RATIO]>=.6).mean()*100:.1f}%   >=0.7 {(d[RATIO]>=.7).mean()*100:.1f}%")
    sp = d["spread_bps"]
    print(f"價差代理(賣一/買一−1) 中位 {sp.median():.1f} bps  p25 {sp.quantile(.25):.1f}  p75 {sp.quantile(.75):.1f}  n={sp.notna().sum()}")
    print(f"A 路成交率:樂觀 {d['A_fill_opt'].mean()*100:.1f}%  嚴格 {d['A_fill_strict'].mean()*100:.1f}%")

    print("\n==== 1. 執行面:外盤比 → 掛買一 30 秒成交率 ====")
    d["q"] = pd.qcut(d[RATIO], 10, labels=False, duplicates="drop")
    g = d.groupby("q").agg(n=("sid", "size"), lo=(RATIO, "min"), hi=(RATIO, "max"),
                           fill_opt=("A_fill_opt", "mean"), fill_st=("A_fill_strict", "mean"),
                           run=("run_bps", "mean"), spread=("spread_bps", "median"))
    for q, r in g.iterrows():
        print(f"  D{int(q)+1:2d} 外盤比[{r.lo:.2f},{r.hi:.2f}] n={int(r.n):4d}  樂觀成交 {r.fill_opt*100:4.0f}%  嚴格 {r.fill_st*100:4.0f}%  價差中位 {r.spread:5.1f}  30s後跑 {r.run:+6.1f}")
    hi = d[RATIO] >= 0.6
    print(f"  外盤比>=0.6 成交率 {d.loc[hi,'A_fill_opt'].mean()*100:.1f}% vs <0.6 {d.loc[~hi,'A_fill_opt'].mean()*100:.1f}%")

    for exit_col, elab in (("exit_bid", "出場=買一(悲觀)"), ("exit_close", "出場=桶收(樂觀)")):
        print(f"\n==== 2. 經濟面 — {elab},成本 {COST:.0f} bps,口徑=每訊號(未成交記0) ====")
        Lg = legs(d, exit_col)
        for sl, m in (("IS ", isb), ("OOS", ~isb)):
            dd = d["date"].values[m]
            print(" " + stat(Lg["A"][m], dd, f"{sl} S_A 掛買一(樂觀成交)"))
            print(" " + stat(Lg["A_strict"][m], dd, f"{sl} S_A 掛買一(嚴格成交)"))
            print(" " + stat(Lg["B"][m], dd, f"{sl} S_B 全部吃賣一"))
        print("  -- S_C(θ):外盤比>=θ 吃賣一,否則掛買一 --")
        rows = []
        for th in np.arange(0.0, 0.95, 0.05):
            sel = (d[RATIO].values >= th)
            c = np.where(sel, Lg["B"], Lg["A"])
            r = {"th": th, "share": sel.mean()}
            for sl, m in (("is", isb), ("oos", ~isb)):
                mu, t = cl_t(c[m], d["date"].values[m])
                r[f"{sl}_mu"], r[f"{sl}_t"] = mu, t
            rows.append(r)
        R = pd.DataFrame(rows)
        base_is, _ = cl_t(Lg["A"][isb], d["date"].values[isb])
        base_oos, _ = cl_t(Lg["A"][~isb], d["date"].values[~isb])
        for r in R.itertuples():
            mark = " <<" if r.is_mu == R["is_mu"].max() else ""
            print(f"   θ={r.th:.2f} 吃賣一占{r.share*100:4.0f}%  IS {r.is_mu:+7.2f}(t{r.is_t:+5.2f}) Δ{r.is_mu-base_is:+6.2f} | OOS {r.oos_mu:+7.2f}(t{r.oos_t:+5.2f}) Δ{r.oos_mu-base_oos:+6.2f}{mark}")
        print(f"   基準 S_A: IS {base_is:+.2f} / OOS {base_oos:+.2f}")
        print("  -- S_E(θ):掛買一未成交且外盤比>=θ → 30 秒後追吃 --")
        for th in (0.0, 0.3, 0.4, 0.5, 0.6, 0.7):
            sel = (d[RATIO].values >= th) & Lg["chase"]
            c = np.where(sel, Lg["E"], Lg["A"])
            o = []
            for sl, m in (("IS", isb), ("OOS", ~isb)):
                mu, t = cl_t(c[m], d["date"].values[m])
                o.append(f"{sl} {mu:+7.2f}(t{t:+5.2f})")
            print(f"   θ={th:.2f} 追吃{sel.sum():4d}筆  " + " | ".join(o))

    print("\n==== 3. 安慰劑:隨機分派同比例「吃賣一」,200 次(IS,出場=買一) ====")
    Lg = legs(d, "exit_bid")
    rng = np.random.default_rng(7)
    for th in (0.4, 0.5, 0.6):
        sel = d[RATIO].values >= th
        real = np.where(sel, Lg["B"], Lg["A"])
        mu_r, _ = cl_t(real[isb], d["date"].values[isb])
        sims = []
        for _ in range(200):
            f = rng.permutation(sel)
            c = np.where(f, Lg["B"], Lg["A"])
            sims.append(cl_t(c[isb], d["date"].values[isb])[0])
        sims = np.array(sims)
        print(f"  θ={th:.2f} 真實 {mu_r:+.2f} vs 隨機 {sims.mean():+.2f} [p5 {np.percentile(sims,5):+.2f}, p95 {np.percentile(sims,95):+.2f}]  百分位 {(sims<mu_r).mean()*100:.0f}%")

    print("\n==== 4. 檢定力(MDE, t=2) ====")
    for sl, m in (("IS ", isb), ("OOS", ~isb)):
        v = Lg["A"][m]; nd = d["date"].values[m]
        sd = v.std(ddof=1); nday = len(np.unique(nd))
        print(f"  {sl} n={m.sum()} 日={nday} sd={sd:.1f} → 每訊號 MDE≈{2*sd/np.sqrt(m.sum()):.1f} bps(獨立假設) / 日聚類約 ×{np.sqrt(m.sum()/nday):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
