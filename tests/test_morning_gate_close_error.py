"""morning_gate_brief.close_error_lines：讀 claudecode 預測帳本（唯讀），缺檔不影響簡報。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from morning_gate_brief import close_error_lines


def _write(d: Path, rows, res):
    (d / "close_forecasts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (d / "close_results.json").write_text(json.dumps(res), encoding="utf-8")


class CloseErrorLinesTest(unittest.TestCase):
    def test_missing_files_give_empty(self):
        self.assertEqual(close_error_lines(Path(tempfile.mkdtemp())), [])

    def test_small_sample_says_no_conclusion_and_skips_replay(self):
        d = Path(tempfile.mkdtemp())
        rows, res = [], {}
        for i, (kind, sid, rep) in enumerate([("taiex", "TAIEX", False), ("stock", "2330", False), ("stock", "2317", True)]):
            r = {"id": f"x{i}", "bid": "20261012_1200", "kind": kind, "sid": sid, "target_date": "2026-10-12", "pred_close": 101.0, "ref_px": 100.0}
            if rep:
                r["replay"] = True
            rows.append(r)
            res[r["id"]] = {"status": "ok", "actual_close": 100.5, "err_pct": 0.5, "naive_err_pct": -0.5, "beat_naive": False}
        _write(d, rows, res)
        txt = "\n".join(close_error_lines(d))
        self.assertIn("加權指數 最後一次預測 101 → 收 100", txt)
        self.assertIn("個股 1 檔", txt)
        self.assertIn("不下結論", txt)

    def test_enough_days_reports_skill(self):
        d = Path(tempfile.mkdtemp())
        rows, res = [], {}
        for day in range(15):
            for s in range(3):
                r = {"id": f"{day}-{s}", "bid": "b", "kind": "stock", "sid": str(1000 + s), "target_date": f"2026-09-{day + 1:02d}", "pred_close": 1.0, "ref_px": 1.0}
                rows.append(r)
                res[r["id"]] = {"status": "ok", "actual_close": 1.0, "err_pct": 0.5, "naive_err_pct": 1.0, "beat_naive": True}
        _write(d, rows, res)
        txt = "\n".join(close_error_lines(d))
        self.assertIn("技巧分 +50%", txt)
        self.assertNotIn("不下結論", txt)


if __name__ == "__main__":
    unittest.main()
