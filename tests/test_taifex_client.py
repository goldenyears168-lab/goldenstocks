import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src")]

import taifex_client as T  # noqa: E402

# 2026-10-15 期交所『三大法人-區分各期貨契約』臺股期貨列(攤平後的純文字;與當日 FinMind TX 三列逐項相同)
TX_TEXT = ("|1|臺股期貨|自營商|16,318|155,108,977|11,820|111,804,883|4,498|43,304,095|10,619|99,803,899|3,725|35,029,794|6,894|64,774,105"
           "|投信|2,783|26,328,968|2,271|21,442,466|512|4,886,502|81,728|767,295,600|2,799|26,271,974|78,929|741,023,626"
           "|外資|67,510|642,998,774|77,347|737,190,984|-9,837|-94,192,211|9,171|86,145,532|106,753|1,002,482,939|-97,582|-916,337,407|2|電子期貨|自營商|36|")


class TaifexClientTests(unittest.TestCase):
    def test_parse_tx_three_identities(self):
        rows = T.parse_futures_institutional(TX_TEXT, "TX", "2026-10-15")
        by = {r["inst_name"]: r for r in rows}
        self.assertEqual(set(by), {"自營商", "投信", "外資"})
        f = by["外資"]
        self.assertEqual((f["long_oi_vol"], f["short_oi_vol"], f["net_oi_vol"]), (9171, 106753, -97582))
        self.assertEqual((f["long_deal_vol"], f["short_deal_vol"], f["net_deal_vol"]), (67510, 77347, -9837))
        self.assertEqual(by["投信"]["net_oi_vol"], 78929)
        self.assertEqual(by["自營商"]["net_deal_vol"], 4498)
        self.assertTrue(all(r["source"] == "taifex" and r["futures_id"] == "TX" for r in rows))

    def test_missing_product_or_holiday_returns_none(self):
        self.assertIsNone(T.parse_futures_institutional("|查無資料|", "TX", "2026-10-09"))
        self.assertIsNone(T.parse_futures_institutional(TX_TEXT, "TE", "2026-10-15"))      # 電子期貨列不完整 → None

    def test_unknown_futures_id_raises(self):
        with self.assertRaises(KeyError):
            T.parse_futures_institutional(TX_TEXT, "ZZZ", "2026-10-15")

    def test_market_oi_total(self):
        txt = "|小型臺指 ( MTX ) 行情表|MTX|202610|1|2|3|小計:|130361|138951|269312|44732|*|"
        self.assertEqual(T.parse_market_oi_total(txt, "MTX"), 44732)
        self.assertIsNone(T.parse_market_oi_total("|沒有|", "MTX"))

    def test_parse_vix_ticks_sorted_and_skips_stats_row(self):
        from datetime import date
        raw = ("交易日期\t時間(時/分/秒/毫秒)\t臺指選擇權波動率指數\n--------\t----\t----\n"
               "20261016\t9001500\t\t\t29.37\n20261016\t9000000\t\t\t29.39\n"
               "20261016\t11224500\t\t\t27.83\n20261016\tLast 1 min AVG\t\t\t26.33\n20261015\t9000000\t\t\t1.0\n")
        t = T.parse_vix_ticks(raw, date(2026, 10, 16))
        self.assertEqual([(x["time"], x["vix"]) for x in t], [("09:00:00", 29.39), ("09:00:15", 29.37), ("11:22:45", 27.83)])
        self.assertTrue(all(x["date"] == "2026-10-16" for x in t))            # 別的日期列與統計列都不收

    def test_vix_zip_fallback_and_archive(self):
        import io
        import tempfile
        import zipfile
        from datetime import date
        from pathlib import Path
        from unittest import mock

        member = "20260807\t9000000\t\t\t36.31\n20260807\t9001500\t\t\t36.27\n".encode("big5")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("tvix_20260807", member)
        calls = []

        def fake_read(url, timeout):
            calls.append(url)
            return buf.getvalue() if url.endswith(".zip") or "%E6%AD%B7" in url else None      # 逐日檔 404,月壓縮檔有

        with tempfile.TemporaryDirectory() as d, mock.patch.object(T, "_read", fake_read):
            t = T.fetch_vix_ticks(date(2026, 8, 7), archive_dir=d)
            self.assertEqual(len(t), 2)
            self.assertTrue((Path(d) / "zip" / "202608.zip").exists())            # 壓縮檔存本機
            n = len(calls)
            T.fetch_vix_ticks(date(2026, 8, 7), archive_dir=d)
            self.assertEqual(len(calls), n + 1)                                    # 第二次只再嘗試逐日檔,壓縮檔不重下載

    def test_parse_futures_daily_after_market_keeps_every_row(self):
        from datetime import date
        txt = ("|( TX ) 行情表|2026/10/15 15:00~次日05:00 盤後交易時段行情表|契約|"
               "TX |202610 |46926|47417|46456|47366|▲435|▲0.93%|41283|-|- |47332|47367|50265|39852|"
               "TX |202611 |47079|47556|46620|47451|▲336|▲0.71%|1018|-|- |47484|47526|50440|44435|"
               "TX |202612 |-|-|-|-|-|-|0|-|- |47629|47677|50560|27382|"
               "小計:|42344|*|價差行情表|TX |202610/202611 |169|169|148|148|")
        rows = T.parse_futures_daily(txt, "TX", date(2026, 10, 16), "after_market")
        self.assertEqual([(r["contract_date"], r["close"], r["volume"]) for r in rows],
                         [("202610", 47366.0, 41283), ("202611", 47451.0, 1018)])      # 無成交列與價差列不收
        self.assertAlmostEqual(rows[0]["spread_per"], 0.93)
        self.assertTrue(all(r["trading_session"] == "after_market" and r["date"] == "2026-10-16" for r in rows))

    def test_parse_futures_daily_position_uses_regular_session_volume(self):
        from datetime import date
        txt = ("|( TX ) 行情表|TX |202610 |47228|47634|47140|47242|▲311|▲0.66%|41283|63312|104595|47284|97247|47245|47256|50265|39852|"
               "TX |202703 |48300|48300|48266|48266|▼328|▼0.68%|7|2|9|48110|125|48099|48121|51041|32400|小計:|1|")
        rows = T.parse_futures_daily(txt, "TX", date(2026, 10, 16), "position")
        self.assertEqual([r["volume"] for r in rows], [63312, 2])                       # 一般時段量,不是盤後量也不是合計
        self.assertAlmostEqual(rows[1]["spread_per"], -0.68)


if __name__ == "__main__":
    unittest.main()
