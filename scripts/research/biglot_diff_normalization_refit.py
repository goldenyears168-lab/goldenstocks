#!/usr/bin/env python3
"""大戶−散戶差值正規化研究 —— 前視偏誤重驗（2026-09-28 稽核修正）。

背景：`biglot-diff-normalization-verdict` 這篇研究筆記的原始程式碼（2026-09-24
一次性 heredoc、從未存檔，見 session transcript
`c1eb2de7-71ee-517d-870a-128c1b9bc0db.jsonl` 行 14826）有一個前視偏誤：

    if abs(chg) >= 0.095: continue   # chg = 當天收盤/前一日收盤 - 1

這行把「當天收盤是否鎖漲停／跌停」（±9.5% 以上的同日收盤變動，注意：不是只
剔除漲停，跌停同樣被剔除——稽核 agent 轉述的 `cl>=pc*1.094`（只剔漲停）跟
逐字讀到的原始碼不完全一致，以此檔為準）當成「這筆股票日要不要進入 `recs`
池」的門檻，而 `recs` 池又是後續 `fm()` 逐日計算 z-score / demean 的**唯一
母體**（`byd[d] = [r for r in recs if r["d"]==d]`）。也就是說，同一天的橫斷面
基準（平均數、標準差）組成本身取決於「今天誰鎖死了」——這是要等收盤才會確定
的資訊，且鎖死與否本身很可能跟大戶淨買（訊號）相關（大戶買才推得動鎖漲停），
等於用一個跟訊號相關的結果變數去篩選標準化母體，讓存活股票的 z-score 被系統
性地位移。

修正方式（本檔案）：
  1. 對「當天完整宇宙」（不論收盤是否鎖死，只要通過基本資料完整性檢查）計算
     z-score（訊號欄）與 demean（目標欄）。
  2. 只在最後、要把哪些股票日丟進 `np.linalg.lstsq` 回歸母體那一步，才用
     `locked` 旗標剔除鎖死股。
  3. 其餘邏輯（除數候選清單、目標定義、Fama-MacBeth 逐日回歸＋跨日 t 檢定、
     |ov|>1500 的離群值閘門、20 日均額視窗、i<20/i+1>=len(ds) 的樣本邊界）
     完全比照原始程式碼，未新增任何檢定方法。

同時保留「bug 版」的計算路徑（`fm_buggy`），兩者共用同一份 `full_recs`，唯一
差異就是 z-score / demean 的母體選取時機，方便逐項比對修正前後的係數與 t 值
是否跨過顯著邊界、方向有沒有翻轉。

用法：
  PYTHONPATH=src .venv/bin/python scripts/research/biglot_diff_normalization_refit.py
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, "src")
from stock_db import DATA_DIR  # noqa: E402

BASE = os.path.expanduser("~/goldenstocks-data/scratch/biglot_panels")
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"

# chg = 當天收盤 / 前一日收盤 - 1；|chg| >= LOCK_THRESHOLD 視為「鎖漲停或跌停」。
# 注意：這是逐字比照原始碼的雙邊門檻（漲停與跌停都剔），不是稽核轉述的
# 「只剔漲停」單邊寫法。
LOCK_THRESHOLD = 0.095
OV_OUTLIER_BPS = 1500.0
MIN_HISTORY_DAYS = 20
MIN_CROSS_SECTION = 12

SIGNAL_LABELS = (
    ("大戶淨÷成交額(現欄 大戶佔比)", "bigp"),
    ("(大戶−散戶)÷成交額", "diffp"),
    ("散戶淨÷成交額", "retp"),
    ("大戶淨÷20日均額", "big_avg"),
    ("(大戶−散戶)÷20日均額", "diff_avg"),
    ("大戶−散戶 絕對額(億)", "diff_abs"),
)
CONTROL_LABELS = (
    ("(大戶−散戶)÷成交額 | 控大戶佔比", "diffp"),
    ("散戶淨÷成交額 | 控大戶佔比", "retp"),
    ("大戶÷20日均額 | 控大戶佔比", "big_avg"),
    ("成交÷20日均額(量能) | 控大戶佔比", "tot_rel"),
)


def load_full_recs():
    """建立「完整原始宇宙」股票日紀錄——鎖死股一樣算完整套欄位，只多標一個
    `locked` 旗標，不在建池階段就 continue 掉。"""
    cal = json.loads(CALIB.read_text())
    sids = {r["sid"] for r in cal["universe"]}

    big1000 = defaultdict(float)
    with open(f"{BASE}/pit100_bucket5_dual_2026-03_08.csv") as f:
        for r in csv.DictReader(f):
            big1000[(r["sid"], r["date"])] += float(r["big1000"])

    day = {}
    with open(f"{BASE}/pit100_daily_panel_2026-03_08.csv") as f:
        for r in csv.DictReader(f):
            k = (r["sid"], r["date"])
            tot = float(r["tot_amt"])
            if r["sid"] not in sids or tot <= 0 or k not in big1000:
                continue
            day[k] = dict(
                big=big1000[k],
                ret=float(r["ret_net"]),
                tot=tot,
                o=float(r["open_px"]),
                c=float(r["close_px"]),
            )

    by = defaultdict(list)
    for (s, d) in day:
        by[s].append(d)

    recs = []
    for s, ds in by.items():
        ds = sorted(ds)
        for i, d in enumerate(ds):
            if i < MIN_HISTORY_DAYS or i + 1 >= len(ds):
                continue
            v = day[(s, d)]
            n = day[(s, ds[i + 1])]
            pc = day[(s, ds[i - 1])]["c"]
            if not (v["o"] and v["c"] and n["o"] and pc):
                continue
            chg = v["c"] / pc - 1
            locked = abs(chg) >= LOCK_THRESHOLD  # ← 旗標化，不再 continue
            avg20 = np.mean([day[(s, x)]["tot"] for x in ds[i - 20:i]])
            ov = (n["o"] / v["c"] - 1) * 1e4
            if abs(ov) > OV_OUTLIER_BPS:
                # 逐字比照原始碼：這道離群值閘門本來就無關本次要修的 bug，
                # 維持原樣（仍會讓該股票日整筆從母體消失，鎖死與否都一樣）。
                continue
            diff = v["big"] - v["ret"]
            recs.append(dict(
                d=d, s=s, locked=locked, chg=chg,
                ov=ov, d1=(n["c"] / v["c"] - 1) * 1e4,
                bigp=v["big"] / v["tot"], diffp=diff / v["tot"], retp=v["ret"] / v["tot"],
                big_avg=v["big"] / avg20, diff_avg=diff / avg20, diff_abs=diff / 1e8,
                tot_rel=v["tot"] / avg20,
            ))
    return recs


def zc(x):
    x = np.array(x, float)
    return (x - x.mean()) / (x.std() or 1)


def fm_buggy(recs, sig, target, ctrl=()):
    """原始（bug 版）路徑：z-score / demean 的母體 = 剔除鎖死股「之後」的
    rows，跟原始 heredoc 逐字等價（先過濾掉 locked，再在剩下的股票上算
    z/demean）。"""
    byd = defaultdict(list)
    for r in recs:
        if r["locked"]:
            continue
        byd[r["d"]].append(r)
    cs = []
    for d, rows in byd.items():
        if len(rows) < MIN_CROSS_SECTION:
            continue
        Y = np.array([r[target] for r in rows])
        Y = Y - Y.mean()
        X = np.column_stack(
            [zc([r[sig] for r in rows])] + [zc([r[c] for r in rows]) for c in ctrl]
        )
        b, *_ = np.linalg.lstsq(X, Y, rcond=None)
        cs.append(b[0])
    a = np.array(cs)
    return a.mean(), a.mean() / (a.std(ddof=1) / math.sqrt(len(a))), len(a)


def fm_fixed(recs, sig, target, ctrl=()):
    """修正版路徑：z-score / demean 的母體 = 當天「完整宇宙」（locked +
    非 locked 都在內）；只有最後選進 lstsq 的樣本才剔除 locked。"""
    byd_full = defaultdict(list)
    for r in recs:
        byd_full[r["d"]].append(r)
    cs = []
    for d, full_rows in byd_full.items():
        keep_idx = [i for i, r in enumerate(full_rows) if not r["locked"]]
        if len(keep_idx) < MIN_CROSS_SECTION:
            continue
        Y_full = np.array([r[target] for r in full_rows])
        Y_full = Y_full - Y_full.mean()
        X_full = np.column_stack(
            [zc([r[sig] for r in full_rows])] + [zc([r[c] for r in full_rows]) for c in ctrl]
        )
        Y = Y_full[keep_idx]
        X = X_full[keep_idx]
        b, *_ = np.linalg.lstsq(X, Y, rcond=None)
        cs.append(b[0])
    a = np.array(cs)
    return a.mean(), a.mean() / (a.std(ddof=1) / math.sqrt(len(a))), len(a)


def fm_fixed_intercept(recs, sig, target, ctrl=()):
    """診斷用途，非原始程式碼邏輯的一部分：`fm_fixed` 把 z-score/demean 的
    母體換成「完整宇宙」之後，subset 出來的 `X`/`Y` 不再保證零均值——原始
    程式碼從未放截距項（`np.linalg.lstsq(X, Y, ...)`，全靠「demean 母體＝回歸
    母體」這個隱性假設讓 through-origin 迴歸等價於有截距的迴歸）。一旦母體跟
    迴歸樣本脫鉤（本檔案 STEP 3 要求的修正），這個隱性假設就不成立了：鎖死股
    被剔除後，剩下樣本的 X、Y 均值不再是 0，through-origin 迴歸會把這個殘留
    位移也吃進斜率係數，人為放大係數與 t 值。

    這支函式額外加一欄截距，把「鎖死股被剔除後樣本均值偏移」跟「訊號本身的
    斜率」分開估計，用來檢查 `fm_fixed` 的係數膨脹有多少是這個 through-origin
    副作用造成的。不是使用者要求的修正邏輯，只是驗證用的穩健性檢查。"""
    byd_full = defaultdict(list)
    for r in recs:
        byd_full[r["d"]].append(r)
    cs = []
    for d, full_rows in byd_full.items():
        keep_idx = [i for i, r in enumerate(full_rows) if not r["locked"]]
        if len(keep_idx) < MIN_CROSS_SECTION:
            continue
        Y_full = np.array([r[target] for r in full_rows])
        Y_full = Y_full - Y_full.mean()
        X_full = np.column_stack(
            [zc([r[sig] for r in full_rows])] + [zc([r[c] for r in full_rows]) for c in ctrl]
        )
        Y = Y_full[keep_idx]
        X_sig = X_full[keep_idx]
        X = np.column_stack([np.ones(len(keep_idx)), X_sig])
        b, *_ = np.linalg.lstsq(X, Y, rcond=None)
        cs.append(b[1])  # b[0] 是截距，b[1] 才是訊號係數
    a = np.array(cs)
    return a.mean(), a.mean() / (a.std(ddof=1) / math.sqrt(len(a))), len(a)


def main():
    recs = load_full_recs()
    n_locked = sum(1 for r in recs if r["locked"])
    print(f"股-日 {len(recs)}（其中鎖死 {n_locked}，{n_locked / len(recs):.1%}）")
    print()

    header = (f"{'訊號(z,日切面)':30s}{'bug版':>10s}{'t':>7s}"
               f"{'修正版':>10s}{'t':>7s}{'修正+截距':>12s}{'t':>7s}")
    print(header)
    for nm, sig in SIGNAL_LABELS:
        mb, tb, _ = fm_buggy(recs, sig, "ov")
        mf, tf, _ = fm_fixed(recs, sig, "ov")
        mi, ti, _ = fm_fixed_intercept(recs, sig, "ov")
        print(f"{nm:30s}{mb:+10.1f}{tb:+7.2f}{mf:+10.1f}{tf:+7.2f}{mi:+12.1f}{ti:+7.2f}")

    print()
    print("--- 增量檢定：控制 大戶佔比(bigp) 後 ---")
    header2 = (f"{'訊號 | 控制':40s}{'bug版':>9s}{'t':>7s}"
               f"{'修正版':>9s}{'t':>7s}{'修正+截距':>11s}{'t':>7s}")
    print(header2)
    for nm, sig in CONTROL_LABELS:
        mb, tb, nb = fm_buggy(recs, sig, "ov", ("bigp",))
        mf, tf, nf = fm_fixed(recs, sig, "ov", ("bigp",))
        mi, ti, ni = fm_fixed_intercept(recs, sig, "ov", ("bigp",))
        print(f"{nm:40s}{mb:+9.1f}{tb:+7.2f}{mf:+9.1f}{tf:+7.2f}{mi:+11.1f}{ti:+7.2f}"
              f"   (n={nb}/{nf}/{ni})")


if __name__ == "__main__":
    main()
