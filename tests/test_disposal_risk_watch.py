"""釘住處置風險計數器的三個正確性規則（2026-09-30 建立時各踩過一次）。

1. 只有第一~第八款會累積成處置次數；第 9~13 款（當沖比、本益比…）被公布注意但不累積。
2. 處置期滿後計數重新起算 —— 否則剛出關的股票會被永遠誤判成「已達標」。
3. 「最近 N 個營業日」必須用真實交易日曆；拿「注意公告日」當代理會讓視窗偏長、n30 高估。

驗收案例：2455 全新 2026-09-15 出關 → 09-16 起連續被列注意 → 09-22 滿連續 5 日 →
09-23 進處置。正確的實作在 09-21（09-22 開盤前）就該是「倒數 1」。
"""
from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "disposal_risk_watch", ROOT / "scripts" / "research" / "disposal_risk_watch.py")

CAL = ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12",
       "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19",
       "2026-09-22", "2026-09-23"]


def _load(tmp: Path, notices, windows):
    M = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(M)
    npath = tmp / "notice_history.csv"; wpath = tmp / "disposal_windows.csv"
    with npath.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["market", "stock_id", "date", "cum", "clauses", "reason"])
        w.writeheader(); w.writerows(notices)
    with wpath.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["market", "stock_id", "start", "end", "measure"])
        w.writeheader(); w.writerows(windows)
    M.NOTICE_CSV = npath; M.WINDOWS_CSV = wpath
    M._calendar = lambda _df: list(CAL)          # 固定日曆，避免測試依賴生產 DB
    return M


def _n(sid, date, clauses):
    return {"market": "TWSE", "stock_id": sid, "date": date, "cum": "1", "clauses": clauses, "reason": ""}


class DisposalRiskTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_clause_9_to_13_does_not_accumulate(self):
        """只中第十款（當沖比）不該累積成處置次數。"""
        notices = [_n("1111", d, "10") for d in ("2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19", "2026-09-22")]
        M = _load(self.tmp, notices, [])
        self.assertNotIn("1111", M.build("2026-09-22")["stocks"],
                         "只有第 9~13 款的個股不應進入處置風險計數")

        notices += [_n("2222", d, "1") for d in ("2026-09-16", "2026-09-17")]
        M = _load(self.tmp, notices, [])
        v = M.build("2026-09-17")["stocks"]["2222"]
        self.assertEqual(v["streak"], 2)

    def test_counting_resets_after_disposal(self):
        """處置期滿後計數重新起算（2455 的實際情形）。"""
        old = [_n("2455", d, "1") for d in ("2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12")]
        new = [_n("2455", d, "6") for d in ("2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19")]
        win = [{"market": "TWSE", "stock_id": "2455", "start": "2026-09-08",
                "end": "2026-09-15", "measure": "第二次處置"}]
        M = _load(self.tmp, old + new, win)
        v = M.build("2026-09-19")["stocks"]["2455"]
        self.assertEqual(v["count_since"], "2026-09-15")
        self.assertEqual(v["streak"], 4, "出關後只該算 09-16~09-19 這 4 天")
        self.assertEqual(v["in30"], 4, "視窗不可回溯到上次處置結束之前")
        self.assertEqual(v["need"], 1)
        self.assertEqual(v["level"], 3)          # 倒數 1 → 紅燈

    def test_full_2455_timeline(self):
        """09-21 收盤後（= 09-22 開盤前）必須已經是紅燈；09-22 滿 5 日。"""
        days = ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19", "2026-09-22"]
        win = [{"market": "TWSE", "stock_id": "2455", "start": "2026-09-08",
                "end": "2026-09-15", "measure": "第二次處置"}]
        M = _load(self.tmp, [_n("2455", d, "6") for d in days], win)
        self.assertEqual(M.build("2026-09-18")["stocks"]["2455"]["level"], 2)   # 連 3 日 → 橘
        v19 = M.build("2026-09-19")["stocks"]["2455"]
        self.assertEqual((v19["level"], v19["need"]), (3, 1))                   # 連 4 日 → 紅‧倒數 1
        v22 = M.build("2026-09-22")["stocks"]["2455"]
        self.assertEqual((v22["level"], v22["need"]), (3, 0))                   # 連 5 日 → 已達標
        self.assertIn("已達標", v22["label"])

    def test_in_disposal_takes_priority(self):
        win = [{"market": "TWSE", "stock_id": "2455", "start": "2026-09-22",
                "end": "2026-09-23", "measure": "第二次處置"}]
        M = _load(self.tmp, [_n("2455", "2026-09-22", "1")], win)
        v = M.build("2026-09-22")["stocks"]["2455"]
        self.assertEqual(v["level"], -1)
        self.assertTrue(v["in_disposal"])
        self.assertEqual(v["release_date"], "2026-09-23")


if __name__ == "__main__":
    unittest.main()
