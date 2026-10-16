#!/usr/bin/env python3
"""
同步上市櫃股票 Beta 因子查表（vs ^TWII，250 交易日滾動計算）。

清單：2026-10-17 起改官方公司基本資料(TWSE openapi t187ap03_L → TSE、櫃買 openapi mopsfin_t187ap03_O → OTC)，
再與 stock_beta 既有清單(含 ETF)合併；原本的 FinMind TaiwanStockInfo 已停用。價格與 Beta 計算仍是 Yahoo。
Beta：Yahoo 日線自算，小數 2 位，source=yahoo_computed

建議每週手動更新（非 daily_sync 一環）：
  .venv/bin/python sync_stock_beta.py --sync-db --csv data/stock_beta.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

from project_config import DEFAULT_CAPITAL_NTD
from stock_db import DEFAULT_DB_PATH, connect, upsert_stock_beta

BENCHMARK_SYMBOL = "^TWII"
BETA_WINDOW = ""
BETA_SOURCE = "yahoo_computed"
BETA_DECIMALS = 2
BETA_TRADING_DAYS = 250
DOWNLOAD_PERIOD = "400d"
BATCH_SIZE = 40
MARKET_YAHOO = {"TSE": "TW", "OTC": "TWO"}


@dataclass(frozen=True)
class StockRow:
    stock_id: str
    name: str
    market: str


TWSE_COMPANY_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_COMPANY_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"


def _get_json(url: str, timeout: float = 60.0) -> list[dict]:
    import json
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (goldenstocks)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_company_lists(twse: list[dict], tpex: list[dict]) -> list[StockRow]:
    """官方公司基本資料 → 上市(TSE)、上櫃(OTC)的 4 碼普通股。"""
    out: dict[str, StockRow] = {}
    for r in twse:
        sid = str(r.get("公司代號", "")).strip()
        if sid.isdigit() and len(sid) == 4:
            out[sid] = StockRow(sid, str(r.get("公司簡稱", "")).strip(), "TSE")
    for r in tpex:
        sid = str(r.get("SecuritiesCompanyCode", "")).strip()
        if sid.isdigit() and len(sid) == 4:
            out[sid] = StockRow(sid, str(r.get("CompanyAbbreviation", "")).strip(), "OTC")
    return [out[k] for k in sorted(out)]


def fetch_stock_universe(include_emerging: bool = False) -> list[StockRow]:
    """官方公司清單 ∪ stock_beta 既有清單(含 ETF、已下市但仍有歷史者);同代號以官方為準。興櫃不處理(Yahoo 無對應)。"""
    del include_emerging
    merged: dict[str, StockRow] = {}
    try:
        conn = connect(DEFAULT_DB_PATH)
        try:
            for sid, name, market in conn.execute("SELECT stock_id, name, market FROM stock_beta"):
                if market in MARKET_YAHOO and str(sid).isdigit() and len(str(sid)) == 4:
                    merged[str(sid)] = StockRow(str(sid), str(name or ""), market)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — 沒有舊表也能靠官方清單跑
        print(f"  WARN 讀取 stock_beta 既有清單失敗: {exc}")
    for row in parse_company_lists(_get_json(TWSE_COMPANY_URL), _get_json(TPEX_COMPANY_URL)):
        merged[row.stock_id] = row
    return [merged[k] for k in sorted(merged)]


def yahoo_symbol(stock: StockRow) -> str:
    suffix = MARKET_YAHOO.get(stock.market)
    if not suffix:
        raise ValueError(f"Unsupported market for Yahoo: {stock.market}")
    return f"{stock.stock_id}.{suffix}"


def download_market_returns() -> pd.Series:
    raw = yf.download(
        BENCHMARK_SYMBOL,
        period=DOWNLOAD_PERIOD,
        progress=False,
        auto_adjust=True,
    )
    close = raw["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    returns = close.pct_change().dropna().tail(BETA_TRADING_DAYS)
    if len(returns) < BETA_TRADING_DAYS // 2:
        raise RuntimeError(f"基準 {BENCHMARK_SYMBOL} 資料不足（{len(returns)} 日）")
    return returns


def compute_batch_beta(
    stocks: list[StockRow],
    market_returns: pd.Series,
) -> list[dict]:
    symbols = [yahoo_symbol(s) for s in stocks]
    symbol_map = dict(zip(symbols, stocks))
    raw = yf.download(
        " ".join(symbols),
        period=DOWNLOAD_PERIOD,
        progress=False,
        auto_adjust=True,
        threads=True,
    )
    closes = raw["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame()

    as_of = date.today().isoformat()
    rows: list[dict] = []
    for sym in symbols:
        stock = symbol_map[sym]
        try:
            if sym not in closes.columns:
                continue
            series = closes[sym].dropna()
            if series.empty:
                continue
            stock_returns = series.pct_change().dropna()
            aligned = pd.concat(
                [stock_returns.rename("stock"), market_returns.rename("market")],
                axis=1,
                join="inner",
            ).dropna()
            window = aligned.tail(BETA_TRADING_DAYS)
            if len(window) < BETA_TRADING_DAYS // 2:
                continue
            var = window["market"].var()
            if var == 0 or pd.isna(var):
                continue
            beta = float(window["stock"].cov(window["market"]) / var)
            rows.append(
                {
                    "stock_id": stock.stock_id,
                    "name": stock.name,
                    "market": stock.market,
                    "beta": round(beta, BETA_DECIMALS),
                    "beta_window": BETA_WINDOW,
                    "benchmark": BENCHMARK_SYMBOL,
                    "source": BETA_SOURCE,
                    "as_of_date": as_of,
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return rows


def sync_computed_beta(
    stocks: list[StockRow],
    batch_size: int,
    sleep_sec: float,
) -> list[dict]:
    print(f"  下載基準 {BENCHMARK_SYMBOL}…")
    market_returns = download_market_returns()
    all_rows: list[dict] = []
    total_batches = (len(stocks) + batch_size - 1) // batch_size
    for i in range(0, len(stocks), batch_size):
        batch = stocks[i : i + batch_size]
        batch_no = i // batch_size + 1
        print(f"  Beta 批次 {batch_no}/{total_batches}（{len(batch)} 檔）…")
        all_rows.extend(compute_batch_beta(batch, market_returns))
        if sleep_sec > 0 and i + batch_size < len(stocks):
            time.sleep(sleep_sec)
    return all_rows


def _normalize_header(name: str) -> str:
    return re.sub(r"\s+", "", name.strip().lower())


def _pick_column(fieldnames: list[str], candidates: tuple[str, ...]) -> str | None:
    normalized = {_normalize_header(h): h for h in fieldnames}
    for cand in candidates:
        key = _normalize_header(cand)
        if key in normalized:
            return normalized[key]
    return None


def load_csv_beta(path: Path) -> list[dict]:
    """匯入含 Beta 欄的 CSV（寫入 source=yahoo_computed）。"""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"CSV 無欄位：{path}")

        col_id = _pick_column(
            reader.fieldnames,
            ("代號", "股票代號", "stock_id", "code", "symbol"),
        )
        col_name = _pick_column(
            reader.fieldnames,
            ("名稱", "股票名稱", "name", "description", "公司名稱"),
        )
        col_beta = _pick_column(
            reader.fieldnames,
            ("beta", "β", "Beta", "BETA", "貝他係數", "貝塔", "beta_250d"),
        )
        col_market = _pick_column(
            reader.fieldnames,
            ("market", "市場", "交易所", "上市別"),
        )
        if not col_id or not col_beta:
            raise ValueError(f"CSV 需含代號與 Beta 欄（找到：{reader.fieldnames}）")

        as_of = date.today().isoformat()
        rows: list[dict] = []
        for line in reader:
            raw_id = str(line.get(col_id, "")).strip()
            match = re.match(r"^(\d{4})", raw_id)
            if not match:
                continue
            stock_id = match.group(1)
            beta_raw = str(line.get(col_beta, "")).strip().replace(",", "")
            if not beta_raw or beta_raw in {"-", "--", "N/A", "nan"}:
                continue
            try:
                beta = round(float(beta_raw), BETA_DECIMALS)
            except ValueError:
                continue
            name = str(line.get(col_name or "", "")).strip() if col_name else ""
            market_raw = str(line.get(col_market or "", "")).strip() if col_market else ""
            if market_raw.upper() in {"TSE", "TWSE", "上市"}:
                market = "TSE"
            elif market_raw.upper() in {"OTC", "TPEX", "上櫃"}:
                market = "OTC"
            else:
                market = "TSE"
            rows.append(
                {
                    "stock_id": stock_id,
                    "name": name,
                    "market": market,
                    "beta": beta,
                    "beta_window": BETA_WINDOW,
                    "benchmark": BENCHMARK_SYMBOL,
                    "source": BETA_SOURCE,
                    "as_of_date": as_of,
                }
            )
    return rows


def enrich_names_from_universe(rows: list[dict], universe: list[StockRow]) -> list[dict]:
    by_id = {s.stock_id: s for s in universe}
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        ref = by_id.get(item["stock_id"])
        if ref:
            if not item.get("name"):
                item["name"] = ref.name
            if item.get("market") not in {"TSE", "OTC"}:
                item["market"] = ref.market
        out.append(item)
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "stock_id",
        "name",
        "market",
        "beta",
        "beta_window",
        "benchmark",
        "source",
        "as_of_date",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: row.get(k) for k in fields} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="同步上市櫃 Beta 至 stock_beta")
    parser.add_argument(
        "--csv-input",
        type=Path,
        help="改由 CSV 匯入",
    )
    parser.add_argument(
        "--include-emerging",
        action="store_true",
        help="含興櫃（預設僅 TSE + OTC）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="僅處理前 N 檔（測試用，0=全部）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Yahoo 批次大小（預設 {BATCH_SIZE}）",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="批次間 sleep 秒數（預設 0.5）",
    )
    parser.add_argument(
        "--sync-db",
        action="store_true",
        help="寫入 SQLite stock_beta 表",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="輸出 CSV（例 data/stock_beta.csv）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 輸出至 stdout",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不寫入 DB",
    )
    args = parser.parse_args()

    print("取得股票清單（官方公司基本資料 + stock_beta 既有清單）…")
    universe = fetch_stock_universe(include_emerging=args.include_emerging)
    universe = [s for s in universe if s.market in {"TSE", "OTC"} or args.include_emerging]
    if args.limit > 0:
        universe = universe[: args.limit]
    print(
        f"  共 {len(universe)} 檔（TSE {sum(1 for s in universe if s.market == 'TSE')} / "
        f"OTC {sum(1 for s in universe if s.market == 'OTC')}）"
    )

    if args.csv_input:
        rows = enrich_names_from_universe(load_csv_beta(args.csv_input), universe)
        print(f"完成：自 CSV 匯入 {len(rows)} 筆 Beta")
    else:
        rows = sync_computed_beta(
            universe,
            max(1, args.batch_size),
            max(0.0, args.sleep),
        )
        print(f"完成：計算 {len(rows)} 筆 Beta（vs {BENCHMARK_SYMBOL}）")

    if args.csv:
        write_csv(args.csv, rows)
        print(f"已寫入 CSV：{args.csv}")

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))

    if args.sync_db and not args.dry_run:
        conn = connect(args.db)
        count = upsert_stock_beta(conn, rows)
        conn.close()
        print(f"已寫入 DB：{count} 筆 → {args.db} stock_beta")
    elif args.sync_db and args.dry_run:
        print("dry-run：略過 DB 寫入")

    if not rows:
        sys.exit(1)


if __name__ == "__main__":
    main()
