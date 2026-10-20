#!/usr/bin/env python3
"""隔日沖慣性全市場檢定·階段 1：逐日掃描全市場分點 tape，產生大買事件與事前已知的慣性計數（協議：docs/warrant-xiaoge/flip-habit-study-protocol.md）。

為何逐日而不是逐股：`stock_broker_branch_daily` 逐股讀取要掃散落在 52GB 檔案各處的列（單股約 46 秒），
逐日讀取是循序讀（單日約 3 秒）。唯讀；輸出 `${GOLDENSTOCKS_DATA_DIR}/research/xiaoge_flip_habit/events.pkl`。
事件 (s, t, b)：分點 b 在 t 日為股 s 淨買前 5 名、淨買 ≥ 1% 當日總買進量（超集；主定義再用 share≥3% 篩）；
t 與 t+1 兩日該股分點 tape 都要「完整」（買進合計 ≥ 成交量 95%）才納入。
用法：nice PYTHONPATH=src .venv/bin/python scripts/research/xiaoge_flip_habit_events.py [--days N] [--workers 4]
"""
from __future__ import annotations

import argparse
import pickle
import sqlite3
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

DB = Path.home() / "goldenstocks-data/data/stocks.db"
OUT = Path.home() / "goldenstocks-data/research/xiaoge_flip_habit"
START, END = "2024-07-01", "2026-07-16"
COMPLETE = 0.95
TOPN, MINSH_MIN = 5, 0.01
ALPHAS = (0.5, 0.7, 0.9)
WINDOWS = (60, 90, 180)
FILTERS = {"p": (5, 0.03), "t3": (3, 0.03), "m1": (5, 0.01), "m5": (5, 0.05)}

_BARS = None  # (date → DataFrame[stock_id, volume])


def _init(bars):
    global _BARS
    _BARS = bars


def _load_day(c, d):
    df = pd.read_sql_query(
        "select stock_id, securities_trader_id as br, buy, sell from stock_broker_branch_daily where trade_date=?", c, params=(d,))
    df["buy"] = df["buy"].fillna(0.0); df["sell"] = df["sell"].fillna(0.0)
    return df


def _day_stats(df, d):
    """回傳 (df 僅完整股票, 各股 vol)。"""
    vol = df.groupby("stock_id")["buy"].sum()
    bv = _BARS[d].reindex(vol.index)
    ok = (vol >= COMPLETE * bv) & (bv > 0)
    keep = ok[ok.fillna(False)].index
    return df[df["stock_id"].isin(keep)].copy(), vol[keep]


def chunk(args):
    cal, lo, hi = args           # 處理 cal[lo:hi] 各日 t（需要 t+1，故 hi ≤ len(cal)-1）
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.execute("pragma query_only=1")
    out = []
    cur, cur_vol = _day_stats(_load_day(c, cal[lo]), cal[lo])
    for i in range(lo, hi):
        t, nt = cal[i], cal[i + 1]
        nxt, _ = _day_stats(_load_day(c, nt), nt)
        cur["net"] = cur["buy"] - cur["sell"]
        cur["rank"] = cur.groupby("stock_id")["net"].rank(method="first", ascending=False)
        ev = cur[(cur["rank"] <= TOPN) & (cur["net"] > 0)].copy()
        ev["vol"] = ev["stock_id"].map(cur_vol)
        ev = ev[ev["net"] >= MINSH_MIN * ev["vol"]]
        nx = nxt.set_index(["stock_id", "br"])[["buy", "sell"]]
        nstocks = set(nxt["stock_id"].unique())
        ev = ev[ev["stock_id"].isin(nstocks)]                      # t+1 該股也要完整
        got = nx.reindex(pd.MultiIndex.from_arrays([ev["stock_id"].to_numpy(), ev["br"].to_numpy()]))
        ev["buy_n"] = got["buy"].fillna(0.0).to_numpy()
        ev["sell_n"] = got["sell"].fillna(0.0).to_numpy()
        ev["idx"] = i
        out.append(ev[["stock_id", "br", "idx", "net", "vol", "rank", "buy_n", "sell_n"]])
        cur, cur_vol = nxt, None
        cur_vol = nxt.groupby("stock_id")["buy"].sum()
    c.close()
    return pd.concat(out, ignore_index=True)


