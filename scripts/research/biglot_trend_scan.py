#!/usr/bin/env python3
"""順勢(trend-following)訊號階段 0 掃描(2026-09-30)。

動機:2026-09-30 環球晶(6488)從 953 漲到漲停 1035(+8.6%),V2.5 全天分數 ≤0 —— 因為
V2.5 的正分項全是「急跌」族,結構上不覆蓋趨勢上漲。本檔問:順勢方向有沒有東西?

方法(刻意用最便宜的口徑先篩):全母體 5 分桶面板,**桶收價進、桶收價出**(雙邊不可成交,
樂觀上界)。已知桶收→可成交價的偏誤約 15~20 bps(進出各 7~9),故門檻訂在
毛報酬 > 22(成本) + 18(執行偏誤) ≈ **40 bps** 才值得再花 tick 抽取去驗。

先讀既有否證,避免重跑:[[intraday-momentum-mv-reversal-verdict]] 近 5/15/30 分報酬對未來
30 分超額全反轉;[[intraday-megarun-start-undetectable]] 當日最大波段起點不可辨識;
[[momentum-burst-continuation-verdict]] 個股期貨爆量延續真效應僅 ~3bps vs 成本地板 21.6。

用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_trend_scan.py
"""
from __future__ import annotations
import sys

import numpy as np
import pandas as pd

from biglot_score_v23_fit import cl_t
from biglot_hold_lab import load_lab

GATE = 40.0      # 樂觀口徑要超過這個才值得往下做


