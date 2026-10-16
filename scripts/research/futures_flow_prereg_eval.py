#!/usr/bin/env python3
"""依 docs/futures-flow-prereg-20261016.md 評估 H1~H4(只讀)。凍結日之前=探索,之後=確認;假設與門檻不得在此檔事後修改。"""
import collections
import csv
import gzip
import math
import os
import pathlib
import statistics as S
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
try:
    from stock_db import DATA_DIR as _D
    STATE_ROOT = pathlib.Path(_D).parent
except Exception:
    STATE_ROOT = pathlib.Path(os.environ.get("GOLDENSTOCKS_DATA_DIR", os.path.expanduser("~/goldenstocks-data")))

FREEZE_FIRST_CONFIRM = "2026-10-17"      # T >= 此日為確認期
STEP = 15                                # 分鐘
MIN_SESSIONS, MIN_WINDOWS = 15, 10
BONF_T = 2.7
COST_BPS = {"tmf": 1.23, "mxf": 1.34}    # MXF:價差 0.60 + (手續費 40 元/口/邊 ×2 + 期交稅 0.002%×2)/名目 235.5 萬;若 40 元為來回則約 1.17
GROUPS = {"Mid": ("M1", "M2"), "Large": ("L", "XL"), "Small": ("S",)}
HYP = ["H1", "H2", "H3", "H4"]


def sgn(x):
    return (x > 0) - (x < 0)


def tl(session, hhmm):
    """session 內的分鐘序:日盤自 08:45;夜盤自 15:00,00:00 以後 +24h。"""
    h, m = int(hhmm[:2]), int(hhmm[3:])
    mins = h * 60 + m
    return mins if session == "day" else (mins if mins >= 12 * 60 else mins + 24 * 60)


def load(root):
    p = STATE_ROOT / "data" / "research" / "futures_session_minute" / f"{root}_minute.csv.gz"
    S_ = collections.defaultdict(list)
    with gzip.open(p, "rt") as fh:
        for r in csv.DictReader(fh):
            S_[(r["session"], r["T"])].append(r)
    return S_


def session_windows(session, rows):
    start = 8 * 60 + 45 if session == "day" else 15 * 60
    end = 13 * 60 + 45 if session == "day" else 29 * 60
    bars = {tl(session, r["hhmm"]): r for r in rows}
    cl = sorted(bars)
    def close_at(t):          # t 之前(含)最後一筆收盤
        last = None
        for m in cl:
            if m <= t:
                last = float(bars[m]["close"])
            else:
                break
        return last
    out = []
    t = start + STEP
    while t + STEP <= end:
        win = [bars[m] for m in cl if t - STEP <= m < t]
        vol = sum(float(r["vol"]) for r in win)
        c0, c1 = close_at(t - 1), None
        c_t, c_f = close_at(t - 1), close_at(t + STEP - 1)
        if vol > 0 and c_t and c_f:
            I = {}
            for g, ks in GROUPS.items():
                b = sum(float(r[f"b_{k}"]) for r in win for k in ks)
                s = sum(float(r[f"s_{k}"]) for r in win for k in ks)
                I[g] = (b - s) / vol
            out.append((I, (c_f / c_t - 1) * 1e4))
        t += STEP
    return out


def stat_for(h, I, ret):
    if h == "H1":
        s = sgn(I["Mid"]); return s * ret if s else None
    if h == "H2":
        s = sgn(I["Large"]); return s * ret if s else None
    if h == "H3":
        s = sgn(I["Small"]); return -s * ret if s else None
    if h == "H4":
        sl, ss = sgn(I["Large"]), sgn(I["Small"])
        return sl * ret if sl and ss and sl != ss else None


def evaluate(root, confirm):
    per = {h: [] for h in HYP}           # h -> [(session, [x...])]
    for (stype, T), rows in sorted(load(root).items(), key=lambda kv: kv[0][1]):
        if len(rows) < 30 or (T >= FREEZE_FIRST_CONFIRM) != confirm:
            continue
        w = session_windows(stype, rows)
        for h in HYP:
            xs = [x for I, r in w if (x := stat_for(h, I, r)) is not None]
            if len(xs) >= MIN_WINDOWS:
                per[h].append((T, xs))
    res = {}
    for h, lst in per.items():
        sm = [S.mean(x) for _, x in lst]
        n = len(sm)
        t = S.mean(sm) / (S.stdev(sm) / math.sqrt(n)) if n > 2 and S.stdev(sm) > 0 else float("nan")
        res[h] = (n, sum(len(x) for _, x in lst), S.mean(sm) if sm else float("nan"), t)
    return res


def main():
    for confirm in (False, True):
        tag = "確認期(計成績)" if confirm else "探索期(不計成績,不得據此改假設)"
        print(f"\n===== {tag} =====")
        for root in ("tmf", "mxf"):
            try:
                res = evaluate(root, confirm)
            except FileNotFoundError:
                print(root, "缺特徵表,先跑 futures_session_minute_features.py"); continue
            for h, (n, nw, m, t) in res.items():
                if n == 0:
                    print(f"{root.upper()} {h}: 無資料"); continue
                verdict = ""
                if confirm:
                    if n < MIN_SESSIONS:
                        verdict = f"不足(需 ≥{MIN_SESSIONS} session,現 {n})"
                    else:
                        sig = abs(t) >= BONF_T
                        c = COST_BPS[root]
                        verdict = ("統計" + ("顯著" if sig else "不顯著")
                                   + (f";毛效應 {'>' if m >= c else '<'} 成本 {c}bps" if c is not None else ";MXF 成本未量,不判可交易"))
                print(f"{root.upper()} {h}: session {n}、窗 {nw}、平均 {m:+.2f}bps、t {t:+.1f}  {verdict}")


if __name__ == "__main__":
    main()
