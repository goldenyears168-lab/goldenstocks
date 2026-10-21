import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts" / "research")]

from biglot import futflow_view as F  # noqa: E402

F.LEDGER_PATH = Path(tempfile.mkdtemp()) / "futures_streak_events.jsonl"     # 測試不得寫進正式前向帳本


def row(ts, px, cum, serial, qt="FUTURE", bid=None, ask=None, tt=1, stale=False):
    return json.dumps({"ts": ts, "price": px, "cum_volume": cum, "serial": serial, "quote_type": qt, "bid": bid if bid is not None else px - 1,
                       "ask": ask if ask is not None else px, "trade_time": tt, "stale": stale})


class FutFlowTests(unittest.TestCase):
    def test_groups(self):
        self.assertEqual(F.group_of(1, 2_400_000), "R")
        self.assertEqual(F.group_of(3, 7_000_000), "M")
        self.assertEqual(F.group_of(5, 11_800_000), "B")

    def test_day_burst_and_filters(self):
        b = F.Book("MXF")
        L = [row("2026-10-14T00:03:00.000+08:00", 100, 5, 1),                       # 殭屍(時窗外)
             row("2026-10-14T08:50:00.000+08:00", 47000, 1000, 2, tt=10),           # 基準
             row("2026-10-14T08:50:01.000+08:00", 47000, 1001, 3, tt=20),           # 1 口主動買 → 散戶
             row("2026-10-14T08:50:02.000+08:00", 47000, 1006, 4, tt=30),           # 5 口(名目 1175 萬)→ 大戶
             row("2026-10-14T08:50:03.000+08:00", 47000, 1006, 5, tt=40, stale=True),
             row("2026-10-14T08:50:04.000+08:00", 47000, 1006, 4, tt=30),           # (serial,cum) 重複
             row("2026-10-14T08:50:09.000+08:00", 47001, 1007, 6, tt=10_000_000)]   # 讓前面的爆發被沖出(≥2 口要超過 5 秒沒續單才結算)
        for ln in L:
            b.feed_line(ln, "2026-10-14")
        b.flush()
        bar = b.sessions[("day", "2026-10-14")][8 * 60 + 50]
        self.assertEqual(bar["vol"], 7)
        self.assertEqual(bar["bl"]["R"], 1)                                         # 孤立單口
        self.assertEqual(bar["bl"]["M"], 5)                                         # 小台 5 口(名目 1175 萬)不是連發 → 其餘;不再叫大單
        self.assertEqual(bar["bl"]["B"], 0)

    def test_night_stitch_across_midnight(self):
        b = F.Book("TMF")
        b.feed_line(row("2026-10-14T23:59:00.000+08:00", 48000, 100, 1, qt="FUTURE_AH"), "2026-10-14")
        b.feed_line(row("2026-10-14T23:59:30.000+08:00", 48000, 103, 2, qt="FUTURE_AH", tt=1), "2026-10-14")
        b.feed_line(row("2026-10-15T00:00:10.000+08:00", 48001, 106, 3, qt="FUTURE_AH", tt=9_000_000), "2026-10-15")
        self.assertEqual(list(b.sessions), [("night", "2026-10-14")])           # 00:00 以後仍屬 10-14 夜盤
        ks = sorted(b.sessions[("night", "2026-10-14")])
        self.assertLess(ks[0], ks[-1])                                           # 00:00 排在 23:59 之後
        self.assertEqual(sum(v["vol"] for v in b.sessions[("night", "2026-10-14")].values()), 6)


if __name__ == "__main__":
    unittest.main()


