"""同步台指 VIX（VIXTWN, 期交所官方逐日檔）與美盤 ^VIX（Yahoo）到 market_vix_daily。

2026-10 起 VIXTWN 來源由 FinMind TaiwanOptionVix 改為期交所 `getVixData`(同一份 15 秒逐筆,
開高低收與 n_ticks 與 FinMind 逐日相同),source='taifex';只補資料庫缺的交易日。

背景
----
`market_vix_daily` 原本是先前臨時灌入、沒有留下腳本的表（不在 stock_db/_schema.py，
Mac mini 上甚至沒有這張表）。本腳本補上正式、可排程的 ingest：

* VIXTWN：期交所 ``getVixData``（date/time/vix，日盤 15 秒 tick）→ 聚合成日 OHLC，
  open=當日最早、close=當日最晚、high/low=當日極值、n_ticks=當日筆數。source='taifex'。
  （歷史 source='finmind' 列自 2026-03-01 起，保留不動。）
* VIX（美盤 ^VIX）：Yahoo 日線 OHLC。source='yahoo'。

刻意「自帶 CREATE TABLE IF NOT EXISTS」而不改 stock_db/_schema.py + SCHEMA_VERSION，
因為 bump 版本會連帶 re-run ensure_copytrade_schema（會把已退役的 copytrade 表重建）。

用法
----
    PYTHONPATH=src .venv/bin/python src/sync_vixtwn.py            # 近 7 日，寫入
    PYTHONPATH=src .venv/bin/python src/sync_vixtwn.py --days 30
    PYTHONPATH=src .venv/bin/python src/sync_vixtwn.py --dry-run  # 只抓不寫，印摘要
    PYTHONPATH=src .venv/bin/python src/sync_vixtwn.py --vixtwn-only
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta

import time

import taifex_client
from stock_db import DATA_DIR, connect
from stock_db.util import utc_now_iso

US_VIX_SYMBOL = "^VIX"
ARCHIVE_DIR = DATA_DIR.parent / "cache" / "taifex_vix"   # 期交所原始檔歸檔(來源只留約 4 個月)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS market_vix_daily(
  symbol TEXT NOT NULL, date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL NOT NULL,
  n_ticks INTEGER, source TEXT NOT NULL DEFAULT 'finmind',
  synced_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(symbol,date,source));
CREATE INDEX IF NOT EXISTS idx_vix_date ON market_vix_daily(date DESC, symbol);
"""

