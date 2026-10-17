"""momentum-rotation · 現貨逐筆 websocket feed(2026-08-31 訊號源搬遷).

## 為什麼觸發要搬到現貨

2026-08-31 盲點檢定的裁決性發現:期貨 tape 觸發與現貨側研究**只有 46%/19% 交集**——
期貨獨有爆量 ret120 −6.73(顯著為負)、同時漏掉 81% 的真事件(仍有 +3.23 alpha);
「現貨與期貨同時爆」則是全戰役最強過濾器(ret30 +9.85 / ret120 +11.34,三 horizon
t>2.4,lag 中位 −0.4s = 同時非回聲)。因此:**觸發搬現貨、期貨爆量降為確認欄位**。

## 設計

比照 momentum_rotation_websocket_feed.py(期貨版,production 已驗證)同一套骨架:
- 單一 feed instance 管全部現貨 symbol 的訂閱與 buffer,drain 清空
- 訊息格式「未知原則」(沿 chip_intraday_lot_probe / shadow 腳本):.get 容錯,
  同時接受 data.trades 列表形(期貨版格式)與扁平形(股票版 data 直接帶
  symbol/price/size);不假設 schema,解析失敗靜默丟棄
- **volume 單位是「張」(1,000 股)**——與期貨的「口」(2,000 股)不同,金額換算
  在 Micro30State.unit_shares 處理,這裡只忠實轉發
- 斷線不自動重連:fresh() 過期 → 呼叫端拿不到現貨 tick → 不出訊號(fail-safe;
  dry-run 下最壞情況是沒訊號,log 可見)。現貨**沒有 REST fallback**(盤中重啟
  會少掉重啟前的窗內歷史,~10 分鐘暖機後恢復,dry-run 可接受,已知限制)

訂閱走同一個 Fubon session 的 `marketdata.websocket_client.stock`(與期貨的
.futopt 是不同 client,可並存;chip_intraday_lot_probe 實測過 stock trades 頻道
與訂閱上限 45×1)。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from order.fubon_session import FubonSession

_TZ = timezone(timedelta(hours=8))


class SpotTradesFeed:
    def __init__(self, session: FubonSession, symbols: list[str]) -> None:
        self._symbols = list(symbols)
        self._buffers: dict[str, list[dict[str, Any]]] = {s: [] for s in self._symbols}
        self._books: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._started = False
        self._last_msg_mono = 0.0
        self._session = session

    def start(self) -> bool:
        try:
            ws = self._session.sdk.marketdata.websocket_client.stock
            ws.on("message", self._on_message)
            ws.on("disconnect", self._on_disconnect)
            ws.connect()
            for s in self._symbols:
                for ch in ("trades", "books"):
                    try:
                        ws.subscribe({"channel": ch, "symbol": s})
                    except Exception:  # noqa: BLE001 -- 單檔訂閱失敗不擋其餘
                        continue
            self._started = True
            return True
        except Exception:  # noqa: BLE001
            self._started = False
            return False

    def _on_message(self, raw: Any) -> None:
        try:
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        if msg.get("event") not in (None, "data"):
            return
        data = msg.get("data")
        if not isinstance(data, dict):
            return
        symbol = str(data.get("symbol") or "")
        if symbol not in self._buffers:
            return
        if msg.get("channel") == "books":
            bids = data.get("bids"); asks = data.get("asks")
            if isinstance(bids, list) and isinstance(asks, list):
                with self._lock:
                    self._books[symbol] = {"bids": bids, "asks": asks,
                                           "mono": time.monotonic()}
                    self._last_msg_mono = time.monotonic()
            return
        if msg.get("channel") != "trades":
            return
        rows: list[dict[str, Any]] = []
        trades = data.get("trades")
        if isinstance(trades, list):                       # 期貨版格式(以防萬一)
            t_outer = data.get("time")
            for tr in trades:
                if isinstance(tr, dict):
                    rows.append({"price": tr.get("price"), "size": tr.get("size"),
                                 "time": tr.get("time") or t_outer})
        else:                                              # 股票版扁平格式
            rows.append({"price": data.get("price"), "size": data.get("size"),
                         "time": data.get("time")})
        with self._lock:
            self._buffers[symbol].extend(rows)
            self._last_msg_mono = time.monotonic()

    def _on_disconnect(self, _code: Any, _msg: Any) -> None:
        self._started = False                              # 靠 fresh() 讓呼叫端自然停訊號

    def fresh(self, max_age_sec: float = 60.0) -> bool:
        with self._lock:
            if not self._started or self._last_msg_mono <= 0:
                return False
            return (time.monotonic() - self._last_msg_mono) < max_age_sec

    def book_snapshot(self, symbol: str) -> dict[str, Any] | None:
        """觸發當下的五檔壓縮快照(觀察欄位,記錄不行動)。

        回傳 {b1,a1,bv,av,imb,spr_bp,age_s}:買一/賣一價、五檔買/賣總量(張)、
        失衡 (bv−av)/(bv+av)、價差 bp、快照年齡秒。解析不出來回 None。
        """
        with self._lock:
            bk = self._books.get(symbol)
        if not bk:
            return None
        try:
            bids = [(float(x["price"]), float(x["size"])) for x in bk["bids"][:5]
                    if isinstance(x, dict)]
            asks = [(float(x["price"]), float(x["size"])) for x in bk["asks"][:5]
                    if isinstance(x, dict)]
            if not bids or not asks:
                return None
            b1 = max(p for p, _ in bids); a1 = min(p for p, _ in asks)
            bv = sum(v for _, v in bids); av = sum(v for _, v in asks)
            return {
                "b1": b1, "a1": a1, "bv": bv, "av": av,
                "imb": round((bv - av) / (bv + av), 3) if bv + av > 0 else None,
                "spr_bp": round((a1 - b1) / b1 * 1e4, 1) if b1 > 0 else None,
                "age_s": round(time.monotonic() - bk["mono"], 1),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def drain_as_bars(self, symbol: str) -> list[dict[str, Any]]:
        """吐出跟 fetch_ticks_as_bars 同 shape 的 list[{t,o,h,l,c,v}](v=張)。"""
        with self._lock:
            raw = self._buffers.get(symbol) or []
            if raw:
                self._buffers[symbol] = []
        out: list[dict[str, Any]] = []
        for r in raw:
            try:
                px = float(r["price"]); sz = float(r["size"])
            except (KeyError, TypeError, ValueError):
                continue
            t = r.get("time")
            iso = None
            try:
                # Fubon time 慣例:微秒 epoch 整數(shadow 腳本實測);字串則原樣用
                if isinstance(t, (int, float)) and t > 1e14:
                    iso = datetime.fromtimestamp(t / 1e6, tz=_TZ).isoformat(timespec="milliseconds")
                elif isinstance(t, str) and len(t) >= 19:
                    iso = t
            except (ValueError, OSError, OverflowError):
                iso = None
            if iso is None:
                iso = datetime.now(tz=_TZ).isoformat(timespec="milliseconds")
            out.append({"t": iso, "o": px, "h": px, "l": px, "c": px, "v": sz})
        out.sort(key=lambda b: b["t"])
        return out


_FEED: SpotTradesFeed | None = None
_FEED_LOCK = threading.Lock()


def get_spot_feed(session: FubonSession, symbols: list[str]) -> SpotTradesFeed | None:
    """行程級單例。啟動失敗回 None(呼叫端視同沒有現貨 tick,不出訊號)。"""
    global _FEED
    with _FEED_LOCK:
        if _FEED is not None and _FEED.fresh(max_age_sec=600):
            return _FEED
        if _FEED is None or not _FEED._started:
            feed = SpotTradesFeed(session, symbols)
            if feed.start():
                _FEED = feed
        return _FEED


def peek_spot_feed() -> SpotTradesFeed | None:
    """只窺視既有單例、絕不建立連線——給觀察欄位(v3_meta book)用。"""
    with _FEED_LOCK:
        return _FEED
