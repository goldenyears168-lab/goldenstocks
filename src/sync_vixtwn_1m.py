"""Sync VIXTWN 1-minute OHLC from TAIFEX ``getVixData`` (15s ticks).

Source
------
2026-10: switched from FinMind ``TaiwanOptionVix`` to the TAIFEX public daily files
(same official 15s series; OHLC/n_ticks identical to FinMind day by day). New rows use
source='taifex'; existing source='finmind' rows are kept. Prints every 15s during
**day session only** (09:00–13:45). No night session.

This is **not** recomputed from TXO chain (daily settlement only). We aggregate
the official 15s series → 1m bars (open/high/low/close + n_ticks).

Writes
------
1. SQLite ``market_vix_1m`` (symbol='VIXTWN', source='finmind')
2. Optional JSON cache for channel_lab research

Usage
-----
    PYTHONPATH=src .venv/bin/python src/sync_vixtwn_1m.py
    PYTHONPATH=src .venv/bin/python src/sync_vixtwn_1m.py --start 2026-06-26 --end 2026-07-31
    PYTHONPATH=src .venv/bin/python src/sync_vixtwn_1m.py --dry-run --json-out reports/research/channel_lab/vixtwn_1m_cache.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import taifex_client
from stock_db import DATA_DIR, connect
from stock_db.util import utc_now_iso

_CREATE = """
CREATE TABLE IF NOT EXISTS market_vix_1m (
  symbol TEXT NOT NULL,
  ts TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  n_ticks INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'finmind',
  synced_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (symbol, ts, source)
);
CREATE INDEX IF NOT EXISTS idx_market_vix_1m_date
  ON market_vix_1m (symbol, substr(ts, 1, 10), source);
"""

_UPSERT = """
INSERT INTO market_vix_1m (symbol, ts, open, high, low, close, n_ticks, source, synced_at)
VALUES (:symbol, :ts, :open, :high, :low, :close, :n_ticks, :source, :synced_at)
ON CONFLICT(symbol, ts, source) DO UPDATE SET
  open=excluded.open, high=excluded.high, low=excluded.low,
  close=excluded.close, n_ticks=excluded.n_ticks, synced_at=excluded.synced_at
"""


ARCHIVE_DIR = DATA_DIR.parent / "cache" / "taifex_vix"


def _minute_floor(t: str) -> str:
    """'09:00:45' → '09:00' ; also accept '09:00'."""
    s = str(t).strip()
    if len(s) >= 5:
        return s[:5]
    return s


def ticks_to_1m(rows: list[dict], source: str = "taifex") -> list[dict]:
    """Aggregate {date,time,vix} ticks → 1m OHLC bars with ts='YYYY-MM-DD HH:MM'."""
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    # 先依 (日期, 時間) 排序:open/close 取分鐘內第一/最後一筆,輸入順序不保證時間序時會顛倒(舊 FinMind 資料就是這樣)
    for r in sorted(rows, key=lambda x: (str(x.get("date") or ""), str(x.get("time") or ""))):
        d = r.get("date")
        tm = r.get("time")
        v = r.get("vix")
        if not d or not tm or v is None:
            continue
        try:
            vx = float(v)
        except (TypeError, ValueError):
            continue
        if not (1.0 < vx < 200.0):
            continue
        buckets[(str(d)[:10], _minute_floor(tm))].append(vx)

    out: list[dict] = []
    for (d, hm), xs in sorted(buckets.items()):
        if not xs:
            continue
        out.append(
            {
                "symbol": "VIXTWN",
                "ts": f"{d} {hm}",
                "open": round(xs[0], 4),
                "high": round(max(xs), 4),
                "low": round(min(xs), 4),
                "close": round(xs[-1], 4),
                "n_ticks": len(xs),
                "source": source,
            }
        )
    return out


def fetch_range(start: str, end: str, *, sleep_s: float = 1.2) -> list[dict]:
    """Fetch TAIFEX daily VIX files day by day (skip weekends/holidays); return raw ticks."""
    d0 = dt.date.fromisoformat(start)
    d1 = dt.date.fromisoformat(end)
    cur = d0
    all_ticks: list[dict] = []
    while cur <= d1:
        if cur.weekday() < 5:
            data = taifex_client.fetch_vix_ticks(cur, archive_dir=ARCHIVE_DIR) or []
            all_ticks.extend(data)
            print(f"  fetch {cur}: ticks={len(data)}", flush=True)
            time.sleep(sleep_s)
        cur += dt.timedelta(days=1)
    return all_ticks


def default_start() -> str:
    return "2026-03-02"


def default_end() -> str:
    return dt.date.today().isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync VIXTWN 1m bars from TAIFEX getVixData")
    ap.add_argument("--start", default=default_start())
    ap.add_argument("--end", default=default_end())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--json-out",
        default="",
        help="Also write day→bars JSON cache (research)",
    )
    A = ap.parse_args()

    print(f"VIXTWN 1m sync {A.start}→{A.end} (day session 09:00–13:45 only)", flush=True)
    ticks = fetch_range(A.start, A.end)
    bars = ticks_to_1m(ticks)
    print(f"aggregated bars={len(bars)} from ticks={len(ticks)}", flush=True)

    by_day: dict[str, list[dict]] = defaultdict(list)
    for b in bars:
        by_day[b["ts"][:10]].append(
            {
                "ts": b["ts"],
                "o": b["open"],
                "h": b["high"],
                "l": b["low"],
                "c": b["close"],
                "n": b["n_ticks"],
            }
        )
    print(f"days={len(by_day)} bars/day≈{len(bars) / max(1, len(by_day)):.0f}", flush=True)

    # Sanity vs daily finmind close (last 1m close ≈ daily close)
    if not A.dry_run or True:
        try:
            con = sqlite3.connect(
                f"file:{Path.home() / 'goldenstocks-data/data/stocks.db'}?mode=ro",
                uri=True,
            )
            daily = {
                d: float(c)
                for d, c in con.execute(
                    "SELECT date, close FROM market_vix_daily "
                    "WHERE symbol='VIXTWN' AND source IN ('finmind','taifex')"
                )
            }
            con.close()
            diffs = []
            for d, xs in by_day.items():
                if d not in daily or not xs:
                    continue
                diffs.append(abs(xs[-1]["c"] - daily[d]))
            if diffs:
                print(
                    f"vs daily official close: n={len(diffs)} "
                    f"mae={sum(diffs)/len(diffs):.3f} max={max(diffs):.3f}",
                    flush=True,
                )
        except Exception as e:
            print(f"(skip daily check: {e})", flush=True)

    synced = utc_now_iso()
    if not A.dry_run:
        conn = connect()
        conn.executescript(_CREATE)
        payload = [{**b, "synced_at": synced} for b in bars]
        conn.executemany(_UPSERT, payload)
        conn.commit()
        print(f"wrote market_vix_1m rows={len(payload)}", flush=True)
        conn.close()
    else:
        print("dry-run: skip DB write", flush=True)

    if A.json_out:
        out = Path(A.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "title": "VIXTWN 1m OHLC",
            "source": "TAIFEX getVixData → 1m aggregate",
            "session": "day 09:00–13:45 (no night)",
            "start": A.start,
            "end": A.end,
            "n_days": len(by_day),
            "n_bars": len(bars),
            "synced_at": synced,
            "days": dict(sorted(by_day.items())),
        }
        out.write_text(json.dumps(payload, ensure_ascii=False))
        print(f"wrote JSON cache {out} ({out.stat().st_size // 1024} KB)", flush=True)

    print("DONE", flush=True)


if __name__ == "__main__":
    main()
