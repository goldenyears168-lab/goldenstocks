#!/usr/bin/env python
"""個股期貨即時成交價 + 買一/賣一五檔 → futprice_{date}.json,供 biglot dashboard
「期貨買(量)/期貨賣(量)」欄。

兩條獨立唯讀連線(2026-09-22 釋放 6 條無關富邦連線後帳號容量充足):
  - **主執行緒 REST 輪詢**近月成交價(px)。沿用原本已現場驗證的路徑,單檔失敗不中斷。
  - **背景執行緒 WS books channel** 收買一/賣一 price+size。獨立 session、自行重連;
    失敗只影響 bid/ask 欄,**不影響**主執行緒的 px 收集。books 訂閱一定要帶
    ``"afterHours": False``(布林,見 collect_ccf_books_websocket.py 檔頭踩坑紀錄),
    否則 SDK 靜默退回夜盤凍結 snapshot。

啟動時對 biglot 宇宙每檔用 tickers(product=root) 解析近月合約(標準契約 F 優先,
避開減資調整後契約 FT1/IX1 的低流動性坑)。之後每 interval 秒把 REST px 與 WS 買賣簿
合併,寫 sid -> {px,sym,t, bid,bidsz,ask,asksz,bt} 到 futprice_{date}.json(整檔覆寫)。
買賣簿逾 STALE_SEC 秒沒更新(疑似 ws 斷線)就從輸出剔除,避免儀表板顯示凍結價。
13:45 自動退出。唯讀、不碰任何 order layer 送單路徑。
"""
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DATA_DIR  # noqa: E402
from order.fubon_session import connect_fubon, safe_logout  # noqa: E402  (session helper,比照 biglot_live_watch)

TZ = ZoneInfo("Asia/Taipei")
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
OUT_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch"
INTERVAL = int(os.environ.get("FUTPRICE_INTERVAL", "10"))
END_HHMM = "13:45"
#: 整條 ws feed 超過這麼久沒收到「任何一檔」的推播,才視為斷線、剔除所有買賣簿。
#: 不再用「單檔簿太久沒更新」判斷——漲停/跌停鎖死的簿本來就不動(南電/欣興漲停時
#: IRFJ6/LYFJ6 簿幾秒才動一次),per-檔門檻會誤砍掉鎖死檔,造成期貨欄閃爍消失。
STALE_SEC = float(os.environ.get("FUTBOOK_STALE_SEC", "30"))
#: 由 ws 背景執行緒維護:最後一次收到「任何」books 推播的 monotonic 時間(feed 活著的心跳)。
_WS_LAST = {"mono": 0.0}
#: ws session 主動重連年齡上限(比照 tmf_channel / ccf 收集器的 3500s)。
SESSION_MAX_AGE_SEC = 3500.0
RECONNECT_SLEEP_SEC = 5.0

#: 由 WS 背景執行緒維護,主執行緒讀取合併。key = 期貨 symbol。
BOOK: dict[str, dict] = {}
_RUN = {"on": True}


def _now():
    return datetime.now(TZ)


def resolve_front(fut, root, today):
    data = fut.intraday.tickers(type="FUTURE", exchange="TAIFEX", product=root)
    rows = data if isinstance(data, list) else (data.get("data") or data.get("tickers") or [])
    cands = []
    for r in rows or []:
        sym = str(r.get("symbol") or "")
        end = str(r.get("endDate") or r.get("end_date") or "")[:10]
        if not sym.startswith(root) or (end and end < today):
            continue
        std = 0 if sym[len(root):len(root) + 1] == "F" else 1  # 標準契約優先
        cands.append((end or "9999-99-99", std, sym))
    cands.sort()
    return cands[0][2] if cands else None


