#!/usr/bin/env python3
"""「真空」等急跌狀態格 → 個股期貨掛單方式(JOIN / IMP / L2 / 市價)的 tick 重放(唯讀)。

問題(jack 2026-09-24):真空格(近5分≤−0.2% ∧ 30秒主動賣≤40% ∧ 末30秒仍跌≥10bps)是 V2.5 最強多方格,
此時最佳買價的隊伍正在消失,IMP(往內掛一檔)是否比 JOIN 合理?跟著大盤殺時要不要排除?

資料:訊號來自**現股逐筆**(cache/biglot_live_watch/raw_{日}.jsonl,富邦 ws,交易所 µs 時戳),
      執行在**個股期貨**五檔+逐筆({root}_books/{root}_trades,8Hz 快照+完整逐筆)。重疊 2026-09-03~09-22、9 檔。
狀態(每 5 秒格點、同檔同狀態 300 秒冷卻,首次進入才算事件):
  真空       w5≤−20 ∧ sell30s≤0.40 ∧ r30s≤−10
  賣壓未竭   w5≤−20 ∧ sell30s≥0.60
  竭盡已止跌 w5≤−20 ∧ sell30s≤0.40 ∧ r30s>0     (預期負的對照)
  急跌任意   w5≤−20                                (基準)
市場:36 檔等權的 w5 / r30s(同一時刻)。
每個事件在期貨簿模擬買方 JOIN(最佳價隊尾)/ IMP(價差≥2 tick 時往內 1 tick)/ L2(下一檔)/ MKT(打賣一),
存活 120 秒、撤單 phi=0.5;成交後 60s/5m/30m/60m 期貨 mid 對成交價 markout(bps);未成交記 0 並記錄 60 分後 mid 跑掉多少。
用法:PYTHONPATH=src .venv/bin/python scripts/research/vacuum_entry_futures_sim.py
"""
from __future__ import annotations
import json, sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_futures_queue_sim import load_day, best, size_at, _mid, DATA  # noqa: E402
from stock_futures_queue_level2_sim import simulate, step, tick_size, mid_at  # noqa: E402

RAW = Path.home() / "goldenstocks-data/cache/biglot_live_watch"
MAP = {"2492": "HB", "8039": "KI", "6173": "PK", "1303": "CA", "3105": "NA", "6488": "OW", "3532": "QD", "3406": "LE", "3374": "QL",
       "2455": "GU", "3189": "IX", "6147": "NS", "2301": "FQ", "3042": "IT", "2303": "CCF", "2344": "FZ", "5483": "NO"}   # 36 檔中期貨簿持續收到 09-22 的 17 檔(2026-09-24 擴)
TPE = timezone(timedelta(hours=8))
GRID, COOL, HORIZON, PHI = 5, 300, 120, 0.5
STATES = {
    "真空":       lambda w5, s, r: w5 <= -20 and s is not None and s <= 0.40 and r is not None and r <= -10,
    "賣壓未竭":   lambda w5, s, r: w5 <= -20 and s is not None and s >= 0.60,
    "竭盡已止跌": lambda w5, s, r: w5 <= -20 and s is not None and s <= 0.40 and r is not None and r > 0,
    "急跌任意":   lambda w5, s, r: w5 <= -20,
}


def load_stock_day(day: str) -> dict[str, list]:
    """sid → [(t_us, px, amt, sgn)] 連續盤逐筆(去殭屍、用交易所時戳)。全部 36 檔都讀(市場代理用)。"""
    f = RAW / f"raw_{day}.jsonl"
    if not f.exists():
        return {}
    out: dict[str, list] = defaultdict(list); seen = set(); last = {}
    for line in f.open(encoding="utf-8"):
        if '"channel": "trades"' not in line or '"event": "data"' not in line:
            continue
        try:
            d = json.loads(line)["payload"]["data"]
        except Exception:  # noqa: BLE001
            continue
        sid = str(d.get("symbol", "")); px, sz, t = d.get("price"), d.get("size"), d.get("time")
        if not sid or px is None or sz is None or t is None or d.get("isTrial") or d.get("isContinuous") is not True:
            continue
        key = (sid, d.get("serial"))
        if key in seen: continue
        seen.add(key)
        px, sz = float(px), float(sz); b, a = d.get("bid"), d.get("ask")
        side = 1 if (a is not None and px >= float(a)) else (-1 if (b is not None and px <= float(b)) else 0)
        if side == 0:
            p = last.get(sid); side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
        last[sid] = px
        out[sid].append((int(t), px, px * sz * 1000, side))
    for v in out.values():
        v.sort()
    return out


