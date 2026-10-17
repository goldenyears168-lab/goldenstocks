#!/usr/bin/env python3
"""補儀表板名單（_live_calib.json universe）的歷史分點 tape：只抓「不完整」的交易日。

完整＝當日分點買進合計 ≥ stock_daily_bars.volume 的 98%，或分點數 ≥ 100。
  PYTHONPATH=src .venv/bin/python scripts/ops/backfill_dashboard_branch_tape.py --since 2026-07-20 --exclude 6173
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "research"))

from stock_db import DATA_DIR, DEFAULT_DB_PATH, connect  # noqa: E402

import run_expert_pool_watch as pool  # noqa: E402


def incomplete_days(conn, sid: str, since: str, until: str) -> list[str]:
    bars = conn.execute(
        "SELECT trade_date, MAX(volume) FROM stock_daily_bars WHERE stock_id=? AND trade_date BETWEEN ? AND ? AND close>0 GROUP BY trade_date ORDER BY trade_date",
        (sid, since, until),
    ).fetchall()
    tape = {
        d: (n, b)
        for d, n, b in conn.execute(
            "SELECT trade_date, COUNT(*), SUM(buy) FROM stock_broker_branch_daily WHERE stock_id=? AND trade_date BETWEEN ? AND ? GROUP BY trade_date",
            (sid, since, until),
        )
    }
    out = []
    for d, vol in bars:
        n, b = tape.get(d, (0, 0))
        if n >= 100 or (vol and b and b >= 0.98 * vol):
            continue
        out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="2026-12-31")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    skip = {s for s in a.exclude.split(",") if s}
    calib = json.loads((DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json").read_text("utf-8"))
    sids = [str(r["sid"]) for r in calib["universe"] if str(r["sid"]) not in skip]
    conn = connect(DEFAULT_DB_PATH)
    total = 0
    for sid in sids:
        days = incomplete_days(conn, sid, a.since, a.until)
        print(f"[{sid}] 待補 {len(days)} 天", flush=True)
        if days and not a.dry_run:
            counts = pool.refresh_stock_tape(conn, sid, days)
            total += sum(1 for v in counts.values() if v > 0)
    print(f"done: 補到資料 {total} 個 stock-day")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
