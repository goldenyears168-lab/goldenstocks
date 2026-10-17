"""sleeve_infra_healthcheck 的判定邏輯測試。

這支健檢是要取代大部分 AI 盤中監看的，所以它自己的判定必須被釘住——尤其
「log 檔名日期不是今天卻仍在被寫入」這條，是 2026-08-19 修掉的 STAMP 靜態化
bug 的回歸偵測；它一旦失效，同一個缺陷會再一次無聲無息地跑好幾天。
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "ops" / "sleeve_infra_healthcheck.py"
TZ = ZoneInfo("Asia/Taipei")


def _load_module():
    spec = importlib.util.spec_from_file_location("sleeve_infra_healthcheck", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hc = _load_module()


class WindowTest(unittest.TestCase):
    def test_weekday_inside_window(self) -> None:
        # 2026-08-19 是週三
        now = datetime(2026, 8, 19, 9, 30, tzinfo=TZ)
        self.assertTrue(hc._in_window(now, ("08:44", "13:41")))

    def test_weekday_outside_window(self) -> None:
        now = datetime(2026, 8, 19, 14, 30, tzinfo=TZ)
        self.assertFalse(hc._in_window(now, ("08:44", "13:41")))

    def test_weekend_is_never_in_window(self) -> None:
        # 2026-08-22 是週六；即使時間落在盤中也不算
        now = datetime(2026, 8, 22, 9, 30, tzinfo=TZ)
        self.assertFalse(hc._in_window(now, ("08:44", "13:41")))

    def test_window_boundaries_are_inclusive(self) -> None:
        for hhmm in ("08:44", "13:41"):
            h, m = (int(x) for x in hhmm.split(":"))
            now = datetime(2026, 8, 19, h, m, tzinfo=TZ)
            self.assertTrue(hc._in_window(now, ("08:44", "13:41")), hhmm)


class FindingsTest(unittest.TestCase):
    def test_exit_code_precedence(self) -> None:
        f = hc.Findings()
        self.assertEqual(f.exit_code, 0)
        f.add("WARN", "c", "s", "d")
        self.assertEqual(f.exit_code, 1)
        f.add("FAIL", "c", "s", "d")
        self.assertEqual(f.exit_code, 2, "FAIL 必須壓過 WARN")


class FailClosedArtifactTest(unittest.TestCase):
    """這兩份檔案過期時閘門會靜默排除所有候選，外觀跟『今天沒訊號』一樣。"""

    def _run_with_dir(self, tmp: Path) -> list[dict]:
        original = hc.ARTIFACT_DIR
        hc.ARTIFACT_DIR = tmp
        try:
            f = hc.Findings()
            hc.check_failclosed_artifacts(f, datetime(2026, 8, 19, 9, 0, tzinfo=TZ))
            return f.items
        finally:
            hc.ARTIFACT_DIR = original

    def test_missing_files_are_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            items = self._run_with_dir(Path(tmp))
            subjects = {i["subject"] for i in items}
            self.assertIn("futures_daily_cache.json", subjects)
            self.assertIn("pit_seat_flip_latest.json", subjects)
            self.assertTrue(all(i["level"] == "FAIL" for i in items))

    def test_fresh_artifacts_produce_no_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "futures_daily_cache.json").write_text(
                json.dumps({"2330": {"2026-08-18": [0, 0, 0, 0, 1200]}})
            )
            (d / "pit_seat_flip_latest.json").write_text(
                json.dumps({"computed_at": "2026-08-14", "values": {"9217": 0.55}})
            )
            self.assertEqual(self._run_with_dir(d), [])

    def test_stale_pit_table_is_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "futures_daily_cache.json").write_text(
                json.dumps({"2330": {"2026-08-18": [0, 0, 0, 0, 1200]}})
            )
            # computed_at 距 2026-08-19 有 30 天 > _PIT_FLIP_MAX_AGE_DAYS(14)
            (d / "pit_seat_flip_latest.json").write_text(
                json.dumps({"computed_at": "2026-07-20", "values": {"9217": 0.55}})
            )
            items = self._run_with_dir(d)
            self.assertTrue(
                any("pit_seat_flip" in i["subject"] and i["level"] == "FAIL" for i in items)
            )

    def test_no_seat_passes_threshold_is_fail(self) -> None:
        """表在、也夠新，但沒有任何席位過門檻＝等同全部排除，一樣要叫。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "futures_daily_cache.json").write_text(
                json.dumps({"2330": {"2026-08-18": [0, 0, 0, 0, 1200]}})
            )
            (d / "pit_seat_flip_latest.json").write_text(
                json.dumps({"computed_at": "2026-08-14", "values": {"9217": 0.10}})
            )
            items = self._run_with_dir(d)
            self.assertTrue(any("沒有任何席位" in i["detail"] for i in items))

    def test_stale_futures_cache_is_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "futures_daily_cache.json").write_text(
                json.dumps({"2330": {"2026-07-01": [0, 0, 0, 0, 1200]}})
            )
            (d / "pit_seat_flip_latest.json").write_text(
                json.dumps({"computed_at": "2026-08-14", "values": {"9217": 0.55}})
            )
            items = self._run_with_dir(d)
            self.assertTrue(
                any("futures_daily_cache" in i["subject"] and i["level"] == "FAIL"
                    for i in items)
            )


class KillSwitchTest(unittest.TestCase):
    def _run(self, tmp: Path, now: datetime) -> list[dict]:
        class _Stub:
            DATA_DIR = tmp
        original = hc.stock_db
        hc.stock_db = _Stub  # type: ignore[assignment]
        try:
            f = hc.Findings()
            hc.check_kill_switches(f, now)
            return f.items
        finally:
            hc.stock_db = original

    def test_today_kill_switch_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "order"
            d.mkdir()
            (d / "momentum_rotation_kill_switch.json").write_text(
                json.dumps({"date": "2026-08-19", "reason": "測試理由"})
            )
            items = self._run(Path(tmp), datetime(2026, 8, 19, 9, 0, tzinfo=TZ))
            self.assertEqual(len(items), 1)
            self.assertIn("測試理由", items[0]["detail"])

    def test_old_kill_switch_file_warns_to_clean_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "order"
            d.mkdir()
            (d / "x_kill_switch.json").write_text(json.dumps({"date": "2026-08-01"}))
            items = self._run(Path(tmp), datetime(2026, 8, 19, 9, 0, tzinfo=TZ))
            self.assertTrue(any("還在" in i["detail"] for i in items))

    def test_recent_but_not_today_is_quiet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "order"
            d.mkdir()
            (d / "x_kill_switch.json").write_text(json.dumps({"date": "2026-08-18"}))
            self.assertEqual(self._run(Path(tmp), datetime(2026, 8, 19, 9, 0, tzinfo=TZ)), [])


if __name__ == "__main__":
    unittest.main()
