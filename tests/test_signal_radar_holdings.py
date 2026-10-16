"""strategy_signal_radar 與富邦持倉讀取的依賴反轉：未注入 fail-closed、注入後走 fetcher。"""
import os
import unittest
from unittest.mock import patch

import strategy_signal_radar as radar
from order import signal_radar_holdings


class _Reset(unittest.TestCase):
    def tearDown(self):
        radar.install_holdings_fetcher(None)


class NoFetcherTest(_Reset):
    def test_returns_error_without_touching_broker(self):
        radar.install_holdings_fetcher(None)
        with patch.dict(os.environ, {"SIGNAL_RADAR_HOLDINGS_OVERRIDE": "", "SIGNAL_RADAR_USE_FUBON": "1"}):
            holdings, err = radar.fetch_fubon_holdings()
        self.assertEqual(holdings, {})
        self.assertIn("not installed", err)

    def test_override_still_wins_without_fetcher(self):
        with patch.dict(os.environ, {"SIGNAL_RADAR_HOLDINGS_OVERRIDE": "2330:1000, 2317:500"}):
            holdings, err = radar.fetch_fubon_holdings()
        self.assertEqual(holdings, {"2330": 1000, "2317": 500})
        self.assertIsNone(err)

    def test_use_fubon_flag_off_short_circuits(self):
        radar.install_holdings_fetcher(lambda: {"2330": 1})
        with patch.dict(os.environ, {"SIGNAL_RADAR_HOLDINGS_OVERRIDE": "", "SIGNAL_RADAR_USE_FUBON": "0"}):
            holdings, err = radar.fetch_fubon_holdings()
        self.assertEqual(holdings, {})
        self.assertEqual(err, "SIGNAL_RADAR_USE_FUBON=0")


class WithFetcherTest(_Reset):
    def test_fetcher_result_returned(self):
        radar.install_holdings_fetcher(lambda: {"2330": 2000})
        with patch.dict(os.environ, {"SIGNAL_RADAR_HOLDINGS_OVERRIDE": "", "SIGNAL_RADAR_USE_FUBON": "1"}):
            self.assertEqual(radar.fetch_fubon_holdings(), ({"2330": 2000}, None))

    def test_fetcher_exception_degrades_to_error(self):
        def boom():
            raise RuntimeError("login failed")
        radar.install_holdings_fetcher(boom)
        with patch.dict(os.environ, {"SIGNAL_RADAR_HOLDINGS_OVERRIDE": "", "SIGNAL_RADAR_USE_FUBON": "1"}):
            holdings, err = radar.fetch_fubon_holdings()
        self.assertEqual(holdings, {})
        self.assertEqual(err, "login failed")

    def test_install_helper_wires_readonly_fetcher(self):
        signal_radar_holdings.install()
        self.assertIs(radar._HOLDINGS_FETCHER, signal_radar_holdings.fetch_holdings)

    def test_fetch_holdings_uses_non_realtime_session_and_inventories_only(self):
        with patch.object(signal_radar_holdings, "connect_fubon", return_value="S") as c, \
                patch.object(signal_radar_holdings, "holdings_shares_by_symbol", return_value={"2330": 1}) as h:
            self.assertEqual(signal_radar_holdings.fetch_holdings(), {"2330": 1})
        c.assert_called_once_with(realtime=False)
        h.assert_called_once_with("S")


if __name__ == "__main__":
    unittest.main()
