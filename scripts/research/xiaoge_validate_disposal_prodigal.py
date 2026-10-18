#!/usr/bin/env python3
"""驗證小哥 R67 處置浪子回頭(預註冊定義見 docs/warrant-xiaoge/validation/disposal_prodigal.md)。
PYTHONPATH=src nice .venv/bin/python scripts/research/xiaoge_validate_disposal_prodigal.py
DB 唯讀;不寫 DB。"""
from __future__ import annotations
import sqlite3, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

COST = 0.44
NEW = "2026-08-10"
HS = (5, 10)
LAST = "2026-10-16"


def load():
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    B = pd.read_sql("select stock_id,trade_date d,open o,high h,low l,close c,volume v,source s from stock_daily_bars "
                    "where trade_date>='2019-10-01' and (stock_id glob '[0-9][0-9][0-9][0-9]' or stock_id='0050')", con)
    B["p"] = B.s.map({"twse_mi_index": 0, "tpex_daily": 0, "finmind": 1}).fillna(2)
    B = B.sort_values(["stock_id", "d", "p"]).drop_duplicates(["stock_id", "d"])
    B = B[(B.o > 0) & (B.h >= B.l) & (B.c > 0)].sort_values(["stock_id", "d"]).reset_index(drop=True)
    return B


def prep(B):
    g = B.groupby("stock_id")
    B["ma20"] = g.c.transform(lambda x: x.rolling(20, min_periods=20).mean())
    B["hi20"] = g.c.transform(lambda x: x.rolling(20, min_periods=15).max())
    B["dd"] = (B.c / B.hi20 - 1) * 100
    B["touch"] = (B.l <= B.ma20 * 1.02) & (B.c >= B.ma20 * 0.95)
    B["sig"] = (B.dd <= -20) & B.touch
    B["sig_dd"] = B.dd <= -20
    # forward: entry open at next day; exit close at T+h
    for h in HS:
        B[f"x{h}"] = g.c.shift(-h)
    B["e"] = g.o.shift(-1)
    B["nd"] = g.d.shift(-1)
    return B


def tst(x, grp):
    df = pd.DataFrame({"x": np.asarray(x), "g": np.asarray(grp)}).dropna()
    n = len(df)
    if n < 3:
        return n, np.nan, np.nan, np.nan, np.nan
    m = df.x.mean()
    s = df.groupby("g").x.apply(lambda v: (v - m).sum())
    se = np.sqrt((s ** 2).sum()) / n
    return n, m, df.x.median(), (df.x > 0).mean() * 100, (m / se if se > 0 else np.nan)


def fmt(lab, d, col, h):
    n, m, med, win, t = tst(d[col], d["T"])
    if n < 3:
        return f"| {lab} | h{h} | {n} | 不足 | | | |"
    return f"| {lab} | h{h} | {n} | {m:+.2f} | {med:+.2f} | {win:.0f}% | {t:.1f} |"


