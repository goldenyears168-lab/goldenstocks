#!/usr/bin/env python3
"""個股期貨 · 「排在最佳價後面」vs「掛在下一檔當第一單」——被動限價單的排隊技巧對照（唯讀）。

jack 的假設（2026-09-24）：最佳價隊伍太長時，輪到我之前市場多半已經吃到下一檔；
不如直接掛在下一檔（往遠離 mid 方向 1 tick）當**第一單**，省一檔價、又不用排隊。

四張假想單（買方；賣方對稱），同一時點 t0、同一存活期 H：
  JOIN   掛 b1，排在既有量後面（q_ahead = q1）
  L2     掛 b1 − 1 tick，q_ahead = 該價既有量（常為 0 → 「第一單」）；另切 L2 空/不空
  L3     掛 b1 − 2 tick（對照：再退一檔值不值）
  IMP    價差 > 1 tick 時掛 b1 + 1 tick（隊頭，付 1 tick；價差 = 1 tick 時不掛）
  MKT    對照：立刻用 a1 買（成本 = 半價差＋衝擊）
排隊機制沿用 stock_futures_queue_sim.simulate_one（交易所時間戳、8Hz 快照、逐筆消耗、
穿越即成交、取消 phi 上下界）。

輸出每策略：成交率、中位等待、成交後 30/60/120 秒 mid 對成交價的 markout（tick 與 bps）、
每次嘗試的 EV（未成交記 0）、未成交時價格跑掉多少；再依 q1 深淺三分位、L2 是否為空切。
每日分解後算 t（日為獨立單位）。

用法：
  PYTHONPATH=src .venv/bin/python scripts/research/stock_futures_queue_level2_sim.py \
      --roots OW,GU,QD,NA,LE,IX --horizon 120 --grid 60 --out ~/goldenstocks-data/scratch/queue_l2_sim.json
"""
from __future__ import annotations
import argparse, json, statistics as st, sys
from bisect import bisect_left
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stock_futures_queue_sim import load_day, best, size_at, _mid, _spread, _hhmm, DATA  # noqa: E402

T_START, T_END = "09:00", "13:20"


def tick_size(p: float) -> float:
    """TAIFEX 股票期貨跳動點。"""
    if p < 10: return 0.01
    if p < 50: return 0.05
    if p < 100: return 0.1
    if p < 500: return 0.5
    if p < 1000: return 1.0
    return 5.0


def step(px: float, side: str, n: int) -> float:
    """自 px 退 n 檔:n>0 往遠離 mid(買方往下、賣方往上),n<0 往 mid 靠。跨級距時用「目標方向那一側」的跳動點。"""
    out = px
    down = (side == "bid") == (n > 0)          # 這一步是往下走嗎
    for _ in range(abs(n)):
        t = tick_size(out - 1e-9) if down else tick_size(out)
        out = out - t if down else out + t
    return round(out, 4)


def mid_at(books, bt_list, t_us):
    j = bisect_left(bt_list, t_us)
    return _mid(books[j]) if j < len(books) else None


