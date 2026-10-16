"""Supabase PostgREST 共用工具（設定判斷、headers、排程推送守衛）。

自 supabase_research_sync 抽出（2026-10 Supabase 清理）；僅供仍殘留的
lens／rrg_universe／strategy_performance 寫入段使用，這三者移除後本檔一併刪除。
Supabase 已於 2026-08-17 停用，預設旗標皆為 0。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

from market_benchmark import is_trading_date, is_trading_session_day

_TPE = ZoneInfo("Asia/Taipei")
_SUPABASE_SCHEMA = "stock_research"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _supabase_url() -> str:
    return _env("SUPABASE_URL") or _env("VITE_PUBLIC_SUPABASE_URL")


def supabase_configured() -> bool:
    return bool(_supabase_url() and _env("SUPABASE_SERVICE_ROLE_KEY"))


def _headers() -> dict[str, str]:
    key = _env("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY 未設定（見 .env.example）")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
        "Accept-Profile": _SUPABASE_SCHEMA,
        "Content-Profile": _SUPABASE_SCHEMA,
    }


def _today_tpe() -> date:
    return datetime.now(_TPE).date()


def allow_scheduled_supabase_push(
    conn: sqlite3.Connection,
    *,
    scheduled: bool = True,
    intraday: bool = False,
) -> bool:
    """排程推送僅在台股交易日執行；backfill 傳 scheduled=False。

    intraday=True（1300 slot）：盤中 TEJ 日線尚未落庫時仍允許推送。
    """
    if not scheduled:
        return True
    today = _today_tpe()
    if intraday:
        return is_trading_session_day(conn, today)
    return is_trading_date(conn, today)
