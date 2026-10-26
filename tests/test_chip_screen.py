import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))

import chip_screen as cs  # noqa: E402


def _inst(**kw):
    base = {"f_streak": 0, "t_streak": 0, "d_streak": 0, "t_dir": 0, "tri1": 0, "tri1_pct": 0.0, "f20": 0.0,
            "f5": 0.0, "f5_pct": 0.0, "t5": 0.0, "t5_pct": 0.0, "d5": 0.0, "d5_pct": 0.0}
    base.update(kw)
    return base


class StreakTest(unittest.TestCase):
    def test_streak(self):
        self.assertEqual(cs.streak([1, 2, 3]), 3)
        self.assertEqual(cs.streak([-1, 2, -3, -4]), -2)
        self.assertEqual(cs.streak([1, 2, 0]), 0)
        self.assertEqual(cs.streak([]), 0)


class EvaluateTest(unittest.TestCase):
    def test_large_cap_lead_is_foreign(self):
        f = {"px": {"chg": 0.0, "r5": 1.0, "ratio": 1.0, "vm20_lots": 9999}, "tier": "大",
             "inst": _inst(f_streak=6, f5=500.0, f5_pct=5.0, t5=-900.0, t5_pct=-9.0, f20=800.0)}
        ids = {(h["id"], h["side"]) for h in cs.evaluate(f)["hits"]}
        self.assertIn(("I1", "bull"), ids)
        self.assertIn(("I2", "bull"), ids)  # 大型看外資，投信賣超不影響

    def test_small_cap_lead_is_dealer(self):
        f = {"px": {"chg": 0.0, "ratio": 1.0, "vm20_lots": 9999}, "tier": "小", "inst": _inst(f5=500.0, f5_pct=9.0, d5=-30.0, d5_pct=-4.0)}
        sides = {h["side"] for h in cs.evaluate(f)["hits"] if h["id"] == "I2"}
        self.assertEqual(sides, {"bear"})

    def test_margin_signal_needs_price_direction(self):
        mg = {"mb": 1000, "sb": 10, "ratio": 1.0, "mb_d5": 50, "sb_d5": -5, "mb_d5_pct": 5.0, "mb_d20_pct": None}
        down = cs.evaluate({"px": {"r5": -3.0, "ratio": 1.0, "vm20_lots": 9999}, "margin": mg})
        up = cs.evaluate({"px": {"r5": 3.0, "ratio": 1.0, "vm20_lots": 9999}, "margin": mg})
        self.assertIn("I5", {h["id"] for h in down["hits"]})
        self.assertNotIn("I5", {h["id"] for h in up["hits"]})

    def test_label_needs_two_groups(self):
        f = {"px": {"ratio": 1.0, "vm20_lots": 9999}, "tier": "大", "inst": _inst(f_streak=9)}
        self.assertEqual(cs.evaluate(f)["label"], "資料不足")

    def test_agree_conflict_and_neutral(self):
        bull_inst = _inst(f_streak=9)
        disp_bull = {"asof": "b", "base": "a", "b1000": 50, "d_b1000": 1.0, "d_b400": 1.0, "d_b400_800": 0.0, "d_r100": -1.0, "d_tot": -10}
        disp_bear = {**disp_bull, "d_b1000": -1.0, "d_b400": -1.0, "d_r100": 1.0, "d_tot": 10}
        px = {"ratio": 1.0, "vm20_lots": 9999}
        agree = cs.evaluate({"px": px, "inst": bull_inst, "disp": disp_bull})
        self.assertEqual((agree["label"], agree["strength"]), ("偏多", "強"))
        conflict = cs.evaluate({"px": px, "inst": bull_inst, "disp": disp_bear})
        self.assertEqual(conflict["label"], "分歧")
        neutral = cs.evaluate({"px": px, "inst": _inst(), "disp": {**disp_bull, "d_b1000": 0.0, "d_b400": 0.0, "d_r100": 0.0, "d_tot": 0}})
        self.assertEqual(neutral["label"], "中性")

    def test_watch_flags_do_not_move_direction(self):
        f = {"px": {"chg": 9.9, "ratio": 3.0, "vm20_lots": 100}, "tier": "小", "inst": _inst(),
             "disposal": {"windows": [], "notice_today": [("4", "10")], "notices_30d": 4}}
        ev = cs.evaluate(f)
        self.assertTrue({"W2", "W3", "W5", "W11"} <= {h["id"] for h in ev["hits"]})
        self.assertEqual(ev["group_net"], {"inst": 0, "branch": 0, "disp": 0})

    def test_group_conflict_flag(self):
        f = {"px": {"ratio": 1.0, "vm20_lots": 9999}, "tier": "大", "inst": _inst(f_streak=9, t_dir=-1, tri1=-5.0, tri1_pct=-1.0)}
        ids = [h["id"] for h in cs.evaluate(f)["hits"]]
        self.assertIn("W14", ids)


