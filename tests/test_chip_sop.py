import json
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "src"))

import chip_sop  # noqa: E402
import market_calendar  # noqa: E402
from biglot import chips_view  # noqa: E402

CFG = {"flip_high_threshold": 0.30, "seats": {"9268": {"flip": None}, "920M": {"flip": 0.93}, "989g": {"flip": 0.47}, "9216": {"flip": 0.15}}}


class MainNetTest(unittest.TestCase):
    def test_top_n_each_side(self):
        nets = [100, 50, 10, -30, -60, -5]
        self.assertEqual(chip_sop.main_net(nets, top_n=2), 100 + 50 - 60 - 30)

    def test_only_signed_side_counted(self):
        self.assertEqual(chip_sop.main_net([5, 3, 1], top_n=15), 9)
        self.assertEqual(chip_sop.main_net([-5, -3], top_n=15), -8)


class GateTest(unittest.TestCase):
    def _rows(self, n, buy_each):
        return {str(i): ("x", buy_each, 0.0) for i in range(n)}

    def test_complete(self):
        self.assertTrue(chip_sop.day_complete(self._rows(120, 1000), 120_000))

    def test_too_few_branches(self):
        self.assertFalse(chip_sop.day_complete(self._rows(9, 1000), 9_000))

    def test_buy_short_of_volume(self):
        self.assertFalse(chip_sop.day_complete(self._rows(120, 1000), 140_000))


class WindowTest(unittest.TestCase):
    def test_period_sums_branch_net_before_top_n(self):
        tape = {
            "d1": {"a": ("A", 100, 100), "b": ("B", 100, -100)},
            "d2": {"a": ("A", 100, -100), "b": ("B", 100, 100)},
        }
        w = chip_sop.window_stats(tape, ["d1", "d2"])
        self.assertEqual(w["main_lots"], 0)
        self.assertEqual(w["diff_cnt"], 0)


class SeatTagTest(unittest.TestCase):
    def test_levels_case_sensitive(self):
        self.assertEqual(chip_sop.seat_tag("920M", CFG)["level"], "high")
        self.assertEqual(chip_sop.seat_tag("9216", CFG)["level"], "low")
        self.assertEqual(chip_sop.seat_tag("9268", CFG)["level"], "unknown")
        self.assertIsNone(chip_sop.seat_tag("0000", CFG))
        self.assertIsNone(chip_sop.seat_tag("920m", CFG))
        self.assertIsNone(chip_sop.seat_tag("989G", CFG))

    def test_real_config_has_kgi_taipei(self):
        self.assertIn("9268", chip_sop.load_cfg()["seats"])


class RenderTest(unittest.TestCase):
    def test_group_labels_escaped_and_bad_row_rendered(self):
        rows = [{"sid": "2330", "name": "台積電", "status": "tape_incomplete", "close": 1000.0, "chg_pct": 1.0, "it": {}}]
        html = chips_view.render_table(rows)
        self.assertIn("資料不全", html)
        self.assertIn("分點最新日未完整", html)
        self.assertNotIn("< −10%", html)

    def test_flip_alert_and_next_cells(self):
        flip = {"prev_date": "2026-10-12", "k": 3, "strict": True, "loose": True,
                "items": [{"id": "9A9R", "name": "永豐金信義", "prev_lots": 500, "now_lots": -300, "sell_rank": 2}]}
        row = {"sid": "2330", "name": "台積電", "date": "2026-10-13", "flip3": flip, "close": 100.0,
               "next": {"date": "2026-10-14", "open": 101, "close": 99, "ret_pct": -1.0, "gap_pct": 1.0, "intraday_pct": -1.98}}
        alert = chips_view._flip_alert([row])
        self.assertIn("永豐金信義", alert)
        self.assertIn("賣超第2", alert)
        self.assertIn("無（", chips_view._flip_alert([{**row, "flip3": {**flip, "strict": False}}]))
        self.assertIn("-1.00%", chips_view._next(row))
        self.assertIn("待下一交易日", chips_view._next({"close": 1.0}))


