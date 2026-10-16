#!/usr/bin/env python3
"""依 docs/futures-flow-prereg-extreme-20261016.md 評估 T1(只讀)。探索期不計成績;事件定義、視窗與門檻不得在此檔事後修改。"""
import math
import statistics as S
import sys

import numpy as np

from futures_flow_prereg_eval import FREEZE_FIRST_CONFIRM, load, tl

START, GAP, HORIZON = 30, 15, 30
MIN_SESSIONS = 15
BONF_T = 2.9            # 14 檢定 Bonferroni,雙尾 α=0.05/14
COST_BPS = {"tmf": 1.23, "mxf": 1.34}


def events(session, rows):
    """回傳 [(追價?, rev_bps)]。"""
    rows = sorted(rows, key=lambda r: tl(session, r["hhmm"]))
    c = np.array([float(r["close"]) for r in rows])
    ns = np.array([float(r["b_S"]) - float(r["s_S"]) for r in rows])     # 淨口=b−s(nb_* 是無法判定方向的量)
    cs = np.cumsum(ns)
    out, last = [], -10**9
    for i in range(START, len(c) - HORIZON):
        if i - last < GAP:
            continue
        hi, lo = c[i] >= c[: i + 1].max(), c[i] <= c[: i + 1].min()
        if not (hi or lo):
            continue
        d = 1 if hi else -1
        chase = cs[i] >= cs[: i + 1].max() if hi else cs[i] <= cs[: i + 1].min()
        out.append((bool(chase), -d * (c[i + HORIZON] / c[i] - 1) * 1e4))
        last = i
    return out


def evaluate(root, confirm):
    deltas, chase_means = [], []
    for (stype, T), rows in load(root).items():
        if len(rows) < 120 or (T >= FREEZE_FIRST_CONFIRM) != confirm:
            continue
        ev = events(stype, rows)
        a = [r for ch, r in ev if ch]
        b = [r for ch, r in ev if not ch]
        if a and b:
            deltas.append(S.mean(a) - S.mean(b))
            chase_means.append(S.mean(a))
    n = len(deltas)
    if n < 3:
        return n, float("nan"), float("nan"), float("nan")
    sd = S.stdev(deltas)
    t = S.mean(deltas) / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    return n, S.mean(deltas), t, S.mean(chase_means)


def main():
    for root in ("tmf", "mxf"):
        try:
            for confirm in (False, True):
                tag = "確認期(計成績)" if confirm else "探索期(不計成績,不得據此改假設)"
                n, d, t, cm = evaluate(root, confirm)
                if n == 0:
                    print(f"{root.upper()} [{tag}] 無資料")
                    continue
                verdict = ""
                if confirm:
                    if n < MIN_SESSIONS:
                        verdict = f"不足(需 ≥{MIN_SESSIONS} session,現 {n})"
                    else:
                        verdict = ("統計" + ("顯著" if abs(t) >= BONF_T else "不顯著")
                                   + f";追價組平均 {cm:+.2f}bps {'≥' if cm >= COST_BPS[root] else '<'} 成本 {COST_BPS[root]}bps")
                print(f"{root.upper()} [{tag}] T1: session {n}、Δ(追價−非追價) {d:+.2f}bps、t {t:+.1f}、追價組平均 {cm:+.2f}bps  {verdict}")
        except FileNotFoundError:
            print(root, "缺特徵表,先跑 futures_session_minute_features.py")


if __name__ == "__main__":
    sys.exit(main())
