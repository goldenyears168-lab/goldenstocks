#!/usr/bin/env python3
"""開盤前最後一刻「試撮價／買一量突然消失」偵測 + 開盤後 30 分鐘報酬統計（現貨＋個股期貨）。

事件（開盤前 W 秒內，與開盤前 5 分鐘到 30 秒的區間比較）：
  fade_up    試撮價先被拉高、最後回落   peak/last-1 >= PX_THR
  fade_down  試撮價先被砸低、最後回升   last/trough-1 >= PX_THR
  bid_vanish 買一量（bid1）區間峰值 >= MIN_SIZE，最後 <= 30% 峰值
  ask_vanish 賣一量同理

資料：
  現貨 fubon_premarket_quote_snapshot（只有 4~10 檔，5 秒一筆，08:29~09:00）
       開盤價／+30 分價取自 cache/pit_universe_tick 逐筆（僅 2026-03~09 成交值前 100 大）
  期貨 cache/futopt_mis_depth/mis_depth_*.jsonl（45 檔個股期貨，日盤 08:45 開）
       事件窗 08:43:30~08:45；開盤價 = 08:45 後第一筆有量的 CLastPrice；+30 分 = 09:15

用法：PYTHONPATH=src .venv/bin/python scripts/research/premarket_vanish_detect.py [--out FILE]
唯讀 DB；不下單。
"""
from __future__ import annotations
import argparse, json, math, os, sqlite3, statistics as st, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("GOLDENSTOCKS_DATA_DIR") or Path.home() / "goldenstocks-data")
DB = ROOT / "data" / "stocks.db"
TICK = ROOT / "data" / "cache" / "pit_universe_tick"
MIS = ROOT / "cache" / "futopt_mis_depth"


def hms(s: str) -> int:
    return int(s[0:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:8])


