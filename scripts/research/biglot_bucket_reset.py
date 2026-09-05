#!/usr/bin/env python3
"""每格歸零重算版：只看該格內成交的大單，在格末的未實現損益。

跟累積版的差別（照使用者定義）：
  累積版 = 一本從 09:00 開始的帳，未實現含整天堆起來的部位 → 會被舊部位綁住
  歸零版 = 每格帳歸零，只算該格內建立的淨部位在格末賺不賺 → 「現在正在賺錢」

  多方未實現 = Σ(該格內淨多的檔) 淨部位 × 1000 × (格末價 − 該格內平均成本)
  空方未實現 = 同理，淨空的檔
  連勝 = 某一邊連續 k 格未實現 > 0
切格：監測清單自己的成交值等分（N 格）。下單標的：台指期。
"""
from __future__ import annotations
import argparse, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, "scripts/research")
from biglot_unrealized import load, book              # noqa: E402
from biglot_unrealized_volclock import vol_buckets    # noqa: E402
from txf_volume_clock import build_minutes            # noqa: E402


def run_day(date, thresh, N):
    df = load(date)
    df["mi"] = df.t.str.slice(0, 2).astype(int) * 60 + df.t.str.slice(3, 5).astype(int)
    bk = vol_buckets(df, N)          # 秒級解析度（N>=40 用分鐘會有空格）
    df["sec"] = (df.t.str.slice(0, 2).astype(int) * 3600
                 + df.t.str.slice(3, 5).astype(int) * 60 + df.t.str.slice(6, 8).astype(int))
    df["bk"] = df.sec.map(bk)
    big = df[(df.amt >= thresh) & (df.d != 0) & df.bk.notna()]
    ends = df.dropna(subset=["bk"]).groupby("bk").mi.max()
    tape = {s: (g.mi.to_numpy(), g.px.to_numpy())
            for s, g in df.sort_values(["sym", "mi"]).groupby("sym")}
    rows = []
    for b in sorted(ends.index):
        cut = int(ends[b])
        L = S = 0.0; ln = sn = 0.0; nl = ns = 0; nt = 0
        for s, g in big[big.bk == b].groupby("sym"):
            if len(g) < 2:
                continue
            tmi, tpx = tape[s]
            i = int(np.searchsorted(tmi, cut, side="right"))
            if i == 0:
                continue
            P = float(tpx[i - 1])
            _, _, pos, avg, _, _ = book(list(zip(g.d, g.sz, g.px)))
            nt += len(g)
            if pos == 0:
                continue
            u = pos * 1000 * (P - avg)
            if pos > 0:
                L += u; ln += pos * 1000 * avg; nl += 1
            else:
                S += u; sn += -pos * 1000 * avg; ns += 1
        rows.append(dict(date=date, bk=int(b), end_mi=cut, long_u=L, short_u=S,
                         long_bps=L / ln * 1e4 if ln else np.nan,
                         short_bps=S / sn * 1e4 if sn else np.nan,
                         nL=nl, nS=ns, n_big=nt))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=5_000_000)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--date", default=None, help="只跑單日（收盤後排程用）")
    ap.add_argument("--append-csv", default=None, help="把每格結果 append 到這個 CSV（依日期去重）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    try:
        tx = build_minutes(); txm = tx.set_index(["trade_date", "mi"]).close
    except Exception as e:                      # TXF 快取缺料不擋大戶那一半
        print(f"WARN: TXF 分鐘檔讀不到（{type(e).__name__}），txf 欄位留空")
        txm = pd.Series(dtype=float)
    out = []
    dates = (a.date,) if a.date else ("2026-09-03", "2026-09-04")
    for date in dates:
        r = run_day(date, a.thresh, a.n)
        r["txf"] = [txm.get((date, mi), np.nan) if len(txm) else np.nan for mi in r.end_mi]
        r["txf"] = r.txf.ffill()
        r["txf_r"] = r.txf.pct_change() * 1e4
        wl = ws = 0; SL = []; SS = []
        for _, x in r.iterrows():
            wl = wl + 1 if x.long_u > 0 else 0
            ws = ws + 1 if x.short_u > 0 else 0
            SL.append(wl); SS.append(ws)
        r["streak_L"] = SL; r["streak_S"] = SS
        out.append(r)
        if a.quiet:
            continue
        print(f"\n{'='*104}\n### {date}  N={a.n} 等量格 · **每格歸零** · 大戶該格未實現")
        print(f"{'格':>3}{'結束':>7}{'大單筆':>7}{'多方(萬)':>10}{'空方(萬)':>10}"
              f"{'多bps':>8}{'空bps':>8}{'多連勝':>7}{'空連勝':>7}{'TXF':>9}{'次格bps':>9}")
        for i, x in r.iterrows():
            nxt = r.txf_r.iloc[i + 1] if i + 1 < len(r) else np.nan
            print(f"{int(x.bk):>3}{int(x.end_mi)//60:>5}:{int(x.end_mi)%60:02d}{int(x.n_big):>7}"
                  f"{x.long_u/1e4:>10,.0f}{x.short_u/1e4:>10,.0f}"
                  f"{x.long_bps if x.long_bps==x.long_bps else float('nan'):>8.0f}"
                  f"{x.short_bps if x.short_bps==x.short_bps else float('nan'):>8.0f}"
                  f"{int(x.streak_L):>7}{int(x.streak_S):>7}{x.txf:>9,.0f}"
                  f"{nxt if nxt==nxt else float('nan'):>9.1f}")
    d = pd.concat(out, ignore_index=True)
    if a.append_csv:
        from pathlib import Path as _P
        f = _P(a.append_csv); f.parent.mkdir(parents=True, exist_ok=True)
        cols = ["date", "bk", "end_mi", "n_big", "long_u", "short_u", "long_bps",
                "short_bps", "nL", "nS", "streak_L", "streak_S", "txf"]
        new = d[cols].copy()
        if f.exists():
            old = pd.read_csv(f, dtype={"date": str})
            new = pd.concat([old[~old.date.isin(new.date.unique())], new], ignore_index=True)
        new.sort_values(["date", "bk"]).to_csv(f, index=False)
        print(f"append → {f}（{len(new)} 列 / {new.date.nunique()} 天）")
    if a.quiet:
        return
    d["fwd"] = d.groupby("date").txf_r.shift(-1)
    d["sig"] = np.where(d.streak_L >= a.k, 1, np.where(d.streak_S >= a.k, -1, 0))
    d["win"] = np.where(d.long_u > d.short_u, 1, -1)
    ok = d.dropna(subset=["fwd"])
    print(f"\n{'='*104}\n### 檢定（{len(ok)} 格 / 2 天 —— 只能當管線驗證,不是結論）")
    print(f"  同期 corr(多方bps−空方bps, 同格 TXF)  = {(d.long_bps-d.short_bps).corr(d.txf_r):+.3f}"
          f"   ← 累積版是 +0.721")
    print(f"  前瞻 corr(同上, 次格 TXF)             = {(d.long_bps-d.short_bps).corr(d.fwd):+.3f}")
    for lab, v in [("跟該格贏的一邊", ok.win * ok.fwd),
                   (f"連勝>={a.k} 才進場", (ok.sig * ok.fwd)[ok.sig != 0])]:
        if len(v) < 3:
            print(f"  {lab}: n={len(v)} 太少"); continue
        print(f"  {lab:<16} n={len(v):>3}  平均 {v.mean():+6.2f} bps  "
              f"t={v.mean()/(v.std(ddof=1)/np.sqrt(len(v))):+5.2f}")
    for date, g in ok.groupby("date"):
        v = (g.sig * g.fwd)[g.sig != 0]
        w = g.win * g.fwd
        print(f"    {date}  跟贏邊 {w.mean():+5.2f} (t={w.mean()/(w.std(ddof=1)/np.sqrt(len(w))):+5.2f})"
              f"   連勝>={a.k} n={len(v)} {v.mean() if len(v) else float('nan'):+5.2f}")
    print(f"\n  訊號覆蓋率：連勝>={a.k} 有訊號的格數 {int((d.sig!=0).sum())}/{len(d)}"
          f"（多 {int((d.sig>0).sum())} / 空 {int((d.sig<0).sum())}）")
    sd = (ok.win * ok.fwd).std(ddof=1)
    print(f"  每格報酬 sd {sd:.1f} bps → t=2 需要：效應 1 bps/格 → "
          f"{(2*sd/1)**2/28:.0f} 天；2 bps → {(2*sd/2)**2/28:.0f} 天；3 bps → {(2*sd/3)**2/28:.0f} 天")


if __name__ == "__main__":
    main()
