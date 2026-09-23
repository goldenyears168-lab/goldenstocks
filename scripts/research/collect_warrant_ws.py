#!/usr/bin/env python
"""盤中權證多空 —— 富邦 ws 逐筆版(v3)。唯讀、無送單路徑。

取代 REST 輪詢(v2,被富邦 REST 持續配額卡在 ~300 檔/2 req/s)與 MIS(v1,IP 封鎖事故)。
- 名單:重用 collect_warrant_flow.build_universe(前一交易日 TWSE 0999/0999P + TPEx,成交額前 TOPN 檔,對到 36 檔標的)。
- 訂閱:富邦 ws ``stock`` 的 ``trades`` channel,每條連線 PER_CONN 檔(2026-09-23 實測單線 300 全 ack),多條連線分攤。
  事件驅動,不吃 REST 配額;每筆成交自帶當下 bid/ask → **逐筆簽號**(價≥賣一買、≤買一賣、中間 tick rule、無法判定對半)。
- 輸出:與 v1/v2 相同 schema 的 warrantflow_{date}.json(儀表板/影子帳不改),每 5 秒整檔覆寫;
  另逐筆 append cache/warrant_trades_ws/warrant_trades_{date}.jsonl(sid, sym, side, price, size, bid, ask, dirn)供事後驗證。
- 重連:每條連線獨立執行緒,斷線/認證失敗整組重建(不複用 ws client),session 年齡 3500s 主動重連,
  盤中 600s 無任何推播即重連(權證稀,門檻放寬)。08:55~13:35 自退。
用法:PYTHONPATH=src .venv-fubon/bin/python scripts/research/collect_warrant_ws.py [--smoke]
"""
import json
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2] / "src"))
sys.path.insert(0, str(_HERE.parent))
from stock_db import DATA_DIR  # noqa: E402
from order.fubon_session import connect_fubon, safe_logout  # noqa: E402
import collect_warrant_flow as cw  # noqa: E402  (名單建法、常數)

TZ = ZoneInfo("Asia/Taipei")
OUT_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch"
TR_DIR = DATA_DIR.parent / "cache" / "warrant_trades_ws"
END_HHMM = "13:35"
TOPN = int(os.environ.get("WARRANT_WS_TOPN", "600"))
PER_CONN = int(os.environ.get("WARRANT_WS_PER_CONN", "300"))
SESSION_MAX_AGE_SEC = 3500.0
WATCHDOG_STALL_SEC = 600.0
WRITE_EVERY = 5.0
BULL = cw.BULL

_lock = threading.Lock()
EVENTS = []                                   # (ts, sid, inst, dirn, amt)
DAYAMT = defaultdict(float)
DAYSIG = defaultdict(float)
LAST_PX = {}
STATS = {"n": 0, "last": time.monotonic(), "conn": {}}
_RUN = {"on": True}


def _now():
    return datetime.now(TZ)