class FutFlowSummaryTests(unittest.TestCase):
    def _book(self):
        b = F.Book("MXF")
        rows = [row("2026-10-14T08:50:00.000+08:00", 47000, 1000, 1, tt=10),
                row("2026-10-14T08:50:01.000+08:00", 47000, 1001, 2, tt=20),               # 散戶買 1 口
                row("2026-10-14T08:51:01.000+08:00", 47000, 1006, 3, tt=30_000_000, bid=47000, ask=47001),  # 5 口主動賣(價=買一)→ 大戶賣
                row("2026-10-14T08:59:59.000+08:00", 47000, 1007, 4, tt=90_000_000)]
        for ln in rows:
            b.feed_line(ln, "2026-10-14")
        b.flush()
        return b

    def test_summarize_cumulative_in_yi(self):
        b = self._book()
        S = F.summarize(b.sessions[("day", "2026-10-14")], "MXF")
        self.assertEqual(S["root"], "MXF")
        self.assertEqual(S["price"][0][1], 47000)
        self.assertEqual(len(S["cum"]["R"]), len(S["ks"]))
        self.assertAlmostEqual(S["cum"]["R"][-1][1], 1 * 50 * 47000 / 1e8)               # 零星單口 1 口外盤 → +0.0235 億
        self.assertLess(S["cum"]["M"][-1][1], 0)                                         # 5 口內盤 → 中單淨額為負
        self.assertEqual(S["cum"]["B"][-1][1], 0)                                        # 沒有連續單口

    def test_yi_format(self):
        self.assertEqual(F._yi(17.33), "+17.3億")
        self.assertEqual(F._yi(-0.0391), "-0.04億")
        self.assertEqual(F._yi(1234.5, sign=False), "1,234.5億")

    def test_market_status(self):
        D = dt.datetime
        self.assertEqual(F.market_status(D(2026, 10, 14, 10, 0))[0], "日盤進行中")      # 週三
        self.assertEqual(F.market_status(D(2026, 10, 14, 20, 0))[0], "夜盤進行中")
        self.assertEqual(F.market_status(D(2026, 10, 15, 3, 0))[0], "夜盤進行中")        # 凌晨屬前夜
        self.assertEqual(F.market_status(D(2026, 10, 14, 14, 30))[0].startswith("盤後定格"), True)
        self.assertEqual(F.market_status(D(2026, 10, 17, 10, 0))[0].startswith("盤後定格"), True)   # 週六白天
        self.assertEqual(F.market_status(D(2026, 10, 17, 3, 0))[0], "夜盤進行中")        # 週六凌晨=週五夜盤尾

    def test_nice_step(self):
        self.assertEqual(F._nice_step(20), 5)
        self.assertEqual(F._nice_step(300), 60)
        self.assertEqual(F._nice_step(840), 120)


class FutFlowStreakBookTests(unittest.TestCase):
    """Book 與 StreakTagger 的整合:延遲確認後回頭歸類。"""

    @staticmethod
    def _chain(root, n, gap_us=500_000, start_tt=1_000_000, base=1000):
        b = F.Book(root)
        b.feed_line(row("2026-10-14T08:50:00.000+08:00", 47000, base, 1, tt=10), "2026-10-14")                    # 基準
        for i in range(n):
            b.feed_line(row(f"2026-10-14T08:50:{1 + i:02d}.000+08:00", 47000, base + 1 + i, 2 + i, tt=start_tt + i * gap_us), "2026-10-14")
        b.feed_line(row("2026-10-14T08:51:30.000+08:00", 47000, base + 1 + n, 99, tt=90_000_000), "2026-10-14")   # 很久以後:沖出前面所有爆發
        b.flush()
        return b.sessions[("day", "2026-10-14")][8 * 60 + 50]

    def test_six_one_lot_bursts_become_streak_retroactively(self):
        bar = self._chain("MXF", 6)
        self.assertEqual(bar["bl"]["B"], 6)                    # 連續 6 筆 → 全部(含達標前的前 4 筆)算 1口連發
        self.assertEqual(bar["bl"]["R"], 0)

    def test_four_one_lot_bursts_not_enough(self):
        bar = self._chain("MXF", 4)                            # 1 口需連續 ≥5 筆
        self.assertEqual(bar["bl"]["B"], 0)
        self.assertEqual(bar["bl"]["R"], 4)

    def test_slow_one_lot_bursts_are_isolated(self):
        bar = self._chain("TMF", 6, gap_us=2_000_000)         # 間隔 2 秒 > 1 秒 → 不連
        self.assertEqual(bar["bl"]["B"], 0)
        self.assertEqual(bar["bl"]["R"], 6)

    def test_txf_big_wins_over_streak_and_chain_has_own_slot(self):
        bar = self._chain("TXF", 6)                            # 大台 1 口名目 940 萬 < 4000 萬;6 筆連發 → C 槽
        self.assertEqual(bar["bl"]["C"], 6)
        self.assertEqual(bar["bl"]["R"], 0)

    def test_tsmc_has_no_tagger(self):
        self.assertIsNone(F.Book("QFF").tagger)
        self.assertIsNone(F.Book("CDF").tagger)
        self.assertIsNotNone(F.Book("MXF").tagger)


