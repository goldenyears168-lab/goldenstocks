"""共用模組 src/futures_flow.py:一列成交 → Tick → 爆發量 的規則。"""
import json
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src")]

import futures_flow as FF  # noqa: E402


def row(ts, px, cum, serial, qt="FUTURE", bid=None, ask=None, tt=1, stale=False):
    return json.dumps({"ts": ts, "price": px, "cum_volume": cum, "serial": serial, "quote_type": qt, "bid": bid if bid is not None else px - 1,
                       "ask": ask if ask is not None else px, "trade_time": tt, "stale": stale})


class GroupTests(unittest.TestCase):
    def test_group_of(self):
        self.assertEqual(FF.group_of(1, 2_400_000), "R")
        self.assertEqual(FF.group_of(3, 7_000_000), "M")
        self.assertEqual(FF.group_of(5, 11_800_000), "B")

    def test_tier_of(self):
        self.assertEqual([FF.tier_of(x) for x in (0, 2.4, 2.5, 4.9, 5, 11.9, 12, 24.9, 25, 90)], ["S", "S", "M1", "M1", "M2", "M2", "L", "L", "XL", "XL"])

    def test_pv(self):
        self.assertEqual((FF.PV["MXF"], FF.PV["TMF"], FF.PV["TXF"]), (50, 10, 200))


class SessionTests(unittest.TestCase):
    def test_session_and_minute_key(self):
        self.assertEqual(FF.session_of("FUTURE", "2026-10-14", 9 * 3600), ("day", "2026-10-14"))
        self.assertEqual(FF.session_of("FUTURE_AH", "2026-10-14", 16 * 3600), ("night", "2026-10-14"))
        self.assertEqual(FF.session_of("FUTURE_AH", "2026-10-15", 1 * 3600), ("night", "2026-10-14"))     # 凌晨屬前一夜
        self.assertEqual(FF.session_of("FUTURE_AH", "2026-10-17", 2 * 3600), ("night", "2026-10-16"))     # 週六凌晨=週五夜盤
        self.assertLess(FF.minute_key("night", 23 * 3600 + 59 * 60), FF.minute_key("night", 60))          # 午夜後單調遞增
        self.assertEqual(FF.minute_key("day", 9 * 3600), 540)


class StreamTests(unittest.TestCase):
    def test_filters_baseline_dedupe_and_dv(self):
        st = FF.TradeStream()
        L = [row("2026-10-14T00:03:00.000+08:00", 100, 5, 1),                          # 日盤時窗外(殭屍)
             row("2026-10-14T08:50:00.000+08:00", 47000, 1000, 2),                      # 基準
             row("2026-10-14T08:50:01.000+08:00", 47000, 1003, 3),                      # +3
             row("2026-10-14T08:50:01.500+08:00", 47000, 1003, 3),                      # (serial,cum) 重複
             row("2026-10-14T08:50:02.000+08:00", 47000, 1004, 4, stale=True),          # stale
             row("2026-10-14T08:50:03.000+08:00", 47000, 990, 5),                       # 累計量往回跳=重置
             row("2026-10-14T08:50:04.000+08:00", 47000, 995, 6)]                       # +5
        ts = [st.feed(x, "2026-10-14", "f") for x in L]
        got = [t for t in ts if t is not None]
        self.assertEqual([(t.dv, t.baseline, t.reset) for t in got], [(0, True, False), (3, False, False), (0, True, True), (5, False, False)])

    def test_two_streams_independent(self):
        st = FF.TradeStream()
        a = st.feed(row("2026-10-14T15:00:00.000+08:00", 47000, 100, 1, qt="FUTURE_AH"), "2026-10-14", "f")
        b = st.feed(row("2026-10-14T15:00:01.000+08:00", 47000, 10, 2, qt="FUTURE"), "2026-10-14", "f")     # 日盤時窗外,但不影響夜盤狀態
        c = st.feed(row("2026-10-14T15:00:02.000+08:00", 47000, 105, 3, qt="FUTURE_AH"), "2026-10-14", "f")
        self.assertTrue(a.baseline)
        self.assertIsNone(b)
        self.assertEqual(c.dv, 5)

    def test_side(self):
        st = FF.TradeStream()
        st.feed(row("2026-10-14T09:00:00.000+08:00", 100, 10, 1, bid=99, ask=101), "d", "f")
        buy = st.feed(row("2026-10-14T09:00:01.000+08:00", 101, 12, 2, bid=99, ask=101), "d", "f")
        sell = st.feed(row("2026-10-14T09:00:02.000+08:00", 99, 13, 3, bid=99, ask=101), "d", "f")
        mid = st.feed(row("2026-10-14T09:00:03.000+08:00", 100, 14, 4, bid=99, ask=101), "d", "f")
        self.assertEqual((buy.side, sell.side, mid.side), (1, -1, 0))


class BurstTests(unittest.TestCase):
    def _ticks(self):
        st = FF.TradeStream(); out = []
        L = [row("2026-10-14T09:00:00.000+08:00", 47000, 100, 1, tt=1),
             row("2026-10-14T09:00:01.000+08:00", 47000, 102, 2, tt=10),      # +2 買(同毫秒 tt=10)
             row("2026-10-14T09:00:01.001+08:00", 47001, 105, 3, tt=10, ask=47001),   # +3 買(同 tt,合併)
             row("2026-10-14T09:00:02.000+08:00", 47001, 106, 4, tt=20, ask=47001)]   # +1 買(新 tt)
        for x in L:
            t = st.feed(x, "2026-10-14", "f")
            if t:
                out.append(t)
        return out

    def test_streaming_merges_same_trade_time(self):
        got = []
        m = FF.BurstMerger(50, got.append, streaming=True)
        for t in self._ticks():
            m.add(t)
        m.flush_all()
        self.assertEqual([b.lots for b in got], [5, 1])
        self.assertAlmostEqual(got[0].notional_sum, 2 * 50 * 47000 + 3 * 50 * 47001)
        self.assertEqual(got[0].last_px, 47001)

    def test_batch_mode_same_result_when_adjacent(self):
        a, b = [], []
        ma, mb = FF.BurstMerger(50, a.append, streaming=True), FF.BurstMerger(50, b.append, streaming=False)
        for t in self._ticks():
            ma.add(t); mb.add(t)
        ma.flush_all(); mb.flush_all()
        self.assertEqual(sorted((x.trade_time, x.lots) for x in a), sorted((x.trade_time, x.lots) for x in b))

    def test_flush_old(self):
        got = []
        m = FF.BurstMerger(50, got.append, streaming=True)
        ticks = self._ticks()
        m.add(ticks[1]); self.assertEqual(got, [])
        m.last_tt["FUTURE"] = ticks[1].trade_time + 3_000_000          # 時間已往前走 3 秒
        m.flush_old()
        self.assertEqual(len(got), 1)


if __name__ == "__main__":
    unittest.main()
