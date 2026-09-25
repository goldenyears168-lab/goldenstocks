#!/usr/bin/env python3
"""「分數達標就掛」還是「達標持續 N 秒才掛」——真空狀態的秒級持續性檢定(pit100 逐筆,5 秒格點)。

狀態(與儀表板 V2.5 同口徑,秒級):真空 = 近5分 ≤−20bps ∧ 近30秒主動賣占比 ≤40% ∧ 近30秒 ≤−10bps。
每個「首次進入」事件記:持續秒數(狀態連續為真多久)、進入當下/30s/60s/120s 後的價(=延遲進場價)、
+60 分價、同時刻市場(100 檔等權)近5分與近30秒、同一時刻其他檔是否也在真空(群發數)。
輸出 scratch/vacuum_persistence_2026-03_08.csv;摘要印出:依持續秒數分箱的 +60 分報酬、延遲 N 秒進場的報酬。
用法:PYTHONPATH=src .venv/bin/python scripts/research/biglot_vacuum_persistence_tick.py [--workers 4]
"""
from __future__ import annotations
import argparse, csv, json, sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path
import numpy as np

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OUT = Path.home() / "goldenstocks-data/scratch/vacuum_persistence_2026-03_08.csv"
G0, G1, STEP = 9 * 3600 + 35 * 60, 12 * 3600 + 25 * 60, 5


def _sec(t: str) -> float:
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + float(t[6:])


def feats(path: Path):
    """回傳 (sid, grid, w5, r30s, sell30s, px_at(grid), px_at(grid+3600), px_at(grid+30/60/120))。"""
    try:
        ticks = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    sid = path.stem.split("_", 1)[0]
    t, px, amt, side = [], [], [], []
    for x in ticks:
        tt = x.get("Time") or ""
        if len(tt) < 8: continue
        s = _sec(tt)
        if s < 9 * 3600 or s >= 13 * 3600 + 30 * 60: continue
        t.append(s); p = float(x["deal_price"]); px.append(p); amt.append(p * float(x["volume"]) * 1000); side.append(str(x.get("TickType", "0")))
    if len(t) < 200: return None
    t = np.array(t); px = np.array(px); amt = np.array(amt); side = np.array(side)
    order = np.argsort(t, kind="stable"); t, px, amt, side = t[order], px[order], amt[order], side[order]
    cb = np.concatenate([[0], np.cumsum(amt * (side == "1"))]); cs = np.concatenate([[0], np.cumsum(amt * (side == "2"))])
    grid = np.arange(G0, G1 + 1, STEP, dtype=float)
    def px_at(ts):
        i = np.searchsorted(t, ts, side="right") - 1
        return np.where(i >= 0, px[np.clip(i, 0, len(px) - 1)], np.nan)
    p0 = px_at(grid); p5 = px_at(grid - 300); p30 = px_at(grid - 30)
    i1 = np.searchsorted(t, grid, side="right"); i0 = np.searchsorted(t, grid - 30, side="right")
    sb = cb[i1] - cb[i0]; ss = cs[i1] - cs[i0]
    sell = np.where(sb + ss > 0, ss / np.maximum(sb + ss, 1e-9), np.nan)
    return sid, grid, (p0 / p5 - 1) * 1e4, (p0 / p30 - 1) * 1e4, sell, p0, px_at(grid + 3600), px_at(grid + 30), px_at(grid + 60), px_at(grid + 120), px_at(grid + 300)


def one_day(args):
    day, files = args
    res = [r for r in (feats(f) for f in files) if r]
    if len(res) < 20: return []
    W5 = np.array([r[2] for r in res]); R30S = np.array([r[3] for r in res])
    mkt5 = np.nanmean(W5, axis=0); mkt30s = np.nanmean(R30S, axis=0)
    vac_all = np.array([(r[2] <= -20) & (r[4] <= 0.40) & (r[3] <= -10) for r in res])
    n_vac = np.nansum(vac_all, axis=0)
    rows = []
    for (sid, grid, w5, r30s, sell, p0, p60, p30s, p60s, p120s, p300s), vac in zip(res, vac_all):
        v = np.nan_to_num(vac, nan=False).astype(bool)
        starts = np.where(v & ~np.concatenate([[False], v[:-1]]))[0]
        for i in starts:
            j = i
            while j + 1 < len(v) and v[j + 1]: j += 1
            dur = (j - i + 1) * STEP
            if np.isnan(p0[i]) or np.isnan(p60[i]): continue
            rows.append([sid, day, f"{int(grid[i]//3600):02d}:{int(grid[i]%3600//60):02d}:{int(grid[i]%60):02d}", dur, w5[i], r30s[i], sell[i],
                         mkt5[i], mkt30s[i], int(n_vac[i]),
                         (p60[i] / p0[i] - 1) * 1e4,
                         (p60[i] / p30s[i] - 1) * 1e4 if p30s[i] else np.nan, (p60[i] / p60s[i] - 1) * 1e4 if p60s[i] else np.nan,
                         (p60[i] / p120s[i] - 1) * 1e4 if p120s[i] else np.nan, (p60[i] / p300s[i] - 1) * 1e4 if p300s[i] else np.nan,
                         (p30s[i] / p0[i] - 1) * 1e4 if p30s[i] else np.nan, (p120s[i] / p0[i] - 1) * 1e4 if p120s[i] else np.nan])
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workers", type=int, default=4); a = ap.parse_args()
    by = defaultdict(list)
    for f in TICK.glob("*_2026-*.json"): by[f.stem.split("_", 1)[1]].append(f)
    jobs = sorted(by.items()); print(f"days={len(jobs)}", flush=True)
    n = 0
    with OUT.open("w", newline="", encoding="utf-8") as fh, Pool(a.workers) as pool:
        w = csv.writer(fh); w.writerow(["sid", "date", "hms", "dur_s", "w5", "r30s", "sell30s", "mkt5", "mkt30s", "n_vac", "r60_0", "r60_d30", "r60_d60", "r60_d120", "r60_d300", "r_0_30", "r_0_120"])
        for i, rows in enumerate(pool.imap_unordered(one_day, jobs, chunksize=2)):
            w.writerows(rows); n += len(rows)
            if i % 20 == 0: print(f"{i}/{len(jobs)} events={n}", flush=True)
    print(f"done events={n} → {OUT}")


if __name__ == "__main__":
    sys.exit(main())