def simulate(books, bt_list, trades, tt_list, t_idx, side, px, q_ahead, horizon_s, phi):
    """我方 1 口排在 q_ahead 之後。**只有成交才成交**:穿越我價 → 成交;印在我價且該筆量 > q_ahead → 成交。
    撤單只縮短隊伍(phi 比例算在前面),本身不構成成交(修正 2026-09-24:舊版 q_ahead<=0 於快照即判成交,
    會讓「第一單」在下一個快照就假成交)。"""
    b0 = books[t_idx]; t0 = b0["book_time"]; deadline = t0 + int(horizon_s * 1e6)
    ti = bisect_left(tt_list, t0); bi = t_idx + 1
    prev_disp = size_at(b0, side, px); prints = 0.0
    while True:
        nb = bt_list[bi] if bi < len(books) else None
        nt = tt_list[ti] if ti < len(trades) else None
        if nb is None and nt is None: break
        nxt = min(x for x in (nb, nt) if x is not None)
        if nxt > deadline: break
        if nt is not None and nt == nxt:
            tr = trades[ti]; ti += 1
            tpx, tsz = float(tr["price"]), float(tr["size"])
            if (side == "bid" and tpx < px - 1e-9) or (side == "ask" and tpx > px + 1e-9):
                return tr["trade_time"], "through"
            if abs(tpx - px) < 1e-9:
                if tsz > q_ahead + 1e-9: return tr["trade_time"], "queue"
                q_ahead -= tsz; prints += tsz
            continue
        nbk = books[bi]; bi += 1
        disp = size_at(nbk, side, px)
        cancels = max(0.0, prev_disp - prints - disp)
        q_ahead = max(0.0, q_ahead - phi * cancels); prev_disp, prints = disp, 0.0
    return None, None


def run(roots, horizon, grid, phis, out_path):
    rows = []
    for root in roots:
        lr = root.lower()
        days = sorted(p.name.split("_")[-1][:-6] for p in (DATA / f"{lr}_books").glob(f"{lr}_books_*.jsonl"))
        for day in days:
            books, trades = load_day(root, day)
            if len(books) < 100 or len(trades) < 50:
                continue
            bt_list = [b["book_time"] for b in books]; tt_list = [t["trade_time"] for t in trades]
            last = 0; n_day = 0
            for i, b in enumerate(books):
                if b["book_time"] - last < grid * 1e6: continue
                if not (T_START <= _hhmm(b["book_time"]) <= T_END): continue
                last = b["book_time"]
                bb, aa = best(b, "bid"), best(b, "ask")
                if bb is None or aa is None: continue
                mid0 = (bb[0] + aa[0]) / 2; spr = aa[0] - bb[0]
                for side in ("bid", "ask"):
                    top = bb if side == "bid" else aa
                    sgn = 1.0 if side == "bid" else -1.0
                    tick = tick_size(top[0] - 1e-9) if side == "bid" else tick_size(top[0])
                    spread_ticks = round(spr / tick)
                    plans = {"JOIN": (top[0], top[1]), "L2": (step(top[0], side, 1), None), "L3": (step(top[0], side, 2), None)}
                    if spread_ticks >= 2:
                        plans["IMP"] = (step(top[0], side, -1), 0.0)
                    for name, (px, q0) in plans.items():
                        q_ahead = size_at(b, side, px) if q0 is None else q0
                        for phi in phis:
                            t_fill, how = simulate(books, bt_list, trades, tt_list, i, side, px, q_ahead, horizon, phi)
                            rec = {"root": root, "day": day, "hm": _hhmm(b["book_time"]), "side": side, "strat": name, "phi": phi,
                                   "q1": top[1], "q0": q_ahead, "spread_ticks": spread_ticks, "tick_bps": tick / mid0 * 1e4,
                                   "filled": t_fill is not None, "how": how, "wait_s": None, "mo30": None, "mo60": None, "mo120": None,
                                   "miss120": None}
                            if t_fill is not None:
                                rec["wait_s"] = (t_fill - b["book_time"]) / 1e6
                                for h in (30, 60, 120):
                                    m = mid_at(books, bt_list, t_fill + h * 1_000_000)
                                    if m is not None: rec[f"mo{h}"] = sgn * (m - px) / mid0 * 1e4   # bps,買方=mid−成交價
                            else:
                                m = mid_at(books, bt_list, b["book_time"] + horizon * 1_000_000)
                                if m is not None: rec["miss120"] = sgn * (m - mid0) / mid0 * 1e4   # 沒成交時價格往不利方向跑多少(正=跑掉)
                            rows.append(rec)
                    # 市價對照(只記一次,不分 phi)
                    opp = aa if side == "bid" else bb
                    m = mid_at(books, bt_list, b["book_time"] + horizon * 1_000_000)
                    rows.append({"root": root, "day": day, "hm": _hhmm(b["book_time"]), "side": side, "strat": "MKT", "phi": -1,
                                 "q1": top[1], "q0": 0, "spread_ticks": spread_ticks, "tick_bps": tick / mid0 * 1e4, "filled": True, "how": "market",
                                 "wait_s": 0.0, "mo30": None, "mo60": None, "mo120": (sgn * (m - opp[0]) / mid0 * 1e4) if m is not None else None, "miss120": None})
                    n_day += 1
            print(f"{root} {day} placements={n_day} books={len(books)} trades={len(trades)}", flush=True)
    Path(out_path).write_text(json.dumps(rows, ensure_ascii=False))
    print(f"rows={len(rows)} → {out_path}")
    summarize(rows)


