"""資料產品宣告與新鮮度判定（read-only · 不連網 · 不寫 DB）。

SSOT：`config/data_products.yaml`。每個資料產品（SQLite 表或檔案）宣告 producer / consumers /
新鮮度門檻；本模組只負責讀宣告、量測、判級，不決定『要不要告警』（由 scripts/ops/check_data_products.py 決定）。

判級以『交易日』計，不以日曆日計：
  FRESH   資料落後期望日 <= fresh_td
  STALE   落後 <= dead_td
  DEAD    落後 > dead_td
  DORMANT 宣告為 dormant（停更、是否退役待決）——只報告，不算違規
  RETIRED 宣告為 retired——略過
  ERROR   讀不到（表/檔不存在、欄位錯）——一律視為『無資料』而非 0

期望日（expected day）不依賴 stock_daily_bars 自己的最新日，否則日線入庫失敗時所有產品都會
『相對新鮮』。做法：以週一至週五為底，扣掉『已知休市日』——也就是 stock_daily_bars 最新日以前、
沒有官方日 K 的平日，加上宣告檔的 extra_holidays。最新日之後的平日一律視為交易日，所以日線
入庫失敗會被看見。限制：週一遇連假的『當天』在日線入庫前會被當成交易日而短暫誤報 STALE。
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

OFFICIAL_SOURCES = ("twse_mi_index", "tpex_daily")  # 與 market_calendar 一致；僅用來判定休市日
_MIN_OFFICIAL_ROWS = 500
_LOOKBACK_DAYS = 60


@dataclass
class Result:
    id: str
    level: str                       # FRESH/STALE/DEAD/DORMANT/RETIRED/ERROR
    data_day: str | None = None
    expected_day: str | None = None
    age_td: int | None = None
    notes: list[str] = field(default_factory=list)       # 需要人看的問題（算違規）
    known: list[str] = field(default_factory=list)       # 已知且接受的問題（不算違規）
    consumers: list[str] = field(default_factory=list)
    orphan: bool = False             # 宣告為 active 但沒有任何消費者：停更不影響功能，改列『待決』而非違規

    @property
    def violation(self) -> bool:
        return (self.level in ("STALE", "DEAD", "ERROR") and not self.orphan) or bool(self.notes)


def load_products(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    doc.setdefault("defaults", {})
    doc.setdefault("products", [])
    doc.setdefault("extra_holidays", [])
    ids = [p["id"] for p in doc["products"]]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"data_products.yaml 有重複 id: {sorted(dup)}")
    return doc


def _weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class Calendar:
    """以週一至週五扣已知休市日估計交易日（見模組說明的限制）。"""

    def __init__(self, bar_days: set[str], extra_holidays: list[str], today: date):
        self.bar_days = bar_days
        self.latest_bar = max(bar_days) if bar_days else None
        self.extra = {str(x) for x in extra_holidays}
        self.today = today

    def is_trading(self, d: date) -> bool:
        if d.weekday() >= 5 or d.isoformat() in self.extra:
            return False
        if self.latest_bar and d.isoformat() < self.latest_bar and d.isoformat() not in self.bar_days:
            return False                       # 最新日以前、沒有官方日 K 的平日 = 休市日
        return True

    def trading_days(self, start: date, end: date) -> list[date]:
        return [d for d in _weekdays(start, end) if self.is_trading(d)]

    def expected_day(self, now: datetime, ready_after: str) -> date:
        hh, mm = (int(x) for x in ready_after.split(":"))
        d = now.date()
        if not (self.is_trading(d) and now.time() >= now.time().replace(hour=hh, minute=mm, second=0, microsecond=0)):
            d -= timedelta(days=1)
        while not self.is_trading(d):
            d -= timedelta(days=1)
        return d

    def age_td(self, data_day: date, expected: date) -> int:
        if data_day >= expected:
            return 0
        return len(self.trading_days(data_day + timedelta(days=1), expected))


def build_calendar(conn: sqlite3.Connection, extra_holidays: list[str], today: date) -> Calendar:
    q = ",".join("?" * len(OFFICIAL_SOURCES))
    start = (today - timedelta(days=_LOOKBACK_DAYS)).isoformat()
    rows = conn.execute(
        f"SELECT trade_date FROM stock_daily_bars WHERE trade_date >= ? AND source IN ({q}) "
        "GROUP BY trade_date HAVING COUNT(*) >= ?",
        (start, *OFFICIAL_SOURCES, _MIN_OFFICIAL_ROWS),
    ).fetchall()
    return Calendar({r[0] for r in rows}, extra_holidays, today)


def _table_max_and_coverage(conn: sqlite3.Connection, p: dict) -> tuple[str | None, dict | None]:
    t, dc = p["table"], p["date_col"]
    mx = conn.execute(f'SELECT MAX("{dc}") FROM "{t}"').fetchone()[0]
    if mx is None:
        return None, None
    key = p.get("key_col")
    if not key:
        return str(mx)[:10], None
    n_latest = conn.execute(f'SELECT COUNT(DISTINCT "{key}") FROM "{t}" WHERE "{dc}" = ?', (mx,)).fetchone()[0]
    prev_days = [r[0] for r in conn.execute(
        f'SELECT DISTINCT "{dc}" FROM "{t}" WHERE "{dc}" < ? ORDER BY "{dc}" DESC LIMIT 10', (mx,))]
    counts = [conn.execute(f'SELECT COUNT(DISTINCT "{key}") FROM "{t}" WHERE "{dc}" = ?', (d,)).fetchone()[0]
              for d in prev_days]
    med = statistics.median(counts) if counts else None
    return str(mx)[:10], {"latest": n_latest, "median_prev10": med}


def _file_day(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def evaluate(doc: dict, conn: sqlite3.Connection, now: datetime, data_dir: Path, repo_dir: Path) -> list[Result]:
    cal = build_calendar(conn, doc.get("extra_holidays", []), now.date())
    dflt = doc["defaults"]
    results: list[Result] = []
    for raw in doc["products"]:
        p = {**dflt, **raw}
        r = Result(id=p["id"], level="FRESH", consumers=list(p.get("consumers") or []))
        status = p.get("status", "active")
        if status == "retired":
            r.level = "RETIRED"
            results.append(r)
            continue
        try:
            cov = None
            if p["kind"] == "sqlite_table":
                day, cov = _table_max_and_coverage(conn, p)
            elif p["kind"] == "file":
                prefix, rel = p["path"].split(":", 1)
                base = {"data": data_dir, "root": data_dir.parent, "repo": repo_dir}[prefix]
                day = _file_day(base / rel)
            else:
                raise ValueError(f"未知 kind {p['kind']!r}")
        except Exception as e:  # noqa: BLE001 — 讀不到一律回 ERROR（視為無資料而非 0）
            r.level = "ERROR"
            r.notes.append(f"讀取失敗: {type(e).__name__}: {e}")
            results.append(r)
            continue
        if day is None:
            r.level = "DORMANT" if status == "dormant" else "ERROR"
            r.notes.append("無任何資料列/檔案不存在") if r.level == "ERROR" else r.known.append("無資料")
            results.append(r)
            continue
        r.data_day = day
        d = date.fromisoformat(day)
        if p.get("cadence", "trading_day") == "manual":
            age_days = (now.date() - d).days
            limit = int(p.get("max_age_days", 14))
            r.expected_day = f"<= {limit} 天內"
            r.age_td = age_days
            r.level = "FRESH" if age_days <= limit else "STALE"
        else:
            exp = cal.expected_day(now, p.get("ready_after", "22:00"))
            r.expected_day = exp.isoformat()
            r.age_td = cal.age_td(d, exp)
            r.level = ("FRESH" if r.age_td <= int(p.get("fresh_td", 0))
                       else "STALE" if r.age_td <= int(p.get("dead_td", 3)) else "DEAD")
        if status == "active" and not r.consumers and r.level in ("STALE", "DEAD"):
            r.orphan = True
            r.known.append(f"無任何消費者(落後 {r.age_td} 個交易日)：退役，或補上消費者——待決")
        if status == "dormant":
            r.level = "DORMANT"
            r.known.append(f"停更中(落後 {r.age_td} 個交易日),是否退役待決")
        if cov and cov["median_prev10"]:
            ratio = cov["latest"] / cov["median_prev10"]
            floor = float(p.get("min_rows_ratio", 0.0))
            if ratio < floor:
                msg = f"覆蓋率 {cov['latest']}/{cov['median_prev10']:.0f}={ratio:.0%} < {floor:.0%}"
                (r.known if p.get("known_issue") else r.notes).append(
                    msg + (f"(已知:{p['known_issue']})" if p.get("known_issue") else ""))
        results.append(r)
    return results
