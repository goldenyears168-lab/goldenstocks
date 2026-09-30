#!/usr/bin/env python3
"""處置股各時點的漲跌/紅黑/開收統計(2026-09-30)。

對照市面上的「處置股歷史數據統計」面板(處前2/處前1/處置1..3/出前3/出前1/出關1..2),
用我們自己的 disposal_windows.csv × stock_daily_bars 重算,並補上對方沒有的:
  - 紅黑兩種口徑分開報(K棒顏色=收vs開;漲跌=收vs昨收) —— 混用會得到不同結論
  - 每格附 n 與 t 值(對零的檢定),避免把小樣本當結論
  - 「累犯」兩種定義(TWSE measure 明寫第二次以上 / 前次處置在 60 日內)都算

時點定義(以處置起日 start、結束日 end 為錨,皆取該檔實際有日線的交易日):
  處前2/處前1 = start 前第 2/1 個交易日(處前1 即「公告日」,收盤後才公告)
  處置1/2/3   = start 起第 1/2/3 個交易日
  出前3/出前1 = end 往前第 3/1 個交易日(仍在處置期內)
  出關1/出關2 = end 之後第 1/2 個交易日

用法:PYTHONPATH=src .venv/bin/python scripts/research/disposal_window_stats.py
"""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from stock_db import DATA_DIR, DEFAULT_DB_PATH

WIN = DATA_DIR / "disposal" / "disposal_windows.csv"
NEW_RULE = "2026-08-10"          # 新制(5日/2分盤)上路
POINTS = ["處前2", "處前1", "處置1", "處置2", "處置3", "出前3", "出前1", "出關1", "出關2"]


def load():
    W = pd.read_csv(WIN, dtype=str)
    W["start"] = pd.to_datetime(W["start"]); W["end"] = pd.to_datetime(W["end"])
    W = W.dropna(subset=["start", "end"]).sort_values(["stock_id", "start"]).reset_index(drop=True)
    # 累犯定義 A:measure 明寫第二次以上(僅 TWSE 有);B:同檔前次處置在 60 日內
    W["repeat_a"] = W["measure"].astype(str).str.contains("第二次|第三次|第四次|連續三次")
    prev = W.groupby("stock_id")["end"].shift(1)
    W["repeat_b"] = (W["start"] - prev).dt.days.le(60).fillna(False)
    ids = tuple(W["stock_id"].unique())
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    q = (f"select stock_id,trade_date,open,high,low,close,volume from stock_daily_bars "
         f"where stock_id in ({','.join('?' * len(ids))}) and trade_date>='2019-01-01'")
    P = pd.read_sql(q, con, params=ids).drop_duplicates(["stock_id", "trade_date"], keep="first")
    P["trade_date"] = pd.to_datetime(P["trade_date"])
    P = P[(P["close"] > 0) & (P["open"] > 0)].sort_values(["stock_id", "trade_date"]).reset_index(drop=True)
    return W, P


def build(W, P):
    rows = []
    for sid, g in P.groupby("stock_id"):
        g = g.reset_index(drop=True)
        dts = list(g["trade_date"]); n = len(g)
        op = g["open"].values; cl = g["close"].values
        for e in W[W["stock_id"] == sid].itertuples():
            before = [i for i, d in enumerate(dts) if d < e.start]
            inside = [i for i, d in enumerate(dts) if e.start <= d <= e.end]
            after = [i for i, d in enumerate(dts) if d > e.end]
            if len(before) < 3 or not inside:
                continue
            idx = {"處前2": before[-2] if len(before) >= 2 else None,
                   "處前1": before[-1],
                   "處置1": inside[0] if len(inside) >= 1 else None,
                   "處置2": inside[1] if len(inside) >= 2 else None,
                   "處置3": inside[2] if len(inside) >= 3 else None,
                   "出前3": inside[-3] if len(inside) >= 3 else None,
                   "出前1": inside[-1],
                   "出關1": after[0] if len(after) >= 1 else None,
                   "出關2": after[1] if len(after) >= 2 else None}
            rec = {"sid": sid, "start": e.start, "measure": e.measure,
                   "repeat_a": e.repeat_a, "repeat_b": e.repeat_b, "new_rule": e.start >= pd.Timestamp(NEW_RULE)}
            for k, i in idx.items():
                if i is None or i == 0 or i >= n:
                    rec[f"{k}_漲跌"] = np.nan; rec[f"{k}_開收"] = np.nan; continue
                rec[f"{k}_漲跌"] = (cl[i] / cl[i - 1] - 1) * 100      # 收 vs 昨收
                rec[f"{k}_開收"] = (cl[i] / op[i] - 1) * 100          # 收 vs 開(K棒顏色)
            rows.append(rec)
    return pd.DataFrame(rows)


def report(R, label, mask):
    g = R[mask]
    if len(g) < 5:
        print(f"\n### {label}: n={len(g)} (太少,略)"); return
    print(f"\n### {label}（事件 n={len(g)}）")
    print(f"  {'時點':<6}{'n':>5}  {'平均漲跌%':>10}{'中位':>8}  {'紅K%':>7}{'黑K%':>7}  {'平均開收%':>10}  {'t(開收)':>8}")
    for k in POINTS:
        a = g[f"{k}_漲跌"].dropna(); b = g[f"{k}_開收"].dropna()
        if len(b) < 5:
            print(f"  {k:<6}{len(b):>5}  n<5"); continue
        red = float((b > 0).mean() * 100); blk = float((b < 0).mean() * 100)
        t = b.mean() / (b.std(ddof=1) / np.sqrt(len(b))) if b.std(ddof=1) > 0 else np.nan
        print(f"  {k:<6}{len(b):>5}  {a.mean():+10.2f}{a.median():+8.2f}  {red:>6.1f}%{blk:>6.1f}%  {b.mean():+10.2f}  {t:+8.2f}")


def main():
    W, P = load()
    print(f"處置事件 {len(W)} 筆 / 日線覆蓋 {P['stock_id'].nunique()} 檔")
    R = build(W, P)
    print(f"可對齊時點的事件 {len(R)} 筆（{R['start'].min():%Y-%m-%d} ~ {R['start'].max():%Y-%m-%d}）")
    print("\n口徑:紅K/黑K = 收盤 vs 開盤(K棒顏色);平均漲跌% = 收盤 vs 昨收;t 是開收幅對 0 的檢定。")
    report(R, "全樣本", np.ones(len(R), bool))
    report(R, "初犯（前次處置不在 60 日內）", ~R["repeat_b"].values)
    report(R, "累犯 B（前次處置在 60 日內）", R["repeat_b"].values)
    report(R, "累犯 A（TWSE measure 明寫第二次以上）", R["repeat_a"].values)
    report(R, f"新制（{NEW_RULE} 起 · 5日2分盤）", R["new_rule"].values)
    report(R, f"新制 × 累犯 B", (R["new_rule"] & R["repeat_b"]).values)
    return 0


if __name__ == "__main__":
    sys.exit(main())
