import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src")]

from tmf_channel import causal_engine as CE  # noqa: E402


def make_db(rows):
    d = tempfile.mkdtemp()
    p = Path(d) / "s.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE market_vix_daily(symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL NOT NULL, "
              "n_ticks INTEGER, source TEXT NOT NULL, synced_at TEXT, PRIMARY KEY(symbol,date,source))")
    c.executemany("INSERT INTO market_vix_daily(symbol,date,close,source) VALUES ('VIXTWN',?,?,?)", rows)
    c.commit()
    c.close()
    return p


class VixDeltaFallbackTests(unittest.TestCase):
    def test_extends_beyond_stale_computed_with_official(self):
        p = make_db([("2026-09-30", 20.0, "computed"), ("2026-10-01", 21.0, "computed"),
                     ("2026-10-01", 21.4, "finmind"), ("2026-10-02", 22.4, "finmind"),
                     ("2026-10-05", 22.0, "taifex"), ("2026-10-06", 23.5, "taifex")])
        out = CE.load_vixtwn_delta(p)
        self.assertAlmostEqual(out["2026-10-01"], 1.0)               # computed 涵蓋的日期不變(用 computed 自己的 Δ)
        self.assertAlmostEqual(out["2026-10-02"], 22.4 - 21.4)       # 之後改用官方 Δ
        self.assertAlmostEqual(out["2026-10-05"], 22.0 - 22.4)
        self.assertAlmostEqual(out["2026-10-06"], 1.5)

    def test_computed_only_unchanged(self):
        p = make_db([("2026-09-30", 20.0, "computed"), ("2026-10-01", 21.0, "computed")])
        self.assertEqual(CE.load_vixtwn_delta(p), {"2026-10-01": 1.0})

    def test_official_series_not_touched_when_source_explicit(self):
        p = make_db([("2026-10-01", 21.4, "finmind"), ("2026-10-02", 22.4, "finmind")])
        self.assertAlmostEqual(CE.load_vixtwn_delta(p, source="finmind")["2026-10-02"], 1.0)

    def test_no_data_returns_empty(self):
        self.assertEqual(CE.load_vixtwn_delta(make_db([])), {})


if __name__ == "__main__":
    unittest.main()