class FutFlowActiveTests(unittest.TestCase):
    def test_is_active(self):
        D = dt.datetime
        self.assertTrue(F._is_active(("day", "2026-10-14"), D(2026, 10, 14, 9, 0)))
        self.assertFalse(F._is_active(("day", "2026-10-13"), D(2026, 10, 14, 9, 0)))
        self.assertTrue(F._is_active(("night", "2026-10-14"), D(2026, 10, 14, 15, 5)))
        self.assertTrue(F._is_active(("night", "2026-10-14"), D(2026, 10, 15, 2, 0)))     # 跨午夜仍是 10-14 夜盤
        self.assertFalse(F._is_active(("night", "2026-10-13"), D(2026, 10, 15, 2, 0)))
        self.assertFalse(F._is_active(("night", "2026-10-14"), D(2026, 10, 15, 10, 0)))   # 已收盤


class FutFlowCheckpointTests(unittest.TestCase):
    def _fed(self):
        b = F.Book("MXF")
        for ln in [row("2026-10-14T23:59:00.000+08:00", 47000, 100, 1, qt="FUTURE_AH"),
                   row("2026-10-14T23:59:10.000+08:00", 47000, 106, 2, qt="FUTURE_AH", tt=5),     # 6 口主動買 → 大戶(名目 1,410 萬)
                   row("2026-10-15T00:00:10.000+08:00", 47001, 107, 3, qt="FUTURE_AH", ask=47001, tt=9_000_000)]:
            b.feed_line(ln, "2026-10-14" if ln.startswith('{"ts": "2026-10-14') else "2026-10-15", "f")
        return b

    def test_roundtrip_preserves_state(self):
        import json as _j
        b = self._fed()
        st = _j.loads(_j.dumps(b.to_state()))                      # 經過 JSON,模擬落地再讀回
        b2 = F.Book.from_state("MXF", st)
        self.assertIsNotNone(b2)
        self.assertEqual(sorted(b2.sessions), sorted(b.sessions))
        k = ("night", "2026-10-14")
        self.assertEqual({m: x["vol"] for m, x in b2.sessions[k].items()}, {m: x["vol"] for m, x in b.sessions[k].items()})
        self.assertEqual(b2.stream.prev, b.stream.prev)
        self.assertEqual(b2.merger.open, b.merger.open)
        self.assertEqual(_j.loads(_j.dumps(b.tagger.to_state())), b2.tagger.to_state())    # 尚未確定的連發串也要續接
        # 還原後繼續餵資料,結果與沒中斷的一致
        nxt = row("2026-10-15T00:00:20.000+08:00", 47001, 109, 4, qt="FUTURE_AH", ask=47001, tt=9_100_000)
        b.feed_line(nxt, "2026-10-15", "f"); b2.feed_line(nxt, "2026-10-15", "f")
        self.assertEqual(sum(x["vol"] for x in b2.sessions[k].values()), sum(x["vol"] for x in b.sessions[k].values()))

    def test_bad_checkpoint_rejected(self):
        st = self._fed().to_state()
        self.assertIsNone(F.Book.from_state("TMF", st))                       # 品牌不符
        self.assertIsNone(F.Book.from_state("MXF", dict(st, rules=999)))     # 規則版本不符
        self.assertIsNone(F.Book.from_state("MXF", dict(st, schema=0)))
        self.assertIsNone(F.Book.from_state("MXF", {"schema": F.CKPT_SCHEMA}))   # 結構殘缺


