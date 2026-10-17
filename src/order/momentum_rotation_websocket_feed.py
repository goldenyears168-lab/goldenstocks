"""momentum-rotation 專用：逐筆成交(trades)websocket feed，取代REST
intraday.trades()輪詢當即時資料來源。

比照 tmf_channel_ws_feed.py（TMF sleeve既有、已經在production跑的
「優先websocket、新鮮度不足自動退回REST」手法，見該檔docstring沿革）同一套
設計骨架，差異點：

- momentum-rotation 12檔同時訂閱（TMF只有1檔前月合約）——這裡一個feed
  instance管全部symbol的訂閱與buffer，不是per-(session,symbol)各開一個。
- 'trades'頻道推送的是逐筆事件（append-only），不像candles那樣「同一天的
  bar會被新推送覆蓋」的冪等snapshot——這裡改用「per-symbol累積buffer +
  drain清空」，drain_as_bars()吐出的shape刻意跟
  momentum_rotation_tick_marketdata.fetch_ticks_as_bars完全一樣
  （list[{t,o,h,l,c,v}]），呼叫端(_feed_new_bars)不用改一行狀態機邏輯。
- Speed模式（跟live worker既有session的init_realtime()預設同一個mode，不用
  像TMF那樣為了candles/aggregates頻道另開Normal模式）——2026-08-13已用
  momentum_rotation_websocket_trades_shadow.py實測確認'trades'頻道沒有被
  Speed模式擋掉（只有candles/aggregates被擋，見fubon_neo/adapter.py的
  WebSocketFutOptClientWrapper.subscribe）；shadow腳本也用同一個
  connect_fubon(realtime=True)驗證過真實推送訊息格式（見本檔_on_message對
  該格式的解析假設）。

Kill switch：ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED=0/1（預設0=關）。
跟tmf_channel_ws_feed.py預設開不同——那支已經在production跑過夠久、驗證過
一整個交易日的行為；這支只跑過幾分鐘的2檔shadow smoke test，比照這個repo
「新機制先關閉、觀察一段時間再明確開啟」的一貫作法（見CLAUDE.md下單層安全
預設段落），刻意先預設關閉，不是TMF那支的預設開。

已知、接受的殘留風險（跟TMF那支同一個等級，不重複解決）：
- 斷線不主動reconnect，靠fresh()新鮮度判斷退回REST（同一份保守設計）；
  worker長時間運行下若websocket斷線，效果等同永久退回REST，不會更糟。
- session更新（session pool ~3500s過期或例外後reset_session_pool()）時，
  舊feed的websocket連線用stop()明確關閉（比TMF那支「直接放著不管」更進一步
  ——momentum-rotation是12檔連續輪詢，背景執行緒/socket數量比TMF的1檔更值得
  乾淨關閉，這裡選擇比照這個repo一貫「不留孤兒背景執行緒」的偏好處理）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from order.fubon_session import FubonSession
from order.momentum_rotation_tick_marketdata import _dedupe_same_second, _normalize_tick
from order.tmf_channel_marketdata import _ensure_realtime_once, _in_day_window


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


class WebsocketTradesFeed:
    def __init__(self, session: FubonSession, symbols: list[str]) -> None:
        self._session = session
        self._symbols = list(symbols)
        self._lock = threading.Lock()
        self._buffers: dict[str, list[dict[str, Any]]] = {s: [] for s in self._symbols}
        self._last_msg_mono = 0.0
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        # 沿用momentum_rotation_tick_marketdata/tmf_channel_marketdata既有的
        # 「每個session object至多init_realtime一次」guard（見_ensure_realtime_once
        # docstring），Speed模式（預設），跟live worker既有session一致，不用
        # 像TMF candles那樣切換Normal模式。
        _ensure_realtime_once(self._session)
        ws = self._session.sdk.marketdata.websocket_client.futopt
        ws.on("message", self._on_message)
        ws.on("disconnect", self._on_disconnect)
        ws.connect()
        for sym in self._symbols:
            ws.subscribe({"channel": "trades", "symbol": sym})
        self._started = True

    def _on_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:  # noqa: BLE001
            return
        if msg.get("event") != "data" or msg.get("channel") != "trades":
            return
        data = msg.get("data")
        if not isinstance(data, dict):
            return
        symbol = data.get("symbol")
        trades = data.get("trades")
        outer_time = data.get("time")
        if symbol not in self._buffers or not isinstance(trades, list) or not trades:
            return
        with self._lock:
            for tr in trades:
                if not isinstance(tr, dict):
                    continue
                self._buffers[symbol].append(
                    {"price": tr.get("price"), "size": tr.get("size"), "time": outer_time}
                )
            self._last_msg_mono = time.monotonic()

    def _on_disconnect(self, _code: Any, _msg: Any) -> None:
        # 比照tmf_channel_ws_feed.py：不主動reconnect，靠fresh()新鮮度判斷
        # 讓呼叫端自然退回REST，最差情況等同永久回退，不會比之前更糟。
        self._started = False

    def fresh(self, max_age_sec: float = 30.0) -> bool:
        with self._lock:
            if not self._started or self._last_msg_mono <= 0:
                return False
            return (time.monotonic() - self._last_msg_mono) < max_age_sec

    def drain_as_bars(self, symbol: str) -> list[dict[str, Any]]:
        with self._lock:
            raw = self._buffers.get(symbol) or []
            if raw:
                self._buffers[symbol] = []
        ticks = [n for n in (_normalize_tick(r) for r in raw) if n is not None]
        if not ticks:
            return []
        ticks.sort(key=lambda x: x["dt"])
        today = ticks[-1]["dt"].date()
        ticks = [tk for tk in ticks if _in_day_window(tk["dt"], today)]
        return _dedupe_same_second(ticks)

    def stop(self) -> None:
        with self._lock:
            started = self._started
            self._started = False
        if not started:
            return
        try:
            ws = self._session.sdk.marketdata.websocket_client.futopt
            for sym in self._symbols:
                try:
                    ws.unsubscribe({"channel": "trades", "symbol": sym})
                except Exception:  # noqa: BLE001
                    pass
            ws.disconnect()
        except Exception:  # noqa: BLE001
            pass


_FEED: WebsocketTradesFeed | None = None
_FEED_LOCK = threading.Lock()


def _get_or_start_feed(session: FubonSession, symbols: list[str]) -> WebsocketTradesFeed | None:
    global _FEED
    with _FEED_LOCK:
        feed = _FEED
        if feed is None or feed._session is not session or set(feed._symbols) != set(symbols):
            if feed is not None:
                feed.stop()
            feed = WebsocketTradesFeed(session, symbols)
            _FEED = feed
    if not feed._started:
        try:
            feed.start()
        except Exception:  # noqa: BLE001
            return None
    return feed


def fetch_ticks_as_bars_ws(
    session: FubonSession, symbol: str, *, all_symbols: list[str]
) -> list[dict[str, Any]] | None:
    """websocket優先版的fetch_ticks_as_bars。

    新鮮就回傳drain到的新tick（可能是空list，代表這段時間這檔沒有新成交，
    不是失敗）；feed還沒建好、啟動失敗、或太久沒收到任何訊息（整體連線不新鮮）
    才回傳None，呼叫端(_feed_new_bars)收到None要退回REST fetch_ticks_as_bars，
    不是把None當成「這檔沒訊號」處理。
    """
    if not _env_flag("ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED", "0"):
        return None
    feed = _get_or_start_feed(session, all_symbols)
    if feed is None or not feed.fresh():
        return None
    return feed.drain_as_bars(symbol)


def stop_feed() -> None:
    """worker乾淨關閉時呼叫，明確斷線＋清空singleton，避免留下孤兒背景執行緒。"""
    global _FEED
    with _FEED_LOCK:
        feed = _FEED
        _FEED = None
    if feed is not None:
        feed.stop()
