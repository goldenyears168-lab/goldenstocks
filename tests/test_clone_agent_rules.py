"""代理人規則版本釘樁：watcher 的常數必須等於 config/clone_agent_rules.json；改參數必須升版號。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED = {"1.0.0": "b05ddc2aa2"}   # 版本號 → 規則指紋；改參數就要新增一列並升 version


class CloneRulesTest(unittest.TestCase):
    def test_fingerprint_matches_version(self):
        from clone_agent.rules_version import fingerprint, load_rules
        r = load_rules()
        self.assertIn(r["version"], PINNED, "規則改了卻沒升版號/沒登記指紋")
        self.assertEqual(fingerprint(r), PINNED[r["version"]])

    def test_watcher_constants_equal_rules(self):
        from clone_agent.rules_version import load_rules
        sys.path.insert(0, str(ROOT / "scripts" / "research"))
        sys.path.insert(0, str(ROOT / "src"))
        import trader_signal_watch as w
        s = load_rules()["signal"]
        got = {"big_amt": w.BIG_AMT, "retail_cap": w.RETAIL_CAP, "amt_pct_min": w.AMT_PCT_MIN,
               "amt_pct_strict": w.AMT_PCT_STRICT, "pullback_max": w.PULLBACK_MAX,
               "pullback_strict": w.PULLBACK_STRICT, "chg_cm_strict": w.CHG_CM_STRICT,
               "skip_hours": list(w.SKIP_HOURS), "recheck_min": w.RECHECK_MIN}
        self.assertEqual(got, s)


if __name__ == "__main__":
    unittest.main()
