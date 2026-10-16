"""finmind_client 測試。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from finmind_client import (
    FINMIND_DATA_URL,
    finmind_headers,
)


class FinmindDisabledByDefaultTests(unittest.TestCase):
    """2026-10 起不續約:沒設 FINMIND_ENABLED=1 就不得連線。"""

    def test_disabled_by_default_raises_without_network(self) -> None:
        from finmind_client import FinMindDisabled, fetch_finmind_json

        env = {k: v for k, v in os.environ.items() if k != "FINMIND_ENABLED"}
        with patch.dict(os.environ, env, clear=True), patch("finmind_client.requests.get") as get:
            with self.assertRaises(FinMindDisabled):
                fetch_finmind_json({"dataset": "TaiwanStockPrice"})
            get.assert_not_called()

    def test_token_hidden_and_skip_helper_when_disabled(self) -> None:
        from finmind_client import finmind_token, skip_if_disabled

        with patch.dict(os.environ, {"FINMIND_TOKEN": "x", "FINMIND_ENABLED": "0"}):
            self.assertEqual(finmind_token(), "")
            self.assertTrue(skip_if_disabled("測試步驟"))
        with patch.dict(os.environ, {"FINMIND_ENABLED": "1"}):
            self.assertFalse(skip_if_disabled("測試步驟"))

    def test_disabled_error_is_runtime_error(self) -> None:
        from finmind_client import FinMindDisabled

        self.assertTrue(issubclass(FinMindDisabled, RuntimeError))     # 既有 except RuntimeError 路徑照常處理


@patch.dict(os.environ, {"FINMIND_ENABLED": "1"})
class FinmindClientTests(unittest.TestCase):
    def test_finmind_headers_empty_without_token(self) -> None:
        with patch("finmind_client.finmind_token", return_value=""):
            self.assertEqual(finmind_headers(), {})

    def test_finmind_headers_bearer_with_token(self) -> None:
        with patch.dict(os.environ, {"FINMIND_TOKEN": "test-token"}):
            self.assertEqual(
                finmind_headers(),
                {"Authorization": "Bearer test-token"},
            )

    def test_fetch_finmind_json_raises_on_api_error(self) -> None:
        from finmind_client import fetch_finmind_json

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": 400, "msg": "bad request"}
        mock_resp.raise_for_status = MagicMock()

        with patch("finmind_client.requests.get", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_finmind_json({"dataset": "TaiwanStockInfo"})
        self.assertIn("bad request", str(ctx.exception))

    def test_fetch_finmind_dataset_returns_rows(self) -> None:
        from finmind_client import fetch_finmind_dataset

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": 200, "data": [{"stock_id": "2330"}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("finmind_client.finmind_token", return_value="test-token"):
            with patch("finmind_client.requests.get", return_value=mock_resp) as get:
                rows = fetch_finmind_dataset("TaiwanStockInfo")
        self.assertEqual(rows, [{"stock_id": "2330"}])
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["params"]["dataset"], "TaiwanStockInfo")
        self.assertEqual(get.call_args.kwargs["params"]["token"], "test-token")
        self.assertEqual(get.call_args.kwargs["headers"], {})
        self.assertEqual(get.call_args.args[0], FINMIND_DATA_URL)

    def test_fetch_futures_snapshots(self) -> None:
        from finmind_client import fetch_futures_snapshots

        tx_resp = MagicMock()
        tx_resp.json.return_value = {
            "status": 200,
            "data": [{"futures_id": "TXFF6", "close": 22000}],
        }
        ex_resp = MagicMock()
        ex_resp.json.return_value = {"status": 200, "data": []}

        with patch.dict(os.environ, {"FINMIND_TOKEN": "test-token"}):
            with patch(
                "finmind_client.requests.get",
                side_effect=[tx_resp, ex_resp],
            ) as get:
                rows, err = fetch_futures_snapshots(["TXF", "EXF"])
        self.assertIsNone(err)
        self.assertEqual(len(rows), 1)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["data_id"], "TXF")
        self.assertEqual(get.call_args_list[1].kwargs["params"]["data_id"], "EXF")


if __name__ == "__main__":
    unittest.main()
