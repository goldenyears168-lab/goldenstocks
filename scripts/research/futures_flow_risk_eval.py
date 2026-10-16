#!/usr/bin/env python3
"""依 docs/futures-flow-prereg-risk-20261016.md 評估 R1/R2(只讀)。探索期不計成績;假設、控制變數、β 與門檻不得在此檔事後修改。"""
import collections
import math
import statistics as S
import sys

import numpy as np

from futures_flow_prereg_eval import FREEZE_FIRST_CONFIRM, STEP, load, tl

MIN_SESSIONS, MIN_WINDOWS = 15, 10
BONF_T = 2.9            # 12 檢定 Bonferroni,雙尾 α=0.05/12
PRACTICAL_RATIO = 1.25  # 最高三分位 / 其餘 的平均 |報酬|
HYP = ["R1", "R2"]


def windows(session, rows):
    """回傳 [(I_L, I_S, ret_w, ln_vol, y_next)];決策點 t=開盤+15,+30...,窗=[t-15,t),目標=[t,t+15)。"""
    start = 8 * 60 + 45 if session == "day" else 15 * 60
    end = 13 * 60 + 45 if session == "day" else 29 * 60
    bars = {tl(session, r["hhmm"]): r for r in rows}
    ms = sorted(bars)

    def close_at(t):
        last = None
        for m in ms:
            if m <= t:
                last = float(bars[m]["close"])
            else:
                break
        return last

    out = []
    t = start + STEP
    while t + STEP <= end:
        win = [bars[m] for m in ms if t - STEP <= m < t]
        vol = sum(float(r["vol"]) for r in win)
        c_in, c_t, c_f = float(win[0]["open"]) if win else None, close_at(t - 1), close_at(t + STEP - 1)
        if vol > 0 and c_in and c_t and c_f:
            def imb(ks):
                b = sum(float(r[f"b_{k}"]) for r in win for k in ks)
                s = sum(float(r[f"s_{k}"]) for r in win for k in ks)
                return (b - s) / vol
            out.append((imb(("L", "XL")), imb(("S",)), (c_t / c_in - 1) * 1e4, math.log(vol),
                        abs(c_f / c_t - 1) * 1e4))
        t += STEP
    return out


def collect(root):
    per = {False: [], True: []}      # confirm? -> [(T, windows)]
    for (stype, T), rows in sorted(load(root).items(), key=lambda kv: kv[0][1]):
        if len(rows) < 30:
            continue
        w = windows(stype, rows)
        if len(w) >= MIN_WINDOWS:
            per[T >= FREEZE_FIRST_CONFIRM].append(((stype, T), w))
    return per


def frozen_beta(explore):
    """探索期全部窗,I_L 對 ret_w 無截距 OLS。"""
    x = np.array([w[2] for _, ws in explore for w in ws])
    y = np.array([w[0] for _, ws in explore for w in ws])
    return float((x * y).sum() / (x * x).sum())


def evaluate(sessions, beta):
    if not sessions:
        return {}
    rec = [(T, w) for T, ws in sessions for w in ws]
    y = np.array([w[4] for _, w in rec])
    ctrl = np.column_stack([np.ones(len(rec)), [abs(w[2]) for _, w in rec], [w[3] for _, w in rec]])
    coef, *_ = np.linalg.lstsq(ctrl, y, rcond=None)
    y_res = y - ctrl @ coef
    feats = {
        "R1": np.array([abs(w[0] - beta * w[2]) for _, w in rec]),
        "R2": np.array([-w[0] * w[1] for _, w in rec]),
    }
    Ts = np.array([f"{k[0]}|{k[1]}" for k, _ in rec])   # session = (盤別, 日期);日夜盤同日不可合併
    res = {}
    for h, f in feats.items():
        cs = []
        for T in sorted(set(Ts)):
            m = Ts == T
            if m.sum() >= MIN_WINDOWS and f[m].std() > 0 and y_res[m].std() > 0:
                cs.append(float(np.corrcoef(f[m], y_res[m])[0, 1]))
        n = len(cs)
        t = S.mean(cs) / (S.stdev(cs) / math.sqrt(n)) if n > 2 and S.stdev(cs) > 0 else float("nan")
        hi = f >= np.quantile(f, 2 / 3)
        ratio = float(y[hi].mean() / y[~hi].mean()) if hi.any() and (~hi).any() else float("nan")
        res[h] = (n, len(rec), S.mean(cs) if cs else float("nan"), t, ratio)
    return res


def main():
    for root in ("tmf", "mxf"):
        try:
            per = collect(root)
        except FileNotFoundError:
            print(root, "缺特徵表,先跑 futures_session_minute_features.py")
            continue
        beta = frozen_beta(per[False])
        print(f"\n{root.upper()} 凍結 β(I_L 對 ret_w,探索期)= {beta:.6f}")
        for confirm in (False, True):
            tag = "確認期(計成績)" if confirm else "探索期(不計成績,不得據此改假設)"
            res = evaluate(per[confirm], beta)
            if not res:
                print(f"  [{tag}] 無資料")
                continue
            for h, (n, nw, m, t, ratio) in res.items():
                verdict = ""
                if confirm:
                    if n < MIN_SESSIONS:
                        verdict = f"不足(需 ≥{MIN_SESSIONS} session,現 {n})"
                    else:
                        sig = abs(t) >= BONF_T
                        prac = ratio >= PRACTICAL_RATIO
                        verdict = ("統計" + ("顯著" if sig else "不顯著") + ";實用門檻" + ("達成" if prac else "未達"))
                print(f"  [{tag}] {h}: session {n}、窗 {nw}、平均相關 {m:+.3f}、t {t:+.1f}、高三分位/其餘 {ratio:.2f}  {verdict}")


if __name__ == "__main__":
    sys.exit(main())
