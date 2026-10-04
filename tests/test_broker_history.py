"""broker_history 持久化層的回歸測試。

釘住 2026-10-04 實際踩到的兩個 bug：
  ① 早期拉取腳本用 dir() 展開 SDK enum，buy_sell 變成巨大字典，split('.') 後
     是 `UnSupported'}}` 之類垃圾 → 必須擋在庫外，不能入庫
  ② ON CONFLICT 只覆蓋部分欄位 → 後來正確的資料無法修正先前的壞值
     （當時造成 3,101 列買賣方向全壞）
以及一個設計前提：成交紀錄的 seq_no 富邦一律回 None，委託↔成交只能用
(date, order_no) 關聯。
"""
import sqlite3
import unittest

import broker_history as bh


def _mem():
    con = sqlite3.connect(":memory:")
    con.executescript(bh.SCHEMA)
    con.row_factory = sqlite3.Row
    return con


ORDER_OK = {
    "date": "2026/10/02", "order_no": "oP286", "seq_no": "00024823734",
    "stock_no": "3006", "buy_sell": "BSAction.Buy", "order_type": "OrderType.Stock",
    "price_type": "PriceType.Limit", "time_in_force": "TimeInForce.ROD",
    "market_type": "MarketType.Common", "price": 285.5, "quantity": 1000,
    "status": 30, "user_def": None,
    "details": [{"function_type": 10, "modified_time": "09:13:40.000",
                 "before_price": 285.5, "after_price": 285.5, "status": 10}],
}
# 壞值：enum 被 dir() 展開後序列化的殘骸
ORDER_BAD = {**ORDER_OK, "order_no": "oBAD",
             "buy_sell": "{'Buy': {'Buy': 'BSAction.Buy'}}", "seq_no": "99"}


class EnumGuardTest(unittest.TestCase):
    def test_clean_enum_unwrapped(self):
        self.assertEqual(bh._enum("BSAction.Buy"), "Buy")
        self.assertEqual(bh._enum("FutOptOrderType.New"), "New")
        self.assertIsNone(bh._enum(None))

    def test_corrupted_enum_rejected(self):
        """dir() 展開的殘骸一律回 None，不得產生 UnSupported'}} 這種值。"""
        for bad in ("{'Buy': {'Buy': 'BSAction.Buy'}}",
                    "UnSupported'}}", "[1, 2]", "'x'"):
            self.assertIsNone(bh._enum(bad), f"{bad!r} 應被擋掉")

    def test_bad_row_not_inserted(self):
        con = _mem()
        n = bh.upsert_orders(con, [ORDER_OK, ORDER_BAD],
                             account_type="stock", pulled_at="t0")
        self.assertEqual(n, 1, "壞列不應入庫")
        sides = [r["side"] for r in con.execute("SELECT side FROM broker_orders")]
        self.assertEqual(sides, ["Buy"])


class UpsertConvergenceTest(unittest.TestCase):
    def test_repull_corrects_every_mutable_column(self):
        """重跑必須收斂到最新真相——這是當初 3,101 列壞掉的根因。"""
        con = _mem()
        stale = {**ORDER_OK, "buy_sell": "BSAction.Sell", "price": 1.0,
                 "status": 10, "market_type": "MarketType.Odd"}
        bh.upsert_orders(con, [stale], account_type="stock", pulled_at="t0")
        bh.upsert_orders(con, [ORDER_OK], account_type="stock", pulled_at="t1")
        r = con.execute("SELECT * FROM broker_orders").fetchone()
        self.assertEqual(con.execute("SELECT COUNT(*) c FROM broker_orders").fetchone()["c"], 1)
        self.assertEqual(r["side"], "Buy")
        self.assertEqual(r["limit_price"], 285.5)
        self.assertEqual(r["status"], 30)
        self.assertEqual(r["market_type"], "Common")
        self.assertEqual(r["pulled_at"], "t1")

    def test_idempotent(self):
        con = _mem()
        for _ in range(3):
            bh.upsert_orders(con, [ORDER_OK], account_type="stock", pulled_at="t")
        self.assertEqual(
            con.execute("SELECT COUNT(*) c FROM broker_orders").fetchone()["c"], 1)