def features(ev: pd.DataFrame, cal: list[str]) -> pd.DataFrame:
    ev = ev.copy()
    ev["share"] = ev["net"] / ev["vol"]
    ev["net_n"] = ev["buy_n"] - ev["sell_n"]
    for a in ALPHAS:
        ev[f"fA{int(a*100)}"] = ev["sell_n"] >= a * ev["net"]
        ev[f"fB{int(a*100)}"] = ev["net_n"] <= -a * ev["net"]
    tord = np.array([pd.Timestamp(d).toordinal() for d in cal])
    ev["tord"] = tord[ev["idx"].to_numpy()]
    ev["gid"] = pd.factorize(ev["stock_id"] + "|" + ev["br"])[0]
    for fname, (topn, ms) in FILTERS.items():
        m = ((ev["rank"] <= topn) & (ev["share"] >= ms)).to_numpy()
        ev[f"in_{fname}"] = m
        sub = ev[m].sort_values(["gid", "idx"])
        gid = sub["gid"].to_numpy().astype(np.int64)
        idx = sub["idx"].to_numpy().astype(np.int64)
        td = sub["tord"].to_numpy().astype(np.int64)
        k_idx = gid * 100000 + idx
        k_td = gid * 10**8 + td
        wins = WINDOWS if fname == "p" else (90,)
        als = ALPHAS if fname == "p" else (0.7,)
        # 群起點（lo 不可跨群）
        starts = np.searchsorted(gid, gid, side="left")
        hi = np.searchsorted(k_idx, gid * 100000 + idx - 2, side="right")
        for w in wins:
            lo = np.searchsorted(k_td, gid * 10**8 + td - w, side="left")
            lo = np.maximum(lo, starts)
            hi_w = np.maximum(hi, lo)
            n = hi_w - lo
            col_n = np.zeros(len(ev), dtype=np.int32)
            col_n[sub.index.to_numpy()] = n
            ev[f"n{w}_{fname}"] = col_n
            for kind in ("A", "B"):
                for a in als:
                    f = sub[f"f{kind}{int(a*100)}"].to_numpy().astype(np.int64)
                    cs = np.concatenate([[0], np.cumsum(f)])
                    cnt = cs[hi_w] - cs[lo]
                    col = np.zeros(len(ev), dtype=np.int32)
                    col[sub.index.to_numpy()] = cnt
                    ev[f"{kind}{int(a*100)}_{w}_{fname}"] = col
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.execute("pragma query_only=1")
    bars = pd.read_sql_query(
        "select stock_id, trade_date, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') "
        "and trade_date between ? and ? and stock_id glob '[1-9][0-9][0-9][0-9]'", c, params=(START, "2026-07-20"))
    c.close()
    bars = bars.drop_duplicates(["stock_id", "trade_date"])
    cnt = bars.groupby("trade_date")["stock_id"].count()
    cal = sorted(d for d in cnt.index if START <= d <= END and cnt[d] >= 1000)
    if a.days:
        cal = cal[: a.days + 1]
    bmap = {d: g.set_index("stock_id")["volume"] for d, g in bars.groupby("trade_date")}
    n = len(cal) - 1
    step = max(1, (n + a.workers * 3 - 1) // (a.workers * 3))
    jobs = [(cal, lo, min(lo + step, n)) for lo in range(0, n, step)]
    print("交易日", len(cal), "任務", len(jobs), flush=True)
    t0 = time.time()
    parts = []
    with Pool(a.workers, initializer=_init, initargs=(bmap,)) as pool:
        for k, r in enumerate(pool.imap_unordered(chunk, jobs), 1):
            parts.append(r)
            print(f"{k}/{len(jobs)} {time.time()-t0:.0f}s 事件 {sum(len(p) for p in parts):,}", flush=True)
    ev = pd.concat(parts, ignore_index=True).sort_values(["stock_id", "br", "idx"]).reset_index(drop=True)
    print("事件", len(ev), "股票", ev["stock_id"].nunique(), "分點", ev["br"].nunique(), flush=True)
    ev = features(ev, cal)
    print("特徵完成", f"{time.time()-t0:.0f}s", flush=True)
    with open(OUT / ("events_test.pkl" if a.days else "events.pkl"), "wb") as f:
        pickle.dump({"events": ev, "cal": cal}, f, protocol=4)
    print("done", flush=True)


if __name__ == "__main__":
    main()
