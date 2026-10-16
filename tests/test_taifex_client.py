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


if __name__ == "__main__":
    unittest.main()
