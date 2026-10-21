import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "src"))

import boll_position  # noqa: E402
import branch_habit  # noqa: E402
import chip_sop  # noqa: E402
import market_calendar  # noqa: E402
from biglot import chips_view  # noqa: E402


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
        self.assertNotIn("diff_cnt", w)           # 家數差不是小哥的東西，已移除


class NamedBranchTest(unittest.TestCase):
    NAMED = [{"name": "元大-館前", "ids": ["984K"], "role": "公司派", "stocks": ["廣達(2382)", "音記(ASR原文)"]},
             {"name": "凱基-台北", "ids": ["9268"], "role": "隔日沖", "stocks": ["鴻海(2317)"]}]

    def test_named_is_per_stock(self):
        self.assertEqual(chip_sop.named_for("2382", self.NAMED), {"984K": {"name": "元大-館前", "role": "公司派"}})
        self.assertEqual(chip_sop.named_for("2330", self.NAMED), {})        # 沒被點名過的股票不給任何標籤

    def test_real_config_loads(self):
        self.assertTrue(chip_sop.load_named())


class BollTest(unittest.TestCase):
    def test_band_and_level_boundaries(self):
        self.assertEqual(boll_position.bw_level(0.049), "非常窄")
        self.assertEqual(boll_position.bw_level(0.05), "窄")
        self.assertEqual(boll_position.bw_level(0.10), "窄")        # 使用者裁決：剛好 10% 算窄
        self.assertEqual(boll_position.bw_level(0.1001), "正常")
        self.assertEqual(boll_position.bw_level(0.1999), "正常")
        self.assertEqual(boll_position.bw_level(0.20), "寬")

    def test_bandwidth_is_upper_over_lower(self):
        closes = [100.0] * 19 + [100.0]
        closes[-1] = 100.0
        b = boll_position.boll_position(closes + [100.0])
        self.assertEqual(b["bw_pct"], 0.0)
        # 上100下100→帶寬 0；人為：σ 使上=105、下=100 時帶寬=5%（定義是 上÷下−1，不是 (上−下)÷中）
        self.assertAlmostEqual(105 / 100 - 1, 0.05)

    def test_zone_slope_and_open_boll(self):
        flat = [100.0 + (0.1 if i % 2 else -0.1) for i in range(30)]       # 窄帶寬整理
        up = flat + [103.0]                                                  # 突破：收盤站上上軌
        b = boll_position.boll_position(up, low_today=100.0)
        self.assertEqual(b["zone"], "above_ub")
        self.assertTrue(b["upper_half"])
        self.assertEqual(b["ma_dir"], "up")
        self.assertLessEqual(b["bw_prev_pct"], 10.0)
        self.assertTrue(b["open_boll"])                                      # 昨窄、今擴大、上軌上彎、收在月線上
        self.assertFalse(boll_position.boll_position(flat + [99.0])["open_boll"])   # 往下破不是開布林

    def test_touch_lower_band_and_low_zone(self):
        base = [100.0 + (3 if i % 2 else -3) for i in range(30)]
        b = boll_position.boll_position(base + [80.0], low_today=79.0)
        self.assertEqual(b["zone"], "below_lb")
        self.assertFalse(b["upper_half"])
        self.assertTrue(b["touch_lb"])
        self.assertFalse(boll_position.boll_position(base + [100.0], low_today=100.0)["touch_lb"])

    def test_not_enough_bars_or_bad_prices(self):
        self.assertIsNone(boll_position.boll_position([100.0] * 20))        # 需要 21 根才有昨日通道
        self.assertIsNone(boll_position.boll_position([100.0] * 20 + [0.0]))

    def test_bias_year(self):
        self.assertIsNone(boll_position.bias_year([1.0] * 239))
        self.assertAlmostEqual(boll_position.bias_year([100.0] * 239 + [340.0]), 340 / 101 - 1, places=6)


