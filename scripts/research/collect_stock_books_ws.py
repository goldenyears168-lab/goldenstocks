#!/usr/bin/env python
"""現股五檔委託簿 ws 收集器(biglot 36 檔)→ stock_books_{date}.jsonl。唯讀、無送單路徑。

動機(2026-09-23):急殺回彈研究要驗「被動掛單 + 依委託簿改價」的成交,MIS 5 秒快照(事件時覆蓋 52%)
不夠;需要逐筆五檔推播。富邦 ws ``marketdata.websocket_client.stock`` 的 ``books`` channel 會推五檔
(bids/asks 各 5 檔 price+size)。36 檔 × 1 訂閱,佔一條連線(2026-09-22 已釋放 6 條額度)。

設計比照 collect_biglot_futprice 的 ws 執行緒:獨立 session、失敗整組重建(不複用舊 ws client)、
逐筆 append 寫檔、看門狗(盤中 300s 無訊息即重連)、08:55~13:35 自退。
輸出:${GOLDENSTOCKS_DATA_DIR}/cache/stock_books_ws/stock_books_{YYYY-MM-DD}.jsonl
每行 {"ts", "symbol", "bids":[{price,size}×5], "asks":[...], "book_time", "event"}。
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DATA_DIR  # noqa: E402
from order.fubon_session import connect_fubon, safe_logout  # noqa: E402

TZ = ZoneInfo("Asia/Taipei")
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
OUT_DIR = DATA_DIR.parent / "cache" / "stock_books_ws"
END_HHMM = os.environ.get("STOCK_BOOKS_END", "13:35")
SESSION_MAX_AGE_SEC = 3500.0
RECONNECT_SLEEP_SEC = 5.0
WATCHDOG_STALL_SEC = 300.0


def _now():
    return datetime.now(TZ)


def _in_market(hm):
    return "09:00" <= hm <= "13:30"


def main():
    smoke = "--smoke" in sys.argv
    cal = json.loads(CALIB.read_text())
    sids = [r["sid"] for r in cal["universe"]]
    if smoke:
        sids = sids[:3]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{_now():%H:%M:%S} 現股五檔 ws 收集 {len(sids)} 檔 → {OUT_DIR}", flush=True)
    consecutive_fail = 0
    while smoke or _now().strftime("%H:%M") < END_HHMM:
        sess = ws = None
        state = {"disc": False, "n": 0, "last": time.monotonic()}
        today = _now().strftime("%Y-%m-%d")
        out = OUT_DIR / f"stock_books_{today}.jsonl"
        try:
            sess = connect_fubon(realtime=True)
            ws = sess.sdk.marketdata.websocket_client.stock

            def on_message(raw):
                try:
                    msg = json.loads(raw)
                except Exception:  # noqa: BLE001
                    return
                ev = msg.get("event")
                data = msg.get("data")
                if ev not in ("snapshot", "data") or not isinstance(data, dict):
                    return
                if not ("bids" in data and "asks" in data):
                    return
                row = {"ts": _now().isoformat(timespec="milliseconds"), "symbol": data.get("symbol"),
                       "bids": data.get("bids"), "asks": data.get("asks"),
                       "book_time": data.get("time"), "event": ev}
                try:
                    with out.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                except Exception as exc:  # noqa: BLE001
                    print(f"寫檔失敗: {exc}", flush=True)
                state["n"] += 1
                state["last"] = time.monotonic()

            def on_disc(code, m):
                state["disc"] = True
                print(f"{_now():%H:%M:%S} ws disconnect code={code} msg={m}", flush=True)

            def on_err(err):
                print(f"{_now():%H:%M:%S} ws error: {err}", flush=True)

            ws.on("message", on_message)
            ws.on("disconnect", on_disc)
            ws.on("error", on_err)
            ws.connect()
            if ws.error is not None:
                raise RuntimeError(f"ws auth failed: {ws.error}")
            for s in sids:
                ws.subscribe({"channel": "books", "symbol": s})
            print(f"{_now():%H:%M:%S} 已訂閱 books {len(sids)} 檔", flush=True)
            t0 = time.monotonic()
            last_rep = t0
            while not state["disc"] and (smoke or _now().strftime("%H:%M") < END_HHMM):
                time.sleep(1.0)
                if smoke and time.monotonic() - t0 > 20:
                    break
                if time.monotonic() - t0 > SESSION_MAX_AGE_SEC:
                    print(f"{_now():%H:%M:%S} 主動重連(session 年齡)", flush=True)
                    break
                if _in_market(_now().strftime("%H:%M")) and time.monotonic() - state["last"] > WATCHDOG_STALL_SEC:
                    print(f"{_now():%H:%M:%S} 看門狗:{WATCHDOG_STALL_SEC:.0f}s 無推播,重連", flush=True)
                    break
                if time.monotonic() - last_rep > 300:
                    print(f"{_now():%H:%M:%S} alive · 累計 {state['n']} 筆", flush=True)
                    last_rep = time.monotonic()
            consecutive_fail = 0
        except Exception as exc:  # noqa: BLE001
            consecutive_fail += 1
            print(f"{_now():%H:%M:%S} 例外 {exc!r}(連續 {consecutive_fail})", flush=True)
            if consecutive_fail >= 6:
                print("連續失敗 6 次,退出交給明日重啟", flush=True)
                return 1
        finally:
            try:
                if ws is not None:
                    ws.disconnect()
            except Exception:  # noqa: BLE001
                pass
            safe_logout(sess)
        if smoke:
            print(f"smoke 結束:收到 {state['n']} 筆 → {out}", flush=True)
            return 0
        time.sleep(RECONNECT_SLEEP_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
