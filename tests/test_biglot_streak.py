"""biglot.streak 連次/連量 串流狀態機：邊界行為釘樁（與批次的逐事件比對見 scripts/research/exhaust_parity.py）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
from biglot.streak import StreakTracker  # noqa: E402

T0 = 9 * 3600 + 100  # 窗對齊 5 秒：09:01:40


def feed_window(trk, sid, k, outer=0, inner=0, px=100.0):
    """在第 k 個 5 秒窗內餵外盤/內盤成交（bid=px-0.5, ask=px+0.5：外盤價=ask、內盤價=bid）。"""
    t = T0 + 5 * k + 0.5
    ev = []
    if outer:
        ev += trk.on_trade(sid, t, px + 0.5, outer, px - 0.5, px + 0.5)
    if inner:
        ev += trk.on_trade(sid, t + 1, px - 0.5, inner, px - 0.5, px + 0.5)
    return ev


class StreakTest(unittest.TestCase):
    def setUp(self):
        self.trk = StreakTracker()
        self.trk.set_ref("X", 10.0)   # 每分鐘均量 10 張 → 爆量 ≥ 10 張

    def test_side_classification_ask_bid_and_tick_rule(self):
        trk = StreakTracker()
        trk.on_trade("A", T0, 100.5, 1, 99.5, 100.5)          # = ask → 外
        trk.on_trade("A", T0 + 1, 99.5, 1, 99.5, 100.5)       # = bid → 內
        trk.on_trade("A", T0 + 2, 100.0, 1, 99.5, 100.5)      # 區間內、價高於前筆 → tick rule 外
        st = trk._s["A"]
        self.assertEqual((st.o, st.i), (2.0, 1.0))

    def test_streak_counts_and_break_emits_event(self):
        ev = []
        for k in range(4):                                     # 4 個外攻窗，共 40 張 = 4 分鐘量
            ev += feed_window(self.trk, "X", k, outer=10, inner=1)
        self.assertEqual(ev, [])
        ev += feed_window(self.trk, "X", 4, inner=5)           # 內盤主導 → 中斷（先收第 4 窗，事件在收第 5 窗時才出）
        ev += self.trk.advance("X", T0 + 5 * 5 + 5)
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual((e["d"], e["n"], e["win_lots"]), (1, 4, 10.0))   # 最後一窗主導量＝官方連量（單窗）
        self.assertEqual(e["nb5"], 4)

    def test_empty_windows_do_not_break_streak(self):
        ev = []
        for k in (0, 1, 2, 7, 8):                              # 中間空窗跳過
            ev += feed_window(self.trk, "X", k, outer=10)
        ev += feed_window(self.trk, "X", 9, inner=3)
        ev += self.trk.advance("X", T0 + 5 * 10 + 5)
        self.assertEqual(ev[0]["n"], 5)

    def test_tie_breaks_streak(self):
        ev = []
        for k in range(3):
            ev += feed_window(self.trk, "X", k, outer=10)
        ev += feed_window(self.trk, "X", 3, outer=4, inner=4)  # 平手 → 中斷
        ev += self.trk.advance("X", T0 + 5 * 4 + 5)
        self.assertEqual(ev[0]["n"], 3)

    def test_short_streak_emits_no_event(self):
        ev = []
        for k in range(2):                                     # 只有 2 窗 < BREAK_MIN_N
            ev += feed_window(self.trk, "X", k, outer=2)
        ev += feed_window(self.trk, "X", 2, inner=2)
        ev += self.trk.advance("X", T0 + 5 * 3 + 5)
        self.assertEqual(ev, [])

    def test_late_trade_after_advance_is_ignored(self):
        feed_window(self.trk, "X", 0, outer=10)
        self.trk.advance("X", T0 + 10)                         # 窗 0 已收
        before = self.trk._s["X"].n
        self.trk.on_trade("X", T0 + 4.9, 100.5, 5, 99.5, 100.5)   # 遲到、屬於窗 0
        self.assertEqual(self.trk._s["X"].n, before)
        self.assertIsNone(self.trk._s["X"].cb)

    def test_auction_and_out_of_session_not_counted(self):
        trk = StreakTracker(); trk.set_ref("A", 10.0)
        trk.on_trade("A", T0, 100.5, 500, 99.5, 100.5, auction=True)
        trk.on_trade("A", 8 * 3600, 100.5, 5, 99.5, 100.5)     # 08:00 不在連續盤窗
        trk.on_trade("A", 13 * 3600 + 28 * 60, 100.5, 5, 99.5, 100.5)
        self.assertIsNone(trk._s["A"].cb)

    def test_snapshot_status_and_risk_flag(self):
        sym, k, ev = "X", 0, []
        px = 100.0
        for rep in range(3):                                   # 3 段爆量連攻（各 11 窗，連次達 10 亮燈）+ 急拉
            for _ in range(11):
                ev += feed_window(self.trk, sym, k, outer=10, px=px); k += 1
                px *= 1.004
            ev += feed_window(self.trk, sym, k, inner=2, px=px); k += 1
        now = T0 + 5 * k + 5
        snap = self.trk.snapshot(sym, now)
        self.assertEqual(snap["status"], "剛中斷")
        self.assertGreaterEqual(snap["nb5"], 0)
        self.assertTrue(snap["risk"])           # 剛中斷 ∧ 該段連次≥10 ∧ 爆量窗≥3
        far = self.trk.snapshot(sym, now + 120)                # 60 秒後不再「剛中斷」
        self.assertEqual(far["status"], "")
        self.assertFalse(far["risk"])

    def test_official_win_lots_lit_and_burst_windows(self):
        """官方連量＝最近一窗主導方向張數；連次達 10 亮燈；爆量窗＝單窗≥5×每5秒均量（ref=10張/分→每5秒0.833張→≥4.17張）。"""
        ev = []
        vols = [1, 1, 5, 6, 1, 1, 1, 1, 7, 2]             # 10 個外攻窗；其中 5、6、7 為爆量窗
        for k, v in enumerate(vols):
            ev += feed_window(self.trk, "X", k, outer=v, inner=0)
        ev += feed_window(self.trk, "X", len(vols), outer=0, inner=3)   # 觸發第 10 窗收盤
        snap = self.trk.snapshot("X", T0 + 5 * (len(vols) + 1) - 0.1)    # 水位在第 11 窗內（尚未收）
        self.assertEqual(snap["d"], 1)
        self.assertEqual(snap["n"], 10)
        self.assertTrue(snap["lit"])
        self.assertEqual(snap["win_lots"], 2.0)             # 最近已收窗（第 10 窗）的外盤張數
        self.assertAlmostEqual(snap["win_x"], 2.0 / (10.0 / 12.0))
        self.assertEqual(snap["nb5"], 3)
        self.assertEqual(snap["vmax"], 7.0)
        self.assertEqual(snap["cum_lots"], 26.0)            # 累計解讀＝ 1+1+5+6+1+1+1+1+7+2（並列顯示）

    def test_no_ref_means_no_burst_window(self):
        trk = StreakTracker()
        for k in range(5):
            feed_window(trk, "Z", k, outer=100)
        snap = trk.snapshot("Z", T0 + 5 * 4 + 4.9)
        self.assertEqual(snap["nb5"], 0)
        self.assertIsNone(snap["win_x"])


if __name__ == "__main__":
    unittest.main()


class StreakEventsTest(unittest.TestCase):
    def _run(self, px_step=0.0, outer=30, n=10, ref=10.0, flip_lots=40):
        trk = StreakTracker()
        trk.set_ref("X", ref)
        px = 100.0
        for k in range(n):
            feed_window(trk, "X", k, outer=outer, px=px)
            px += px_step
        feed_window(trk, "X", n, inner=flip_lots, px=px)       # 綠窗打斷
        trk.advance("X", T0 + 5 * (n + 2))
        return trk

    def test_lit_and_flip_events_recorded_for_grid_marks(self):
        ev = self._run().events("X")
        self.assertEqual([e["kind"] for e in ev], ["lit", "flip"])
        f = ev[1]
        self.assertEqual((f["d"], f["n"]), (1, 10))        # 被打斷的是外攻
        self.assertGreaterEqual(f["nb5"], 3)

    def test_exhaustion_needs_volume_and_stall(self):
        from biglot.streak import is_exhaustion
        flat = self._run(px_step=0.0).events("X")[1]
        self.assertTrue(flat["stall"] and is_exhaustion(flat))            # 爆量、價格不再創高 → 竭盡
        rising = self._run(px_step=0.5).events("X")[1]
        self.assertFalse(rising["stall"])                                  # 每窗都創新高 → 沒漲不動，不算竭盡
        self.assertFalse(is_exhaustion(rising))
        thin = self._run(px_step=0.0, outer=2).events("X")[1]              # 每窗 2 張（<5×均量）→ 量不足
        self.assertLess(thin["nb5"], 3)
        self.assertFalse(is_exhaustion(thin))
        tie1 = self._run(px_step=0.0, flip_lots=1).events("X")[1]          # 打斷窗只有 1 張＝不是『突然轉色』
        self.assertTrue(tie1["stall"] and tie1["nb5"] >= 3)
        self.assertFalse(tie1["brk_ok"])
        self.assertFalse(is_exhaustion(tie1))
        self.assertTrue(self._run(px_step=0.0, flip_lots=5).events("X")[1]["brk_ok"])   # 5 張＝門檻（max(5, 2.5×均量)）

    def test_listener_receives_flip_events(self):
        got = []
        trk = StreakTracker()
        trk.add_listener(lambda sid, ev: got.append((sid, ev["kind"], ev["d"])))
        trk.set_ref("X", 10.0)
        for k in range(10):
            feed_window(trk, "X", k, outer=30)
        feed_window(trk, "X", 10, inner=40)
        trk.advance("X", T0 + 5 * 12)
        self.assertEqual(got, [("X", "flip", 1)])
        trk.reset()
        feed_window(trk, "X", 0, outer=30)                                 # reset 不清監聽
        self.assertEqual(len(trk._listeners), 1)

    def test_snapshot_flip_carries_exhaustion_flag(self):
        trk = self._run()
        snap = trk.snapshot("X", T0 + 5 * 12)
        self.assertTrue(snap["flip"]["exh"])

    def test_tie_window_is_break_not_color_flip(self):
        trk = StreakTracker(); trk.set_ref("X", 10.0)
        for k in range(10):
            feed_window(trk, "X", k, outer=30)
        feed_window(trk, "X", 10, outer=5, inner=5)           # 平手：連次中斷但沒有轉色
        trk.advance("X", T0 + 5 * 12)
        self.assertEqual([e["kind"] for e in trk.events("X")], ["lit"])
        self.assertIsNone(trk.snapshot("X", T0 + 5 * 12)["flip"])

    def test_percentile_threshold_overrides_x5_burst(self):
        from biglot.streak import is_exhaustion
        px = 100.0
        trk = StreakTracker(); trk.set_ref("X", 10.0)
        for k in range(10):
            feed_window(trk, "X", k, outer=3, px=px)             # 3 張 <5×均量(0.83×5=4.2) → 舊規則不算爆量
        feed_window(trk, "X", 10, inner=40, px=px)
        trk.advance("X", T0 + 5 * 12)
        self.assertFalse(is_exhaustion(trk.events("X")[1]))
        trk2 = StreakTracker(); trk2.set_ref("X", 10.0)
        trk2.set_thresholds("X", {bk: 2.0 for bk in range(9)})   # 各時段 P90＝2 張 → 3 張都算大量
        for k in range(10):
            feed_window(trk2, "X", k, outer=3, px=px)
        feed_window(trk2, "X", 10, inner=40, px=px)
        trk2.advance("X", T0 + 5 * 12)
        f = trk2.events("X")[1]
        self.assertGreaterEqual(f["nb5"], 3)
        self.assertTrue(is_exhaustion(f))
        self.assertEqual(trk2.snapshot("X", T0 + 5 * 12)["burst_src"], "p90")

    def test_short_streak_has_no_events(self):
        trk = StreakTracker()
        trk.set_ref("X", 10.0)
        for k in range(5):
            feed_window(trk, "X", k, outer=30)
        feed_window(trk, "X", 5, inner=40)
        trk.advance("X", T0 + 5 * 7)
        self.assertEqual(trk.events("X"), [])