def _tape(prev: dict, today: dict) -> dict:
    pad = {f"p{i}": ("x", 1.0, 0.0) for i in range(chip_sop.MIN_BRANCHES)}
    return {"d1": {**pad, **prev}, "d2": {**pad, **today}}


class BuyToSellTest(unittest.TestCase):
    def test_strict_when_prev_top_buyer_is_today_top_seller(self):
        t = _tape({"a": ("A", 0, 9000.0), "b": ("B", 0, 100.0)}, {"a": ("A", 0, -8000.0), "b": ("B", 0, 50.0)})
        f = chip_sop.buy_to_sell(t, ["d1", "d2"], "d2")
        self.assertTrue(f["strict"])
        self.assertEqual(f["items"][0]["sell_rank"], 1)

    def test_loose_only_when_not_in_top3_sellers(self):
        today = {"a": ("A", 0, -100.0)}
        today.update({f"s{i}": ("S", 0, -(1000.0 + i)) for i in range(3)})
        t = _tape({"a": ("A", 0, 9000.0)}, today)
        f = chip_sop.buy_to_sell(t, ["d1", "d2"], "d2")
        self.assertFalse(f["strict"])
        self.assertTrue(f["loose"])

    def test_no_flip_when_still_buying_or_prev_incomplete(self):
        t = _tape({"a": ("A", 0, 9000.0)}, {"a": ("A", 0, 500.0)})
        self.assertFalse(chip_sop.buy_to_sell(t, ["d1", "d2"], "d2")["strict"])
        self.assertIsNone(chip_sop.buy_to_sell({"d1": {}, "d2": {}}, ["d1", "d2"], "d2"))


class SeatConsensusTest(unittest.TestCase):
    def _day(self, **nets):
        return {k: (k, 0, v * 1000) for k, v in nets.items()}

    def test_two_small_seats_hit(self):
        c = chip_sop.seat_consensus(self._day(**{"920M": 150, "989g": 120, "9216": 50}), CFG)
        self.assertTrue(c["buy_hit"])
        self.assertFalse(c["sell_hit"])
        self.assertEqual([x["id"] for x in c["buy"]], ["920M", "989g"])

    def test_big_pair_only_is_not_consensus(self):
        cfg = {**CFG, "seats": {**CFG["seats"], "9800": {"flip": 0.3}}}
        self.assertFalse(chip_sop.seat_consensus(self._day(**{"9268": 300, "9800": 500}), cfg)["buy_hit"])
        self.assertTrue(chip_sop.seat_consensus(self._day(**{"9268": 300, "9800": 500, "920M": 100}), cfg)["buy_hit"])

    def test_opposite_sides_and_threshold(self):
        c = chip_sop.seat_consensus(self._day(**{"920M": 150, "989g": -150, "9216": 99}), CFG)
        self.assertFalse(c["buy_hit"] or c["sell_hit"])


class VoteTest(unittest.TestCase):
    def test_three_states(self):
        self.assertEqual(chip_sop.vote(5.0, 1, 2.0), "agree")
        self.assertEqual(chip_sop.vote(-5.0, 1, 2.0), "disagree")
        self.assertEqual(chip_sop.vote(0.2, 1, 2.0), "abstain")
        self.assertEqual(chip_sop.vote(-0.1, -1, 2.0), "abstain")
        self.assertEqual(chip_sop.vote(0.0, 1), "abstain")
        self.assertEqual(chip_sop.vote(None, 1, 2.0), "missing")

    def test_make_warnings_covers_bad_and_missing_it(self):
        rows = [{"sid": "1", "status": "ok"}, {"sid": "2", "status": "tape_incomplete"}, {"sid": "3", "status": "ok", "it_missing": True}]
        w = chip_sop.make_warnings(rows)
        self.assertEqual(len(w), 2)
        self.assertIn("2", w[0])
        self.assertIn("3", w[1])
        self.assertEqual(chip_sop.make_warnings([{"sid": "1", "status": "ok"}]), [])