def _f(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _conn_loop(idx, symbols, info, trf):
    """一條連線:訂閱 symbols 的 trades,逐筆簽號寫入全域累加器。失敗整組重建。"""
    while _RUN["on"] and _now().strftime("%H:%M") < END_HHMM:
        sess = ws = None
        st = {"disc": False, "last": time.monotonic(), "n": 0}
        try:
            sess = connect_fubon(realtime=True)
            ws = sess.sdk.marketdata.websocket_client.stock

            def on_message(raw):
                try:
                    m = json.loads(raw)
                except Exception:  # noqa: BLE001
                    return
                if m.get("event") not in ("data", "snapshot"):
                    return
                d = m.get("data") or {}
                sym = d.get("symbol")
                if sym not in info or d.get("isTrial"):
                    return
                p, sz = _f(d.get("price")), _f(d.get("size"))
                if not p or not sz:
                    return
                a, b = _f(d.get("ask")), _f(d.get("bid"))
                lp = LAST_PX.get(sym)
                dirn = 1 if (a and p >= a) else (-1 if (b and p <= b) else (0 if lp is None else (1 if p > lp else (-1 if p < lp else 0))))
                LAST_PX[sym] = p
                sid, side = info[sym]
                inst = "多" if side in BULL else "空"
                amt = p * sz * 1000
                ts = time.time()
                with _lock:
                    EVENTS.append((ts, sid, inst, dirn, amt))
                    DAYAMT[(sid, inst)] += amt
                    bl = dirn * (1 if inst == "多" else -1)
                    if bl > 0:
                        DAYSIG[(sid, "多")] += amt
                    elif bl < 0:
                        DAYSIG[(sid, "空")] += amt
                    else:
                        DAYSIG[(sid, "多")] += amt / 2
                        DAYSIG[(sid, "空")] += amt / 2
                    STATS["n"] += 1
                    STATS["last"] = time.monotonic()
                st["n"] += 1
                st["last"] = time.monotonic()
                try:
                    with trf.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": _now().isoformat(timespec="milliseconds"), "sid": sid, "sym": sym, "side": side,
                                            "price": p, "size": sz, "bid": b, "ask": a, "dirn": dirn,
                                            "time": d.get("time")}, ensure_ascii=False) + "\n")
                except Exception:  # noqa: BLE001
                    pass

            def on_disc(code, msg):
                st["disc"] = True
                print(f"{_now():%H:%M:%S} [conn{idx}] disconnect {code} {msg}", flush=True)

            ws.on("message", on_message)
            ws.on("disconnect", on_disc)
            ws.on("error", lambda e: print(f"{_now():%H:%M:%S} [conn{idx}] ws error {e}", flush=True))
            ws.connect()
            if ws.error is not None:
                raise RuntimeError(f"auth failed: {ws.error}")
            for s in symbols:
                ws.subscribe({"channel": "trades", "symbol": s})
            print(f"{_now():%H:%M:%S} [conn{idx}] 已訂閱 {len(symbols)} 檔", flush=True)
            t0 = time.monotonic()
            while _RUN["on"] and not st["disc"] and _now().strftime("%H:%M") < END_HHMM:
                time.sleep(1.0)
                if time.monotonic() - t0 > SESSION_MAX_AGE_SEC:
                    print(f"{_now():%H:%M:%S} [conn{idx}] 主動重連(session 年齡)", flush=True)
                    break
                hm = _now().strftime("%H:%M")
                if "09:05" <= hm <= "13:25" and time.monotonic() - st["last"] > WATCHDOG_STALL_SEC:
                    print(f"{_now():%H:%M:%S} [conn{idx}] 看門狗:{WATCHDOG_STALL_SEC:.0f}s 無推播,重連", flush=True)
                    break
                STATS["conn"][idx] = st["n"]
        except Exception as exc:  # noqa: BLE001
            print(f"{_now():%H:%M:%S} [conn{idx}] 例外 {exc!r}", flush=True)
        finally:
            try:
                if ws is not None:
                    ws.disconnect()
            except Exception:  # noqa: BLE001
                pass
            safe_logout(sess)
        if _RUN["on"]:
            time.sleep(5.0)


