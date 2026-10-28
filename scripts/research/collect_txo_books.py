#!/usr/bin/env python
"""TXO(台指選擇權)月選買一/賣一 15 秒快照 → cache/txo_books/txo_15s_{date}.jsonl。

用途:累積盤中真實買賣報價,離線驗證「每分鐘自算 VIXTWN」(中價版)對官方 15 秒檔的誤差,
通過後才接儀表板。**只錄資料,不算指標、不送單、不碰 order layer。**

選檔:近月/次月(同 sync_vixtwn_computed 規則:近 = 距到期 7~30 天最大者,次 = 下一個;
僅月選,週選代碼不屬 product=TXO)。履約價取現貨 ±BAND(預設 25%)內,價外一側
(K ≤ 現貨取賣權、K > 現貨取買權)+ 現貨附近 ±3 檔兩邊都訂(平價求 F 用)。
離線試算:band 縮到 ±10% 會讓 VIX 低估約 2 點,所以預設不縮得太窄。

訂閱上限(2026-10-21 開盤後實測):單連線 **300 檔**——40/100/200 全數成功,412 檔時
接受 300、其餘回 code 1001「Subscription limit exceeded」(不斷線);跑期間 futprice/ccf
收集器不受影響。所以遠翼隔檔訂(--thin,預設每 3 檔取 1),預設 --max-symbols 300。
books 只在報價變動時推,冷門履約價可能整天無簿(即「有簿 N/M」比例 <100% 屬正常)。

用法(用 .venv-fubon):
    .venv-fubon/bin/python scripts/research/collect_txo_books.py --list [--session AFTERHOURS]
    .venv-fubon/bin/python scripts/research/collect_txo_books.py --max-symbols 40
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DATA_DIR  # noqa: E402
from order.fubon_session import connect_fubon, safe_logout  # noqa: E402
from txo_vix import MidBook, SpikeFilter, minute_vix  # noqa: E402  (同目錄,腳本執行時 sys.path[0] 就是這裡)

TZ = ZoneInfo("Asia/Taipei")
OUT_DIR = DATA_DIR.parent / "cache" / "txo_books"
FUTPRICE_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch"
END_HHMM = "13:45"
SNAP_SEC = 15.0
BOOK: dict[str, dict] = {}
SPIKE = SpikeFilter()
MID = MidBook()                    # 供每分鐘自算 VIX(txo_vix.py)
_FEED = {"mono": 0.0}              # 最後一次收到任何簿推播的 monotonic;太久沒動就不算 VIX
FEED_STALE_SEC = 90.0
SPOT_WAIT_SEC = 900                # 08:50 啟動時 futprice 檔可能還沒生出來,最多等 15 分鐘


def _now() -> datetime:
    return datetime.now(TZ)


def spot_from_futprice() -> float | None:
    """台指近月最新價(biglot futprice 收集器落地);沒有就回 None,由 --spot 補。"""
    try:
        d = json.loads((FUTPRICE_DIR / f"futprice_{_now():%Y-%m-%d}.json").read_text())
        return float(d["TXF"]["px"])
    except Exception:  # noqa: BLE001
        return None


def pick_symbols(rows: list[dict], spot: float, band: float, today: date,
                 core: float = 0.10, thin: int = 1) -> list[dict]:
    """rows = intraday.tickers(OPTION/TXO) 的 data。回傳 [{symbol,strike,cp,end,days}]。"""
    parsed = []
    for r in rows:
        m = re.match(r"TXO(\d+)[A-X]\d$", str(r.get("symbol") or ""))
        end = str(r.get("endDate") or "")[:10]
        name = str(r.get("name") or "")
        if not m or not end or end < str(today):
            continue
        cp = "C" if "買權" in name else "P" if "賣權" in name else None
        if cp is None:
            continue
        days = (date.fromisoformat(end) - today).days
        parsed.append({"symbol": r["symbol"], "strike": int(m.group(1)), "cp": cp, "end": end, "days": days})
    exps = sorted({(p["end"], p["days"]) for p in parsed if p["days"] >= 1}, key=lambda x: x[1])
    below = [e for e in exps if 7 <= e[1] <= 30]
    near = below[-1] if below else next((e for e in exps if e[1] >= 7), None)
    nxt = next((e for e in exps if near and e[1] > near[1]), None)
    if not near or not nxt:
        return []
    keep = {near[0], nxt[0]}
    strikes = sorted({p["strike"] for p in parsed if p["end"] in keep})
    atm = min(strikes, key=lambda k: abs(k - spot))
    i = strikes.index(atm)
    near_atm = set(strikes[max(0, i - 3): i + 4])
    # 遠翼(離現貨 > core)只留每 thin 檔一檔,省訂閱數(上限 300/連線)
    wing_rank = {}
    for e in keep:
        for side in (-1, 1):
            ks = sorted((k for k in strikes if (k - spot) * side > 0 and abs(k / spot - 1) > core), key=lambda k: abs(k - spot))
            for i, k in enumerate(ks):
                wing_rank[(e, k)] = i
    out = []
    for p in parsed:
        if p["end"] not in keep or abs(p["strike"] / spot - 1) > band:
            continue
        if abs(p["strike"] / spot - 1) > core and wing_rank.get((p["end"], p["strike"]), 0) % thin:
            continue
        otm = (p["cp"] == "P" and p["strike"] <= spot) or (p["cp"] == "C" and p["strike"] > spot)
        if otm or p["strike"] in near_atm:
            out.append(p)
    # 先訂靠近現貨的,--max-symbols 截斷時留下最重要的
    out.sort(key=lambda p: abs(p["strike"] - spot))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列出選檔結果,不訂閱")
    ap.add_argument("--session", default="REGULAR", choices=["REGULAR", "AFTERHOURS"])
    ap.add_argument("--band", type=float, default=0.25)
    ap.add_argument("--spot", type=float, default=None)
    ap.add_argument("--thin", type=int, default=3, help="遠翼(>10%%)每 N 檔取 1,預設 3")
    ap.add_argument("--max-symbols", type=int, default=300, help="每連線訂閱上限實測 300(2026-10-21)")
    a = ap.parse_args()

    sess = connect_fubon(realtime=True)
    try:
        rest = sess.sdk.marketdata.rest_client.futopt
        d = rest.intraday.tickers(type="OPTION", exchange="TAIFEX", product="TXO", session=a.session)
        rows = d.get("data") or []
        spot = a.spot or spot_from_futprice()
        t_wait = time.monotonic()
        while not spot and time.monotonic() - t_wait < SPOT_WAIT_SEC:
            time.sleep(10)
            spot = spot_from_futprice()
        if not spot:
            raise SystemExit("找不到現貨價,請用 --spot")
        picks = pick_symbols(rows, spot, a.band, _now().date(), thin=a.thin)
        if a.max_symbols:
            picks = picks[: a.max_symbols]
        ends = sorted({p["end"] for p in picks})
        print(f"{_now():%H:%M:%S} 商品 {len(rows)} 檔 → 選 {len(picks)} 檔 到期 {ends} 現貨 {spot:,.0f}", flush=True)
        if a.list or not picks:
            return
        ws = sess.sdk.marketdata.websocket_client.futopt
        meta = {p["symbol"]: p for p in picks}
        for p in picks:
            MID.register(p["end"], p["strike"])

        def on_message(raw):
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                return
            ev, data = msg.get("event"), msg.get("data")
            if ev not in ("snapshot", "data") or not isinstance(data, dict):
                if ev not in ("subscribed", "authenticated", "pong", "heartbeat"):
                    print(f"{_now():%H:%M:%S} ws {ev}: {str(msg)[:200]}", flush=True)
                return
            sym = data.get("symbol")
            if sym not in meta or "bids" not in data:
                return
            b, k = (data.get("bids") or [{}])[0], (data.get("asks") or [{}])[0]
            BOOK[sym] = {"b": b.get("price"), "bs": b.get("size"), "a": k.get("price"), "as": k.get("size")}
            pm = meta[sym]
            n = _now()
            MID.update(pm["end"], pm["strike"], pm["cp"], b.get("price"), k.get("price"),
                       n.hour * 3600 + n.minute * 60 + n.second)
            _FEED["mono"] = time.monotonic()

        ws.on("message", on_message)
        ws.on("error", lambda e: print(f"{_now():%H:%M:%S} ws error: {e}", flush=True))
        ws.on("disconnect", lambda c, m: print(f"{_now():%H:%M:%S} ws disconnect {c} {m}", flush=True))
        ws.connect()
        if ws.error is not None:
            raise RuntimeError(f"ws auth failed: {ws.error}")
        for p in picks:
            ws.subscribe({"channel": "books", "symbol": p["symbol"], "afterHours": a.session == "AFTERHOURS"})
            time.sleep(0.03)
        print(f"{_now():%H:%M:%S} 已訂閱 {len(picks)} 檔", flush=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"txo_15s_{_now():%Y-%m-%d}.jsonl"
        vix_out = FUTPRICE_DIR / f"vixtwn_1m_{_now():%Y-%m-%d}.jsonl"   # 儀表板台指圖讀這個
        cur = {"label": None, "v": None, "vs": []}                              # 該分鐘每 15 秒算出的值,落地取中位數

        def _flush_minute() -> None:
            vs = sorted(cur["vs"])
            if cur["label"] and vs:
                med = vs[len(vs) // 2] if len(vs) % 2 else round((vs[len(vs) // 2 - 1] + vs[len(vs) // 2]) / 2, 2)
                with vix_out.open("a", encoding="utf-8") as vf:
                    vf.write(json.dumps({"t": cur["label"], "v": med}) + "\n")

        while _now().strftime("%H:%M") < END_HHMM:
            time.sleep(SNAP_SEC)
            n = _now()
            t = n.strftime("%H:%M:%S")
            label = n.strftime("%H:%M")
            v = None
            if time.monotonic() - _FEED["mono"] <= FEED_STALE_SEC:
                v = SPIKE.accept(minute_vix(MID, n.date(), n.hour * 3600 + n.minute * 60 + n.second))
            if label != cur["label"]:
                _flush_minute()
                cur["label"], cur["v"], cur["vs"] = label, v, ([v] if v is not None else [])
            elif v is not None:
                cur["v"] = v
                cur["vs"].append(v)
            with out.open("a", encoding="utf-8") as f:
                for sym, bk in list(BOOK.items()):
                    p = meta[sym]
                    f.write(json.dumps({"t": t, "s": sym, "k": p["strike"], "cp": p["cp"], "e": p["end"], **bk},
                                       ensure_ascii=False) + "\n")
            print(f"{t} 有簿 {len(BOOK)}/{len(picks)} vix {cur['v']}", flush=True)
        _flush_minute()
    finally:
        # 2026-10-21 實測:13:45 迴圈結束後行程卡住不退出(0% CPU,疑似 ws 執行緒/登出阻塞),
        # 所以收尾前先掛 30 秒看門狗,強制結束。
        threading.Timer(30.0, lambda: os._exit(0)).start()
        safe_logout(sess)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)   # 不等 ws 非 daemon 執行緒