class ViewConsistencyTest(unittest.TestCase):
    def test_cons_cell_shows_abstain_and_missing(self):
        c = {"agree": 2, "disagree": 0, "abstain": 1, "missing": 1, "known": 2, "total": 4}
        out = chips_view._cons(c)
        self.assertIn("2/2", out)
        self.assertIn("1票棄權", out)
        self.assertIn("1票缺資料", out)
        self.assertEqual(chips_view._cons(None), "—")
        self.assertTrue(chips_view._cons({"agree": 0, "disagree": 0, "abstain": 4, "missing": 0, "known": 0, "total": 4}).startswith("—"))

    def test_group_summary_has_baseline_delta(self):
        def row(ret):
            return {"next": {"date": "2026-10-13", "ret_pct": ret, "gap_pct": ret, "intraday_pct": 0.0}}
        allr = [row(-4.0), row(-6.0), row(-2.0)]
        base = chips_view._next_stats(allr)
        self.assertAlmostEqual(base["ret"], -4.0)
        self.assertIn("對全體 -2.00", chips_view._group_summary([row(-6.0)], base))
        self.assertNotIn("對全體", chips_view._group_summary(allr))
        html = chips_view.render_table([{**row(-4.0), "sid": "1", "name": "a", "status": "tape_incomplete", "close": 1.0, "it": {}}])
        self.assertIn("全體基準", html)

    def test_missing_date_messages(self):
        dates = ["2026-10-08", "2026-10-12"]
        self.assertIn("週末", chips_view._missing_msg("2026-10-10", dates))
        self.assertIn("尚未產生", chips_view._missing_msg("2026-10-20", dates))
        self.assertIn("休市日", chips_view._missing_msg("2026-10-09", dates))
        self.assertIn("格式錯誤", chips_view._missing_msg("abc", dates))
        self.assertIn("不存在", chips_view._missing_msg("2026-13-45", dates))

    def test_nav_around_missing_date_points_to_neighbours(self):
        nav = chips_view._date_nav(["2026-10-08", "2026-10-12"], "2026-10-09")
        self.assertIn("← 10-08(四)", nav)
        self.assertIn("10-12(一) →", nav)


def _mem_db(bars_2330, bars_other=(), branch_days=(), zombie=()):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE stock_daily_bars (stock_id TEXT, trade_date TEXT, open REAL, close REAL, volume REAL, source TEXT)")
    c.execute("CREATE TABLE stock_broker_branch_daily (stock_id TEXT, trade_date TEXT, securities_trader_id TEXT, securities_trader TEXT, buy REAL, net REAL, source TEXT)")
    c.execute("CREATE TABLE stock_institutional_daily (stock_id TEXT, trade_date TEXT, investment_trust_net REAL)")
    for sid, rows in (("2330", bars_2330), ("1101", bars_other)):
        for d, px in rows:
            c.execute("INSERT INTO stock_daily_bars VALUES (?,?,?,?,?,'twse_mi_index')", (sid, d, px, px, 1000.0))
    for sid in ("2330", "1101"):
        for d in zombie:
            c.execute("INSERT INTO stock_daily_bars VALUES (?,?,1,1,0,'yfinance')", (sid, d))
    for d in branch_days:
        for i in range(chip_sop.MIN_BRANCHES):
            c.execute("INSERT INTO stock_broker_branch_daily VALUES ('1101',?,?,?,?,?,'finmind')", (d, f"b{i}", "x", 10.0, 1.0 if i % 2 else -1.0))
    return c


DAYS = ["2026-10-08", "2026-10-12", "2026-10-13"]
MARKET = ["2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14"]