class Tape:
    def __init__(self, arr):
        self.t = [x[0] for x in arr]; self.px = [x[1] for x in arr]; self.amt = [x[2] for x in arr]; self.sgn = [x[3] for x in arr]
    def px_at(self, t_us):
        i = bisect_right(self.t, t_us) - 1
        return self.px[i] if i >= 0 else None
    def feats(self, t_us):
        p0 = self.px_at(t_us); p5 = self.px_at(t_us - 300_000_000); p30 = self.px_at(t_us - 30_000_000)
        if p0 is None or p5 is None: return None
        w5 = (p0 / p5 - 1) * 1e4; r30s = (p0 / p30 - 1) * 1e4 if p30 else None
        i0, i1 = bisect_right(self.t, t_us - 30_000_000), bisect_right(self.t, t_us)
        sb = sum(self.amt[i] for i in range(i0, i1) if self.sgn[i] > 0); ss = sum(self.amt[i] for i in range(i0, i1) if self.sgn[i] < 0)
        s = ss / (sb + ss) if (sb + ss) > 0 else None
        return w5, s, r30s, p0


def main():
    days = sorted(p.name[4:-6] for p in RAW.glob("raw_2026-09-*.jsonl"))
    rows = []
    for day in days:
        stock = load_stock_day(day)
        if not stock: continue
        tapes = {sid: Tape(a) for sid, a in stock.items() if len(a) > 50}
        fut = {}
        for sid, root in MAP.items():
            if sid not in tapes: continue
            books, trades = load_day(root, day)
            if len(books) > 100 and len(trades) > 20:
                fut[sid] = (books, [b["book_time"] for b in books], trades, [t["trade_time"] for t in trades])
        if not fut:
            print(f"{day}: 無期貨資料", flush=True); continue
        t0 = int(datetime.fromisoformat(f"{day}T09:05:00+08:00").timestamp() * 1e6); t1 = int(datetime.fromisoformat(f"{day}T13:20:00+08:00").timestamp() * 1e6)
        last_ev = defaultdict(lambda: -1e18); n_ev = 0
        for t in range(t0, t1, GRID * 1_000_000):
            mk = [tp.feats(t) for tp in tapes.values()]
            mk = [m for m in mk if m]
            if len(mk) < 10: continue
            mkt_w5 = float(np.mean([m[0] for m in mk])); mkt_r30s = float(np.mean([m[2] for m in mk if m[2] is not None] or [0]))
            for sid, (books, bt, trades, tt) in fut.items():
                f = tapes[sid].feats(t)
                if not f: continue
                w5, s, r30s, p0 = f
                for st, fn in STATES.items():
                    if not fn(w5, s, r30s): continue
                    if t - last_ev[(sid, st)] < COOL * 1_000_000: continue
                    last_ev[(sid, st)] = t
                    bi = bisect_left(bt, t)
                    if bi >= len(books) or bt[bi] - t > 10_000_000: continue       # 期貨簿 10 秒內沒快照就略過
                    b = books[bi]; bb, aa = best(b, "bid"), best(b, "ask")
                    if bb is None or aa is None: continue
                    mid0 = (bb[0] + aa[0]) / 2; tick = tick_size(bb[0] - 1e-9); spr_t = round((aa[0] - bb[0]) / tick)
                    plans = {"JOIN": (bb[0], bb[1]), "L2": (step(bb[0], "bid", 1), None)}
                    if spr_t >= 2: plans["IMP"] = (step(bb[0], "bid", -1), 0.0)
                    base = {"day": day, "sid": sid, "hm": datetime.fromtimestamp(t / 1e6, TPE).strftime("%H:%M:%S"), "state": st,
                            "w5": w5, "sell30s": s, "r30s": r30s, "mkt_w5": mkt_w5, "mkt_r30s": mkt_r30s, "spread_ticks": spr_t, "q1": bb[1],
                            "tick_bps": tick / mid0 * 1e4}
                    p60 = tapes[sid].px_at(t + 3600_000_000); base["stk_fwd60"] = (p60 / p0 - 1) * 1e4 if p60 else None
                    for name, (px, q0) in plans.items():
                        q = size_at(b, "bid", px) if q0 is None else q0
                        tf, how = simulate(books, bt, trades, tt, bi, "bid", px, q, HORIZON, PHI)
                        rec = dict(base, strat=name, q0=q, filled=tf is not None, how=how, wait_s=None, mo60=None, mo300=None, mo1800=None, mo3600=None, miss3600=None)
                        if tf is not None:
                            rec["wait_s"] = (tf - t) / 1e6
                            for h in (60, 300, 1800, 3600):
                                m = mid_at(books, bt, tf + h * 1_000_000)
                                if m is not None: rec[f"mo{h}"] = (m - px) / mid0 * 1e4
                        else:
                            m = mid_at(books, bt, t + 3600_000_000)
                            if m is not None: rec["miss3600"] = (m - mid0) / mid0 * 1e4
                        rows.append(rec)
                    m = mid_at(books, bt, t + 3600_000_000); m5 = mid_at(books, bt, t + 300_000_000)
                    rows.append(dict(base, strat="MKT", q0=0, filled=True, how="market", wait_s=0.0, mo60=None,
                                     mo300=((m5 - aa[0]) / mid0 * 1e4) if m5 else None, mo1800=None,
                                     mo3600=((m - aa[0]) / mid0 * 1e4) if m else None, miss3600=None))
                    n_ev += 1
        print(f"{day}: 檔={len(fut)} 事件={n_ev}", flush=True)
    out = Path.home() / "goldenstocks-data/scratch/vacuum_entry_sim_2026-09-24.json"
    out.write_text(json.dumps(rows, ensure_ascii=False)); print(f"rows={len(rows)} → {out}")
    summarize(rows)