class FutFlowFreshnessTests(unittest.TestCase):
    def test_freshness_levels(self):
        now = dt.datetime(2026, 10, 14, 10, 0, 0)
        self.assertEqual(F.freshness("2026-10-14T09:59:30.000+08:00", True, now), ("fresh", 30.0))
        self.assertEqual(F.freshness("2026-10-14T09:58:00.000+08:00", True, now), ("stale", 120.0))
        self.assertEqual(F.freshness("", True, now), ("missing", None))
        self.assertEqual(F.freshness("2026-10-14T09:00:00.000+08:00", False, now), ("idle", None))     # 非交易時段不算過舊
        self.assertEqual(F.freshness("壞掉的時間", True, now), ("missing", None))


def _synthetic_session(k, minutes=30):
    """確定性的合成盤別(不用亂數),k 讓各盤別略有不同。"""
    bars = {}
    for i in range(minutes):
        px = 47000 + (i * 7 + k * 13) % 90
        b = F._new_bar(px)
        b["h"], b["l"], b["c"] = px + 4, px - 4, px + (1 if i % 2 else -1)
        b["vol"], b["t"], b["amt"] = 20 + i % 5, 10, (20 + i % 5) * 50 * px
        for g, mult in (("B", 3), ("M", 2), ("R", 1)):
            b["bl"][g] = (i + k) % 4 * mult
            b["sl"][g] = (i * 2 + k) % 4 * mult
            b["bn"][g] = b["bl"][g] * 50 * px
            b["sn"][g] = b["sl"][g] * 50 * px
        bars[540 + i] = b
    return bars