class CalendarSnapshotTest(unittest.TestCase):
    cfg = {**CFG, "flip_asof": "2026-10-13", "flip_reversal": 0.5, "flip_top_k": 10, "flip_min_lots": 100}

    def setUp(self):
        p = mock.patch.object(market_calendar, "MIN_OFFICIAL_ROWS", 1)
        p.start()
        self.addCleanup(p.stop)

    def _db(self, other=None, zombie=("2026-10-09",)):
        bars = [(d, 100.0 + i) for i, d in enumerate(MARKET)]
        other = [(d, 50.0 + i) for i, d in enumerate(MARKET) if d != "2026-10-14"] if other is None else other
        return _mem_db(bars, other, DAYS, zombie)

    def test_market_calendar_ignores_zombie_day_and_leaves_next_open(self):
        c = self._db()
        self.assertEqual(market_calendar.calendar_for(c, "2026-10-12"), {"asof": "2026-10-12", "prev": "2026-10-08", "next": "2026-10-13"})
        self.assertIsNone(market_calendar.calendar_for(c, "2026-10-14")["next"])

    def test_single_day_equals_multi_day(self):
        c, uni = self._db(), [("1101", "水泥")]
        multi = chip_sop.compute_rows(c, self.cfg, DAYS, uni)
        for d in DAYS:
            self.assertEqual(multi[d], chip_sop.compute_rows(c, self.cfg, [d], uni)[d])

    def test_non_trading_day_is_rejected(self):
        with self.assertRaises(ValueError):
            chip_sop.compute_rows(self._db(), self.cfg, ["2026-10-09"], [("1101", "水泥")])

    def test_gap_day_is_not_skipped_and_zombie_day_is_ignored(self):
        other = [("2026-10-08", 50.0), ("2026-10-13", 53.0)]
        c = self._db(other=other)
        c.execute("INSERT INTO stock_daily_bars VALUES ('1101','2026-10-09',1,1,0,'yfinance')")
        r = chip_sop.compute_rows(c, self.cfg, ["2026-10-13"], [("1101", "水泥")])["2026-10-13"][0]
        self.assertIsNone(r["chg_pct"])
        self.assertEqual(r["tape_days"], 1)  # 10-12 缺 K：連續完整段只剩資料日自己，不會跨過缺口接到 10-08

    def test_stock_without_bar_on_asof_is_stale_not_analysed(self):
        c = self._db(other=[("2026-10-08", 50.0), ("2026-10-12", 51.0)])
        r = chip_sop.compute_rows(c, self.cfg, ["2026-10-13"], [("1101", "水泥")])["2026-10-13"][0]
        self.assertEqual((r["status"], r["date"]), ("stale_price", "2026-10-12"))
        self.assertTrue(chip_sop.make_warnings([r]))

    def test_snapshot_carries_calendar_and_source(self):
        c = self._db()
        snap = chip_sop.make_snapshot(c, self.cfg, "2026-10-13", [], "backfill")
        self.assertEqual((snap["schema"], snap["source"]), (chip_sop.SCHEMA, "backfill"))
        self.assertEqual(snap["calendar"], {"asof": "2026-10-13", "prev": "2026-10-12", "next": "2026-10-14"})
        self.assertEqual(snap["params"]["band_pct"], chip_sop.BAND)
        self.assertEqual(snap["params"]["consensus_big"], ["9268", "9800"])  # 測試 cfg 沒有這兩個席位名稱，退回代號

    def test_fill_outcomes_only_accepts_calendar_next_day(self):
        c = self._db()
        row = {"sid": "1101", "close": 51.0, "date": "2026-10-12"}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "chip_sop_2026-10-12.json"
            path.write_text(json.dumps({"asof": "2026-10-12", "rows": [row]}), encoding="utf-8")
            self.assertEqual(chip_sop.fill_outcomes(c, path), 1)
            d = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(d["calendar"]["next"], "2026-10-13")
            self.assertEqual(d["rows"][0]["next"]["date"], "2026-10-13")
            c.execute("DELETE FROM stock_daily_bars WHERE stock_id='1101' AND trade_date='2026-10-13'")
            d["rows"][0].pop("next")
            path.write_text(json.dumps(d), encoding="utf-8")
            self.assertEqual(chip_sop.fill_outcomes(c, path), 0)

    def test_view_reads_calendar_over_row_inference(self):
        cal = {"asof": "2026-10-13", "prev": "2026-10-12", "next": "2026-10-14"}
        rows = [{"sid": "1", "name": "a", "date": "2026-10-13", "status": "tape_incomplete", "close": 1.0, "it": {}, "flip3": {"prev_date": "2026-10-08"}}]
        self.assertIn("10-12(一)", chips_view.render_table(rows, "2026-10-13", cal))
        self.assertIn("10-08(四)", chips_view.render_table(rows, "2026-10-13"))
        self.assertNotIn("10-14", chips_view.render_table(rows, "2026-10-13", cal))