def summarize(rows):
    import pandas as pd, numpy as np
    d = pd.DataFrame(rows)
    d["ev120"] = np.where(d["filled"], d["mo120"], 0.0)
    d["ev120"] = d["ev120"].fillna(0.0)
    def tbl(sub, lab):
        print(f"\n==== {lab} (n={len(sub)//max(1,sub['strat'].nunique())}) ====")
        print(f"{'策略':6s}{'phi':>4s}{'n':>7s}{'成交%':>7s}{'中位等待s':>9s}{'mo30':>7s}{'mo60':>7s}{'mo120':>7s}{'EV120':>7s}{'日t':>6s}{'未成交跑掉':>9s}")
        for (s_, p_), g in sub.groupby(["strat", "phi"]):
            f = g[g["filled"]]
            ev_day = g.groupby("day")["ev120"].mean()
            t = ev_day.mean() / (ev_day.std() / np.sqrt(len(ev_day))) if len(ev_day) > 2 and ev_day.std() > 0 else float("nan")
            print(f"{s_:6s}{p_:4.0f}{len(g):7d}{len(f)/len(g)*100:7.1f}{f['wait_s'].median() if len(f) else float('nan'):9.1f}"
                  f"{f['mo30'].mean() if len(f) else float('nan'):+7.1f}{f['mo60'].mean() if len(f) else float('nan'):+7.1f}{f['mo120'].mean() if len(f) else float('nan'):+7.1f}"
                  f"{g['ev120'].mean():+7.1f}{t:+6.1f}{g['miss120'].mean() if g['miss120'].notna().any() else float('nan'):+9.1f}")
    tbl(d, "全部")
    d["q1_bin"] = pd.qcut(d["q1"], 3, labels=["q1薄", "q1中", "q1厚"], duplicates="drop")
    for b, g in d.groupby("q1_bin", observed=True): tbl(g, f"最佳價深度 {b}")
    l2 = d[d["strat"] == "L2"]
    for emp, g in l2.groupby(l2["q0"] == 0):
        tbl(pd.concat([g, d[(d["strat"] != "L2") & d.set_index(["root", "day", "hm", "side"]).index.isin(g.set_index(["root", "day", "hm", "side"]).index)]]),
            f"L2 {'為空(第一單)' if emp else '不空'}")
    for sp, g in d.groupby(d["spread_ticks"].clip(upper=3)): tbl(g, f"價差 {sp} tick{'+' if sp == 3 else ''}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", default="OW,GU,QD,NA,LE,IX")
    ap.add_argument("--horizon", type=float, default=120)
    ap.add_argument("--grid", type=float, default=60)
    ap.add_argument("--phis", default="0,1")
    ap.add_argument("--out", default=str(Path.home() / "goldenstocks-data/scratch/queue_l2_sim.json"))
    ap.add_argument("--summarize-only", action="store_true")
    a = ap.parse_args()
    if a.summarize_only:
        summarize(json.loads(Path(a.out).read_text()))
    else:
        run(a.roots.split(","), a.horizon, a.grid, [float(x) for x in a.phis.split(",")], a.out)
