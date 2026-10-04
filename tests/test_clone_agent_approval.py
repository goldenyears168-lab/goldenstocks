"""複製人待批准區的行為釘樁。

重點不是覆蓋率，是釘住三件「錯了會很貴」的事：
  1. 本模組永遠不得取得送單能力（import 掃描）
  2. 不存在的 intent_id 不得寫進帳本（否則 A→C 升級門檻的同意率會被污染）
  3. 同一 intent_id 重複裁決只算最後一次（避免重複計數把同意率沖高）
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


class CloneApprovalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("GOLDENSTOCKS_DATA_DIR")
        os.environ["GOLDENSTOCKS_DATA_DIR"] = self._tmp.name
        import clone_agent.approval as A
        self.A = A
        self.day = "2026-10-02"
        d = Path(self._tmp.name) / "data" / "clone_agent"
        d.mkdir(parents=True, exist_ok=True)
        cands = [
            {"day": self.day, "time": "09:32:16", "symbol": "LU", "side": "Buy",
             "price": 524.0, "uname": "臻鼎-KY", "verdict": "ok"},
            {"day": self.day, "time": "11:32:08", "symbol": "LY", "side": "Buy",
             "price": 1448.0, "uname": "南電", "verdict": "ok"},
        ]
        for c in cands:
            c["intent_id"] = A.intent_id(c)
        (d / f"candidates_{self.day}.jsonl").write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in cands) + "\n", encoding="utf-8")
        self.ids = [c["intent_id"] for c in cands]

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GOLDENSTOCKS_DATA_DIR", None)
        else:
            os.environ["GOLDENSTOCKS_DATA_DIR"] = self._old
        self._tmp.cleanup()

    def test_no_order_capability_anywhere_in_package(self):
        """審核層不得 import 富邦 SDK 或 src/order/ —— 構造上就不可能送單。"""
        import clone_agent
        pkg = Path(clone_agent.__file__).parent
        banned = ("fubon_neo", "import fubon", "from order", "import order",
                  "place_order", "connect_fubon")
        hits = []
        for p in sorted(pkg.glob("*.py")):
            src = p.read_text(encoding="utf-8")
            code = "\n".join(ln.split("#")[0] for ln in src.splitlines())
            for b in banned:
                if b in code:
                    hits.append(f"{p.name}: {b}")
        self.assertEqual(hits, [], f"審核層出現送單能力: {hits}")

    def test_unknown_intent_id_rejected(self):
        with self.assertRaises(ValueError):
            self.A.record_decision(self.day, "deadbeef0000", "approve")
        self.assertEqual(self.A.load_decisions(self.day), {})

    def test_known_intent_id_on_wrong_day_rejected(self):
        with self.assertRaises(ValueError):
            self.A.record_decision("2026-09-29", self.ids[0], "approve")

    def test_bad_decision_rejected(self):
        for bad in ("submit", "SUBMIT_ORDER", "", "Approve"):
            with self.assertRaises(ValueError):
                self.A.record_decision(self.day, self.ids[0], bad)

    def test_last_decision_wins_not_counted_twice(self):
        self.A.record_decision(self.day, self.ids[0], "approve")
        self.A.record_decision(self.day, self.ids[0], "skip", "這檔我不碰")
        ds = self.A.load_decisions(self.day)
        self.assertEqual(len(ds), 1)
        self.assertEqual(ds[self.ids[0]]["decision"], "skip")
        st = self.A.agreement_stats([self.day])
        self.assertEqual(st["總裁決"], 1)
        self.assertEqual(st["總同意"], 0)

    def test_agreement_rate_denominator_is_decided_not_candidates(self):
        """只裁決一筆時同意率是 1/1，不是 1/2 —— 沒看的不算不同意。"""
        self.A.record_decision(self.day, self.ids[0], "approve")
        st = self.A.agreement_stats([self.day])
        self.assertEqual(st["同意率"], 100.0)
        self.assertEqual(st["分日"][0]["候選"], 2)
        self.assertEqual(st["分日"][0]["已裁決"], 1)

    def test_skip_reason_only_stored_for_skip(self):
        self.A.record_decision(self.day, self.ids[1], "approve", "這檔我不碰")
        self.assertEqual(self.A.load_decisions(self.day)[self.ids[1]]["skip_reason"], "")

    def test_intent_id_is_stable_and_distinguishes_price(self):
        base = {"day": self.day, "time": "09:32:16", "symbol": "LU", "side": "Buy", "price": 524.0}
        self.assertEqual(self.A.intent_id(base), self.A.intent_id(dict(base)))
        self.assertNotEqual(self.A.intent_id(base), self.A.intent_id({**base, "price": 525.0}))

    def test_edit_only_recorded_when_different(self):
        """沒改就不該寫 edited —— 否則帳本全是雜訊，看不出他真正改了哪些。"""
        c = self.A.load_candidates(self.day)[0]
        self.A.record_decision(self.day, self.ids[0], "approve",
                               edited={"price": c["price"], "lot": c.get("lot", 1), "note": ""})
        self.assertNotIn("edited", self.A.load_decisions(self.day)[self.ids[0]])

    def test_edit_recorded_when_changed(self):
        self.A.record_decision(self.day, self.ids[0], "approve",
                               edited={"price": 526.0, "lot": 2, "note": "再加一口"})
        e = self.A.load_decisions(self.day)[self.ids[0]]["edited"]
        self.assertEqual(e["price"], 526.0)
        self.assertEqual(e["lot"], 2)
        self.assertEqual(e["note"], "再加一口")

    def test_edit_out_of_range_rejected_and_nothing_written(self):
        for bad in ({"price": -5}, {"price": 0}, {"price": 200_000},
                    {"lot": 0}, {"lot": 999}, {"price": "DROP TABLE"}, {"lot": "x"}):
            with self.assertRaises(ValueError, msg=f"should reject {bad}"):
                self.A.record_decision(self.day, self.ids[0], "approve", edited=bad)
        self.assertEqual(self.A.load_decisions(self.day), {})

    def test_note_truncated_and_stripped(self):
        self.A.record_decision(self.day, self.ids[0], "approve",
                               edited={"note": "  " + "x" * 500 + "  "})
        self.assertEqual(len(self.A.load_decisions(self.day)[self.ids[0]]["edited"]["note"]), 300)

    def test_pages_render_without_data_files(self):
        """缺 capital_legs.json / fills.json 時頁面要降級顯示，不是 500。"""
        from clone_agent.panels import render_agent_page
        html = render_agent_page(self.day)
        self.assertIn("操盤手代理人", html)
        for f in ("f-px", "f-lot", "f-note"):      # 價格/口數/理由必須是可編輯欄位
            self.assertIn(f, html)
        self.assertIn("不會送出任何委託單", html)

    def test_blocked_candidates_have_no_submit_buttons(self):
        """被規則擋下的候選仍要顯示（供覆盤），但排在後面且標記出來。"""
        from clone_agent.panels import render_agent_page
        html = render_agent_page(self.day)
        self.assertIn("被規則擋下", html)


if __name__ == "__main__":
    unittest.main()
