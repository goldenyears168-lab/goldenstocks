#!/usr/bin/env python3
"""大戶「未實現」損益 —— 把大單當成一本帳（不是一筆一筆獨立）。

跟前一版的差別：前一版把每筆大單當成各自獨立的部位全部 mark（flow view），
所以買 1.7 萬張、賣 1.9 萬張會被算成兩本帳。這一版按時間序做加權平均成本記帳：
  · 同向加碼 → 更新平均成本
  · 反向 → 先平掉（進「已實現」），超出部分翻方向、成本重設
  · **未實現 = 剩餘淨部位 × 1000 × (mark 價 − 平均成本)**

A 累積：09:00 起同一本帳，每個整點看當時的未實現
B 每小時重算：每個小時開始把帳歸零，只看該小時內建立的淨部位在小時結束時的未實現
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
sys.path.insert(0, "src")
from stock_db import DATA_DIR

TPE = timezone(timedelta(hours=8))
MARKS = ["10:00", "11:00", "12:00", "13:00", "13:30"]
BUCKETS = [("09:00-10:00", "09:00:00", "10:00:00"), ("10:00-11:00", "10:00:00", "11:00:00"),
           ("11:00-12:00", "11:00:00", "12:00:00"), ("12:00-13:00", "12:00:00", "13:00:00"),
           ("13:00-13:30", "13:00:00", "13:30:01")]


def load(date):
    maxc = defaultdict(float); rows = []
    for line in open(f"/Users/jackm4/goldenstocks-data/cache/biglot_live_watch/raw_{date}.jsonl"):
        if '"trades"' not in line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        pl = m.get("payload") or {}
        if pl.get("event") != "data" or pl.get("channel") != "trades":
            continue
        d = pl["data"]; sym = str(d.get("symbol")); cum = d.get("volume")
        if cum is None:
            continue
        cum = float(cum)
        if cum <= maxc[sym]:
            continue
        # 單筆量一律用 Δvolume（含第一筆：cum 本身就是從 0 起算的增量）。
        # 不可退回 data.size —— 富邦 ws 的 size 不是單筆量、且有殭屍重送
        # （2026-09-06 實測 Σsize 為真實日量 3~4 倍，用它做 Lee-Ready 會把方向算反）。
        sz = cum - maxc[sym]
        maxc[sym] = cum
        rows.append((sym, datetime.fromtimestamp(d["time"] / 1e6, TPE).strftime("%H:%M:%S"),
                     float(d["price"]), sz, float(d.get("bid") or np.nan),
                     float(d.get("ask") or np.nan),
                     bool(d.get("isOpen") or d.get("isClose"))))
    df = pd.DataFrame(rows, columns=["sym", "t", "px", "sz", "bid", "ask", "auction"]).sort_values(["sym", "t"])
    # 開/收盤集合競價是單一價，內外盤無意義 → 只算量、方向記 0（不進大戶多空）。
    # 13:30 那一撮佔全日成交值中位 6.33%，不排除會系統性污染方向。
    # ⚠ 只能用 isOpen / isClose 判斷，**不可**要求 isContinuous is True ——
    #   處置/分盤股（每 N 分鐘集合競價）的真成交完全不帶 isContinuous 旗標：
    #   2026-09-08 實測金居 8358、全新 2455、精材 3374、雙鴻 3324、大立光 3008、
    #   玉晶光 3406 六檔 100% 缺旗標，正檢查會讓這六檔整檔方向歸零、從多空分析中消失。
    df["d"] = np.where(df.auction, 0.0,
                       np.where(df.px >= df.ask, 1.0, np.where(df.px <= df.bid, -1.0, 0.0)))
    df["amt"] = df.px * df.sz * 1000
    return df


def book(trades):
    """加權平均成本記帳。回傳 (已實現_多單平倉, 已實現_空單回補, 淨部位張, 平均成本,
    多單累計平倉張, 空單累計回補張)"""
    pos = 0.0; avg = 0.0; rL = 0.0; rS = 0.0; qL = 0.0; qS = 0.0
    for d, v, p in trades:
        q = d * v
        if pos == 0 or np.sign(q) == np.sign(pos):
            avg = (avg * abs(pos) + p * v) / (abs(pos) + v)
            pos += q
        else:
            close = min(abs(pos), v)
            if pos > 0:                      # 手上是多單，被賣出平掉
                rL += close * 1000 * (p - avg); qL += close
            else:                            # 手上是空單，被買回補掉
                rS += close * 1000 * (avg - p); qS += close
            pos += np.sign(q) * close
            rem = v - close
            if rem > 0:
                pos = np.sign(q) * rem; avg = p
            elif pos == 0:
                avg = 0.0
    return rL, rS, pos, avg, qL, qS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-04")
    ap.add_argument("--thresh", type=float, default=5_000_000)
    a = ap.parse_args()
    df = load(a.date)
    big = df[(df.amt >= a.thresh) & (df.d != 0)]
    uni = json.loads((DATA_DIR / "cache/pit_universe_tick/_hivol_universe_v3.json").read_text())
    V3 = {x["sid"]: x["name"] for x in uni["universe"]}
    A, B = [], []
    for s, g in df.groupby("sym"):
        b = big[big.sym == s]
        if len(b) < 10:
            continue
        for mk in MARKS:
            P = g[g.t <= mk + ":59"]
            if not len(P):
                continue
            P = float(P.px.iloc[-1])
            cum = b[b.t <= mk + ":59"]
            if not len(cum):
                continue
            rL, rS, pos, avg, qL, qS = book(list(zip(cum.d, cum.sz, cum.px)))
            real = rL + rS
            A.append(dict(sym=s, mark=mk, 已實現=real, 已實現多=rL, 已實現空=rS, 淨部位=pos, 平均成本=avg, 市價=P,
                          未實現=pos * 1000 * (P - avg) if pos else 0.0, n=len(cum)))
        for lab, t0, t1 in BUCKETS:
            P = g[g.t < t1]
            if not len(P):
                continue
            P = float(P.px.iloc[-1])
            w = b[(b.t >= t0) & (b.t < t1)]
            if not len(w):
                continue
            rL, rS, pos, avg, qL, qS = book(list(zip(w.d, w.sz, w.px)))
            real = rL + rS
            B.append(dict(sym=s, bucket=lab, 已實現=real, 已實現多=rL, 已實現空=rS, 淨部位=pos, 平均成本=avg, 市價=P,
                          未實現=pos * 1000 * (P - avg) if pos else 0.0, n=len(w)))
    ra, rb = pd.DataFrame(A), pd.DataFrame(B)
    for r in (ra, rb):
        r["name"] = r.sym.map(lambda x: V3.get(x, ""))
        r["★"] = r.sym.map(lambda x: "★" if x in V3 else "")

    print(f"=== {a.date}  A｜從開盤累積：各整點的**未實現**損益（萬元）===")
    p = ra.pivot_table(index="sym", columns="mark", values="未實現", aggfunc="sum")[MARKS] / 1e4
    last = ra[ra["mark"] == "13:30"].set_index("sym")
    p["收盤淨部位(張)"] = last.淨部位; p["平均成本"] = last.平均成本; p["收盤"] = last.市價
    p["已實現(收盤)"] = last.已實現 / 1e4
    p["name"] = [V3.get(i, "") for i in p.index]; p["★"] = ["★" if i in V3 else "" for i in p.index]
    t = p.sort_values("13:30", ascending=False)
    print(pd.concat([t.head(8), t.tail(6)])[["★", "name"] + MARKS +
          ["收盤淨部位(張)", "平均成本", "收盤", "已實現(收盤)"]].to_string(float_format=lambda x: f"{x:,.1f}"))
    print(f"  全體未實現合計(萬)：" + "  ".join(
        f"{m}={ra[ra['mark']==m].未實現.sum()/1e4:,.0f}" for m in MARKS))
    print(f"  收盤：已實現合計 {last.已實現.sum()/1e4:,.0f} 萬 · 未實現合計 "
          f"{(last.淨部位*1000*(last.市價-last.平均成本)).sum()/1e4:,.0f} 萬")

    print(f"\n=== {a.date}  B｜每小時重算：該小時建立的淨部位、在小時結束時的**未實現**（萬元）===")
    q = rb.pivot_table(index="sym", columns="bucket", values="未實現", aggfunc="sum")
    q = q.reindex(columns=[x[0] for x in BUCKETS]) / 1e4
    q["合計"] = q.sum(axis=1)
    q["name"] = [V3.get(i, "") for i in q.index]; q["★"] = ["★" if i in V3 else "" for i in q.index]
    t = q.sort_values("合計", ascending=False)
    print(pd.concat([t.head(8), t.tail(6)])[["★", "name"] + [x[0] for x in BUCKETS] + ["合計"]]
          .to_string(float_format=lambda x: f"{x:,.0f}"))
    print("  全體各時段未實現合計(萬)：" + "  ".join(
        f"{x[0]}={rb[rb.bucket==x[0]].未實現.sum()/1e4:,.0f}" for x in BUCKETS))
    print("  全體各時段已實現合計(萬)：" + "  ".join(
        f"{x[0]}={rb[rb.bucket==x[0]].已實現.sum()/1e4:,.0f}" for x in BUCKETS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
