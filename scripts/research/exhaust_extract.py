#!/usr/bin/env python3
"""連次/連量 買盤竭盡研究 — 階段 1：ws 逐筆 → 緊湊面板（2026-10-17）.

來源 cache/biglot_live_watch/raw_YYYY-MM-DD.jsonl（富邦 ws trades，有 bid/ask、累積 volume、無 TickType）。
單筆量一律用累積 volume 增量 dv（Σsize 會灌水 2.7~4.1 倍，見 fubon-ws-trades-dual-stream-pitfalls）。
排除 isTrial；開/收盤競價(isOpen/isClose)只推進量不算內外盤。處置分盤股不帶 isContinuous → 排除法。
輸出 OUT/{date}.pkl = {sym: ndarray[t_sec, price, dv, bid, ask, flag]}，flag: 0=連續盤 1=競價。
用法: PYTHONPATH=src nice .venv/bin/python scripts/research/exhaust_extract.py [OUT]
"""
from __future__ import annotations
import json, pickle, sys
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from biglot.streak import exch_tod  # noqa: E402

RAW = Path.home() / "goldenstocks-data/cache/biglot_live_watch"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/exhaust_panel")


def one(f: Path):
    date = f.stem.replace("raw_", "")
    out = Path(OUT) / f"{date}.pkl"
    if out.exists():
        return date, -1
    acc, last, rows = {}, {}, {}
    for l in open(f, encoding="utf-8"):
        if '"channel": "trades"' not in l or '"event": "data"' not in l:
            continue
        try:
            d = json.loads(l)
            p = d["payload"]["data"]
        except Exception:  # noqa: BLE001
            continue
        if p.get("isTrial") or "price" not in p:
            continue
        s = p["symbol"]; v = p.get("volume") or 0
        dv = v - last.get(s, 0)
        if dv <= 0:
            continue
        last[s] = v
        ts = datetime.fromisoformat(d["ts"])
        t = exch_tod(p.get("time"), ts.hour * 3600 + ts.minute * 60 + ts.second + ts.microsecond / 1e6)   # 交易所成交時間（2026-10-21 起；舊面板用收到時間）
        if t < 9 * 3600 - 1 or t > 13 * 3600 + 31 * 60:
            continue
        flag = 1 if (p.get("isOpen") or p.get("isClose")) else 0
        rows.setdefault(s, []).append((t, p["price"], dv, p.get("bid", np.nan), p.get("ask", np.nan), flag))
    pan = {s: np.array(r, dtype="float64") for s, r in rows.items() if len(r) > 50}
    pickle.dump(pan, open(out, "wb"))
    return date, len(pan)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    files = sorted(RAW.glob("raw_2026-*.jsonl"))
    with Pool(3) as pool:
        for r in pool.imap_unordered(one, files):
            print(r, flush=True)
