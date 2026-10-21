#!/usr/bin/env python3
"""個股期貨 08:43 鎖單前的「逐價位」刪單偵測（歷史 MIS 五檔 jsonl）。

期交所 08:43:00–08:45:00 不得刪改單。對每檔、每天：
  ref = 08:42:00~08:42:40 內最後一筆快照；lock = 08:42:50~08:43:00 內最後一筆快照
  對 ref 裡每個買／賣價位 (price,size>=--min-size)：
    - 在 lock 快照裡該價位仍在五檔範圍內（買價 >= lock 第5檔買價；賣價 <= 第5檔賣價）
      但 size 降 >= --drop（預設 70%）或消失 → 「刪單」（不是被擠出五檔）
  並回報 08:44:50 前最後快照的同價位量，驗證鎖定後沒再變。
用法：.venv/bin/python scripts/research/futures_preopen_cancel_scan.py [--date YYYY-MM-DD] [--min-size 20]
"""
import argparse, json, os
from pathlib import Path

ROOT = Path(os.environ.get("GOLDENSTOCKS_DATA_DIR") or Path.home() / "goldenstocks-data")
MIS = ROOT / "cache" / "futopt_mis_depth"


def sec(t): return int(t[0:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:8])
def num(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0


def book(d):
    bids = {num(d[f"CBidPrice{i}"]): num(d[f"CBidSize{i}"]) for i in range(1, 6) if num(d[f"CBidPrice{i}"]) > 0}
    asks = {num(d[f"CAskPrice{i}"]): num(d[f"CAskSize{i}"]) for i in range(1, 6) if num(d[f"CAskPrice{i}"]) > 0}
    return bids, asks


def scan(f, min_size, drop):
    snaps = {}
    for line in open(f):
        d = json.loads(line)
        s = sec(d["ts"][11:19])
        if 8 * 3600 + 42 * 60 <= s <= 8 * 3600 + 45 * 60:
            snaps.setdefault(d["SymbolID"], []).append((s, d["ts"][11:19], d["DispCName"], *book(d)))
    out, n_sym = [], 0
    T = lambda m, x: 8 * 3600 + m * 60 + x
    for sym, L in snaps.items():
        L.sort(key=lambda r: r[0])
        ref = [r for r in L if T(42, 0) <= r[0] <= T(42, 40)]
        lock = [r for r in L if T(42, 50) <= r[0] < T(43, 0)]
        fin = [r for r in L if T(43, 0) <= r[0] <= T(44, 50)]
        if not ref or not lock:
            continue
        n_sym += 1
        r0, r1 = ref[-1], lock[-1]
        f1 = fin[-1] if fin else None
        for side, i in (("買", 3), ("賣", 4)):
            b0, b1 = r0[i], r1[i]
            edge = min(b1) if side == "買" and b1 else (max(b1) if b1 else None)
            for p, sz in b0.items():
                if sz < min_size or edge is None:
                    continue
                in_range = p >= edge if side == "買" else p <= edge
                if not in_range:
                    continue
                now = b1.get(p, 0.0)
                if now <= sz * (1 - drop):
                    out.append((sym, r0[2], side, p, sz, now, r0[1], r1[1], (f1[i].get(p, 0.0) if f1 else None)))
    return out, n_sym


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--min-size", type=float, default=20)
    ap.add_argument("--drop", type=float, default=0.7)
    a = ap.parse_args()
    files = sorted(MIS.glob("mis_depth_*.jsonl"))
    if a.date: files = [x for x in files if a.date in x.name]
    tot = 0
    for f in files:
        ev, n = scan(f, a.min_size, a.drop)
        tot += len(ev)
        print(f"== {f.stem[-10:]}  可比檔數 {n}  刪單事件 {len(ev)}")
        for e in sorted(ev, key=lambda e: e[5] - e[4])[:8]:
            print("   %s %s %s價 %.1f  %d→%d口  (%s→%s)  08:44:50前=%s" % (e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[8]))
    print("總事件", tot)


if __name__ == "__main__":
    main()
