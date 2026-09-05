#!/usr/bin/env python3
"""訊號＝監測清單「大戶未平倉損益」；切格＝該清單自己的等量成交；下單＝台指期。

跟前幾版的差別（照使用者定義修正）：
  · 切格用的是**監測清單自己的成交值**等分，不是大盤、不是固定時間
  · 每格觀察的是**大戶未平倉（未實現）損益**，不是價格、不是成交量
  · 連勝＝某一邊的未實現損益連續 k 格增加
  · 執行標的可以是台指期（TXF 分鐘檔已快取），訊號仍來自個股大戶
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, "scripts/research")
from stock_db import DATA_DIR
from biglot_unrealized import load, book          # noqa: E402
from txf_volume_clock import build_minutes        # noqa: E402

TPE = timezone(timedelta(hours=8))


def vol_buckets(df, N, res="sec"):
    """用監測清單自己的成交值切等量格。

    res="sec" 用逐筆的秒級時戳切（N>=40 時必要：早盤單一分鐘的成交值就可能超過 1/N，
    分鐘解析度會讓格子空掉）；res="min" 是舊行為，保留供回溯比對。
    回傳 index=秒(或分) -> 格編號。
    """
    df = df.copy()
    if res == "sec":
        key = (df.t.str.slice(0, 2).astype(int) * 3600 + df.t.str.slice(3, 5).astype(int) * 60
               + df.t.str.slice(6, 8).astype(int))
        cutoff = (13 * 60 + 25) * 60
    else:
        key = df.t.str.slice(0, 2).astype(int) * 60 + df.t.str.slice(3, 5).astype(int)
        cutoff = 13 * 60 + 25
    df["_k"] = key
    v = df.groupby("_k").apply(lambda g: (g.px * g.sz).sum(), include_groups=False)
    v = v[v.index < cutoff].sort_index()                  # 13:25 後不進時鐘
    cum = v.cumsum(); tot = v.sum()
    f = (cum - v * 0.5) / tot
    return pd.Series(np.minimum((f * N).astype(int), N - 1), index=v.index)


def run_day(date, thresh, N):
    df = load(date)
    df["mi"] = df.t.str.slice(0, 2).astype(int) * 60 + df.t.str.slice(3, 5).astype(int)
    big = df[(df.amt >= thresh) & (df.d != 0)]
    bk = vol_buckets(df, N)
    df["sec"] = (df.t.str.slice(0,2).astype(int)*3600
                 + df.t.str.slice(3,5).astype(int)*60 + df.t.str.slice(6,8).astype(int))
    df["bk"] = df.sec.map(bk); big = big.assign(bk=big.mi.map(bk))
    ends = df.dropna(subset=["bk"]).groupby("bk").mi.max()
    # 預先把每檔的 tape 與大單切成陣列,之後用 searchsorted 取前綴,避免重複掃全表
    tape = {s: (g.mi.to_numpy(), g.px.to_numpy())
            for s, g in df.sort_values(["sym", "mi"]).groupby("sym")}
    bigs = {s: (g.mi.to_numpy(), list(zip(g.d, g.sz, g.px)))
            for s, g in big.sort_values(["sym", "mi"]).groupby("sym")}
    rows = []
    for b in sorted(ends.index):
        cut = ends[b]
        recs = []
        for s, (bmi, trades) in bigs.items():
            j = int(np.searchsorted(bmi, cut, side="right"))
            if j < 5:
                continue
            tmi, tpx = tape[s]
            i = int(np.searchsorted(tmi, cut, side="right"))
            if i == 0:
                continue
            P = float(tpx[i - 1])
            _, _, pos, avg, _, _ = book(trades[:j])
            if pos == 0:
                continue
            recs.append((pos, avg, P))
        if not recs:
            continue
        L = [(p, a, x) for p, a, x in recs if p > 0]
        S = [(p, a, x) for p, a, x in recs if p < 0]
        lu = sum(p * 1000 * (x - a) for p, a, x in L)
        su = sum(p * 1000 * (x - a) for p, a, x in S)
        ln = sum(abs(p) * 1000 * a for p, a, x in L)
        sn = sum(abs(p) * 1000 * a for p, a, x in S)
        rows.append(dict(date=date, bk=int(b), end_mi=int(cut),
                         long_u=lu, short_u=su, nL=len(L), nS=len(S),
                         long_bps=lu / ln * 1e4 if ln else np.nan,
                         short_bps=su / sn * 1e4 if sn else np.nan))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=5_000_000)
    ap.add_argument("--n", type=int, default=30)
    a = ap.parse_args()
    tx = build_minutes()
    txm = tx.set_index(["trade_date", "mi"]).close
    out = []
    for date in ("2026-09-03", "2026-09-04"):
        r = run_day(date, a.thresh, a.n)
        r["txf"] = [txm.get((date, mi), np.nan) for mi in r.end_mi]
        r["txf"] = r.txf.ffill()
        r["txf_r"] = r.txf.pct_change() * 1e4
        r["d_long"] = r.long_u.diff(); r["d_short"] = r.short_u.diff()
        r["side"] = r.long_bps - r.short_bps
        out.append(r)
        print(f"\n{'='*100}\n### {date}  N={a.n} 等量格（清單自己的成交值）· 大戶未平倉損益")
        print(f"{'格':>3}{'結束':>7}{'多方未實現(萬)':>14}{'空方未實現(萬)':>14}"
              f"{'多方Δ':>9}{'空方Δ':>9}{'誰在賺':>7}{'連勝':>6}{'TXF':>9}{'次格TXF bps':>12}")
        w_l = w_s = 0
        for i, x in r.iterrows():
            if i > 0:
                w_l = w_l + 1 if x.d_long > 0 else 0
                w_s = w_s + 1 if x.d_short > 0 else 0
            who = "多" if (x.d_long or 0) > (x.d_short or 0) else "空"
            st = w_l if who == "多" else w_s
            nxt = r.txf_r.iloc[i + 1] if i + 1 < len(r) else np.nan
            print(f"{int(x.bk):>3}{int(x.end_mi)//60:>5}:{int(x.end_mi)%60:02d}"
                  f"{x.long_u/1e4:>14,.0f}{x.short_u/1e4:>14,.0f}"
                  f"{(x.d_long or 0)/1e4:>9,.0f}{(x.d_short or 0)/1e4:>9,.0f}{who:>7}{st:>6}"
                  f"{x.txf:>9,.0f}{nxt if nxt==nxt else float('nan'):>12.1f}")
    d = pd.concat(out, ignore_index=True)
    d["win"] = np.where(d.d_long > d.d_short, 1, -1)
    d["fwd"] = d.groupby("date").txf_r.shift(-1)
    ok = d.dropna(subset=["fwd", "d_long"])
    print(f"\n{'='*100}\n### 檢定（樣本 {len(ok)} 格 / 2 天 —— 樣本量不足,只能當管線驗證）")
    print(f"  同期 corr(Δside, 同格 TXF 報酬)   = {d.side.diff().corr(d.txf_r):+.3f}")
    print(f"  前瞻 corr(Δside, 次格 TXF 報酬)   = {d.side.diff().corr(d.fwd):+.3f}")
    x = ok.win * ok.fwd
    print(f"  跟贏的那一邊做次格 TXF：平均 {x.mean():+.1f} bps  t={x.mean()/(x.std(ddof=1)/np.sqrt(len(x))):+.2f}")
    for date, g in ok.groupby("date"):
        y = g.win * g.fwd
        print(f"    {date}  {y.mean():+.1f} bps  t={y.mean()/(y.std(ddof=1)/np.sqrt(len(y))):+.2f}  n={len(y)}")


if __name__ == "__main__":
    main()
