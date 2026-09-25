#!/usr/bin/env python3
"""從逐筆重建小時K(09:00-10:00/10:00-11:00/11:00-12:00/12:00-13:00/13:00-13:30),供「關鍵一條線」小時線版研究。
資料源:pit_universe_tick(100 檔×127 日逐筆)。輸出 scratch/biglot_panels/pit100_hourly_2026-03_08.csv。
用法:PYTHONPATH=src .venv/bin/python scripts/research/key_line_hourly_tick_features.py [--workers 4]
"""
from __future__ import annotations
import argparse, csv, json, sys
from multiprocessing import Pool
from pathlib import Path

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OUT = Path.home() / "goldenstocks-data/scratch/biglot_panels/pit100_hourly_2026-03_08.csv"
HOURS = [("09:00", "10:00"), ("10:00", "11:00"), ("11:00", "12:00"), ("12:00", "13:00"), ("13:00", "13:30")]


def _sec(t: str) -> float:
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + float(t[6:] or 0)


def _sec_hm(t: str) -> float:
    return int(t[:2]) * 3600 + int(t[3:5]) * 60


def one(path: Path) -> list[list]:
    try:
        ticks = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    sid, day = path.stem.split("_", 1)
    bounds = [(_sec_hm(a), _sec_hm(b)) for a, b in HOURS]
    buckets = [[] for _ in bounds]
    for x in ticks:
        t = x.get("Time") or ""
        px = x.get("deal_price")
        if len(t) < 8 or px in (None, ""):
            continue
        try:
            pxf = float(px)
        except (TypeError, ValueError):
            continue
        s = _sec(t)
        for i, (lo, hi) in enumerate(bounds):
            if lo <= s < hi:
                buckets[i].append((s, pxf))
                break
    rows = []
    for (a, b), arr in zip(HOURS, buckets):
        if not arr:
            continue
        arr.sort()
        px = [p for _, p in arr]
        rows.append([sid, day, a, px[0], max(px), min(px), px[-1], len(px)])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--workers", type=int, default=4); a = ap.parse_args()
    files = sorted(TICK.glob("*_2026-*.json"))
    print(f"files={len(files)}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with OUT.open("w", newline="", encoding="utf-8") as f, Pool(a.workers) as pool:
        w = csv.writer(f); w.writerow(["sid", "date", "hour", "open", "high", "low", "close", "n_ticks"])
        for i, rows in enumerate(pool.imap_unordered(one, files, chunksize=20)):
            w.writerows(rows); n += len(rows)
            if i % 2000 == 0:
                print(f"{i}/{len(files)} rows={n}", flush=True)
    print(f"done rows={n} → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