def main():
    B = prep(load())
    B = B[B.d <= LAST]
    SB = {k: g for k, g in B.groupby("stock_id")}
    etf = B[B.stock_id == "0050"].set_index("d")
    W = pd.read_csv(DATA_DIR / "disposal" / "disposal_windows.csv", dtype=str)
    W = W[W.measure.isin(["第一次處置", "第二次處置"])].drop_duplicates(["stock_id", "start"])
    W = W[W.start >= "2020-02-01"]
    cal = sorted(B[B.stock_id == "0050"].d)
    out = [f"windows_total={len(W)}"]
    have = set(B.stock_id)
    rows, nobars = [], 0
    for w in W.itertuples():
        if w.stock_id not in have:
            nobars += 1; continue
        g0 = SB[w.stock_id]
        s0 = g0[(g0.d >= w.start) & (g0.d <= w.end)]
        s = s0[s0.sig]
        if s.empty:
            continue
        r = s.iloc[0]  # 窗內第一次成立
        # 窗內 ma20 需要連續日曆(確認前20日有資料)
        rows.append(dict(sid=w.stock_id, T=r.d, start=w.start, end=w.end, meas=w.measure, dd=r.dd, e=r.e, nd=r.nd,
                         x5=r.x5, x10=r.x10, kd=int((s0.d <= r.d).sum())))
    E = pd.DataFrame(rows)
    out.append(f"windows_without_any_bars={nobars}")
    # 窗-level coverage
    for h in HS:
        # 0050 同期 open(T+1)->close(T+h)
        def etfret(r):
            try:
                return (etf.loc[etf.index[etf.index.get_loc(r.nd) + h - 1], "c"] / etf.loc[r.nd, "o"] - 1) * 100
            except Exception:
                return np.nan
        E[f"b{h}"] = E.apply(etfret, axis=1)
        E[f"g{h}"] = (E[f"x{h}"] / E.e - 1) * 100  # 持有 T+1 開 → T+h 收;h=1 即 T+1
    # 注意: x{h}=close of T+h, 入場 open T+1 => 持有 h 個交易日(含入場日)
    for h in HS:
        E[f"n{h}"] = E[f"g{h}"] - COST
        E[f"ex{h}"] = E[f"n{h}"] - E[f"b{h}"]
    E = E.dropna(subset=["e", "x5", "x10", "b5", "b10"]).copy()
    E["T"] = E["T"].astype(str)
    E.to_csv(DATA_DIR / "disposal" / "xiaoge_prodigal_events.csv", index=False) if False else None
    lines = []
    lines.append(f"- 有訊號且 T+10 已實現的事件(窗內首次成立)n={len(E)};窗總數(2020-02起,一二次處置)={len(W)};無任何價格資料的窗={nobars}")
    lines.append(f"- 事件期間 {E['T'].min()} ~ {E['T'].max()};平均訊號在窗第 {E.kd.mean():.1f} 天;中位 dd {E.dd.median():.1f}%")
    lines.append(f"- 持有至窗末前即超過出關日者(T+10>窗末)比例未排除(報酬只看價格路徑)")
    # 舊制 OOS 排除新制
    old = E[E["T"] < NEW]
    medo = sorted(old["T"])[len(old)//2]
    E["seg"] = np.where(E["T"] >= NEW, "新制", np.where(E["T"] <= medo, "IS(舊制前半)", "OOS(舊制後半)"))
    lines.append(f"- IS/OOS 以舊制訊號日中位 {medo} 切分;新制另列")
    tab = ["| 子集 | 持有 | n | 淨超額0050 均(%) | 中位 | 勝率(淨超額>0) | t(日期叢集) |", "|---|---|---|---|---|---|---|"]
    tabg = ["| 子集 | 持有 | n | 毛報酬 均(%) | 中位 | 勝率(毛>0) | t |", "|---|---|---|---|---|---|---|"]
    for lab, d in (("全部", E), ("舊制全", E[E["T"] < NEW]), ("IS", E[E.seg == "IS(舊制前半)"]),
                   ("OOS", E[E.seg == "OOS(舊制後半)"]), ("新制", E[E.seg == "新制"])):
        d = d.assign(T=d["T"])
        for h in HS:
            tab.append(fmt(lab, d, f"ex{h}", h))
            tabg.append(fmt(lab, d, f"g{h}", h))
    lines += ["", "### 淨超額(扣0.44%、減0050同期)", *tab, "", "### 毛報酬(小哥口徑「反彈」:進場後收盤>進場開盤)", *tabg]
    # hit rate on raw>0 (宣稱 90%)
    for h in HS:
        lines.append(f"- h{h} 毛>0 比例 {(E[f'g{h}']>0).mean()*100:.0f}% (n={len(E)});淨(扣成本)>0 {(E[f'n{h}']>0).mean()*100:.0f}%;0050同期上漲比例(基準率) {(E[f'b{h}']>0).mean()*100:.0f}%")
    # 控制組: 同一條件(dd<=-20 & touch) 非處置日
    # 窗內任一日(含)排除:以 (sid,date) 集合
    inwin = set()
    Wall = pd.read_csv(DATA_DIR / "disposal" / "disposal_windows.csv", dtype=str)
    cs = B[B.sig & (B.d >= "2020-02-01") & (B.d <= "2026-09-30") & B.x10.notna()].copy()
    # 排除任何處置窗(任何 measure)內及窗前後各 1 個月內: 用 start/end 區間
    bysid = {k: g for k, g in Wall.groupby("stock_id")}
    def indisp(r):
        g = bysid.get(r.stock_id)
        if g is None: return False
        return bool(((g.start <= r.d) & (g.end >= r.d)).any())
    cs["indisp"] = cs.apply(indisp, axis=1)
    cs = cs[~cs.indisp]
    # 每檔每10日取首次(去叢集)
    cs = cs.sort_values(["stock_id", "d"])
    keep, last = [], {}
    pos = {d: i for i, d in enumerate(cal)}
    for r in cs.itertuples():
        i = pos.get(r.d)
        if i is None: continue
        if r.stock_id in last and i - last[r.stock_id] < 10: continue
        last[r.stock_id] = i; keep.append(r.Index)
    cs = cs.loc[keep].copy()
    for h in HS:
        def etfret2(r):
            try:
                return (etf.loc[etf.index[etf.index.get_loc(r.nd) + h - 1], "c"] / etf.loc[r.nd, "o"] - 1) * 100
            except Exception:
                return np.nan
        cs[f"b{h}"] = cs.apply(etfret2, axis=1)
        cs[f"g{h}"] = (cs[f"x{h}"] / cs.e - 1) * 100
        cs[f"ex{h}"] = cs[f"g{h}"] - COST - cs[f"b{h}"]
    cs = cs.dropna(subset=["ex5", "ex10"]); cs["T"] = cs.d
    ctab = ["| 子集 | 持有 | n | 淨超額 均 | 中位 | 勝率 | t |", "|---|---|---|---|---|---|---|"]
    for h in HS:
        ctab.append(fmt("對照:同條件非處置(每檔10日去叢集)", cs, f"ex{h}", h))
        ctab.append(fmt("對照 毛報酬", cs, f"g{h}", h).replace("對照 毛報酬", "對照 毛報酬"))
    lines += ["", "### 對照組(同價格條件、非處置期間)", *ctab]
    # 籌碼: 舊制才有 -> 不計算,報告註明
    # 有無處置的差(日期叢集 Welch 簡版)
    for h in HS:
        a, b = E[f"ex{h}"], cs[f"ex{h}"]
        lines.append(f"- h{h} 處置事件淨超額均 {a.mean():+.2f} vs 對照 {b.mean():+.2f};差 {a.mean()-b.mean():+.2f}pp(未做分群差檢定,n 差距大僅供參考)")
    # 敏感度: 僅 dd<=-20,不要求月線
    sens = []
    for w in W.itertuples():
        if w.stock_id not in have: continue
        g0 = SB[w.stock_id]
        s = g0[(g0.d >= w.start) & (g0.d <= w.end) & g0.sig_dd & g0.x10.notna()]
        if s.empty: continue
        r = s.iloc[0]
        sens.append(dict(T=r.d, nd=r.nd, e=r.e, x5=r.x5, x10=r.x10))
    S = pd.DataFrame(sens)
    if len(S):
        for h in HS:
            S[f"b{h}"] = S.apply(lambda r: (etf.loc[etf.index[etf.index.get_loc(r.nd) + h - 1], "c"] / etf.loc[r.nd, "o"] - 1) * 100 if r.nd in etf.index else np.nan, axis=1)
            S[f"g{h}"] = (S[f"x{h}"] / S.e - 1) * 100
            S[f"ex{h}"] = S[f"g{h}"] - COST - S[f"b{h}"]
        S = S.dropna(subset=["ex5", "ex10"])
        st = ["| 子集 | 持有 | n | 淨超額 均 | 中位 | 勝率 | t |", "|---|---|---|---|---|---|---|"]
        for h in HS:
            st.append(fmt("敏感度:只要dd≤-20%(不要月線)", S, f"ex{h}", h))
            st.append(fmt("敏感度 毛報酬", S, f"g{h}", h))
        lines += ["", "### 敏感度(放寬:不要求回測月線)", *st]
    print("\n".join(out + lines))
    E.to_csv(Path(__file__).with_suffix(".events.csv"), index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
