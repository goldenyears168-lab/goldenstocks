#!/usr/bin/env python3
"""biglot dashboard Phase 0：把某一個真實交易日「凍結」成一份可重放的 golden fixture。

凍結三樣東西（只凍快取目錄不夠，datetime.now()/即時DB都還是會漂移）：
  1. 生產 DB 的一份小型唯讀子集（只留 biglot_dashboard.py 實際會查的 8 張表，
     其中 6 張逐股表只留 45 檔觀察宇宙的資料——不整份 49GB 複製，那既慢又佔硬碟，
     這 8 張表對 45 檔宇宙的子集通常在幾十 MB 內）。
  2. 當天的即時 tick／五檔快取檔（cache/biglot_live_watch、data/cache/watchlist_books）。
  3. 靜態校準檔（_live_calib.json 決定 45 檔宇宙是誰，import 當下就會讀，缺了會直接炸）。

凍結時鐘（datetime.now(TZ) 要回放成固定值）不在這裡處理，是 run_golden_diff.py／
smoke_test.py 用 _fixture_lib.make_frozen_datetime 在跑的時候動態注入，跟這支
「準備資料」的腳本職責分開。

用法：
    PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/build_fixture.py \\
        --date 2026-09-25

只從生產 DB 讀（sqlite3 mode=ro），輸出寫到全新檔案，不會碰生產庫或生產 cache。
"""
from __future__ import annotations

import argparse
import glob
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "src")
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

# 8 張 biglot_dashboard.py（含它 import 的 compute_xq_style_metrics._load_holder_tiers/
# _load_beta）實際會查的表。逐股表用 stock_id IN (45檔宇宙) 過濾；全域表整張複製
# （etf_holdings 是 ETF981 成分股清單，跟 45 檔觀察宇宙是兩回事，不能拿宇宙去濾它）。
PER_STOCK_TABLES = (
    "stock_daily_bars",
    "stock_margin_daily",
    "stock_lending_balance_daily",
    "stock_xq_style_daily",
    "stock_beta",
    "stock_holding_dispersion_weekly",
)
FULL_COPY_TABLES = ("market_vix_daily", "etf_holdings")


def _load_universe(prod_data_dir: Path) -> list[str]:
    calib = prod_data_dir / "cache" / "pit_universe_tick" / "_live_calib.json"
    cal = json.loads(calib.read_text(encoding="utf-8"))
    return [r["sid"] for r in cal["universe"]]


def _copy_table(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection,
                 table: str, stock_ids: list[str] | None) -> int:
    ddl = src_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if ddl is None:
        print(f"  [skip] {table} 在生產庫不存在", file=sys.stderr)
        return 0
    dst_conn.execute(ddl[0])
    if stock_ids is None:
        rows = src_conn.execute(f"SELECT * FROM {table}").fetchall()
    else:
        qmarks = ",".join("?" * len(stock_ids))
        rows = src_conn.execute(
            f"SELECT * FROM {table} WHERE stock_id IN ({qmarks})", stock_ids
        ).fetchall()
    if rows:
        cols = len(rows[0])
        dst_conn.executemany(
            f"INSERT INTO {table} VALUES ({','.join('?' * cols)})", rows
        )
    return len(rows)


def build_db(prod_db_path: Path, out_db_path: Path, stock_ids: list[str]) -> None:
    out_db_path.parent.mkdir(parents=True, exist_ok=True)
    if out_db_path.exists():
        out_db_path.unlink()
    src = sqlite3.connect(f"file:{prod_db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(out_db_path)
    try:
        for t in PER_STOCK_TABLES:
            n = _copy_table(src, dst, t, stock_ids)
            print(f"  {t}: {n} rows (45檔宇宙過濾)")
        for t in FULL_COPY_TABLES:
            n = _copy_table(src, dst, t, None)
            print(f"  {t}: {n} rows (整張複製)")
        dst.commit()
    finally:
        src.close()
        dst.close()


def copy_cache_files(prod_data_dir: Path, out_data_dir: Path, date: str) -> None:
    # cache/biglot_live_watch/ 下所有 *_{date}.* 檔（raw tick、盤前試撮、期貨即時價、
    # 權證流量、iceberg影子帳……）一次抓，未來新增側檔不用回來改這支腳本。
    src_dir = prod_data_dir.parent / "cache" / "biglot_live_watch"
    dst_dir = out_data_dir.parent / "cache" / "biglot_live_watch"
    dst_dir.mkdir(parents=True, exist_ok=True)
    matched = sorted(glob.glob(str(src_dir / f"*_{date}.*")))
    for f in matched:
        shutil.copy2(f, dst_dir / Path(f).name)
    print(f"  cache/biglot_live_watch/*_{date}.*: {len(matched)} 個檔")
    if not any(Path(f).name == f"raw_{date}.jsonl" for f in matched):
        print(f"  [警告] raw_{date}.jsonl 不存在——這天可能沒有即時ingest紀錄，"
              f"fixture 的 ingest() 會是空的一天", file=sys.stderr)

    # data/cache/watchlist_books/
    wb_src = prod_data_dir / "cache" / "watchlist_books" / f"watchlist_books_{date}.jsonl"
    wb_dst_dir = out_data_dir / "cache" / "watchlist_books"
    wb_dst_dir.mkdir(parents=True, exist_ok=True)
    if wb_src.exists():
        shutil.copy2(wb_src, wb_dst_dir / wb_src.name)
        print(f"  watchlist_books_{date}.jsonl: 已複製")
    else:
        print(f"  [提示] watchlist_books_{date}.jsonl 不存在，五檔相關欄位會是空的",
              file=sys.stderr)

    # 靜態/慢變依賴：_live_calib.json（必要，決定45檔宇宙）+ pe_peer_group 快照（缺了會
    # 優雅降級成空表，非致命）
    calib_src = prod_data_dir / "cache" / "pit_universe_tick" / "_live_calib.json"
    calib_dst_dir = out_data_dir / "cache" / "pit_universe_tick"
    calib_dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(calib_src, calib_dst_dir / calib_src.name)

    for f in glob.glob(str(prod_data_dir.parent / "scratch" / "pe_peer_group_*.json")):
        pe_dst_dir = out_data_dir.parent / "scratch"
        pe_dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, pe_dst_dir / Path(f).name)

    mops_src = prod_data_dir.parent / "cache" / f"mops_today_{date}.json"
    if mops_src.exists():
        shutil.copy2(mops_src, dst_dir.parent / mops_src.name)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD，要凍結的交易日")
    ap.add_argument("--out", default=None,
                     help="fixture 根目錄，預設 ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/{date}")
    args = ap.parse_args()

    prod_data_dir = DATA_DIR
    out_root = Path(args.out) if args.out else (
        DATA_DIR.parent / "scratch" / "biglot_fixture" / args.date
    )
    out_data_dir = out_root / "data"

    print(f"來源生產庫：{DEFAULT_DB_PATH}")
    print(f"輸出 fixture：{out_root}")

    stock_ids = _load_universe(prod_data_dir)
    print(f"觀察宇宙：{len(stock_ids)} 檔")

    print("複製 DB 子集…")
    build_db(DEFAULT_DB_PATH, out_data_dir / "stocks.db", stock_ids)

    print("複製 cache 檔案…")
    copy_cache_files(prod_data_dir, out_data_dir, args.date)

    print(f"\n完成。用法：\n"
          f"  from _fixture_lib import load_dashboard_module\n"
          f"  bd = load_dashboard_module('{out_root}', frozen_now=...)")


if __name__ == "__main__":
    main()
