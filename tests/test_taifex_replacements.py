import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src")]

import morning_gate_brief as M  # noqa: E402
import sync_morning_futures as S  # noqa: E402
import taifex_client as T  # noqa: E402


def row(fid, cd, close, vol, sess, pct=0.5):
    return {"date": "2026-10-16", "futures_id": fid, "contract_date": cd, "close": close, "volume": vol,
            "trading_session": sess, "spread_per": pct}


class MorningSnapshotTests(unittest.TestCase):
    def test_rows_use_snapshot_ids_and_skip_spread(self):
        def fake(day, fid, session):
            self.assertEqual(session, "after_market")
            return [row(fid, "202610", 47366.0, 41283, session), row(fid, "202610/202611", 150.0, 99, session)]
        with mock.patch.object(T, "fetch_futures_daily", fake):
            rows = S.fetch_taifex_snapshot_rows(date(2026, 10, 16), "TXF", "EXF")
        self.assertEqual({r["futures_id"] for r in rows}, {"TXF", "EXF"})
        self.assertEqual(len(rows), 2)                                           # 價差合約被排除
        self.assertEqual(S.pick_snapshot_row(rows, "TXF")["close"], 47366.0)       # 下游 pick 認得快照代號

    def test_taifex_ids_mapping(self):
        self.assertEqual((S.TAIFEX_ID["TXF"], S.TAIFEX_ID["EXF"]), ("TX", "TE"))


class NightGapTests(unittest.TestCase):
    def test_gap_from_after_market_vs_previous_regular_close(self):
        calls = []

        def fake(day, fid, session):
            calls.append((day, session))
            if session == "after_market":
                return [row("TX", "202610", 47366.0, 41283, session), row("TX", "202611", 47451.0, 1018, session)]
            if day == date(2026, 10, 15):
                return [row("TX", "202610", 46980.0, 99999, session), row("TX", "202611", 47100.0, 10, session)]
            return []
        with mock.patch.object(T, "fetch_futures_daily", fake):
            g = M._tx_night_gap("2026-10-16")
        self.assertAlmostEqual(g, (47366 / 46980 - 1) * 100)                     # 取量最大的近月,不是最後一列
        self.assertEqual(calls[0], (date(2026, 10, 16), "after_market"))

    def test_skips_weekend_back_to_last_trading_day(self):
        seen = []

        def fake(day, fid, session):
            seen.append(day)
            if session == "after_market":
                return [row("TX", "202610", 100.0, 5, session)]
            return [row("TX", "202610", 99.0, 5, session)] if day == date(2026, 10, 16) else []
        with mock.patch.object(T, "fetch_futures_daily", fake):
            g = M._tx_night_gap("2026-10-19")                                      # 週一:前一個一般時段是上週五
        self.assertAlmostEqual(g, (100 / 99 - 1) * 100)
        self.assertNotIn(date(2026, 10, 17), seen)                                 # 週六不查
        self.assertNotIn(date(2026, 10, 18), seen)

    def test_none_when_no_night_session(self):
        with mock.patch.object(T, "fetch_futures_daily", lambda *a: []):
            self.assertIsNone(M._tx_night_gap("2026-10-19"))


if __name__ == "__main__":
    unittest.main()
