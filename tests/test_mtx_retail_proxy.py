import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts" / "research")]

from biglot import mtx_retail_proxy as MP  # noqa: E402

ROWS = [{"date": "2026-10-15", "total_oi": 40000.0, "inst_net": -8000.0},
        {"date": "2026-10-16", "total_oi": 44000.0, "inst_net": -11000.0}]      # 散戶淨部位 +11,000 口 = +25.0%


def sess(buy, sell):
    """單一分鐘列:買 buy 口、賣 sell 口(分在兩個組別,驗證會全組加總)。"""
    return {0: {"bl": {"B": buy, "M": 0, "R": 0}, "sl": {"B": 0, "M": sell // 2, "R": sell - sell // 2}}}


class MtxRetailProxyTests(unittest.TestCase):
    def test_night_after_close_ok(self):
        e = MP.estimate({("day", "2026-10-16"): sess(999, 0), ("night", "2026-10-16"): sess(100, 400)}, ROWS)
        self.assertEqual(e["status"], "ok")
        self.assertEqual(e["flow"], -300)                                   # 只算 L 日日盤收盤之後
        self.assertAlmostEqual(e["delta"], MP.BETA * -300)
        self.assertAlmostEqual(e["base_pct"], 25.0)
        self.assertAlmostEqual(e["est_net"], 11000 + MP.BETA * -300)
        self.assertEqual(e["windows"], ["night|2026-10-16"])

    def test_next_day_session_ok(self):
        e = MP.estimate({("night", "2026-10-16"): sess(0, 200), ("day", "2026-10-19"): sess(500, 100)}, ROWS)
        self.assertEqual(e["status"], "ok")
        self.assertEqual(e["flow"], -200 + 400)

    def test_stale_when_night_of_next_day_arrives(self):
        e = MP.estimate({("night", "2026-10-16"): sess(0, 1), ("day", "2026-10-19"): sess(1, 0),
                         ("night", "2026-10-19"): sess(1, 0)}, ROWS)
        self.assertEqual(e["status"], "stale")

    def test_stale_when_baseline_two_days_old(self):
        e = MP.estimate({("night", "2026-10-16"): sess(0, 1), ("day", "2026-10-19"): sess(1, 0),
                         ("day", "2026-10-20"): sess(1, 0)}, ROWS)
        self.assertEqual(e["status"], "stale")

    def test_incomplete_without_previous_night(self):
        e = MP.estimate({("day", "2026-10-19"): sess(500, 100)}, ROWS)
        self.assertEqual(e["status"], "incomplete")

    def test_nodata_and_render(self):
        self.assertEqual(MP.estimate({}, []), {"status": "nodata"})
        self.assertIn("沒有期交所基準檔", MP.render_block({"status": "nodata"}))
        self.assertIn("不外推", MP.render_block({"status": "stale", "base_date": "2026-10-16"}))
        html = MP.render_block(MP.estimate({("night", "2026-10-16"): sess(100, 400)}, ROWS))
        self.assertIn("估計多空比", html)
        self.assertIn("不是反指標", html)

    def test_page_only_on_mxf(self):
        from biglot import futflow_view as F
        src = Path(F.__file__).read_text(encoding="utf-8")
        self.assertIn('if root == "MXF":', src)


if __name__ == "__main__":
    unittest.main()
