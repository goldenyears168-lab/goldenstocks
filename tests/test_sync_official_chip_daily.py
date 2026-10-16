import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts" / "research")]

import sync_official_chip_daily as S  # noqa: E402
from source_dedup import SOURCE_PRIORITY  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_tpex_margin_rows(self):
        row = ["3105", "穩懋", "44,268", "506", "827", "4", "43,943", "458", "41.46", "105,985",
               "769", "4", "91", "0", "682", "46", "0.64", "105,985", "10", ""]
        payload = {"tables": [{"data": [row, ["", "合計", "1"] + [""] * 17]}]}
        rows = S.parse_tpex_margin(payload, date(2026, 10, 16))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["stock_id"], r["margin_balance"], r["margin_change"], r["short_balance"], r["short_change"]),
                         ("3105", 43943.0, -325.0, 682.0, -87.0))
        self.assertEqual(r["source"], "tpex_margn")

    def test_tpex_margin_empty_payload(self):
        self.assertEqual(S.parse_tpex_margin({}, date(2026, 10, 16)), [])
        self.assertEqual(S.parse_tpex_margin({"tables": []}, date(2026, 10, 16)), [])

    def test_source_priority_registers_tpex_margin(self):
        self.assertEqual(SOURCE_PRIORITY["stock_margin_daily"][:2], ("twse_mi_margn", "tpex_margn"))


class TpexInstitutionalMappingTests(unittest.TestCase):
    """櫃買法人表 24 欄、7 組買賣超:外資不含自營 / 外資自營 / 外資合計 / 投信 / 自營自行 / 自營避險 / 自營合計。"""

    def payload(self, ncols=24):
        fields = ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]
        row = ["3081", "聯亞", "463,637", "1,566,306", "-1,102,669", "0", "0", "0", "463,637", "1,566,306", "-1,102,669",
               "141,600", "238,000", "-96,400", "125,669", "121,967", "3,702", "309,000", "317,800", "-8,800",
               "434,669", "439,767", "-5,098", "-1,204,167"]
        return {"tables": [{"title": "買賣超股數", "fields": fields[:ncols] if ncols != 24 else fields, "data": [row]}]}

    def test_trust_and_dealer_use_correct_groups(self):
        bi = S._load_script("backfill_twse_institutional")
        with mock.patch.object(bi, "_get", return_value=self.payload()), mock.patch.object(bi, "_table", side_effect=lambda p, k: p["tables"][0]):
            rows = bi.fetch_tpex(date(2026, 10, 13))
        r = rows[0]
        self.assertEqual((r["foreign_net"], r["investment_trust_net"], r["dealer_self_net"], r["three_institution_net"]),
                         (-1102669.0, -96400.0, -5098.0, -1204167.0))      # 與同日 FinMind 投信 −96,400 相同
        self.assertEqual(r["source"], "tpex_insti")

    def test_unrecognised_layout_writes_nothing(self):
        bi = S._load_script("backfill_twse_institutional")
        with mock.patch.object(bi, "_get", return_value=self.payload(ncols=20)), mock.patch.object(bi, "_table", side_effect=lambda p, k: p["tables"][0]):
            self.assertEqual(bi.fetch_tpex(date(2026, 10, 13)), [])


class SyncDayTests(unittest.TestCase):
    def setUp(self):
        from stock_db import connect
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def mods(self, margin_rows=None):
        bm = mock.Mock(MARGIN_SOURCE="twse_mi_margn", DAYTRADE_SOURCE="twse_twtb4u")
        bm.fetch_margin.return_value = margin_rows or []
        bm.upsert_margin.side_effect = lambda conn, rows: len(rows)
        bm._get.return_value = {}
        bm.fetch_daytrade.return_value = []
        bi = mock.Mock()
        bi.fetch_twse.return_value = []
        bi.fetch_tpex.return_value = []
        return {"bm": bm, "bi": bi}

    def test_per_source_skip_not_per_day(self):
        """FinMind 已有當天資料時,官方來源仍要補(舊回補腳本的『任何來源有就跳過』正是缺口原因)。"""
        self.conn.execute("INSERT INTO stock_margin_daily(stock_id,trade_date,margin_balance,source,synced_at) VALUES ('2330','2026-10-16',1,'finmind','x')")
        self.conn.commit()
        m = self.mods([{"stock_id": "2330", "trade_date": "2026-10-16", "source": "twse_mi_margn"}])
        res = S.sync_day(self.conn, date(2026, 10, 16), ("margin_twse",), sleep=0, mods=m)
        self.assertEqual(res["margin_twse"], 1)
        m["bm"].fetch_margin.assert_called_once()

    def test_skips_when_official_already_present_unless_forced(self):
        self.conn.execute("INSERT INTO stock_margin_daily(stock_id,trade_date,margin_balance,source,synced_at) VALUES ('2330','2026-10-16',1,'twse_mi_margn','x')")
        self.conn.commit()
        m = self.mods([{"stock_id": "2330", "trade_date": "2026-10-16", "source": "twse_mi_margn"}])
        self.assertEqual(S.sync_day(self.conn, date(2026, 10, 16), ("margin_twse",), sleep=0, mods=m)["margin_twse"], 0)
        m["bm"].fetch_margin.assert_not_called()
        self.assertEqual(S.sync_day(self.conn, date(2026, 10, 16), ("margin_twse",), sleep=0, mods=m, force=True)["margin_twse"], 1)

    def test_nothing_fetched_is_reported_not_written(self):
        res = S.sync_day(self.conn, date(2026, 10, 17), ("margin_twse", "inst_tpex"), sleep=0, mods=self.mods())
        self.assertEqual(res, {"margin_twse": -1, "inst_tpex": -1})

    def test_single_institutional_side(self):
        m = self.mods()
        S.sync_day(self.conn, date(2026, 10, 16), ("inst_tpex",), sleep=0, mods=m)
        m["bi"].fetch_tpex.assert_called_once()
        m["bi"].fetch_twse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