def fnum(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def detect(series, open_s, win, px_thr, min_size, ratio):
    """series: [(sec, trial_px, bid1_sz, ask1_sz)] 已排序。回傳事件標籤 set；None=窗內無資料。"""
    base = [r for r in series if open_s - 300 <= r[0] < open_s - win]
    last = [r for r in series if open_s - win <= r[0] < open_s]
    if not base or not last:
        return None
    ev = set()
    bp = [r[1] for r in base if r[1]]
    lp = last[-1][1]
    if bp and lp:
        if max(bp) / lp - 1 >= px_thr:
            ev.add("fade_up")
        if lp / min(bp) - 1 >= px_thr:
            ev.add("fade_down")
    for idx, name in ((2, "bid_vanish"), (3, "ask_vanish")):
        bs = [r[idx] for r in base if r[idx] is not None]
        ls = [r[idx] for r in last if r[idx] is not None]
        if bs and ls and max(bs) >= min_size and ls[-1] <= ratio * max(bs):
            ev.add(name)
    return ev


def spot_rows(win, px_thr, min_size, ratio):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = ("select stock_id, trade_date, substr(poll_ts,12,8), last_trial_price, bid_sizes, ask_sizes "
         "from fubon_premarket_quote_snapshot where substr(poll_ts,12,5)>='08:55' "
         "order by stock_id, trade_date, poll_ts")
    by = defaultdict(list)
    for sid, d, t, p, bs, az in con.execute(q):
        def first(x):
            try:
                v = json.loads(x)
                return float(v[0]) if v else None
            except Exception:
                return None
        by[(sid, d)].append((hms(t), fnum(p), first(bs), first(az)))
    out, universe = [], 0
    for (sid, d), s in by.items():
        ev = detect(s, 9 * 3600, win, px_thr, min_size, ratio)
        if ev is None:
            continue
        r = tick_ret(sid, d)
        if r is None:
            continue
        universe += 1
        out.append((sid, d, ev, r))
    return out, len(by)


def tick_ret(sid, d, horizon=1800):
    f = TICK / f"{sid}_{d}.json"
    if not f.exists():
        return None
    try:
        ticks = json.load(open(f))
    except Exception:
        return None
    o = e = None
    for t in ticks:
        s = hms(t["Time"])
        if s >= 9 * 3600 and o is None:
            o = t["deal_price"]
        if 9 * 3600 <= s <= 9 * 3600 + horizon:
            e = t["deal_price"]
    return (e / o - 1) if o and e and o > 0 else None


def fut_rows(win, px_thr, min_size, ratio):
    out, seen = [], 0
    for f in sorted(MIS.glob("mis_depth_*.jsonl")):
        day = f.stem.split("_")[-1]
        by = defaultdict(list)
        for line in open(f):
            d = json.loads(line)
            t = d["ts"][11:19]
            s = hms(t)
            if not (8 * 3600 + 29 * 60 <= s <= 9 * 3600 + 15 * 60 + 60):
                continue
            by[d["SymbolID"]].append((s, fnum(d["CLastPrice"]), fnum(d["CTotalVolume"]),
                                      fnum(d["CBidSize1"]), fnum(d["CAskSize1"]), d.get("SpotID")))
        for sym, rows in by.items():
            rows.sort()
            open_s = 8 * 3600 + 45 * 60
            ser = [(r[0], r[1] if not r[2] else None, r[3], r[4]) for r in rows if r[0] < open_s]
            ev = detect(ser, open_s, win, px_thr, min_size, ratio)
            if ev is None:
                continue
            seen += 1
            o = next((r[1] for r in rows if r[0] >= open_s and r[2] and r[1]), None)
            e = [r[1] for r in rows if open_s <= r[0] <= open_s + 1800 and r[1]]
            if o and e:
                out.append((sym, day, ev, e[-1] / o - 1))
    return out, seen


def stats(rets):
    n = len(rets)
    if n == 0:
        return "n=0"
    m = sum(rets) / n
    sd = st.pstdev(rets) if n > 1 else 0
    t = m / (sd / math.sqrt(n)) if sd else float("nan")
    win = sum(r > 0 for r in rets) / n
    return f"n={n:<5} 均值 {m*1e4:+7.1f}bps  中位 {st.median(rets)*1e4:+7.1f}bps  勝率 {win:.0%}  t={t:+.2f}"


def report(title, rows, covered):
    names = sorted({e for r in rows for e in r[2]})
    syms = len({r[0] for r in rows})
    days = len({r[1] for r in rows})
    L = [f"### {title}", f"可算標的 {syms} 檔 × {days} 天，標的日 {len(rows)}（有盤前資料 {covered}）", ""]
    L.append(f"- 全部（基準）  {stats([r[3] for r in rows])}")
    for n in names:
        sub = [r[3] for r in rows if n in r[2]]
        L.append(f"- {n:<10} {stats(sub)}  事件標的 {len({r[0] for r in rows if n in r[2]})} 檔")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=int, default=60, help="事件窗秒數（開盤前）")
    ap.add_argument("--px-thr", type=float, default=0.003)
    ap.add_argument("--min-size", type=float, default=20)
    ap.add_argument("--ratio", type=float, default=0.3)
    ap.add_argument("--out")
    a = ap.parse_args()
    k = (a.win, a.px_thr, a.min_size, a.ratio)
    sp, sc = spot_rows(*k)
    fu, fc = fut_rows(*k)
    txt = f"# 開盤前試撮消失事件 → 開盤後 30 分報酬（{datetime.now():%F}）\n參數 win={a.win}s px_thr={a.px_thr} min_size={a.min_size} ratio={a.ratio}\n\n"
    txt += report("現貨（開盤 09:00 → 09:30）", sp, sc) + "\n\n" + report("個股期貨（開盤 08:45 → 09:15）", fu, fc) + "\n"
    print(txt)
    if a.out:
        Path(a.out).write_text(txt)


if __name__ == "__main__":
    main()
