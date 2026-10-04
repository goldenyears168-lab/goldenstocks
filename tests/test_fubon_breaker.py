"""富邦連線斷路器的行為釘樁。

事故背景：2026-09-28 13:14 → 09-29 10:10 富邦端連續 21 小時回 503
（`FubonSDK()` 建構式就丟,在登入之前）。我們的反應把被動故障放大成主動轟炸：
collector 退避上限只有 120s、第 6 次失敗就退出讓 launchd 重啟,而**重啟讓計數器
歸零** → 156 秒一輪、當天重啟 231 次、約 6,700 次連線嘗試,9/29 開盤完全沒有資料。

所以這些測試釘的是「不能再發生」的幾條：跨重啟不歸零、冷卻會變長、
設定類錯誤不被藏起來、斷路器自己壞掉不能擋住交易。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path


class FubonBreakerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("GOLDENSTOCKS_DATA_DIR")
        os.environ["GOLDENSTOCKS_DATA_DIR"] = self._tmp.name
        os.environ.pop("FUBON_BREAKER_DISABLE", None)
        import importlib
        import order.fubon_session as FS
        self.FS = importlib.reload(FS)
        self.FS.breaker_reset()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GOLDENSTOCKS_DATA_DIR", None)
        else:
            os.environ["GOLDENSTOCKS_DATA_DIR"] = self._old
        self._tmp.cleanup()

    def test_clean_state_does_not_block(self):
        self.FS._breaker_check()                      # 不該丟例外
        self.assertFalse(self.FS.breaker_state()["open"])

    def test_503_trips_breaker_and_blocks_next_attempt(self):
        self.FS._breaker_record(ValueError("HTTP error: 503 Service Unavailable"))
        st = self.FS.breaker_state()
        self.assertTrue(st["open"])
        self.assertEqual(st["fails"], 1)
        with self.assertRaises(self.FS.FubonBreakerOpen):
            self.FS._breaker_check()

    def test_cooldown_grows_and_caps_at_30min(self):
        """9/29 的致命點是退避上限只有 120s。這裡釘住它會一路長到 30 分鐘。"""
        seen = []
        for _ in range(8):
            self.FS._breaker_record(ValueError("503 Service Unavailable"))
            seen.append(round(self.FS.breaker_state()["retry_after"]))
            self.FS._breaker_write({**self.FS._breaker_read(), "next_try": 0})
        self.assertEqual(seen[:6], [5, 15, 60, 300, 900, 1800])
        self.assertEqual(seen[6], 1800)               # 封頂不再成長
        self.assertEqual(seen[7], 1800)

    def test_state_survives_process_restart(self):
        """★ 9/29 最關鍵的洞：行程退出重啟會把記憶體計數器歸零,
        所以「退出讓 launchd 重啟」反而讓退避完全失效。狀態必須在檔案裡。"""
        for _ in range(4):
            self.FS._breaker_record(ValueError("503 Service Unavailable"))
        import importlib
        import order.fubon_session as FS2
        FS2 = importlib.reload(FS2)                   # 模擬行程重啟
        st = FS2.breaker_state()
        self.assertEqual(st["fails"], 4, "重啟後失敗計數歸零 = 9/29 的重演")
        self.assertTrue(st["open"])
        self.assertGreater(st["retry_after"], 100)

    def test_config_errors_do_not_trip_breaker(self):
        """帳密／憑證錯誤重試再多次也不會好,但那是人要修的,不能被斷路器藏起來。"""
        for exc in (ValueError("FUBON_PASSWORD 未設定"),
                    FileNotFoundError("Fubon cert not found: x.pfx"),
                    RuntimeError("Fubon login failed: 密碼錯誤")):
            self.FS._breaker_record(exc)
        self.assertEqual(self.FS.breaker_state()["fails"], 0)
        self.FS._breaker_check()                      # 不該被擋

    def test_connection_limit_also_trips(self):
        self.FS._breaker_record(RuntimeError("Maximum number of connections reached"))
        self.assertTrue(self.FS.breaker_state()["open"])

    def test_success_clears_breaker(self):
        self.FS._breaker_record(ValueError("503 Service Unavailable"))
        self.FS._breaker_clear()
        self.assertFalse(self.FS.breaker_state()["open"])
        self.assertEqual(self.FS.breaker_state()["fails"], 0)

    def test_env_kill_switch(self):
        self.FS._breaker_record(ValueError("503 Service Unavailable"))
        os.environ["FUBON_BREAKER_DISABLE"] = "1"
        try:
            self.FS._breaker_check()                  # 緊急時人工繞過
        finally:
            os.environ.pop("FUBON_BREAKER_DISABLE")

    def test_corrupt_state_file_never_blocks_trading(self):
        """斷路器自己壞掉,最壞就是回到沒有斷路器的舊行為,絕不能擋住交易。"""
        self.FS._BREAKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.FS._BREAKER_PATH.write_text("{ not json", encoding="utf-8")
        self.FS._breaker_check()
        self.assertEqual(self.FS.breaker_state()["fails"], 0)

    def test_unwritable_state_dir_does_not_raise(self):
        self.FS._BREAKER_PATH = Path("/proc/nonexistent/fubon_breaker.json")
        self.FS._breaker_record(ValueError("503 Service Unavailable"))   # 不該丟
        self.FS._breaker_check()

    def test_exception_carries_retry_after_for_caller_backoff(self):
        self.FS._breaker_record(ValueError("503 Service Unavailable"))
        try:
            self.FS._breaker_check()
            self.fail("should have raised")
        except self.FS.FubonBreakerOpen as e:
            self.assertGreater(e.retry_after, 0)
            self.assertEqual(e.fails, 1)
            self.assertIn("503", e.last_error)

    def test_state_file_is_written_atomically(self):
        self.FS._breaker_record(ValueError("503 Service Unavailable"))
        d = json.loads(self.FS._BREAKER_PATH.read_text(encoding="utf-8"))
        self.assertIn("next_try", d)
        self.assertFalse(self.FS._BREAKER_PATH.with_suffix(".tmp").exists())

    def test_first_fail_at_is_preserved_across_failures(self):
        """故障持續多久,是判斷「是不是又一次 21 小時事故」的依據。"""
        self.FS._breaker_record(ValueError("503 Service Unavailable"))
        t0 = self.FS.breaker_state()["first_fail_at"]
        time.sleep(0.01)
        self.FS._breaker_record(ValueError("503 Service Unavailable"))
        self.assertEqual(self.FS.breaker_state()["first_fail_at"], t0)


if __name__ == "__main__":
    unittest.main()
