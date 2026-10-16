"""C18acc screen 與下單層的依賴反轉：未注入 fail-closed、注入後與原 inline 邏輯等價。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import rrg_mono_swap_accel_screen as screen
from order import c18acc_screen_hooks as hooks


class _Reset(unittest.TestCase):
    def tearDown(self):
        screen.install_order_hooks(None)


class FailClosedTest(_Reset):
    def test_no_hooks_means_every_hook_is_none(self):
        screen.install_order_hooks(None)
        for name in ("assert_pool", "pyramid_cfg", "pyramid_scan", "pyramid_record",
                     "process_orders", "send_fill_report", "send_pool_digest"):
            self.assertIsNone(screen._hook(name), name)

    def test_install_wires_all_hooks(self):
        hooks.install()
        for name in ("assert_pool", "pyramid_cfg", "pyramid_scan", "pyramid_record",
                     "process_orders", "send_fill_report", "send_pool_digest"):
            self.assertIsNotNone(screen._hook(name), name)


class ProcessOrdersEquivalenceTest(unittest.TestCase):
    def _run(self, *, order_enabled, auto_submit, dry):
        cfg = SimpleNamespace(order_enabled=order_enabled, auto_submit=auto_submit)
        out = {"applied_actions": ["A"], "results": []}
        with patch.object(hooks, "load_c18acc_order_config", return_value=cfg), \
                patch.object(hooks, "process_c18acc_orders", return_value=out) as m:
            res = hooks._process_orders(["x"], session_date="2026-10-01", poll_minute="09:05", dry=dry)
        return res, m

    def test_disabled_skips(self):
        res, m = self._run(order_enabled=False, auto_submit=True, dry=False)
        self.assertEqual(res, (None, None))
        m.assert_not_called()

    def test_dry_without_auto_submit_skips(self):
        res, m = self._run(order_enabled=True, auto_submit=False, dry=True)
        self.assertEqual(res, (None, None))
        m.assert_not_called()

    def test_live_without_auto_submit_runs_as_dry_and_keeps_state(self):
        res, m = self._run(order_enabled=True, auto_submit=False, dry=False)
        self.assertTrue(m.call_args.kwargs["dry_run"])
        self.assertIsNotNone(res[0])
        self.assertIsNone(res[1])

    def test_live_auto_submit_applies_actions(self):
        res, m = self._run(order_enabled=True, auto_submit=True, dry=False)
        self.assertFalse(m.call_args.kwargs["dry_run"])
        self.assertEqual(res[1], ["A"])

    def test_dry_with_auto_submit_runs_dry_without_applying(self):
        res, m = self._run(order_enabled=True, auto_submit=True, dry=True)
        self.assertTrue(m.call_args.kwargs["dry_run"])
        self.assertIsNone(res[1])


class ChampionPoolConsistencyTest(unittest.TestCase):
    def test_champion_pool_matches_order_yaml(self):
        """原本在每次 _resolve_champion_config 執行的守衛，改為測試釘住。"""
        from order.c18acc_order_config import assert_pool_matches_champion
        assert_pool_matches_champion(str(screen.champion_score_swap_c_config().candidate_pool))


if __name__ == "__main__":
    unittest.main()