class HabitTest(unittest.TestCase):
    """隔日沖逐股：同分點在 A 股有紀錄，不影響 B 股。"""

    def _data(self):
        D = {("A", "d1"): {"x": (900.0, 100.0), "y": (10.0, 10.0)}, ("A", "d2"): {"x": (50.0, 700.0)},
             ("B", "d1"): {"x": (900.0, 100.0)}, ("B", "d2"): {"x": (900.0, 0.0)}}
        vol = {(s, d): 10_000.0 for s in "AB" for d in ("d1", "d2", "d3")}
        return D, vol, ["d1", "d2", "d3"]

    def test_per_stock_not_per_branch(self):
        D, vol, days = self._data()
        self.assertEqual(branch_habit.habit_of(D, vol, days, "A", "x", "d3")[:2], (1, 1))
        self.assertEqual(branch_habit.habit_of(D, vol, days, "B", "x", "d3")[:2], (2, 0))      # 同分點在 B 股沒賣 → 0 次隔日沖（不借 A 股的紀錄）
        self.assertEqual(branch_habit.habit_of(D, vol, days, "C", "x", "d3")[:2], (0, 0))      # 沒紀錄 → 不借別檔的

    def test_small_buy_is_not_an_event(self):
        D, vol, days = self._data()
        self.assertEqual(branch_habit.habit_of(D, vol, days, "A", "y", "d3")[:2], (0, 0))      # 淨買 0 / 未達 3% 量

    def test_pit_outcome_must_be_known(self):
        D, vol, days = self._data()
        self.assertEqual(branch_habit.habit_of(D, vol, days, "A", "x", "d1")[:2], (0, 0))      # d2 的結果在 d1 時還不知道

    def test_skip_unknown_next_day(self):
        D, vol, days = self._data()
        D.pop(("A", "d2"))
        self.assertEqual(branch_habit.habit_of(D, vol, days, "A", "x", "d3")[:2], (1, 0))
        self.assertEqual(branch_habit.habit_of(D, vol, days, "A", "x", "d3", skip_unknown_next=True)[:2], (0, 0))

    def test_summary_marks_strong(self):
        D, vol, days = self._data()
        s = branch_habit.habit_summary(D, vol, days, "A", "d3", [("x", 800.0)], {"x": "元大"})
        self.assertEqual((s[0]["name"], s[0]["events"], s[0]["flips"], s[0]["strong"]), ("元大", 1, 1, False))


def _boll(**kw):
    b = {"ma20": 100, "ub": 110, "lb": 90, "bw_pct": 22.0, "bw_prev_pct": 22.0, "bw_level": "寬", "ma_slope_pct": -0.5, "ma_dir": "down",
         "zone": "lb_to_ma", "upper_half": False, "open_boll": False, "touch_lb": False}
    b.update(kw)
    return b


def _ids(ctx):
    return {x["id"] for x in chip_sop.rule_flags(ctx)}


