import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts" / "research")]

from biglot import futflow_view as F  # noqa: E402


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

    def test_amount_and_windows(self):
        b = self._book()
        S = F.summarize(b.sessions[("day", "2026-10-14")], "MXF")
        self.assertEqual(S["root"], "MXF")
        self.assertEqual(S["vol"], 7)
        self.assertAlmostEqual(S["amt"], 7 * 50 * 47000)
        t = S["full"]["tot"]
        self.assertEqual(t["R"]["bl"], 1)
        self.assertEqual(t["M"]["sl"], 5)                                                # 5 口主動賣 → 其餘(非連發)
        self.assertLess(t["M"]["bn"] - t["M"]["sn"], 0)
        self.assertEqual(S["w5"]["vol"], 1)                                              # 最後 5 分鐘只剩 08:59 那 1 口
        # 第一線(B=連發)此處為空 → (0 − 第二線淨額) ÷ 成交額
        self.assertAlmostEqual(F.share_diff(S["full"]), (0 - (1 * 50 * 47000)) / S["amt"] * 100)

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


class FutFlowPercentileTests(unittest.TestCase):
    def test_pct_rank(self):
        self.assertEqual(F._pct_rank(5, [1, 2, 3, 4]), 100.0)
        self.assertEqual(F._pct_rank(0, [1, 2, 3, 4]), 0.0)
        self.assertEqual(F._pct_rank(2, [1, 2, 3]), (1 + 0.5) / 3 * 100)
        self.assertIsNone(F._pct_rank(1, []))

    def _sess(self, net_wan_per_min, minutes=30):
        """合成一個盤別:每分鐘大戶買 net_wan_per_min 萬(其他為 0)。"""
        bars = {}
        for i in range(minutes):
            b = F._new_bar(47000)
            b["bn"]["B"] = net_wan_per_min * F.WAN
            b["bl"]["B"] = 1
            b["vol"] = 1
            b["amt"] = 10 * F.WAN
            bars[540 + i] = b
        return bars

    def test_percentiles_same_elapsed_and_min_prior(self):
        sessions = {("day", f"2026-10-{d:02d}"): self._sess(d) for d in range(1, 9)}   # 8 個歷史盤別,大戶淨額隨日期增加
        cur_key = ("day", "2026-10-09")
        sessions[cur_key] = self._sess(100)                                          # 比所有歷史都高
        order = sorted(sessions, key=lambda k: k[1])
        p = F.percentiles(sessions[cur_key], cur_key, sessions, order)
        self.assertEqual(p["n"], 8)
        self.assertEqual(p["B"], 100.0)
        # 歷史不足 5 個 → 只回 n
        few = {k: v for k, v in sessions.items() if k[1] in ("2026-10-01", "2026-10-02", "2026-10-09")}
        p2 = F.percentiles(few[cur_key], cur_key, few, sorted(few, key=lambda k: k[1]))
        self.assertEqual(p2, {"n": 2})

    def test_incomplete_history_excluded(self):
        sessions = {("day", f"2026-10-{d:02d}"): self._sess(d, minutes=5 if d == 1 else 30) for d in range(1, 8)}
        cur_key = ("day", "2026-10-09")
        sessions[cur_key] = self._sess(3)
        order = sorted(sessions, key=lambda k: k[1])
        self.assertEqual(F.percentiles(sessions[cur_key], cur_key, sessions, order)["n"], 6)   # 10-01 只有 5 分鐘,不比


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
    """凍結時間與合成資料,整段頁面片段與 tests/golden 逐字比對:改 HTML/CSS 結構時會失敗,迫使你有意識地更新基準(UPDATE_GOLDEN=1)。"""

    GOLDEN = Path(__file__).resolve().parent / "golden" / "futflow_mxf_body.html"

    def setUp(self):
        import os
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()                          # 隔離:不讀真實成交檔、不寫真實檢查點
        self._saved = (F._BOOKS.copy(), dict(F._LOADING), F._now, F._files, F.CKPT_DIR)
        F._files = lambda root: []
        F.CKPT_DIR = Path(self._tmp.name)
        self._csv = F.MP.CSV_PATH                                          # 小台持倉估計讀的基準檔也要隔離,否則輸出會隨正式機檔案與日期變動
        F.MP.CSV_PATH = Path(self._tmp.name) / "mtx_retail_ratio.csv"
        F.MP.CSV_PATH.write_text("date,total_oi,inst_net_oi\n2026-10-07,40000,-8000\n", encoding="utf-8")
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
        self._upd = os.environ.get("UPDATE_GOLDEN") == "1"

    def tearDown(self):
        F._BOOKS.clear(); F._BOOKS.update(self._saved[0]); F._LOADING.clear(); F._LOADING.update(self._saved[1])
        F._now, F._files, F.CKPT_DIR = self._saved[2], self._saved[3], self._saved[4]
        F.MP.CSV_PATH = self._csv
        self._tmp.cleanup()
        if self._bd is None:
            sys.modules.pop("biglot_dashboard", None)
        else:
            sys.modules["biglot_dashboard"] = self._bd

    def test_linked_page_renders_with_tsmc(self):
        for r in ("QFF", "CDF"):                                                    # 台積電期貨用同一份合成盤別(只驗版面與不當機)
            b = F.Book(r)
            b.sessions[("day", "2026-10-07")] = {m: dict(bar) for m, bar in _synthetic_session(7).items()}
            b.last_ts = "2026-10-07T09:29:50.000+08:00"
            F._BOOKS[r] = b
        html, live = F._body("ALL", "")
        self.assertTrue(live)
        for needle in ("期貨聯動", "MXF", "QFF", "CDF", "台積電合計", "相對開盤報酬", "第一線累計淨額", "1口連發", "孤立單口", "root=ALL"):
            self.assertIn(needle, html)
        self.assertEqual(F.render_futflow("ALL", "").count("id='ffapp'"), 1)       # 整頁入口也接受 ALL
        self.assertEqual(F._body("all", "day|2026-10-03")[1], False)                # 固定歷史盤別不輪詢

    def test_linked_page_without_tsmc_data(self):
        html, _ = F._body("ALL", "")                                                # 只有 MXF 時不應出錯,也不顯示台積電合計
        self.assertIn("MXF", html)
        self.assertNotIn("台積電合計", html)

    def test_health_overall_ignores_thin_tsmc(self):
        from biglot import health as H
        tmf = F.Book("TMF"); tmf.last_ts = "2026-10-07T09:29:50.000+08:00"
        cdf = F.Book("CDF"); cdf.last_ts = "2026-10-07T09:00:00.000+08:00"          # 30 分鐘沒成交:台積電期貨夜盤常態
        F._BOOKS["TMF"], F._BOOKS["CDF"] = tmf, cdf
        h = H.health()
        self.assertEqual(h["futflow"]["CDF"]["freshness"], "stale")                 # 仍如實回報
        self.assertEqual(h["overall"], "fresh")                                     # 但不影響整體判斷

    def test_body_matches_golden(self):
        html, live = F._body("MXF", "")
        self.assertTrue(live)
        self.assertIn("日盤進行中", html)
        self.assertIn("P", html)                                                   # 有百分位欄
        if self._upd or not self.GOLDEN.exists():
            self.GOLDEN.write_text(html, encoding="utf-8")
        self.assertEqual(html, self.GOLDEN.read_text(encoding="utf-8"))

    def test_per_root_definitions_and_adj_toggle(self):
        for r in ("TMF", "TXF"):
            b = F.Book(r)
            b.sessions[("day", "2026-10-07")] = {m: dict(bar) for m, bar in _synthetic_session(7).items()}
            b.last_ts = "2026-10-07T09:29:50.000+08:00"
            F._BOOKS[r] = b
        names = {"MXF": ("1口連發", "孤立單口"), "TMF": ("1口連發", "孤立單口"), "TXF": ("大單≥4000萬", "1口連發", "孤立單口")}
        for r, need in names.items():
            html, _ = F._body(r, "")
            for n in need:
                self.assertIn(n, html, (r, n))
            self.assertNotIn(">散戶</td>", html, r)                                      # 表格列不再用舊的『散戶』稱呼
            self.assertNotIn(">大單</td>", html.replace("大單≥4000萬", ""), r)
            self.assertIn("原始淨額", html)
            self.assertIn("扣價格殘差", html)
            self.assertIn("data-g=", html)
        adj, _ = F._body("MXF", "day|2026-10-07|adj")
        self.assertIn("扣價格", adj)
        self.assertIn("累計殘差", adj)
        self.assertEqual(F._parse_sel("day|2026-10-07|adj"), ("day|2026-10-07", True))
        self.assertEqual(F._parse_sel("adj"), ("", True))
        self.assertEqual(F._parse_sel("day|2026-10-07"), ("day|2026-10-07", False))
        self.assertEqual(F._body("MXF", "adj")[0].count("<svg"), 1)
        # 大台第三條線:四個槽位都顯示
        self.assertEqual(F.groups("TXF"), ("B", "C", "M", "R"))
        self.assertEqual(F.groups("MXF"), ("B", "M", "R"))

    def test_txf_has_streak_block_and_others_do_not(self):
        import futures_flow as FFm
        b = F.Book("TXF")
        b.sessions[("day", "2026-10-07")] = {m: dict(bar) for m, bar in _synthetic_session(7).items()}
        b.last_ts = "2026-10-07T09:29:50.000+08:00"
        for i in range(12):                                                 # 12 個連續紅窗 → 亮燈
            b.wstreak.add(FFm.Burst("FUTURE", 1, int((1_000_000 + i * 5 + 1) * 1e6), "day", "2026-10-07", 540, 3, 1.0, 47000.0))
        b.wstreak.flush_all()
        F._BOOKS["TXF"] = b
        html, _ = F._body("TXF", "")
        for needle in ("連次／連量(5 秒窗", "🔔", "+12", "預先登記", "futures-streak-prereg-20261019", "只放大台"):
            self.assertIn(needle, html, needle)
        mxf, _ = F._body("MXF", "")
        self.assertNotIn("連次／連量(5 秒窗", mxf)                           # 小台/微台不顯示(每天亮燈 12~17 次且無訊號)
        self.assertIsNone(F.Book("MXF").wstreak)
        self.assertIsNone(F.Book("TMF").wstreak)

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

    def test_health_shape(self):
        from biglot import health as H
        h = H.health()
        self.assertEqual(h["market"], "日盤進行中")
        self.assertEqual(h["futflow"]["MXF"]["freshness"], "fresh")
        self.assertEqual(h["futflow"]["MXF"]["sessions_loaded"], 7)
        self.assertEqual(h["futflow"]["TMF"]["freshness"], "missing")             # TMF 沒載入=交易時段內沒有資料
        self.assertEqual(h["overall"], "missing")

    def test_page_wires_live_js_only_when_live(self):
        page = F.render_futflow("MXF", "")
        self.assertIn("/assets/live.js", page)
        self.assertIn("data-live='1'", page)
        self.assertIn("data-live='0'", F.render_futflow("MXF", "day|2026-10-03"))   # 固定看歷史盤別不輪詢