def main():
    smoke = "--smoke" in sys.argv
    cal = json.loads(cw.CALIB.read_text())
    names = {r["sid"]: r["name"] for r in cal["universe"]}
    today = _now().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TR_DIR.mkdir(parents=True, exist_ok=True)
    cw.TOPN = TOPN
    wmap, ref = cw.build_universe(names)
    (OUT_DIR / f"warrant_map_{today}.json").write_text(json.dumps(wmap, ensure_ascii=False), encoding="utf-8")
    info = {}
    for sid, lst in wmap.items():
        for w in lst:
            info[w["sym"]] = (sid, w["side"])
    syms = list(info)
    if smoke:
        syms = syms[:PER_CONN]
    conns = [syms[i:i + PER_CONN] for i in range(0, len(syms), PER_CONN)]
    print(f"{_now():%H:%M:%S} 權證 ws:{len(syms)} 檔 / {len(wmap)} 標的(依 {ref} 前 {TOPN}),{len(conns)} 條連線 × ≤{PER_CONN}", flush=True)
    trf = TR_DIR / f"warrant_trades_{today}.jsonl"
    for i, chunk in enumerate(conns):
        threading.Thread(target=_conn_loop, args=(i, chunk, info, trf), name=f"wws{i}", daemon=True).start()
        time.sleep(1.0)
    out = OUT_DIR / f"warrantflow_{today}.json"
    start = _now().strftime("%H:%M:%S")
    t_smoke = time.monotonic()
    lastrep = time.monotonic()
    while _now().strftime("%H:%M") < END_HHMM:
        time.sleep(WRITE_EVERY)
        now = _now()
        tnow = time.time()
        with _lock:
            EVENTS[:] = [e for e in EVENTS if e[0] > tnow - 1800]
            ev = list(EVENTS)
            dayamt = dict(DAYAMT)
            daysig = dict(DAYSIG)
            ntot = STATS["n"]
        w5, w30 = defaultdict(float), defaultdict(float)
        s5, s30 = defaultdict(float), defaultdict(float)
        l5 = defaultdict(float)
        for ts, sid_, inst, dirn, a in ev:
            in5 = ts > tnow - 300
            w30[(sid_, inst)] += a
            if in5:
                w5[(sid_, inst)] += a
                if ts > tnow - WRITE_EVERY:
                    l5[(sid_, inst)] += a
            bl = dirn * (1 if inst == "多" else -1)
            parts = ((("多", a),) if bl > 0 else (("空", a),) if bl < 0 else (("多", a / 2), ("空", a / 2)))
            for k, v in parts:
                s30[(sid_, k)] += v
                if in5:
                    s5[(sid_, k)] += v
        snap = {"_meta": {"start": start, "t": now.strftime("%H:%M:%S"), "mapped": len(syms), "conns": len(conns),
                          "trades": ntot, "ref": ref, "src": "fubon_ws"}}
        for sid in wmap:
            nc = sum(1 for w in wmap[sid] if w["side"] in BULL)
            snap[sid] = {"call_day": dayamt.get((sid, "多"), 0.0), "put_day": dayamt.get((sid, "空"), 0.0),
                         "call_5": w5[(sid, "多")], "put_5": w5[(sid, "空")],
                         "call_30": w30[(sid, "多")], "put_30": w30[(sid, "空")],
                         "bull_5": s5[(sid, "多")], "bear_5": s5[(sid, "空")],
                         "bull_30": s30[(sid, "多")], "bear_30": s30[(sid, "空")],
                         "bull_day": daysig.get((sid, "多"), 0.0), "bear_day": daysig.get((sid, "空"), 0.0),
                         "call_last": l5[(sid, "多")], "put_last": l5[(sid, "空")],
                         "n_call": nc, "n_put": len(wmap[sid]) - nc, "t": now.strftime("%H:%M:%S")}
        try:
            out.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"寫檔失敗 {exc}", flush=True)
        if time.monotonic() - lastrep > 120:
            top = sorted(((daysig.get((s_, "多"), 0.0) - daysig.get((s_, "空"), 0.0), s_) for s_ in wmap), reverse=True)[:3]
            print(f"{now:%H:%M:%S} 累計 {ntot} 筆 · 連線推播 {dict(STATS['conn'])} · 全日簽號淨多前3: "
                  + " ".join(f"{names.get(s_, s_)}{a/1e4:+,.0f}萬" for a, s_ in top), flush=True)
            lastrep = time.monotonic()
        if smoke and time.monotonic() - t_smoke > 45:
            break
    _RUN["on"] = False
    print(f"{_now():%H:%M:%S} 結束,累計 {STATS['n']} 筆", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
