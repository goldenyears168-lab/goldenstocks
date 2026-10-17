#!/usr/bin/env python3
"""補齊 stock_block_trade（TWSE 鉅額交易, FinMind ``TaiwanStockBlockTrade``）。

先前完全是空表：daily ``sync_stock_sponsor_daily.py`` 依賴 ``pm_watchlist``
（Score Engine 產物）解析 top-N 名單，該表最新一筆已停在 2026-06-18、
``SCORE_VERSION`` 對不上時 ``resolve_top_stock_ids`` 回空清單，
main() 直接印 "無 pm_watchlist" 結束，從未真正呼叫過 sync。此外
``parse_block_trade_rows`` 原本讀 ``amount``/``Amount`` 欄位，但 FinMind
實際回傳的金額欄位是 ``trading_money``（已在 sync_stock_sponsor_daily.py 修正）。

這支腳本繞過 pm_watchlist 依賴，直接對指定股票清單一次拉全部歷史
（單一 request 涵蓋整個日期區間即可，不需分段；2330 實測 2015-01-05
至今 13,199 列一次拿到）。

用法::

    PYTHONPATH=src .venv/bin/python scripts/backfill_stock_block_trade.py \\
        --universe liquid --start 2015-01-01 --sync-db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finmind_client import fetch_finmind  # noqa: E402
from stock_db import DEFAULT_DB_PATH, connect, upsert_stock_block_trade  # noqa: E402
from sync_stock_sponsor_daily import parse_block_trade_rows  # noqa: E402

REQUEST_DELAY_SEC = 0.5
LIQUIDITY_FLOOR_NTD = 30_000_000  # 同 VSA absorption 研究口徑


def liquid_universe(db_path: Path, floor: float = LIQUIDITY_FLOOR_NTD) -> list[str]:
    """finmind 日線、中位成交金額 >= floor、排除 00 開頭(ETF)。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT stock_id, amount, volume, close
            FROM stock_daily_bars
            WHERE source='finmind' AND stock_id NOT LIKE '00%'
              AND close > 0 AND volume > 0
            """
        ).fetchall()
    finally:
        conn.close()
    by_stock: dict[str, list[float]] = {}
    for stock_id, amount, volume, close in rows:
        amt = amount if amount is not None else volume * close
        by_stock.setdefault(stock_id, []).append(amt)
    import statistics

    keep = [sid for sid, amts in by_stock.items() if statistics.median(amts) >= floor]
    return sorted(keep)


def backfill(
    db_path: Path,
    stock_ids: list[str],
    start: date,
    end: date,
    *,
    dry_run: bool,
    request_delay: float,
) -> None:
    total_rows = 0
    ok = warn = 0
    for i, stock_id in enumerate(stock_ids, 1):
        if i > 1 and request_delay > 0:
            time.sleep(request_delay)
        try:
            raw = fetch_finmind("TaiwanStockBlockTrade", stock_id, start, end)
        except Exception as exc:  # noqa: BLE001
            warn += 1
            print(f"  WARN {stock_id}: {exc}", file=sys.stderr)
            continue
        rows = parse_block_trade_rows(stock_id, raw)
        if not rows:
            warn += 1
            continue
        ok += 1
        if dry_run:
            total_rows += len(rows)
        else:
            conn = connect(db_path)
            try:
                total_rows += upsert_stock_block_trade(conn, rows)
                conn.commit()
            finally:
                conn.close()
        if i % 25 == 0 or i == len(stock_ids):
            print(f"  ...{i}/{len(stock_ids)} 檔 累計 {total_rows} 列 (ok={ok} warn={warn})")
    print(f"完成：{ok}/{len(stock_ids)} 檔有資料，{total_rows} 列，{warn} 檔無資料/失敗")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stock-ids", default=None, help="逗號分隔；與 --universe 二選一")
    p.add_argument("--universe", choices=["liquid"], default=None, help="liquid=中位金額>=3千萬")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--sync-db", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sleep", type=float, default=REQUEST_DELAY_SEC)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db_path = Path(args.db)
    if args.stock_ids:
        stock_ids = [s.strip() for s in args.stock_ids.split(",") if s.strip()]
    elif args.universe == "liquid":
        stock_ids = liquid_universe(db_path)
    else:
        print("ERROR: 需指定 --stock-ids 或 --universe liquid", file=sys.stderr)
        return 1
    print(f"universe: {len(stock_ids)} 檔, {args.start}..{args.end}")
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    dry_run = args.dry_run or not args.sync_db
    if dry_run:
        print("(dry-run，加 --sync-db 才真的寫入)")
    backfill(db_path, stock_ids, start, end, dry_run=dry_run, request_delay=args.sleep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
