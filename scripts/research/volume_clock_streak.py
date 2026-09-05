#!/usr/bin/env python3
"""成交量時鐘（equal-volume bars）+ 連勝 k 次換邊規則。

為什麼用成交量切：早盤量大 → 同樣一格代表較短的鐘面時間（切得細），
午盤量小 → 一格涵蓋較長的鐘面時間（切得粗）。

規則（使用者版本）：
  某一邊連續 k 格賺錢 → 下一格站那一邊；另一邊連續 k 格賺錢就換邊。
  已證實「哪一邊在賺」≈ 籃子報酬的方向（同期 corr +0.923），
  所以這裡直接用籃子報酬的正負當「哪一邊在賺」，才能用 670 天的長史測。

輸出：掃 N（一天切幾格）× k（連勝幾次）的網格，含成本後淨值。
"""
from __future__ import annotations
import argparse, json, sqlite3, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src")
import stock_db
from stock_db import DATA_DIR


def load_panel():
    V3 = {x["sid"] for x in json.loads(
        (DATA_DIR / "cache/pit_universe_tick/_hivol_universe_v3.json").read_text())["universe"]}
    c = sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
    q = ("SELECT stock_id,trade_date,minute,close,volume FROM stock_kbar_1m "
         "WHERE source='finmind' AND stock_id IN (%s)" % ",".join("?" * len(V3)))
    k = pd.read_sql(q, c, params=sorted(V3))
    k["mi"] = k.minute.str.slice(0, 2).astype(int) * 60 + k.minute.str.slice(3, 5).astype(int)
    k = k[(k.mi >= 540) & (k.mi <= 810)]
    k["val"] = k.close * k.volume
    return k


def minute_panel(k):
    """一次算好：每 (date, mi) 的等權籃子分鐘報酬 與 成交值"""
    k = k.sort_values(["stock_id", "trade_date", "mi"])
    k["ret"] = k.groupby(["stock_id", "trade_date"]).close.pct_change(fill_method=None)
    m = k.groupby(["trade_date", "mi"]).agg(r=("ret", "mean"), val=("val", "sum")).reset_index()
    return m.dropna(subset=["r"])


def bucketize(m, N, mode):
    """向量化：回傳每 (date,bk) 的籃子報酬與該格結束分鐘"""
    m = m.copy()
    # 13:30 集合競價單一分鐘就佔全日成交值中位 6.33%（≈N=30 的兩格），
    # 不剔除的話尾端幾格會被它吞掉（N=30 時第 29 格 625/670 天是空的）。
    m = m[m.mi < 805]
    if mode == "time":
        lo = m.groupby("trade_date").mi.transform("min")
        hi = m.groupby("trade_date").mi.transform("max")
        f = (m.mi - lo) / (hi - lo + 1e-9)
    else:
        cum = m.groupby("trade_date").val.cumsum()
        tot = m.groupby("trade_date").val.transform("sum")
        f = (cum - m.val * 0.5) / tot     # 錨在該分鐘量的中點,跨格誤差比錨格首小 3 成
    m["bk"] = np.minimum((f * N).astype(int), N - 1)
    g = m.groupby(["trade_date", "bk"]).agg(r=("r", "sum"), end_mi=("mi", "max")).reset_index()
    return g.rename(columns={"trade_date": "date"})


def day_buckets(g, N, mode):
    """回傳每分鐘所屬的格編號（0..N-1）"""
    v = g.groupby("mi").val.sum().sort_index()
    if mode == "time":
        mi = v.index.to_numpy()
        return pd.Series(np.minimum(((mi - mi.min()) / (mi.max() - mi.min() + 1e-9) * N).astype(int),
                                    N - 1), index=v.index)
    cum = v.cumsum() / v.sum()
    return pd.Series(np.minimum((cum.to_numpy() * N).astype(int), N - 1), index=v.index)


def run(m, N, mode):
    return bucketize(m, N, mode)


def strategy(d, kstreak, cost_bps):
    res = []
    for date, g in d.groupby("date"):
        g = g.sort_values("bk").reset_index(drop=True)
        sig = 0; prev = 0; pos = []
        rr = g.r.to_numpy()
        for i in range(len(rr)):
            if i >= kstreak:
                w = rr[i - kstreak:i]
                if (w > 0).all():
                    sig = 1
                elif (w < 0).all():
                    sig = -1
            pos.append(sig)
        pos = np.array(pos)
        pnl = pos * rr
        sw = int((np.abs(np.diff(np.r_[0, pos])) > 0).sum())
        res.append(dict(date=date, gross=pnl.sum(), switches=sw,
                        net=pnl.sum() - sw * cost_bps / 1e4, n=len(rr)))
    return pd.DataFrame(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=16.8, help="每次換邊成本 bps")
    a = ap.parse_args()
    k = load_panel()
    m = minute_panel(k)
    print(f"1m 面板：{len(k):,} 列 / {k.stock_id.nunique()} 檔 / "
          f"{k.trade_date.nunique()} 天 / {k.trade_date.min()}~{k.trade_date.max()}")

    print("\n### 成交量時鐘長什麼樣（N=12，各格結束的鐘面時間中位數）")
    for N in (12,):
        d = run(m, N, "vol")
        med = d.groupby("bk").end_mi.median()
        print("  量時鐘: " + "  ".join(
            f"{i}:{int(m)//60:02d}:{int(m)%60:02d}" for i, m in med.items()))
        d2 = run(m, N, "time")
        med2 = d2.groupby("bk").end_mi.median()
        print("  時間鐘: " + "  ".join(
            f"{i}:{int(m)//60:02d}:{int(m)%60:02d}" for i, m in med2.items()))

    print(f"\n### 掃 N（一天幾格）× k（連勝幾次）· 每日毛/淨 bps · 換邊成本 {a.cost} bps")
    print(f"{'N':>4}{'clock':>7}{'k':>3}{'每日毛bps':>11}{'t':>7}{'每日換邊':>9}{'每日淨bps':>11}{'t':>7}")
    best = []
    for N in (6, 9, 12, 18, 27, 54):
        for mode in ("vol", "time"):
            d = run(m, N, mode)
            for ks in (2, 3, 4):
                s = strategy(d, ks, a.cost)
                gt = s.gross.mean() / (s.gross.std(ddof=1) / np.sqrt(len(s)))
                nt = s.net.mean() / (s.net.std(ddof=1) / np.sqrt(len(s)))
                print(f"{N:>4}{mode:>7}{ks:>3}{s.gross.mean()*1e4:>11.1f}{gt:>7.2f}"
                      f"{s.switches.mean():>9.2f}{s.net.mean()*1e4:>11.1f}{nt:>7.2f}")
                best.append((s.net.mean() * 1e4, nt, N, mode, ks, s.gross.mean() * 1e4,
                             s.switches.mean(), len(s)))
    best.sort(reverse=True)
    print("\n淨值最好的三組：")
    for x in best[:3]:
        print(f"  N={x[2]} {x[3]} k={x[4]}：毛 {x[5]:+.1f} / 換邊 {x[6]:.2f} 次 / "
              f"淨 {x[0]:+.1f} bps  t={x[1]:+.2f}  n={x[7]} 天")


if __name__ == "__main__":
    main()