class FutFlowGoldenTests(unittest.TestCase):
    """凍結時間與合成資料,驗證每天一頁的版面(只放圖、日盤＋夜盤、各品牌兩張圖)。"""

    def setUp(self):
        import os
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()                          # 隔離:不讀真實成交檔、不寫真實檢查點
        self._saved = (F._BOOKS.copy(), dict(F._LOADING), F._now, F._files, F.CKPT_DIR)
        F._files = lambda root: []
        F.CKPT_DIR = Path(self._tmp.name)
        import types
        self._bd = sys.modules.get("biglot_dashboard")                     # 以空殼取代,避免 import 整個儀表板(會載入資料、約 27 秒)
        sys.modules["biglot_dashboard"] = types.SimpleNamespace(TABLE_CSS="body{}")
        book = F.Book("MXF")
        for d in range(1, 8):
            book.sessions[("day", f"2026-10-{d:02d}")] = _synthetic_session(d)
        book.last_ts = "2026-10-07T09:29:50.000+08:00"
        F._BOOKS.clear(); F._BOOKS["MXF"] = book
        F._LOADING.update({"started": True, "done": True, "err": ""})
        F._now = lambda: dt.datetime(2026, 10, 7, 9, 30, 0)                 # 週三 09:30,日盤進行中

    def tearDown(self):
        F._BOOKS.clear(); F._BOOKS.update(self._saved[0]); F._LOADING.clear(); F._LOADING.update(self._saved[1])
        F._now, F._files, F.CKPT_DIR = self._saved[2], self._saved[3], self._saved[4]
        self._tmp.cleanup()
        if self._bd is None:
            sys.modules.pop("biglot_dashboard", None)
        else:
            sys.modules["biglot_dashboard"] = self._bd

    def test_health_overall_ignores_thin_tsmc(self):
        from biglot import health as H
        tmf = F.Book("TMF"); tmf.last_ts = "2026-10-07T09:29:50.000+08:00"
        cdf = F.Book("CDF"); cdf.last_ts = "2026-10-07T09:00:00.000+08:00"          # 30 分鐘沒成交:台積電期貨夜盤常態
        F._BOOKS["TMF"], F._BOOKS["CDF"] = tmf, cdf
        h = H.health()
        self.assertEqual(h["futflow"]["CDF"]["freshness"], "stale")                 # 仍如實回報
        self.assertEqual(h["overall"], "fresh")                                     # 但不影響整體判斷

    def _two_roots(self):
        for r in ("TMF", "TXF"):
            b = F.Book(r)
            b.sessions[("day", "2026-10-07")] = {m: dict(bar) for m, bar in _synthetic_session(7).items()}
            b.last_ts = "2026-10-07T09:29:50.000+08:00"
            F._BOOKS[r] = b

    def test_day_page_has_only_charts_for_all_brands(self):
        self._two_roots()
        for r in ("QFF", "CDF"):                                                    # 台積電期貨用同一份合成盤別(只驗版面與不當機)
            b = F.Book(r)
            b.sessions[("day", "2026-10-07")] = {m: dict(bar) for m, bar in _synthetic_session(7).items()}
            F._BOOKS[r] = b
        page = F.render_futflow("MXF", "")
        self.assertEqual(page.count("<svg"), 2 + 5 * 2)                             # 聯動 2 張 + 5 個品牌各 2 張(原始、扣價格)
        for needle in ("TXF", "MXF", "TMF", "CDF", "QFF", "連續單口", "零星單口", "大單≥4000萬", "累計內外盤差", "扣掉價格影響", "相對開盤漲跌(%)", "10/07(三)"):
            self.assertIn(needle, page, needle)
        for gone in ("<table", "百分位", "口徑與限制", "逐 5 分鐘", "散戶淨部位", "bps", "萬元", "1口連發", "孤立單口"):
            self.assertNotIn(gone, page, gone)                                      # 表格/說明區塊/舊名詞/混用單位都不再出現

    def test_one_page_per_day_navigation(self):
        book = F._BOOKS["MXF"]
        book.sessions[("night", "2026-10-06")] = _synthetic_session(6)
        page = F.render_futflow("MXF", "2026-10-06")
        self.assertIn("10/06(二)", page)
        self.assertIn("href='/futflow?s=2026-10-07'", page)                          # 下一日
        self.assertIn("10/06 15:00 → 10/07 05:00 夜盤", page)
        self.assertIn("10/07(三)", F.render_futflow("MXF", ""))                      # 預設=最新一天
        self.assertIn("10/03(六)", F.render_futflow("MXF", "day|2026-10-03"))        # 舊連結格式導向該日
        self.assertIn("10/07(三)", F.render_futflow("MXF", "2099-01-01"))            # 不存在的日期退回最新

    def test_live_only_for_active_session(self):
        page = F.render_futflow("MXF", "")
        self.assertIn("/assets/live.js", page)
        self.assertIn("data-live='1'", page)
        self.assertIn("data-u='/futflowfrag?s=day|2026-10-07'", page)
        self.assertIn("data-live='0'", F.render_futflow("MXF", "2026-10-03"))        # 歷史日不輪詢
        frag = F.render_futflow_frag("MXF", "day|2026-10-07")
        self.assertIn("進行中", frag)
        self.assertEqual(frag.count("<svg"), 2 + 1 * 2)                              # 只有 MXF 有資料:聯動 2 + MXF 2
        self.assertEqual(F.render_futflow_frag("MXF", "night|2026-10-07"), "")       # 沒這個盤別
        self.assertEqual(F.render_futflow_frag("MXF", "垃圾"), "")

    def test_txf_streak_marks_only_on_txf(self):
        import futures_flow as FFm
        self._two_roots()
        b = F._BOOKS["TXF"]
        t0 = dt.datetime(2026, 10, 7, 9, 5, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()     # 落在合成盤別 09:00~09:29 內才畫得出標注
        for i in range(12):                                                         # 12 個連續紅窗 → 亮燈
            b.wstreak.add(FFm.Burst("FUTURE", 1, int((t0 + i * 5 + 1) * 1e6), "day", "2026-10-07", 540, 3, 1.0, 47000.0))
        b.wstreak.flush_all()
        page = F.render_futflow("MXF", "")
        self.assertIn("亮燈 紅連10", page)
        self.assertEqual(page.count("連次達 10 亮燈"), 1)                            # 說明列只掛在大台原始圖下,小台微台沒有
        self.assertNotIn("連次／連量(5 秒窗", page)                                    # 連次表格已移除

    def test_names_use_taiwan_futures_terms(self):
        import futures_flow as FFm
        self.assertEqual(FFm.slot_name("MXF", "B"), "連續單口")
        self.assertEqual(FFm.slot_name("MXF", "R"), "零星單口")
        self.assertEqual(FFm.slot_name("TXF", "M"), "中單")
        self.assertEqual(F.groups("TXF"), ("B", "C", "M", "R"))
        self.assertEqual(F.groups("MXF"), ("B", "M", "R"))

    def test_streak_state_survives_checkpoint(self):
        import json as _j
        import futures_flow as FFm
        b = F.Book("TXF")
        for i in range(11):
            b.wstreak.add(FFm.Burst("FUTURE", 1, int((2_000_000 + i * 5 + 1) * 1e6), "day", "2026-10-07", 540, 3, 1.0, 47000.0))
        b.wstreak.flush({"FUTURE": int((2_000_000 + 80) * 1e6)})
        st = _j.loads(_j.dumps(b.to_state()))
        b2 = F.Book.from_state("TXF", st)
        self.assertIsNotNone(b2)
        self.assertEqual(b.wstreak.snapshot(), b2.wstreak.snapshot())

    def test_shadow_tick_rule_version_keeps_frozen_definition_untouched(self):
        """凍結版（預先登記）仍丟棄買賣價之間無法判定方向的成交；影子版用 tick rule 補判後餵同一個 WindowStreak。"""
        import json as _j
        b = F.Book("TXF")
        base = 1_000_000_000_000_000            # trade_time µs

        def line(px, bid, ask, cv, i):
            return _j.dumps({"ts": "2026-10-07T09:00:01.000+08:00", "root": "TXF", "symbol": "TXFJ6", "price": px, "size": 1,
                             "bid": bid, "ask": ask, "trade_time": base + i * 1000, "cum_volume": cv, "serial": i,
                             "quote_type": "FUTURE", "stale": False})
        b.feed_line(line(47000, 46999, 47001, 100, 1), "2026-10-07")        # 基準列
        b.feed_line(line(47001, 46999, 47001, 102, 2), "2026-10-07")        # ≥ask → 外盤（2 口）
        b.feed_line(line(47002, 46999, 47003, 105, 3), "2026-10-07")        # 在買賣價之間 → 凍結版無法判定；tick rule：價高於前一筆 → 外盤（3 口）
        self.assertEqual(b.unk[("day", "2026-10-07")], [3, 5])              # 無法判定口數／總口數
        b.merger.flush_all(); b.merger_fill.flush_all()
        frozen = sum(w[0] for ws in b.wstreak.win.values() for w in ws.values())
        shadow = sum(w[0] for ws in b.wstreak_fill.win.values() for w in ws.values())
        self.assertEqual((frozen, shadow), (2, 5))
        st = _j.loads(_j.dumps(b.to_state()))                               # 影子狀態與無法判定統計都進檢查點
        b2 = F.Book.from_state("TXF", st)
        self.assertEqual(b2.unk, b.unk)
        self.assertEqual(b2._fill_state, b._fill_state)
        self.assertIsNone(F.Book("MXF").wstreak_fill)

    def test_streak_marks_two_versions_and_flat_turn_not_drawn(self):
        import datetime as _dt
        tz = _dt.timezone(_dt.timedelta(hours=8))
        e = lambda h, m, s: _dt.datetime(2026, 10, 7, h, m, s, tzinfo=tz).timestamp()   # noqa: E731
        ws = {("day", "T"): {"lits": [[e(9, 10, 5), 1, 10]], "turns": [[e(9, 12, 0), -1, 12], [e(9, 15, 0), 0, 11]]}}
        wf = {("day", "T"): {"lits": [[e(9, 10, 20), 1, 10]], "turns": []}}
        marks = F._streak_marks(ws, ("day", "T"), "A") + F._streak_marks(wf, ("day", "T"), "B")
        ks = list(range(540, 600))
        S = {"ks": ks, "root": "TXF", "price": [(k, 47000 + k % 7) for k in ks],
             "cum": {g: [(k, i * 10.0) for i, k in enumerate(ks)] for g in F.groups("TXF")}}
        svg = F._overlay_svg(S, '測試圖', marks=marks)
        self.assertIn("[凍結版]", svg)
        self.assertIn("[補tick rule版]", svg)
        self.assertEqual(svg.count("轉綠(前段12窗)"), 1)
        self.assertNotIn("轉平", svg)                                       # 平手轉色不畫標記

    def test_tsmc_futures_also_run_streak_with_shadow_and_others_do_not(self):
        for r in ("TXF", "CDF", "QFF"):
            b = F.Book(r)
            self.assertIsNotNone(b.wstreak, r)
            self.assertIsNotNone(b.wstreak_fill, r)
        for r in ("MXF", "TMF"):
            self.assertIsNone(F.Book(r).wstreak, r)                     # 小台微台探索期無效，不顯示
        self.assertEqual(F.STREAK_BOOKS, ("TXF", "CDF", "QFF"))

    def test_streak_events_ledger_records_both_versions_once(self):
        import futures_flow as FFm
        F._LEDGER_SEEN.clear()
        F.LEDGER_PATH = Path(tempfile.mkdtemp()) / "futures_streak_events.jsonl"
        b = F.Book("CDF")
        b.sessions[("day", "2026-10-07")] = {m: dict(bar) for m, bar in _synthetic_session(7).items()}
        t0 = dt.datetime(2026, 10, 7, 9, 5, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        for i in range(12):
            bu = FFm.Burst("FUTURE", 1, int((t0 + i * 5 + 1) * 1e6), "day", "2026-10-07", 545, 3, 1.0, 2200.0)
            b.wstreak.add(bu); b.wstreak_fill.add(bu)
        b.wstreak.flush_all(); b.wstreak_fill.flush_all()
        b._record_streak_events(); b._record_streak_events()          # 第二次不得重複
        rows = [json.loads(x) for x in F.LEDGER_PATH.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sorted((r["version"], r["kind"]) for r in rows), [("A", "lit"), ("B", "lit")])
        self.assertTrue(all(r["root"] == "CDF" and r["dir"] == 1 and r["n"] == 10 and r["source"] == "replay" for r in rows))   # 2026-10-07 距現在遠 → replay

    def test_streak_legend_mentions_brand_status_and_excluded_share(self):
        wsr = ({("day", "T"): {"lits": [[1000.0, 1, 10]], "turns": []}}, {("day", "T"): {"lits": [[1005.0, 1, 10], [2000.0, -1, 10]], "turns": []}},
               {("day", "T"): [150, 1000]})
        txt = F._streak_legend("CDF", ("day", "T"), wsr)
        self.assertIn("台積電期貨：前向記錄中", txt)
        self.assertIn("15.0%", txt)                                       # 無法判定口數被凍結版排除的比例
        self.assertIn("凍結版 1 次、補版 2 次", txt)
        self.assertIn("凍結版 1/1、補版 1/2 重疊", txt)
        self.assertIn("大台：預先登記確認期", F._streak_legend("TXF", ("day", "T"), wsr))

    def test_health_shape(self):
        from biglot import health as H
        h = H.health()
        self.assertEqual(h["market"], "日盤進行中")
        self.assertEqual(h["futflow"]["MXF"]["freshness"], "fresh")
        self.assertEqual(h["futflow"]["MXF"]["sessions_loaded"], 7)
        self.assertEqual(h["futflow"]["TMF"]["freshness"], "missing")             # TMF 沒載入=交易時段內沒有資料
        self.assertEqual(h["overall"], "missing")