class OrderProcessTest(unittest.TestCase):
    def test_amend_vs_cancel_not_confused(self):
        """function_type 30 是刪單（after_lot=0），不是改價；真改價是 ft=10 且價格有變。"""
        amended = {**ORDER_OK, "order_no": "oA", "details": [
            {"function_type": 10, "modified_time": "09:00:00.000",
             "before_price": 100.0, "after_price": 100.0},
            {"function_type": 10, "modified_time": "09:05:00.000",
             "before_price": 100.0, "after_price": 101.0, "status": 15},
        ]}
        cancelled = {**ORDER_OK, "order_no": "oC", "details": [
            {"function_type": 10, "modified_time": "09:00:00.000",
             "before_price": 100.0, "after_price": 100.0},
            {"function_type": 30, "modified_time": "10:00:00.000",
             "before_lot": 1, "after_lot": 0, "status": 30},
        ]}
        con = _mem()
        bh.upsert_orders(con, [amended, cancelled], account_type="stock", pulled_at="t")
        rows = {r["order_no"]: r for r in con.execute("SELECT * FROM broker_orders")}
        self.assertEqual(rows["oA"]["n_amend_price"], 1)
        self.assertEqual(rows["oA"]["cancelled"], 0)
        self.assertEqual(rows["oC"]["n_amend_price"], 0)
        self.assertEqual(rows["oC"]["cancelled"], 1)

    def test_submit_time_from_new_event(self):
        con = _mem()
        bh.upsert_orders(con, [ORDER_OK], account_type="stock", pulled_at="t")
        r = con.execute("SELECT submit_time FROM broker_orders").fetchone()
        self.assertEqual(r["submit_time"], "09:13:40.000")


class ManualVsAutoTest(unittest.TestCase):
    def test_blank_user_def_is_manual(self):
        for u in (None, "", "   ", "none"):
            self.assertFalse(bh.is_auto(u), f"{u!r} 應視為手動")

    def test_sleeve_tags_are_auto(self):
        for u in ("tmfch", "dfshort", "dfshortcv", "mf09062377", "manflat",
                  "ss5d", "epsg", "ep2327", "c18x", "ld1"):
            self.assertTrue(bh.is_auto(u), f"{u!r} 應視為自動袖")


class FillJoinTest(unittest.TestCase):
    def test_fills_join_on_date_and_order_no(self):
        """富邦成交紀錄的 seq_no 一律 None，只能用 (date, order_no) 關聯。"""
        fill = {"date": "2026/10/02", "filled_no": "00000000001",
                "order_no": "oP286", "seq_no": None, "stock_no": "3006",
                "buy_sell": "BSAction.Buy", "order_type": "OrderType.Stock",
                "filled_time": "09:13:41.123", "filled_price": 285.5,
                "filled_qty": 1000, "filled_avg_price": 285.5, "user_def": None}
        con = _mem()
        bh.upsert_orders(con, [ORDER_OK], account_type="stock", pulled_at="t")
        self.assertEqual(bh.upsert_fills(con, [fill], account_type="stock", pulled_at="t"), 1)
        r = con.execute("""SELECT o.side, f.filled_price FROM broker_fills f
                           JOIN broker_orders o
                             ON o.date=f.date AND TRIM(o.order_no)=TRIM(f.order_no)""").fetchone()
        self.assertEqual((r["side"], r["filled_price"]), ("Buy", 285.5))

    def test_date_normalised_to_iso(self):
        con = _mem()
        bh.upsert_orders(con, [ORDER_OK], account_type="stock", pulled_at="t")
        self.assertEqual(
            con.execute("SELECT date FROM broker_orders").fetchone()["date"], "2026-10-02")


if __name__ == "__main__":
    unittest.main()
