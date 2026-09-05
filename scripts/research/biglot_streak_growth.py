#!/usr/bin/env python3
"""在「連勝 k 格」之上再加「越賺越多」的條件。

變體（u = 該邊在該格的未實現；k=3 為例，t 是第三格）：
  A  連勝>=3                      （基準）
  B  連勝>=3 且 u[t] > u[t-2]      第三格 > 第一格
  C  連勝>=3 且 u[t] > u[t-1]      第三格 > 第二格
  D  連勝>=3 且 u[t] > u[t-1] > u[t-2]   單調遞增
  E  連勝>=3 且 u[t] > (u[t-1]+u[t-2])/2 高於前兩格平均
金額版與 bps 版（尺度無關）各跑一次。
資料來源＝biglot-bucket-eod 落的 CSV，所有變體都可事後從 CSV 重算，不必改收集。
"""
from __future__ import annotations
import argparse, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, "scripts/research")

CSV = os.path.expanduser("~/goldenstocks-data/data/cache/biglot_bucket/bucket_reset_n{n}.csv")


def variants(g, col_l, col_s, k):
    """回傳每格的訊號 dict（+1 多 / -1 空 / 0 無）"""
    L = g[col_l].to_numpy(); S = g[col_s].to_numpy()
    n = len(g)
    sl = np.zeros(n, int); ss = np.zeros(n, int)
    for i in range(n):
        sl[i] = (sl[i - 1] + 1) if i and L[i] > 0 else (1 if L[i] > 0 else 0)
        ss[i] = (ss[i - 1] + 1) if i and S[i] > 0 else (1 if S[i] > 0 else 0)
    out = {}
    def mk(cond_l, cond_s):
        v = np.zeros(n, int)
        for i in range(n):
            if sl[i] >= k and cond_l(i):
                v[i] = 1
            elif ss[i] >= k and cond_s(i):
                v[i] = -1
        return v
    ok = lambda i: True
    gt2 = lambda A: (lambda i: i >= 2 and A[i] > A[i - 2])
    gt1 = lambda A: (lambda i: i >= 1 and A[i] > A[i - 1])
    mono = lambda A: (lambda i: i >= 2 and A[i] > A[i - 1] > A[i - 2])
    avg = lambda A: (lambda i: i >= 2 and A[i] > (A[i - 1] + A[i - 2]) / 2)
    out["A 連勝>=k"] = mk(ok, ok)
    out["B 第3格>第1格"] = mk(gt2(L), gt2(S))
    out["C 第3格>第2格"] = mk(gt1(L), gt1(S))
    out["D 單調遞增"] = mk(mono(L), mono(S))
    out["E >前兩格平均"] = mk(avg(L), avg(S))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--cost", type=float, default=2.0)
    a = ap.parse_args()
    d = pd.read_csv(CSV.format(n=a.n), dtype={"date": str}).sort_values(["date", "bk"])
    d["txf_r"] = d.groupby("date").txf.pct_change() * 1e4
    d["fwd"] = d.groupby("date").txf_r.shift(-1)
    print(f"樣本 {len(d)} 格 / {d.date.nunique()} 天 / N={a.n} / k={a.k}")
    for scale, cl, cs in [("金額", "long_u", "short_u"), ("bps", "long_bps", "short_bps")]:
        print(f"\n### 用「{scale}」判斷越賺越多")
        print(f"{'變體':<16}{'訊號格/日':>10}{'每格bps':>9}{'t':>7}{'換邊':>9}"
              f"{'毛':>9}{'淨':>9}{'持有換邊':>9}{'持有毛':>9}{'持有淨':>9}   逐日(進出版)")
        sigs = {}
        for date, g in d.groupby("date"):
            g = g.reset_index(drop=True)
            for name, v in variants(g, cl, cs, a.k).items():
                sigs.setdefault(name, []).append((date, g, v))
        for name in ["A 連勝>=k", "B 第3格>第1格", "C 第3格>第2格", "D 單調遞增", "E >前兩格平均"]:
            allv, per, sws, ns = [], [], [], []
            hper, hsws = [], []
            for date, g, v in sigs[name]:
                # 持有版：訊號觸發後一直抱著,直到反向訊號才換（不因條件消失就出場）
                h = np.zeros(len(v), int); cur = 0
                for i, x in enumerate(v):
                    if x != 0:
                        cur = x
                    h[i] = cur
                fh = g.fwd.to_numpy()
                hper.append(np.nansum(h * np.nan_to_num(fh)))
                hsws.append(int((np.abs(np.diff(np.r_[0, h])) > 0).sum()))
                f = g.fwd.to_numpy()
                m = (~np.isnan(f)) & (v != 0)
                allv.append(v[m] * f[m])
                per.append(np.nansum(v * np.nan_to_num(f)))
                sws.append(int((np.abs(np.diff(np.r_[0, v])) > 0).sum()))
                ns.append(int((v != 0).sum()))
            x = np.concatenate(allv) if allv else np.array([])
            if len(x) < 3:
                print(f"{name:<16}{np.mean(ns):>10.1f}  n={len(x)} 太少"); continue
            t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
            gross = np.mean(per); net = gross - np.mean(sws) * a.cost
            hg = np.mean(hper); hn = hg - np.mean(hsws) * a.cost
            print(f"{name:<16}{np.mean(ns):>10.1f}{x.mean():>9.2f}{t:>7.2f}{np.mean(sws):>9.1f}"
                  f"{gross:>9.1f}{net:>9.1f}{np.mean(hsws):>9.1f}{hg:>9.1f}{hn:>9.1f}   " +
                  "  ".join(f"{dt[5:]} 進{p:+.0f}/持{hp:+.0f}"
                            for (dt, _, _), p, hp in zip(sigs[name], per, hper)))


if __name__ == "__main__":
    main()