def _top_rows(pos, neg, base=1000.0):
    """前段分點日:pos 個買超、neg 個賣超(淨額由大到小遞減),不含其他分點。"""
    rows = {f"p{i}": (f"買{i}", 0.0, base * (pos - i)) for i in range(pos)}
    rows.update({f"n{i}": (f"賣{i}", 0.0, -base * (neg - i)) for i in range(neg)})
    return rows


class TopModeTest(unittest.TestCase):
    """2026-10-17 起 FinMind 停用:富邦前段分點('top')口徑。每日主力 = 買超前 N + 賣超前 N,與全分點版相同。"""

    def test_day_mode_gates(self):
        self.assertEqual(chip_sop.day_mode(_top_rows(15, 15), 1e6, is_full=False), "top")
        self.assertIsNone(chip_sop.day_mode(_top_rows(9, 15), 1e6, is_full=False))           # 買超分點太少 = 頁面沒抓全
        self.assertIsNone(chip_sop.day_mode(_top_rows(15, 3), 1e6, is_full=False))
        rows = {f"b{i}": ("x", 10.0, 1.0) for i in range(120)}
        self.assertEqual(chip_sop.day_mode(rows, 1200.0, is_full=True), "full")              # 全分點閘門維持原樣
        self.assertIsNone(chip_sop.day_mode(rows, 1_000_000.0, is_full=True))

    def test_window_stats_default_unchanged_without_modes(self):
        tape = {"d1": {"a": ("x", 10.0, 5.0), "b": ("y", 10.0, -3.0)}}
        w = chip_sop.window_stats(tape, ["d1"])
        self.assertEqual((w["main_lots"], w["conc_pct"], w["diff_cnt"]), (0, 10.0, 0))

    def test_top_day_uses_volume_denominator_and_has_no_count_diff(self):
        tape = {"d1": _top_rows(15, 15)}
        w = chip_sop.window_stats(tape, ["d1"], {"d1": "top"}, {"d1": 1_000_000.0})
        main = sum(1000.0 * (15 - i) for i in range(15)) - sum(1000.0 * (15 - i) for i in range(15))
        self.assertEqual(w["main_lots"], round(main / 1000))
        self.assertIsNone(w["diff_cnt"])
        tape = {"d1": {"a": ("x", 0.0, 50_000.0), "b": ("y", 0.0, -20_000.0)}}
        w = chip_sop.window_stats(tape, ["d1"], {"d1": "top"}, {"d1": 1_000_000.0})
        self.assertEqual(w["main_lots"], 30)
        self.assertEqual(w["conc_pct"], 3.0)                                                 # 30,000 股 ÷ 100 萬股

    def test_mixed_window_is_sum_of_daily_main_not_netting(self):
        """全分點日 + top 日混合:逐日主力加總(同一分點兩天反向,不可被跨日淨額抵銷)。"""
        tape = {"d1": {"a": ("x", 100.0, 40_000.0), "b": ("y", 100.0, -10_000.0)},
                "d2": {"a": ("x", 0.0, -40_000.0), "c": ("z", 0.0, 10_000.0)}}
        w = chip_sop.window_stats(tape, ["d1", "d2"], {"d1": "full", "d2": "top"}, {"d1": 500_000.0, "d2": 500_000.0})
        self.assertEqual(w["main_lots"], (40_000 - 10_000 - 40_000 + 10_000) // 1000)
        d = chip_sop.window_stats(tape, ["d1", "d2"], {"d1": "top", "d2": "top"}, {"d1": 500_000.0, "d2": 500_000.0})
        self.assertEqual(d["main_lots"], 0)

    def test_analyse_marks_top_mode_and_reports_ok(self):
        cal = ["2026-10-14", "2026-10-15", "2026-10-16"]
        bars = {d: (2_000_000.0, 100.0 + i) for i, d in enumerate(cal)}
        tape = {d: _top_rows(15, 15) for d in cal}
        tape["2026-10-16"]["p0"] = ("買0", 0.0, 900_000.0)                                    # 當日第一大買超
        r = chip_sop.analyse("1101", "水泥", {"bars": bars, "tape": tape, "it": {}, "full": set()}, "2026-10-16", CFG, cal)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["tape_mode"], "top")
        self.assertIsNone(r["day"]["diff_cnt"])
        self.assertEqual(r["buyers"][0]["name"], "買0")
        self.assertEqual(r["buyers"][0]["lots"], 900)

    def test_analyse_full_days_still_use_old_math(self):
        cal = ["2026-10-14", "2026-10-15"]
        rows = {f"b{i}": ("x", 10.0, 1.0 if i % 2 else -1.0) for i in range(chip_sop.MIN_BRANCHES)}
        bars = {d: (rows_vol, 10.0) for d, rows_vol in zip(cal, (1000.0, 1000.0))}
        r = chip_sop.analyse("1101", "水泥", {"bars": bars, "tape": {d: rows for d in cal}, "it": {}, "full": set(cal)}, "2026-10-15", CFG, cal)
        self.assertEqual(r["tape_mode"], "full")
        self.assertIsNotNone(r["day"]["diff_cnt"])


