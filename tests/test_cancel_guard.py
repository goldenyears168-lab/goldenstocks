"""撤單神器單元測試：全程不碰券商、不讀真實 cache。"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from order import cancel_guard as cg

T0 = datetime.fromisoformat("2026-10-21T10:00:00+08:00").timestamp()


class FakeBroker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def cancel(self, order_no: str) -> dict:
        self.calls.append(order_no)
        return {"ok": True, "status": "cancelled", "order_no": order_no}


def _iso(off: float) -> str:
    return datetime.fromtimestamp(T0 + off, cg.TZ).isoformat(timespec="milliseconds")


def _order(no: str, off: float, side: str = "Buy", price: float = 100.0, otype: str = "New", sym: str = "FILEF", kind="futopt_order[1]", status=10):
    return {"ts": _iso(off), "kind": kind, "payload": {"order_no": no, "symbol": sym, "buy_sell": f"BSAction.{side}", "price": price,
            "after_price": price, "status": status, "order_type": f"FutOptOrderType.{otype}", "price_type": "FutOptPriceType.Limit"}}


def _book(off: float, bid: float, ask: float, sym: str = "LEFJ6") -> dict:
    return {"ts": _iso(off), "event": "data", "session": "day", "stale": False, "symbol": sym,
            "bids": [{"price": bid, "size": 3}], "asks": [{"price": ask, "size": 3}]}


class CancelGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "le_books").mkdir()
        self.ev = self.tmp / "tmf_channel" / "tick_seconds"; self.ev.mkdir(parents=True)
        self.bk = self.tmp / "le_books" / "le_books_2026-10-21.jsonl"
        self.broker = FakeBroker()
        self.now = T0 + 5
        self.g = cg.Guard(broker=self.broker, cache_dir=self.tmp, order_dir=self.tmp, clock=lambda: self.now)
        self.g.set_day("2026-10-21")

    def _w(self, path: Path, rows: list[dict]) -> None:
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def _ev(self, rows): self._w(self.ev / "futopt_fill_events_2026-10-21.jsonl", rows)

    def _sw(self, **kw): return {**cg.DEFAULT_SWITCH, **kw}

    def test_tick_and_helpers(self):
        self.assertEqual([cg.tick_size(p) for p in (9, 30, 80, 300, 1200, 3000)], [0.01, 0.05, 0.1, 0.5, 1.0, 5.0])
        self.assertEqual(cg.product_of("FILEF"), "LEF")
        self.assertEqual(cg.against_ticks(1, 100, 99, 0.5), 2.0)      # 買單，中價跌 1 元＝不利 2 tick
        self.assertEqual(cg.against_ticks(-1, 100, 99, 0.5), -2.0)    # 賣單，中價跌＝有利

    def test_mode_matrix_and_fail_closed(self):
        d = cg.DEFAULT_SWITCH
        self.assertEqual(cg.mode_of(d, True), "off")
        self.assertEqual(cg.mode_of({**d, "enabled": True}, True), "alert")
        self.assertEqual(cg.mode_of({**d, "enabled": True, "auto_cancel": True}, True), "dry_cancel")
        self.assertEqual(cg.mode_of({**d, "enabled": True, "auto_cancel": True}, False), "live_cancel")
        self.assertEqual(cg.load_switch(self.tmp / "nope.json"), cg.DEFAULT_SWITCH)          # 缺檔→全關
        (self.tmp / "bad.json").write_text("{not json")
        self.assertFalse(cg.load_switch(self.tmp / "bad.json")["enabled"])                    # 壞檔→全關

    def _setup_buy(self):
        self._w(self.bk, [_book(-1, 99.5, 100.0)])
        self._ev([_order("A1", 0, price=99.5)])

    def test_off_does_nothing(self):
        self._setup_buy(); self._w(self.bk, [_book(2, 98.5, 99.0)])
        self.assertEqual(self.g.step(self._sw(), True), [])
        self.assertEqual(self.broker.calls, [])

    def test_alert_mode_triggers_without_cancel(self):
        self._setup_buy(); self._w(self.bk, [_book(2, 98.5, 99.0)])
        recs = self.g.step(self._sw(enabled=True), False)
        self.assertEqual([r["action"] for r in recs], ["alert"])
        self.assertEqual(self.broker.calls, [])

    def test_small_move_does_not_trigger(self):
        self._setup_buy(); self._w(self.bk, [_book(2, 99.5, 100.0)])
        self.assertEqual(self.g.step(self._sw(enabled=True, auto_cancel=True), False), [])

    def test_dry_cancel_never_calls_broker(self):
        self._setup_buy(); self._w(self.bk, [_book(2, 98.5, 99.0)])
        recs = self.g.step(self._sw(enabled=True, auto_cancel=True), True)
        self.assertEqual(recs[0]["action"], "would_cancel"); self.assertEqual(self.broker.calls, [])

    def test_live_cancel_once_and_daily_cap(self):
        self._setup_buy(); self._w(self.bk, [_book(2, 98.5, 99.0)])
        sw = self._sw(enabled=True, auto_cancel=True, max_cancels_per_day=1)
        recs = self.g.step(sw, False)
        self.assertEqual(recs[0]["action"], "cancelled"); self.assertEqual(self.broker.calls, ["A1"])
        self._ev([_order("A2", 3, price=98.5)]); self._w(self.bk, [_book(3.5, 98.0, 98.5), _book(5, 97.0, 97.5)])
        recs = self.g.step(sw, False)
        self.assertEqual([r["action"] for r in recs], ["alert_daily_cap_reached"]); self.assertEqual(self.broker.calls, ["A1"])
        self.assertEqual(self.g.step(sw, False), [])                # 同一張單不再觸發

    def test_sell_order_against_is_price_up(self):
        self._w(self.bk, [_book(-1, 100.0, 100.5)])
        self._ev([_order("S1", 0, side="Sell", price=100.5)])
        self._w(self.bk, [_book(2, 101.0, 101.5)])
        recs = self.g.step(self._sw(enabled=True), False)
        self.assertEqual(recs[0]["side"], "賣"); self.assertEqual(recs[0]["against_ticks"], 2.0)

    def test_close_orders_monitored_by_default_and_can_be_excluded(self):
        self.assertEqual(cg.DEFAULT_SWITCH["apply_to"], ["New", "Close"])
        self._w(self.bk, [_book(-1, 99.5, 100.0)])
        self._ev([_order("C1", 0, price=99.5, otype="Close")])
        self._w(self.bk, [_book(2, 98.5, 99.0)])
        recs = self.g.step(self._sw(enabled=True, apply_to=["New"]), False)               # 使用者設定只監控新倉 → 平倉單不碰
        self.assertEqual([r for r in recs if r.get("event") == "trigger"], [])
        g2 = cg.Guard(broker=self.broker, cache_dir=self.tmp, order_dir=self.tmp, clock=lambda: self.now); g2.set_day("2026-10-21")
        recs = g2.step(self._sw(enabled=True, auto_cancel=True), True)                    # 預設含平倉 → 觸發，標註部位暫無出場單
        trig = [r for r in recs if r.get("event") == "trigger"]
        self.assertEqual(trig[0]["type"], "Close"); self.assertTrue(trig[0]["exit_unprotected"])

    def test_marketable_and_non_stock_future_skipped(self):
        self._w(self.bk, [_book(-1, 99.5, 100.0)])
        self._ev([_order("M1", 0, price=100.0), _order("X1", 0, sym="FITM")])
        self._w(self.bk, [_book(2, 98.5, 99.0)])
        recs = self.g.step(self._sw(enabled=True, auto_cancel=True), False)
        self.assertEqual([r for r in recs if r.get("event") == "trigger"], [])    # 跨價差單不碰
        self.assertEqual(self.broker.calls, [])
        self.assertTrue(any(r.get("event") == "skip_not_stock_future" for r in recs))

    def test_root_with_stopped_collector_is_skipped(self):
        import os
        old = self.tmp / "ck_books"; old.mkdir()                     # CKF 的買賣簿目錄存在，但檔案很舊＝已停收
        f = old / "ck_books_2026-09-01.jsonl"; f.write_text("{}\n")
        os.utime(f, (T0 - 10 * 86400, T0 - 10 * 86400))
        self.assertFalse(cg.books_live("ck", self.tmp))
        self._ev([_order("K1", 0, price=100.0, sym="FICKF")])
        recs = self.g.step(self._sw(enabled=True, auto_cancel=True), False)
        self.assertTrue(any(r.get("event") == "skip_no_books" for r in recs))
        self.assertNotIn("K1", self.g.orders)

    def test_contract_roll_does_not_trigger(self):
        self._setup_buy()
        self._w(self.bk, [_book(2, 90.0, 90.5, sym="LEFK6")])        # 換月後的另一個合約，價位差很多，不是「行情往不利走」
        self.assertEqual(self.g.step(self._sw(enabled=True, auto_cancel=True), False), [])
        self.assertEqual(self.broker.calls, [])

    def test_stale_book_record_never_cancels(self):
        self._setup_buy(); self._w(self.bk, [_book(2, 98.5, 99.0)])
        self.now = T0 + 120                                         # 買賣簿紀錄已是 2 分鐘前
        recs = self.g.step(self._sw(enabled=True, auto_cancel=True), False)
        self.assertEqual(recs[0]["action"], "alert_feed_stale_no_cancel"); self.assertEqual(self.broker.calls, [])

    def test_fill_and_cancel_events_stop_guarding(self):
        self._setup_buy(); self.g.step(self._sw(enabled=True), False)
        self._ev([{"ts": _iso(1), "kind": "futopt_filled[1]", "payload": {"order_no": "A1", "symbol": "FILEF", "buy_sell": "BSAction.Buy", "filled_price": 99.5}}])
        self._w(self.bk, [_book(2, 98.5, 99.0)])
        self.assertEqual(self.g.step(self._sw(enabled=True), False), [])


if __name__ == "__main__":
    unittest.main()
