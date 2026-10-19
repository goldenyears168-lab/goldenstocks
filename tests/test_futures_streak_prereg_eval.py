"""大台連次預註冊評估腳本的純函式:報酬方向、群集 t、判定邊界(不讀任何成交檔)。"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts" / "research")]

import futures_streak_prereg_eval as E  # noqa: E402


def _data(events, prices):
    tt = np.array([p[0] for p in prices], dtype=float)
    px = np.array([p[1] for p in prices], dtype=float)
    return {"2026-10-20": {"ev": events, "tt": tt, "px": px}}


class EvalTests(unittest.TestCase):
    def test_fade_sign_is_against_the_streak(self):
        # 紅串(dir=+1)亮燈於 t=1000,價 100;15 分鐘後 99 → 反向做空賺 +100 bps
        d = _data([(1000.0, 1, 10)], [(900, 100.0), (1000, 100.0), (1900, 99.0), (2000, 99.0)])
        r = E.fade_returns(d, 15, ["2026-10-20"])
        self.assertAlmostEqual(r[0][1], 100.0, places=6)
        # 綠串(dir=−1)後價格漲 → 反向做多賺
        d = _data([(1000.0, -1, 10)], [(900, 100.0), (1000, 100.0), (1900, 101.0), (2000, 101.0)])
        self.assertAlmostEqual(E.fade_returns(d, 15, ["2026-10-20"])[0][1], 100.0, places=6)

    def test_exit_beyond_session_is_skipped(self):
        d = _data([(1000.0, 1, 10)], [(900, 100.0), (1000, 100.0), (1500, 99.0)])          # 最後成交 1500 < 1000+900
        self.assertEqual(E.fade_returns(d, 15, ["2026-10-20"]), [])

    def test_stats_cluster_by_day(self):
        rows = [("d1", 5.0), ("d1", 5.0), ("d2", -1.0), ("d3", 3.0)]
        s = E.stats(rows)
        self.assertEqual((s["n"], s["days"]), (4, 3))
        self.assertAlmostEqual(s["mean"], 3.0)
        self.assertAlmostEqual(s["win"], 0.75)
        self.assertIsNone(E.stats([("d1", 1.0)]))

    def test_frozen_constants(self):
        self.assertEqual(E.CONFIRM_START, "2026-10-20")
        self.assertIn("2026-10-19", E.EXCLUDED)
        self.assertEqual((E.INTERIM_DAYS, E.FINAL_DAYS, E.T_THRESH, E.GROSS_MIN, E.WIN_MIN), (30, 60, 3.0, 2.5, 0.55))
        self.assertEqual(E.PRIMARY, ("TXF", 15))


if __name__ == "__main__":
    unittest.main()
