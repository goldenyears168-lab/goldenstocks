"""代理人規則版本釘樁：watcher 的常數必須等於 config/clone_agent_rules.json；改參數必須升版號。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED = {"1.0.0": "b05ddc2aa2", "1.0.1": "1dcca3dcf3", "1.2.0": "84f1165745", "1.2.1": "d753d168d7", "1.2.2": "779ae1a540"}   # 版本號 → 規則指紋；改參數就要新增一列並升 version


class CloneRulesTest(unittest.TestCase):
    def test_fingerprint_matches_version(self):
        from clone_agent.rules_version import fingerprint, load_rules
        r = load_rules()
        self.assertIn(r["version"], PINNED, "規則改了卻沒升版號/沒登記指紋")
        self.assertEqual(fingerprint(r), PINNED[r["version"]])

    def test_watcher_constants_equal_rules(self):
        from clone_agent.rules_version import load_rules
        sys.path.insert(0, str(ROOT / "scripts" / "research"))
        sys.path.insert(0, str(ROOT / "src"))
        import trader_signal_watch as w
        s = load_rules()["signal"]
        got = {"big_amt": w.BIG_AMT, "retail_cap": w.RETAIL_CAP, "amt_pct_min": w.AMT_PCT_MIN,
               "amt_pct_strict": w.AMT_PCT_STRICT, "pullback_max": w.PULLBACK_MAX,
               "pullback_strict": w.PULLBACK_STRICT, "chg_cm_strict": w.CHG_CM_STRICT,
               "skip_hours": list(w.SKIP_HOURS), "recheck_min": w.RECHECK_MIN,
               "start_hm": w.SIGNAL_START, "hi_window_min": w.HI_WINDOW_MIN, "rank_keep_pct": w.RANK_KEEP_PCT}
        self.assertEqual(got, s)

    def test_futures_tick_table(self):
        """期貨最小跳動 ≠ 現貨級距:>=500 一律 1 元(1.0.0 誤用現貨 >=1000 為 5 元)。"""
        sys.path.insert(0, str(ROOT / "scripts" / "research"))
        sys.path.insert(0, str(ROOT / "src"))
        import trader_signal_watch as w
        for px, tk in ((9.9, .01), (49.9, .05), (99.9, .1), (363.5, .5), (499.5, .5), (500, 1.), (1549, 1.), (3594, 1.)):
            self.assertEqual(w.tick_size(px), tk, px)
        self.assertEqual(round(1549 - w.tick_size(1549), 2), 1548.0)

    def test_pullback_ref_high_window(self):
        """回檔參考高點:近 30 分高 <= 當日高(所以 30 分高回檔≥0.5% 必然也滿足當日高回檔≥0.5%)。"""
        sys.path.insert(0, str(ROOT / "scripts" / "research"))
        sys.path.insert(0, str(ROOT / "src"))
        import trader_signal_watch as w
        tp = w.Tape("2099-01-01")
        tp._pxev["x"] = [(0.0, 100.0), (600.0, 110.0), (3600.0, 105.0), (4000.0, 104.0)]
        self.assertEqual(tp.px_at("x", 4000.0), (104.0, 110.0))
        self.assertEqual(tp.px_at("x", 4000.0, 1800), (104.0, 105.0))
        self.assertEqual(tp.px_at("x", 4000.0, 1800)[1], 105.0)
        self.assertLessEqual(tp.px_at("x", 4000.0, 1800)[1], tp.px_at("x", 4000.0)[1])
        self.assertEqual(tp.px_at("x", 500.0), (100.0, 100.0))   # 不看未來

    def test_flow_window_no_lookahead_and_boundaries(self):
        """Tape.flow:只算 t<=now 的事件;窗口含 now-sec 邊界(同舊 window 語意);sec=None 為全日。"""
        sys.path.insert(0, str(ROOT / "scripts" / "research"))
        sys.path.insert(0, str(ROOT / "src"))
        import trader_signal_watch as w
        tp = w.Tape("2099-01-01")
        evs = [(100.0, 10.0, 10.0, 0.0, 1), (1000.0, 20.0, 0.0, -5.0, -1), (1900.0, 30.0, 30.0, 0.0, 1), (5000.0, 99.0, 99.0, 99.0, 1)]
        c = tp._cum["x"] = ([], [0.0], [0.0], [0.0], [0.0])
        for t, a, b, r, sg in evs:
            c[0].append(t)
            for arr, v in zip(c[1:], (a, b, r, sg * a)):
                arr.append(arr[-1] + v)
        self.assertEqual(tp.flow("x", 2000.0, None), (60.0, 40.0, -5.0, 20.0))
        self.assertEqual(tp.flow("x", 2000.0, 1800)[0], 50.0)      # t=100 在 now-1800=200 之外
        self.assertEqual(tp.flow("x", 1900.0, 1800)[0], 60.0)      # 100 剛好 = now-1800 → 含
        self.assertEqual(tp.flow("x", 1999.0, 1800)[0], 50.0)      # now-1800=199 → 100 出窗
        self.assertEqual(tp.flow("x", 2000.0, None)[0], 60.0)      # 5000 的未來事件不入
        self.assertEqual(tp.flow("none", 2000.0, None), (0.0, 0.0, 0.0, 0.0))

    def test_frac_rank_convention(self):
        """frac_rank=(排前面者+平手/2)/母體;大戶以大者為前、散戶以小者為前;母體<5 回 None。"""
        sys.path.insert(0, str(ROOT / "scripts" / "research"))
        sys.path.insert(0, str(ROOT / "src"))
        import trader_signal_watch as w
        v = {str(i): float(i) for i in range(10)}
        self.assertEqual(w.frac_rank(v, "9"), 0.05)               # 最大 → 排最前
        self.assertEqual(w.frac_rank(v, "0"), 0.95)
        self.assertEqual(w.frac_rank(v, "0", larger=False), 0.05)  # 散戶:最小(最會賣)排最前
        self.assertIsNone(w.frac_rank({"a": 1.0, "b": 2.0}, "a"))
        self.assertIsNone(w.frac_rank({**v, "z": None}, "z"))

    def test_fut_quote_ladder_no_lookahead(self):
        import json
        import tempfile
        from datetime import datetime
        sys.path.insert(0, str(ROOT / "scripts" / "research"))
        sys.path.insert(0, str(ROOT / "src"))
        import trader_signal_watch as w
        day = "2099-01-02"
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "zz_books"
            d.mkdir()
            rows = []
            for hms, bids in (("10:00:00", [(1548, 3), (1547, 5), (1545, 2)]), ("10:05:00", [(1550, 1), (1549, 4)])):
                rows.append({"ts": f"{day}T{hms}+08:00", "session": "day", "stale": False,
                             "bids": [{"price": p, "size": z} for p, z in bids],
                             "asks": [{"price": bids[0][0] + 1, "size": 1}]})
            (d / f"zz_books_{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n" + " " * 100)
            old, w.CACHE = w.CACHE, Path(td)
            w._FUT_CACHE.clear()
            try:
                t = datetime.fromisoformat(f"{day}T10:02:00+08:00").timestamp()
                bid, ask, lad = w.fut_quote("ZZ", day, t, ladder=True)
                self.assertEqual((bid, ask), (1548.0, 1549.0))
                self.assertEqual([p for p, _ in lad], [1548.0, 1547.0, 1545.0])
                self.assertEqual(w.fut_quote("ZZ", day, t), (1548.0, 1549.0))
            finally:
                w.CACHE = old
                w._FUT_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
