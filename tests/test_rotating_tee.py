"""rotating_tee 的行為測試 —— 釘住 2026-08-19 修掉的 log 日期輪替 bug。

這支 helper 站在每個 KeepAlive worker 的 stdout 管線上，壞掉會直接讓盤中 log 消失，
所以「檔案寫不進去也不能拋例外」這條要有測試守著。
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "launchd" / "rotating_tee.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("rotating_tee", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rotating_tee = _load_module()


class TargetDateTest(unittest.TestCase):
    def test_calendar_mode_ignores_hour(self) -> None:
        for hour in (0, 3, 12, 23):
            now = _dt.datetime(2026, 8, 19, hour, 30)
            self.assertEqual(
                rotating_tee._target_date(now, 0),
                _dt.date(2026, 8, 19),
                f"rollover_hour=0 時 {hour}:30 應維持日曆日",
            )

    def test_rollover_pushes_early_morning_to_previous_day(self) -> None:
        # 夜盤 15:00 開到隔日 05:00：凌晨 03:00 的輸出應歸前一天
        now = _dt.datetime(2026, 8, 19, 3, 0)
        self.assertEqual(
            rotating_tee._target_date(now, 5), _dt.date(2026, 8, 18)
        )

    def test_rollover_boundary_is_exclusive(self) -> None:
        # 正好 05:00 已經是新的一天，不再位移
        now = _dt.datetime(2026, 8, 19, 5, 0)
        self.assertEqual(
            rotating_tee._target_date(now, 5), _dt.date(2026, 8, 19)
        )

    def test_rollover_crosses_month_boundary(self) -> None:
        now = _dt.datetime(2026, 9, 1, 2, 0)
        self.assertEqual(
            rotating_tee._target_date(now, 5), _dt.date(2026, 8, 31)
        )


class RotatingWriterTest(unittest.TestCase):
    def test_writes_to_dated_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "dayflip_short"
            writer = rotating_tee._RotatingWriter(prefix, 0)
            writer.write("line-a\n", _dt.datetime(2026, 8, 19, 9, 0))
            writer.close()
            target = Path(tmp) / "dayflip_short_20260819.log"
            self.assertTrue(target.exists(), "應建立以日期命名的檔案")
            self.assertEqual(target.read_text(encoding="utf-8"), "line-a\n")

    def test_rotates_when_day_changes(self) -> None:
        """這就是原始 bug：同一個長駐行程跨日後必須換檔。"""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "tmf_channel_live"
            writer = rotating_tee._RotatingWriter(prefix, 0)
            writer.write("day1\n", _dt.datetime(2026, 8, 18, 23, 59))
            writer.write("day2\n", _dt.datetime(2026, 8, 19, 0, 1))
            writer.close()
            first = Path(tmp) / "tmf_channel_live_20260818.log"
            second = Path(tmp) / "tmf_channel_live_20260819.log"
            self.assertEqual(first.read_text(encoding="utf-8"), "day1\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "day2\n")

    def test_appends_to_existing_file(self) -> None:
        """worker 重啟後同一天要接續寫，不能截斷既有內容。"""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "momentum_rotation"
            existing = Path(tmp) / "momentum_rotation_20260819.log"
            existing.write_text("earlier\n", encoding="utf-8")
            writer = rotating_tee._RotatingWriter(prefix, 0)
            writer.write("later\n", _dt.datetime(2026, 8, 19, 10, 0))
            writer.close()
            self.assertEqual(
                existing.read_text(encoding="utf-8"), "earlier\nlater\n"
            )

    def test_unwritable_path_does_not_raise(self) -> None:
        """log 寫不進去絕不能殺掉 worker —— 只降級成 stderr 警告。"""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocked"
            blocker.write_text("i am a file, not a directory\n", encoding="utf-8")
            prefix = blocker / "nested" / "worker"  # mkdir 必然失敗
            writer = rotating_tee._RotatingWriter(prefix, 0)
            writer.write("should-not-raise\n", _dt.datetime(2026, 8, 19, 9, 0))
            writer.close()

    def test_creates_missing_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "logs" / "intraday" / "futopt_books_collect"
            writer = rotating_tee._RotatingWriter(prefix, 0)
            writer.write("x\n", _dt.datetime(2026, 8, 19, 9, 0))
            writer.close()
            self.assertTrue(
                (prefix.parent / "futopt_books_collect_20260819.log").exists()
            )


class EndToEndTest(unittest.TestCase):
    def test_pipeline_forwards_stdout_and_writes_file(self) -> None:
        """維持 tee 語義：stdout 要照樣輸出，launchd StandardOutPath 才收得到。"""
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "probe"
            proc = subprocess.run(
                [sys.executable, str(_SCRIPT_PATH), str(prefix)],
                input="alpha\nbeta\n",
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "alpha\nbeta\n", "stdout 必須原樣轉發")
            today = _dt.date.today().strftime("%Y%m%d")
            written = Path(tmp) / f"probe_{today}.log"
            self.assertEqual(written.read_text(encoding="utf-8"), "alpha\nbeta\n")

    def test_rejects_out_of_range_rollover_hour(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "/tmp/x", "--rollover-hour", "24"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
