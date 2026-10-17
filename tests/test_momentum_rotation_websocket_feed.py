"""order.momentum_rotation_websocket_feed · websocket trades feed + REST fallback.

比照 test_tmf_channel_ws_feed.py 同一份「絕不比純REST更差」的驗證骨架
（見該檔docstring）——這裡多驗證的是momentum-rotation特有的兩點：(1) 12檔
共用一個feed instance、按symbol各自累積buffer；(2) 'trades'頻道是append-only
逐筆事件，drain_as_bars()要吐出跟fetch_ticks_as_bars完全同shape的
list[{t,o,h,l,c,v}]且drain後buffer要清空，不是像candles那樣覆蓋式snapshot。
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from order import momentum_rotation_websocket_feed as wsf

_TZ = ZoneInfo("Asia/Taipei")


def _micros(dt_str: str) -> int:
    dt = datetime.fromisoformat(dt_str).replace(tzinfo=_TZ)
    return int(dt.timestamp() * 1_000_000)


def _trades_push(symbol: str, trades: list[dict], time_micros: int) -> str:
    return json.dumps(
        {
            "event": "data",
            "channel": "trades",
            "data": {"symbol": symbol, "trades": trades, "time": time_micros, "serial": 1},
        }
    )


class WebsocketTradesFeedTest(unittest.TestCase):
    def _fake_session(self):
        session = MagicMock()
        ws = session.sdk.marketdata.websocket_client.futopt
        return session, ws

    def test_start_subscribes_all_symbols_once(self):
        session, ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6", "FFFH6"])
        feed.start()
        session.init_realtime.assert_called_once()
        ws.connect.assert_called_once()
        self.assertEqual(
            ws.subscribe.call_args_list,
            [
                unittest.mock.call({"channel": "trades", "symbol": "RAFH6"}),
                unittest.mock.call({"channel": "trades", "symbol": "FFFH6"}),
            ],
        )
        self.assertTrue(feed._started)

    def test_start_is_idempotent(self):
        session, ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        feed.start()
        ws.connect.assert_called_once()
        session.init_realtime.assert_called_once()

    def test_not_fresh_before_any_message(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        self.assertFalse(feed.fresh())
        self.assertEqual(feed.drain_as_bars("RAFH6"), [])

    def test_single_trade_push_buffers_by_symbol(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6", "FFFH6"])
        feed.start()
        t = _micros("2026-08-13T09:00:00")
        feed._on_message(_trades_push("RAFH6", [{"price": 100.0, "size": 1, "bid": 99.5, "ask": 100.0}], t))
        self.assertTrue(feed.fresh())
        self.assertEqual(feed._buffers["RAFH6"], [{"price": 100.0, "size": 1, "time": t}])
        self.assertEqual(feed._buffers["FFFH6"], [])

    def test_multiple_trades_in_one_push_all_buffered(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        t = _micros("2026-08-13T09:00:00")
        feed._on_message(
            _trades_push(
                "RAFH6",
                [{"price": 100.0, "size": 1}, {"price": 100.5, "size": 2}],
                t,
            )
        )
        self.assertEqual(len(feed._buffers["RAFH6"]), 2)

    def test_unknown_symbol_ignored(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        feed._on_message(_trades_push("XXXX", [{"price": 1.0, "size": 1}], _micros("2026-08-13T09:00:00")))
        self.assertFalse(feed.fresh())
        self.assertEqual(feed._buffers["RAFH6"], [])

    def test_non_trades_channel_ignored(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        feed._on_message(json.dumps({"event": "data", "channel": "quote", "data": {"symbol": "RAFH6"}}))
        self.assertFalse(feed.fresh())

    def test_malformed_message_never_raises(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        feed._on_message("not json")
        feed._on_message(json.dumps({"event": "heartbeat"}))
        feed._on_message(json.dumps({"event": "data", "channel": "trades", "data": None}))
        feed._on_message(json.dumps({"event": "data", "channel": "trades", "data": {"symbol": "RAFH6", "trades": []}}))
        self.assertFalse(feed.fresh())

    def test_disconnect_marks_not_started_and_stale(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        feed._on_message(_trades_push("RAFH6", [{"price": 1.0, "size": 1}], _micros("2026-08-13T09:00:00")))
        feed._on_disconnect(1006, "abnormal closure")
        self.assertFalse(feed._started)
        self.assertFalse(feed.fresh())

    def test_stale_beyond_ttl_is_not_fresh(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        feed._on_message(_trades_push("RAFH6", [{"price": 1.0, "size": 1}], _micros("2026-08-13T09:00:00")))
        feed._last_msg_mono -= 1000.0
        self.assertFalse(feed.fresh(max_age_sec=30.0))

    def test_drain_as_bars_matches_tick_shape_and_empties_buffer(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        t1 = _micros("2026-08-13T09:00:00")
        t2 = _micros("2026-08-13T09:00:01")
        feed._on_message(_trades_push("RAFH6", [{"price": 100.0, "size": 1}], t1))
        feed._on_message(_trades_push("RAFH6", [{"price": 100.5, "size": 2}], t2))
        bars = feed.drain_as_bars("RAFH6")
        self.assertEqual(len(bars), 2)
        for bar in bars:
            self.assertEqual(set(bar.keys()), {"t", "o", "h", "l", "c", "v"})
        self.assertEqual([b["c"] for b in bars], [100.0, 100.5])
        # drain清空buffer，第二次drain應該是空的（不是重複吐出舊資料）
        self.assertEqual(feed.drain_as_bars("RAFH6"), [])

    def test_drain_outside_day_window_dropped(self):
        session, _ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.start()
        t_night = _micros("2026-08-13T20:00:00")
        feed._on_message(_trades_push("RAFH6", [{"price": 100.0, "size": 1}], t_night))
        self.assertEqual(feed.drain_as_bars("RAFH6"), [])

    def test_stop_unsubscribes_and_disconnects(self):
        session, ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6", "FFFH6"])
        feed.start()
        feed.stop()
        self.assertFalse(feed._started)
        ws.disconnect.assert_called_once()
        self.assertEqual(ws.unsubscribe.call_count, 2)

    def test_stop_before_start_is_noop(self):
        session, ws = self._fake_session()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed.stop()
        ws.disconnect.assert_not_called()


class FetchTicksAsBarsWsTest(unittest.TestCase):
    def setUp(self):
        wsf._FEED = None
        self._env_backup = os.environ.get("ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED")

    def tearDown(self):
        wsf._FEED = None
        if self._env_backup is None:
            os.environ.pop("ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED", None)
        else:
            os.environ["ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED"] = self._env_backup

    def test_default_disabled_returns_none_without_touching_session(self):
        os.environ.pop("ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED", None)
        session = MagicMock()
        result = wsf.fetch_ticks_as_bars_ws(session, "RAFH6", all_symbols=["RAFH6"])
        self.assertIsNone(result)
        session.init_realtime.assert_not_called()

    def test_explicitly_disabled_returns_none(self):
        os.environ["ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED"] = "0"
        session = MagicMock()
        self.assertIsNone(wsf.fetch_ticks_as_bars_ws(session, "RAFH6", all_symbols=["RAFH6"]))

    def test_start_exception_falls_back_to_none(self):
        os.environ["ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED"] = "1"
        session = MagicMock()
        session.init_realtime.side_effect = RuntimeError("boom")
        self.assertIsNone(wsf.fetch_ticks_as_bars_ws(session, "RAFH6", all_symbols=["RAFH6"]))

    def test_not_yet_fresh_returns_none(self):
        os.environ["ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED"] = "1"
        session = MagicMock()
        result = wsf.fetch_ticks_as_bars_ws(session, "RAFH6", all_symbols=["RAFH6"])
        self.assertIsNone(result)

    def test_fresh_feed_returns_drained_bars(self):
        os.environ["ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED"] = "1"
        session = MagicMock()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed._started = True
        feed._buffers["RAFH6"] = [{"price": 100.0, "size": 1, "time": _micros("2026-08-13T09:00:00")}]
        feed._last_msg_mono = wsf.time.monotonic()
        wsf._FEED = feed
        result = wsf.fetch_ticks_as_bars_ws(session, "RAFH6", all_symbols=["RAFH6"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["c"], 100.0)

    def test_symbol_set_change_rebuilds_feed_and_stops_old(self):
        os.environ["ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED"] = "1"
        session = MagicMock()
        old_feed = MagicMock()
        old_feed._session = session
        old_feed._symbols = ["RAFH6"]
        old_feed._started = True
        wsf._FEED = old_feed
        wsf.fetch_ticks_as_bars_ws(session, "RAFH6", all_symbols=["RAFH6", "FFFH6"])
        old_feed.stop.assert_called_once()
        self.assertIsNot(wsf._FEED, old_feed)
        self.assertEqual(set(wsf._FEED._symbols), {"RAFH6", "FFFH6"})

    def test_session_change_rebuilds_feed(self):
        os.environ["ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED"] = "1"
        old_session = MagicMock()
        old_feed = wsf.WebsocketTradesFeed(old_session, ["RAFH6"])
        old_feed._started = True
        wsf._FEED = old_feed
        new_session = MagicMock()
        wsf.fetch_ticks_as_bars_ws(new_session, "RAFH6", all_symbols=["RAFH6"])
        self.assertIs(wsf._FEED._session, new_session)


class StopFeedTest(unittest.TestCase):
    def setUp(self):
        wsf._FEED = None

    def tearDown(self):
        wsf._FEED = None

    def test_stop_feed_clears_singleton_and_calls_stop(self):
        session = MagicMock()
        feed = wsf.WebsocketTradesFeed(session, ["RAFH6"])
        feed._started = True
        wsf._FEED = feed
        wsf.stop_feed()
        self.assertIsNone(wsf._FEED)
        self.assertFalse(feed._started)

    def test_stop_feed_without_feed_is_noop(self):
        wsf.stop_feed()  # 不應該raise
        self.assertIsNone(wsf._FEED)


if __name__ == "__main__":
    unittest.main()
