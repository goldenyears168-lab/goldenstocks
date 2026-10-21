#!/usr/bin/env python3
"""M09／M07 盤中氣氛記錄器（唯讀、不下單）：對「前一交易日盤點命中的隔日沖慣犯股」記下試撮、開盤、盤中高低、五檔委買委賣、成交量的時間序列，
用來回答「隔日沖隔天怎麼反應、為什麼有人漲停有人跌停」。小哥線索：試撮漲停但開盤沒漲停→被倒貨；委買 8:59 後急減→假的；10 點前賣壓宣洩；委買太多殺尾盤。
資料：富邦 REST stock.intraday.quote（單檔、實測 15 req/s 無節流；這裡 ≤40 檔，間隔 ≥0.1 秒）。一個 session，結束即登出。
輸出：${DATA}/chip/xiaoge_intraday/<日>/m09_ticks.jsonl（每筆一檔一次輪詢）、universe.json。
時程（台北）：08:30–09:00 每 30 秒（試撮）→ 09:00–10:15 每 30 秒 → 10:15–13:00 每 5 分 → 13:00–13:30 每 30 秒（尾盤）；13:32 結束。
用法：PYTHONPATH=src .venv/bin/python scripts/ops/xiaoge_m09_intraday.py [--date 2026-10-22] [--once]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR  # noqa: E402
from order.fubon_session import connect_fubon, safe_logout  # noqa: E402

INV = DATA_DIR / "chip" / "xiaoge_inventory"
OUT = DATA_DIR / "chip" / "xiaoge_intraday"
END = "13:32"
MAX_SYMBOLS = 40


def now() -> datetime:
    return datetime.now()


def universe(day: str) -> tuple[list[str], str | None]:
    files = sorted(p for p in INV.glob("inventory_20??-??-??.json") if p.stem.replace("inventory_", "") < day)
    if not files:
        return [], None
    inv = json.loads(files[-1].read_text(encoding="utf-8"))
    sids = []
    for k in ("M09", "M07"):
        for h in inv["methods"].get(k, {}).get("hits", []):
            if h["stock"] not in sids:
                sids.append(h["stock"])
    return sids[:MAX_SYMBOLS], inv["date"]


def interval(hhmm: str) -> int:
    if hhmm < "10:15":
        return 30
    if hhmm < "13:00":
        return 300
    return 30


def simplify(sid: str, q: dict) -> dict:
    tot = q.get("total") or {}
    bids, asks = q.get("bids") or [], q.get("asks") or []
    lt, ltr = q.get("lastTrade") or {}, q.get("lastTrial") or {}
    return {"ts": now().strftime("%H:%M:%S"), "s": sid, "open": q.get("openPrice"), "high": q.get("highPrice"), "low": q.get("lowPrice"),
            "last": q.get("closePrice") or q.get("lastPrice") or lt.get("price"), "prev": q.get("previousClose"),
            "vol": tot.get("tradeVolume"), "val": tot.get("tradeValue"), "n": tot.get("transaction"),
            "trial_px": ltr.get("price"), "trial_sz": ltr.get("size"), "trial_ts": ltr.get("time"),
            "b1": (bids[0].get("price"), bids[0].get("size")) if bids else None, "a1": (asks[0].get("price"), asks[0].get("size")) if asks else None,
            "bsum": sum((b.get("size") or 0) for b in bids), "asum": sum((a.get("size") or 0) for a in asks),
            "lu": q.get("isLimitUpPrice"), "ld": q.get("isLimitDownPrice"), "lu_bid": q.get("isLimitUpBid"), "lu_ask": q.get("isLimitUpAsk")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=now().strftime("%Y-%m-%d"))
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    sids, inv_day = universe(a.date)
    if not sids:
        print(f"{a.date} 找不到更早的盤點檔或無 M09/M07 命中，結束", flush=True)
        return 0
    out = OUT / a.date
    out.mkdir(parents=True, exist_ok=True)
    (out / "universe.json").write_text(json.dumps({"date": a.date, "signal_day": inv_day, "stocks": sids}, ensure_ascii=False), encoding="utf-8")
    print(f"{a.date} 追蹤 {len(sids)} 檔（盤點日 {inv_day}）：{' '.join(sids)}", flush=True)
    sess = connect_fubon(realtime=True)
    try:
        stk = sess.sdk.marketdata.rest_client.stock
        while True:
            hhmm = now().strftime("%H:%M")
            if hhmm >= END and not a.once:
                break
            if hhmm < "08:30" and not a.once:
                time.sleep(20); continue
            t0 = time.monotonic(); rows = []; err = 0
            for sid in sids:
                try:
                    rows.append(json.dumps(simplify(sid, stk.intraday.quote(symbol=sid)), ensure_ascii=False))
                except Exception as exc:  # noqa: BLE001
                    err += 1
                    if "429" in str(exc):
                        print(f"{now():%H:%M:%S} 被節流，退讓 60s", flush=True); time.sleep(60)
                time.sleep(0.1)
            if rows:
                with (out / "m09_ticks.jsonl").open("a", encoding="utf-8") as f:
                    f.write("\n".join(rows) + "\n")
            if err:
                print(f"{now():%H:%M:%S} 輪詢錯誤 {err}/{len(sids)}", flush=True)
            if a.once:
                break
            time.sleep(max(1.0, interval(hhmm) - (time.monotonic() - t0)))
    finally:
        print("logout:", safe_logout(sess), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
