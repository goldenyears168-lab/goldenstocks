#!/usr/bin/env python3
"""把 30 格等量時鐘 + 連勝 k 換邊規則,直接套到台指期本身（TX 日盤）。

為什麼換標的：現股籃子上兩平成本卡在 11~14 bps，現貨(16.8 tick + 24 稅費)與
個股期貨(五檔價差 56 bps)都過不了；台指期一個 tick = 1 點，指數 ~45,000 時
只有 ≈0.22 bps，是唯一成本結構撐得住的商品。

資料：cache/tmf_channel/finmind_tx_tick_by_day/*.json（FinMind TaiwanFuturesTick，
1,010 天 2021-12 ~ 2026-08）。每日只取成交量最大的單一月份合約
（contract_date 含 '/' 的是跨月價差單，必須剔除）。
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "src")

SRC = Path("/Users/jackm4/goldenstocks-data/cache/tmf_channel/finmind_tx_tick_by_day")
CACHE = Path(os.path.expanduser("~/goldenstocks-data/data/cache/gap_open/txf_minute.parquet"))


def build_minutes(refresh: bool = True):
    """TX 日盤分鐘檔。快取存在時只增量處理比快取更新的日期（收盤後排程會每天多一天）。"""
    old = pd.read_parquet(CACHE) if CACHE.exists() else None
    if old is not None and not refresh:
        return old
    have = set(old.trade_date.unique()) if old is not None else set()
    rows = []
    files = [f for f in sorted(SRC.glob("*.json")) if f.stem not in have]
    if old is not None and not files:
        return old
    for i, f in enumerate(files):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if not d:
            continue
        df = pd.DataFrame(d)
        df = df[~df.contract_date.astype(str).str.contains("/")]     # 剔除跨月價差單
        if df.empty:
            continue
        top = df.groupby("contract_date").volume.sum().idxmax()      # 當日最大量月份
        df = df[df.contract_date == top]
        df["hm"] = df.date.str.slice(11, 16)
        df = df[(df.hm >= "08:45") & (df.hm <= "13:45")]             # 日盤
        if df.empty:
            continue
        df["mi"] = df.hm.str.slice(0, 2).astype(int) * 60 + df.hm.str.slice(3, 5).astype(int)
        g = df.groupby("mi").agg(close=("price", "last"), vol=("volume", "sum")).reset_index()
        g["trade_date"] = f.stem
        rows.append(g)
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(files)}", flush=True)
    m = pd.concat(rows, ignore_index=True) if rows else None
    if old is not None:
        m = pd.concat([old, m], ignore_index=True) if m is not None else old
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    m.to_parquet(CACHE)
    return m


def bucketize(m, N, mode="vol"):
    m = m.sort_values(["trade_date", "mi"]).copy()
    m = m[m.mi < 13 * 60 + 40]                     # 13:40 之後（收盤前量爆）不進時鐘
    if mode == "time":
        lo = m.groupby("trade_date").mi.transform("min")
        hi = m.groupby("trade_date").mi.transform("max")
        f = (m.mi - lo) / (hi - lo + 1e-9)
    else:
        cum = m.groupby("trade_date").val.cumsum()
        tot = m.groupby("trade_date").val.transform("sum")
        f = (cum - m.val * 0.5) / tot
    m["bk"] = np.minimum((f * N).astype(int), N - 1)
    g = m.groupby(["trade_date", "bk"]).agg(px=("close", "last"), end_mi=("mi", "max"),
                                            v=("val", "sum")).reset_index()
    g["r"] = g.groupby("trade_date").px.pct_change()
    return g.dropna(subset=["r"])


def strategy(d, k, cost_bps):
    res = []
    for date, g in d.groupby("trade_date"):
        rr = g.sort_values("bk").r.to_numpy()
        sig = 0; pos = []
        for i in range(len(rr)):
            if i >= k:
                w = rr[i - k:i]
                if (w > 0).all():
                    sig = 1
                elif (w < 0).all():
                    sig = -1
            pos.append(sig)
        pos = np.array(pos); pnl = pos * rr
        sw = int((np.abs(np.diff(np.r_[0, pos])) > 0).sum())
        res.append(dict(trade_date=date, gross=pnl.sum(), switches=sw,
                        net=pnl.sum() - sw * cost_bps / 1e4))
    return pd.DataFrame(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=2.0, help="每次換邊來回成本 bps")
    a = ap.parse_args()
    m = build_minutes()
    m["val"] = m.close * m.vol
    print(f"TX 日盤分鐘面板：{len(m):,} 列 / {m.trade_date.nunique()} 天 / "
          f"{m.trade_date.min()}~{m.trade_date.max()}")
    px = m.groupby("trade_date").close.last()
    print(f"一個 tick(1 點) 折算 bps：中位 {(1/px*1e4).median():.3f}"
          f"（指數中位 {px.median():,.0f}）→ 來回一次 ≈ {a.cost} bps 已含手續費")
    print(f"\n{'N':>4}{'clock':>7}{'k':>3}{'每日毛bps':>11}{'t':>7}{'每日換邊':>9}"
          f"{'兩平成本':>10}{'每日淨bps':>11}{'t':>7}{'勝率':>7}")
    best = []
    for N in (20, 30, 40):
        for mode in ("vol", "time"):
            d = bucketize(m, N, mode)
            for k in (2, 3, 4, 5, 6):
                s = strategy(d, k, a.cost)
                gt = s.gross.mean() / (s.gross.std(ddof=1) / np.sqrt(len(s)))
                nt = s.net.mean() / (s.net.std(ddof=1) / np.sqrt(len(s)))
                be = s.gross.mean() * 1e4 / s.switches.mean() if s.switches.mean() else np.nan
                print(f"{N:>4}{mode:>7}{k:>3}{s.gross.mean()*1e4:>11.1f}{gt:>7.2f}"
                      f"{s.switches.mean():>9.2f}{be:>10.1f}{s.net.mean()*1e4:>11.1f}{nt:>7.2f}"
                      f"{(s.net>0).mean()*100:>6.1f}%")
                best.append((nt, s.net.mean() * 1e4, N, mode, k, s, be))
    best.sort(reverse=True)
    print("\n最佳三組的逐年：")
    for nt, nm_, N, mode, k, s, be in best[:3]:
        print(f"  N={N} {mode} k={k}：每日淨 {nm_:+.1f} bps t={nt:+.2f} 兩平 {be:.1f} bps")
        s = s.copy(); s["yr"] = s.trade_date.str.slice(0, 4)
        for y, g in s.groupby("yr"):
            t = g.net.mean() / (g.net.std(ddof=1) / np.sqrt(len(g)))
            print(f"     {y} n={len(g):>3} 每日淨 {g.net.mean()*1e4:+6.1f} bps t={t:+5.2f}")


if __name__ == "__main__":
    main()
