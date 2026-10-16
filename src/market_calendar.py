"""台股交易日曆單一來源（L0）。

交易日 ＝ 該日有 ≥ MIN_OFFICIAL_ROWS 列官方來源日 K（twse_mi_index／tpex_daily／finmind）。
不可用 `SELECT DISTINCT trade_date`：颱風假等休市日會被 yfinance 殭屍列混入（2026-07-10 實例，見記憶 phantom-trading-day-20260710），
也不可用單一個股的日 K（個股缺料、停牌會讓日曆少一天）。唯讀，不寫 DB。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

OFFICIAL_SOURCES = ("twse_mi_index", "tpex_daily", "finmind")
MIN_OFFICIAL_ROWS = 500
_MAX_GAP_DAYS = 20  # 連假最長不超過兩週


def trading_days(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    q = ",".join("?" * len(OFFICIAL_SOURCES))
    return [
        r[0]
        for r in conn.execute(
            f"SELECT trade_date FROM stock_daily_bars WHERE trade_date BETWEEN ? AND ? AND source IN ({q}) "
            "GROUP BY trade_date HAVING COUNT(*) >= ? ORDER BY trade_date",
            (start, end, *OFFICIAL_SOURCES, MIN_OFFICIAL_ROWS),
        )
    ]


def _shift(d: str, days: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def latest_trading_day(conn: sqlite3.Connection, upto: str) -> str | None:
    days = trading_days(conn, _shift(upto, -_MAX_GAP_DAYS), upto)
    return days[-1] if days else None


def prev_trading_day(conn: sqlite3.Connection, d: str) -> str | None:
    return latest_trading_day(conn, _shift(d, -1))


def next_trading_day(conn: sqlite3.Connection, d: str) -> str | None:
    days = trading_days(conn, _shift(d, 1), _shift(d, _MAX_GAP_DAYS))
    return days[0] if days else None


def calendar_for(conn: sqlite3.Connection, asof: str) -> dict:
    """資料日與其前後交易日；next 在隔日官方日 K 入庫前為 None。"""
    return {"asof": asof, "prev": prev_trading_day(conn, asof), "next": next_trading_day(conn, asof)}
