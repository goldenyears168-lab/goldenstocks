import os
import tempfile
import unittest

os.environ["GOLDENSTOCKS_DATA_DIR"] = tempfile.mkdtemp()

from clone_agent import agent_supervisor as sv  # noqa: E402

CFG = sv.load_config()


def feat(**kw):
    base = {"px": 100.0, "chg_pct": 1.0, "dist_hi30": -0.6, "dist_lo30": 0.8, "amt_pct": 60.0,
            "big30_wan": -500, "rank": {"big_day": 0.6, "big30": 0.8, "ret30": 0.9}, "last_trade_age_sec": 5}
    base.update(kw)
    return base


class MirrorTest(unittest.TestCase):
    def test_mirror_rank_is_one_minus_long_rank(self):
        r = sv.mirror_short(feat(), CFG)
        self.assertEqual(r["values"]["mirror_rank"], {"big_day": 0.4, "big30": 0.2, "ret30": 0.1})
        self.assertTrue(r["conds"]["c3_big30_bottom70"])

    def test_all_met_and_price_only(self):
        r = sv.mirror_short(feat(), CFG)
        self.assertTrue(r["all_met"] and r["flow_met"] and r["price_only_met"])
        r = sv.mirror_short(feat(dist_lo30=0.2), CFG)
        self.assertFalse(r["all_met"] or r["price_only_met"])
        r = sv.mirror_short(feat(rank={"big_day": 0.1, "big30": 0.1, "ret30": 0.1}), CFG)
        self.assertFalse(r["flow_met"])

    def test_missing_rank_is_false_not_error(self):
        r = sv.mirror_short(feat(rank={}), CFG)
        self.assertFalse(r["all_met"])


class TriggerTest(unittest.TestCase):
    def test_e1_needs_flow_and_price(self):
        z = CFG["triggers"]["e1_price_below_entry_bps"]
        px = 100 * (1 - (z + 1) / 1e4)
        self.assertTrue(sv.eval_exit_triggers(feat(px=px), 100.0, CFG)["E1"]["met"])
        self.assertFalse(sv.eval_exit_triggers(feat(px=100.0), 100.0, CFG)["E1"]["met"])
        flow_off = feat(px=px, rank={"big_day": 0.6, "big30": 0.1, "ret30": 0.1})
        self.assertFalse(sv.eval_exit_triggers(flow_off, 100.0, CFG)["E1"]["met"])

    def test_e3_and_no_entry_px(self):
        L = CFG["triggers"]["e3_loss_bps"]
        self.assertTrue(sv.eval_exit_triggers(feat(px=100 * (1 - (L + 1) / 1e4)), 100.0, CFG)["E3"]["met"])
        r = sv.eval_exit_triggers(feat(), None, CFG)
        self.assertIsNone(r["pnl_bps"])
        self.assertFalse(r["E1"]["met"] or r["E3"]["met"])

    def test_cancel_triggers(self):
        t = sv.eval_cancel_triggers(feat(big30_wan=-10), {"big30_wan": 50}, 12, 0, 0, CFG)
        self.assertTrue(t["C1"]["met"])
        self.assertFalse(sv.eval_cancel_triggers(feat(big30_wan=-10), {"big30_wan": 50}, 5, 0, 0, CFG)["C1"]["met"])
        self.assertTrue(sv.eval_cancel_triggers(feat(chg_pct=9.6), {}, 1, 0, 0, CFG)["C2"]["met"])
        self.assertTrue(sv.eval_cancel_triggers(feat(last_trade_age_sec=400), {}, 1, 0, 0, CFG)["C2"]["met"])
        self.assertTrue(sv.eval_cancel_triggers(feat(), {}, 1, -40, -60, CFG)["C3"]["met"])
        self.assertFalse(sv.eval_cancel_triggers(feat(), {}, 1, -40, -10, CFG)["C3"]["met"])


