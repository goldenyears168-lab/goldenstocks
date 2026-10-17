#!/usr/bin/env python3
"""補齊個股籌碼的兩塊缺口 + 修好現沖比例。

三件事：

1. **股權分散表（大戶／散戶結構）** —— FinMind ``TaiwanStockHoldingSharesPer``，
   週頻。原本 repo 完全沒有這份資料，只有 ``stock_shareholding_daily``（外資
   持股比例），看不到 >200 張大戶的持股集中度。

2. **借券餘額** —— TWSE ``TWT72U``（證交所借券系統與證商／證金營業處所借券
   餘額合計表）。FinMind 的 ``TaiwanStockSecuritiesLending`` 只給逐筆議借成交
   明細，**沒有餘額欄位**，拿不到真正的空方部位存量。

3. **現沖比例** —— ``stock_daytrade_daily.daytrade_ratio_pct`` 原本存的是
   當沖買金額／賣金額之比（恆等於 99~100%，無意義）。改用
   當沖量 ÷ 全日成交量重算。

用法::

    PYTHONPATH=src .venv/bin/python scripts/backfill_stock_chip_extras.py \\
        --stock-ids 2408 --start 2015-01-01
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from finmind_client import fetch_finmind
from stock_db.chip import (
    refresh_daytrade_ratio,
    upsert_stock_short_interest_daily,
    upsert_stock_holding_dispersion_weekly,
    upsert_stock_lending_balance_daily,
)
from stock_db.connection import connect
from stock_db.util import DEFAULT_DB_PATH

TWSE_LENDING_URL = "https://www.twse.com.tw/rwd/zh/lending/TWT72U"
TWSE_SHORT_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U"
TWSE_SOURCE = "twse"
FINMIND_SOURCE = "finmind"


def _num(text: str) -> float | None:
    cleaned = str(text).replace(",", "").strip()
    if not cleaned or cleaned in {"-", "--"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _level_bounds(level: str) -> tuple[int | None, int | None]:
    """把 ``1,000-5,000`` / ``more than 1,000,001`` 轉成上下界（單位：股）。"""
    raw = str(level).replace(",", "").strip()
    if raw.startswith("more than"):
        found = re.search(r"\d+", raw)
        return (int(found.group()), None) if found else (None, None)
    matched = re.match(r"^(\d+)-(\d+)$", raw)
    if matched:
        return int(matched.group(1)), int(matched.group(2))
    return None, None


def backfill_dispersion(conn, stock_ids: list[str], start: date, end: date) -> int:
    """FinMind 股權分散表 → stock_holding_dispersion_weekly。"""
    total = 0
    for stock_id in stock_ids:
        raw = fetch_finmind("TaiwanStockHoldingSharesPer", stock_id, start, end)
        rows = []
        for item in raw:
            level = str(item.get("HoldingSharesLevel") or "").strip()
            as_of = str(item.get("date") or "")[:10]
            if not level or not as_of:
                continue
            lo, hi = _level_bounds(level)
            rows.append(
                {
                    "stock_id": stock_id,
                    "as_of_date": as_of,
                    "level": level,
                    "level_lo": lo,
                    "level_hi": hi,
                    "people": item.get("people"),
                    "shares": _num(item.get("unit")),
                    "percent": _num(item.get("percent")),
                    "source": FINMIND_SOURCE,
                }
            )
        written = upsert_stock_holding_dispersion_weekly(conn, rows)
        total += written
        print(f"  [dispersion] {stock_id}: {written} 列 ({start}..{end})")
    return total


def _twse_lending_for_date(day: date, retries: int = 3) -> list[dict]:
    """TWT72U 單日全市場借券餘額。TWSE 沒有區間查詢，只能逐日打。"""
    params = {"date": day.strftime("%Y%m%d"), "response": "json"}
    for attempt in range(retries):
        try:
            resp = requests.get(TWSE_LENDING_URL, params=params, timeout=30)
            if resp.status_code != 200:
                time.sleep(3 * (attempt + 1))
                continue
            payload = resp.json()
        except (requests.RequestException, ValueError):
            time.sleep(3 * (attempt + 1))
            continue
        if payload.get("stat") != "OK":
            return []  # 非交易日
        return payload.get("data") or []
    return []


def backfill_lending_balance(
    conn,
    stock_ids: list[str] | None,
    start: date,
    end: date,
    *,
    sleep_sec: float = 2.0,
) -> int:
    """TWSE 借券餘額 → stock_lending_balance_daily。

    ``stock_ids=None`` 代表整個市場都收（TWT72U 本來就是一次回全市場，
    多存不用多打 request，只多佔 DB）。
    """
    wanted = set(stock_ids) if stock_ids else None
    total = 0
    day = start
    days_hit = 0
    while day <= end:
        if day.weekday() >= 5:  # 週末直接跳過，省 request
            day += timedelta(days=1)
            continue
        data = _twse_lending_for_date(day)
        if data:
            rows = []
            for item in data:
                sid = str(item[0]).strip()
                if wanted is not None and sid not in wanted:
                    continue
                rows.append(
                    {
                        "stock_id": sid,
                        "trade_date": day.isoformat(),
                        "prev_balance": _num(item[2]),
                        "borrow_volume": _num(item[3]),
                        "return_volume": _num(item[4]),
                        "lending_balance": _num(item[5]),
                        "close": _num(item[6]),
                        "market_value": _num(item[7]),
                        "market": str(item[8]).strip() if len(item) > 8 else None,
                        "source": TWSE_SOURCE,
                    }
                )
            written = upsert_stock_lending_balance_daily(conn, rows)
            total += written
            days_hit += 1
            if days_hit % 20 == 0:
                print(f"  [lending] ...{day} 累計 {total} 列 / {days_hit} 個交易日")
        time.sleep(sleep_sec)
        day += timedelta(days=1)
    print(f"  [lending] 完成：{total} 列 / {days_hit} 個交易日 ({start}..{end})")
    return total


def _twse_json(url: str, day: date, retries: int = 3) -> list[dict]:
    params = {"date": day.strftime("%Y%m%d"), "response": "json"}
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=40)
            if resp.status_code != 200:
                time.sleep(3 * (attempt + 1))
                continue
            payload = resp.json()
        except (requests.RequestException, ValueError):
            time.sleep(3 * (attempt + 1))
            continue
        if payload.get("stat") != "OK":
            return []
        return payload.get("data") or []
    return []


def backfill_short_interest(
    conn,
    stock_ids: list[str] | None,
    start: date,
    end: date,
    *,
    sleep_sec: float = 1.2,
) -> int:
    """TWSE TWT93U → stock_short_interest_daily（融券 ＋ 借券賣出餘額）。

    欄位順序（15 欄）：代號 名稱 | 融券:前日 賣出 買進 現券 今日 次一限額
    | 借券賣出:前日 當日賣出 當日還券 當日調整 當日餘額 次一可限額 | 備註
    """
    wanted = set(stock_ids) if stock_ids else None
    total = 0
    days_hit = 0
    day = start
    while day <= end:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        data = _twse_json(TWSE_SHORT_URL, day)
        if data:
            rows = []
            for item in data:
                if len(item) < 14:
                    continue
                sid = str(item[0]).strip()
                if wanted is not None and sid not in wanted:
                    continue
                rows.append(
                    {
                        "stock_id": sid,
                        "trade_date": day.isoformat(),
                        "short_prev": _num(item[2]),
                        "short_sell": _num(item[3]),
                        "short_buy": _num(item[4]),
                        "short_cash_offset": _num(item[5]),
                        "short_balance": _num(item[6]),
                        "short_limit": _num(item[7]),
                        "sbl_prev": _num(item[8]),
                        "sbl_sell": _num(item[9]),
                        "sbl_return": _num(item[10]),
                        "sbl_adjust": _num(item[11]),
                        "sbl_balance": _num(item[12]),
                        "sbl_next_limit": _num(item[13]),
                        "note": str(item[14]).strip() if len(item) > 14 else None,
                        "source": TWSE_SOURCE,
                    }
                )
            written = upsert_stock_short_interest_daily(conn, rows)
            total += written
            days_hit += 1
            if days_hit % 20 == 0:
                print(f"  [short] ...{day} 累計 {total} 列 / {days_hit} 個交易日")
        time.sleep(sleep_sec)
        day += timedelta(days=1)
    print(f"  [short] 完成：{total} 列 / {days_hit} 個交易日 ({start}..{end})")
    return total


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stock-ids",
        default="2408",
        help="逗號分隔股票代號；借券餘額可傳 ALL 代表全市場",
    )
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--lending-start",
        default=None,
        help="借券餘額起始日（TWSE 逐日打，預設同 --start 但通常想縮短）",
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=None,
        help=(
            "只補最近 N 個日曆天（收盤排程用）。會同時覆寫 --start 與 "
            "--lending-start，比手算日期不易出錯。"
        ),
    )
    parser.add_argument("--skip-dispersion", action="store_true")
    parser.add_argument("--skip-lending", action="store_true")
    parser.add_argument("--skip-daytrade-fix", action="store_true")
    parser.add_argument("--skip-short-interest", action="store_true")
    parser.add_argument("--sleep", type=float, default=2.0, help="TWSE 請求間隔秒數")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ids_raw = args.stock_ids.strip()
    all_market = ids_raw.upper() == "ALL"
    stock_ids = [] if all_market else [s.strip() for s in ids_raw.split(",") if s.strip()]
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if args.recent_days is not None:
        start = lending_start = end - timedelta(days=max(1, args.recent_days))
    else:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        lending_start = (
            datetime.strptime(args.lending_start, "%Y-%m-%d").date()
            if args.lending_start
            else start
        )

    conn = connect(Path(args.db))
    try:
        if not args.skip_dispersion and stock_ids:
            print("== 股權分散表（大戶／散戶） ==")
            backfill_dispersion(conn, stock_ids, start, end)

        if not args.skip_lending:
            print("== 借券餘額（TWSE TWT72U） ==")
            backfill_lending_balance(
                conn,
                None if all_market else stock_ids,
                lending_start,
                end,
                sleep_sec=args.sleep,
            )

        if not args.skip_short_interest:
            print("== 借券賣出餘額 ＋ 融券（TWSE TWT93U） ==")
            backfill_short_interest(
                conn,
                None if all_market else stock_ids,
                lending_start,
                end,
                sleep_sec=args.sleep,
            )

        if not args.skip_daytrade_fix:
            print("== 現沖比例重算 ==")
            fixed = refresh_daytrade_ratio(
                conn,
                stock_ids=stock_ids or None,
                start_date=start.isoformat(),
            )
            print(f"  [daytrade] 重算 {fixed} 列")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