def summarize(rows):
    d = pd.DataFrame(rows)
    d["ev3600"] = np.where(d["filled"], d["mo3600"], 0.0); d["ev3600"] = d["ev3600"].fillna(0.0)
    d["ev300"] = np.where(d["filled"], d["mo300"], 0.0); d["ev300"] = d["ev300"].fillna(0.0)
    def tbl(sub, lab):
        n_ev = len(sub[sub["strat"] == "JOIN"])
        print(f"\n==== {lab} (事件 {n_ev}) ====")
        print(f"{'策略':6s}{'n':>5s}{'成交%':>7s}{'等待s':>7s}{'mo60':>7s}{'mo300':>7s}{'mo1800':>8s}{'mo3600':>8s}{'EV300':>7s}{'EV3600':>8s}{'日t':>6s}{'未成交跑掉':>9s}{'現股fwd60':>9s}")
        for s_, g in sub.groupby("strat"):
            f = g[g["filled"]]; ev_day = g.groupby("day")["ev3600"].mean()
            t = ev_day.mean() / (ev_day.std() / np.sqrt(len(ev_day))) if len(ev_day) > 2 and ev_day.std() > 0 else float("nan")
            fm = lambda c: (f[c].mean() if len(f) and f[c].notna().any() else float("nan"))
            print(f"{s_:6s}{len(g):5d}{len(f)/len(g)*100:7.1f}{(f['wait_s'].median() if len(f) else float('nan')):7.1f}{fm('mo60'):+7.1f}{fm('mo300'):+7.1f}{fm('mo1800'):+8.1f}{fm('mo3600'):+8.1f}"
                  f"{g['ev300'].mean():+7.1f}{g['ev3600'].mean():+8.1f}{t:+6.1f}{(g['miss3600'].mean() if g['miss3600'].notna().any() else float('nan')):+9.1f}{g['stk_fwd60'].mean():+9.1f}")
    for st, g in d.groupby("state"):
        tbl(g, f"狀態 {st}")
        if st in ("真空", "賣壓未竭"):
            tbl(g[g["mkt_r30s"] >= 0], f"  {st} × 大盤末30秒≥0(自己殺)"); tbl(g[g["mkt_r30s"] < 0], f"  {st} × 大盤末30秒<0(跟著殺)")
            tbl(g[g["mkt_w5"] <= -20], f"  {st} × 大盤5分≤−0.2%"); tbl(g[g["mkt_w5"] > -20], f"  {st} × 大盤5分>−0.2%")
            tbl(g[g["spread_ticks"] >= 2], f"  {st} × 價差≥2tick"); tbl(g[g["spread_ticks"] < 2], f"  {st} × 價差1tick")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--summarize-only":
        summarize(json.loads((Path.home() / "goldenstocks-data/scratch/vacuum_entry_sim_2026-09-24.json").read_text()))
    else:
        main()
