#!/usr/bin/env python3
"""「平倉後可再次交易」回測 — 階段 2:兩套進場閘門的組合模擬(2026-09-30)。

同一份全日桶路徑上跑三套規則,隔離「平倉後可再進」到底多做了什麼:
  A 舊規則  每檔每日只進一次,且 seen 在訊號 fire 當下就記(容量被擋/沒買一/掛單未成交
            都消耗掉當日唯一額度)——2026-09-29 之前的線上行為
  B 新規則  同時同檔最多一口、平倉後可再進、無次數上限;暫時性障礙只冷卻一桶
  C 對照    同 B 的冷卻語意,但每檔每日最多成交一次(= B 拿掉「平倉後可再進」)
     → B − C 就是「平倉後可再交易」這一條的淨貢獻

共同設定:進場=桶收掛買一等 30 秒(可成交價),出場=分數≤0 ∨ 壞標籤 ∨ 持滿 60 分 ∨ 收盤,
出場價=桶邊界後首筆賣方主動成交(買一,悲觀),成本 22 bps,同時最多 3 口,同桶多檔按分數降序。
桶級粒度 5 分鐘,故 60s/300s 冷卻在此都等於「下一桶」。

用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_reentry_sim.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from biglot_score_v23_fit import cl_t
from biglot_hold_lab import load_lab

SRC = Path.home() / "goldenstocks-data/scratch/biglot_daypaths_2026-09-30.parquet"
COST = 22.0
TH = 15.0
K_CAP = 3          # 同時最多口數
MAX_HOLD = 12      # 12 桶 = 60 分
IS_END = "2026-06-30"


def simulate(day, mode, uidx, kmax):
    """day:單日 DataFrame(index=k)。mode:'A'|'B'|'C'。回傳成交明細 list。"""
    s = day["s"].values; bad = day["bad"].values; bid = day["bid"].values
    filled = day["filled"].values; exit_px = day["exit_px"].values; sid = day["sid"].values[0]
    trades = []; pos = None; seen = False; n_ent = 0; cool = -1
    for k in range(kmax + 1):
        # --- 出場 ---
        if pos is not None:
            hold = k - pos["k"]
            if hold >= 1 and (s[k] <= 0 or bad[k] or hold >= MAX_HOLD or k == kmax):
                px_out = exit_px[k]
                if np.isfinite(px_out):
                    trades.append({"sid": sid, "k_in": pos["k"], "k_out": k, "entry": pos["px"], "exit": px_out,
                                   "hold": (k - pos["k"]) * 5, "nth": n_ent})
                    pos = None; n_ent += 1
                    cool = k + 1                      # 平倉後冷卻一桶
        # --- 進場 ---
        if pos is not None or k > kmax - 1 or s[k] < TH or day["bucket"].values[k] > "12:20":
            continue
        if mode == "A":
            if seen:
                continue
            seen = True                                # fire 當下就記(含後續被擋的情形)
        else:
            if k < cool:
                continue
            if mode == "C" and n_ent >= 1:
                continue
        if not np.isfinite(bid[k]):
            cool = k + 1
            continue
        if not filled[k]:
            cool = k + 1                               # 未成交:B/C 冷卻一桶後可重試;A 已被 seen 擋死
            continue
        pos = {"k": k, "px": bid[k]}
    return trades


def run(mode, df, uidx, kmax):
    out = []
    for (sid, date), g in df.groupby(["sid", "date"], sort=False):
        g = g.sort_values("k")
        for t in simulate(g, mode, uidx, kmax):
            u = uidx.loc[date]
            t.update({"date": date, "uret": (u.iloc[t["k_out"]] / u.iloc[t["k_in"]] - 1) * 1e4})
            out.append(t)
    r = pd.DataFrame(out)
    if len(r):
        r["gross"] = (r["exit"] / r["entry"] - 1) * 1e4
        r["net"] = r["gross"] - COST
        r["ex"] = r["net"] - r["uret"]
        r["is"] = r["date"] <= IS_END
    return r


def rep(r, lab):
    o = f"  {lab:<28}n={len(r):4d}"
    for sl, m in (("IS", r["is"]), ("OOS", ~r["is"])):
        v = r.loc[m, "net"].values
        if len(v) < 20:
            o += f" | {sl} n<20"; continue
        mu, t = cl_t(v, r.loc[m, "date"].values)
        o += f" | {sl} n={len(v):4d} 毛−22 {mu:+7.2f}(t{t:+5.2f}) 勝{np.mean(v>0)*100:3.0f}%"
    return o


def main():
    df = pd.read_parquet(SRC)
    L = load_lab(); uidx = L["uidx"]; buckets = L["buckets"]
    kmax = len(buckets) - 1
    print(f"全日桶路徑 {len(df)} 列 / {df.groupby(['sid','date']).ngroups} 組 (sid,date);桶 {len(buckets)} 個\n")
    res = {m: run(m, df, uidx, kmax) for m in ("A", "B", "C")}
    print("==== 1. 三套規則(每成交筆,毛 − 22 bps) ====")
    print(rep(res["A"], "A 舊:一天一次+fire即記"))
    print(rep(res["C"], "C 新冷卻但每日只成交一次"))
    print(rep(res["B"], "B 新:平倉後可再進"))

    print("\n==== 2. 交易量與每日組合 ====")
    for m in ("A", "C", "B"):
        r = res[m]
        per_day = r.groupby("date")["net"].agg(["size", "sum"])
        print(f"  {m}: 總筆數 {len(r):4d}  每日筆數 中位 {per_day['size'].median():.0f} p90 {per_day['size'].quantile(.9):.0f} max {per_day['size'].max()}"
              f"  日合計 bps 均 {per_day['sum'].mean():+7.1f}  總和 {r['net'].sum():+9.1f}")

    print("\n==== 3. B − C:「平倉後可再進」多做的那些交易 ====")
    key = ["sid", "date", "k_in"]
    extra = res["B"].merge(res["C"][key], on=key, how="left", indicator=True)
    extra = extra[extra["_merge"] == "left_only"]
    print(f"  多做 {len(extra)} 筆(占 B 的 {len(extra)/len(res['B'])*100:.1f}%)")
    if len(extra) >= 20:
        print(rep(extra, "  └ 這批多做的"))
        print(f"  └ 第 2 次以上進場的分布: {extra['nth'].value_counts().sort_index().to_dict()}")
    print("\n==== 4. B − A:新規則相對線上舊行為多做的 ====")
    ex2 = res["B"].merge(res["A"][key], on=key, how="left", indicator=True)
    ex2 = ex2[ex2["_merge"] == "left_only"]
    print(f"  多做 {len(ex2)} 筆(占 B 的 {len(ex2)/len(res['B'])*100:.1f}%)")
    if len(ex2) >= 20:
        print(rep(ex2, "  └ 這批多做的"))
    lost = res["A"].merge(res["B"][key], on=key, how="left", indicator=True)
    lost = lost[lost["_merge"] == "left_only"]
    print(f"  A 有而 B 沒有的: {len(lost)} 筆")

    print("\n==== 5. 第 n 次進場的品質(B 規則) ====")
    r = res["B"]
    for nth, g in r.groupby("nth"):
        if len(g) < 10:
            print(f"  第 {nth+1} 次 n={len(g)} (n<10)"); continue
        mu, t = cl_t(g["net"].values, g["date"].values)
        print(f"  第 {nth+1} 次 n={len(g):4d} 毛−22 {mu:+7.2f}(t{t:+5.2f}) 勝{np.mean(g['net']>0)*100:3.0f}% 均持{g['hold'].mean():.0f}分")

    print("\n==== 6. 逐月(每成交筆,毛−22) ====")
    print("  月份      A 筆/均        C 筆/均        B 筆/均")
    for mth in sorted(res["B"]["date"].str[:7].unique()):
        line = f"  {mth} "
        for m in ("A", "C", "B"):
            g = res[m][res[m]["date"].str[:7] == mth]
            line += f" {len(g):3d}/{g['net'].mean():+7.2f}" if len(g) else "    –/     –"
        print(line)

    print("\n==== 7. 超額口徑(減宇宙)對照 ====")
    for m in ("A", "C", "B"):
        r = res[m]; o = f"  {m}"
        for sl, mk in (("IS", r["is"]), ("OOS", ~r["is"])):
            v = r.loc[mk, "ex"].values
            mu, t = cl_t(v, r.loc[mk, "date"].values)
            o += f" | {sl} {mu:+7.2f}(t{t:+5.2f})"
        print(o)
    return 0


if __name__ == "__main__":
    sys.exit(main())
