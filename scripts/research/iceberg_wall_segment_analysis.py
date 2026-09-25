#!/usr/bin/env python3
"""隱形大戶「牆」的強度分層(2026-09-25 jack 交辦:「大牆會破,小牆會反彈...卡住價格的交易金額
跟當天的交易金額的比例,還有哪些方式可以再做細分」)。

上一輪(iceberg_wall_score_design.py)把 backing/breakout 合併成單一群組測,結果多數是雜訊、
唯一撐過延遲檢定的訊號(breakout_bull)方向還跟假說相反——但那是把「大牆」跟「小牆」混在一起測
的結果,有可能兩種牆的行為根本相反,混在一起平均掉了。這裡把牆按強度分層,分別測,直接檢驗
「大牆會破、小牆會反彈」這個說法本身,而不是假設所有牆都同一種行為。

分層維度(依 jack 提議+市場微結構直覺,依重要性排序):

  1. 牆的相對量(jack 提議):該守價位期間吸收的量(vol_absorbed,張)÷ 當天該股總成交量(張)。
     這是「這道牆吃掉了今天多少比例的成交」,比絕對張數更有意義(呼應 xinxing-950 教訓:
     絕對量要換算成相對量才有意義,不是看絕對數字大小)。
  2. 時間段(呼應原始假說來源——節目講的是「開盤時觀察」):開盤前30分(09:00-09:30)/
     中段(09:30-12:00)/尾盤(12:00-13:30)分開測,因為這個專案已經多次發現同一種訊號在
     不同時段行為完全不同(開盤前15-30分常是離群的特殊時段)。
  3. 打的持續時間(hits 次數,已用作候選門檻,這裡當連續劑量變數分高/低兩組)。

方法論延續前一輪:日聚類 cl_t、延遲30秒進場對照(抓買賣價彈跳偽訊號)、安慰劑對照。

用法:PYTHONPATH=src .venv/bin/python scripts/research/iceberg_wall_segment_analysis.py
"""
from __future__ import annotations
import glob, json, sqlite3, sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, "scripts/research")
sys.path.insert(0, "src")
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402
from biglot_score_v23_fit import cl_t  # noqa: E402

BOOKS_DIR = DATA_DIR / "cache" / "watchlist_books"
MIN_QTY_CHANGE = 5.0
MIN_HITS_CANDIDATE = 3
MIN_REFILLS_CANDIDATE = 2
MIN_HITS_BREAKOUT = 5
ATR_N = 14


