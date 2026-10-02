#!/usr/bin/env python3
"""短尺度外盤比順勢訊號 — 階段 0 探查(2026-10-02)。

jack 的構想:不要用當日累積的內外盤比,改用**最近 1 分鐘**外盤比衝高(+最近 5 分鐘也高
當順勢確認)當進場鍵,而且**不排隊**——直接吃賣一成交。反轉方向同理。

本檔只做兩件事(先便宜後貴):
  A 描述:1 分鐘／5 分鐘外盤比到底怎麼分布,「高」是什麼概念,以及成交稀疏造成的假極端
  B 初篩:事件後固定持有 N 分鐘的毛報酬,用「吃賣一進、吃買一出」的可成交價
已知先驗(不先講會重做白工):
  - 2026-09-30 順勢階段 0:17 個條件全負超額,「急拉·買壓未竭」n=50,919 超額 -5.9
    (IS t-6.4 / OOS t-8.2),而那一項的定義就接近「短期主動買占比高」
  - 2026-09-30 外盤比條件式進場:吃賣一立即多付中位 19.3 bps,是本線最大的逆風

用法:PYTHONPATH=src .venv/bin/python scripts/research/aggr_ratio_momentum_probe.py [--days 30]
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OPEN_S, CLOSE_S = 9 * 3600, 13 * 3600 + 25 * 60


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


def rolling_ratio(t, amt, is_buy, win):
    """每一筆成交當下、最近 win 秒的外盤比(金額加權)與該視窗總金額。向量化前綴和。"""
    cb = np.concatenate([[0.0], np.cumsum(np.where(is_buy, amt, 0.0))])
    ca = np.concatenate([[0.0], np.cumsum(amt)])
    lo = np.searchsorted(t, t - win, side="left")
    hi = np.arange(len(t)) + 1
    buy = cb[hi] - cb[lo]
    tot = ca[hi] - ca[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(tot > 0, buy / tot, np.nan)
    return r, tot, hi - lo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    files = sorted(TICK.glob("*_2026-*.json"))
    dates = sorted({f.name.split("_")[-1][:-5] for f in files})[-args.days:]
    sids = sorted({f.name.split("_")[0] for f in files})
    print(f"取樣 {len(dates)} 個交易日({dates[0]} ~ {dates[-1]})× 最多 {len(sids)} 檔", flush=True)

    DAY, SEC = [], []
    R1, R5, TOT1, N1, FWD = [], [], [], [], {m: [] for m in (1, 3, 5, 10, 30, 60, 120)}
    pairs = 0
    for d in dates:
        for sid in sids:
            tk = load(sid, d)
            if tk is None:
                continue
            pairs += 1
            t, px, vol, ty = tk
            amt = px * vol
            is_buy = ty == 1
            r1, tot1, n1 = rolling_ratio(t, amt, is_buy, 60)
            r5, _tot5, _n5 = rolling_ratio(t, amt, is_buy, 300)
            # 每 10 秒取樣一次,避免同一波重複計入
            keep = np.concatenate([[True], np.diff(np.floor(t / 10)) > 0])
            m = keep & np.isfinite(r1) & np.isfinite(r5)
            R1.append(r1[m]); R5.append(r5[m]); TOT1.append(tot1[m]); N1.append(n1[m])
            DAY.append(np.full(int(m.sum()), dates.index(d), dtype=np.int16))
            SEC.append(t[m])
            # 前瞻報酬:用「之後第一筆賣方主動成交」(= 可成交的買一)當出場價
            idx = np.where(m)[0]
            for mins in FWD:
                tgt = np.searchsorted(t, t[idx] + mins * 60, side="left")
                ok = tgt < len(t)
                fwd = np.full(len(idx), np.nan)
                fwd[ok] = (px[tgt[ok]] / px[idx[ok]] - 1) * 1e4
                FWD[mins].append(fwd)
    r1 = np.concatenate(R1); r5 = np.concatenate(R5)
    tot1 = np.concatenate(TOT1); n1 = np.concatenate(N1)
    fwd = {k: np.concatenate(v) for k, v in FWD.items()}
    day = np.concatenate(DAY); sec = np.concatenate(SEC)
    # 同期宇宙基準:把每個 (日, 分鐘) 格的所有取樣點平均,當作該時刻的市場漂移。
    # 不減掉它的話,樣本期大盤在跌就會讓「做空」自動賺錢 —— 那是 beta 不是 alpha。
    key = day.astype(np.int64) * 100000 + (sec // 60).astype(np.int64)
    exf = {}
    for mins, v in fwd.items():
        ok = np.isfinite(v)
        o = np.argsort(key[ok], kind="stable")
        k2 = key[ok][o]; v2 = v[ok][o]
        bounds = np.concatenate([[0], np.flatnonzero(np.diff(k2)) + 1, [len(k2)]])
        means = np.zeros(len(k2))
        for i in range(len(bounds) - 1):
            a, b = bounds[i], bounds[i + 1]
            means[a:b] = v2[a:b].mean()
        base = np.full(len(v), np.nan)
        idx = np.flatnonzero(ok)[o]
        base[idx] = means
        exf[mins] = v - base
    print(f"檔日 {pairs}、取樣點 {len(r1):,}\n")

    print("==== A1. 外盤比分布：「高」是什麼概念 ====")
    print(f"  {'分位':<6}{'1 分鐘':>10}{'5 分鐘':>10}")
    for q in (10, 25, 50, 75, 90, 95, 99):
        print(f"  p{q:<5}{np.nanpercentile(r1, q):>10.3f}{np.nanpercentile(r5, q):>10.3f}")
    for th in (0.6, 0.7, 0.8, 0.9, 1.0):
        print(f"  ≥{th:.1f} 的比例：1 分鐘 {np.nanmean(r1 >= th) * 100:5.1f}%   5 分鐘 {np.nanmean(r5 >= th) * 100:5.1f}%")

    print("\n==== A2. 成交稀疏造成的假極端（1 分鐘視窗內的成交筆數）====")
    for lo, hi_, lab in ((0, 3, "1~2 筆"), (3, 10, "3~9 筆"), (10, 30, "10~29 筆"), (30, 10 ** 9, "≥30 筆")):
        m = (n1 >= lo) & (n1 < hi_)
        if m.sum() < 100:
            continue
        print(f"  {lab:<8} 占 {m.mean() * 100:5.1f}%   外盤比=1.0 的比例 {np.nanmean(r1[m] >= 0.999) * 100:5.1f}%   "
              f"外盤比中位 {np.nanmedian(r1[m]):.3f}")
    print("  → 筆數少的視窗很容易出現 100% 外盤，必須設最小筆數/金額門檻，否則門檻會選到流動性枯竭")

    print("\n==== B. 初篩：1 分鐘外盤比分桶 → 之後 N 分鐘毛報酬（bps，吃賣一進、買一出前的中價近似）====")
    liq = n1 >= 10          # 最小活躍度：1 分鐘內至少 10 筆
    print(f"  (限 1 分鐘內 ≥10 筆，樣本 {liq.sum():,} 點)")
    print(f"  {'1分外盤比':<12}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in (1, 3, 5, 10, 30)))
    for lo, hi_ in ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.95), (0.95, 1.01)):
        m = liq & (r1 >= lo) & (r1 < hi_)
        if m.sum() < 200:
            continue
        row = f"  [{lo:.2f},{hi_:.2f})  {m.sum():>9,}"
        for mins in (1, 3, 5, 10, 30):
            v = fwd[mins][m]
            row += f"{np.nanmean(v):>9.2f}"
        print(row)

    print("\n==== B2. 加上 5 分鐘順勢確認（1 分鐘 ≥0.7 且 5 分鐘 ≥θ）====")
    print(f"  {'5分外盤比':<12}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in (1, 3, 5, 10, 30)))
    base = liq & (r1 >= 0.7)
    for lo in (0.0, 0.5, 0.6, 0.7, 0.8):
        m = base & (r5 >= lo)
        if m.sum() < 200:
            continue
        row = f"  ≥{lo:.2f}        {m.sum():>9,}"
        for mins in (1, 3, 5, 10, 30):
            row += f"{np.nanmean(fwd[mins][m]):>9.2f}"
        print(row)
    print("\n==== B3. 極端組合（排除窄縫）：1分≥0.95 ∧ 5分≥θ ∧ 1分內≥30筆 ====")
    print(f"  {'5分門檻':<12}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in (1, 3, 5, 10, 30)))
    hot = (n1 >= 30) & (r1 >= 0.95)
    for lo in (0.0, 0.7, 0.8, 0.9):
        m = hot & (r5 >= lo)
        if m.sum() < 200:
            print(f"  ≥{lo:.2f}        {m.sum():>9,}  n<200"); continue
        row = f"  ≥{lo:.2f}        {m.sum():>9,}"
        for mins in (1, 3, 5, 10, 30):
            row += f"{np.nanmean(fwd[mins][m]):>9.2f}"
        print(row)

    print("\n==== B4. 加速度：1 分鐘外盤比「超出」5 分鐘多少（r1 − r5）====")
    acc = r1 - r5
    print(f"  {'r1−r5':<12}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in (1, 3, 5, 10, 30)))
    for lo, hi_ in ((-1.01, -0.3), (-0.3, -0.1), (-0.1, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 1.01)):
        m = liq & (acc >= lo) & (acc < hi_)
        if m.sum() < 500:
            continue
        row = f"  [{lo:+.2f},{hi_:+.2f})  {m.sum():>7,}"
        for mins in (1, 3, 5, 10, 30):
            row += f"{np.nanmean(fwd[mins][m]):>9.2f}"
        print(row)

    print("\n==== B5. 反轉方向（外盤比極高 → 做空,正值=做空獲利）原始 vs 超額 ====")
    hold = (5, 10, 30, 60, 120)
    print(f"  {'組合':<30}{'n':>9}{'口徑':>7}" + "".join(f"{f'+{m}分':>9}" for m in hold))
    for lab, m in (("1分≥0.9 ∧ ≥10筆", liq & (r1 >= 0.9)),
                   ("1分≥0.95 ∧ 5分≥0.8 ∧ ≥30筆", (n1 >= 30) & (r1 >= 0.95) & (r5 >= 0.8)),
                   ("1分≥0.95 ∧ 5分≥0.9 ∧ ≥30筆", (n1 >= 30) & (r1 >= 0.95) & (r5 >= 0.9))):
        if m.sum() < 200:
            continue
        for tag, src in (("原始", fwd), ("超額", exf)):
            row = f"  {lab:<30}{m.sum():>9,}{tag:>7}"
            for mins in hold:
                row += f"{-np.nanmean(src[mins][m]):>9.2f}"
            print(row)
    print("\n  超額 = 減掉同一(日,分鐘)格全市場取樣點的平均報酬,去掉市場漂移。")
    print("  損益兩平約 41 bps(22 稅費 + 19.3 吃賣一價差),超額要超過它才有意義。")

    print("\n  ⚠ 本表是**中價近似**（用成交價，未扣買賣價差與 22 bps 成本）。")
    print("     吃賣一進場實測多付中位 19.3 bps（2026-09-30 量測），所以要先看到 >40 bps 的毛值才值得往下做。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
