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
        b = F.Book("MXF"); seen = set()
        L = [row("2026-10-14T00:03:00.000+08:00", 100, 5, 1),                       # 殭屍(時窗外)
             row("2026-10-14T08:50:00.000+08:00", 47000, 1000, 2, tt=10),           # 基準
             row("2026-10-14T08:50:01.000+08:00", 47000, 1001, 3, tt=20),           # 1 口主動買 → 散戶
             row("2026-10-14T08:50:02.000+08:00", 47000, 1006, 4, tt=30),           # 5 口(名目 1175 萬)→ 大戶
             row("2026-10-14T08:50:03.000+08:00", 47000, 1006, 5, tt=40, stale=True),
             row("2026-10-14T08:50:04.000+08:00", 47000, 1006, 4, tt=30),           # (serial,cum) 重複
             row("2026-10-14T08:50:09.000+08:00", 47001, 1007, 6, tt=5_000_000)]    # 讓前面的爆發被沖出
        for ln in L:
            b.feed_line(ln, "2026-10-14", seen)
        b._flush_old()
        bar = b.sessions[("day", "2026-10-14")][8 * 60 + 50]
        self.assertEqual(bar["vol"], 7)
        self.assertEqual(bar["bl"]["R"], 1)
        self.assertEqual(bar["bl"]["B"], 5)

    def test_night_stitch_across_midnight(self):
        b = F.Book("TMF"); seen = set()
        b.feed_line(row("2026-10-14T23:59:00.000+08:00", 48000, 100, 1, qt="FUTURE_AH"), "2026-10-14", seen)
        b.feed_line(row("2026-10-14T23:59:30.000+08:00", 48000, 103, 2, qt="FUTURE_AH", tt=1), "2026-10-14", seen)
        b.feed_line(row("2026-10-15T00:00:10.000+08:00", 48001, 106, 3, qt="FUTURE_AH", tt=9_000_000), "2026-10-15", seen)
        self.assertEqual(list(b.sessions), [("night", "2026-10-14")])           # 00:00 以後仍屬 10-14 夜盤
        ks = sorted(b.sessions[("night", "2026-10-14")])
        self.assertLess(ks[0], ks[-1])                                           # 00:00 排在 23:59 之後
        self.assertEqual(sum(v["vol"] for v in b.sessions[("night", "2026-10-14")].values()), 6)


if __name__ == "__main__":
    unittest.main()


class FutFlowSummaryTests(unittest.TestCase):
    def _book(self):
        b = F.Book("MXF"); seen = set()
        rows = [row("2026-10-14T08:50:00.000+08:00", 47000, 1000, 1, tt=10),
                row("2026-10-14T08:50:01.000+08:00", 47000, 1001, 2, tt=20),               # 散戶買 1 口
                row("2026-10-14T08:51:01.000+08:00", 47000, 1006, 3, tt=30_000_000, bid=47000, ask=47001),  # 5 口主動賣(價=買一)→ 大戶賣
                row("2026-10-14T08:59:59.000+08:00", 47000, 1007, 4, tt=90_000_000)]
        for ln in rows:
            b.feed_line(ln, "2026-10-14", seen)
        b._flush_old()
        return b

    def test_amount_and_windows(self):
        b = self._book()
        S = F.summarize(b.sessions[("day", "2026-10-14")])
        self.assertEqual(S["vol"], 7)
        self.assertAlmostEqual(S["amt"], 7 * 50 * 47000)
        t = S["full"]["tot"]
        self.assertEqual(t["R"]["bl"], 1)
        self.assertEqual(t["B"]["sl"], 5)
        self.assertLess(t["B"]["bn"] - t["B"]["sn"], 0)                                  # 大戶淨賣
        self.assertEqual(S["w5"]["vol"], 1)                                              # 最後 5 分鐘只剩 08:59 那 1 口
        self.assertAlmostEqual(F.share_diff(S["full"]), ((0 - 5 * 50 * 47000) - (1 * 50 * 47000)) / S["amt"] * 100)

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


class FutFlowActiveTests(unittest.TestCase):
    def test_is_active(self):
        D = dt.datetime
        self.assertTrue(F._is_active(("day", "2026-10-14"), D(2026, 10, 14, 9, 0)))
        self.assertFalse(F._is_active(("day", "2026-10-13"), D(2026, 10, 14, 9, 0)))
        self.assertTrue(F._is_active(("night", "2026-10-14"), D(2026, 10, 14, 15, 5)))
        self.assertTrue(F._is_active(("night", "2026-10-14"), D(2026, 10, 15, 2, 0)))     # 跨午夜仍是 10-14 夜盤
        self.assertFalse(F._is_active(("night", "2026-10-13"), D(2026, 10, 15, 2, 0)))
        self.assertFalse(F._is_active(("night", "2026-10-14"), D(2026, 10, 15, 10, 0)))   # 已收盤
