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

試撮窗（2026-09-19 加）
-----------------------
08:30–08:45 是期交所盤前委託建簿期，這段的五檔跟真開盤方向常常不一致（見肉眼案例：
大立光期貨 08:42:55 試撮價瞬間顯示 -9.92%、成交量 0，08:48:51 真開盤卻是 +1.33%）。
MIS 這段時間也回應（純 HTTP，無需等日盤開盤），預設輪詢間隔在 `--preopen-until`
之前改用 `--preopen-interval`（貼齊 MIS 自身約 5 秒更新頻率，快於平時不會多拿到新資訊）。
"""
from __future__ import annotations
import argparse, json, os, sys, time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import requests
sys.path.insert(0, "src")
from stock_db import DATA_DIR

TPE = timezone(timedelta(hours=8))
URL = "https://mis.taifex.com.tw/futures/api/getQuoteDetail"
OUT = Path(os.environ.get("GOLDENSTOCKS_DATA_DIR") or Path.home() / "goldenstocks-data") \
    / "cache" / "futopt_mis_depth"
MONTH = "ABCDEFGHIJKL"          # A=1月 … L=12月


def _third_wednesday(y: int, m: int) -> date:
    """台灣期貨最後交易日／結算日＝當月第三個星期三。"""
    first = date(y, m, 1)
    first_wed = 1 + (2 - first.weekday()) % 7      # weekday(): Mon=0 … Wed=2
    return date(y, m, first_wed + 14)


def contract_code(d: datetime) -> str:
    """近月合約碼。

    ⚠ 2026-09-17 事故：舊版只用 `d.month`，結算日隔天起仍請求已到期的合約，
    MIS 照樣回 45 筆、欄位卻全是空字串 —— 檔案大小正常、排程正常、內容全空的
    靜默失敗，連續 4 個交易日才被發現。結算日（第三個週三）當天仍是近月，
    **隔日起轉次月**。
    """
    y, m = d.year, d.month
    if d.date() > _third_wednesday(y, m):
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return f"{MONTH[m - 1]}{y % 10}"


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
    ap.add_argument("--interval", type=float, default=60.0, help="日盤輪詢秒數")
    ap.add_argument("--until", default="13:45", help="收到幾點（HH:MM）")
    ap.add_argument("--preopen-until", default="08:45",
                     help="此時間之前用 --preopen-interval（試撮窗）")
    ap.add_argument("--preopen-interval", type=float, default=5.0,
                     help="試撮窗輪詢秒數（貼齊 MIS 約 5 秒更新頻率）")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    now = datetime.now(TPE)
    syms = [f"{c}{contract_code(now)}-F" for c in roots()]
    OUT.mkdir(parents=True, exist_ok=True)

    # ── 啟動自檢：擋掉「合約已到期 → 回空殼」的靜默失敗（2026-09-17 事故）──
    try:
        probe = poll(syms[:3])
    except Exception as e:
        print(f"FATAL 啟動自檢連線失敗 {type(e).__name__}: {e}", flush=True)
        return 2
    if not probe or not any((x.get("DispCName") or "").strip() for x in probe):
        print(f"FATAL 合約 {contract_code(now)} 疑似已到期或不存在："
              f"MIS 回 {len(probe)} 筆但 DispCName 全空。"
              f"樣本 {syms[:3]} —— 不寫入空殼，直接結束。", flush=True)
        return 2

    f = OUT / f"mis_depth_{now:%Y-%m-%d}.jsonl"
    print(f"{len(syms)} 個 symbol · 合約碼 {contract_code(now)} · 試撮窗(<{a.preopen_until}) "
          f"每 {a.preopen_interval:.0f}s · 日盤每 {a.interval:.0f}s · 收到 {a.until} · → {f}",
          flush=True)
    n = fail = empty_rounds = 0
    with f.open("a", encoding="utf-8") as fh:
        while True:
            ts = datetime.now(TPE)
            hhmm = ts.strftime("%H:%M")
            if not a.once and hhmm >= a.until:
                break
            try:
                q = poll(syms)
                blank = sum(1 for x in q if not (x.get("CBidPrice1") or "").strip())
                if q and blank == len(q):
                    empty_rounds += 1
                    if empty_rounds in (5, 50, 200):
                        print(f"  WARN {ts:%H:%M:%S} 連續 {empty_rounds} 輪五檔全空"
                              f"（合約 {contract_code(now)}）", flush=True)
                else:
                    empty_rounds = 0
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
            time.sleep(a.preopen_interval if hhmm < a.preopen_until else a.interval)
    print(f"完成：{n} 列 / 失敗 {fail} 輪 → {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
