#!/usr/bin/env python3
"""連次/連量『大量』門檻（2026-10-21 jack 選定）：每檔、每個 30 分鐘時段，用前 10 個交易日『單 5 秒窗主導方向成交張數』的第 90 百分位當門檻（PIT）。

為什麼不用「全天均量 × 5」：全天均量對開盤太低（開盤窗窗都爆）、對午後太高（幾乎不爆）；分位數按時段與個股自動校正。
作者（權證小哥）沒公開連量亮燈門檻（知識書 G1）；q=0.90 與『爆窗≥3』『末2窗』都是本 repo 自訂。

流程：raw_*.jsonl（富邦 ws 逐筆；窗時間用交易所成交時間 `time`，與儀表板、期貨一致）→ 逐日壓成 dom_YYYY-MM-DD.pkl（各股各時段的單窗主導量陣列，只做一次）→ 取目標日之前最近 10 日合併 → thresholds.json。
用法: PYTHONPATH=src:scripts/research nice .venv/bin/python scripts/research/biglot_streak_thresholds.py [目標日 YYYY-MM-DD，預設=最新 raw 日的下一個日曆日]
輸出: <DATA_DIR>/data/biglot_streak/{dom_*.pkl, thresholds.json}。唯讀 raw、不碰 stocks.db。
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from biglot.streak import exch_tod  # noqa: E402
from exhaust_lib import bins, classify  # noqa: E402
from stock_db import DEFAULT_DB_PATH  # noqa: E402

DATA = Path(DEFAULT_DB_PATH).parent
RAW = DATA.parent / "cache" / "biglot_live_watch"
OUT = DATA / "biglot_streak"
BUCKET_SEC = 1800
N_BUCKETS = 9
Q = 0.90
WARMUP_DAYS = 10
MIN_SAMPLES = 50


def bucket_of(t: float) -> int:
    return int((t - 9 * 3600) // BUCKET_SEC)


def day_dom(raw: Path) -> dict:
    acc, last, rows = {}, {}, {}
    for line in open(raw, encoding="utf-8"):
        if '"channel": "trades"' not in line or '"event": "data"' not in line:
            continue
        try:
            d = json.loads(line)
            p = d["payload"]["data"]
        except Exception:  # noqa: BLE001
            continue
        if p.get("isTrial") or "price" not in p:
            continue
        s = p["symbol"]
        v = p.get("volume") or 0
        dv = v - last.get(s, 0)
        if dv <= 0:
            continue
        last[s] = v
        ts = datetime.fromisoformat(d["ts"])
        t = exch_tod(p.get("time"), ts.hour * 3600 + ts.minute * 60 + ts.second + ts.microsecond / 1e6)   # 交易所成交時間（與儀表板一致）
        if t < 9 * 3600 - 1 or t > 13 * 3600 + 31 * 60:
            continue
        flag = 1 if (p.get("isOpen") or p.get("isClose")) else 0
        rows.setdefault(s, []).append((t, p["price"], dv, p.get("bid", np.nan), p.get("ask", np.nan), flag))
    out = {}
    for s, r in rows.items():
        if len(r) <= 50:
            continue
        a = np.array(r, dtype="float64")
        a[0, 5] = 1                                   # 當日第一筆＝開盤競價彙總量
        side, _, _ = classify(a)
        bn = bins(a, side)
        if bn is None:
            continue
        o, i, b = bn["o"], bn["i"], bn["b"]
        dom = np.where(o > i, o, np.where(i > o, i, 0.0))
        per = {}
        for k in range(len(b)):
            if dom[k] > 0:
                per.setdefault(bucket_of(b[k] * 5.0), []).append(dom[k])
        out[s] = {bk: np.asarray(v, dtype="float32") for bk, v in per.items()}
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raws = sorted(RAW.glob("raw_2026-*.jsonl"))
    dates = [r.stem.replace("raw_", "") for r in raws]
    for r, d in zip(raws, dates):
        f = OUT / f"dom_{d}.pkl"
        if f.exists():
            continue
        pickle.dump(day_dom(r), open(f, "wb"))
        print("dom", d, flush=True)
    target = sys.argv[1] if len(sys.argv) > 1 else (datetime.strptime(dates[-1], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    use = [d for d in dates if d < target][-WARMUP_DAYS:]
    merged: dict = {}
    for d in use:
        for s, per in pickle.load(open(OUT / f"dom_{d}.pkl", "rb")).items():
            for bk, v in per.items():
                merged.setdefault(s, {}).setdefault(bk, []).append(v)
    thr = {}
    for s, per in merged.items():
        t = {}
        for bk, vs in per.items():
            v = np.concatenate(vs)
            if len(v) >= MIN_SAMPLES:
                t[str(bk)] = float(np.quantile(v, Q))
        if t:
            thr[s] = t
    (OUT / "thresholds.json").write_text(json.dumps(
        {"asof": target, "time_basis": "exchange_time", "dates": use, "q": Q, "bucket_sec": BUCKET_SEC, "min_samples": MIN_SAMPLES, "thr": thr},
        ensure_ascii=False), encoding="utf-8")
    print("thresholds", target, "stocks", len(thr), "from", use[0] if use else None, "to", use[-1] if use else None)


if __name__ == "__main__":
    main()
