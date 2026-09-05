#!/usr/bin/env python3
"""大戶損益的三種算法對照：累積 / 每小時重算 / 固定持有期。

A 累積      ：09:00 起所有大單，mark 到該時點  → 現行版本
B 每小時重算：只算該小時內成交的大單，mark 到該小時結束
C 固定持有期：每筆大單成交後 +HOLD 分鐘 mark，再依「成交所在小時」彙總

A 的問題：同一批單被重複 mark，5 個時點不是 5 個獨立樣本；且時點越晚，早盤那批的權重越大。
B 的問題：09:59 成交的單只有 1 分鐘可以走，09:01 的有 59 分鐘 —— 窗內位置不同、不可比。
C 修掉 B 的問題：每筆單的持有時間一樣長。
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
sys.path.insert(0, "src")
from stock_db import DATA_DIR

TPE = timezone(timedelta(hours=8))
BUCKETS = [("09:00-10:00", "09:00:00", "10:00:00"), ("10:00-11:00", "10:00:00", "11:00:00"),
           ("11:00-12:00", "11:00:00", "12:00:00"), ("12:00-13:00", "12:00:00", "13:00:00"),
           ("13:00-13:30", "13:00:00", "13:30:01")]


def load(date):
    maxc = defaultdict(float); rows = []
    p = f"/Users/jackm4/goldenstocks-data/cache/biglot_live_watch/raw_{date}.jsonl"
    for line in open(p):
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
        sz = cum - maxc[sym] if maxc[sym] else float(d["size"])
        maxc[sym] = cum
        rows.append((sym, datetime.fromtimestamp(d["time"] / 1e6, TPE).strftime("%H:%M:%S"),
                     float(d["price"]), sz, float(d.get("bid") or np.nan),
                     float(d.get("ask") or np.nan)))
    df = pd.DataFrame(rows, columns=["sym", "t", "px", "sz", "bid", "ask"]).sort_values(["sym", "t"])
    df["d"] = np.where(df.px >= df.ask, 1.0, np.where(df.px <= df.bid, -1.0, 0.0))
    df["amt"] = df.px * df.sz * 1000
    df["sec"] = df.t.str.slice(0, 2).astype(int) * 3600 + df.t.str.slice(3, 5).astype(int) * 60 \
        + df.t.str.slice(6, 8).astype(int)
    return df


def px_at(g, sec):
    gg = g[g.sec <= sec]
    return float(gg.px.iloc[-1]) if len(gg) else np.nan


def run(date, thresh, hold_min):
    df = load(date)
    big = df[(df.amt >= thresh) & (df.d != 0)]
    A, B, C = [], [], []
    for s, g in df.groupby("sym"):
        b = big[big.sym == s]
        if len(b) < 10:
            continue
        gs = g.sort_values("sec")
        secs = gs.sec.to_numpy(); pxs = gs.px.to_numpy()
        for lab, t0, t1 in BUCKETS:
            e = int(t1[:2]) * 3600 + int(t1[3:5]) * 60
            P = px_at(gs, e)
            if P != P:
                continue
            cum = b[b.t < t1]
            if len(cum):
                A.append(dict(date=date, sym=s, bucket=lab,
                              pnl=float((cum.d * cum.sz * 1000 * (P - cum.px)).sum()),
                              notional=float((cum.sz * 1000 * cum.px).sum()), n=len(cum)))
            win = b[(b.t >= t0) & (b.t < t1)]
            if len(win):
                B.append(dict(date=date, sym=s, bucket=lab,
                              pnl=float((win.d * win.sz * 1000 * (P - win.px)).sum()),
                              notional=float((win.sz * 1000 * win.px).sum()), n=len(win)))
                j = np.searchsorted(secs, win.sec.to_numpy() + hold_min * 60, side="right") - 1
                j = np.clip(j, 0, len(pxs) - 1)
                Pm = pxs[j]
                C.append(dict(date=date, sym=s, bucket=lab,
                              pnl=float((win.d.to_numpy() * win.sz.to_numpy() * 1000 *
                                         (Pm - win.px.to_numpy())).sum()),
                              notional=float((win.sz * 1000 * win.px).sum()), n=len(win)))
    out = {}
    for k, v in [("A累積", A), ("B每小時重算", B), (f"C固定持有{hold_min}分", C)]:
        r = pd.DataFrame(v)
        r["bps"] = r.pnl / r.notional * 1e4
        out[k] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=5_000_000)
    ap.add_argument("--hold", type=int, default=30)
    a = ap.parse_args()
    uni = json.loads((DATA_DIR / "cache/pit_universe_tick/_hivol_universe_v3.json").read_text())
    V3 = {x["sid"]: x["name"] for x in uni["universe"]}
    res = {d: run(d, a.thresh, a.hold) for d in ("2026-09-03", "2026-09-04")}

    for k in res["2026-09-04"]:
        print(f"\n{'='*88}\n### {k} —— 2026-09-04 各時段大戶損益（萬元）")
        r = res["2026-09-04"][k]
        piv = r.pivot_table(index="sym", columns="bucket", values="pnl", aggfunc="sum") / 1e4
        piv = piv.reindex(columns=[b[0] for b in BUCKETS])
        piv["合計"] = piv.sum(axis=1) if k != "A累積" else piv[BUCKETS[-1][0]]
        piv["name"] = [V3.get(i, "") for i in piv.index]
        piv["★"] = ["★" if i in V3 else "" for i in piv.index]
        t = piv.sort_values("合計", ascending=False)
        print(pd.concat([t.head(6), t.tail(4)])[
            ["★", "name"] + [b[0] for b in BUCKETS] + ["合計"]].to_string(float_format=lambda x: f"{x:,.0f}"))
        print(f"  全體各時段合計(萬): " + "  ".join(
            f"{b[0]}={r[r.bucket==b[0]].pnl.sum()/1e4:,.0f}" for b in BUCKETS))

    print(f"\n{'='*88}\n### 統計性質對照")
    print(f"{'算法':<16}{'相鄰時段相關':>14}{'跨日Spearman':>14}{'單檔佔全體|損益|最大比':>22}")
    for k in res["2026-09-04"]:
        r4 = res["2026-09-04"][k]; r3 = res["2026-09-03"][k]
        p = r4.pivot_table(index="sym", columns="bucket", values="bps")
        cols = [b[0] for b in BUCKETS if b[0] in p.columns]
        ac = np.nanmean([p[cols[i]].corr(p[cols[i + 1]]) for i in range(len(cols) - 1)])
        agg4 = r4.groupby("sym").apply(lambda x: x.pnl.sum() / x.notional.sum() * 1e4, include_groups=False)
        agg3 = r3.groupby("sym").apply(lambda x: x.pnl.sum() / x.notional.sum() * 1e4, include_groups=False)
        j = pd.concat([agg3, agg4], axis=1, join="inner")
        sp = j.iloc[:, 0].corr(j.iloc[:, 1], method="spearman")
        tot = r4.groupby("sym").pnl.sum()
        share = tot.abs().max() / tot.abs().sum()
        print(f"{k:<16}{ac:>14.3f}{sp:>14.3f}{share*100:>21.1f}%")


if __name__ == "__main__":
    main()
