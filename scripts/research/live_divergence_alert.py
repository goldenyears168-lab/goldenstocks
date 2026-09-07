#!/usr/bin/env python3
"""盤中即時警示:揪「散戶推、大戶倒」的背離（環球晶型）。

判準（2026-09-07 從環球晶案例定）:某檔今天在漲（vs 昨收 >0），但
**大戶累積淨買為負**（Δvolume+Lee-Ready，用修好的邏輯,不用 data.size），
且**散戶主動買占比高**→ 散戶在推、大戶在倒,追高易套。反面=真抬轎(大戶淨買為正)。

用法: PYTHONPATH=src .venv/bin/python scripts/research/live_divergence_alert.py [YYYY-MM-DD]
不給日期=今天(台北)。讀 ${GOLDENSTOCKS_DATA_DIR}/cache/biglot_live_watch/raw_{date}.jsonl。
大戶=單筆成交金額≥500萬;散戶=單筆≤2張;中間戶=其間。
"""
from __future__ import annotations
import json, os, sqlite3, sys, collections
from datetime import datetime, timezone, timedelta
from stock_db import DATA_DIR, DEFAULT_DB_PATH

TPE = timezone(timedelta(hours=8))
BIG = 5_000_000
RETAIL_LOTS = 2
date = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TPE).strftime("%Y-%m-%d")


def prev_close(con, s):
    for src in ("twse_mi_index", "finmind"):
        r = con.execute("SELECT close FROM stock_daily_bars WHERE stock_id=? AND source=? "
                        "AND trade_date < ? ORDER BY trade_date DESC LIMIT 1", (s, src, date)).fetchone()
        if r and r[0]:
            return r[0]


def main():
    v3 = json.loads((DATA_DIR / "cache/pit_universe_tick/_hivol_universe_v3.json").read_text())
    name = {str(r["sid"]): r["name"] for r in v3["universe"]}
    raw = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{date}.jsonl"
    if not raw.exists():
        raw = DATA_DIR / "cache" / "biglot_live_watch" / f"raw_{date}.jsonl"
    if not raw.exists():
        print(f"找不到 {raw}"); return
    prev = {}
    hi = {}; last = {}
    bignet = collections.Counter(); retbuy = collections.Counter(); allbuy = collections.Counter()
    for line in raw.open():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        pl = m.get("payload")
        if not (isinstance(pl, dict) and pl.get("event") == "data"):
            continue
        d = pl.get("data") or {}
        s = str(d.get("symbol"))
        if s not in name or d.get("isTrial"):
            continue
        p, v, bid, ask = d.get("price"), d.get("volume"), d.get("bid"), d.get("ask")
        if p is None or v is None:
            continue
        p = float(p); dv = v - prev.get(s, 0); prev[s] = v
        last[s] = p; hi[s] = max(hi.get(s, 0), p)
        if dv <= 0 or d.get("isContinuous") is not True:
            continue
        side = 1 if (ask and p >= float(ask)) else (-1 if (bid and p <= float(bid)) else 0)
        if side > 0:
            allbuy[s] += dv
            if dv <= RETAIL_LOTS:
                retbuy[s] += dv
        if p * dv * 1000 >= BIG and side:
            bignet[s] += side * dv

    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    rows = []
    for s in last:
        pc = prev_close(con, s)
        chg = (last[s] / pc - 1) * 100 if pc else None
        share = retbuy[s] / allbuy[s] * 100 if allbuy[s] else 0
        frm_hi = (last[s] / hi[s] - 1) * 100 if hi[s] else 0
        rows.append((s, chg, bignet[s], share, frm_hi))

    print(f"=== 盤中背離警示 {date}（{datetime.now(TPE):%H:%M:%S}）===\n")
    # 散戶推大戶倒:漲 + 大戶淨賣 + 散戶主買占比高
    alert = [r for r in rows if r[1] is not None and r[1] > 0 and r[2] < 0 and r[3] >= 45]
    alert.sort(key=lambda r: (r[2], -r[3]))   # 大戶賣越多、散戶占比越高越前
    print("⚠ 散戶推、大戶倒（漲 + 大戶淨賣 + 散戶主買≥45%）— 追高易套:")
    print(f"  {'代號 名稱':<11}{'漲%':>6}{'大戶淨(張)':>11}{'散戶主買%':>9}{'距高%':>7}")
    for s, chg, bn, sh, fh in alert:
        print(f"  {s} {name[s]:<7}{chg:>+6.1f}{bn:>+11,.0f}{sh:>8.0f}%{fh:>+7.1f}")
    if not alert:
        print("  （無）")
    # 真抬轎對照
    ramp = sorted([r for r in rows if r[1] is not None and r[1] > 0 and r[2] > 0],
                  key=lambda r: -r[2])[:5]
    print("\n✓ 對照·真抬轎（漲 + 大戶淨買）:")
    for s, chg, bn, sh, fh in ramp:
        print(f"  {s} {name[s]:<7}{chg:>+6.1f}{bn:>+11,.0f}  散戶主買{sh:>3.0f}%")


if __name__ == "__main__":
    main()
