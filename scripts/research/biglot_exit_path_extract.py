#!/usr/bin/env python3
"""出場規則研究 — 階段 1:路徑抽取(2026-09-30)。

對每個 V2.5>=15 事件存完整 13 桶路徑,讓任意出場規則都能離線重放:
  進場 = 掛買一 30 秒(可成交價);每個桶邊界 k 的桶收價、桶邊界後首筆買一(TickType=2)成交價、
  分數 s[k]、壞標籤各子項、宇宙報酬。
動機:2026-09-29 紙上帳 8/9 筆走「壞標籤」出場、中位持有 2.4 分鐘,出場規則疑似過急。

輸出 ~/goldenstocks-data/scratch/biglot_exit_paths_2026-09-30.parquet
用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_exit_path_extract.py
"""
from __future__ import annotations
import json, sys
from bisect import bisect_left
from pathlib import Path

import numpy as np
import pandas as pd

from biglot_hold_lab import load_lab, make_events, BAD

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OUT = Path.home() / "goldenstocks-data/scratch/biglot_exit_paths_2026-09-30.parquet"
H = 12          # 最多 12 桶 = 60 分
BUY_WAIT = 30.0


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


def first_after(tk, t0, side=None, within=180.0):
    t, px, ty = tk
    i = bisect_left(t, t0)
    while i < len(t) and t[i] <= t0 + within:
        if side is None or ty[i] == side:
            return float(px[i])
        i += 1
    return None


def last_before(tk, t0, side, back=300.0):
    t, px, ty = tk
    i = bisect_left(t, t0) - 1
    while i >= 0 and t[i] >= t0 - back:
        if ty[i] == side:
            return float(px[i])
        i -= 1
    return None


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
    ev = make_events(L, "score", 15)
    buckets = L["buckets"]; PX = L["px"]; U = L["uidx"]; P = L["paths"]
    d = L["d"]
    sub = {k: d.pivot_table(index=["sid", "date"], columns="bucket", values="it:" + k).reindex(columns=buckets).fillna(0)
           for k in BAD}
    print(f"事件 {len(ev)}", flush=True)
    rows = []
    for n, e in enumerate(ev.itertuples()):
        if n % 200 == 0:
            print(f"  ...{n}/{len(ev)}", flush=True)
        key = (e.sid, e.date)
        if key not in PX.index:
            continue
        i0 = buckets.index(e.bucket)
        if i0 + H >= len(buckets):
            continue
        tk = load_ticks(e.sid, e.date)
        if tk is None:
            continue
        t_sig = bsec(e.bucket) + 300
        bid = last_before(tk, t_sig, 2)
        if bid is None or bid <= 0:
            continue
        rec = {"sid": e.sid, "date": e.date, "bucket": e.bucket, "is": bool(e.is_), "score": float(e.s),
               "entry": bid, "filled": fill_bid(tk, t_sig, bid, BUY_WAIT)}
        pxp = PX.loc[key].values; u = U.loc[e.date].values
        sp = P["s"].loc[key].values if key in P["s"].index else np.zeros(len(buckets))
        for k in range(H + 1):
            j = i0 + k
            rec[f"c{k}"] = float(pxp[j]) if np.isfinite(pxp[j]) else np.nan
            rec[f"u{k}"] = (u[j] / u[i0] - 1) * 1e4
            rec[f"s{k}"] = float(sp[j])
            bidk = first_after(tk, bsec(buckets[j]) + 300, 2) if k > 0 else None
            rec[f"b{k}"] = bidk if bidk else (float(pxp[j]) if np.isfinite(pxp[j]) else np.nan)
            for m, nm in enumerate(BAD):
                rec[f"x{m}_{k}"] = bool(sub[nm].loc[key].values[j]) if key in sub[nm].index else False
        rows.append(rec)
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"\n寫出 {OUT}  n={len(df)}  IS {int(df['is'].sum())} / OOS {int((~df['is']).sum())}  成交率 {df['filled'].mean()*100:.1f}%")
    print("壞標籤子項:", list(BAD))
    return 0


if __name__ == "__main__":
    sys.exit(main())