class ParamsDrivenViewTest(unittest.TestCase):
    P = {**{k: v for k, v in chip_sop.PARAMS.items()}, "flip_high_threshold": 0.33, "flip_reversal": 0.5, "flip_top_k": 10, "flip_min_lots": 100,
         "consensus_big": ["凱基台北", "元大"]}

    def _page(self, p):
        note = chips_view._fill(chips_view.NOTE, p)
        labels = " ".join(label for _, label in chips_view._groups(p))
        return note + labels + chips_view._consensus_alert([], p, "2026-10-13")

    def test_every_threshold_comes_from_params(self):
        base = self._page(self.P)
        self.assertNotIn("?", base.replace("x.xx", ""))
        changed = self._page({**self.P, "band_pct": 12.0, "complete_ratio": 0.95, "min_branches": 80, "it_act_pct": 3.0, "period_dead_pct": 4.0,
                              "top_n": 20, "flip_k": 5, "consensus_lots": 150, "consensus_min_seats": 3, "dominant_share": 0.6, "thin_lots": 5000})
        for must in ("+12%", "&lt; −12%", "≥ 95%", "≥80 分點", "≥ 3%", "&lt; 4%", "前20買超", "前5名", "≥150 張", "≥3 個", "≥ 60%", "&lt; 5000"):
            self.assertIn(must, changed, must)
        for stale in ("+10%", "≥ 97%", "前15", "≥100 分點", "≥ 2%"):
            self.assertNotIn(stale, changed, stale)

    def test_consensus_alert_names_the_date(self):
        self.assertIn("10-13", chips_view._consensus_alert([], self.P, "2026-10-13"))
        self.assertNotIn("今日", chips_view._consensus_alert([], self.P, "2026-10-13"))

    def test_missing_params_show_question_mark_not_a_default(self):
        self.assertIn("?", chips_view._fill(chips_view.NOTE, {}))
        self.assertIn("+?%", chips_view._groups({})[0][1])

    def test_snapshot_params_cover_every_placeholder(self):
        import re
        need = set(re.findall(r"\{([a-z_]+)\}", chips_view.NOTE)) - {"d0", "prev", "next", "asof", "complete_pct", "reversal_pct", "dominant_pct", "foreign_pct"}
        self.assertLessEqual(need, set(self.P), need - set(self.P))


if __name__ == "__main__":
    unittest.main()
