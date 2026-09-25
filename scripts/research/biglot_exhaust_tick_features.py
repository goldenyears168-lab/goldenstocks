#!/usr/bin/env python3
"""從 pit_universe_tick(100 檔 × 127 日逐筆,FinMind TickType 1=買方主動/2=賣方主動)算每個 5 分桶收盤時的
「竭盡」特徵,供 biglot_exhaust_combo_test.py 與 V2.x 盤中分。唯讀,不碰 DB。

每桶(label=起點,窗 [L, L+5m))在桶收前算:
  sell30s / sell60s / sell5m  主動賣金額占比(賣÷(買+賣)),與儀表板 sell30s_r 同口徑(金額加權、只算能判方向的)
  act30s   桶內最後 30 秒成交量 ÷ (桶總量/10)  (<1 = 量能塌陷)
  n30s     最後 30 秒筆數
  r30s     最後 30 秒報酬 bps(桶收價 ÷ L+4:30 價 −1)
輸出 scratch/biglot_panels/pit100_bucket5_exhaust_2026-03_08.csv(sid,date,bucket,...)

用法:PYTHONPATH=src .venv/bin/python scripts/research/biglot_exhaust_tick_features.py [--workers 4]
"""
from __future__ import annotations
import argparse, csv, json, sys
from multiprocessing import Pool
from pathlib import Path

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OUT = Path.home() / "goldenstocks-data/scratch/biglot_panels/pit100_bucket5_exhaust_2026-03_08.csv"
BUCKETS = [f"{h:02d}:{m:02d}" for h in range(9, 14) for m in range(0, 60, 5) if f"{h:02d}:{m:02d}" <= "13:20"]


def _sec(t: str) -> float:
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + float(t[6:])


def one(path: Path) -> list[list]:
    try:
        ticks = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    sid, day = path.stem.split("_", 1)
    rows = []
    # 依桶分組
    by = {b: [] for b in BUCKETS}
    for x in ticks:
        t = x.get("Time") or ""
        if len(t) < 8: continue
        s = _sec(t)
        if s < 9 * 3600 or s >= 13 * 3600 + 25 * 60: continue
        b = f"{int(s // 3600):02d}:{int((s % 3600) // 300) * 5:02d}"
        if b in by:
            by[b].append((s, float(x["deal_price"]), float(x["volume"]), str(x.get("TickType", "0"))))
    for b, arr in by.items():
        if not arr: continue
        L = _sec(b + ":00"); end = L + 300
        vol_all = sum(v for _, _, v, _ in arr)
        def share(win):
            sb = ss = 0.0
            for s, p, v, tt in arr:
                if s >= end - win:
                    if tt == "1": sb += p * v
                    elif tt == "2": ss += p * v
            return (ss / (sb + ss)) if (sb + ss) > 0 else ""
        last30 = [(s, p, v) for s, p, v, _ in arr if s >= end - 30]
        v30 = sum(v for _, _, v in last30)
        act = (v30 / (vol_all / 10)) if vol_all > 0 else ""
        px_end = arr[-1][1]
        px_430 = next((p for s, p, v, _ in reversed(arr) if s < end - 30), None)
        r30s = ((px_end / px_430 - 1) * 1e4) if px_430 else ""
        rows.append([sid, day, b, share(30), share(60), share(300), act, len(last30), r30s, px_end])
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workers", type=int, default=4); a = ap.parse_args()
    files = sorted(TICK.glob("*_2026-*.json"))
    print(f"files={len(files)}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with OUT.open("w", newline="", encoding="utf-8") as f, Pool(a.workers) as pool:
        w = csv.writer(f); w.writerow(["sid", "date", "bucket", "sell30s", "sell60s", "sell5m", "act30s", "n30s", "r30s", "px_end"])
        for i, rows in enumerate(pool.imap_unordered(one, files, chunksize=20)):
            w.writerows(rows); n += len(rows)
            if i % 1000 == 0: print(f"{i}/{len(files)} rows={n}", flush=True)
    print(f"done rows={n} → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
