"""儀表板「套用代理人規則」與代理人(trader_signal_watch)的對齊釘:欄位算法、內嵌門檻/名單。"""
from __future__ import annotations

import json
import re
import sys
import unittest
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))

try:
    import biglot_dashboard as bd
    from biglot.scoring_support import _rolling
    import trader_signal_watch as w
    from clone_agent.rules_version import load_rules, universe_sids
except Exception as exc:  # noqa: BLE001
    bd = None
    _IMPORT_ERR = exc


@unittest.skipIf(bd is None, "biglot_dashboard 無法在此環境匯入")
class DashboardAgentAlignTest(unittest.TestCase):
    def test_dist_hi30_uses_only_last_30_minutes(self):
        now = 1_000_000.0
        q = deque([
            (now - 2000, 120.0, 1e6, 1, False, False),   # 30 分外的高點不算
            (now - 1500, 100.0, 1e6, 1, False, False),
            (now - 900, 110.0, 1e6, -1, False, False),   # 窗內最高
            (now - 10, 109.45, 1e6, -1, False, False),
        ])
        bd.ST.recent["__T__"] = q
        try:
            out = _rolling("__T__", now)
        finally:
            bd.ST.recent.pop("__T__", None)
        self.assertAlmostEqual(out["dhi30_r"], (109.45 / 110.0 - 1) * 100, places=9)
        self.assertLessEqual(out["dhi30_r"], -0.5)

    def test_embedded_agent_constants_match_rules_and_watcher(self):
        m = re.search(r"const AGENT=(\{.*?\}|null);", bd.TABLE_INTERACT_JS)
        self.assertIsNotNone(m)
        a = json.loads(m.group(1))
        sg = load_rules()["signal"]
        self.assertEqual(a["pct"], sg["rank_keep_pct"])
        self.assertEqual(a["pull"], sg["pullback_max"])
        self.assertEqual(a["amtmin"], sg["amt_pct_min"])
        self.assertEqual(a["skip"], list(sg["skip_hours"]))
        self.assertEqual(a["start"], sg["start_hm"])
        self.assertEqual((a["pct"], a["pull"], a["amtmin"], tuple(a["skip"])),
                         (w.RANK_KEEP_PCT, w.PULLBACK_MAX, w.AMT_PCT_MIN, w.SKIP_HOURS))
        self.assertEqual(set(a["members"]), w.core_universe() & universe_sids())

    def test_placeholder_replaced(self):
        self.assertNotIn("__AGENT_RULES__", bd.TABLE_INTERACT_JS)


if __name__ == "__main__":
    unittest.main()
