"""複製人實單層:全部用假券商,不碰任何真 API。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from clone_agent import order_switch as sw
from order import clone_agent_order as co

TZ = co.TZ
DAY = "2026-10-06"
NOW = datetime(2026, 10, 6, 10, 30, 30, tzinfo=TZ)
LATER = datetime(2026, 10, 6, 10, 31, 0, tzinfo=TZ)
POS = {"symbol": "FIQDF", "side": "L", "lots": 1, "price": 471.5}


class FakeBroker:
    def __init__(self, results=None, place_exc=None, positions=None, avail=10_000_000, account_fail=None):
        self.placed, self.results, self.place_exc, self.n = [], results or [], place_exc, 0
        self.positions, self.avail, self.account_fail, self.calls = positions or [], avail, account_fail, 0

    def fetch_account(self):
        self.calls += 1
        if self.account_fail:
            return {"orders": None, "positions": None, "available_margin": None, "errors": {self.account_fail: "boom"}}
        return {"orders": self.results, "positions": self.positions, "available_margin": self.avail, "errors": {}}

    def resolve_contract(self, root):
        return None if root == "ZZ" else f"{root}FJ6"

    def place_buy(self, contract, lot, price_text):
        if self.place_exc:
            raise self.place_exc
        self.n += 1
        self.placed.append((contract, lot, price_text))
        return {"order_no": f"N{self.n}", "seq_no": f"S{self.n}", "dry": False}

    def fetch_results(self):
        return self.results

    def cancel_order(self, order_no):
        self.cancelled = getattr(self, "cancelled", [])
        if getattr(self, "cancel_exc", None):
            raise self.cancel_exc
        self.cancelled.append(order_no)
        return {"ok": True, "status": "cancelled"}


def card(sid="3532", root="QD", price=471.5, margin=203688, time="10:30:00", **kw):
    c = {"day": DAY, "time": time, "symbol": f"{root}(x)", "uname": "x", "sid": sid, "side": "Buy", "action": "新倉買進",
         "price": price, "lot": 1, "shares": 2000, "rate": 0.216, "margin": margin, "verdict": "ok", "stars": 4}
    c["intent_id"] = f"i-{sid}-{time}-{price}"
    c.update(kw)
    return c


class OrderLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("GOLDENSTOCKS_DATA_DIR")
        os.environ["GOLDENSTOCKS_DATA_DIR"] = self.tmp.name
        self.cfg = co.Config(dry_run=False)
        self.write_quota_tables({"QDF": 20.25, "RKF": 20.25, "ZZF": 20.25, "HBF": 20.25, "IRF": 20.25, "KBF": 20.25, "GUF": 40.5})

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GOLDENSTOCKS_DATA_DIR", None)
        else:
            os.environ["GOLDENSTOCKS_DATA_DIR"] = self._old
        self.tmp.cleanup()

    def write_quota_tables(self, pct, shares=None):
        d = Path(self.tmp.name) / "data" / "cache"
        d.mkdir(parents=True, exist_ok=True)
        (d / "taifex_stock_futures_margin_rates.json").write_text(
            json.dumps({k: {"sid": k, "原始%": v} for k, v in pct.items()}), encoding="utf-8")
        (d / "taifex_stock_futures_specs.json").write_text(
            json.dumps({k[:-1]: {"contract_shares": (shares or {}).get(k, 2000)} for k in pct}), encoding="utf-8")

    def put(self, *cards):
        p = Path(self.tmp.name) / "data" / "clone_agent" / f"candidates_{DAY}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for c in cards:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

    def ent(self):
        return co.load_ledger(DAY)["entries"]

    def test_send_once_and_idempotent(self):
        b = FakeBroker()
        self.put(card())
        self.assertEqual(co.tick(b, self.cfg, NOW)["sent"], 1)
        self.assertEqual(co.tick(b, self.cfg, NOW)["sent"], 0)
        self.assertEqual(b.placed, [("QDFJ6", 1, "471.5")])
        e = next(iter(self.ent().values()))
        self.assertEqual((e["status"], e["order_no"]), ("working", "N1"))

    def test_switch_default_on_off_and_corrupt(self):
        self.assertTrue(sw.read_switch()["enabled"])
        sw.set_switch(False)
        b = FakeBroker()
        self.put(card())
        co.tick(b, self.cfg, NOW)
        self.assertEqual(b.placed, [])
        self.assertEqual(next(iter(self.ent().values()))["reason"], "switch_off")
        sw.switch_path().write_text("{bad", encoding="utf-8")
        s = sw.read_switch()
        self.assertFalse(s["enabled"])
        self.assertEqual(s["state"], "corrupt")

    def test_off_cards_not_sent_after_turning_on(self):
        sw.set_switch(False)
        b = FakeBroker()
        self.put(card())
        co.tick(b, self.cfg, NOW)
        sw.set_switch(True)
        co.tick(b, self.cfg, NOW)
        self.assertEqual(b.placed, [])

    def test_guards(self):
        b = FakeBroker()
        self.put(card("1001", time="10:25:00"),                       # 過期
                 card("1002", price=471.3),                           # 非跳動單位
                 card("1003", verdict="blocked"),
                 card("1004", side="Sell", action="新倉賣出"),
                 card("1005", lot=2),
                 card("1006", root="ZZ"),                             # 無合約
                 card("1007", stars=3),                               # ★ 不足 4
                 card("1008", stars=None))                            # 沒帶 stars → fail-closed
        co.tick(b, self.cfg, NOW)
        r = {e["sid"]: (e["status"], e["reason"]) for e in self.ent().values()}
        self.assertTrue(r["1001"][1].startswith("stale_card"))
        self.assertEqual(r["1002"][1], "price_not_tick_aligned")
        self.assertEqual(r["1003"][1], "verdict_blocked")
        self.assertEqual(r["1004"][1], "not_new_buy")
        self.assertEqual(r["1005"][1], "lot_over_max")
        self.assertEqual(r["1006"][0], "failed")
        self.assertEqual(r["1007"][1], "stars_below_4")
        self.assertEqual(r["1008"][1], "stars_below_4")
        self.assertEqual(b.placed, [])

    def test_default_min_stars_from_rules_is_4(self):
        self.assertEqual(co.load_config().min_stars, 4)

    def test_user_skip_blocks(self):
        c = card()
        self.put(c)
        d = Path(self.tmp.name) / "data" / "clone_agent" / f"decisions_{DAY}.jsonl"
        d.write_text(json.dumps({"intent_id": c["intent_id"], "decision": "skip", "skip_reason": "x"}) + "\n", encoding="utf-8")
        b = FakeBroker()
        co.tick(b, self.cfg, NOW)
        self.assertEqual(b.placed, [])
        self.assertEqual(next(iter(self.ent().values()))["reason"], "user_skipped")

    def _req(self, iid, order_no="N1", trigger="C1", age=5, source="ai_supervisor", rid="r1"):
        sw.append_cancel_request(DAY, {"request_id": rid, "source": source, "intent_id": iid, "order_no": order_no,
                                       "trigger_id": trigger, "reason": "t", "requested_at": NOW.timestamp() - age})

    def test_ai_cancel_request_executes_once(self):
        b = FakeBroker()
        c = card()
        self.put(c)
        co.tick(b, self.cfg, NOW)
        self._req(c["intent_id"])
        co.tick(b, self.cfg, LATER)
        co.tick(b, self.cfg, LATER)
        self.assertEqual(b.cancelled, ["N1"])
        self.assertEqual(co.load_ledger(DAY)["cancels"]["r1"]["status"], "cancel_sent")

    def test_ai_cancel_rejections(self):
        b = FakeBroker()
        c = card()
        self.put(c)
        co.tick(b, self.cfg, NOW)
        iid = c["intent_id"]
        self._req(iid, rid="a", trigger="E1")
        self._req(iid, rid="b", order_no="X9")
        self._req(iid, rid="c", age=500)
        self._req(iid, rid="d", source="someone")
        co.tick(b, self.cfg, LATER)
        cs = co.load_ledger(DAY)["cancels"]
        self.assertEqual({k: v["reason"] for k, v in cs.items()},
                         {"a": "bad_request", "b": "order_mismatch", "c": "request_stale", "d": "bad_request"})
        self.assertFalse(getattr(b, "cancelled", []))

    def test_ai_cancel_not_for_filled_and_daily_cap_and_failure(self):
        b = FakeBroker()
        c = card()
        self.put(c)
        co.tick(b, self.cfg, NOW)
        b.results = [{"order_no": "N1", "status": 50, "filled_lot": 1}]
        co.tick(b, self.cfg, LATER)
        self._req(c["intent_id"], rid="f")
        co.tick(b, self.cfg, LATER)
        self.assertTrue(co.load_ledger(DAY)["cancels"]["f"]["reason"].startswith("not_working"))
        self.assertFalse(getattr(b, "cancelled", []))
        b2 = FakeBroker()
        b2.cancel_exc = co.BrokerRejected("not_working:filled")
        c2 = card("2002", price=100.0)
        self.put(c2)
        co.tick(b2, self.cfg, LATER)
        self._req(c2["intent_id"], order_no="N1", rid="g")
        co.tick(b2, self.cfg, LATER)
        self.assertEqual(co.load_ledger(DAY)["cancels"]["g"]["status"], "cancel_failed")

    def test_ai_cancel_works_when_switch_off_or_halted(self):
        b = FakeBroker()
        c = card()
        self.put(c)
        co.tick(b, self.cfg, NOW)
        sw.set_switch(False, by="t")
        self._req(c["intent_id"])
        co.tick(b, self.cfg, LATER)
        self.assertEqual(b.cancelled, ["N1"])

    def test_margin_cap_fcfs_and_cancel_releases(self):
        b = FakeBroker()
        self.put(card("2001", margin=900_000), card("2002", margin=700_000, price=100.0), card("2003", margin=200_000, price=50.0))
        out = co.tick(b, self.cfg, NOW)
        r = {e["sid"]: e for e in self.ent().values()}
        self.assertEqual(r["2001"]["status"], "working")
        self.assertEqual(r["2002"]["reason"], "margin_cap")   # 900k+700k > 1.47M
        self.assertEqual(r["2003"]["status"], "working")      # 900k+200k ok
        self.assertEqual(out["reserved_margin"], 1_100_000)
        b.results = [{"order_no": "N1", "status": 30, "filled_lot": 0}]
        out = co.tick(b, self.cfg, LATER)
        self.assertEqual(self.ent()[card("2001")["intent_id"]]["status"], "cancelled")
        self.assertEqual(out["reserved_margin"], 200_000)

    def test_same_sid_active_skipped(self):
        b = FakeBroker()
        self.put(card("3001"), card("3001", time="10:30:10", price=472.0))
        co.tick(b, self.cfg, NOW)
        self.assertEqual(len(b.placed), 1)

    def test_filled_holds_margin_until_broker_flat(self):
        b = FakeBroker()
        self.put(card("4001", margin=1_000_000))
        co.tick(b, self.cfg, NOW)
        b.results = [{"order_no": "N1", "status": 50, "filled_lot": 1}]
        b.positions = [POS]
        co.tick(b, self.cfg, LATER)
        e = next(iter(self.ent().values()))
        self.assertEqual(e["status"], "filled")
        far = datetime(2026, 10, 6, 12, 30, 0, tzinfo=TZ)           # 2 小時後:券商仍有部位 → 仍占保證金(舊版 60 分鐘會誤放)
        out = co.tick(b, self.cfg, far)
        self.assertEqual(out["reserved_margin"], 1_000_000)
        b.positions = []                                            # 你手動平倉
        out = co.tick(b, self.cfg, datetime(2026, 10, 6, 12, 36, 0, tzinfo=TZ))
        self.assertEqual(next(iter(self.ent().values()))["status"], "closed")
        self.assertEqual(out["reserved_margin"], 0)

    def test_no_release_within_grace_or_unknown_symbol_format(self):
        b = FakeBroker()
        self.put(card("4002", margin=500_000))
        co.tick(b, self.cfg, NOW)
        b.results = [{"order_no": "N1", "status": 50, "filled_lot": 1}]
        co.tick(b, self.cfg, LATER)                                 # 剛成交,券商端尚未顯示部位:寬限期內不釋放
        out = co.tick(b, self.cfg, datetime(2026, 10, 6, 10, 31, 40, tzinfo=TZ))
        self.assertEqual(out["reserved_margin"], 500_000)
        b.positions = [{"symbol": "WEIRD", "side": "L", "lots": 1}]  # 符號格式不認得 → 寧可多占不誤放
        out = co.tick(b, self.cfg, datetime(2026, 10, 6, 10, 40, 0, tzinfo=TZ))
        self.assertEqual(out["reserved_margin"], 500_000)

    def test_window_starts_at_1000(self):
        b = FakeBroker()
        early = datetime(2026, 10, 6, 9, 59, 30, tzinfo=TZ)
        self.put(card("4101", time="09:59:20"))
        co.tick(b, self.cfg, early)
        self.assertEqual(next(iter(self.ent().values()))["reason"], "outside_window")
        self.put(card("4102", time="10:00:00", price=472.0))
        co.tick(b, self.cfg, datetime(2026, 10, 6, 10, 0, 5, tzinfo=TZ))
        self.assertEqual(len(b.placed), 1)

    def test_foreign_position_or_order_same_root_blocks(self):
        b = FakeBroker(positions=[POS])                              # 你手動買了同一檔(帳本不知道)
        self.put(card("4201"))
        co.tick(b, self.cfg, NOW)
        self.assertEqual(next(iter(self.ent().values()))["reason"], "broker_position_same_root")
        b2 = FakeBroker(results=[{"order_no": "M9", "status": 10, "filled_lot": 0, "lot": 1, "symbol": "FIQDF"}])
        self.put(card("4202", price=472.0, time="10:31:00"))
        co.tick(b2, self.cfg, LATER)
        self.assertEqual(self.ent()[card("4202", price=472.0, time="10:31:00")["intent_id"]]["reason"], "broker_order_same_root")
        self.assertEqual(b.placed + b2.placed, [])

    def test_broker_available_margin_short_blocks(self):
        b = FakeBroker(avail=100_000)
        self.put(card("4301", margin=203688))
        co.tick(b, self.cfg, NOW)
        self.assertTrue(next(iter(self.ent().values()))["reason"].startswith("broker_margin_short"))
        self.assertEqual(b.placed, [])

    def test_quota_gate_counts_manual_positions_and_working_orders(self):
        # 2026-10-06 事故:手動欣興 549,585 + 金居類持倉不在自家帳本,自家 margin_cap 看不到,券商才拒 [8481329]
        manual = [{"symbol": "FIIRF", "side": "L", "lots": 1, "price": 1357.0},   # 1357*2000*.2025=549,585
                  {"symbol": "FIKBF", "side": "L", "lots": 1, "price": 788.0}]    # 319,140
        orders = [{"order_no": "M1", "status": 10, "filled_lot": 0, "lot": 1, "symbol": "FIHBF", "price": 374.0}]  # 151,470
        b = FakeBroker(positions=manual, results=orders)
        self.put(card("4601", root="RK", price=1227.0, margin=1))            # 新單 496,935 → 合計 1,517,130 > 1,470,000
        co.tick(b, self.cfg, NOW)
        r = next(iter(self.ent().values()))["reason"]
        self.assertTrue(r.startswith("quota_over:1517130"), r)
        self.assertEqual(b.placed, [])

    def test_quota_gate_passes_under_cap_and_recomputes_from_price(self):
        b = FakeBroker(positions=[{"symbol": "FIIRF", "side": "L", "lots": 1, "price": 1357.0}])
        self.put(card("4602", root="RK", price=788.0, margin=1))   # 閘門自己用價格×股數×率重算、不信卡片 margin 欄;合計 868,725 ≤ 147萬
        co.tick(b, self.cfg, NOW)
        self.assertEqual(len(b.placed), 1)

    def test_quota_gate_uses_override_rate_for_guf(self):
        # 全新:TAIFEX 表 40.5%,券商實扣 ~20.25%(覆寫);566*2000*.2025=229,230
        b = FakeBroker()
        self.put(card("2455", root="GU", price=566.0, margin=1))
        self.write_quota_tables({"GUF": 40.5})
        snap_exposure = co.quota_exposure(card("2455", root="GU", price=566.0), co.new_ledger(DAY), {"positions": [], "open_orders": []})
        self.assertAlmostEqual(snap_exposure, 229_230.0, places=0)
        co.tick(b, self.cfg, NOW)
        self.assertEqual(len(b.placed), 1)

    def test_quota_gate_counts_in_flight_order_not_yet_in_snapshot(self):
        led = co.new_ledger(DAY)
        led["entries"]["x"] = {"status": "working", "dry": False, "sent_at": 2000.0, "symbol": "KB(x)", "price": 788.0, "lot": 1}
        snap = {"ts": 1000.0, "positions": [], "open_orders": []}
        one = co.quota_exposure(card("4603", root="RK", price=788.0), co.new_ledger(DAY), snap)
        two = co.quota_exposure(card("4603", root="RK", price=788.0), led, snap)
        self.assertAlmostEqual(two - one, 319_140.0, places=0)

    def test_quota_gate_fail_closed_when_rate_or_price_unknown(self):
        b = FakeBroker(positions=[{"symbol": "FIXXF", "side": "L", "lots": 1, "price": 100.0}])   # 查不到 XXF 原始%
        self.put(card("4604", root="RK", price=788.0))
        co.tick(b, self.cfg, NOW)
        self.assertTrue(next(iter(self.ent().values()))["reason"].startswith("quota_unknown"))
        self.assertEqual(b.placed, [])
        (Path(self.tmp.name) / "data" / "cache" / "taifex_stock_futures_margin_rates.json").unlink()   # 表不見也不放行
        b2 = FakeBroker()
        self.put(card("4605", root="RK", price=789.0, time="10:31:00"))
        co.tick(b2, self.cfg, LATER)
        self.assertEqual(b2.placed, [])

    def test_quota_gate_ignores_non_stock_futures_symbols(self):
        b = FakeBroker(positions=[{"symbol": "TMFJ6", "side": "L", "lots": 2, "price": 22000.0}])
        self.put(card("4606", root="RK", price=788.0))
        co.tick(b, self.cfg, NOW)
        self.assertEqual(len(b.placed), 1)

    def test_broker_check_failure_is_fail_closed(self):
        b = FakeBroker(account_fail="positions")
        self.put(card("4401"))
        co.tick(b, self.cfg, NOW)
        self.assertTrue(next(iter(self.ent().values()))["reason"].startswith("broker_check_failed"))
        self.assertEqual(b.placed, [])

    def test_fetch_account_hang_times_out_instead_of_freezing(self):
        """2026-10-06 修正的 fail-open:券商查詢永久卡住(不丟例外、不回應)不能讓
        `tick()` 跟著卡死——同一天稍早在儀表板的 futures_pnl.py 抓到過一樣的失效模式。
        用 time.sleep 模擬永久卡住,把逾時壓到極短測試才跑得動,驗證:(1)能在短時間內
        返回、不是真的等滿 20 秒,(2)視同查詢失敗,fail-closed 擋單,(3)下一輪呼叫不會
        被上一次卡住的任務佔用名額(每次呼叫都開新 executor)。"""
        import time as _time

        class HangingBroker(FakeBroker):
            def fetch_account(self):
                self.calls += 1
                _time.sleep(5)   # 比下面覆蓋後的逾時長很多,模擬「永久卡住」
                return super().fetch_account()

        old_timeout = co.FETCH_ACCOUNT_TIMEOUT
        co.FETCH_ACCOUNT_TIMEOUT = 0.05
        try:
            b = HangingBroker()
            self.put(card("8501"))
            t0 = _time.time()
            co.tick(b, self.cfg, NOW)
            elapsed = _time.time() - t0
            self.assertLess(elapsed, 2.0, "tick() 應該在遠短於券商回應時間內返回,不是被卡住的呼叫拖住")
            self.assertTrue(next(iter(self.ent().values()))["reason"].startswith("broker_check_failed"))
            self.assertEqual(b.placed, [])
            # 下一輪(不同卡片,強制重查)也要一樣快——如果用的是固定 1-worker 共用池,
            # 這輪會卡在排在第一個(仍在背景 sleep 5 秒的)任務後面,等不到。
            self.put(card("8502", price=472.0, time="10:31:00"))
            t1 = _time.time()
            co.tick(b, self.cfg, LATER)
            elapsed2 = _time.time() - t1
            self.assertLess(elapsed2, 2.0, "第二輪不該被第一輪卡住的背景任務佔用 executor 名額")
        finally:
            co.FETCH_ACCOUNT_TIMEOUT = old_timeout

    def test_carry_over_overnight_position(self):
        prev = co.new_ledger("2026-10-05")
        prev["entries"]["old"] = {"intent_id": "old", "sid": "3532", "symbol": "QD(x)", "status": "filled", "dry": False, "margin": 900_000,
                                  "held_since": 1.0, "lot": 1}
        co.save_ledger(prev)
        b = FakeBroker(positions=[{"symbol": "FIQDF", "side": "L", "lots": 1}])
        self.put(card("3532", margin=900_000), card("4501", root="RK", price=100.0, margin=900_000))
        co.tick(b, self.cfg, NOW)
        led = co.load_ledger(DAY)
        r = {e["sid"]: e for e in led["entries"].values() if not e.get("carried_from")}
        self.assertIn(r["3532"]["reason"], ("same_sid_active",))     # 昨天留倉的同一檔不會被重複買
        self.assertEqual(r["4501"]["reason"], "margin_cap")          # 留倉的保證金仍占額度
        self.assertEqual(sum(1 for e in led["entries"].values() if e.get("carried_from")), 1)

    def test_halt_after_three_rejections_and_account_level(self):
        b = FakeBroker(place_exc=co.BrokerRejected("boom"))
        self.put(*[card(str(5000 + i), price=471.5 + i * 0.5, margin=100_000) for i in range(5)])
        co.tick(b, self.cfg, NOW)
        led = co.load_ledger(DAY)
        self.assertIn("consecutive_failures", led["halted"]["reason"])
        self.assertEqual(sum(1 for e in led["entries"].values() if e["status"] == "failed"), 3)
        self.assertEqual(sum(1 for e in led["entries"].values() if e["status"] == "skipped"), 2)

    def test_account_level_error_cools_down_not_halt(self):
        b = FakeBroker(place_exc=co.BrokerRejected("交易額度超過資(財)力證明額度"))
        self.put(card("6001"), card("6002", price=472.0))
        co.tick(b, self.cfg, NOW)
        led = co.load_ledger(DAY)
        self.assertIsNone(led["halted"])
        self.assertEqual(led["consec_fail"], 0)
        self.assertEqual(sum(1 for e in led["entries"].values() if e["status"] == "failed"), 1)
        self.assertEqual([e["reason"] for e in led["entries"].values() if e["status"] == "skipped"], ["account_cooldown"])
        b2 = FakeBroker()
        self.put(card("6003", price=473.0))
        co.tick(b2, self.cfg, NOW)
        self.assertEqual(len(b2.placed), 0)
        later = NOW.replace(minute=NOW.minute + 6) if NOW.minute < 53 else NOW
        if later is not NOW:
            self.put(card("6004", price=474.0, time=later.strftime("%H:%M:%S")))
            co.tick(b2, self.cfg, later)
            self.assertEqual(len(b2.placed), 1)

    def test_unknown_submit_never_resent_and_holds_margin(self):
        b = FakeBroker(place_exc=TimeoutError("net"))
        self.put(card("7001", margin=500_000))
        out = co.tick(b, self.cfg, NOW)
        e = next(iter(self.ent().values()))
        self.assertEqual(e["status"], "unknown_submit")
        self.assertEqual(out["reserved_margin"], 500_000)
        b.place_exc = None
        co.tick(b, self.cfg, NOW)
        self.assertEqual(b.placed, [])

    def test_dry_run_broker_never_hits_network(self):
        self.put(card())
        out = co.tick(co.DryBroker(), co.Config(dry_run=True), NOW)
        e = next(iter(self.ent().values()))
        self.assertEqual((out["sent"], e["status"], e["dry"]), (1, "dry_run", True))

    def test_real_broker_blocked_under_test_runner(self):
        class Boom:
            def __getattr__(self, n):
                raise AssertionError("session must not be touched")
        fb = co.FubonBroker(session_getter=lambda: Boom())
        with self.assertRaises(co.BrokerRejected):
            fb.place_buy("QDFJ6", 1, "471.5")

    def test_classify(self):
        self.assertEqual(co.classify_result(10, 0, 1), "working")
        self.assertEqual(co.classify_result(40, 1, 2), "partial")
        self.assertEqual(co.classify_result(90, 0, 1), "failed")
        self.assertEqual(co.classify_result(30, 0, 1), "cancelled")
        self.assertEqual(co.classify_result(50, 1, 1), "filled")

    def test_margin_fetch_failure_alone_blocks(self):
        """2026-10-06 修正的 fail-open 缺陷:orders/positions 都查得到,只有可用保證金
        這一項查詢失敗時,舊版會讓保證金檢查整個短路放行(只剩本地 margin_cap 當防線,
        它不知道其他袖共用同一個券商保證金池)。現在應該跟其他三項查詢失敗一樣擋單。"""
        class PartialBroker(FakeBroker):
            def fetch_account(self):
                self.calls += 1
                return {"orders": self.results, "positions": self.positions, "available_margin": None,
                        "errors": {"available_margin": "boom"}}
        b = PartialBroker()
        self.put(card("8001"))
        co.tick(b, self.cfg, NOW)
        self.assertEqual(next(iter(self.ent().values()))["reason"], "broker_margin_unknown")
        self.assertEqual(b.placed, [])

    def test_ledger_corrupt_file_halts_instead_of_resetting(self):
        """2026-10-06 修正的 fail-open 缺陷:帳本檔案存在但讀不出來(壞掉的 JSON)時,
        舊版會跟『今天還沒有帳本』回傳同一個值,呼叫端把它當成新的一天,悄悄重置熔斷
        旗標/當日送單數/同標的防重複,在壞資料上繼續送單。現在應該直接熔斷擋單。"""
        p = sw.ledger_path(DAY)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not valid json", encoding="utf-8")
        b = FakeBroker()
        self.put(card("8101"))
        co.tick(b, self.cfg, NOW)
        led = co.load_ledger(DAY)
        self.assertEqual(led["halted"]["reason"], "ledger_unreadable")
        self.assertEqual(b.placed, [])

    def test_carry_over_corrupt_prior_ledger_halts(self):
        """2026-10-06 修正:往回找留倉時,如果前一天帳本壞掉,舊版會被 `if not prev: continue`
        吃掉,悄悄跳去更早的日子找,可能漏掉真的留倉中的部位。現在應該直接熔斷。"""
        prev_day = "2026-10-05"
        sw.ledger_path(prev_day).parent.mkdir(parents=True, exist_ok=True)
        sw.ledger_path(prev_day).write_text("{not valid json", encoding="utf-8")
        b = FakeBroker()
        self.put(card("8201"))
        co.tick(b, self.cfg, NOW)
        led = co.load_ledger(DAY)
        self.assertIn("carry_over_ledger_unreadable", led["halted"]["reason"])
        self.assertEqual(b.placed, [])

    def test_price_format_and_tick(self):
        self.assertEqual(co.price_text(471.5), "471.5")
        self.assertEqual(co.price_text(3675.0), "3675")
        self.assertEqual(co.price_text(48.05), "48.05")
        self.assertTrue(co.tick_aligned(48.05))
        self.assertFalse(co.tick_aligned(48.02))
        self.assertTrue(co.tick_aligned(3675.0))


if __name__ == "__main__":
    unittest.main()


class PanelStripTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old = os.environ.get("GOLDENSTOCKS_DATA_DIR")
        os.environ["GOLDENSTOCKS_DATA_DIR"] = self._td.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GOLDENSTOCKS_DATA_DIR", None)
        else:
            os.environ["GOLDENSTOCKS_DATA_DIR"] = self._old
        self._td.cleanup()

    def test_strip_default_on_no_worker(self):
        from clone_agent.panels import render_order_strip
        h = render_order_strip("2026-10-06")
        self.assertIn("自動送單：開", h)
        self.assertIn("data-sw='off'", h)
        self.assertIn("下單層未運行", h)

    def test_strip_off_and_corrupt(self):
        from clone_agent import order_switch as sw
        from clone_agent.panels import render_order_strip
        sw.set_switch(False)
        h = render_order_strip("2026-10-06")
        self.assertIn("自動送單：關", h)
        self.assertIn("data-sw='on'", h)
        sw.switch_path().write_text("{bad", encoding="utf-8")
        self.assertIn("已視為關閉", render_order_strip("2026-10-06"))

    def test_strip_shows_halt_and_margin(self):
        from clone_agent import order_switch as sw
        from clone_agent.panels import render_order_strip
        sw.atomic_write(sw.ledger_path("2026-10-06"), {
            "day": "2026-10-06", "halted": {"reason": "account_level", "at": "t"}, "consec_fail": 0,
            "entries": {"a": {"status": "working", "margin": 500000}, "b": {"status": "skipped", "margin": 9}}})
        h = render_order_strip("2026-10-06")
        self.assertIn("今日已停單", h)
        self.assertIn("50 萬", h)

    def test_pending_block_contains_strip(self):
        from clone_agent.panels import render_pending_block
        self.assertIn("自動送單", render_pending_block("2026-10-06"))
