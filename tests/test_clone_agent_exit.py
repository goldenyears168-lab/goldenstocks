"""複製人出場(小哥:反向竭盡/最大停損 5%/13:15 平倉)。假券商、不碰真 API。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from order import clone_agent_exit as cx
from order import clone_agent_order as co
from test_clone_agent_order import DAY, NOW, POS, FakeBroker, card

TZ = co.TZ
POL = cx.load_policy()


def at(h, m, s=0):
    return datetime(2026, 10, 6, h, m, s, tzinfo=TZ)


class PolicyTest(unittest.TestCase):
    def test_policy_values_from_book(self):
        self.assertEqual(POL["stop_loss_pct"], 5.0)
        self.assertEqual(POL["time_flat_hm"], "13:15")
        self.assertEqual(POL["exit_signal"]["long_exit_on"].startswith("買盤竭盡"), True)

    def test_evaluate_priority_and_conditions(self):
        held = at(10, 31).timestamp()
        e = {"price": 1000.0, "sid": "2327", "held_since": held}
        now = at(11, 0).timestamp()
        ev_up = {"date": DAY, "sid": "2327", "t": 11 * 3600 - 20, "d": 1, "px": 1500}
        ev_dn = {**ev_up, "d": -1}
        self.assertIsNone(cx.evaluate_exit(e, 990.0, now, [], POL))
        self.assertEqual(cx.evaluate_exit(e, 949.0, now, [], POL)["reason"], "stop_loss")
        self.assertEqual(cx.evaluate_exit(e, 990.0, now, [ev_up], POL)["reason"], "reverse_exhaustion")
        self.assertIsNone(cx.evaluate_exit(e, 990.0, now, [ev_dn], POL))                      # 賣盤竭盡不是多單的反向
        self.assertIsNone(cx.evaluate_exit(e, 1010.0, now, [ev_up], POL))                     # 賺錢:預設不因買盤竭盡出
        self.assertIsNone(cx.evaluate_exit(e, 990.0, now, [{**ev_up, "sid": "9999"}], POL))   # 別檔
        self.assertIsNone(cx.evaluate_exit(e, 990.0, now, [{**ev_up, "t": 10 * 3600}], POL))  # 進場前的事件
        self.assertIsNone(cx.evaluate_exit(e, 990.0, now, [{**ev_up, "t": 11 * 3600 - 300}], POL))  # 太舊
        self.assertEqual(cx.evaluate_exit(e, None, at(13, 15).timestamp(), [], POL)["reason"], "time_flat")
        self.assertIsNone(cx.evaluate_exit(e, None, now, [ev_up], POL))                        # 沒報價:不判竭盡/停損


class ExitFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("GOLDENSTOCKS_DATA_DIR")
        os.environ["GOLDENSTOCKS_DATA_DIR"] = self.tmp.name
        d = Path(self.tmp.name) / "data" / "cache"
        d.mkdir(parents=True, exist_ok=True)
        (d / "taifex_stock_futures_margin_rates.json").write_text(json.dumps({k: {"原始%": 20.25} for k in ("QDF", "RKF", "HBF", "IRF")}), encoding="utf-8")
        (d / "taifex_stock_futures_specs.json").write_text(json.dumps({k: {"contract_shares": 2000} for k in ("QD", "RK", "HB", "IR")}), encoding="utf-8")
        self.cfg = co.Config(dry_run=False)
        self.cfg_exit = co.Config(dry_run=False, exit_enabled=True)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GOLDENSTOCKS_DATA_DIR", None)
        else:
            os.environ["GOLDENSTOCKS_DATA_DIR"] = self._old
        self.tmp.cleanup()

    def books(self, bid, ts):
        p = Path(self.tmp.name) / "data" / "cache" / "qd_books" / f"qd_books_{DAY}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": ts.isoformat(), "event": "data", "root": "QD", "bids": [{"price": bid, "size": 3}],
               "asks": [{"price": bid + 1, "size": 3}], "session": "day", "stale": False}
        p.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    def events(self, *evs):
        p = Path(self.tmp.name) / "data" / "biglot_forward" / "exhaustion_events.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(e) + "\n" for e in evs), encoding="utf-8")

    def held_position(self, b):
        c = card()
        p = Path(self.tmp.name) / "data" / "clone_agent" / f"candidates_{DAY}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(c) + "\n", encoding="utf-8")
        co.tick(b, self.cfg, NOW)
        b.results = [{"order_no": "N1", "status": 50, "filled_lot": 1}]
        b.positions = [POS]
        co.tick(b, self.cfg, at(10, 31))
        return next(iter(co.load_ledger(DAY)["entries"].values()))

    def test_signal_only_by_default_no_sell(self):
        b = FakeBroker()
        self.held_position(b)
        self.books(440.0, at(11, 0))                         # 471.5 → 440 = −6.7%
        co.tick(b, self.cfg, at(11, 0, 5))
        e = next(iter(co.load_ledger(DAY)["entries"].values()))
        self.assertEqual(e["exit"]["signal"]["reason"], "stop_loss")
        self.assertFalse(getattr(b, "sold", []))

    def test_enabled_sells_ioc_below_bid_with_retry_gap(self):
        b = FakeBroker()
        self.held_position(b)
        self.books(440.0, at(11, 0))
        co.tick(b, self.cfg_exit, at(11, 0, 5))
        self.assertEqual(b.sold, [("QDFJ6", 1, "439")])      # 買一 440 − 2 檔(1 元)
        self.books(440.0, at(11, 0, 20))
        co.tick(b, self.cfg_exit, at(11, 0, 25))             # 距上次 20 秒 <30 秒:不重送
        self.assertEqual(len(b.sold), 1)
        self.books(440.0, at(11, 0, 40))
        co.tick(b, self.cfg_exit, at(11, 0, 45))
        self.assertEqual(len(b.sold), 2)

    def test_no_sell_when_quote_stale_or_position_unknown(self):
        b = FakeBroker()
        self.held_position(b)
        self.books(440.0, at(10, 50))                        # 10 分鐘前的報價
        co.tick(b, self.cfg_exit, at(11, 0, 5))
        self.assertFalse(getattr(b, "sold", []))
        self.books(440.0, at(11, 0, 4))
        b.positions = []                                     # 券商端查無部位
        co.tick(b, self.cfg_exit, at(11, 2, 0))
        self.assertFalse(getattr(b, "sold", []))

    def test_time_flat_without_quote_still_signals(self):
        b = FakeBroker()
        self.held_position(b)
        co.tick(b, self.cfg, at(13, 16))
        e = next(iter(co.load_ledger(DAY)["entries"].values()))
        self.assertEqual(e["exit"]["signal"]["reason"], "time_flat")
        self.assertFalse(getattr(b, "sold", []))             # 沒報價 → 不賣(fail-closed)

    def test_reverse_exhaustion_exit_signal(self):
        b = FakeBroker()
        self.held_position(b)
        self.books(468.0, at(11, 0))
        self.events({"date": DAY, "sid": "3532", "t": 11 * 3600 - 10, "d": 1, "n": 12, "nb5": 4, "px": 469.0})
        co.tick(b, self.cfg, at(11, 0, 5))
        e = next(iter(co.load_ledger(DAY)["entries"].values()))
        self.assertEqual(e["exit"]["signal"]["reason"], "reverse_exhaustion")

    def test_max_concurrent_positions(self):
        b = FakeBroker()
        for sid, root in (("1", "QD"), ("2", "RK"), ("3", "HB"), ("4", "IR")):
            p = Path(self.tmp.name) / "data" / "clone_agent" / f"candidates_{DAY}.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(card(sid, root, 100.0 + int(sid), margin=100_000)) + "\n")
        co.tick(b, self.cfg, NOW)
        ents = list(co.load_ledger(DAY)["entries"].values())
        self.assertEqual(sum(1 for e in ents if e["status"] != "skipped"), 3)
        self.assertEqual([e["reason"] for e in ents if e["status"] == "skipped"], ["max_concurrent_positions"])


class BrokerImportTest(unittest.TestCase):
    def test_live_broker_imports_resolve(self):
        import importlib
        importlib.import_module("tmf_channel.session_pool")
        m = importlib.import_module("order.tmf_channel_marketdata")
        self.assertTrue(callable(m.resolve_live_futures_symbol))


if __name__ == "__main__":
    unittest.main()
