#!/usr/bin/env python3
"""45 檔個股期貨五檔深度收集器 —— 走期交所 MIS，**不佔 Fubon websocket 訂閱額度**。

為什麼不用 Fubon ws books
-------------------------
補齊 45 檔要 34 個新 root × 4 訂閱 = 136 個訂閱，得再開 3 條 ws 連線。mini 現在已掛
11 個 job，其中包含唯一 live order-capable 的 tmf-channel-poll。
futopt-books-collect-stocks.command 檔頭記著：2026-08-21 ROOTS 從 8 擴到 27、108 個
訂閱擠一條連線，當天 11:08 起日盤掉了 157/300 分鐘（52%）。再加 3 條連線可能撞帳號
連線額度，而排在後面的是送單 worker —— 不值得。

MIS 這條
--------
POST https://mis.taifex.com.tw/futures/api/getQuoteDetail  {"SymbolID": [...]}
純 HTTP、無 session。實測**一次請求 45 個 symbol 回 45 筆、49 KB**，且回傳完整五檔：
CBidPrice1-5 / CAskPrice1-5 / CBidSize1-5 / CAskSize1-5 / CBestBid/AskPrice，還附 SpotID。

輸出：${GOLDENSTOCKS_DATA_DIR}/cache/futopt_mis_depth/mis_depth_{date}.jsonl（每輪一列/檔）
用途：算個股期貨的真實價差與深度 → 決定「同一訊號改做個股期貨」是否過得了成本。
"""
from __future__ import annotations
import argparse, json, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
sys.path.insert(0, "src")
from stock_db import DATA_DIR

TPE = timezone(timedelta(hours=8))
URL = "https://mis.taifex.com.tw/futures/api/getQuoteDetail"
OUT = Path(os.environ.get("GOLDENSTOCKS_DATA_DIR") or Path.home() / "goldenstocks-data") \
    / "cache" / "futopt_mis_depth"
MONTH = "ABCDEFGHIJKL"          # A=1月 … L=12月


def contract_code(d: datetime) -> str:
    return f"{MONTH[d.month - 1]}{d.year % 10}"


def roots() -> list[str]:
    cal = json.loads((DATA_DIR / "cache/pit_universe_tick/_live_calib.json").read_text())
    return [r["fut_code"] for r in cal["universe"] if r.get("fut_code")]


def poll(symbols: list[str], timeout: float = 25.0) -> list[dict]:
    r = requests.post(URL, json={"SymbolID": symbols}, timeout=timeout,
                      headers={"User-Agent": "Mozilla/5.0 (goldenstocks research)",
                               "Content-Type": "application/json"})
    r.raise_for_status()
    return ((r.json().get("RtData") or {}).get("QuoteList") or [])


KEEP = ("SymbolID", "SpotID", "DispCName", "CTime", "CLastPrice", "CTotalVolume",
        "CBestBidPrice", "CBestAskPrice", "CBestBidSize", "CBestAskSize") + tuple(
    f"C{s}{k}{i}" for s in ("Bid", "Ask") for k in ("Price", "Size") for i in range(1, 6))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=60.0, help="輪詢秒數")
    ap.add_argument("--until", default="13:45", help="收到幾點（HH:MM）")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    now = datetime.now(TPE)
    syms = [f"{c}{contract_code(now)}-F" for c in roots()]
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"mis_depth_{now:%Y-%m-%d}.jsonl"
    print(f"{len(syms)} 個 symbol · 合約碼 {contract_code(now)} · 每 {a.interval:.0f}s "
          f"· 收到 {a.until} · → {f}", flush=True)
    n = fail = 0
    with f.open("a", encoding="utf-8") as fh:
        while True:
            ts = datetime.now(TPE)
            if not a.once and ts.strftime("%H:%M") >= a.until:
                break
            try:
                q = poll(syms)
                for x in q:
                    fh.write(json.dumps({"ts": ts.isoformat(timespec="milliseconds"),
                                         **{k: x.get(k) for k in KEEP if k in x}},
                                        ensure_ascii=False) + "\n")
                fh.flush(); n += len(q)
            except Exception as e:                    # 單輪失敗不中斷
                fail += 1
                if fail <= 3:
                    print(f"  WARN {ts:%H:%M:%S} {type(e).__name__}: {e}", flush=True)
            if a.once:
                break
            time.sleep(a.interval)
    print(f"完成：{n} 列 / 失敗 {fail} 輪 → {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
