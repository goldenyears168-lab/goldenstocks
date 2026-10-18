#!/usr/bin/env python3
"""資料產品新鮮度斷言（read-only · 預設 warn-only）。

讀 config/data_products.yaml，逐一量測『資料最新日 vs 期望交易日』與覆蓋率，輸出判級報告。
不寫 DB、不連網。寫入的只有報告檔 <DATA_DIR>/ops/data_products_latest.json（供 mini-schedule 體檢讀取）。

退出碼：預設一律 0（只警告）；加 --strict 時，只要有『違規』（STALE/DEAD/ERROR 或覆蓋率告警，
不含 DORMANT/RETIRED/已知問題）就回 1。信件管道不可信（Gmail 535），所以告警靠退出碼＋報告檔。

  PYTHONPATH=src .venv/bin/python scripts/ops/check_data_products.py
  PYTHONPATH=src .venv/bin/python scripts/ops/check_data_products.py --json
  PYTHONPATH=src .venv/bin/python scripts/ops/check_data_products.py --strict
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from data_products import evaluate, load_products  # noqa: E402
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

DEFAULT_CONFIG = ROOT / "config" / "data_products.yaml"
DEFAULT_REPORT = DATA_DIR / "ops" / "data_products_latest.json"
ORDER = {"ERROR": 0, "DEAD": 1, "STALE": 2, "ORPHAN": 3, "DORMANT": 4, "FRESH": 5, "RETIRED": 6}


def _lv(r) -> str:
    return "ORPHAN" if r.orphan else r.level


def run(config: Path, db: Path, now: datetime) -> list:
    doc = load_products(config)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        return evaluate(doc, conn, now, DATA_DIR, ROOT)
    finally:
        conn.close()


def render(results: list, now: datetime) -> str:
    lines = [f"資料產品新鮮度 · {now:%Y-%m-%d %H:%M}（落後單位：交易日）", ""]
    for r in sorted(results, key=lambda x: (ORDER.get(_lv(x), 9), x.id)):
        if r.level == "RETIRED":
            continue
        age = "" if r.age_td is None else f"落後 {r.age_td}"
        lines.append(f"  {_lv(r):<7} {r.id:<34} 資料 {r.data_day or '-':<10} 期望 {r.expected_day or '-':<12} {age}")
        for n in r.notes:
            lines.append(f"            ⚠ {n}")
        for k in r.known:
            lines.append(f"            · {k}")
    viol = [r for r in results if r.violation]
    cnt = {lv: sum(1 for r in results if _lv(r) == lv) for lv in ORDER}
    lines += ["", "摘要: " + " ".join(f"{k}={v}" for k, v in cnt.items() if v),
              f"違規 {len(viol)} 項" + (": " + ", ".join(r.id for r in viol) if viol else "")]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--no-report", action="store_true", help="不寫報告檔")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有違規時退出碼 1（預設 warn-only 一律 0）")
    args = ap.parse_args()

    now = datetime.now()
    results = run(args.config, args.db, now)
    payload = {"generated_at": now.isoformat(timespec="seconds"),
               "violations": [r.id for r in results if r.violation],
               "products": [{**asdict(r), "violation": r.violation} for r in results]}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render(results, now))
    if not args.no_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if (args.strict and payload["violations"]) else 0


if __name__ == "__main__":
    sys.exit(main())
