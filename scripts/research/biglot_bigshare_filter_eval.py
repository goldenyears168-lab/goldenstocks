#!/usr/bin/env python3
"""大戶占比門檻當做多方進場過濾器(2026-09-30)。

問題:V2.5≥15 之外再要求「大戶占比 >= θ%」(jack 問 20%),能不能把負 EV 的進場救回來?
資料:biglot_exit_paths_2026-09-30.parquet(1,071 事件 × 13 桶路徑)join 127 日面板的大戶欄位。
口徑:
  b5n      = 當桶(5分)大戶淨額 / 該桶總成交額 %       (訊號桶,PIT 安全)
  bigsh30  = 含當桶的 rolling 6 桶(30分)大戶淨占比 %
  bigp30   = shift(1) 後 rolling 6 桶(PIT 更保守的 30 分)
  cum_day  = 當日累計大戶淨額 / 當日累計成交額 %(截至訊號桶)
損益一律報「毛 − 22 bps」(現股單邊做多實際吃到的;超額口徑要同時放空籃子才成立),
同時報每成交筆(篩選力)與每訊號(未成交=0,實際部位口徑),以區分真篩選 vs 只是稀釋樣本。

用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_bigshare_filter_eval.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from biglot_score_v23_fit import cl_t
from biglot_hold_lab import load_lab, BAD

SRC = Path.home() / "goldenstocks-data/scratch/biglot_exit_paths_2026-09-30.parquet"
COST = 22.0
H = 12
COLS = ("b5n", "bigsh30", "bigp30", "cum_day")


def build():
    d = pd.read_parquet(SRC)
    d = d[np.isfinite(d["entry"].values) & (d["entry"].values > 0)].reset_index(drop=True)
    L = load_lab(); p = L["d"]
    p = p.sort_values(["sid", "date", "bucket"])
    g = p.groupby(["sid", "date"])
    p["cum_day"] = g["big_net"].cumsum() / g["tot_amt"].cumsum().replace(0, np.nan) * 100
    keep = ["sid", "date", "bucket", "b5n", "bigsh30", "bigp30", "cum_day", "share5", "tot_amt"] + ["it:蓄勢", "it:急跌·大戶接∧未竭"]
    d = d.merge(p[keep], on=["sid", "date", "bucket"], how="left")
    ff = lambda m: pd.DataFrame(m).ffill(axis=1).bfill(axis=1).values          # noqa: E731
    B = ff(np.column_stack([d[f"b{k}"].values for k in range(H + 1)]))
    S = np.column_stack([d[f"s{k}"].values for k in range(H + 1)])
    X = {m: np.column_stack([d[f"x{m}_{k}"].values for k in range(H + 1)]).astype(bool) for m in range(len(BAD))}
    bad = np.logical_or.reduce([X[m] for m in range(len(BAD))])
    return d, B, S, bad


def exit_k(S, bad, th=0.0, consec=1, cap=H):
    n = S.shape[0]; out = np.full(n, cap); run = np.zeros(n, int); done = np.zeros(n, bool)
    for k in range(1, cap + 1):
        run = np.where(S[:, k] <= th, run + 1, 0)
        hit = ((run >= consec) | bad[:, k]) & ~done
        out[hit] = k; done |= hit
    return out


def main():
    d, B, S, bad = build()
    isb = d["is"].values.astype(bool); ok = d["filled"].values
    kx = exit_k(S, bad)
    r = np.take_along_axis(B, kx[:, None], 1).ravel()
    gross = (r / d["entry"].values - 1) * 1e4 - COST        # 毛 − 成本
    per_sig = np.where(ok, gross, 0.0)
    dates = d["date"].values
    print(f"樣本 n={len(d)}  IS {isb.sum()} / OOS {(~isb).sum()}  日 {d['date'].nunique()}  成交率 {ok.mean()*100:.1f}%\n")

    print("==== 0. 共線性檢查:大戶占比已經多少進了 V2.5? ====")
    print(f"  「蓄勢」項(條件 bigsh30>=10)在事件池觸發率 {d['it:蓄勢'].mean()*100:.1f}%")
    print(f"  「急跌·大戶接∧未竭」(用 b5n)觸發率 {d['it:急跌·大戶接∧未竭'].mean()*100:.1f}%")
    for c in COLS:
        v = d[c]
        print(f"  {c:8s} 中位 {v.median():6.2f}%  p25 {v.quantile(.25):6.2f}  p75 {v.quantile(.75):6.2f}  "
              f">=10% {(v >= 10).mean()*100:4.1f}%  >=20% {(v >= 20).mean()*100:4.1f}%  >=30% {(v >= 30).mean()*100:4.1f}%  缺 {v.isna().sum()}")

    print("\n==== 1. 基準(不加過濾) ====")
    for lab, v, m in (("每成交筆", gross[ok], None), ("每訊號(未成交=0)", per_sig, None)):
        sub = ok if lab == "每成交筆" else np.ones(len(d), bool)
        o = f"  {lab:16s}"
        for sl, mm in (("IS", isb), ("OOS", ~isb)):
            sel = mm & sub
            mu, t = cl_t(gross[sel] if lab == "每成交筆" else per_sig[sel], dates[sel])
            o += f" | {sl} n={sel.sum():4d} {mu:+7.2f}(t{t:+5.2f})"
        print(o)

    print("\n==== 2. 劑量反應:大戶占比 >= θ 才做 ====")
    for c in COLS:
        print(f"-- {c}")
        base_line = None
        for th in (-999, 0, 5, 10, 15, 20, 25, 30, 40):
            sel = (d[c].values >= th) if th != -999 else np.ones(len(d), bool)
            sel = sel & np.isfinite(d[c].values) if th != -999 else sel
            if sel.sum() < 30:
                print(f"   θ={th:>4} 保留 {sel.sum():4d} (n<30)"); continue
            o = f"   θ={th if th != -999 else 'all':>4}% 保留{sel.mean()*100:3.0f}% 成交率{ok[sel].mean()*100:3.0f}%"
            for sl, mm in (("IS", isb), ("OOS", ~isb)):
                f1 = sel & mm & ok
                if f1.sum() < 20:
                    o += f" | {sl} n<20"; continue
                mu, t = cl_t(gross[f1], dates[f1])
                f2 = sel & mm
                mu2, t2 = cl_t(per_sig[f2], dates[f2])
                o += f" | {sl} 成交筆 n={f1.sum():3d} {mu:+7.2f}(t{t:+5.2f}) 每訊號 {mu2:+7.2f}(t{t2:+5.2f})"
            print(o)
            if th == -999:
                base_line = o

    print("\n==== 3. 安慰劑:隨機保留同比例,300 次(每成交筆) ====")
    rng = np.random.default_rng(23)
    for c in ("bigsh30", "cum_day"):
        for th in (10, 20):
            sel = (d[c].values >= th) & np.isfinite(d[c].values)
            for sl, mm in (("IS", isb), ("OOS", ~isb)):
                f1 = sel & mm & ok
                if f1.sum() < 20:
                    continue
                real, _ = cl_t(gross[f1], dates[f1])
                pool = np.where(mm & ok)[0]; k = f1.sum()
                sims = np.array([cl_t(gross[rng.choice(pool, k, replace=False)], dates[rng.choice(pool, k, replace=False)])[0] for _ in range(300)])
                print(f"  {c} θ={th}% {sl} 真實 {real:+6.2f} vs 隨機 {sims.mean():+6.2f} [p5 {np.percentile(sims,5):+6.2f}, p95 {np.percentile(sims,95):+6.2f}] 百分位 {(sims<real).mean()*100:3.0f}%")

    print("\n==== 4. 檢定力 ====")
    for sl, mm in (("IS ", isb), ("OOS", ~isb)):
        v = gross[mm & ok]; nd = len(np.unique(dates[mm & ok]))
        print(f"  {sl} n={len(v)} 日={nd} sd={v.std(ddof=1):.1f} → MDE(t=2,獨立) {2*v.std(ddof=1)/np.sqrt(len(v)):.1f} bps;"
              f" θ=20% 只留 ~{(d['bigsh30'].values>=20).mean()*100:.0f}% → n≈{int(len(v)*(d['bigsh30'].values>=20).mean())} 時 MDE≈{2*v.std(ddof=1)/np.sqrt(max(1,len(v)*(d['bigsh30'].values>=20).mean())):.1f} bps")

    print("\n==== 5. 逐月(bigsh30 >= 20 vs 全部,每成交筆) ====")
    d["m"] = d["date"].str[:7]
    sel = (d["bigsh30"].values >= 20) & np.isfinite(d["bigsh30"].values)
    for mth, g in d.groupby("m"):
        ix = g.index.values
        a = ix[ok[ix]]; b = ix[ok[ix] & sel[ix]]
        ma = cl_t(gross[a], dates[a])[0] if len(a) >= 5 else float("nan")
        mb = cl_t(gross[b], dates[b])[0] if len(b) >= 5 else float("nan")
        print(f"  {mth} 全部 n={len(a):3d} {ma:+7.2f} | θ20 n={len(b):3d} {mb:+7.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
