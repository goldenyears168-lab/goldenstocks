#!/usr/bin/env python3
"""「盤整 ∧ 大戶佔比高 ∧ 外盤比高」三者交集的短線分數探查(2026-10-03)。

jack 構想:要找的不是單純外盤比高(那是追價),而是**價格沒動但大戶在主動買** ——
盤整中的吸貨。外盤比只在某個門檻以上才啟動計分。

為什麼值得單獨測(先把已知的講清楚,避免重做):
  - 2026-10-02:單純「1 分鐘外盤比高」做多單調為負;做空超額最高僅 +10.1 bps < 41 成本
  - 但那是**無條件**的外盤比。本檔問的是條件交互:在「壓縮 ∧ 大戶買」的子集內,
    外盤比高是否換個方向
  - [[quiet-accumulation-retail-arrival-verdict]] 測過「安靜吸貨等散戶接棒」(機制真、
    溢價無),但那條用的是日級大戶流 + 散戶參與,不是 tick 級的壓縮×外盤比

三個特徵全部從 tick 算,皆為 PIT(只用當下往前的視窗):
  compress  近 5 分鐘 (max−min)/mid,**越小越盤整**
  big_share 近 5 分鐘單筆金額 ≥ BIG_NTD 的成交金額佔比
  r1 / r5   近 1 / 5 分鐘外盤比(金額加權)
活躍度門檻:1 分鐘內 ≥10 筆(2026-10-02 量測:1~2 筆的視窗有 37% 會顯示 100% 外盤)。

用法:PYTHONPATH=src .venv/bin/python scripts/research/quiet_bigflow_aggr_probe.py [--days 20]
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OPEN_S, CLOSE_S = 9 * 3600, 13 * 3600 + 25 * 60
BIG_NTD = 1_000_000.0          # 單筆 ≥100 萬 = 大單(biglot 慣例的下緣;前 1% 分位另行對照)
# ⚠ FinMind tick 的 volume 單位是**張**不是股(2026-10-03 對帳 2330:逐筆加總 13,005
#   vs stock_daily_bars 14,557,662,比值 1,119)。金額必須 ×1000,否則大戶門檻實際上
#   變成 10 億、條件永遠不成立 —— 第一版就是這樣整個 big_share 全為 0 卻沒報錯。
LOT = 1000.0


def _sec(t: str) -> float:
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + float(t[6:])


def load(sid: str, date: str):
    f = TICK / f"{sid}_{date}.json"
    if not f.exists():
        return None
    try:
        arr = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    t, px, vol, ty = [], [], [], []
    for x in arr:
        tt = x.get("Time") or ""
        k = str(x.get("TickType", "0"))
        if len(tt) < 8 or k not in ("1", "2"):
            continue
        s = _sec(tt)
        if s < OPEN_S or s > CLOSE_S:
            continue
        t.append(s); px.append(float(x["deal_price"])); vol.append(float(x.get("volume") or 0))
        ty.append(1 if k == "1" else 2)
    if len(t) < 200:
        return None
    t = np.asarray(t); o = np.argsort(t, kind="stable")
    return t[o], np.asarray(px)[o], np.asarray(vol)[o], np.asarray(ty, dtype=np.int8)[o]


def _win(t, win):
    return np.searchsorted(t, t - win, side="left"), np.arange(len(t)) + 1


def ratio(t, amt, sel, win):
    cs = np.concatenate([[0.0], np.cumsum(np.where(sel, amt, 0.0))])
    ca = np.concatenate([[0.0], np.cumsum(amt)])
    lo, hi = _win(t, win)
    tot = ca[hi] - ca[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(tot > 0, (cs[hi] - cs[lo]) / tot, np.nan), tot, hi - lo


def compress(t, px, win):
    """近 win 秒的 (max−min)/mid,bps。用 O(n log n) 的分段最大最小(視窗小,直接迴圈可接受)。"""
    lo, hi = _win(t, win)
    out = np.full(len(t), np.nan)
    for i in range(len(t)):
        a, b = lo[i], hi[i]
        if b - a < 3:
            continue
        seg = px[a:b]
        mx, mn = seg.max(), seg.min()
        mid = (mx + mn) / 2
        if mid > 0:
            out[i] = (mx - mn) / mid * 1e4
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=20)
    args = ap.parse_args()
    files = sorted(TICK.glob("*_2026-*.json"))
    dates = sorted({f.name.split("_")[-1][:-5] for f in files})[-args.days:]
    sids = sorted({f.name.split("_")[0] for f in files})
    print(f"取樣 {len(dates)} 日({dates[0]} ~ {dates[-1]}) × 最多 {len(sids)} 檔", flush=True)

    HOLD = (5, 10, 30, 60)
    C, BS, R1, R5, N1, DAY, SEC = [], [], [], [], [], [], []
    FWD = {m: [] for m in HOLD}
    pairs = 0
    for d in dates:
        for sid in sids:
            tk = load(sid, d)
            if tk is None:
                continue
            pairs += 1
            t, px, vol, ty = tk
            amt = px * vol * LOT
            r1, _t1, n1 = ratio(t, amt, ty == 1, 60)
            r5, _t5, _n5 = ratio(t, amt, ty == 1, 300)
            bs, _tb, _nb = ratio(t, amt, amt >= BIG_NTD, 300)
            cp = compress(t, px, 300)
            keep = np.concatenate([[True], np.diff(np.floor(t / 10)) > 0])
            m = keep & np.isfinite(r1) & np.isfinite(r5) & np.isfinite(cp) & np.isfinite(bs)
            idx = np.where(m)[0]
            if not len(idx):
                continue
            C.append(cp[m]); BS.append(bs[m]); R1.append(r1[m]); R5.append(r5[m]); N1.append(n1[m])
            DAY.append(np.full(len(idx), dates.index(d), dtype=np.int16)); SEC.append(t[m])
            for mins in HOLD:
                tgt = np.searchsorted(t, t[idx] + mins * 60, side="left")
                ok = tgt < len(t)
                f = np.full(len(idx), np.nan)
                f[ok] = (px[tgt[ok]] / px[idx[ok]] - 1) * 1e4
                FWD[mins].append(f)
    cp = np.concatenate(C); bs = np.concatenate(BS); r1 = np.concatenate(R1)
    r5 = np.concatenate(R5); n1 = np.concatenate(N1)
    day = np.concatenate(DAY); sec = np.concatenate(SEC)
    fwd = {k: np.concatenate(v) for k, v in FWD.items()}
    key = day.astype(np.int64) * 100000 + (sec // 60).astype(np.int64)
    exf = {}
    for mins, v in fwd.items():
        ok = np.isfinite(v)
        o = np.argsort(key[ok], kind="stable")
        k2 = key[ok][o]; v2 = v[ok][o]
        b = np.concatenate([[0], np.flatnonzero(np.diff(k2)) + 1, [len(k2)]])
        mu = np.empty(len(k2))
        for i in range(len(b) - 1):
            mu[b[i]:b[i + 1]] = v2[b[i]:b[i + 1]].mean()
        base = np.full(len(v), np.nan)
        base[np.flatnonzero(ok)[o]] = mu
        exf[mins] = v - base
    liq = n1 >= 10
    print(f"檔日 {pairs}、取樣點 {len(cp):,}（活躍 {liq.sum():,}）\n")

    print("==== A. 三個特徵的分布 ====")
    for nm, v in (("壓縮 compress(bps)", cp), ("大戶佔比 big_share", bs), ("外盤比 r5", r5)):
        qs = [np.nanpercentile(v[liq], q) for q in (10, 25, 50, 75, 90)]
        print(f"  {nm:<22} p10 {qs[0]:8.3f}  p25 {qs[1]:8.3f}  p50 {qs[2]:8.3f}  p75 {qs[3]:8.3f}  p90 {qs[4]:8.3f}")

    q_cp = np.nanpercentile(cp[liq], 25)      # 最盤整的 25%
    q_bs = np.nanpercentile(bs[liq], 75)      # 大戶佔比前 25%
    print(f"\n  盤整門檻（壓縮 ≤ p25）= {q_cp:.1f} bps;大戶門檻（big_share ≥ p75）= {q_bs:.3f}")

    print("\n==== B. 核心問題：在「盤整 ∧ 大戶高」子集內，外盤比高是正是負？（超額 bps）====")
    quiet = liq & (cp <= q_cp) & (bs >= q_bs)
    print(f"  子集 n={quiet.sum():,}（占活躍樣本 {quiet.sum() / liq.sum() * 100:.1f}%）")
    print(f"  {'外盤比 r5':<14}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
    for lo, hi_ in ((0.0, 0.4), (0.4, 0.55), (0.55, 0.7), (0.7, 0.85), (0.85, 1.01)):
        m = quiet & (r5 >= lo) & (r5 < hi_)
        if m.sum() < 200:
            print(f"  [{lo:.2f},{hi_:.2f})  {m.sum():>9,}  n<200"); continue
        row = f"  [{lo:.2f},{hi_:.2f})  {m.sum():>9,}"
        for mins in HOLD:
            row += f"{np.nanmean(exf[mins][m]):>9.2f}"
        print(row)

    print("\n==== C. 對照組：拆掉其中一個條件，看交互作用是否真的存在（超額 +30 分）====")
    hi_aggr = r5 >= 0.7
    for lab, m in (("全樣本（活躍）", liq),
                   ("只有外盤比高", liq & hi_aggr),
                   ("只有盤整", liq & (cp <= q_cp)),
                   ("只有大戶高", liq & (bs >= q_bs)),
                   ("盤整 ∧ 大戶高", quiet),
                   ("盤整 ∧ 外盤比高", liq & (cp <= q_cp) & hi_aggr),
                   ("大戶高 ∧ 外盤比高", liq & (bs >= q_bs) & hi_aggr),
                   ("★ 三者皆成立", quiet & hi_aggr)):
        if m.sum() < 200:
            print(f"  {lab:<20} n={m.sum():>9,}  n<200"); continue
        print(f"  {lab:<20} n={m.sum():>9,}  " + "".join(f"{np.nanmean(exf[mins][m]):>9.2f}" for mins in HOLD))
    print(f"  {'(欄位:+5/+10/+30/+60 分超額 bps)':<20}")
    print("\n==== D. 前後半穩定性：B 表的倒 U 是真的還是噪音（超額 +30 分）====")
    half = len(dates) // 2
    first = day < half
    print(f"  前半 {dates[0]}~{dates[half - 1]} / 後半 {dates[half]}~{dates[-1]}")
    print(f"  {'外盤比 r5':<14}{'前半 n':>8}{'前半':>9}{'後半 n':>8}{'後半':>9}")
    for lo, hi_ in ((0.0, 0.40), (0.40, 0.55), (0.55, 0.70), (0.70, 0.85), (0.85, 1.01)):
        base = quiet & (r5 >= lo) & (r5 < hi_)
        a, b = base & first, base & ~first
        if a.sum() < 100 or b.sum() < 100:
            print(f"  [{lo:.2f},{hi_:.2f})   n 太少"); continue
        print(f"  [{lo:.2f},{hi_:.2f})  {a.sum():>8,}{np.nanmean(exf[30][a]):>9.2f}"
              f"{b.sum():>8,}{np.nanmean(exf[30][b]):>9.2f}")

    print("\n==== E. 最佳格的成本對照 ====")
    best = quiet & (r5 >= 0.55) & (r5 < 0.85)
    for mins in HOLD:
        v = exf[mins][best]
        sd = np.nanstd(v)
        n = np.isfinite(v).sum()
        print(f"  +{mins:>3} 分  n={n:>7,}  超額 {np.nanmean(v):>6.2f} bps  sd {sd:>6.1f}  "
              f"MDE(t=2) {2 * sd / np.sqrt(n):>5.2f}  扣22 {np.nanmean(v) - 22:>7.2f}  扣41 {np.nanmean(v) - 41:>7.2f}")

    print("\n  成本:掛買一等 22 bps;不排隊吃賣一約 41 bps（22 + 19.3 價差）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
