"""biglot.xiaoge_cols：小哥原版欄／統計欄／P1 前向帳本的邊界釘樁。"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
from biglot import xiaoge_cols as xg  # noqa: E402


def snap(**kw):
    s = {"d": 1, "n": 10, "lit": True, "nb5": 2, "rvol30_now": 3.0, "win_lots": 50.0, "cum_lots": 300.0, "win_x": 5.0, "px": 101.0, "px0": 100.0,
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

    def test_p1_v2_is_buy_side_exhaustion(self):
        exh = {"d": 1, "n": 6, "nb5": 0, "age": 5.0, "rvol30": 3.0, "exh": True, "day_up": False}
        self.assertTrue(xg.p1_conditions("X", snap(flip=exh)))
        self.assertFalse(xg.p1_conditions("X", snap(flip=None)))                          # 沒有剛轉色
        self.assertFalse(xg.p1_conditions("X", snap(flip=dict(exh, exh=False, rvol30=1.2))))   # RVOL 不足
        self.assertFalse(xg.p1_conditions("X", snap(flip=dict(exh, d=-1))))              # 賣盤竭盡側不是 P1（P1 是放空評估）
        xg.CTX["X"]["ret1"] = 2.0                                                          # 昨漲幅不再是條件
        self.assertTrue(xg.p1_conditions("X", snap(flip=exh)))

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

    def test_flip_label_follows_exhaustion_v2(self):
        full = {"d": 1, "n": 6, "nb5": 0, "age": 5.0, "rvol30": 3.2, "exh": True, "day_up": False, "brk_lots": 12.0}
        with mock.patch.object(xg, "_snap", return_value=snap(flip=full)):
            html = body(xg.cell_author_streak("X"))
        self.assertIn("買盤竭盡", html)
        self.assertIn("RVOL 3.2×", html)
        self.assertIn("順大勢·新倉方向", html)                                 # 黑K日的買盤竭盡＝空方新倉方向
        self.assertNotIn("平倉/回補提示", html)
        counter = dict(full, day_up=True)
        with mock.patch.object(xg, "_snap", return_value=snap(flip=counter)):
            self.assertIn("平倉/回補提示·今日紅K", body(xg.cell_author_streak("X")))    # 紅K日的買盤竭盡＝多單出場提示，不是新倉
        weak = dict(full, rvol30=1.1, exh=False)
        with mock.patch.object(xg, "_snap", return_value=snap(flip=weak)):
            h2 = body(xg.cell_author_streak("X"))
        self.assertNotIn("買盤竭盡", h2)                                # 只符合『轉色』不得稱竭盡
        self.assertIn("RVOL不足", h2)
        none = dict(full, rvol30=None, exh=False)
        with mock.patch.object(xg, "_snap", return_value=snap(flip=none)):
            self.assertIn("無分均量", body(xg.cell_author_streak("X")))

    def test_lowvol_symbol_is_grayed_out_without_bell_or_exhaustion(self):
        from biglot import streak_live
        streak_live.LOWVOL.add("X")
        try:
            full = {"d": 1, "n": 12, "nb5": 5, "age": 5.0, "rvol30": 4.0, "exh": True, "day_up": False}
            with mock.patch.object(xg, "_snap", return_value=snap(flip=full)):
                html = body(xg.cell_author_streak("X"))
        finally:
            streak_live.LOWVOL.discard("X")
        self.assertNotIn("🔔", html)
        self.assertNotIn("竭盡", html)
        self.assertIn("範圍外", html)

    def test_exhaustion_ledger_only_live_nonlowvol_exhaustion_events(self):
        from biglot import streak_live as sl
        ev = {"kind": "flip", "t": 13 * 3600 - 10.0, "d": 1, "n": 6, "nb5": 0, "px": 100.0, "avg": 30.0, "rvol30": 3.0, "stall": False, "brk_ok": True,
              "brk_lots": 50.0, "day_up": False}

        class FakeDT(sl.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 21, 13, 0, 0)                           # 牆上 13:00

        with tempfile.TemporaryDirectory() as td:
            sl.EXH_LEDGER = Path(td) / "exh.jsonl"
            sl._EXH_SEEN.clear()
            with mock.patch.object(sl, "datetime", FakeDT):
                sl._on_flip("X", dict(ev, t=9 * 3600.0))                      # 事件時間遠早於牆上＝重放，不寫
                sl._on_flip("X", dict(ev, rvol30=1.0))                        # RVOL 不足＝不是竭盡，不寫
                sl.LOWVOL.add("X")
                sl._on_flip("X", ev)                                          # 低量標的，不寫
                sl.LOWVOL.discard("X")
                self.assertFalse(sl.EXH_LEDGER.exists())
                sl._on_flip("X", ev)                                          # 即時竭盡事件 → 寫一行
                sl._on_flip("X", ev)                                          # 同一事件不重複
            rows = [json.loads(x) for x in sl.EXH_LEDGER.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["sid"], rows[0]["d"], rows[0]["aligned"], rows[0]["source"]), ("X", 1, True, "live"))

    def test_p1_v2_ledger_skips_replay_and_cools_down(self):
        ev = {"kind": "flip", "t": 13 * 3600 - 10.0, "d": 1, "n": 6, "px": 100.0, "rvol30": 3.0, "day_up": False}
        st = mock.Mock(bid=99.9, ask=100.1)
        with tempfile.TemporaryDirectory() as td:
            xg.LEDGER = Path(td) / "ledger.jsonl"

            class FakeDT(xg.datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 10, 26, 13, 0, 0)                   # 牆上 13:00

            with mock.patch.object(xg, "datetime", FakeDT):
                xg.record_p1("X", dict(ev, t=9 * 3600.0), st)            # 事件時間 09:00 遠早於牆上 → 重放，不寫
                self.assertFalse(xg.LEDGER.exists())
                xg.record_p1("X", ev, st)                                # 即時事件 → 寫一行
                xg.record_p1("X", dict(ev, t=ev["t"] + 100), st)         # 同股 300 秒內不重複
            rows = [json.loads(x) for x in xg.LEDGER.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["rule"], rows[0]["sid"], rows[0]["n"], rows[0]["rvol30"]), ("P1-v2", "X", 6, 3.0))


if __name__ == "__main__":
    unittest.main()