class GuardTest(unittest.TestCase):
    pack = {"data_age_sec": 10,
            "positions": [{"sid": "2330", "ours": True, "manual_hold": False, "triggers": {"E1": {"met": True}, "E3": {"met": False}}},
                          {"sid": "2303", "ours": True, "manual_hold": True, "triggers": {"E1": {"met": True}}}],
            "orders": [{"sid": "3324", "order_no": "A1", "ours": True, "triggers": {"C3": {"met": True}, "C1": {"met": False}}}]}
    ev = ["f1", "f2"]

    def g(self, a, pack=None, review=None):
        return sv.guard_action(a, pack or self.pack, review or {"insufficient_data": False})

    def test_exit_ok_and_rejects(self):
        self.assertIsNone(self.g({"type": "exit", "target": "2330", "trigger_id": "E1", "evidence": self.ev}))
        self.assertEqual(self.g({"type": "exit", "target": "2330", "trigger_id": "E3", "evidence": self.ev}), "trigger_not_met")
        self.assertEqual(self.g({"type": "exit", "target": "2330", "trigger_id": "E1", "evidence": ["a"]}), "evidence<2")
        self.assertEqual(self.g({"type": "exit", "target": "2303", "trigger_id": "E1", "evidence": self.ev}), "not_ours_or_manual_hold")
        self.assertEqual(self.g({"type": "exit", "target": "9999", "trigger_id": "E1", "evidence": self.ev}), "target_not_in_positions")
        self.assertEqual(self.g({"type": "exit", "target": "2330", "evidence": self.ev}), "trigger_not_met")

    def test_cancel_by_order_no_or_sid(self):
        self.assertIsNone(self.g({"type": "cancel", "target": "A1", "trigger_id": "C3", "evidence": self.ev}))
        self.assertIsNone(self.g({"type": "cancel", "target": "3324", "trigger_id": "C3", "evidence": self.ev}))
        self.assertEqual(self.g({"type": "cancel", "target": "A1", "trigger_id": "C1", "evidence": self.ev}), "trigger_not_met")

    def test_cancel_real_llm_formats_2026_10_06(self):
        # 兩筆真提案:target 夾帶股名/說明、trigger_id 寫 "C1,C3"。修前一律 target_not_in_orders
        pk = {**self.pack, "orders": [{"sid": "2408", "order_no": "s05VY", "intent_id": "6d9e8fb92418", "ours": True,
                                       "triggers": {"C1": {"met": True}, "C3": {"met": True}, "C2": {"met": False}}}]}
        t1 = "2408 南亞科 掛單 order_no=s05VY (limit 531)"
        t2 = "2408 南亞科 掛單 (order_no s05VY, intent_id 6d9e8fb92418, limit 531)"
        self.assertIsNone(self.g({"type": "cancel", "target": t1, "trigger_id": "C1,C3", "evidence": self.ev}, pack=pk))
        self.assertIsNone(self.g({"type": "cancel", "target": t2, "trigger_id": "C1", "evidence": self.ev}, pack=pk))
        self.assertEqual(self.g({"type": "cancel", "target": t1, "trigger_id": "C1,C2", "evidence": self.ev}, pack=pk), "trigger_not_met")
        self.assertEqual(self.g({"type": "cancel", "target": "9999 不存在", "trigger_id": "C1", "evidence": self.ev}, pack=pk), "target_not_in_orders")

    def test_resolve_order_ambiguous_is_not_resolved(self):
        orders = [{"sid": "2408", "order_no": "A1"}, {"sid": "2408", "order_no": "A2"}]
        self.assertIsNone(sv.resolve_order("2408 南亞科 掛單", orders))
        self.assertEqual(sv.resolve_order("南亞科 order_no=A2", orders)["order_no"], "A2")
        self.assertEqual(sv.split_trigger_ids("C1, C3"), ["C1", "C3"])
        self.assertEqual(sv.split_trigger_ids(None), [])

    def test_stale_and_insufficient_block_everything(self):
        a = {"type": "switch_off", "target": "all", "evidence": self.ev}
        self.assertIsNone(self.g(a))
        self.assertEqual(self.g(a, pack={**self.pack, "data_age_sec": 120}), "stale_data")
        self.assertEqual(self.g(a, review={"insufficient_data": True}), "insufficient_data")

    def test_buy_is_not_a_known_action(self):
        self.assertEqual(self.g({"type": "buy", "target": "2330", "evidence": self.ev}), "unknown_type")


class DashboardTest(unittest.TestCase):
    def test_parse_rows(self):
        html = ("<table><thead><tr><th>股票<span class='sub'>點</span></th><th>現價</th><th>全日大戶</th></tr></thead>"
                "<tr><td class='nm'><a href='/stock?sid=2330'>2330 台積電</a></td><td>1000</td><td>—</td></tr>"
                "<tr><td class='nm'><a href='/stock?sid=1101'>1101 台泥</a></td><td>50</td><td>+9</td></tr></table>")
        r = sv.parse_dashboard_rows(html, {"2330"})
        self.assertEqual(list(r), ["2330"])
        self.assertEqual(r["2330"], {"股票": "2330 台積電", "現價": "1000"})


class StorageTest(unittest.TestCase):
    def test_journal_state_view_roundtrip(self):
        sv.journal_append("2026-10-06", {"kind": "patrol", "x": 1})
        self.assertEqual(sv.read_journal("2026-10-06")[0]["x"], 1)
        st = sv.read_state()
        st["notes"]["2330"] = "n"
        sv.write_state(st)
        self.assertEqual(sv.read_state()["notes"]["2330"], "n")
        sv.write_view({"a": 1})
        self.assertEqual(sv.read_view(), {"a": 1})
        self.assertIn("指令", sv.read_directives())


if __name__ == "__main__":
    unittest.main()