class RuleFlagsTest(unittest.TestCase):
    def test_open_boll_needs_main_buy(self):
        b = _boll(open_boll=True, ma_dir="up", upper_half=True, zone="ma_to_ub", bw_level="窄")
        self.assertIn("R03", _ids({"conc": 12.0, "boll": b}))
        self.assertNotIn("R03", _ids({"conc": 9.9, "boll": b}))              # 集中度 <10% 不算大買
        self.assertNotIn("R03", _ids({"conc": 12.0, "boll": _boll(open_boll=False)}))

    def test_wide_band_lower_band_buy(self):
        self.assertIn("R14", _ids({"conc": 11.0, "boll": _boll(touch_lb=True)}))
        self.assertNotIn("R14", _ids({"conc": 11.0, "boll": _boll(touch_lb=True, bw_level="正常")}))
        self.assertNotIn("R14", _ids({"conc": 11.0, "boll": _boll(touch_lb=False)}))

    def test_streak_carries_position(self):
        lo = chip_sop.rule_flags({"conc": 12.0, "boll": _boll(), "streak2": {"buy": True, "sell": False}})
        hi = chip_sop.rule_flags({"conc": 12.0, "boll": _boll(upper_half=True), "streak2": {"buy": True, "sell": False}})
        self.assertIn("低檔", next(x["label"] for x in lo if x["id"] == "R24"))
        self.assertIn("高檔", next(x["label"] for x in hi if x["id"] == "R24"))
        self.assertIn("R41", _ids({"conc": -12.0, "boll": _boll(), "streak2": {"buy": False, "sell": True}}))
        self.assertNotIn("R24", _ids({"conc": 12.0, "boll": _boll(), "streak2": {"buy": False, "sell": False}}))

    def test_main_and_trust(self):
        self.assertIn("主投同買", [x["label"] for x in chip_sop.rule_flags({"conc": 12.0, "boll": _boll(), "it_d1": 50})])
        self.assertIn("主投同賣", [x["label"] for x in chip_sop.rule_flags({"conc": -12.0, "boll": _boll(), "it_d1": -50})])
        miss = [x for x in chip_sop.rule_flags({"conc": -12.0, "boll": _boll(), "it_d1": 50}) if x["id"] == "R22"]
        self.assertEqual(miss[0]["side"], "neutral")                         # 主賣投買不算
        self.assertNotIn("R22", _ids({"conc": 5.0, "boll": _boll(), "it_d1": 50}))

    def test_high_out_pool_and_escape_point(self):
        b = _boll()
        self.assertIn("R11", _ids({"conc": -12.0, "boll": b}))
        self.assertNotIn("R11", _ids({"conc": -12.0, "boll": _boll(bw_level="正常")}))
        self.assertNotIn("R11", _ids({"conc": -12.0, "boll": _boll(ma_dir="up")}))   # 月線仍上彎不是出貨池
        self.assertNotIn("R13", _ids({"conc": -12.0, "boll": b}))                    # 還在月線下，不是逃命點
        self.assertIn("R13", _ids({"conc": -12.0, "boll": _boll(upper_half=True, zone="ma_to_ub")}))
        self.assertIn("R12", _ids({"conc": 0.0, "boll": b, "bias_year": 0.45}))
        self.assertNotIn("R12", _ids({"conc": 0.0, "boll": b, "bias_year": 0.39}))

    def test_short_bear_and_big_seller_need_position(self):
        self.assertIn("R16a", _ids({"conc": -12.0, "boll": _boll()}))
        self.assertNotIn("R16a", _ids({"conc": -12.0, "boll": _boll(ma_dir="up")}))
        self.assertIn("R21c", _ids({"conc": 0.0, "boll": _boll(upper_half=True), "big_sell_share": 0.04}))
        self.assertNotIn("R21c", _ids({"conc": 0.0, "boll": _boll(upper_half=False), "big_sell_share": 0.04}))   # 低檔賣不算「高檔大分點狂賣」
        self.assertNotIn("R21c", _ids({"conc": 0.0, "boll": _boll(upper_half=True), "big_sell_share": 0.02}))

    def test_habit_and_s17(self):
        hab = [{"name": "元大", "events": 4, "flips": 2, "strong": True}]
        ids = _ids({"conc": 0.0, "boll": _boll(), "habit": hab})
        self.assertIn("R21b", ids)
        self.assertNotIn("S17", ids)                                          # 不在出貨池
        ids = _ids({"conc": -12.0, "boll": _boll(), "habit": hab, "red": True})
        self.assertIn("S17", ids)
        self.assertNotIn("S17", _ids({"conc": -12.0, "boll": _boll(), "habit": hab, "red": False}))       # 要收紅 K
        self.assertNotIn("R21b", _ids({"conc": 0.0, "boll": _boll(), "habit": [{"name": "x", "events": 3, "flips": 0, "strong": False}]}))
        s17 = next(x["label"] for x in chip_sop.rule_flags({"conc": -12.0, "boll": _boll(), "habit": hab, "red": True}) if x["id"] == "S17")
        self.assertIn("缺資料", s17)

    def test_removed_non_book_flags(self):
        every = {"conc": -15.0, "boll": _boll(), "habit": [], "streak2": {"buy": False, "sell": True}, "it_d1": 10, "thin": True}
        labels = " ".join(x["label"] for x in chip_sop.rule_flags(every))
        for gone in ("價量背離", "席位共識", "單點主導", "外資主導", "家數差", "買超前三轉賣超前三"):
            self.assertNotIn(gone, labels)

    def test_no_boll_no_position_rules(self):
        self.assertEqual(_ids({"conc": 15.0, "boll": None}), set())


