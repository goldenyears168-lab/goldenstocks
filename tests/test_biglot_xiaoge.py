"""biglot.xiaoge_cols：小哥原版欄／統計欄／P1 前向帳本的邊界釘樁。"""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
from biglot import xiaoge_cols as xg  # noqa: E402


def snap(**kw):
    s = {"d": 1, "n": 10, "lit": True, "nb5": 2, "win_lots": 50.0, "cum_lots": 300.0, "win_x": 5.0, "px": 101.0, "px0": 100.0,
         "bid_ask": (100.5, 101.0), "ret2m": 0.0, "flip": None, "risk": False}
    s.update(kw)
    return s


def body(html):
    """去掉 title 提示文字，只看儲存格內容。"""
    return re.sub(r" title='[^']*'", "", html)


class XiaogeColsTest(unittest.TestCase):
    def setUp(self):
        xg.CTX.clear(); xg._SEG.clear()
        xg.CTX["X"] = {"ret1": 4.0, "red_n": 2, "lu_n": 0, "turn": 35.0, "d1": "2026-10-16"}

    def test_p1_requires_all_conditions(self):
        self.assertTrue(xg.p1_conditions("X", snap()))
        self.assertFalse(xg.p1_conditions("X", snap(nb5=0)))             # 無爆窗
        self.assertFalse(xg.p1_conditions("X", snap(lit=False)))         # 連次 <10
        self.assertFalse(xg.p1_conditions("X", snap(d=-1)))              # 內攻
        self.assertFalse(xg.p1_conditions("X", snap(px=99.0)))           # 今日黑K
        xg.CTX["X"]["ret1"] = 2.0
        self.assertFalse(xg.p1_conditions("X", snap()))                  # 昨漲 ≤3%

    def test_author_short_checklist_marks_only_objective_items(self):
        html = xg.cell_author_short("X", {"day_ret": -1.0})
        self.assertIn("①周轉✓", html)
        self.assertIn("②高壓區✓", html)
        self.assertIn("黑K", html)
        self.assertIn("轉弱自判", html)                                  # 小哥未說明的第③關不自設

    def test_turnover_missing_is_honest(self):
        xg.CTX["X"]["turn"] = None
        self.assertIn("無股本資料", xg.cell_author_short("X", {"day_ret": 1.0}))

    def test_author_streak_shows_flip_label_and_avg_window(self):
        with mock.patch.object(xg, "_snap", return_value=snap(flip={"d": 1, "n": 12, "nb5": 3, "age": 5.0})):
            html = xg.cell_author_streak("X")
        self.assertIn("買盤竭盡", html)
        self.assertIn("窗均30張", html)                                  # 300 ÷ 10
        self.assertIn("🔔", html)

    def test_flip_label_requires_all_three_elements(self):
        full = {"d": 1, "n": 12, "nb5": 3, "age": 5.0, "stall": True, "brk_lots": 120.0, "exh": True, "day_up": False}
        with mock.patch.object(xg, "_snap", return_value=snap(flip=full)):
            html = body(xg.cell_author_streak("X"))
        self.assertIn("買盤竭盡", html)
        self.assertNotIn("逆大勢(今日", html)                                  # 今日黑K配買盤竭盡＝順大勢
        counter = dict(full, day_up=True)
        with mock.patch.object(xg, "_snap", return_value=snap(flip=counter)):
            self.assertIn("逆大勢(今日紅K)", body(xg.cell_author_streak("X")))
        weak = {"d": 1, "n": 12, "nb5": 1, "age": 5.0, "stall": False, "brk_lots": 3.0, "exh": False}
        with mock.patch.object(xg, "_snap", return_value=snap(flip=weak)):
            html = body(xg.cell_author_streak("X"))
        self.assertNotIn("買盤竭盡", html)                                # 只符合『轉色』不得稱竭盡
        self.assertIn("量不足", html)
        self.assertIn("價仍在創極值", html)

    def test_lowvol_symbol_is_grayed_out_without_bell_or_exhaustion(self):
        from biglot import streak_live
        streak_live.LOWVOL.add("X")
        try:
            full = {"d": 1, "n": 12, "nb5": 5, "age": 5.0, "stall": True, "brk_lots": 120.0, "exh": True, "day_up": False}
            with mock.patch.object(xg, "_snap", return_value=snap(flip=full)):
                html = body(xg.cell_author_streak("X"))
        finally:
            streak_live.LOWVOL.discard("X")
        self.assertNotIn("🔔", html)
        self.assertNotIn("竭盡", html)
        self.assertIn("範圍外", html)

    def test_ledger_skips_replay_events(self):
        with tempfile.TemporaryDirectory() as td:
            xg.LEDGER = Path(td) / "ledger.jsonl"

            class FakeDT(xg.datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 10, 20, 13, 0, 0)                   # 牆上 13:00

            with mock.patch.object(xg, "datetime", FakeDT):
                xg.maybe_p1("X", snap(), 9 * 3600)                       # 資料時間 09:00 遠早於牆上 → 視為重放，不寫
                self.assertFalse(xg.LEDGER.exists())
                xg.maybe_p1("X", snap(), 13 * 3600 - 10)                 # 即時事件 → 寫一行
                self.assertTrue(xg.LEDGER.exists())


if __name__ == "__main__":
    unittest.main()