def _last_px(q):
    if not isinstance(q, dict):
        return None
    for k in ("lastPrice", "lastTrade", "closePrice", "close"):
        v = q.get(k)
        if isinstance(v, dict):
            v = v.get("price")
        if v:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _ws_loop(symbols):
    """背景執行緒:獨立 session 訂閱 books,把買一/賣一 price+size 寫進全域 BOOK。

    失敗自行重連(整組重建 session,不複用舊 ws client — 比照 ccf 收集器教訓),
    任何例外只印出、不影響主執行緒 REST px。"""
    while _RUN["on"] and _now().strftime("%H:%M") < END_HHMM:
        wsess = None
        ws = None
        disc = {"f": False}
        try:
            wsess = connect_fubon(realtime=True)
            ws = wsess.sdk.marketdata.websocket_client.futopt

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
                sym = data.get("symbol")
                if not sym:
                    return
                bids = data.get("bids") or []
                asks = data.get("asks") or []
                b0 = bids[0] if bids else {}
                a0 = asks[0] if asks else {}
                BOOK[sym] = {
                    "bid": b0.get("price"), "bidsz": b0.get("size"),
                    "ask": a0.get("price"), "asksz": a0.get("size"),
                    "mono": time.monotonic(),
                    "t": _now().strftime("%H:%M:%S"),
                }
                _WS_LAST["mono"] = time.monotonic()   # feed 心跳(任何檔的推播都算)

            def on_disc(code, m):
                disc["f"] = True
                print(f"{_now():%H:%M:%S} ws disconnect code={code} msg={m}", flush=True)

            def on_err(err):
                print(f"{_now():%H:%M:%S} ws error: {err}", flush=True)

            ws.on("message", on_message)
            ws.on("disconnect", on_disc)
            ws.on("error", on_err)
            ws.connect()
            if ws.error is not None:
                raise RuntimeError(f"ws auth failed: {ws.error}")
            for s in symbols:
                ws.subscribe({"channel": "books", "symbol": s, "afterHours": False})
            print(f"{_now():%H:%M:%S} ws books 已訂閱 {len(symbols)} 檔(日盤)", flush=True)
            t0 = time.monotonic()
            while _RUN["on"] and not disc["f"] and _now().strftime("%H:%M") < END_HHMM:
                time.sleep(1.0)
                if time.monotonic() - t0 > SESSION_MAX_AGE_SEC:
                    print(f"{_now():%H:%M:%S} ws 主動重連(session 年齡上限)", flush=True)
                    break
        except Exception as exc:  # noqa: BLE001 -- ws 失敗絕不影響主執行緒
            print(f"{_now():%H:%M:%S} ws loop 例外: {exc!r}", flush=True)
        finally:
            try:
                if ws is not None:
                    ws.disconnect()
            except Exception:  # noqa: BLE001
                pass
            safe_logout(wsess)
        if _RUN["on"]:
            time.sleep(RECONNECT_SLEEP_SEC)


def main():
    cal = json.loads(CALIB.read_text())
    uni = {r["sid"]: (r.get("fut_code") or "") for r in cal["universe"]}
    sess = connect_fubon(realtime=True)
    fut = sess.sdk.marketdata.rest_client.futopt
    today = _now().strftime("%Y-%m-%d")
    # 解析近月
    sym_of = {}
    for sid, fc in uni.items():
        root = fc[:2] if len(fc) >= 3 else fc
        if not root:
            continue
        try:
            s = resolve_front(fut, root, today)
            if s:
                sym_of[sid] = s
        except Exception as exc:  # noqa: BLE001
            print(f"resolve {sid}/{root} 失敗: {exc}", flush=True)
    print(f"解析近月 {len(sym_of)}/{len(uni)} 檔", flush=True)
    # 台指期(TXF 近月)一併輪詢,寫在 json 的 "TXF" 鍵:供 zcrash_shadow 的台指 1 分逆勢風控
    # (2026-09-22 停掉 futopt-books-collect 後已無 TXF 即時源)。儀表板按 sid 取值,不受此鍵影響。
    try:
        txf = resolve_front(fut, "TXF", today)
        if txf:
            sym_of["TXF"] = txf
            print(f"TXF 近月 {txf}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"resolve TXF 失敗: {exc}", flush=True)

    # 背景 WS books 執行緒(獨立 session)
    once = "--once" in sys.argv
    if not once and sym_of:
        threading.Thread(
            target=_ws_loop, args=(list(sym_of.values()),),
            name="fut-books-ws", daemon=True,
        ).start()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"futprice_{today}.json"
    while _now().strftime("%H:%M") < END_HHMM:
        feed_alive = (time.monotonic() - _WS_LAST["mono"]) <= STALE_SEC
        snap = {}
        for sid, sym in sym_of.items():
            rec = {}
            try:
                q = fut.intraday.quote(symbol=sym)
                px = _last_px(q)
                if px:
                    rec = {"px": px, "sym": sym, "t": _now().strftime("%H:%M:%S")}
                    fpc = q.get("previousClose") if isinstance(q, dict) else None
                    if fpc:  # 期貨自身昨結,供儀表板算期貨漲跌停(紅底/綠底)
                        try:
                            rec["fpc"] = float(fpc)
                        except (TypeError, ValueError):
                            pass
            except Exception:  # noqa: BLE001
                pass
            # 只要整條 ws feed 還活著,就沿用該檔最後已知買賣簿(鎖死檔簿不動也保留);
            # feed 整條斷線(逾 STALE_SEC 沒任何推播)才全部剔除,避免顯示凍結價。
            b = BOOK.get(sym)
            if b and feed_alive:
                rec.setdefault("sym", sym)
                rec.update({"bid": b["bid"], "bidsz": b["bidsz"],
                            "ask": b["ask"], "asksz": b["asksz"], "bt": b["t"]})
            if rec:
                snap[sid] = rec
        try:
            out.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"寫檔失敗: {exc}", flush=True)
        nb = sum(1 for v in snap.values() if "bid" in v)
        print(f"{_now():%H:%M:%S} 期貨 px {len(snap)}/{len(sym_of)} · 買賣簿 {nb} 檔", flush=True)
        if once:
            break
        time.sleep(INTERVAL)
    _RUN["on"] = False
    return 0


if __name__ == "__main__":
    sys.exit(main())
