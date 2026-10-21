#!/usr/bin/env python3
"""竭盡點重放（把某日 raw 逐筆餵進現行 StreakTracker，輸出當日的竭盡事件；2026-10-21）。

為什麼要有：儀表板只存即時事件（biglot_forward/exhaustion_events.jsonl，source=live），啟動前的時段與過去日靠這支補。
口徑與儀表板一致：交易所成交時間、每檔×30 分鐘時段「前 10 日單窗主導量 P90」門檻（PIT：只用目標日之前的 dom_*.pkl）、
爆窗≥3、末 2 窗漲不動、打斷窗要有量（brk_ok）、日均量<3,000 張標的排除。事件另附 5 分鐘後價格變動（只作描述，非訊號）。
輸出 <DATA_DIR>/data/biglot_forward/exhaustion_replay.jsonl（同一天重跑會先清掉該日舊列）；source=replay。
用法: PYTHONPATH=src:scripts/research nice .venv/bin/python scripts/research/biglot_exhaustion_replay.py 2026-10-20 [2026-10-21 ...]
"""
from __future__ import annotations

import bisect
import glob
import json
import pickle
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from biglot.streak import StreakTracker, exch_tod, is_exhaustion  # noqa: E402
from stock_db import DEFAULT_DB_PATH  # noqa: E402

DATA = Path(DEFAULT_DB_PATH).parent
OUT = DATA / "biglot_forward" / "exhaustion_replay.jsonl"
DOM = DATA / "biglot_streak"
RAW = DATA.parent / "cache" / "biglot_live_watch"
LOWVOL_LOTS = 3000.0


def thresholds_for(day: str) -> dict:
    ds = sorted(Path(p).stem.replace("dom_", "") for p in glob.glob(str(DOM / "dom_*.pkl")))
    merged: dict = {}
    for d in [x for x in ds if x < day][-10:]:
        for s, per in pickle.load(open(DOM / f"dom_{d}.pkl", "rb")).items():
            for bk, v in per.items():
                merged.setdefault(s, {}).setdefault(bk, []).append(v)
    out = {}
    for s, per in merged.items():
        t = {int(bk): float(np.quantile(np.concatenate(vs), 0.9)) for bk, vs in per.items() if sum(len(v) for v in vs) >= 50}
        if t:
            out[s] = t
    return out


def replay(day: str) -> list[dict]:
    thr = thresholds_for(day)
    sids = sorted(thr)
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    vols: dict = {}
    for s, d, v in conn.execute("SELECT stock_id, trade_date, MAX(volume) FROM stock_daily_bars WHERE trade_date<? AND trade_date>=date(?,'-30 day') AND volume>0 GROUP BY 1,2", (day, day)):
        vols.setdefault(s, []).append((d, v))
    conn.close()
    tr = StreakTracker()
    low = set()
    for s in sids:
        tr.set_thresholds(s, thr[s])
        L = sorted(vols.get(s, []))[-10:]
        if len(L) >= 3:
            avg = sum(v for _, v in L) / len(L) / 1000.0
            tr.set_ref(s, avg * 0.89 / 265)
            if avg < LOWVOL_LOTS:
                low.add(s)
    f = RAW / f"raw_{day}.jsonl"
    last, first, px = {}, set(), {s: ([], []) for s in sids}
    sidset = set(sids)
    for line in open(f, encoding="utf-8"):
        if '"event": "data"' not in line:
            continue
        r = json.loads(line)
        p = r["payload"]["data"]
        s = p.get("symbol")
        if s not in sidset or p.get("isTrial") or "price" not in p:
            continue
        v = p.get("volume") or 0
        dv = v - last.get(s, 0)
        if dv <= 0:
            continue
        last[s] = v
        ts = datetime.fromisoformat(r["ts"])
        t = exch_tod(p.get("time"), ts.hour * 3600 + ts.minute * 60 + ts.second + ts.microsecond / 1e6)
        aux = bool(p.get("isOpen") or p.get("isClose")) or (s not in first)
        first.add(s)
        tr.on_trade(s, t, p["price"], dv, p.get("bid"), p.get("ask"), auction=aux)
        if not aux:
            px[s][0].append(t); px[s][1].append(p["price"])
    out = []
    for s in sids:
        for e in tr.events(s):
            if e["kind"] != "flip" or not is_exhaustion(e) or s in low:
                continue
            ts_, ps_ = px[s]
            i = bisect.bisect_left(ts_, e["t"]); j = bisect.bisect_left(ts_, e["t"] + 300)
            ret5 = ((ps_[j] / ps_[min(i, len(ps_) - 1)] - 1) * 1e4 * (-e["d"])) if (ps_ and j < len(ps_)) else None
            out.append({"date": day, "sid": s, "t": round(e["t"], 1), "d": e["d"], "n": e["n"], "nb5": e["nb5"], "avg": round(e["avg"], 1), "px": e["px"],
                        "brk_lots": e.get("brk_lots"), "day_up": e.get("day_up"), "aligned": (e["d"] > 0) != bool(e.get("day_up")),
                        "ret5_bps": None if ret5 is None else round(ret5, 1), "thr_src": "p90", "source": "replay"})
    return out


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    keep = []
    days = set(sys.argv[1:])
    if OUT.exists():
        keep = [ln for ln in OUT.read_text(encoding="utf-8").splitlines() if json.loads(ln).get("date") not in days]
    new = []
    for day in sys.argv[1:]:
        rows = replay(day)
        new += [json.dumps(r, ensure_ascii=False) for r in rows]
        print(day, "竭盡", len(rows), "買盤", sum(1 for r in rows if r["d"] > 0), "賣盤", sum(1 for r in rows if r["d"] < 0),
              "順大勢", sum(1 for r in rows if r["aligned"]), "平倉/回補提示", sum(1 for r in rows if not r["aligned"]))
    OUT.write_text("\n".join(keep + new) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
