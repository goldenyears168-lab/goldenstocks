"""ops_live_ta 與富邦的依賴反轉：未注入 fail-closed 降級、注入後走 gateway。"""
import unittest
from types import SimpleNamespace

import ops_live_ta


class _Reset(unittest.TestCase):
    def tearDown(self):
        ops_live_ta.install_fubon_gateway(None)


class NoGatewayTest(_Reset):
    def test_connect_raises_so_callers_degrade(self):
        ops_live_ta.install_fubon_gateway(None)
        with self.assertRaises(RuntimeError):
            ops_live_ta._connect_fubon()

    def test_open_rest_stock_returns_connect_error_not_exception(self):
        ops_live_ta.install_fubon_gateway(None)
        rest, sess, err = ops_live_ta.open_fubon_rest_stock()
        self.assertIsNone(rest)
        self.assertIsNone(sess)
        self.assertIn("fubon_connect:", err)

    def test_load_holdings_empty_without_gateway(self):
        ops_live_ta.install_fubon_gateway(None)
        self.assertEqual(ops_live_ta.load_holdings_from_fubon_session(object()), [])


class WithGatewayTest(_Reset):
    def _gw(self, snap):
        sess = SimpleNamespace(sdk=SimpleNamespace(marketdata=SimpleNamespace(rest_client=SimpleNamespace(stock="REST"))))
        calls = {}

        def connect(**kw):
            calls.update(kw)
            return sess

        return ops_live_ta.FubonGateway(
            connect=connect,
            account_snapshot=lambda s: snap,
            held_qty=lambda h: int(h.get("qty", 0)),
        ), calls

    def test_open_rest_stock_uses_gateway_with_realtime(self):
        gw, calls = self._gw({})
        ops_live_ta.install_fubon_gateway(gw)
        rest, sess, err = ops_live_ta.open_fubon_rest_stock()
        self.assertEqual((rest, err), ("REST", None))
        self.assertEqual(calls, {"realtime": True})

    def test_load_holdings_filters_qty_and_sorts(self):
        gw, _ = self._gw({"holdings": [
            {"stock_no": "2330", "stock_name": "台積電", "qty": 1},
            {"stock_no": "1101", "stock_name": "台泥", "qty": 0},
            {"stock_no": "1216", "qty": 2},
        ]})
        ops_live_ta.install_fubon_gateway(gw)
        out = ops_live_ta.load_holdings_from_fubon_session(object())
        self.assertEqual(out, [("1216", None), ("2330", "台積電")])

    def test_install_helper_wires_gateway(self):
        from order import ops_live_ta_gateway
        ops_live_ta_gateway.install()
        self.assertIsNotNone(ops_live_ta._FUBON_GATEWAY)


if __name__ == "__main__":
    unittest.main()
