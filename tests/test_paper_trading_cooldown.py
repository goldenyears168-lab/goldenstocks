"""釘住 paper trading 的「冷卻制」進場閘門(2026-09-30 取代舊的 seen「訊號一天一次」)。

舊行為:`seen` 在訊號 fire 當下就記,連被成因過濾/容量滿/無買一/掛單未成交都會消耗
當日唯一額度 → 一次暫時性的執行障礙就把該檔封鎖整天。
新行為:每檔每日最多**成交** PAPER_MAX_ENTRY 次;其餘情況各自設冷卻秒數後可重試。
"""
from __future__ import annotations

import sys
import types
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")
COOL = {"skip": 600.0, "cap": 30.0, "nobid": 60.0, "unfilled": 300.0, "closed": 900.0}


def _stub_dashboard():
    """paper_trading 只透過 `biglot_dashboard.X` 屬性存取,用假模組即可,不必載入真儀表板。"""
    m = types.ModuleType("biglot_dashboard")
    m.PAPER_BOOKS = ("bucket", "sec")
    m.PAPER_COST, m.PAPER_K, m.PAPER_TH = 22.0, 3, 15.0
    m.PAPER_BUY_WAIT, m.PAPER_SELL_WAIT, m.PAPER_MAX_HOLD = 30, 60, 3600
    m.PAPER_SKIP = ("處置", "跌停鎖", "跟盤殺")
    m.PAPER_MAX_ENTRY = 2
    m.PAPER_COOL = dict(COOL)
    m.HOLD_BAD = ("散戶虛拉", "過熱")
    m.PAPER = {}
    m.PAPER_PATH = None
    m.PAPER_DAILY = None
    m.TZ = TZ
    m.datetime = datetime
    st = types.SimpleNamespace(date="2026-09-30", last_bid={}, last_ask={}, book={}, recent={})
    m.ST = st
    return m


# CI 是 `PYTHONPATH=src unittest discover`,沒有 scripts/research 在 path,
# 也不該為了測試去 import 真的 biglot_dashboard(會載入儀表板與 DB)。
# 先塞 stub 再用 SourceFileLoader 直接載檔案。
sys.modules.setdefault("biglot_dashboard", _stub_dashboard())
PT = SourceFileLoader(
    "biglot_paper_trading",
    str(ROOT / "scripts" / "research" / "biglot" / "paper_trading.py")).load_module()