def load_universe():
    cal = json.load(open(DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"))
    return {r["sid"]: r["name"] for r in cal["universe"]}


def best_of(price_arr, qty_arr):
    for pp, qq in zip(price_arr, qty_arr):
        if pp is not None and qq is not None and pp > 0:
            return pp, qq
    return None, None


def mid_of(bp, bq, ap, aq):
    b, _ = best_of(bp, bq)
    a, _ = best_of(ap, aq)
    return (b + a) / 2 if (b and a) else None


def tod_bucket(t):
    """t = 'HH:MM:SS'。開盤前30分/中段/尾盤三分法(呼應原假說「開盤觀察」)。"""
    if t < "09:30:00":
        return "開盤前30分"
    if t < "12:00:00":
        return "中段"
    return "尾盤"


def replay_day(rows):
    """回傳 breakout_events(含強度欄位)。不重測 backing——上一輪已證實 backing 兩方向都是
    純雜訊、延遲前後幾乎不變,分層對雜訊沒有意義,聚焦在 breakout(唯一有方向性的部分)。"""
    breakout_events = []
    cur = {"bid": None, "ask": None}
    day_total_v = 0.0
    for ts, t, bp, bq, v, ap, aq in rows:
        if v is not None:
            day_total_v = max(day_total_v, v)
        p_bid, q_bid = best_of(bp, bq)
        p_ask, q_ask = best_of(ap, aq)
        for side, p0, q0 in (("bid", p_bid, q_bid), ("ask", p_ask, q_ask)):
            if p0 is None:
                continue
            c = cur[side]
            if c is None or c["price"] != p0:
                if c and c["hits"] >= MIN_HITS_BREAKOUT and c["refills"] >= MIN_REFILLS_CANDIDATE:
                    if side == "ask" and p0 > c["price"]:
                        breakout_events.append({"ts": c["last_ts"], "t": c["last_t"], "kind": "breakout_bull",
                                                 "hits": c["hits"], "refills": c["refills"],
                                                 "vol_absorbed": c["vol_absorbed"]})
                    elif side == "bid" and p0 < c["price"]:
                        breakout_events.append({"ts": c["last_ts"], "t": c["last_t"], "kind": "breakout_bear",
                                                 "hits": c["hits"], "refills": c["refills"],
                                                 "vol_absorbed": c["vol_absorbed"]})
                cur[side] = {"price": p0, "hits": 0, "refills": 0, "was_hit": False,
                             "last_qty": q0, "last_v": v, "vol_absorbed": 0.0, "last_ts": ts, "last_t": t}
                continue
            c["last_ts"] = ts; c["last_t"] = t
            dq = q0 - c["last_qty"]
            dv = (v - c["last_v"]) if (v is not None and c["last_v"] is not None) else 0.0
            if dq <= -MIN_QTY_CHANGE and dv > 0:
                c["hits"] += 1; c["was_hit"] = True; c["vol_absorbed"] += dv
            elif dq >= MIN_QTY_CHANGE and c["was_hit"]:
                c["refills"] += 1; c["was_hit"] = False
            c["last_qty"] = q0
            if v is not None:
                c["last_v"] = v
    return breakout_events, day_total_v


def main():
    uni = load_universe()
    files = sorted(glob.glob(str(BOOKS_DIR / "watchlist_books_*.jsonl")))
    print(f"掃描 {len(files)} 個交易日,限定 42 檔宇宙\n")

    all_events = []
    for fp in files:
        day = Path(fp).stem.replace("watchlist_books_", "")
        per_sid = defaultdict(list)
        with open(fp) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                sid = d.get("sym")
                if sid not in uni:
                    continue
                try:
                    v = float(d["v"]) if d.get("v") not in (None, "") else None
                except (TypeError, ValueError):
                    v = None
                ts = d.get("ts")
                if ts is None:
                    continue
                per_sid[sid].append((ts, d.get("t", ""), d.get("bp") or [], d.get("bq") or [], v,
                                      d.get("ap") or [], d.get("aq") or []))
        for sid, rows in per_sid.items():
            rows.sort(key=lambda r: r[0])
            seen = set(); dedup = []
            for r in rows:
                if r[0] in seen:
                    continue
                seen.add(r[0]); dedup.append(r)
            mids = [(r[0], mid_of(r[2], r[3], r[5], r[6])) for r in dedup]
            mids = [(t, m) for t, m in mids if m is not None]
            breakout, day_total_v = replay_day(dedup)

            def fwd(from_ts, h):
                target = from_ts + h * 60
                for t, m in mids:
                    if t >= target:
                        return m
                return None

            def px_at(from_ts):
                for t, m in mids:
                    if t >= from_ts:
                        return m
                return None

            for e in breakout:
                px0 = px_at(e["ts"])
                px0_delay = px_at(e["ts"] + 30)
                if not px0 or not day_total_v:
                    continue
                ex15 = ((fwd(e["ts"], 15) / px0 - 1) * 1e4) if fwd(e["ts"], 15) else None
                exd15 = (((fwd(e["ts"] + 30, 15) / px0_delay - 1) * 1e4)
                         if (px0_delay and fwd(e["ts"] + 30, 15)) else None)
                all_events.append({
                    "sid": sid, "day": day, "kind": e["kind"], "hits": e["hits"],
                    "wall_ratio": e["vol_absorbed"] / day_total_v,   # jack 提議的分層維度:牆吸收量÷當天總量
                    "tod": tod_bucket(e["t"]), "ex15": ex15, "exd15": exd15})

    print(f"總 breakout 事件數 {len(all_events)}\n")
    wr = np.array([e["wall_ratio"] for e in all_events])
    print(f"牆佔當日總量比例分布:中位數{np.median(wr)*100:.3f}% · p25={np.percentile(wr,25)*100:.3f}% "
          f"· p75={np.percentile(wr,75)*100:.3f}% · p90={np.percentile(wr,90)*100:.3f}%\n")

    def report(label, events):
        print(f"-- {label}(n={len(events)}) --")
        for h_lbl, key in (("即時", "ex15"), ("延遲30秒", "exd15")):
            vals = [(e[key], e["day"]) for e in events if e.get(key) is not None]
            if len(vals) < 15:
                print(f"    {h_lbl}持15分:n<15({len(vals)}),樣本不足"); continue
            arr = np.array([v for v, _ in vals]); dates = np.array([d for _, d in vals])
            mu, t = cl_t(arr, dates)
            win = float((arr > 0).mean() * 100)
            print(f"    {h_lbl}持15分:n={len(vals):4d} 均{mu:+6.1f}bps(t{t:+4.2f}) 勝率{win:4.1f}%")

    print("==== A. 按『牆佔當日總量比例』分層(檢驗『大牆會破、小牆會反彈』說法本身) ====")
    q50 = np.median(wr)
    for kind in ("breakout_bull", "breakout_bear"):
        sub = [e for e in all_events if e["kind"] == kind]
        big = [e for e in sub if e["wall_ratio"] >= q50]
        small = [e for e in sub if e["wall_ratio"] < q50]
        print(f"\n[{kind}]")
        report("大牆(佔當日量比例 ≥ 中位數)", big)
        report("小牆(佔當日量比例 < 中位數)", small)

    print("\n==== B. 按時間段分層(呼應原假說『開盤時觀察』;開盤前30分 vs 中段 vs 尾盤) ====")
    for kind in ("breakout_bull", "breakout_bear"):
        sub = [e for e in all_events if e["kind"] == kind]
        print(f"\n[{kind}]")
        for tod in ("開盤前30分", "中段", "尾盤"):
            report(tod, [e for e in sub if e["tod"] == tod])

    print("\n==== C. 按持續打的次數(hits)分層(高 hits=打得久/硬,低 hits=剛好過門檻) ====")
    for kind in ("breakout_bull", "breakout_bear"):
        sub = [e for e in all_events if e["kind"] == kind]
        hits_arr = [e["hits"] for e in sub]
        if not hits_arr:
            continue
        q50h = np.median(hits_arr)
        print(f"\n[{kind}](hits 中位數={q50h:.0f})")
        report(f"高強度(hits≥{q50h:.0f})", [e for e in sub if e["hits"] >= q50h])
        report(f"低強度(hits<{q50h:.0f})", [e for e in sub if e["hits"] < q50h])

    print("\n==== D. 雙重分層:大牆 × 開盤前30分(原假說最精確的版本;若哪裡都測不出來,說明這條線該收了) ====")
    for kind in ("breakout_bull", "breakout_bear"):
        sub = [e for e in all_events if e["kind"] == kind and e["wall_ratio"] >= q50 and e["tod"] == "開盤前30分"]
        print(f"\n[{kind}]")
        report("大牆∧開盤前30分", sub)


if __name__ == "__main__":
    main()
