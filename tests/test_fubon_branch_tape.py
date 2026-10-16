import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts" / "research"), str(ROOT / "scripts" / "order")]

import fubon_branch_tape as F  # noqa: E402


def branch_html(rows):
    out = []
    for sid, name, b, s in rows:
        out.append(
            "<tr><td class=\"t4t1\" nowrap id=\"oAddCheckbox\"> <SCRIPT LANGUAGE=javascript> <!-- "
            f"GenLink2stk('AS{sid}','{name}'); //--> </SCRIPT> </td> <td class=\"t3n1\" nowrap>{b:,}</td> "
            f"<td class=\"t3n1\" nowrap>{s:,}</td> <td class=\"t3n1\" nowrap>{b - s:,}</td> </tr>"
        )
    return "<table>" + "".join(out) + "</table>"


def stock_html(rows):
    out = []
    for token, name, b, s in rows:
        out.append(
            f'<TR><TD class="t4t1" nowrap><a href="/z/zc/zco/zco0/zco0.djhtm?a=2303&b={token}&BHID=9200">{name}</a></TD> '
            f'<TD class="t3n1">{b:,}</TD> <TD class="t3n1">{s:,}</TD> <TD class="t3n1">{b - s:,}</TD> <TD class="t3n1">1.19%</TD></TR>'
        )
    return "<table>" + "".join(out) + "</table>"


class ParseTests(unittest.TestCase):
    def test_branch_page(self):
        html = branch_html([("2303", "聯電", 7852, 299), ("2330", "台積電", 100, 6000)])
        self.assertEqual(F.parse_branch_page(html), {"2303": (7852, 299), "2330": (100, 6000)})

    def test_stock_page_ids_numeric_and_hex(self):
        html = stock_html([("9268", "凱基-台北", 3587, 2487), ("0039003200300046", "某分點", 10, 20)])
        rows = F.parse_stock_page(html)
        self.assertEqual([(r["id"], r["buy"], r["sell"]) for r in rows], [("9268", 3587, 2487), ("920F", 10, 20)])

    def test_hex_round_trip(self):
        self.assertEqual(F.decode_branch_id(F.hex_id("585b")), "585b")
        self.assertEqual(F.decode_branch_id("9268"), "9268")


class TapeTests(unittest.TestCase):
    def make(self, pages):
        def http(url, timeout):
            for key, html in pages.items():
                if key in url:
                    return html
            return ""
        return F.FubonTape({"9227": "凱基城中", "585b": "統一某"}, delay=0, http=http)

    def test_stock_page_rows_and_panel_exact_lots(self):
        tape = self.make({
            "zco.djhtm?a=2303": stock_html([("9268", "凱基-台北", 6308, 20347)]),
            f"b={F.hex_id('9227')}&c=E": branch_html([("2303", "聯電", 50, 10)]),
        })
        rows = {r["securities_trader_id"]: r for r in tape.rows_for_stock("2303", "2026-10-16", 55.0)}
        self.assertEqual(rows["9268"]["buy"], 6308000.0)
        self.assertEqual(rows["9268"]["net"], (6308 - 20347) * 1000.0)
        self.assertEqual((rows["9227"]["buy"], rows["9227"]["sell"], rows["9227"]["source"]), (50000.0, 10000.0, "fubon"))

    def test_amount_page_converted_with_close_when_lots_page_misses(self):
        tape = self.make({
            "zco.djhtm?a=3105": stock_html([]),
            f"b={F.hex_id('585b')}&c=B": branch_html([("3105", "穩懋", 110_000, 0)]),      # 買進 1.1 億(仟元)
        })
        rows = tape.rows_for_stock("3105", "2026-10-16", 100.0)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["buy"], 1_100_000.0)                                # 1.1 億 ÷ 100 元 = 110 萬股
        self.assertEqual(tape.rows_for_stock("3105", "2026-10-16", None), [])               # 沒有收盤價就不亂換算

    def test_day_ready_false_when_everything_empty(self):
        tape = self.make({})
        self.assertFalse(tape.day_ready("2026-10-17"))

    def test_branch_pages_fetched_once_for_many_stocks(self):
        calls = []

        def http(url, timeout):
            calls.append(url)
            return ""
        tape = F.FubonTape({"9227": "凱基城中"}, delay=0, http=http)
        for sid in ("2303", "2330", "3105"):
            tape.rows_for_stock(sid, "2026-10-16", 50.0)
        branch_calls = [u for u in calls if "zgb0" in u]
        self.assertEqual(len(branch_calls), 2)                                              # E 與 B 各一次,不隨股票數增加


