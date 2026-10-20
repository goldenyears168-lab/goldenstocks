"""biglot.fut_basis：現貨×個股期貨基差（中價）資料層的邊界釘樁。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
from biglot import fut_basis as fb  # noqa: E402


def book_line(ts, bid, ask, sym="KIFJ6", stale=False, session="day"):
    return json.dumps({"ts": ts, "event": "data", "root": "KI", "symbol": sym, "bids": [{"price": bid, "size": 1}],
                       "asks": [{"price": ask, "size": 1}], "quote_type": "FUTURE", "session": session, "stale": stale})


class SymbolAndCalendarTest(unittest.TestCase):
    def test_parse_symbol_standard_vs_adjusted(self):
        self.assertEqual(fb.parse_symbol("KIFJ6"), {"standard": True, "month": 10, "year_digit": 6, "root": "KI"})
        self.assertFalse(fb.parse_symbol("PK1J6")["standard"])        # 減資等調整後合約
        self.assertEqual(fb.parse_symbol("IXFI6")["month"], 9)
        self.assertIsNone(fb.parse_symbol("XX"))

    def test_third_wednesday_and_settlement_day(self):
        self.assertEqual(fb.third_wednesday(2026, 10).isoformat(), "2026-10-21")
        self.assertEqual(fb.third_wednesday(2026, 9).isoformat(), "2026-09-16")
        self.assertTrue(fb.is_settlement_day("KIFJ6", "2026-10-21"))
        self.assertTrue(fb.is_settlement_day("IXFI6", "2026-09-16"))
        self.assertFalse(fb.is_settlement_day("KIFJ6", "2026-10-20"))

    def test_mid_and_basis(self):
        self.assertEqual(fb.mid(100, 101), 100.5)
        self.assertEqual(fb.mid(0, 101, 100.2), 100.2)               # 缺買一退回成交價
        self.assertIsNone(fb.mid(None, None))
        self.assertAlmostEqual(fb.basis_pct(100.5, 100.0), 0.5)
        self.assertIsNone(fb.basis_pct(None, 100.0))


class SeriesTest(unittest.TestCase):
    def _series(self, lines, sid="8039", day="2026-10-20"):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ki_books"
            p.mkdir()
            (p / f"ki_books_{day}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            row = {"root": "KI", "futures_id": "KIF", "stock_id": sid, "contract_size": "2,000"}
            fb._DAYS.clear(); fb._DIRS.clear(); fb._EXDIV.clear()
            with mock.patch.object(fb, "CACHE", Path(td)), mock.patch.object(fb, "_load_map", return_value={sid: [row]}), \
                    mock.patch.object(fb, "exdiv_info", return_value=None):
                return fb.series(sid, day)

    def test_filters_stale_night_crossed_and_outside_hours(self):
        s = self._series([
            book_line("2026-10-20T09:00:10.000+08:00", 300, 301),
            book_line("2026-10-20T09:01:10.000+08:00", 400, 401, stale=True),          # stale 不算
            book_line("2026-10-20T09:02:10.000+08:00", 400, 401, session="night"),     # 夜盤不算
            book_line("2026-10-20T09:03:10.000+08:00", 305, 304),                      # 交叉簿不算
            book_line("2026-10-20T13:26:00.000+08:00", 310, 311),                      # 13:25 後不算
        ])
        self.assertEqual(s["fmid"]["09:00"], 300.5)
        self.assertEqual(s["fmid"]["09:01"], 300.5)                                    # 沿用前一分鐘（≤3 分鐘）
        self.assertNotIn("13:26", s["fmid"])
        self.assertEqual(s["label"], "KIFJ6（10月契約）")
        self.assertTrue(s["standard"])
        self.assertFalse(s["settle"])

    def test_carry_limit_and_settlement_flag(self):
        s = self._series([book_line("2026-10-21T09:00:10.000+08:00", 300, 301)], day="2026-10-21")
        self.assertIn("09:03", s["fmid"])
        self.assertNotIn("09:04", s["fmid"])                                           # 超過 3 分鐘不沿用
        self.assertTrue(s["settle"])                                                   # 10/21 是 10 月合約結算日
        self.assertIn("結算日", fb.header_html(s, 0.1))

    def test_nonstandard_contract_warning(self):
        s = self._series([book_line("2026-10-20T09:00:10.000+08:00", 300, 301, sym="PK1J6")])
        self.assertFalse(s["standard"])
        self.assertIn("非標準", fb.header_html(s, -1.0))


class RenderTest(unittest.TestCase):
    def test_svg_detail_has_futures_line_and_basis_panel(self):
        from biglot import detail_charts as dc
        mins = {f"09:{m:02d}": {"px": 100.0 + m * 0.01, "vol": 10.0, "big": 0.0, "ret": 0.0, "tot": 1.0, "bid": 99.9 + m * 0.01, "ask": 100.1 + m * 0.01}
                for m in range(30)}
        info = {"fmid": {k: v["px"] + 0.2 for k, v in mins.items()}, "settle": True, "exdiv": {"prev_close": 100, "ref_price": 99, "kind": "息"},
                "label": "KIFJ6", "standard": True, "sym": "KIFJ6"}
        with mock.patch.object(dc._fb, "series", return_value=info):
            svg = dc._svg_detail("8039", "2026-10-20", {"mins": mins, "px0": 100.0}, 100.0)
        self.assertIn("stroke='#2dd4bf'", svg)                                         # 期貨中價虛線
        self.assertIn("基差%=期貨中價÷現貨中價−1", svg)
        self.assertIn("結算日", svg)
        self.assertIn("除息日", svg)
        with mock.patch.object(dc._fb, "series", return_value=None):
            svg2 = dc._svg_detail("5351", "2026-10-20", {"mins": mins, "px0": 100.0}, 100.0)
        self.assertNotIn("stroke='#2dd4bf'", svg2)
        self.assertNotIn("基差%", svg2)


if __name__ == "__main__":
    unittest.main()
