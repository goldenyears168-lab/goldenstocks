"""確保某交易日的全市場官方日線(TWSE MI_INDEX + 櫃買)已在 stock_daily_bars。

2026-10 FinMind 不續約後,新日期的收盤價只剩官方日線(source='twse_mi_index' / 'tpex_daily')。
原本它是由 22:45 的 limitup-fade-nightly 順手回補,比 20:05 專家池 digest、20:10 持倉監控都晚;
這裡讓需要當日收盤價的作業自己確保,已有就不重抓(兩支回補腳本本身可重複執行、會跳過已抓日期)。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from stock_db import connect

ROOT = Path(__file__).resolve().parents[1]
MIN_ROWS = 1000        # 一天全市場約 7,900 列(上市約 1,370 + 上櫃約 6,500);低於此視為尚未完整落庫
_SCRIPTS = ("backfill_twse_daily_prices.py", "backfill_tpex_daily_prices.py")


def have_official_bars(day: str, db_path=None) -> bool:
    conn = connect(db_path) if db_path else connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM stock_daily_bars WHERE trade_date = ? AND source IN ('twse_mi_index', 'tpex_daily')",
            (day,),
        ).fetchone()[0]
    finally:
        conn.close()
    return int(n) >= MIN_ROWS


def ensure_official_bars(day: str, *, timeout: float = 900.0, runner=subprocess.run) -> bool:
    """已有就回 True;沒有就跑上市與上櫃兩支回補腳本(各一日)。回傳最後是否已有完整資料。"""
    if have_official_bars(day):
        return True
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for name in _SCRIPTS:
        try:
            runner([sys.executable, str(ROOT / "scripts" / name), "--start", day, "--end", day, "--sleep", "1.5"],
                   env=env, timeout=timeout, check=False, capture_output=True)
        except Exception as exc:  # noqa: BLE001 — 逾時/無法執行:當作尚未取得,呼叫端用『無收盤價』路徑
            print(f"  official bars {day}: {name} 失敗 {exc}")
    return have_official_bars(day)
