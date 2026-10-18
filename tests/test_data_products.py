"""data_products（資料產品宣告與新鮮度判定）：全部用暫存 SQLite 與凍結時鐘，不碰生產 DB。"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import textwrap
import unittest
from datetime import date, datetime
from pathlib import Path

import yaml

from data_products import Calendar, evaluate, load_products

ROOT = Path(__file__).resolve().parents[1]


def _mk_db(bar_days: list[str], extra_tables: dict[str, list[tuple]] | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stock_daily_bars (trade_date TEXT, stock_id TEXT, source TEXT)")
    for d in bar_days:                                    # 每個交易日 600 檔官方日 K（>= 500 的門檻）
        conn.executemany("INSERT INTO stock_daily_bars VALUES (?,?,?)", [(d, f"{i:04d}", "twse_mi_index") for i in range(600)])
    for name, rows in (extra_tables or {}).items():
        conn.execute(f"CREATE TABLE {name} (trade_date TEXT, stock_id TEXT)")
        conn.executemany(f"INSERT INTO {name} VALUES (?,?)", rows)
    return conn


def _doc(products: list[dict], **kw) -> dict:
    return {"defaults": {"ready_after": "22:00", "fresh_td": 0, "dead_td": 3, "status": "active"},
            "products": products, "extra_holidays": kw.get("extra_holidays", [])}


# 2026-10-12(一)~10-16(五) 為交易日；10-19(一) 起另算
WEEK = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


class CalendarTest(unittest.TestCase):
    def test_holiday_before_latest_bar_is_skipped(self):
        # 10-13 沒有官方日 K（且早於最新日 10-16）→ 視為休市日，不算交易日
        cal = Calendar({"2026-10-12", "2026-10-14", "2026-10-15", "2026-10-16"}, [], date(2026, 10, 17))
        self.assertFalse(cal.is_trading(date(2026, 10, 13)))
        self.assertTrue(cal.is_trading(date(2026, 10, 14)))

    def test_weekday_after_latest_bar_counts_as_trading(self):
        # 最新日之後的平日一律當交易日——日線入庫失敗才會被看見，而不是被當成休市
        cal = Calendar(set(WEEK), [], date(2026, 10, 20))
        self.assertTrue(cal.is_trading(date(2026, 10, 19)))
        self.assertFalse(cal.is_trading(date(2026, 10, 17)))     # 週六

    def test_extra_holidays(self):
        cal = Calendar(set(WEEK), ["2026-10-19"], date(2026, 10, 20))
        self.assertFalse(cal.is_trading(date(2026, 10, 19)))

    def test_expected_day_respects_ready_after(self):
        cal = Calendar(set(WEEK), [], date(2026, 10, 16))
        self.assertEqual(cal.expected_day(datetime(2026, 10, 16, 15, 0), "22:00"), date(2026, 10, 15))
        self.assertEqual(cal.expected_day(datetime(2026, 10, 16, 22, 30), "22:00"), date(2026, 10, 16))

    def test_expected_day_on_weekend_is_friday(self):
        cal = Calendar(set(WEEK), [], date(2026, 10, 18))
        self.assertEqual(cal.expected_day(datetime(2026, 10, 18, 12, 0), "22:00"), date(2026, 10, 16))

    def test_age_counts_trading_days_only(self):
        cal = Calendar({"2026-10-12", "2026-10-14", "2026-10-15", "2026-10-16"}, [], date(2026, 10, 17))
        self.assertEqual(cal.age_td(date(2026, 10, 12), date(2026, 10, 16)), 3)    # 13 休市不算
        self.assertEqual(cal.age_td(date(2026, 10, 16), date(2026, 10, 16)), 0)
        self.assertEqual(cal.age_td(date(2026, 10, 17), date(2026, 10, 16)), 0)    # 資料比期望新 → 0


class EvaluateTest(unittest.TestCase):
    NOW = datetime(2026, 10, 17, 12, 0)        # 週六，期望日 = 10-16

    def _run(self, conn, products, **kw):
        tmp = Path(tempfile.mkdtemp())
        return {r.id: r for r in evaluate(_doc(products, **kw), conn, kw.get("now", self.NOW), tmp, tmp)}

    def test_fresh_stale_dead_levels(self):
        conn = _mk_db(WEEK, {
            "t_fresh": [("2026-10-16", "1")], "t_stale": [("2026-10-15", "1")], "t_dead": [("2026-10-09", "1")]})
        ps = [{"id": n, "kind": "sqlite_table", "table": n, "date_col": "trade_date", "producer": "x", "consumers": ["p"]}
              for n in ("t_fresh", "t_stale", "t_dead")]
        r = self._run(conn, ps)
        self.assertEqual(r["t_fresh"].level, "FRESH")
        self.assertEqual(r["t_stale"].level, "STALE")
        self.assertEqual(r["t_dead"].level, "DEAD")
        self.assertTrue(r["t_stale"].violation)

    def test_fresh_td_tolerance(self):
        conn = _mk_db(WEEK, {"t": [("2026-10-15", "1")]})
        r = self._run(conn, [{"id": "t", "kind": "sqlite_table", "table": "t", "date_col": "trade_date",
                              "producer": "x", "consumers": ["p"], "fresh_td": 1}])
        self.assertEqual(r["t"].level, "FRESH")

    def test_orphan_is_not_a_violation(self):
        conn = _mk_db(WEEK, {"t": [("2026-10-09", "1")]})
        r = self._run(conn, [{"id": "t", "kind": "sqlite_table", "table": "t", "date_col": "trade_date",
                              "producer": "x", "consumers": []}])["t"]
        self.assertTrue(r.orphan)
        self.assertFalse(r.violation)
        self.assertTrue(any("無任何消費者" in k for k in r.known))

    def test_dormant_and_retired(self):
        conn = _mk_db(WEEK, {"t": [("2026-08-01", "1")]})
        base = {"kind": "sqlite_table", "table": "t", "date_col": "trade_date", "producer": "manual", "consumers": []}
        r = self._run(conn, [{**base, "id": "d", "status": "dormant"}, {**base, "id": "x", "status": "retired"}])
        self.assertEqual(r["d"].level, "DORMANT")
        self.assertFalse(r["d"].violation)
        self.assertEqual(r["x"].level, "RETIRED")

    def test_missing_table_is_error_not_zero(self):
        conn = _mk_db(WEEK)
        r = self._run(conn, [{"id": "nope", "kind": "sqlite_table", "table": "nope", "date_col": "trade_date",
                              "producer": "x", "consumers": ["p"]}])["nope"]
        self.assertEqual(r.level, "ERROR")
        self.assertTrue(r.violation)

    def test_empty_table_active_is_error(self):
        conn = _mk_db(WEEK, {"t": []})
        r = self._run(conn, [{"id": "t", "kind": "sqlite_table", "table": "t", "date_col": "trade_date",
                              "producer": "x", "consumers": ["p"]}])["t"]
        self.assertEqual(r.level, "ERROR")

    def test_coverage_drop_flags_violation_unless_known(self):
        rows = [(d, f"{i}") for d in WEEK[:-1] for i in range(100)] + [("2026-10-16", f"{i}") for i in range(10)]
        conn = _mk_db(WEEK, {"t": rows})
        base = {"id": "t", "kind": "sqlite_table", "table": "t", "date_col": "trade_date", "key_col": "stock_id",
                "min_rows_ratio": 0.5, "producer": "x", "consumers": ["p"]}
        r = self._run(conn, [base])["t"]
        self.assertTrue(r.notes and r.violation)
        r2 = self._run(conn, [{**base, "known_issue": "來源縮小"}])["t"]
        self.assertFalse(r2.notes)
        self.assertFalse(r2.violation)
        self.assertTrue(any("來源縮小" in k for k in r2.known))

    def test_file_prefixes_and_mtime(self):
        tmp = Path(tempfile.mkdtemp())
        data, repo = tmp / "data", tmp / "repo"
        (data / "chip").mkdir(parents=True)
        (tmp / "cache").mkdir()
        repo.mkdir()
        f1, f2, f3 = data / "chip" / "a.json", tmp / "cache" / "b.json", repo / "c.md"
        for f in (f1, f2, f3):
            f.write_text("x")
            os.utime(f, (datetime(2026, 10, 16, 20, 0).timestamp(),) * 2)
        conn = _mk_db(WEEK)
        ps = [{"id": "a", "kind": "file", "path": "data:chip/a.json", "producer": "x", "consumers": ["p"]},
              {"id": "b", "kind": "file", "path": "root:cache/b.json", "producer": "x", "consumers": ["p"]},
              {"id": "c", "kind": "file", "path": "repo:c.md", "producer": "x", "consumers": ["p"]},
              {"id": "gone", "kind": "file", "path": "data:chip/none.json", "producer": "x", "consumers": ["p"]}]
        res = {r.id: r for r in evaluate(_doc(ps), conn, self.NOW, data, repo)}
        self.assertEqual([res[k].level for k in "abc"], ["FRESH"] * 3)
        self.assertEqual(res["gone"].level, "ERROR")

    def test_manual_cadence_uses_calendar_days(self):
        tmp = Path(tempfile.mkdtemp())
        f = tmp / "m.json"
        f.write_text("x")
        os.utime(f, (datetime(2026, 9, 1, 0, 0).timestamp(),) * 2)
        conn = _mk_db(WEEK)
        p = {"id": "m", "kind": "file", "path": "repo:m.json", "producer": "manual", "consumers": ["p"],
             "cadence": "manual", "max_age_days": 14}
        self.assertEqual(evaluate(_doc([p]), conn, self.NOW, tmp, tmp)[0].level, "STALE")
        self.assertEqual(evaluate(_doc([{**p, "max_age_days": 60}]), conn, self.NOW, tmp, tmp)[0].level, "FRESH")

    def test_ingest_failure_is_visible_not_masked(self):
        # 週一 10-19 日線入庫失敗(bars 只到 10-16)：期望日仍是 10-19，所有產品都該落後
        conn = _mk_db(WEEK, {"t": [("2026-10-16", "1")]})
        r = self._run(conn, [{"id": "t", "kind": "sqlite_table", "table": "t", "date_col": "trade_date",
                              "producer": "x", "consumers": ["p"]}], now=datetime(2026, 10, 19, 23, 0))["t"]
        self.assertEqual(r.expected_day, "2026-10-19")
        self.assertEqual(r.level, "STALE")


class ConfigIntegrityTest(unittest.TestCase):
    """真實的 config/data_products.yaml 靜態檢查（不連 DB）。"""

    @classmethod
    def setUpClass(cls):
        cls.doc = load_products(ROOT / "config" / "data_products.yaml")
        reg = yaml.safe_load((ROOT / "config" / "job_registry.yaml").read_text(encoding="utf-8"))
        jobs = next(v for v in reg.values() if isinstance(v, list))
        cls.labels = {j["label"] for j in jobs if isinstance(j, dict) and "label" in j}

    def test_ids_unique_and_kinds_known(self):
        ids = [p["id"] for p in self.doc["products"]]
        self.assertEqual(len(ids), len(set(ids)))
        for p in self.doc["products"]:
            self.assertIn(p["kind"], ("sqlite_table", "file"), p["id"])

    def test_producer_is_registry_label_or_manual(self):
        bad = [(p["id"], p["producer"]) for p in self.doc["products"]
               if p["producer"] != "manual" and p["producer"] not in self.labels]
        self.assertEqual(bad, [], "producer 必須是 config/job_registry.yaml 的 label 或 manual")

    def test_required_fields(self):
        for p in self.doc["products"]:
            if p["kind"] == "sqlite_table":
                self.assertTrue(p.get("table") and p.get("date_col"), p["id"])
            else:
                self.assertRegex(p["path"], r"^(data|root|repo):", p["id"])
            self.assertIn("consumers", p, f"{p['id']} 必須明寫 consumers（空陣列＝沒人讀）")

    def test_key_col_requires_min_rows_ratio(self):
        for p in self.doc["products"]:
            if p.get("key_col"):
                self.assertIn("min_rows_ratio", p, p["id"])

    def test_known_issue_only_with_coverage_check(self):
        for p in self.doc["products"]:
            if p.get("known_issue"):
                self.assertTrue(p.get("key_col"), p["id"])

    def test_duplicate_ids_rejected(self):
        tmp = Path(tempfile.mkdtemp()) / "x.yaml"
        tmp.write_text(textwrap.dedent("""
            products:
              - {id: a, kind: file, path: "repo:a", producer: manual, consumers: []}
              - {id: a, kind: file, path: "repo:b", producer: manual, consumers: []}
        """), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_products(tmp)


if __name__ == "__main__":
    unittest.main()