def main():
    L = load_lab()
    d = L["d"].sort_values(["sid", "date", "bucket"]).reset_index(drop=True)
    px = L["px"]; buckets = L["buckets"]; U = L["uidx"]
    bi = {b: i for i, b in enumerate(buckets)}
    P = px.reindex(columns=buckets).values                       # (sid,date) × 桶
    idx = {k: i for i, k in enumerate(px.index)}
    key = list(zip(d["sid"], d["date"]))
    ri = np.array([idx.get(k, -1) for k in key])
    ci = d["bucket"].map(bi).values
    ok = (ri >= 0) & np.isfinite(ci)
    print(f"面板 {len(d)} 列,可定位 {ok.sum()}", flush=True)

    def fwd(k):
        out = np.full(len(d), np.nan)
        tgt = ci + k
        m = ok & (tgt < len(buckets))
        p0 = P[ri[m], ci[m]]; p1 = P[ri[m], tgt[m]]
        out[m] = (p1 / p0 - 1) * 1e4
        return out

    def to_close():
        out = np.full(len(d), np.nan)
        last = len(buckets) - 1
        m = ok
        p0 = P[ri[m], ci[m]]; p1 = P[ri[m], last]
        out[m] = (p1 / p0 - 1) * 1e4
        return out

    R = {k: fwd(k) for k in (1, 3, 6, 12)}
    R["eod"] = to_close()
    LOCK = np.zeros(len(d), bool)          # 漲/跌停代理:下一桶價格完全不動
    # 宇宙同期(算超額用)
    uv = U.reindex(columns=buckets).values
    ui = {dt: i for i, dt in enumerate(U.index)}
    di = np.array([ui.get(x, -1) for x in d["date"]])
    def ufwd(k):
        out = np.full(len(d), np.nan)
        tgt = ci + k
        m = ok & (di >= 0) & (tgt < len(buckets))
        out[m] = (uv[di[m], tgt[m]] / uv[di[m], ci[m]] - 1) * 1e4
        return out
    UR = {k: ufwd(k) for k in (1, 3, 6, 12)}
    UR["eod"] = np.where(ok & (di >= 0), (uv[di, len(buckets) - 1] / uv[di, ci.clip(0, len(buckets) - 1)] - 1) * 1e4, np.nan)
    EX = {k: R[k] - UR[k] for k in R}      # 超額 = 原始 − 同期宇宙
    nxt = np.full(len(d), np.nan)
    mm = ok & (ci + 1 < len(buckets))
    nxt[mm] = P[ri[mm], ci[mm] + 1] / P[ri[mm], ci[mm]] - 1
    LOCK[:] = np.isfinite(nxt) & (np.abs(nxt) < 1e-9)

    # --- 順勢候選條件(全部進場當下可得) ---
    g = d.groupby(["sid", "date"])
    d["hi_so_far"] = g["last_px"].cummax()
    d["is_break"] = d["last_px"] >= d["hi_so_far"] * 0.9999
    d["amt_r"] = d["tot_amt"] / g["tot_amt"].transform(lambda s: s.expanding().mean())
    w5 = d["w5"].fillna(0).values; r30 = d["r30"].fillna(0).values
    b5n = d["b5n"].fillna(0).values; sh = d["share5"].fillna(99).values
    ar = d["amt_r"].fillna(1).values; brk = d["is_break"].values
    mkw = d["mkt_w5"].fillna(0).values
    it = lambda n: d["it:" + n].fillna(False).values.astype(bool) if "it:" + n in d.columns else np.zeros(len(d), bool)  # noqa: E731
    C = [
        ("當日漲 >=2%", w5 >= 200),
        ("當日漲 >=5%", w5 >= 500),
        ("突破當日高", brk),
        ("突破當日高 ∧ 量增2x", brk & (ar >= 2)),
        ("突破當日高 ∧ 漲>=2%", brk & (w5 >= 200)),
        ("突破 ∧ 漲>=2% ∧ 量增2x", brk & (w5 >= 200) & (ar >= 2)),
        ("突破 ∧ 大戶淨買>=20%", brk & (b5n >= 20)),
        ("突破 ∧ 散戶<25%", brk & (sh < 25)),
        ("突破 ∧ 大戶>=20 ∧ 散戶<25", brk & (b5n >= 20) & (sh < 25)),
        ("急拉·買壓未竭", it("急拉·買壓未竭")),
        ("急拉·買壓未竭 ∧ 突破", it("急拉·買壓未竭") & brk),
        ("急拉·散戶追∧未竭", it("急拉·散戶追∧未竭")),
        ("順漲(item)", it("順漲")),
        ("逆強(item)", it("逆強")),
        ("當桶漲 r30>=50 ∧ 突破", (r30 >= 50) & brk),
        ("漲>=2% ∧ 大盤沒跌", (w5 >= 200) & (mkw >= 0)),
        ("漲>=2% ∧ 大戶>=20 ∧ 量增2x", (w5 >= 200) & (b5n >= 20) & (ar >= 2)),
    ]
    isb = (d["date"] <= "2026-06-30").values
    dates = d["date"].values
    print(f"\n口徑:桶收價進、桶收價出(雙邊不可成交,樂觀上界)。門檻 = 毛報酬 > {GATE:.0f} bps 才往下做。")
    print(f"  {'條件':<28}{'n':>7}   {'+5分':>8}{'+15分':>8}{'+30分':>8}{'+60分':>8}{'到收盤':>9}   IS+30(t)       OOS+30(t)")
    base = np.ones(len(d), bool) & ok
    for name, m in [("(全母體基準)", base)] + [(n, c & ok) for n, c in C]:
        n = int(m.sum())
        if n < 200:
            print(f"  {name:<28}{n:>7}  n<200"); continue
        line = f"  {name:<28}{n:>7}  "
        for k in (1, 3, 6, 12, "eod"):
            v = R[k][m]
            line += f"{np.nanmean(v):+8.1f}" if k != "eod" else f"{np.nanmean(v):+9.1f}"
        v6 = R[6]
        for sl, mk in (("IS", isb), ("OOS", ~isb)):
            f = m & mk & np.isfinite(v6)
            if f.sum() < 100:
                line += " | n<100"; continue
            mu, t = cl_t(v6[f], dates[f]); line += f" | {mu:+7.1f}(t{t:+5.1f})"
        print(line)
    print("\n  ※ 上表是原始報酬;樣本期(2026-03~08)大盤在跌,基準本身為負,必須看超額 ↓")
    print(f"\n  {'條件(超額=減同期宇宙)':<28}{'n':>7}   {'+5分':>8}{'+15分':>8}{'+30分':>8}{'+60分':>8}{'到收盤':>9}   IS+30(t)       OOS+30(t)")
    for name, m in [("(全母體基準)", base)] + [(n, c & ok) for n, c in C]:
        n = int(m.sum())
        if n < 200:
            continue
        line = f"  {name:<28}{n:>7}  "
        for k in (1, 3, 6, 12, "eod"):
            v = EX[k][m]
            line += f"{np.nanmean(v):+8.1f}" if k != "eod" else f"{np.nanmean(v):+9.1f}"
        e6 = EX[6]
        for sl, mk in (("IS", isb), ("OOS", ~isb)):
            f = m & mk & np.isfinite(e6)
            if f.sum() < 100:
                line += " | n<100"; continue
            mu, t = cl_t(e6[f], dates[f]); line += f" | {mu:+7.1f}(t{t:+5.1f})"
        print(line)
    print(f"\n  排除價格完全不動的桶({LOCK.sum()} 個,{LOCK.mean()*100:.1f}%,含漲跌停鎖死)後重算 +30 分超額:")
    for name, m0 in C[:6]:
        m = m0 & ok & ~LOCK
        if m.sum() < 200:
            continue
        e6 = EX[6]; o = f"    {name:<28} n={m.sum():6d}"
        for sl, mk in (("IS", isb), ("OOS", ~isb)):
            f = m & mk & np.isfinite(e6)
            mu, t = cl_t(e6[f], dates[f]); o += f" | {sl} {mu:+7.2f}(t{t:+5.2f})"
        print(o)
    print("\n  註:全母體基準是同期所有桶的平均,任何條件要有意義必須明顯高於基準,不只是高於 0。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
