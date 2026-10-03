#!/usr/bin/env python3
"""雙時點引擎：送出委託那一刻 vs 成交那一刻，各自的儀表板狀態 + 成交後績效。

使用者觀察（2026-10-04）：他常把限價掛在離市價較遠處，所以「成交那一刻」才是
市場走過來找他的時刻——績效必須從成交價起算，而不是從送出價。本引擎因此對每
一張新倉委託算兩套快照：
  T1 = 按下送出的那一秒（他的決策時點）
  T2 = 第一筆成交的那一秒（市場接受的時點）
並計算 T1→T2 之間價格與各欄位的變化，以及 T2 之後 +30 分、+60 分、到收盤的走勢。

欄位一律用全名，不用簡稱：
  大戶佔比  = 大戶（單筆成交 ≥1000 萬）淨買金額 ÷ 區間成交額 × 100
  散戶淨佔比 = 散戶（1 張且 <500 萬）淨買金額 ÷ 區間成交額 × 100
  大戶減散戶 = 大戶佔比 − 散戶淨佔比（儀表板第 22 欄）
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from trader_peer_group_choice import day_universe, load_subcat  # noqa: E402

RAW = Path.home() / "goldenstocks-data/cache/biglot_live_watch"
SCR = Path.home() / "goldenstocks-data/data/scratch"
BIG_AMT, RETAIL_CAP, GAP_JUMP, GAP_SEC = 10_000_000, 5_000_000, 500_000_000, 90
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
WINDOWS = (("前5分", 300), ("前30分", 1800), ("開盤累計", None))


def replay(day: str, want: set[str]) -> dict:
    lastvol: dict[str, float] = {}
    first: set[str] = set()
    lb: dict[str, float] = {}
    la: dict[str, float] = {}
    lpx: dict[str, float] = {}
    lseen: dict[str, float] = {}
    px0: dict[str, float] = {}
    acc = {s: {"ts": [], "amt": [], "big": [], "rbuy": [], "rsell": [], "mid": [], "px": []}
           for s in want}
    with open(RAW / f"raw_{day}.jsonl") as fh:
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
            dv = vol - lastvol.get(sid, 0.0)
            lastvol[sid] = vol
            if dv <= 0 or d.get("isOpen") or d.get("isClose"):
                continue
            if sid not in first:
                first.add(sid)
                px0[sid] = px
                continue
            b, a = d.get("bid"), d.get("ask")
            if b is not None:
                lb[sid] = float(b)
            if a is not None:
                la[sid] = float(a)
            side = 1 if (a is not None and px >= float(a)) else (
                -1 if (b is not None and px <= float(b)) else 0)
            if side == 0:
                p = lpx.get(sid)
                side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
            lpx[sid] = px
            ts = rec.get("ts")
            if not ts:
                continue
            t = datetime.fromisoformat(ts).timestamp()
            gap = sid in lseen and (t - lseen[sid]) > GAP_SEC
            lseen[sid] = t
            amt = px * dv * 1000
            if gap and amt >= GAP_JUMP:
                continue
            if sid not in acc:
                continue
            sgn = 1 if side > 0 else (-1 if side < 0 else 0)
            A = acc[sid]
            A["ts"].append(t)
            A["amt"].append(amt)
            A["px"].append(px)
            A["big"].append(sgn * amt if amt >= BIG_AMT else 0.0)
            is_r = (dv == 1 and amt < RETAIL_CAP)
            A["rbuy"].append(amt if (is_r and sgn > 0) else 0.0)
            A["rsell"].append(amt if (is_r and sgn < 0) else 0.0)
            A["mid"].append(sgn * amt if (amt < BIG_AMT and not is_r) else 0.0)
    for A in acc.values():
        for k in ("amt", "big", "rbuy", "rsell", "mid"):
            run, tot = [0.0], 0.0
            for x in A[k]:
                tot += x
                run.append(tot)
            A["c_" + k] = run
    out = {}
    for s, A in acc.items():
        if A["ts"]:
            A["px0"] = px0.get(s)
            out[s] = A
    return out


def state(A: dict, t: float) -> dict | None:
    ts = A["ts"]
    j = bisect.bisect_right(ts, t)
    if j == 0:
        return None
    o = {"現價": A["px"][j - 1]}
    if A.get("px0"):
        o["當日漲跌"] = 100 * (o["現價"] / A["px0"] - 1)
    for lab, w in WINDOWS:
        i = 0 if w is None else bisect.bisect_left(ts, t - w)
        if j <= i:
            continue
        tot = A["c_amt"][j] - A["c_amt"][i]
        if not tot:
            continue
        big = A["c_big"][j] - A["c_big"][i]
        rb = A["c_rbuy"][j] - A["c_rbuy"][i]
        rs = A["c_rsell"][j] - A["c_rsell"][i]
        mid = A["c_mid"][j] - A["c_mid"][i]
        o[f"大戶佔比_{lab}"] = 100 * big / tot
        o[f"大戶淨額萬_{lab}"] = big / 1e4
        o[f"散戶淨佔比_{lab}"] = 100 * (rb - rs) / tot
        o[f"散戶買方參與_{lab}"] = 100 * rb / tot
        o[f"中實戶佔比_{lab}"] = 100 * mid / tot
        o[f"大戶減散戶_{lab}"] = o[f"大戶佔比_{lab}"] - o[f"散戶淨佔比_{lab}"]
        o[f"成交額萬_{lab}"] = tot / 1e4
        p0 = A["px"][i] if w is not None else A["px0"]
        if p0:
            o[f"價格變化_{lab}"] = 100 * (A["px"][j - 1] / p0 - 1)
    return o


def fwd(A: dict, t: float, seconds: int | None) -> tuple[float | None, float | None]:
    """回傳 (該時點後的價格, 相對該時點的漲跌%)。seconds=None 表示到收盤。"""
    ts = A["ts"]
    j = bisect.bisect_right(ts, t) - 1
    if j < 0:
        return None, None
    base = A["px"][j]
    k = len(ts) - 1 if seconds is None else bisect.bisect_right(ts, t + seconds) - 1
    if k <= j:
        return None, None
    return A["px"][k], 100 * (A["px"][k] / base - 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SCR / "trader_dual_moment.csv"))
    args = ap.parse_args()
    sub = load_subcat()
    specs = json.loads((Path.home() / "goldenstocks-data/data/cache/taifex_stock_futures_specs.json")
                       .read_text(encoding="utf-8"))
    d = pd.read_pickle(SCR / "trader_behavior_tables.pkl")
    o = d["futopt"]["orders"]
    o = o[~o["auto"]].copy()
    o["day"] = o["date"].dt.strftime("%Y-%m-%d")
    o = o[o["day"].isin(DAYS) & o["order_type"].eq("FutOptOrderType.New")].copy()
    key = o["symbol"].str.slice(2).map(lambda r: r[:-1] if r.endswith("F") else r)
    o["sid"] = key.map(lambda r: specs.get(r, {}).get("stock_id"))
    o["uname"] = key.map(lambda r: specs.get(r, {}).get("name"))
    o["K"] = o["day"] + "|" + o["order_no"].astype(str).str.strip()
    f = d["futopt"]["fills"]
    f = f[f["tag"] == "manual"].copy()
    f["day"] = f["date"].dt.strftime("%Y-%m-%d")
    f["K"] = f["day"] + "|" + f["order_no"].astype(str).str.strip()
    fill_px = f.groupby("K")["filled_price"].mean().to_dict()
    fill_t = f.groupby("K")["filled_time"].min().to_dict()

    recs = []
    for day in DAYS:
        g = o[o["day"] == day]
        if g.empty:
            continue
        uni = day_universe(day)
        acc = replay(day, uni)
        print(f"{day}: 重放 {len(acc)} 檔 · 新倉 {len(g)} 張", flush=True)
        for r in g.itertuples():
            rec = {"日期": day, "送出時間": r.submit_time, "標的": r.uname, "sid": r.sid,
                   "方向": "開多" if r.side == "Buy" else "開空", "口數": r.lot,
                   "掛價": r.price, "狀態": r.status, "族群": sub.get(str(r.sid)),
                   "成交時間": fill_t.get(r.K), "成交價": fill_px.get(r.K)}
            A = acc.get(str(r.sid))
            if A is None:
                rec["備註"] = "標的當天不在宇宙或無逐筆"
                recs.append(rec)
                continue
            for tag, tm in (("送出", r.submit_time), ("成交", fill_t.get(r.K))):
                if tm is None or (isinstance(tm, float) and pd.isna(tm)):
                    continue
                t = datetime.fromisoformat(f"{day}T{str(tm)[:12]}+08:00").timestamp()
                st = state(A, t)
                if st is None:
                    continue
                allst = {s: state(B, t) for s, B in acc.items()}
                allst = {s: v for s, v in allst.items() if v}
                for k2, v in st.items():
                    rec[f"{tag}:{k2}"] = v
                    col = [x[k2] for x in allst.values() if k2 in x]
                    if len(col) >= 10:
                        rec[f"{tag}#{k2}"] = int(sum(1 for y in col if y > v)) + 1
                        rec[f"{tag}N{k2}"] = len(col)
                if tag == "成交":
                    for lab, sec in (("後30分", 1800), ("後60分", 3600), ("到收盤", None)):
                        px, pct = fwd(A, t, sec)
                        rec[f"成交後_{lab}價"] = px
                        rec[f"成交後_{lab}%"] = pct
                    peers = [p for p in uni if sub.get(p) == sub.get(str(r.sid))
                             and p in acc and p != str(r.sid)]
                    for lab, sec in (("後30分", 1800), ("後60分", 3600), ("到收盤", None)):
                        vals = [fwd(acc[p], t, sec)[1] for p in peers]
                        vals = [v for v in vals if v is not None]
                        rec[f"族群同期_{lab}%"] = sum(vals) / len(vals) if vals else None
            if rec.get("成交:現價") and rec.get("送出:現價"):
                rec["送出到成交價格變化%"] = 100 * (rec["成交:現價"] / rec["送出:現價"] - 1)
            if rec.get("成交時間") and isinstance(rec["成交時間"], str):
                t1 = datetime.fromisoformat(f"{day}T{str(r.submit_time)[:12]}+08:00")
                t2 = datetime.fromisoformat(f"{day}T{str(rec['成交時間'])[:12]}+08:00")
                rec["等待分鐘"] = round((t2 - t1).total_seconds() / 60, 1)
            recs.append(rec)
    out = pd.DataFrame(recs)
    out.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}  {len(out)} 張 × {len(out.columns)} 欄")
    print(f"有成交快照 {int(out.get('成交:現價', pd.Series(dtype=float)).notna().sum())} 張")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