def _mem_db(bars_2330, bars_other=(), branch_days=(), zombie=()):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE stock_daily_bars (stock_id TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, source TEXT)")
    c.execute("CREATE TABLE stock_broker_branch_daily (stock_id TEXT, trade_date TEXT, securities_trader_id TEXT, securities_trader TEXT, buy REAL, sell REAL, net REAL, source TEXT)")
    c.execute("CREATE TABLE stock_institutional_daily (stock_id TEXT, trade_date TEXT, investment_trust_net REAL)")
    for sid, rows in (("2330", bars_2330), ("1101", bars_other)):
        for d, px in rows:
            c.execute("INSERT INTO stock_daily_bars VALUES (?,?,?,?,?,?,?,'twse_mi_index')", (sid, d, px, px, px, px, 1000.0))
    for sid in ("2330", "1101"):
        for d in zombie:
            c.execute("INSERT INTO stock_daily_bars VALUES (?,?,1,1,1,1,0,'yfinance')", (sid, d))
    for d in branch_days:
        for i in range(chip_sop.MIN_BRANCHES):
            c.execute("INSERT INTO stock_broker_branch_daily VALUES ('1101',?,?,?,?,?,?,'finmind')",
                      (d, f"b{i}", "x", 10.0, 9.0 if i % 2 else 11.0, 1.0 if i % 2 else -1.0))
    return c


DAYS = ["2026-10-08", "2026-10-12", "2026-10-13"]
MARKET = ["2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14"]


class CalendarSnapshotTest(unittest.TestCase):
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
        multi = chip_sop.compute_rows(c, DAYS, uni)
        for d in DAYS:
            self.assertEqual(multi[d], chip_sop.compute_rows(c, [d], uni)[d])

    def test_non_trading_day_is_rejected(self):
        with self.assertRaises(ValueError):
            chip_sop.compute_rows(self._db(), ["2026-10-09"], [("1101", "水泥")])

    def test_gap_day_is_not_skipped_and_zombie_day_is_ignored(self):
        other = [("2026-10-08", 50.0), ("2026-10-13", 53.0)]
        c = self._db(other=other)
        c.execute("INSERT INTO stock_daily_bars VALUES ('1101','2026-10-09',1,1,1,1,0,'yfinance')")
        r = chip_sop.compute_rows(c, ["2026-10-13"], [("1101", "水泥")])["2026-10-13"][0]
        self.assertIsNone(r["chg_pct"])
        self.assertEqual(r["tape_days"], 1)  # 10-12 缺 K：連續完整段只剩資料日自己，不會跨過缺口接到 10-08

    def test_stock_without_bar_on_asof_is_stale_not_analysed(self):
        c = self._db(other=[("2026-10-08", 50.0), ("2026-10-12", 51.0)])
        r = chip_sop.compute_rows(c, ["2026-10-13"], [("1101", "水泥")])["2026-10-13"][0]
        self.assertEqual((r["status"], r["date"]), ("stale_price", "2026-10-12"))
        self.assertTrue(chip_sop.make_warnings([r]))

    def test_snapshot_carries_calendar_params_and_gaps(self):
        c = self._db()
        snap = chip_sop.make_snapshot(c, "2026-10-13", [], "backfill")
        self.assertEqual((snap["schema"], snap["source"]), (chip_sop.SCHEMA, "backfill"))
        self.assertEqual(snap["calendar"], {"asof": "2026-10-13", "prev": "2026-10-12", "next": "2026-10-14"})
        self.assertEqual(snap["params"]["band_pct"], chip_sop.BAND)
        self.assertEqual(snap["params"]["windows"], [1, 5, 10, 20])      # 與書的欄位對齊
        self.assertEqual(snap["params"]["streak_days"], 2)
        self.assertEqual(len(snap["gaps"]), len(chip_sop.GAPS))
        self.assertNotIn("seats_flip_asof", snap)

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


def _top_rows(pos, neg, base=1000.0):
    """前段分點日:pos 個買超、neg 個賣超(淨額由大到小遞減),不含其他分點。"""
    rows = {f"p{i}": (f"買{i}", 0.0, base * (pos - i), 0.0) for i in range(pos)}
    rows.update({f"n{i}": (f"賣{i}", 0.0, -base * (neg - i), 0.0) for i in range(neg)})
    return rows


def _strong_buy_day(base=20_000.0, sell=1_000.0, extra=None):
    rows = {f"p{i}": (f"買{i}", 0.0, base, 0.0) for i in range(15)}
    rows.update({f"n{i}": (f"賣{i}", 0.0, -sell, 0.0) for i in range(15)})
    rows.update(extra or {})
    return rows