class DedupTest(unittest.TestCase):
    def test_one_row_per_date_prefers_first_and_windows_by_dates(self):
        rows = [("d5", 5, "twse"), ("d5", 50, "finmind"), ("d4", 4, "twse"), ("d4", 40, "finmind"),
                ("d3", 3, "twse"), ("d2", 2, "finmind"), ("d2", 20, "x"), ("d1", 1, "twse")]
        out = cs.dedup_by_date(rows, 4)
        self.assertEqual([r[0] for r in out], ["d2", "d3", "d4", "d5"])   # 舊→新、4 個「日期」而不是 4 列
        self.assertEqual([r[1] for r in out], [2, 3, 4, 5])               # 同日取第一列


class LiquidityGateTest(unittest.TestCase):
    def test_gate_blocks_direction(self):
        bull = _inst(f_streak=9)
        disp = {"asof": "b", "base": "a", "b1000": 50, "d_b1000": 1.0, "d_b400": 1.0, "d_b400_800": 0.0, "d_r100": -1.0, "d_tot": -10}
        thin = {"ratio": 1.0, "vm20_lots": 120, "am20_yi": 0.02, "close": 80}
        ok = {"ratio": 1.0, "vm20_lots": 5000, "am20_yi": 3.0, "close": 80}
        cheap = {"ratio": 1.0, "vm20_lots": 5000, "am20_yi": 3.0, "close": 8}
        self.assertEqual(cs.evaluate({"px": thin, "tier": "大", "inst": bull, "disp": disp})["label"], "流動性不足")
        self.assertEqual(cs.evaluate({"px": cheap, "tier": "大", "inst": bull, "disp": disp})["label"], "流動性不足")
        self.assertEqual(cs.evaluate({"px": ok, "tier": "大", "inst": bull, "disp": disp})["label"], "偏多")
        self.assertIn("W16", {h["id"] for h in cs.evaluate({"px": thin, "tier": "大", "inst": bull, "disp": disp})["hits"]})


class ExpertRulesTest(unittest.TestCase):
    PX = {"chg": 0.0, "ratio": 1.0, "vm20_lots": 5000, "r10": 3.0, "min_vol5_lots": 900}

    def test_disp_streak_and_jump(self):
        base = {"asof": "b", "base": "a", "b1000": 50, "d_b1000": 0.0, "d_b400": 0.0, "d_b400_800": 0.0, "d_r100": 0.0, "d_tot": 0}
        up = cs.evaluate({"px": self.PX, "disp": {**base, "series": [40.0, 40.3, 40.6, 41.0, 42.2]}})
        ids = {(h["id"], h["side"]) for h in up["hits"]}
        self.assertIn(("D2", "bull"), ids)
        self.assertIn("W15", {h["id"] for h in up["hits"]})  # 最後一週 +1.2pp
        mixed = cs.evaluate({"px": self.PX, "disp": {**base, "series": [40.0, 40.3, 40.1, 41.0, 41.2]}})
        self.assertNotIn("D2", {h["id"] for h in mixed["hits"]})
        down = cs.evaluate({"px": self.PX, "disp": {**base, "series": [42.0, 41.9, 41.8, 41.7, 41.6]}})
        self.assertIn(("D2", "bear"), {(h["id"], h["side"]) for h in down["hits"]})

    def test_small_cap_foreign_needs_liquidity(self):
        inst = _inst(f5=300.0, f5_pct=12.0)
        ok = cs.evaluate({"px": self.PX, "tier": "小", "inst": inst})
        thin = cs.evaluate({"px": {**self.PX, "vm20_lots": 400}, "tier": "小", "inst": inst})
        large = cs.evaluate({"px": self.PX, "tier": "大", "inst": inst})
        self.assertIn("I8", {h["id"] for h in ok["hits"]})
        self.assertNotIn("I8", {h["id"] for h in thin["hits"]})
        self.assertNotIn("I8", {h["id"] for h in large["hits"]})

    def test_forces_need_three_and_volume_share(self):
        inst = _inst(f5=10.0, t5=10.0, d5=10.0, tri5_pct=4.0)
        self.assertIn("I9", {h["id"] for h in cs.evaluate({"px": self.PX, "tier": "中", "inst": inst})["hits"]})
        weak = _inst(f5=10.0, t5=10.0, d5=-1.0, tri5_pct=4.0)
        self.assertNotIn("I9", {h["id"] for h in cs.evaluate({"px": self.PX, "tier": "中", "inst": weak})["hits"]})
        low = _inst(f5=10.0, t5=10.0, d5=10.0, tri5_pct=1.0)
        self.assertNotIn("I9", {h["id"] for h in cs.evaluate({"px": self.PX, "tier": "中", "inst": low})["hits"]})

    def test_main_buy_with_short_high_needs_branch(self):
        mg = {"mb": 100, "sb": 50, "ratio": 50.0, "mb_d5": 0, "sb_d5": 5, "mb_d5_pct": 0.0, "mb_d20_pct": None, "sb_high20": True}
        br = {"win": {"5": {"conc_pct": 8.0}}, "rules": [], "named": []}
        yes = cs.evaluate({"px": self.PX, "margin": mg, "branch": br})
        no_branch = cs.evaluate({"px": self.PX, "margin": mg})
        self.assertIn("B-X1", {h["id"] for h in yes["hits"]})
        self.assertIn("B-X2", {h["id"] for h in yes["hits"]})
        self.assertNotIn("B-X1", {h["id"] for h in no_branch["hits"]})


if __name__ == "__main__":
    unittest.main()