class PoolIntegrationTests(unittest.TestCase):
    def setUp(self):
        import run_expert_pool_watch as P
        from stock_db import connect
        self.P = P
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("INSERT INTO stock_daily_bars(stock_id,trade_date,open,high,low,close,volume,source,synced_at) "
                          "VALUES ('2303','2026-10-16',1,1,1,55.0,1,'twse_mi_index','x')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_close_on_accepts_official_bars(self):
        self.assertEqual(self.P.close_on(self.conn, "2303", "2026-10-16"), 55.0)
        self.assertIsNone(self.P.close_on(self.conn, "2303", "2026-10-15"))

    def test_closes_through_dedupes_same_day_across_sources(self):
        self.conn.execute("INSERT INTO stock_daily_bars(stock_id,trade_date,open,high,low,close,volume,source,synced_at) "
                          "VALUES ('2303','2026-10-16',1,1,1,55.0,1,'finmind','x')")
        self.conn.commit()
        self.assertEqual(self.P.closes_through(self.conn, "2303", "2026-10-16", n=3), [55.0])

    def test_refresh_uses_fubon_when_finmind_disabled_and_skips_days_with_finmind_rows(self):
        P = self.P
        self.conn.execute("INSERT INTO stock_broker_branch_daily(trade_date,securities_trader_id,securities_trader,stock_id,buy,sell,net,source,synced_at) "
                          "VALUES ('2026-10-15','9268','凱基-台北','2303',1,1,0,'finmind','x')")
        self.conn.commit()
        fake = mock.Mock()
        fake.day_ready.return_value = True
        fake.rows_for_stock.return_value = [{"trade_date": "2026-10-16", "securities_trader_id": "9268", "securities_trader": "凱基-台北",
                                             "stock_id": "2303", "buy": 1000.0, "sell": 0.0, "net": 1000.0, "source": "fubon"}]
        with mock.patch.dict("os.environ", {"FINMIND_ENABLED": "0"}), \
             mock.patch.object(P, "_fubon_tape", return_value=fake), \
             mock.patch("official_bars.ensure_official_bars", return_value=True):
            counts = P.refresh_stock_tape(self.conn, "2303", ["2026-10-16", "2026-10-15"])
        self.assertEqual(counts, {"2026-10-16": 1, "2026-10-15": 0})                         # 10/15 已有 FinMind 全分點,不重寫
        n = self.conn.execute("SELECT COUNT(*) FROM stock_broker_branch_daily WHERE source='fubon'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_refresh_marks_failure_when_fubon_has_no_data_yet(self):
        P = self.P
        fake = mock.Mock()
        fake.day_ready.return_value = False
        with mock.patch.dict("os.environ", {"FINMIND_ENABLED": "0"}), \
             mock.patch.object(P, "_fubon_tape", return_value=fake), \
             mock.patch("official_bars.ensure_official_bars", return_value=True):
            self.assertEqual(P.refresh_stock_tape(self.conn, "2303", ["2026-10-17"]), {"2026-10-17": -1})


class OfficialBarsTests(unittest.TestCase):
    def test_skips_download_when_bars_present_and_runs_both_scripts_otherwise(self):
        import official_bars as O
        with mock.patch.object(O, "have_official_bars", side_effect=[True]):
            runner = mock.Mock()
            self.assertTrue(O.ensure_official_bars("2026-10-16", runner=runner))
            runner.assert_not_called()
        with mock.patch.object(O, "have_official_bars", side_effect=[False, True]):
            runner = mock.Mock()
            self.assertTrue(O.ensure_official_bars("2026-10-16", runner=runner))
            self.assertEqual(runner.call_count, 2)


if __name__ == "__main__":
    unittest.main()
