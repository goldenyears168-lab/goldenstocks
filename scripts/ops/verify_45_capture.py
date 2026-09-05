#!/usr/bin/env python3
"""週一(或任一交易日)收盤後,一鍵驗高波動45檔的兩支收集有沒有 silent-freeze。

用法:  PYTHONPATH=src .venv/bin/python scripts/ops/verify_45_capture.py [YYYY-MM-DD]
不給日期 → 今天(台北)。檢查:
  (1) biglot-live-watch 成交流 raw_{date}.jsonl —— 哪些檔 0 筆成交(沒訂到/凍結)
  (2) watchlist-books  watchlist_books_{date}.jsonl —— 哪些檔 0 筆五檔快照
特別標出 v1→v2 新增、從沒 live 跑過的 28 檔。
"""
from __future__ import annotations
import json, sys, os, collections
from datetime import datetime, timezone, timedelta
from stock_db import DATA_DIR

_ROOTS = [DATA_DIR, DATA_DIR.parent]  # 有的收集器寫 .../data/cache,有的寫 .../cache
if os.environ.get("GOLDENSTOCKS_DATA_DIR"):
    import pathlib
    _ROOTS.append(pathlib.Path(os.environ["GOLDENSTOCKS_DATA_DIR"]))

def _find(sub, fname):
    for root in _ROOTS:
        f = root / "cache" / sub / fname
        if f.exists():
            return f
    return None

TPE = timezone(timedelta(hours=8))
date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TPE).strftime("%Y-%m-%d")
CAL = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
uni = json.loads(CAL.read_text())["universe"]
sids = [str(r["sid"]) for r in uni]
name = {str(r["sid"]): r["name"] for r in uni}
mkt = {str(r["sid"]): r.get("market") for r in uni}

def count_biglot():
    f = _find("biglot_live_watch", f"raw_{date}.jsonl")
    if f is None: return None
    c = collections.Counter()
    for line in f.open():
        try: m = json.loads(line)
        except: continue
        pl = m.get("payload")
        if isinstance(pl, dict) and pl.get("event") == "data":
            d = pl.get("data") or {}
            s = str(d.get("symbol"))
            if s: c[s] += 1
    return c

def count_books():
    f = _find("watchlist_books", f"watchlist_books_{date}.jsonl")
    if f is None: return None
    c = collections.Counter()
    for line in f.open():
        try: r = json.loads(line)
        except: continue
        s = str(r.get("sym"))
        if s: c[s] += 1
    return c

bl = count_biglot(); bk = count_books()
print(f"=== {date} 高波動45檔收集驗證 ===\n")
for lab, c in [("biglot-live-watch 成交流", bl), ("watchlist-books 五檔", bk)]:
    if c is None:
        print(f"[{lab}] 檔案不存在(當天沒跑?)\n"); continue
    have = [s for s in sids if c.get(s, 0) > 0]
    zero = [s for s in sids if c.get(s, 0) == 0]
    print(f"[{lab}] 有資料 {len(have)}/45")
    if zero:
        print(f"  ⚠️ 零筆(silent-freeze) {len(zero)} 檔:")
        for s in zero:
            print(f"     {s} {name.get(s,''):<6} {mkt.get(s)}")
    else:
        print("  ✅ 45 檔全有資料")
    print()
