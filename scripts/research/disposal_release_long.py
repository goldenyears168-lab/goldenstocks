#!/usr/bin/env python3
"""處置期間做多(「被關之後跌幅不深就做多」)的回測(2026-09-30)。

起點是 jack 的構想:處置坐牢中面板的「累幅」欄(處置期間累積漲跌)很重要,跌幅不深就想做多。
規格:處置第 3 天收盤買進 → **出關日開盤**賣出;報酬一律減 0050 同期(超額)再扣 0.44%
(現股非當沖來回成本)。累犯定義=前次處置結束在 60 日內。

為什麼出場在出關日開盤:報酬拆解顯示(累犯、跌幅<10%)
  處置第3天→出關前1  +3.40%   出關跳空 +2.12%   出關日內 -0.41%   出關後1→3 +0.43%
→ 報酬全部在處置期內與出關跳空,出關後就沒有了,抱到出關後 3 日只是多承擔波動。

用法:PYTHONPATH=src .venv/bin/python scripts/research/disposal_release_long.py
"""
from __future__ import annotations
import importlib.util
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from stock_db import DEFAULT_DB_PATH

COST = 0.44          # 現股非當沖來回(手續費 2 折×2 + 證交稅 0.3%)
BENCH = "0050"
K_OBS = 2            # 觀察點:處置期第 3 個交易日(index 從 0 起)


def _load_stats_mod():
    p = Path(__file__).with_name("disposal_window_stats.py")
    spec = importlib.util.spec_from_file_location("dws", p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def build():
    M = _load_stats_mod()
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
        dts = list(g["trade_date"]); cl = g["close"].values; op = g["open"].values; vol = g["volume"].values
        for e in W[W["stock_id"] == sid].itertuples():
            ins = [i for i, d in enumerate(dts) if e.start <= d <= e.end]
            aft = [i for i, d in enumerate(dts) if d > e.end]
            if len(ins) <= K_OBS or not aft:
                continue
            base = cl[ins[0]]; i = ins[K_OBS]
            if base <= 0 or op[aft[0]] <= 0:
                continue
            rows.append({"sid": sid, "start": e.start, "year": e.start.year, "repeat": e.repeat_b,
                         "累幅%": (cl[i] / base - 1) * 100,
                         "張數": vol[i] / 1000,
                         "raw": (op[aft[0]] / cl[i] - 1) * 100,
                         "超額": (op[aft[0]] / cl[i] - 1) * 100 - bret(dts[i], dts[aft[0]])})
    R = pd.DataFrame(rows).dropna(subset=["超額"])
    R["net"] = R["超額"] - COST
    return R


def line(lab, v):
    if len(v) < 15:
        return f"  {lab:<22}{len(v):>5}  n<15"
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    return (f"  {lab:<22}{len(v):>5}{v.mean():+9.2f}{v.median():+9.2f}{t:+8.2f}"
            f"{np.mean(v > 0) * 100:5.0f}%{np.percentile(v, 5):+9.2f}{np.percentile(v, 95):+9.2f}")


def main():
    R = build()
    sel = R["累幅%"] > -10
    print(f"處置事件 n={len(R)}（{R['year'].min()}–{R['year'].max()}）;規格=處置第3天收盤買、出關日開盤賣,超額−{COST}%成本\n")
    print(f"  {'分組':<22}{'n':>5}{'均':>9}{'中位':>9}{'t':>8}{'正%':>6}{'p5':>9}{'p95':>9}")
    for nm, m in (("全部 跌幅<10%", sel),
                  ("初犯 跌幅<10%", sel & ~R["repeat"]),
                  ("累犯 跌幅<10%", sel & R["repeat"]),
                  ("累犯 跌幅>=10%", (~sel) & R["repeat"]),
                  ("累犯 累幅 0~+5%", R["repeat"] & (R["累幅%"] > 0) & (R["累幅%"] <= 5))):
        print(line(nm, R.loc[m, "net"]))

    print("\n== IS/OOS（IS 2019–2023 / OOS 2024–2026）==")
    for nm, m in (("全部 跌幅<10%", sel), ("初犯 跌幅<10%", sel & ~R["repeat"]), ("累犯 跌幅<10%", sel & R["repeat"])):
        o = f"  {nm:<18}"
        for lab, ym in (("IS", R["year"] <= 2023), ("OOS", R["year"] >= 2024)):
            v = R.loc[m & ym, "net"]
            if len(v) < 15:
                o += f" | {lab} n<15"; continue
            t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
            o += f" | {lab} n={len(v):4d} {v.mean():+6.2f}%(t{t:+5.2f}) 中位{v.median():+6.2f} 正{np.mean(v > 0) * 100:3.0f}%"
        print(o)

    print("\n== 逐年（累犯 ∧ 跌幅<10%）==")
    for y, gy in R[sel & R["repeat"]].groupby("year"):
        v = gy["net"]
        print(f"  {y} n={len(v):3d} 均 {v.mean():+6.2f}% 中位 {v.median():+6.2f}% 正 {np.mean(v > 0) * 100:3.0f}%")

    print("\n== 可執行性（處置第3天成交量）==")
    for nm, m in (("全部", sel), ("累犯", sel & R["repeat"])):
        g = R[m]
        print(f"  {nm}: 中位 {g['張數'].median():.0f} 張, p25 {g['張數'].quantile(.25):.0f} 張, "
              f"<100 張佔 {(g['張數'] < 100).mean() * 100:.0f}%, <500 張佔 {(g['張數'] < 500).mean() * 100:.0f}%")
    print("\n  ⚠ 處置期為分盤撮合(新制約 2 分鐘、第二次以上約 25 分鐘)且須預收款券;")
    print("     本回測以收盤價假設可成交,實際排隊成交率未驗證。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
