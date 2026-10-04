"""券商委託／成交／已實現損益的持久化儲存（獨立小 SQLite，非 stocks.db）。

**為什麼需要**：富邦 API 單次查詢上限 30 天，且券商端**只保留約 4 個月**
（2026-10-04 實測：現股委託最早回到 2026-06-09、期貨委託 2026-07-31，更早的
已永久消失）。不自己留檔，每過一天就少一天歷史。

**為什麼不塞 stocks.db**：那份約 52GB，寫入與 VACUUM 會鎖住正在跑的收盤排程
（見 CLAUDE.md）。委託紀錄一年只有幾千列，獨立檔案零鎖競爭、可隨時備份。

**路徑**：`${GOLDENSTOCKS_DATA_DIR}/data/broker_history.db`（走 stock_db.DATA_DIR，
不硬寫 PROJECT_ROOT / "data"）。

**冪等**：三張表都有自然主鍵，重複拉取同一區間只會更新不會長出重複列——
`broker_orders` 用 (date, order_no, seq_no)，`broker_fills` 用 (date, filled_no,
order_no, symbol)，`broker_realized` 用 (date, symbol, buy_sell, filled_qty,
filled_price)。⚠ 成交紀錄的 `seq_no` 富邦一律回 None，所以委託↔成交的關聯鍵是
**(date, order_no)**，不是 seq_no（用 seq_no join 會 100% 落空）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from stock_db import DATA_DIR

DEFAULT_PATH = DATA_DIR / "broker_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_orders (
    date           TEXT NOT NULL,          -- YYYY-MM-DD
    order_no       TEXT NOT NULL,          -- 券商當日委託書號（每日重複配發）
    seq_no         TEXT NOT NULL DEFAULT '',
    account_type   TEXT NOT NULL,          -- stock | futopt
    account        TEXT,
    symbol         TEXT NOT NULL,          -- 現股代號 or 期貨商品代號
    expiry_date    TEXT,                   -- 期貨契約月份（現股為 NULL）
    side           TEXT NOT NULL,          -- Buy | Sell
    position_effect TEXT,                  -- New(新倉) | Close(平倉) | Stock…
    price_type     TEXT,
    time_in_force  TEXT,
    market_type    TEXT,
    limit_price    REAL,
    quantity       REAL,                   -- 現股股數 / 期貨口數
    filled_qty     REAL,
    status         INTEGER,                -- 10 已接受 · 30 委託中 · 50 有成交 · 90 失敗或刪單
    user_def       TEXT,                   -- 自動袖標籤；空 = 手動（HTML）下單
    is_auto        INTEGER NOT NULL DEFAULT 0,
    submit_time    TEXT,                   -- details 裡 function_type=10 的 modified_time
    n_amend_price  INTEGER NOT NULL DEFAULT 0,   -- 真改價次數（ft=10 且 before≠after）
    cancelled      INTEGER NOT NULL DEFAULT 0,   -- details 有 function_type=30
    details_json   TEXT,                   -- 完整委託過程（逐筆改價/刪單/成交）
    pulled_at      TEXT NOT NULL,
    PRIMARY KEY (date, order_no, seq_no)
);
CREATE INDEX IF NOT EXISTS idx_orders_symbol_date ON broker_orders(symbol, date);
CREATE INDEX IF NOT EXISTS idx_orders_manual ON broker_orders(is_auto, date);

CREATE TABLE IF NOT EXISTS broker_fills (
    date          TEXT NOT NULL,
    filled_no     TEXT NOT NULL,
    order_no      TEXT NOT NULL,           -- 與 broker_orders 的關聯鍵（配 date）
    account_type  TEXT NOT NULL,
    account       TEXT,
    symbol        TEXT NOT NULL,
    expiry_date   TEXT,
    side          TEXT NOT NULL,
    order_type    TEXT,
    filled_time   TEXT,                    -- HH:MM:SS.mmm
    filled_price  REAL,
    filled_qty    REAL,
    filled_avg_price REAL,
    user_def      TEXT,
    pulled_at     TEXT NOT NULL,
    PRIMARY KEY (date, filled_no, order_no, symbol)
);
CREATE INDEX IF NOT EXISTS idx_fills_symbol_date ON broker_fills(symbol, date);

CREATE TABLE IF NOT EXISTS broker_realized (
    date          TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL,
    order_type    TEXT,
    filled_qty    REAL,
    filled_price  REAL,
    realized_pnl  REAL,                    -- profit − loss（券商分兩欄，這裡合併）
    account       TEXT,
    pulled_at     TEXT NOT NULL,
    PRIMARY KEY (date, symbol, side, filled_qty, filled_price)
);

CREATE TABLE IF NOT EXISTS broker_pull_log (
    pulled_at    TEXT NOT NULL,
    start_date   TEXT NOT NULL,
    end_date     TEXT NOT NULL,
    n_orders     INTEGER NOT NULL,
    n_fills      INTEGER NOT NULL,
    n_realized   INTEGER NOT NULL,
    note         TEXT,
    PRIMARY KEY (pulled_at, start_date, end_date)
);
"""


