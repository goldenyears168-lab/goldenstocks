#!/usr/bin/env python3
"""從原始逐筆重放「下單那一刻儀表板上的大戶占比」，用來反推手動下單邏輯。

輸入：
  1. 手動期貨委託（trader_behavior_broker_pull.py → trader_behavior_profile.py 的 pkl）
  2. biglot 儀表板原始逐筆 `~/goldenstocks-data/cache/biglot_live_watch/raw_YYYY-MM-DD.jsonl`

重放邏輯逐字對齊 scripts/research/biglot/trade_ingest.py::_ingest_trade
（大戶＝單筆主動成交 ≥ BIG_AMT 1000 萬；散戶＝1 張且 < RETAIL_CAP 500 萬；
主動方向用 bid/ask 判定、退回價格比較；空窗 >90 秒且金額 ≥5 億的跳量剔除），
否則還原出來的數字跟使用者當下看到的畫面會不一致。

輸出：每張委託對應的 5 分／30 分／全日大戶淨占比、散戶占比、報酬、量能。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")

BIG_AMT = 10_000_000
RETAIL_CAP = 5_000_000
GAP_JUMP_AMT = 500_000_000
GAP_SECONDS = 90
RAW_DIR = Path.home() / "goldenstocks-data/cache/biglot_live_watch"


def replay_day(day: str, want: set[str]) -> dict[str, dict]:
    """回放一天的逐筆，回傳 {sid: {ts[], amt[], big_net[], ret_net[], px[], px0}}。"""
    path = RAW_DIR / f"raw_{day}.jsonl"
    if not path.exists():
        return {}
    lastvol: dict[str, float] = defaultdict(float)
    first_done: set[str] = set()
    last_bid: dict[str, float] = {}
    last_ask: dict[str, float] = {}
    last_px: dict[str, float] = {}
    last_seen: dict[str, float] = {}
    px0: dict[str, float] = {}
    acc: dict[str, dict[str, list]] = {s: {"ts": [], "amt": [], "big": [], "ret": [], "px": []}
                                       for s in want}
    with open(path) as fh:
        for line in fh:
            if '"trades"' not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("kind") != "message":
                continue
            msg = rec["payload"] or {}
            if msg.get("channel") != "trades" or msg.get("event") not in (None, "data"):
                continue
            d = msg.get("data") or {}
            sid = str(d.get("symbol", ""))
            px, vol = d.get("price"), d.get("volume")
            if not sid or px is None or vol is None or d.get("isTrial"):
                continue
            px, vol = float(px), float(vol)
            dv = vol - lastvol[sid]
            lastvol[sid] = vol
            if dv <= 0 or d.get("isOpen") or d.get("isClose"):
                continue
            if sid not in first_done:
                first_done.add(sid)
                px0[sid] = px
                continue
            b, a = d.get("bid"), d.get("ask")
            if b is not None:
                last_bid[sid] = float(b)
            if a is not None:
                last_ask[sid] = float(a)
            side = 1 if (a is not None and px >= float(a)) else (
                -1 if (b is not None and px <= float(b)) else 0)
            if side == 0:
                p = last_px.get(sid)
                side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
            last_px[sid] = px
            ts = rec.get("ts")
            if not ts:
                continue
            t = datetime.fromisoformat(ts).timestamp()
            gap = sid in last_seen and (t - last_seen[sid]) > GAP_SECONDS
            last_seen[sid] = t
            amt = px * dv * 1000
            if gap and amt >= GAP_JUMP_AMT:
                continue
            if sid not in acc:
                continue
            sgn = 1 if side > 0 else (-1 if side < 0 else 0)
            A = acc[sid]
            A["ts"].append(t)
            A["amt"].append(amt)
            A["big"].append(sgn * amt if amt >= BIG_AMT else 0.0)
            A["ret"].append(sgn * amt if (dv == 1 and amt < RETAIL_CAP) else 0.0)
            A["px"].append(px)
    for sid, A in acc.items():
        A["px0"] = px0.get(sid)
        A["cum_amt"] = _cum(A["amt"])
        A["cum_big"] = _cum(A["big"])
        A["cum_ret"] = _cum(A["ret"])
    return acc


def _cum(xs):
    out, s = [0.0], 0.0
    for x in xs:
        s += x
        out.append(s)
    return out


def window(A, t_end: float, seconds: float | None):
    """回傳 (大戶淨占比%, 散戶淨占比%, 總額, 區間報酬%, 起始價, 結束價)。"""
    ts = A["ts"]
    if not ts:
        return (None,) * 6
    j = bisect.bisect_right(ts, t_end)
    i = 0 if seconds is None else bisect.bisect_left(ts, t_end - seconds)
    if j <= i:
        return (None,) * 6
    tot = A["cum_amt"][j] - A["cum_amt"][i]
    big = A["cum_big"][j] - A["cum_big"][i]
    ret = A["cum_ret"][j] - A["cum_ret"][i]
    p0 = A["px"][i] if seconds is not None else A["px0"]
    p1 = A["px"][j - 1]
    r = (100 * (p1 / p0 - 1)) if (p0 and p1) else None
    return (100 * big / tot if tot else None, 100 * ret / tot if tot else None,
            tot, r, p0, p1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl", default=str(Path.home() / "goldenstocks-data/data/scratch/trader_behavior_tables.pkl"))
    ap.add_argument("--specs", default=str(Path.home() / "goldenstocks-data/data/cache/taifex_stock_futures_specs.json"))
    ap.add_argument("--days", nargs="+", default=["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"])
    ap.add_argument("--out", default=str(Path.home() / "goldenstocks-data/data/scratch/trader_biglot_decision.csv"))
    args = ap.parse_args()

    specs = json.loads(Path(args.specs).read_text(encoding="utf-8"))
    d = pd.read_pickle(args.pkl)
    o = d["futopt"]["orders"]
    o = o[~o["auto"]].copy()
    o["root"] = o["symbol"].str.slice(2)
    o["stock"] = o["root"].map(lambda r: specs.get(r[:-1] if r.endswith("F") else r, {}).get("stock_id"))
    o["uname"] = o["root"].map(lambda r: specs.get(r[:-1] if r.endswith("F") else r, {}).get("name"))
    o["day"] = o["date"].dt.strftime("%Y-%m-%d")
    o = o[o["day"].isin(args.days) & o["stock"].notna()]
    print(f"目標委託 n={len(o)}　天數={o['day'].nunique()}　標的={o['stock'].nunique()}")

    rows = []
    for day, g in o.groupby("day"):
        want = set(g["stock"])
        acc = replay_day(day, want)
        n_tick = sum(len(a["ts"]) for a in acc.values())
        print(f"  {day}: 重放 {len(acc)} 檔 · {n_tick:,} 筆逐筆")
        for r in g.itertuples():
            A = acc.get(r.stock)
            if not A or not A["ts"]:
                rows.append({"day": day, "stock": r.stock, "uname": r.uname,
                             "submit": r.submit_time, "side": r.side, "note": "無逐筆"})
                continue
            t = datetime.fromisoformat(f"{day}T{r.submit_time}+08:00").timestamp()
            rec = {"day": day, "stock": r.stock, "uname": r.uname, "submit": r.submit_time,
                   "side": r.side, "lot": r.lot, "price": r.price, "status": r.status,
                   "order_type": str(r.order_type).split(".")[-1],
                   "amend": r.n_amend_price, "market_type": str(r.market_type).split(".")[-1]}
            for label, sec in (("5m", 300), ("30m", 1800), ("day", None)):
                b, rt, tot, pr, p0, p1 = window(A, t, sec)
                rec[f"big_{label}"] = None if b is None else round(b, 2)
                rec[f"ret_{label}"] = None if rt is None else round(rt, 2)
                rec[f"amt_{label}"] = None if tot is None else round(tot / 1e8, 3)
                rec[f"px_ret_{label}"] = None if pr is None else round(pr, 2)
            rec["px_now"] = p1
            rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)
    print(f"wrote {args.out}  n={len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