_UPSERT_SQL = """
INSERT INTO market_vix_daily (symbol, date, open, high, low, close, n_ticks, source, synced_at)
VALUES (:symbol, :date, :open, :high, :low, :close, :n_ticks, :source, :synced_at)
ON CONFLICT(symbol, date, source) DO UPDATE SET
    open=excluded.open, high=excluded.high, low=excluded.low,
    close=excluded.close, n_ticks=excluded.n_ticks, synced_at=excluded.synced_at
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(_CREATE_SQL)
    conn.commit()


def upsert_vix_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    synced_at = utc_now_iso()
    payload = [{**r, "synced_at": synced_at} for r in rows]
    conn.executemany(_UPSERT_SQL, payload)
    conn.commit()
    return len(payload)


def existing_vixtwn_dates(conn: sqlite3.Connection, start: date, end: date) -> set[str]:
    """資料庫已有的 VIXTWN 日期(任何來源,含 computed 備援);已有就不再向期交所重抓。"""
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM market_vix_daily WHERE symbol='VIXTWN' AND source IN ('finmind','taifex') "
                "AND date >= ? AND date <= ?",
                (start.isoformat(), end.isoformat()),
            )
        }
    except sqlite3.OperationalError:
        return set()


def fetch_vixtwn_daily(start: date, end: date, skip_dates: set[str] | None = None, delay: float = 1.2) -> list[dict]:
    """期交所 VIXTWN 逐日檔（15 秒 tick）→ 聚合成日 OHLC list[dict]。休市日(無檔)略過。"""
    skip = skip_dates or set()
    data = []
    day = start
    while day <= end:
        if day.weekday() < 5 and day.isoformat() not in skip:
            ticks = taifex_client.fetch_vix_ticks(day, archive_dir=ARCHIVE_DIR)
            if ticks:
                data.extend(ticks)
            time.sleep(delay)
        day += timedelta(days=1)
    # 依日期分組（data 已含 date/time/vix）
    by_date: dict[str, list[tuple[str, float]]] = {}
    for r in data:
        d = r.get("date")
        v = r.get("vix")
        if d is None or v is None:
            continue
        by_date.setdefault(d, []).append((r.get("time") or "", float(v)))
    rows: list[dict] = []
    for d, ticks in by_date.items():
        ticks.sort(key=lambda t: t[0])  # 依 time 排序
        vals = [v for _, v in ticks]
        rows.append(
            {
                "symbol": "VIXTWN",
                "date": d,
                "open": vals[0],
                "high": max(vals),
                "low": min(vals),
                "close": vals[-1],
                "n_ticks": len(vals),
                "source": "taifex",
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_us_vix_daily(start: date, end: date) -> list[dict]:
    """Yahoo ^VIX 日線 OHLC → list[dict]。"""
    import yfinance as yf

    df = yf.download(
        US_VIX_SYMBOL,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        progress=False,
        auto_adjust=False,
    )
    if df is None or df.empty:
        return []
    # yfinance 可能回傳 MultiIndex 欄位（單一 ticker 時）
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    rows: list[dict] = []
    for idx, r in df.iterrows():
        try:
            close = float(r["Close"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(
            {
                "symbol": "VIX",
                "date": idx.date().isoformat(),
                "open": _f(r.get("Open")),
                "high": _f(r.get("High")),
                "low": _f(r.get("Low")),
                "close": close,
                "n_ticks": None,
                "source": "yahoo",
            }
        )
    return rows


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _summary(label: str, rows: list[dict]) -> str:
    if not rows:
        return f"  {label}: 0 筆"
    lo, hi = rows[0]["date"], rows[-1]["date"]
    last = rows[-1]
    return (
        f"  {label}: {len(rows)} 筆 [{lo}~{hi}] "
        f"最新 close={last['close']:.2f}"
        + (f" n_ticks={last['n_ticks']}" if last.get("n_ticks") else "")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="同步 VIXTWN + 美盤 VIX 到 market_vix_daily")
    ap.add_argument("--days", type=int, default=7, help="回溯天數（預設 7）")
    ap.add_argument("--dry-run", action="store_true", help="只抓不寫,印摘要")
    ap.add_argument("--vixtwn-only", action="store_true", help="只同步 VIXTWN")
    ap.add_argument("--us-only", action="store_true", help="只同步美盤 ^VIX")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    print(f"=== sync_vixtwn {start} ~ {end} (dry_run={args.dry_run}) ===")

    vixtwn_rows: list[dict] = []
    us_rows: list[dict] = []
    failed = False
    if not args.us_only:                      # 兩邊各自 try:一邊失敗不拖累另一邊(舊版 FinMind 失敗會連 Yahoo 一起丟掉)
        try:
            conn = connect()
            try:
                skip = existing_vixtwn_dates(conn, start, end)
            finally:
                conn.close()
            vixtwn_rows = fetch_vixtwn_daily(start, end, skip)
            print(_summary("VIXTWN(taifex)", vixtwn_rows))
        except Exception as exc:  # noqa: BLE001 — 抓取失敗要清楚回報
            print(f"!! VIXTWN 抓取失敗: {exc}", file=sys.stderr)
            failed = True
    if not args.vixtwn_only:
        try:
            us_rows = fetch_us_vix_daily(start, end)
            print(_summary("VIX(yahoo)", us_rows))
        except Exception as exc:  # noqa: BLE001
            print(f"!! 美盤 VIX 抓取失敗: {exc}", file=sys.stderr)
            failed = True
    if failed and not (vixtwn_rows or us_rows):
        return 2

    if args.dry_run:
        print("dry-run:未寫入。")
        return 0

    conn = connect()
    try:
        ensure_table(conn)
        n1 = upsert_vix_rows(conn, vixtwn_rows)
        n2 = upsert_vix_rows(conn, us_rows)
    finally:
        conn.close()
    print(f"寫入完成:VIXTWN {n1} 筆、VIX {n2} 筆 → market_vix_daily")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