def connect(path: Path | str | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    p = Path(path) if path else DEFAULT_PATH
    if readonly:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(p, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con


def _norm_date(d: Any) -> str:
    """券商回 '2026/10/02'，統一成 '2026-10-02'。"""
    s = str(d or "").strip()
    return s.replace("/", "-")


def _enum(v: Any) -> str | None:
    """'BSAction.Buy' → 'Buy'；None → None；壞值 → None。

    ⚠ 2026-10-04 踩過：早期拉取腳本用 dir() 展開 SDK enum，會得到「類別上所有
    列舉成員」而不是這一筆的值，序列化後變成巨大字典。那種值 split('.') 之後是
    `UnSupported'}}` 之類的垃圾。這裡一律擋掉，不讓壞值進庫。
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s or any(ch in s for ch in "{}'\"[]"):
        return None
    out = s.split(".")[-1] if "." in s else s
    return out or None


VALID_SIDES = ("Buy", "Sell")


AUTO_PREFIXES = ("tmfch", "dfshort", "dfmargin", "ss5d", "manflat", "mf",
                 "c18", "ld", "chase", "abc", "sgn", "epsg", "ep")


def is_auto(user_def: Any) -> bool:
    """自動袖都帶 user_def 前綴；空值＝手動（HTML）下單。"""
    u = str(user_def or "").strip()
    if not u or u.lower() == "none":
        return False
    return any(u.startswith(p) for p in AUTO_PREFIXES)


def _amend_count(details: Iterable[dict] | None) -> int:
    """真改價＝function_type 10 且 before_price ≠ after_price（status 15 受理）。
    ⚠ function_type 30 是**刪單**（after_lot=0），不是改價。"""
    n = 0
    for e in details or []:
        if e.get("function_type") != 10:
            continue
        bp, ap = e.get("before_price"), e.get("after_price")
        if bp is not None and ap is not None and bp != ap:
            n += 1
    return n


def _submit_time(details: Iterable[dict] | None) -> str | None:
    det = list(details or [])
    for e in det:
        if e.get("function_type") == 10:
            return e.get("modified_time")
    return det[0].get("modified_time") if det else None


def upsert_orders(con: sqlite3.Connection, rows: Iterable[dict], *,
                  account_type: str, pulled_at: str) -> int:
    sql = """INSERT INTO broker_orders
      (date, order_no, seq_no, account_type, account, symbol, expiry_date, side,
       position_effect, price_type, time_in_force, market_type, limit_price,
       quantity, filled_qty, status, user_def, is_auto, submit_time,
       n_amend_price, cancelled, details_json, pulled_at)
      VALUES (:date,:order_no,:seq_no,:account_type,:account,:symbol,:expiry_date,:side,
              :position_effect,:price_type,:time_in_force,:market_type,:limit_price,
              :quantity,:filled_qty,:status,:user_def,:is_auto,:submit_time,
              :n_amend_price,:cancelled,:details_json,:pulled_at)
      ON CONFLICT(date, order_no, seq_no) DO UPDATE SET
        account=excluded.account, symbol=excluded.symbol,
        expiry_date=excluded.expiry_date, side=excluded.side,
        position_effect=excluded.position_effect, price_type=excluded.price_type,
        time_in_force=excluded.time_in_force, market_type=excluded.market_type,
        limit_price=excluded.limit_price, quantity=excluded.quantity,
        filled_qty=excluded.filled_qty, status=excluded.status,
        user_def=excluded.user_def, is_auto=excluded.is_auto,
        submit_time=excluded.submit_time, n_amend_price=excluded.n_amend_price,
        cancelled=excluded.cancelled, details_json=excluded.details_json,
        pulled_at=excluded.pulled_at"""
    n = 0
    rejected = 0
    for r in rows:
        det = r.get("details") or []
        payload = {
            "date": _norm_date(r.get("date")),
            "order_no": str(r.get("order_no") or "").strip(),
            "seq_no": str(r.get("seq_no") or "").strip(),
            "account_type": account_type,
            "account": r.get("account"),
            "symbol": str(r.get("stock_no") or r.get("symbol") or "").strip(),
            "expiry_date": r.get("expiry_date"),
            "side": _enum(r.get("buy_sell")) or "",
            "position_effect": _enum(r.get("order_type")),
            "price_type": _enum(r.get("price_type")),
            "time_in_force": _enum(r.get("time_in_force")),
            "market_type": _enum(r.get("market_type")),
            "limit_price": r.get("price"),
            "quantity": r.get("quantity") if r.get("quantity") is not None else r.get("lot"),
            "filled_qty": r.get("filled_qty") if r.get("filled_qty") is not None else r.get("filled_lot"),
            "status": r.get("status"),
            "user_def": r.get("user_def"),
            "is_auto": int(is_auto(r.get("user_def"))),
            "submit_time": _submit_time(det),
            "n_amend_price": _amend_count(det),
            "cancelled": int(any(e.get("function_type") == 30 for e in det)),
            "details_json": json.dumps(det, ensure_ascii=False) if det else None,
            "pulled_at": pulled_at,
        }
        if not payload["date"] or not payload["symbol"] or payload["side"] not in VALID_SIDES:
            rejected += 1      # 來源壞值（見 _enum docstring）或缺關鍵欄 → 不入庫
            continue
        con.execute(sql, payload)
        n += 1
    if rejected:
        print(f"    ⚠ 委託剔除 {rejected} 列（買賣方向或日期/代號為壞值）")
    return n


def upsert_fills(con: sqlite3.Connection, rows: Iterable[dict], *,
                 account_type: str, pulled_at: str) -> int:
    sql = """INSERT INTO broker_fills
      (date, filled_no, order_no, account_type, account, symbol, expiry_date, side,
       order_type, filled_time, filled_price, filled_qty, filled_avg_price,
       user_def, pulled_at)
      VALUES (:date,:filled_no,:order_no,:account_type,:account,:symbol,:expiry_date,:side,
              :order_type,:filled_time,:filled_price,:filled_qty,:filled_avg_price,
              :user_def,:pulled_at)
      ON CONFLICT(date, filled_no, order_no, symbol) DO UPDATE SET
        account=excluded.account, expiry_date=excluded.expiry_date,
        side=excluded.side, order_type=excluded.order_type,
        filled_time=excluded.filled_time, filled_price=excluded.filled_price,
        filled_qty=excluded.filled_qty, filled_avg_price=excluded.filled_avg_price,
        user_def=excluded.user_def, pulled_at=excluded.pulled_at"""
    n = 0
    rejected = 0
    for r in rows:
        payload = {
            "date": _norm_date(r.get("date")),
            "filled_no": str(r.get("filled_no") or "").strip(),
            "order_no": str(r.get("order_no") or "").strip(),
            "account_type": account_type,
            "account": r.get("account"),
            "symbol": str(r.get("stock_no") or r.get("symbol") or "").strip(),
            "expiry_date": r.get("expiry_date"),
            "side": _enum(r.get("buy_sell")) or "",
            "order_type": _enum(r.get("order_type")),
            "filled_time": r.get("filled_time"),
            "filled_price": r.get("filled_price"),
            "filled_qty": r.get("filled_qty") if r.get("filled_qty") is not None else r.get("filled_lot"),
            "filled_avg_price": r.get("filled_avg_price"),
            "user_def": r.get("user_def"),
            "pulled_at": pulled_at,
        }
        if not payload["date"] or not payload["symbol"] or payload["side"] not in VALID_SIDES:
            rejected += 1
            continue
        con.execute(sql, payload)
        n += 1
    if rejected:
        print(f"    ⚠ 成交剔除 {rejected} 列（買賣方向或日期/代號為壞值）")
    return n


def upsert_realized(con: sqlite3.Connection, rows: Iterable[dict], *, pulled_at: str) -> int:
    sql = """INSERT INTO broker_realized
      (date, symbol, side, order_type, filled_qty, filled_price, realized_pnl,
       account, pulled_at)
      VALUES (:date,:symbol,:side,:order_type,:filled_qty,:filled_price,:realized_pnl,
              :account,:pulled_at)
      ON CONFLICT(date, symbol, side, filled_qty, filled_price) DO UPDATE SET
        realized_pnl=excluded.realized_pnl, pulled_at=excluded.pulled_at"""
    n = 0
    for r in rows:
        profit = r.get("realized_profit") or 0
        loss = r.get("realized_loss") or 0
        pnl = r.get("realized_profit_and_loss")
        payload = {
            "date": _norm_date(r.get("date")),
            "symbol": str(r.get("stock_no") or r.get("symbol") or "").strip(),
            "side": _enum(r.get("buy_sell")) or "",
            "order_type": _enum(r.get("order_type")),
            "filled_qty": r.get("filled_qty"),
            "filled_price": r.get("filled_price") if r.get("filled_price") is not None
            else r.get("filled_avg_price"),
            "realized_pnl": pnl if pnl is not None else (float(profit) - float(loss)),
            "account": r.get("account"),
            "pulled_at": pulled_at,
        }
        if not payload["date"] or not payload["symbol"]:
            continue
        con.execute(sql, payload)
        n += 1
    return n


def log_pull(con: sqlite3.Connection, *, pulled_at: str, start: str, end: str,
             n_orders: int, n_fills: int, n_realized: int, note: str = "") -> None:
    con.execute("""INSERT OR REPLACE INTO broker_pull_log
        (pulled_at, start_date, end_date, n_orders, n_fills, n_realized, note)
        VALUES (?,?,?,?,?,?,?)""",
        (pulled_at, start, end, n_orders, n_fills, n_realized, note))


def coverage(con: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tbl in ("broker_orders", "broker_fills", "broker_realized"):
        row = con.execute(
            f"SELECT COUNT(*) n, MIN(date) d0, MAX(date) d1 FROM {tbl}").fetchone()
        out[tbl] = {"n": row["n"], "first": row["d0"], "last": row["d1"]}
    out["manual_orders"] = con.execute(
        "SELECT COUNT(*) n FROM broker_orders WHERE is_auto=0").fetchone()["n"]
    out["by_account"] = {
        r["account_type"]: r["n"] for r in con.execute(
            "SELECT account_type, COUNT(*) n FROM broker_orders GROUP BY account_type")}
    return out
