#!/usr/bin/env python3
"""串流狀態機 biglot.streak 與批次 exhaust_lib 的逐事件重放比對（官方單窗連量口徑；上線前驗收）。

批次：exhaust_lib.bins → 連攻段（同向連續窗，n≥BREAK_MIN_N）被中斷事件 (t, d, n, nb5, vmax, win_lots)。
串流：逐筆餵 StreakTracker（含競價列只更新報價），收集 on_trade／advance 回傳事件。
兩邊必須逐事件相同（整數欄位全等、浮點欄位 1e-9）。
用法: PYTHONPATH=scripts/research:scripts/research/biglot:src .venv/bin/python scripts/research/exhaust_parity.py <面板目錄>
"""
from __future__ import annotations
import glob, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from exhaust_lib import BIN, bins, classify, load_day
from biglot.streak import StreakTracker, BREAK_MIN_N, BURST_WIN_X

P = sys.argv[1]
days = sorted(Path(f).stem for f in glob.glob(P + "/*.pkl"))
hist: dict[str, list[float]] = {}


def batch_events(a, ref):
    side, _, _ = classify(a)
    bn = bins(a, side)
    if bn is None:
        return []
    o, i = bn["o"], bn["i"]
    dr = np.sign(o - i).astype(int)
    dom = np.where(dr > 0, o, np.where(dr < 0, i, 0.0))
    ref5 = ref / 12.0
    ev, k, nb = [], 0, len(dr)
    while k < nb:
        if dr[k] == 0:
            k += 1
            continue
        j = k
        while j + 1 < nb and dr[j + 1] == dr[k]:
            j += 1
        n = j - k + 1
        e = j + 1                                         # 中斷窗：下一個有成交的窗
        if e < nb and n >= BREAK_MIN_N:
            seg = dom[k:j + 1]
            ev.append(((bn["b"][e] + 1) * BIN, int(dr[k]), n, int((seg >= BURST_WIN_X * ref5).sum()),
                       float(seg.max()), float(dom[j])))
        k = j + 1
    return ev


tot_ev = bad = ssyms = 0
mism = []
for dte in days:
    pan = load_day(P, dte)
    for s, a in pan.items():
        c = a[:, 5] == 0
        h_ = hist.setdefault(s, [])
        ref = np.mean(h_[-10:]) / 265.0 if len(h_) >= 3 else np.nan
        h_.append(a[c, 2].sum())
        if not np.isfinite(ref) or ref <= 0:
            continue
        be = batch_events(a, ref)
        trk = StreakTracker(); trk.set_ref(s, ref)
        se = []
        for r in a:
            bd = None if np.isnan(r[3]) else r[3]; ak = None if np.isnan(r[4]) else r[4]
            for e in trk.on_trade(s, r[0], r[1], r[2], bd, ak, auction=bool(r[5])):
                se.append((e["t"], e["d"], e["n"], e["nb5"], e["vmax"], e["win_lots"]))
        for e in trk.advance(s, 14 * 3600):          # 收最後一個窗（上線時由時間驅動）
            se.append((e["t"], e["d"], e["n"], e["nb5"], e["vmax"], e["win_lots"]))
        ssyms += 1; tot_ev += len(be)
        ok = len(be) == len(se) and all(
            x[:4] == y[:4] and abs(x[4] - y[4]) < 1e-9 and abs(x[5] - y[5]) < 1e-9 for x, y in zip(be, se))
        if not ok:
            bad += 1
            if len(mism) < 5:
                mism.append((dte, s, len(be), len(se), be[:2], se[:2]))
print(f"stock-days {ssyms}  batch events {tot_ev}  mismatching stock-days {bad}")
for m in mism:
    print(m)
sys.exit(1 if bad else 0)
