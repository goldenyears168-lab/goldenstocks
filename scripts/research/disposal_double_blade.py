#!/usr/bin/env python3
"""處置「雙刀戰法」回測:空刀(快進處置)+ 多刀(快出關)(2026-09-30)。

對照權證小哥的雙刀邏輯,用我們自己的 1,478 筆處置事件驗證兩把刀是否各自成立:
  空刀 = 公告日收盤放空 → 處置首日收盤回補(賭處置後多頭動能衰竭)
  多刀 = 處置第 3 天收盤買 → 出關日開盤賣(賭出關前買盤卡位;見 disposal_release_long.py)
報酬一律減 0050 同期(超額),每一腳各扣 0.44% 現股來回成本。
另檢定坐牢中面板的兩個欄位對多刀有無增量:位階(近 20 日高低區間位置)、月斜(20 日均線斜率)。

⚠ 注意:本檔只驗證**訊號**。執行面(處置期分盤撮合、預收款券、融券限制、平盤下不得放空)
   完全未驗證,見檔尾提醒。

用法:PYTHONPATH=src .venv/bin/python scripts/research/disposal_double_blade.py
"""
from __future__ import annotations
import importlib.util
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from stock_db import DEFAULT_DB_PATH

COST = 0.44
BENCH = "0050"


def build():
    p = Path(__file__).with_name("disposal_window_stats.py")
    spec = importlib.util.spec_from_file_location("dws", p)
    M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
    W, P = M.load()
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    B = pd.read_sql("select trade_date,close from stock_daily_bars where stock_id=? and trade_date>='2019-01-01' order by trade_date",
                    con, params=(BENCH,)).drop_duplicates("trade_date")
    B["trade_date"] = pd.to_datetime(B["trade_date"]); B = B.set_index("trade_date")["close"]

    def bret(a, b):
        x = B.asof(a); y = B.asof(b)
        return (y / x - 1) * 100 if x and y and x > 0 else np.nan

    rows = []
    for sid, g in P.groupby("stock_id"):
        g = g.reset_index(drop=True)
        dts = list(g["trade_date"]); cl = g["close"].values; op = g["open"].values
        hi20 = pd.Series(cl).rolling(20, min_periods=5).max().shift(1).values
        lo20 = pd.Series(cl).rolling(20, min_periods=5).min().shift(1).values
        ma20 = pd.Series(cl).rolling(20, min_periods=5).mean().values
        for e in W[W["stock_id"] == sid].itertuples():
            ins = [i for i, d in enumerate(dts) if e.start <= d <= e.end]
            pre = [i for i, d in enumerate(dts) if d < e.start]
            aft = [i for i, d in enumerate(dts) if d > e.end]
            if len(ins) < 3 or len(pre) < 2 or not aft:
                continue
            a, d0, k2 = pre[-1], ins[0], ins[2]
            if cl[d0] <= 0 or op[aft[0]] <= 0:
                continue
            rng = (hi20[a] - lo20[a]) if (hi20[a] and lo20[a] and hi20[a] > lo20[a]) else np.nan
            rows.append({
                "sid": sid, "year": e.start.year, "repeat": e.repeat_b,
                "空刀": -((cl[d0] / cl[a] - 1) * 100 - bret(dts[a], dts[d0])) - COST,
                "多刀": (op[aft[0]] / cl[k2] - 1) * 100 - bret(dts[k2], dts[aft[0]]) - COST,
                "累幅%": (cl[k2] / cl[d0] - 1) * 100,
                "位階": ((cl[k2] - lo20[a]) / rng * 100) if np.isfinite(rng) else np.nan,
                "月斜": ((ma20[a] / ma20[max(a - 20, 0)] - 1) * 100) if ma20[a] and ma20[max(a - 20, 0)] else np.nan,
            })
    return pd.DataFrame(rows)


def rep(lab, v):
    if len(v) < 15:
        return f"  {lab:<16}{len(v):>5}  n<15"
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    return (f"  {lab:<16}{len(v):>5}{v.mean():+9.2f}{v.median():+9.2f}{t:+8.2f}"
            f"{np.mean(v > 0) * 100:5.0f}%{np.percentile(v, 5):+9.2f}{np.percentile(v, 95):+9.2f}")


def main():
    R = build()
    sel = R["累幅%"] > -10
    hdr = f"  {'分組':<16}{'n':>5}{'均':>9}{'中位':>9}{'t':>8}{'正%':>6}{'p5':>9}{'p95':>9}"
    for col, title, mask in (("空刀", "空刀:公告日收盤放空 → 處置首日收盤回補", None),
                             ("多刀", "多刀:處置第3天收盤買 → 出關日開盤賣(限累幅>-10%)", sel)):
        print(f"\n===== {title} =====\n{hdr}")
        base = R if mask is None else R[mask]
        for nm, m in (("全部", np.ones(len(base), bool)), ("初犯", ~base["repeat"].values), ("累犯", base["repeat"].values)):
            print(rep(nm, base.loc[m, col].dropna()))
        print("  IS/OOS:")
        for nm, m in (("全部", np.ones(len(base), bool)), ("累犯", base["repeat"].values)):
            o = f"    {nm:<6}"
            for lab, ym in (("IS", base["year"] <= 2023), ("OOS", base["year"] >= 2024)):
                v = base.loc[m & ym, col].dropna()
                if len(v) < 15:
                    o += f" | {lab} n<15"; continue
                t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
                o += f" | {lab} n={len(v):4d} {v.mean():+6.2f}%(t{t:+5.2f}) 正{np.mean(v > 0) * 100:3.0f}%"
            print(o)

    print(f"\n===== 雙刀合體(同一事件先空後多,兩腳各扣成本) =====\n{hdr}")
    both = R[sel].dropna(subset=["空刀", "多刀"])
    for nm, m in (("全部", np.ones(len(both), bool)), ("初犯", ~both["repeat"].values), ("累犯", both["repeat"].values)):
        print(rep(nm, (both.loc[m, "空刀"] + both.loc[m, "多刀"])))

    print("\n===== 坐牢中面板欄位對多刀的增量(累犯 ∧ 累幅>-10%) =====")
    g = R[sel & R["repeat"]]
    for col, labs in (("位階", ("低", "中", "高")), ("月斜", ("低", "中", "高"))):
        gg = g.dropna(subset=[col]).copy()
        if len(gg) < 30:
            continue
        gg["q"] = pd.qcut(gg[col].rank(method="first"), 3, labels=False)
        for q, lab in enumerate(labs):
            v = gg.loc[gg["q"] == q, "多刀"]
            t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
            print(f"  {col}{lab} n={len(v):3d} 多刀 {v.mean():+6.2f}% 中位 {v.median():+6.2f}% t={t:+5.2f}")

    print("\n⚠ 未驗證的執行面(不可略過):")
    print("  - 空刀出場、多刀進場都落在處置期分盤撮合(新制約 2 分鐘、第二次以上約 25 分鐘),排隊成交率未測")
    print("  - 處置股常暫停現股當沖;放空受融券限制與平盤下不得放空約束,個股期貨覆蓋僅約 8 成且流動性差")
    print("  - 處置期須預收款券,資金佔用 100%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
