#!/usr/bin/env python3
"""「平倉後可再次交易」回測 — 階段 1:全日桶路徑抽取(2026-09-30)。

先前的 biglot_exit_path_extract.py 是「每個事件存往後 13 桶」,無法重放同日重複進出場。
本檔改成「每個 (sid, date) 存全日每一桶」的 long 表,任意進出場序列都能離線模擬:
  close   = 桶收價
  bid     = 桶收時刻前最後一筆賣方主動成交價(=買一代理,掛單價)
  filled  = 以 bid 掛買一、桶收後 30 秒內是否有賣方主動成交 <= bid(樂觀口徑)
  exit_px = 桶邊界後首筆賣方主動成交價(=買一,悲觀出場價);無則退回桶收
  s / bad = V2.5 分數與壞標籤(供出場規則)
宇宙報酬另從 uidx 取,不進本表。

輸出 ~/goldenstocks-data/scratch/biglot_daypaths_2026-09-30.parquet
用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_reentry_path_extract.py
"""
from __future__ import annotations
import json, sys
from bisect import bisect_left
from pathlib import Path

import numpy as np
import pandas as pd

from biglot_hold_lab import load_lab, BAD

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OUT = Path.home() / "goldenstocks-data/scratch/biglot_daypaths_2026-09-30.parquet"
BUY_WAIT = 30.0
TH = 15.0


def _sec(t):
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + float(t[6:])


def load_ticks(sid, date):
    f = TICK / f"{sid}_{date}.json"
    if not f.exists():
        return None
    try:
        arr = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    t, px, ty = [], [], []
    for x in arr:
        tt = x.get("Time") or ""
        k = str(x.get("TickType", "0"))
        if len(tt) < 8 or k not in ("1", "2"):
            continue
        s = _sec(tt)
        if s < 9 * 3600 or s > 13 * 3600 + 30 * 60:
            continue
        t.append(s); px.append(float(x["deal_price"])); ty.append(1 if k == "1" else 2)
    if len(t) < 100:
        return None
    t = np.array(t); o = np.argsort(t, kind="stable")
    return t[o], np.array(px)[o], np.array(ty, dtype=np.int8)[o]


def bsec(b):
    return int(b[:2]) * 3600 + int(b[3:5]) * 60


def last_before(tk, t0, side, back=300.0):
    t, px, ty = tk
    i = bisect_left(t, t0) - 1
    while i >= 0 and t[i] >= t0 - back:
        if ty[i] == side:
            return float(px[i])
        i -= 1
    return np.nan


def first_after(tk, t0, side, within=180.0):
    t, px, ty = tk
    i = bisect_left(t, t0)
    while i < len(t) and t[i] <= t0 + within:
        if ty[i] == side:
            return float(px[i])
        i += 1
    return np.nan


def fill_bid(tk, t0, limit, wait):
    t, px, ty = tk
    i = bisect_left(t, t0)
    while i < len(t) and t[i] <= t0 + wait:
        if ty[i] == 2 and px[i] <= limit:
            return True
        i += 1
    return False


def main():
    L = load_lab()
    d = L["d"]; buckets = L["buckets"]; PX = L["px"]
    cand = d[(d["s"] >= TH) & (d["bucket"] <= "12:20")]
    pairs = sorted(set(map(tuple, cand[["sid", "date"]].values)))
    print(f"候選 (sid,date) {len(pairs)} 組,全日桶 {len(buckets)}", flush=True)
    sp = L["paths"]["s"]
    subs = {k: d.pivot_table(index=["sid", "date"], columns="bucket", values="it:" + k).reindex(columns=buckets).fillna(0)
            for k in BAD}
    rows = []
    for n, (sid, date) in enumerate(pairs):
        if n % 200 == 0:
            print(f"  ...{n}/{len(pairs)}", flush=True)
        key = (sid, date)
        if key not in PX.index:
            continue
        tk = load_ticks(sid, date)
        if tk is None:
            continue
        pxp = PX.loc[key].values
        spath = sp.loc[key].values if key in sp.index else np.zeros(len(buckets))
        badp = np.logical_or.reduce([subs[k].loc[key].values.astype(bool) if key in subs[k].index else np.zeros(len(buckets), bool)
                                     for k in BAD])
        for k, b in enumerate(buckets):
            t_c = bsec(b) + 300
            s_k = float(spath[k])
            bid = last_before(tk, t_c, 2) if s_k >= TH else np.nan
            rows.append({
                "sid": sid, "date": date, "k": k, "bucket": b,
                "close": float(pxp[k]) if np.isfinite(pxp[k]) else np.nan,
                "s": s_k, "bad": bool(badp[k]),
                "bid": bid,
                "filled": bool(fill_bid(tk, t_c, bid, BUY_WAIT)) if np.isfinite(bid) else False,
                "exit_px": first_after(tk, t_c, 2),
            })
    df = pd.DataFrame(rows)
    df["exit_px"] = df["exit_px"].fillna(df["close"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    cnd = df["s"] >= TH
    print(f"\n寫出 {OUT}  列 {len(df)}  (sid,date) {df.groupby(['sid','date']).ngroups}")
    print(f"候選桶 {int(cnd.sum())}  其中有買一報價 {int((cnd & df['bid'].notna()).sum())}  掛買一成交 {int((cnd & df['filled']).sum())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
