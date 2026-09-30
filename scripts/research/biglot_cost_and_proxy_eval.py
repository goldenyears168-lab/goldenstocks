#!/usr/bin/env python3
"""兩條收尾檢定(2026-09-30):(A)「跌深∧大量」窄縫是不是已知東西的代理 (B) 成本結構能不能降。

A 代理檢定(窄縫定義:當日 w5<=-200 ∧ 該桶成交額最高三分位, n=44):
  跌停 / 波動正規化 / 個股與日期集中度 / 大盤狀態,逐一排除。
B 成本:現股當沖 22 bps 的組成、個股期貨替代的覆蓋率與流動性、成本敏感度曲線。

用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_cost_and_proxy_eval.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from biglot_score_v23_fit import cl_t
from biglot_hold_lab import load_lab, BAD

SRC = Path.home() / "goldenstocks-data/scratch/biglot_exit_paths_2026-09-30.parquet"
FUT = Path.home() / "goldenstocks-data/cache/stock_futures_daily"
H, COST = 12, 22.0
FUT_FEE_NTD = 50.0      # 個股期貨來回手續費約 NT$50/口(1 口 = 2000 股)
FUT_TAX_BPS = 0.4       # 期交稅十萬分之 2,來回


def ffill(m):
    return pd.DataFrame(m).ffill(axis=1).bfill(axis=1).values


def load():
    d = pd.read_parquet(SRC)
    d = d[np.isfinite(d["entry"].values) & (d["entry"].values > 0)].reset_index(drop=True)
    L = load_lab()
    d = d.merge(L["d"][["sid", "date", "bucket", "w5", "tot_amt", "mkt_w5"]], on=["sid", "date", "bucket"], how="left")
    B = ffill(np.column_stack([d[f"b{k}"].values for k in range(H + 1)]))
    S = np.column_stack([d[f"s{k}"].values for k in range(H + 1)])
    bad = np.logical_or.reduce([np.column_stack([d[f"x{j}_{k}"].values for k in range(H + 1)]).astype(bool)
                                for j in range(len(BAD))])
    n = len(d); kx = np.full(n, H); done = np.zeros(n, bool)
    for k in range(1, H + 1):
        h = ((S[:, k] <= 0) | bad[:, k]) & ~done
        kx[h] = k; done |= h
    gross = (np.take_along_axis(B, kx[:, None], 1).ravel() / d["entry"].values - 1) * 1e4
    return d, gross, L


def tick_bps(p):
    t = np.where(p < 10, 0.01, np.where(p < 50, 0.05, np.where(p < 100, 0.1,
        np.where(p < 500, 0.5, np.where(p < 1000, 1.0, 5.0)))))
    return t / p * 1e4


def main():
    d, gross, L = load()
    ok = d["filled"].values; isb = d["is"].values.astype(bool); dates = d["date"].values
    f = d[ok].reset_index(drop=True); gf = gross[ok] - COST
    w = f["w5"].fillna(0).values
    aq = pd.qcut(np.log10(f["tot_amt"].fillna(1).values.clip(1)), 3, labels=False)
    core = (w <= -200) & (aq == 2)
    df_ = f["date"].values
    mu, t = cl_t(gf[core], df_[core])
    print(f"=== A. 窄縫代理檢定 (n={core.sum()}, 毛−22 {mu:+.2f} t{t:+.2f}) ===")
    print(f"  跌停? w5 min {w[core].min():.0f} bps(跌停約 -1000) → w5<=-700 共 {int((w[core]<=-700).sum())} 筆")
    sub = f[core]
    print(f"  集中度: {sub['sid'].nunique()} 檔 / {sub['date'].nunique()} 日;最多日 {sub['date'].value_counts().index[0]} 佔 {sub['date'].value_counts().iloc[0]} 筆")
    for nm, m in ((f"去掉 {sub['date'].value_counts().index[0]}", core & (df_ != sub["date"].value_counts().index[0])),):
        m2, t2 = cl_t(gf[m], df_[m]); print(f"  {nm}: n={m.sum()} {m2:+7.2f}(t{t2:+5.2f})")
    mk = f["mkt_w5"].fillna(0).values
    print(f"  大盤: 窄縫內 mkt_w5 中位 {np.median(mk[core]):+.0f} vs 全體 {np.median(mk):+.0f} bps")
    for lo, hi, nm in ((-1e9, -100, "大盤跌>1%"), (-100, -30, "跌0.3~1%"), (-30, 1e9, "持平以上")):
        m = core & (mk > lo) & (mk <= hi)
        if m.sum() < 10:
            print(f"    {nm:<10} n={m.sum()} <10"); continue
        m3, t3 = cl_t(gf[m], df_[m]); print(f"    {nm:<10} n={m.sum():3d} {m3:+7.2f}(t{t3:+5.2f})")

    print("\n=== B. 成本結構 ===")
    px = f["entry"].values
    tb = tick_bps(px)
    print(f"  進場價中位 {np.median(px):.0f} 元;最小跳動 = {np.median(tb):.1f} bps")
    print(f"  現股當沖 {COST:.0f} bps ≈ 證交稅(減半)15 + 手續費約 7(≈2.5 折) → 68% 是稅,不可壓縮")
    rows = []
    for p in sorted(FUT.glob("*.parquet")):
        try:
            x = pd.read_parquet(p, columns=["date", "stock_id", "volume", "trading_session"])
        except Exception:  # noqa: BLE001
            continue
        rows.append(x)
    if rows:
        F = pd.concat(rows)
        F = F[F["trading_session"] == "position"]
        F["date"] = F["date"].astype(str)
        liq = F[F["date"] >= "2026-06-01"].groupby("stock_id")["volume"].median()
        cov = f["sid"].isin(liq.index)
        sl = liq.reindex(f.loc[cov, "sid"].unique()).dropna()
        fut_cost = FUT_TAX_BPS + FUT_FEE_NTD / (2000 * px) * 1e4
        print(f"  個股期貨: 訊號池 {f['sid'].nunique()} 檔中 {int(f['sid'].isin(liq.index).groupby(f['sid']).first().sum())} 檔有,按筆數覆蓋 {cov.mean()*100:.0f}%")
        print(f"    日成交口數中位 {sl.median():.0f} 口(<100 口的 {int((sl<100).sum())}/{len(sl)} 檔) → 價差必寬")
        print(f"    理論成本 {np.median(fut_cost):.1f} bps,省 {COST-np.median(fut_cost):.1f};但價差多 1 檔就要付 {np.median(tb):.1f} bps")
        print(f"    → 多 1 檔淨 {COST-np.median(fut_cost)-np.median(tb):+.1f} bps;多 2 檔淨 {COST-np.median(fut_cost)-2*np.median(tb):+.1f} bps")

    print("\n=== C. 成本敏感度(每訊號,未成交=0) ===")
    for c in (22, 15, 10, 5, 0):
        v = np.where(ok, gross - c, 0.0)
        o = f"  成本 {c:2d} bps"
        for sl_, mk_ in (("IS", isb), ("OOS", ~isb)):
            m4, t4 = cl_t(v[mk_], dates[mk_]); o += f" | {sl_} {m4:+7.2f}(t{t4:+5.2f})"
        print(o)
    print("  → 成本歸零時的數字就是訊號毛價值的上界")
    return 0


if __name__ == "__main__":
    sys.exit(main())
