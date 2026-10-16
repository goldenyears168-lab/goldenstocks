"""盤中自選股 K 線同步（Yahoo 5m → stock_kbar_5m）。

自 rrg_mono_swap_accel_screen（2026-10 移除）逐字搬出；holdings_pulse 仍使用。
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import date

from stock_db.kbar import (
    kbar_5m_fresh_for_poll,
    upsert_1m_rows_as_5m,
    upsert_stock_kbar_1m,
    upsert_yahoo_5m_rows,
)
from yahoo_chart_sync import fetch_tw_intraday_kbar_rows


def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no")


def sync_watchlist_kbar(
    conn: sqlite3.Connection,
    stock_ids: list[str],
    trade_date: str,
    *,
    env_key: str = "C18ACC_KBAR_SYNC",
    sleep_sec: float = 0.35,
    poll_minute: str | None = None,
    skip_fresh: bool | None = None,
) -> int:
    """主線盤中 K sync · Yahoo 原生 5m 寫入 stock_kbar_5m。

    ``poll_minute`` 有值且 ``skip_fresh`` 時，若 DB 已有覆蓋該格點的 5m bar 則跳過 API
    （env ``KBAR_SYNC_SKIP_FRESH`` 預設 1）。``KBAR_SYNC_YAHOO_INTERVAL`` 預設 ``5m``；
    設 ``1m`` 可回退舊路徑（聚合寫入）。設 ``KBAR_SYNC_STORE_1M=1`` 會**額外**拉 1m
    寫入 stock_kbar_1m（research / extension）。
    """
    if not _env_flag(env_key, "1"):
        return 0
    if skip_fresh is None:
        skip_fresh = os.environ.get("KBAR_SYNC_SKIP_FRESH", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
    store_1m = os.environ.get("KBAR_SYNC_STORE_1M", "0").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    yahoo_interval = os.environ.get("KBAR_SYNC_YAHOO_INTERVAL", "5m").strip().lower()
    if yahoo_interval not in ("5m", "1m"):
        yahoo_interval = "5m"
    d = date.fromisoformat(trade_date)
    total = 0
    for sid in sorted({str(x) for x in stock_ids if x}):
        if skip_fresh and poll_minute and kbar_5m_fresh_for_poll(
            conn, sid, trade_date, poll_minute
        ):
            continue
        try:
            if yahoo_interval == "5m":
                rows_5m, _ = fetch_tw_intraday_kbar_rows(sid, d, d, interval="5m")
                if rows_5m:
                    total += upsert_yahoo_5m_rows(conn, sid, rows_5m)
            else:
                rows_1m, _ = fetch_tw_intraday_kbar_rows(sid, d, d, interval="1m")
                if rows_1m:
                    total += upsert_1m_rows_as_5m(conn, sid, rows_1m)
                    if store_1m:
                        total += upsert_stock_kbar_1m(conn, rows_1m)
            if store_1m and yahoo_interval == "5m":
                rows_1m, _ = fetch_tw_intraday_kbar_rows(sid, d, d, interval="1m")
                if rows_1m:
                    total += upsert_stock_kbar_1m(conn, rows_1m)
        except Exception:
            continue
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return total