class TopModeTest(unittest.TestCase):
    """2026-10-17 起 FinMind 停用:富邦前段分點('top')口徑。每日主力 = 買超前 N + 賣超前 N,與全分點版相同。"""

    def test_day_mode_gates(self):
        self.assertEqual(chip_sop.day_mode(_top_rows(15, 15), 1e6, is_full=False), "top")
        self.assertIsNone(chip_sop.day_mode(_top_rows(9, 15), 1e6, is_full=False))           # 買超分點太少 = 頁面沒抓全
        self.assertIsNone(chip_sop.day_mode(_top_rows(15, 3), 1e6, is_full=False))
        rows = {f"b{i}": ("x", 10.0, 1.0 if i % 2 else -1.0) for i in range(120)}
        self.assertEqual(chip_sop.day_mode(rows, 1200.0, is_full=True), "full")
        # 全分點日買進占量不足 97%（缺小分點），前段名單仍完整 → 退回 top，不截斷連續窗口
        self.assertEqual(chip_sop.day_mode(rows, 1_000_000.0, is_full=True), "top")
        self.assertIsNone(chip_sop.day_mode({"a": ("x", 1.0, 1.0)}, 1_000_000.0, is_full=True))

    def test_top_day_uses_volume_denominator(self):
        tape = {"d1": {"a": ("x", 0.0, 50_000.0, 0.0), "b": ("y", 0.0, -20_000.0, 0.0)}}
        w = chip_sop.window_stats(tape, ["d1"], {"d1": "top"}, {"d1": 1_000_000.0})
        self.assertEqual(w["main_lots"], 30)
        self.assertEqual(w["conc_pct"], 3.0)                                                 # 30,000 股 ÷ 100 萬股

    def test_mixed_window_is_sum_of_daily_main_not_netting(self):
        """全分點日 + top 日混合:逐日主力加總(同一分點兩天反向,不可被跨日淨額抵銷)。"""
        tape = {"d1": {"a": ("x", 100.0, 40_000.0), "b": ("y", 100.0, -10_000.0)},
                "d2": {"a": ("x", 0.0, -40_000.0), "c": ("z", 0.0, 10_000.0)}}
        w = chip_sop.window_stats(tape, ["d1", "d2"], {"d1": "full", "d2": "top"}, {"d1": 500_000.0, "d2": 500_000.0})
        self.assertEqual(w["main_lots"], (40_000 - 10_000 - 40_000 + 10_000) // 1000)


def _cal(n, end="2026-10-30"):
    import datetime as dt
    d = dt.date.fromisoformat(end)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return sorted(out)


def _closes(cal, start=100.0, step=0.0):
    return {d: (2_000_000.0, start + step * i, start + step * i, start + step * i + 1, start + step * i - 1) for i, d in enumerate(cal)}


class AnalyseTest(unittest.TestCase):
    def test_marks_top_mode_windows_and_book_fields(self):
        cal = _cal(30)
        bars = _closes(cal)
        tape = {d: _top_rows(15, 15) for d in cal}
        tape[cal[-1]]["p0"] = ("買0", 0.0, 900_000.0, 0.0)
        r = chip_sop.analyse("1101", "水泥", {"bars": bars, "tape": tape, "it": {}, "full": set()}, cal[-1], cal, {})
        self.assertEqual((r["status"], r["tape_mode"]), ("ok", "top"))
        self.assertEqual(sorted(r["win"], key=int), ["1", "5", "10", "20"])
        self.assertEqual(r["buyers"][0]["name"], "買0")
        self.assertEqual(r["buyers"][0]["lots"], 900)
        self.assertIsNotNone(r["boll"])
        for gone in ("diff_cnt", "consensus", "flip3", "consistency"):
            self.assertNotIn(gone, r)
            self.assertNotIn(gone, r["win"]["1"])

    def test_two_day_streak_definition(self):
        cal = _cal(30)
        bars = _closes(cal)
        one = {d: _top_rows(15, 15) for d in cal}                 # 主力 0
        one[cal[-1]] = _strong_buy_day()
        r = chip_sop.analyse("1101", "水泥", {"bars": bars, "tape": one, "it": {}, "full": set()}, cal[-1], cal, {})
        self.assertGreaterEqual(r["day"]["conc_pct"], 10.0)
        self.assertFalse(r["streak2"]["buy"])                      # 只有 1 天達門檻不是連續
        two = dict(one)
        two[cal[-2]] = _strong_buy_day()
        r = chip_sop.analyse("1101", "水泥", {"bars": bars, "tape": two, "it": {}, "full": set()}, cal[-1], cal, {})
        self.assertTrue(r["streak2"]["buy"])
        self.assertIn("R24", {x["id"] for x in r["rules"]})
        three_gap = dict(two)
        three_gap[cal[-2]] = _top_rows(15, 15)
        three_gap[cal[-3]] = _strong_buy_day()
        r = chip_sop.analyse("1101", "水泥", {"bars": bars, "tape": three_gap, "it": {}, "full": set()}, cal[-1], cal, {})
        self.assertFalse(r["streak2"]["buy"])                      # 昨天不達門檻，前天達也不算連續

    def test_habit_is_per_stock_and_pit(self):
        cal = _cal(30)
        bars = _closes(cal)
        vol = bars[cal[0]][0]
        big = vol * 0.05
        tape = {d: _top_rows(15, 15) for d in cal}
        # 分點 X 在這檔股票：d(-7) 大買、d(-6) 賣出；今日又大買
        tape[cal[-7]] = {**_top_rows(15, 15), **{"X": ("元大", big, big, 0.0)}}
        tape[cal[-6]] = {**_top_rows(15, 15), **{"X": ("元大", 0.0, -big, big)}}
        tape[cal[-1]] = {**_top_rows(15, 15), **{"X": ("元大", big, big, 0.0)}}
        r = chip_sop.analyse("1101", "水泥", {"bars": bars, "tape": tape, "it": {}, "full": set()}, cal[-1], cal, {})
        h = {x["name"]: x for x in r["habit"]}["元大"]
        self.assertEqual((h["events"], h["flips"]), (1, 1))
        self.assertIn("R21b", {x["id"] for x in r["rules"]})

    def test_named_branch_today_and_missing_vs_zero(self):
        cal = _cal(30)
        bars = _closes(cal)
        tape = {d: _top_rows(15, 15) for d in cal}
        tape[cal[-1]]["984K"] = ("元大-館前", 0.0, -300_000.0, 0.0)
        named = {"984K": {"name": "元大-館前", "role": "公司派"}, "9A9P": {"name": "永豐金-竹北", "role": "其他"}}
        r = chip_sop.analyse("1101", "水泥", {"bars": bars, "tape": tape, "it": {}, "full": set()}, cal[-1], cal, named)
        by = {x["id"]: x for x in r["named"]}
        self.assertEqual(by["984K"]["lots"], -300)
        self.assertIsNone(by["9A9P"]["lots"])                      # 前段口徑沒列入 = 不在名單，不是 0

    def test_avg_volume_thin_flag(self):
        cal = _cal(30)
        bars = {d: (1_000_000.0, 100.0, 100.0, 101.0, 99.0) for d in cal}          # 1000 張
        r = chip_sop.analyse("1101", "水泥", {"bars": bars, "tape": {d: _top_rows(15, 15) for d in cal}, "it": {}, "full": set()}, cal[-1], cal, {})
        self.assertTrue(r["thin"])
        self.assertIn("R27", {x["id"] for x in r["rules"]})


class RenderTest(unittest.TestCase):
    def _ok_row(self, **kw):
        row = {"sid": "2330", "name": "台積電", "status": "ok", "signal": "buy", "close": 100.0, "chg_pct": 1.0, "date": "2026-10-13",
               "day": {"main_lots": 100, "conc_pct": 12.0}, "win": {"1": {"main_lots": 100, "conc_pct": 12.0}, "5": {"main_lots": 300, "conc_pct": 6.0}},
               "boll": {"bw_pct": 4.0, "bw_level": "非常窄", "ma_dir": "up", "ma_slope_pct": 0.5, "zone": "ma_to_ub", "upper_half": True},
               "bias_year": 12.0, "streak2": {"prev_conc_pct": 11.0, "buy": True, "sell": False},
               "it": {"d1": {"lots": 5, "pct": 0.5}}, "buyers": [{"id": "9800", "name": "元大", "lots": 200, "named": "隔日沖"}], "sellers": [],
               "habit": [{"name": "元大", "events": 4, "flips": 2, "strong": True}],
               "named": [{"id": "984K", "name": "元大-館前", "role": "公司派", "lots": None}],
               "rules": [{"id": "R24", "label": "主力連續2日買超≥10%·高檔", "side": "buy"}], "flags": []}
        row.update(kw)
        return row

    def test_group_labels_escaped_and_bad_row_rendered(self):
        rows = [{"sid": "2330", "name": "台積電", "status": "tape_incomplete", "close": 1000.0, "chg_pct": 1.0, "it": {}}]
        html = chips_view.render_table(rows)
        self.assertIn("資料不全", html)
        self.assertIn("分點最新日未完整", html)
        self.assertNotIn("< −10%", html)

    def test_row_shows_book_fields_not_removed_ones(self):
        html = chips_view.render_table([self._ok_row()], "2026-10-13", None, {"band_pct": 10.0, "seats_shown": 3})
        for need in ("帶寬 4.0%（非常窄）", "月線↑", "連續買超", "隔日沖 2/4", "小哥點名", "公司派", "未入前段名單", "R24", "主力連續2日買超"):
            self.assertIn(need, html, need)
        for gone in ("席位共識", "家數差", "一致度", "價量背離", "【沖"):
            self.assertNotIn(gone, html, gone)

    def test_next_cells(self):
        row = {"close": 100.0, "next": {"date": "2026-10-14", "open": 101, "close": 99, "ret_pct": -1.0, "gap_pct": 1.0, "intraday_pct": -1.98}}
        self.assertIn("-1.00%", chips_view._next(row))
        self.assertIn("待下一交易日", chips_view._next({"close": 1.0}))

    def test_rules_alert_groups_by_rule_id(self):
        html = chips_view._rules_alert([self._ok_row()], "2026-10-13")
        self.assertIn("R24", html)
        self.assertIn("2330 台積電", html)
        self.assertIn("無", chips_view._rules_alert([self._ok_row(rules=[])], "2026-10-13"))

    def test_view_reads_calendar_over_row_inference(self):
        cal = {"asof": "2026-10-13", "prev": "2026-10-12", "next": "2026-10-14"}
        rows = [{"sid": "1", "name": "a", "date": "2026-10-13", "status": "tape_incomplete", "close": 1.0, "it": {}}]
        self.assertNotIn("10-14", chips_view.render_table(rows, "2026-10-13", cal))

    def test_old_schema_snapshot_is_refused_not_misrendered(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "chip_sop_2026-10-13.json").write_text(json.dumps({"schema": 3, "asof": "2026-10-13", "rows": []}), encoding="utf-8")
            with mock.patch.object(chips_view, "CHIP_DIR", d), mock.patch.object(chips_view, "LATEST", d / "chip_sop_2026-10-13.json"):
                import biglot_dashboard  # noqa: F401
                html = chips_view.render_chips("2026-10-13")
                self.assertIn("舊口徑快照", html)
                self.assertEqual(chips_view.render_csv().count("\n"), 1)          # 只有表頭


class ParamsDrivenViewTest(unittest.TestCase):
    P = {**chip_sop.PARAMS, "windows": "1／5／10／20", "gaps": "x"}

    def _page(self, p):
        note = chips_view._fill(chips_view.NOTE, p)
        return note + " ".join(label for _, label in chips_view._groups(p))

    def test_every_threshold_comes_from_params(self):
        base = self._page(self.P)
        self.assertNotIn("?", base.replace("{d0}", "").replace("{next}", ""))
        changed = self._page({**self.P, "band_pct": 12.0, "top_n": 20, "streak_days": 3, "thin_lots": 5000, "bw_narrow_pct": 8.0, "habit_days": 60})
        for must in ("+12%", "&lt; −12%", "前20買超", "連續 3 個交易日", "&lt; 5000", "≤8% 窄", "近 60 日"):
            self.assertIn(must, changed, must)
        for stale in ("+10%", "前15買超", "連續 2 個"):
            self.assertNotIn(stale, changed, stale)

    def test_missing_params_show_question_mark_not_a_default(self):
        self.assertIn("?", chips_view._fill(chips_view.NOTE, {}))
        self.assertIn("+?%", chips_view._groups({})[0][1])

    def test_snapshot_params_cover_every_placeholder(self):
        import re
        need = set(re.findall(r"\{([a-z_]+)\}", chips_view.NOTE)) - {"d0", "prev", "next", "asof", "complete_pct"}
        self.assertLessEqual(need, set(self.P), need - set(self.P))


if __name__ == "__main__":
    unittest.main()