class PaperCooldownTest(unittest.TestCase):
    def setUp(self):
        self.dash = _stub_dashboard()
        # paper_trading 的 globals 在載入時就綁住 biglot_dashboard 物件,
        # 每個測試換一份新 stub 時要一併重綁,否則第二個測試起會操作到上一份 stub。
        PT.biglot_dashboard = self.dash
        pt = self.pt = PT
        self.logs: list[dict] = []
        self.p_log = mock.patch.object(pt, "_paper_log", side_effect=self.logs.append)
        self.p_save = mock.patch.object(pt, "_paper_save")
        self.p_log.start(); self.p_save.start()
        self.dash.PAPER.clear(); self.dash.PAPER.update(pt._paper_blank("2026-09-30"))
        # 09:40:00 當基準時間
        self.t0 = datetime(2026, 9, 30, 9, 40, 0, tzinfo=TZ).timestamp()

    def tearDown(self):
        self.p_log.stop(); self.p_save.stop()

    # --- helpers ---
    def _row(self, sid="2330", sc=17.0, px=100.0, cause=(), items=()):
        self.dash.ST.last_bid[sid] = px
        self.dash.ST.last_ask[sid] = px + 0.5
        return {"sid": sid, "sc_v2": sc, "px": px, "cause": [(c, 1) for c in cause],
                "sc_v2_items": [(k, 1) for k in items]}

    def _tick_sell(self, sid, ts, px):
        """餵一筆賣方主動成交(sgn<0),讓掛買一在 ts 成交。"""
        self.dash.ST.recent[sid] = ((ts, px, 1000.0, -1, None, None),)

    def _kinds(self, book="sec"):
        return [(r["ev"], r.get("why")) for r in self.logs if r.get("book") == book]

    # --- 1. 成因過濾不再封鎖整天,只設 600 秒冷卻 ---
    def test_cause_filter_sets_cooldown_not_daily_block(self):
        row = self._row(sid="2303", cause=("跟盤殺",))
        self.pt._paper_update([row], self.t0)
        self.assertEqual(self._kinds(), [("signal_skip", "跟盤殺")])
        self.assertAlmostEqual(self.dash.PAPER["cool"]["sec"]["2303"], self.t0 + COOL["skip"])

        # 冷卻期間內:靜默跳過,不再寫 log
        self.logs.clear()
        self.pt._paper_update([self._row(sid="2303")], self.t0 + 300)
        self.assertEqual(self.logs, [])

        # 冷卻到期後(標籤已消失):重新掛單
        self.pt._paper_update([self._row(sid="2303")], self.t0 + COOL["skip"] + 1)
        self.assertEqual(self._kinds(), [("signal", None)])
        self.assertIn("2303", self.dash.PAPER["orders"]["sec"])

    # --- 2. 掛單未成交只設 300 秒冷卻,之後可重掛 ---
    def test_unfilled_sets_cooldown_and_allows_retry(self):
        self.pt._paper_update([self._row(sid="2337", px=119.5)], self.t0)
        self.assertIn("2337", self.dash.PAPER["orders"]["sec"])
        # 30 秒後仍無成交 → unfilled
        t1 = self.t0 + 31
        self.pt._paper_update([self._row(sid="2337", px=119.5)], t1)
        self.assertIn(("unfilled", None), self._kinds())
        self.assertNotIn("2337", self.dash.PAPER["orders"]["sec"])
        self.assertAlmostEqual(self.dash.PAPER["cool"]["sec"]["2337"], t1 + COOL["unfilled"])
        self.assertEqual(self.dash.PAPER["nent"]["sec"].get("2337", 0), 0, "未成交不該計入當日成交次數")

        # 冷卻到期後可重掛
        self.logs.clear()
        self.pt._paper_update([self._row(sid="2337", px=119.5)], t1 + COOL["unfilled"] + 1)
        self.assertEqual(self._kinds(), [("signal", None)])

    # --- 3. 在途委託/持倉中不重複掛單 ---
    def test_no_duplicate_order_while_pending_or_holding(self):
        self.pt._paper_update([self._row(sid="6173", px=296.5)], self.t0)
        self.logs.clear()
        self.pt._paper_update([self._row(sid="6173", px=296.5)], self.t0 + 5)
        self.assertEqual(self.logs, [], "在途委託期間不應再 fire")

        self._tick_sell("6173", self.t0 + 10, 296.5)
        self.pt._paper_update([self._row(sid="6173", px=296.5)], self.t0 + 11)
        self.assertIn("6173", self.dash.PAPER["pos"]["sec"])
        self.logs.clear()
        self.pt._paper_update([self._row(sid="6173", px=296.5)], self.t0 + 12)
        self.assertEqual([k for k in self._kinds() if k[0] == "signal"], [], "持倉期間不應再 fire")

    # --- 4. 每檔每日最多成交 PAPER_MAX_ENTRY 次 ---
    def test_max_entries_per_day_counts_fills_only(self):
        sid = "2481"
        self.dash.PAPER["nent"]["sec"][sid] = self.dash.PAPER_MAX_ENTRY
        self.pt._paper_update([self._row(sid=sid)], self.t0)
        self.assertEqual(self._kinds(), [("signal_skip", "當日額度用盡")])
        self.assertNotIn(sid, self.dash.PAPER["orders"]["sec"])

    # --- 5. 平倉後累計成交次數並設 900 秒冷卻 ---
    def test_close_increments_entries_and_sets_cooldown(self):
        sid = "3042"
        pos = {"entry": 100.0, "t_fill": self.t0, "strict": False, "low_since": None,
               "sell": {"limit": 101.0, "t_post": self.t0, "reason": "壞標籤"},
               "sig": {"hm": "09:40:00", "score": 17.0}}
        self.dash.PAPER["pos"]["sec"][sid] = pos
        t_close = self.t0 + 120
        self.pt._paper_close("sec", sid, pos, 101.0, "賣一", t_close)
        self.assertEqual(self.dash.PAPER["nent"]["sec"][sid], 1)
        self.assertAlmostEqual(self.dash.PAPER["cool"]["sec"][sid], t_close + COOL["closed"])

    # --- 6. 舊 state(無 cool/nent)載入後自動補鍵 ---
    def test_ensure_backfills_missing_keys_on_old_state(self):
        self.dash.PAPER.pop("cool"); self.dash.PAPER.pop("nent")
        self.dash.PAPER["seen"] = {"sec": ["2330"], "bucket": []}   # 舊欄位殘留
        self.pt._paper_update([self._row(sid="2330")], self.t0)
        self.assertIn("cool", self.dash.PAPER)
        self.assertIn("nent", self.dash.PAPER)
        self.assertIn("2330", self.dash.PAPER["orders"]["sec"], "舊 seen 殘留不應再封鎖該檔")


if __name__ == "__main__":
    unittest.main()
