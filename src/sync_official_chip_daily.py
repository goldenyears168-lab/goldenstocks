#!/usr/bin/env python3
"""官方來源每日籌碼同步:融資融券 / 當沖 / 三大法人 → stock_margin_daily、stock_daytrade_daily、stock_institutional_daily。

2026-10 起取代 FinMind 的 `sync_stock_chip_daily`(融資券/當沖)與 `sync_stock_market_daily` 的法人部分。
官方端點一天一次請求涵蓋全市場(上市 TWSE、上櫃 櫃買),不再受「ETF 成分股名單」限制。

來源與寫入的 source 標籤(`scripts/research/source_dedup.py` 的 SOURCE_PRIORITY 已登記:官方優先、FinMind 備援):
  上市融資券  TWSE MI_MARGN        → stock_margin_daily      source='twse_mi_margn'
  上櫃融資券  櫃買 margin/balance   → stock_margin_daily      source='tpex_margn'
  上市當沖    TWSE TWTB4U          → stock_daytrade_daily    source='twse_twtb4u'
  上市法人    TWSE T86             → stock_institutional_daily source='twse_t86'
  上櫃法人    櫃買 insti/dailyTrade → stock_institutional_daily source='tpex_insti'
上櫃當沖暫無可用端點,未涵蓋。

和既有 `scripts/backfill_twse_*.py` 的差別:它們的『已抓過就跳過』看的是任何來源,FinMind 一直有列的日子會被整天跳過;
這裡改成『依來源判斷』,所以 8/20 以後 FinMind 供應期間的缺口也補得進來。回補腳本的抓取/寫入函式直接重用。

用法:
    PYTHONPATH=src .venv/bin/python src/sync_official_chip_daily.py                      # 近 5 個日曆日
    PYTHONPATH=src .venv/bin/python src/sync_official_chip_daily.py --start 2026-08-20 --end 2026-10-16
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from stock_db import connect
from stock_db.util import DEFAULT_DB_PATH

ROOT = Path(__file__).resolve().parents[1]
TPEX_MARGIN_URL = "https://www.tpex.org.tw/www/zh-tw/margin/balance?date={d}&id=&response=json"
TPEX_MARGIN_SOURCE = "tpex_margn"

PARTS = ("margin_twse", "margin_tpex", "daytrade_twse", "inst")
INST_PARTS = ("inst_twse", "inst_tpex")        # 也可只指定其中一邊(例如只修復櫃買法人)


def _load_script(name: str):
    """scripts/ 不是 package;依路徑載入回補腳本以重用其抓取/寫入函式。"""
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _num(s) -> float | None:
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    try:
        return float(t) if t not in ("", "--", "-", "N/A") else None
    except ValueError:
        return None


def parse_tpex_margin(payload: dict, day: date) -> list[dict]:
    """櫃買融資融券餘額表 → stock_margin_daily 列(欄位順序:前資餘額[2] 資餘額[6] 前券餘額[10] 券餘額[14])。"""
    tables = (payload or {}).get("tables") or []
    if not tables:
        return []
    out = []
    iso = day.isoformat()
    for r in tables[0].get("data") or []:
        if len(r) < 15 or not str(r[0]).strip():
            continue
        margin_bal, short_bal = _num(r[6]), _num(r[14])
        if margin_bal is None and short_bal is None:
            continue
        prev_m, prev_s = _num(r[2]), _num(r[10])
        out.append(
            {
                "stock_id": str(r[0]).strip(),
                "trade_date": iso,
                "margin_balance": margin_bal,
                "margin_change": None if margin_bal is None or prev_m is None else margin_bal - prev_m,
                "short_balance": short_bal,
                "short_change": None if short_bal is None or prev_s is None else short_bal - prev_s,
                "source": TPEX_MARGIN_SOURCE,
            }
        )
    return out


def have(conn, table: str, day: str, sources: tuple[str, ...]) -> bool:
    ph = ",".join("?" * len(sources))
    return conn.execute(
        f"SELECT 1 FROM {table} WHERE trade_date = ? AND source IN ({ph}) LIMIT 1", (day, *sources)
    ).fetchone() is not None


def sync_day(conn, day: date, parts=PARTS, *, sleep: float = 1.5, mods=None, force: bool = False) -> dict[str, int]:
    """同步一天;已有該來源資料的部分跳過。回傳各部分寫入列數(-1=抓不到,視為休市/尚未發布)。"""
    bm = (mods or {}).get("bm") or _load_script("backfill_twse_margin_daytrade")
    bi = (mods or {}).get("bi") or _load_script("backfill_twse_institutional")
    iso = day.isoformat()
    res: dict[str, int] = {}

    def run(part, table, sources, fetch, upsert):
        if part not in parts:
            return
        if not force and have(conn, table, iso, sources):
            res[part] = 0
            return
        rows = fetch()
        time.sleep(sleep)
        res[part] = upsert(rows) if rows else -1

    run("margin_twse", "stock_margin_daily", (bm.MARGIN_SOURCE,), lambda: bm.fetch_margin(day),
        lambda rows: bm.upsert_margin(conn, rows))
    run("margin_tpex", "stock_margin_daily", (TPEX_MARGIN_SOURCE,),
        lambda: parse_tpex_margin(bm._get(TPEX_MARGIN_URL.format(d=f"{day:%Y}%2F{day:%m}%2F{day:%d}")), day),
        lambda rows: bm.upsert_margin(conn, rows))
    if "daytrade_twse" in parts and (force or not have(conn, "stock_daytrade_daily", iso, (bm.DAYTRADE_SOURCE,))):
        _ensure_bars(iso)                       # 當沖比例的分母(全日成交股數)取自當日日線
    run("daytrade_twse", "stock_daytrade_daily", (bm.DAYTRADE_SOURCE,), lambda: bm.fetch_daytrade(day),
        lambda rows: bm.upsert_daytrade(conn, rows, day))
    # 法人:上市(T86)與上櫃(insti)各自判斷,不要因為一邊已有就跳過另一邊
    inst_want = set(INST_PARTS) if "inst" in parts else set(parts) & set(INST_PARTS)
    if inst_want:
        for key, src, fetch in (("inst_twse", "twse_t86", bi.fetch_twse), ("inst_tpex", "tpex_insti", bi.fetch_tpex)):
            if key not in inst_want:
                continue
            if not force and have(conn, "stock_institutional_daily", iso, (src,)):
                res[key] = 0
                continue
            rows = fetch(day)
            time.sleep(sleep)
            res[key] = bi.upsert(conn, rows) if rows else -1
    return res


def _ensure_bars(iso: str) -> None:
    try:
        from official_bars import ensure_official_bars

        ensure_official_bars(iso)
    except Exception as exc:  # noqa: BLE001 — 取不到日線就讓當沖比例留 NULL
        print(f"  official bars {iso}: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--lookback-days", type=int, default=5, help="未指定 --start 時往回幾個日曆日(預設 5)")
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument("--parts", default=",".join(PARTS), help=f"逗號分隔,預設全部:{','.join(PARTS)}")
    ap.add_argument("--force", action="store_true", help="即使該來源當天已有資料也重抓覆蓋(用於修復解析錯誤)")
    args = ap.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=args.lookback_days)
    parts = tuple(p for p in args.parts.split(",") if p)
    conn = connect(args.db)
    mods = {"bm": _load_script("backfill_twse_margin_daytrade"), "bi": _load_script("backfill_twse_institutional")}
    try:
        day, n_days = start, 0
        while day <= end:
            if day.weekday() < 5:
                res = sync_day(conn, day, parts, sleep=args.sleep, mods=mods, force=args.force)
                shown = " ".join(f"{k}={v}" for k, v in res.items())
                print(f"{day} {shown}", flush=True)
                n_days += 1
            day += timedelta(days=1)
        print(f"完成:{start}~{end},{n_days} 個平日(0=該來源已有、-1=抓不到/休市/尚未發布)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
