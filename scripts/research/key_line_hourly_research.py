#!/usr/bin/env python3
"""「關鍵一條線」小時線·近一週版(2026-09-25 jack 交辦):把日線版規則套到小時K,回顧期改成近一週。

跟日線版的關鍵差異:
  · K棒 = 小時K(09-10/10-11/11-12/12-13/13-13:30,5 根/日)
  · 前高回顧期 = 近一週(≈25 根小時K,約5個交易日;日線版是前60個交易日)
  · 漲幅門檻 = 待測(日線版4%是校準給日波動的,小時波動天生小很多,測 1/2/3/4%)
  · 線會「過期」:若過去一週內沒有新觸發棒,視為當下「沒有線」(日線版的線不會過期,只會被新觸發棒覆蓋)

資料:pit100_hourly_2026-03_08.csv(key_line_hourly_tick_features.py 產生,100檔×127日小時K)。
用法:PYTHONPATH=src .venv/bin/python scripts/research/key_line_hourly_research.py
"""
from __future__ import annotations
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "scripts/research")
from biglot_score_v23_fit import cl_t

PANEL = "/Users/jackm4/goldenstocks-data/scratch/biglot_panels/pit100_hourly_2026-03_08.csv"
IS_END = "2026-06-30"
HOUR_ORDER = ["09:00", "10:00", "11:00", "12:00", "13:00"]


def build_line_series(g: pd.DataFrame, W: int, thresh: float):
    """回傳每根小時K當下的『現有線』(過去 W 根內最近一次觸發棒的低點;過期則 NaN)。"""
    n = len(g)
    o, h, lo, c = g["open"].values, g["high"].values, g["low"].values, g["close"].values
    red = c > o
    pct = np.concatenate([[np.nan], c[1:] / c[:-1] - 1])
    prior_hi = pd.Series(c).shift(1).rolling(W, min_periods=W).max().values
    trig = red & (pct > thresh) & (c > prior_hi)
    trig_low = np.where(trig, lo, np.nan)
    line = np.full(n, np.nan); last_i = -10**9; last_low = np.nan
    for i in range(n):
        if trig[i]:
            last_i, last_low = i, trig_low[i]
        line[i] = last_low if (i - last_i) < W else np.nan     # 過期規則:超過 W 根就失效
    return line, trig


def main():
    import json
    cal = json.load(open(f"{__import__('pathlib').Path.home()}/goldenstocks-data/data/cache/pit_universe_tick/_live_calib.json"))
    sids42 = {r["sid"] for r in cal["universe"]}   # jack 2026-09-25:研究只限儀表板實際的 42 檔,不用 pit_universe_tick 全 100 檔
    d = pd.read_csv(PANEL, dtype={"sid": str})
    before = d["sid"].nunique()
    d = d[d["sid"].isin(sids42)].copy()
    print(f"42 檔中有逐筆資料的:{d['sid'].nunique()}/{len(sids42)}(原始 pit_universe_tick 面板 {before} 檔,已過濾)")
    d["hour_ord"] = d["hour"].map({h: i for i, h in enumerate(HOUR_ORDER)})
    d = d.sort_values(["sid", "date", "hour_ord"]).reset_index(drop=True)
    print(f"股-小時K {len(d)}  檔 {d['sid'].nunique()}  日 {d['date'].nunique()}")

    # 宇宙等權(同一 date+hour)未來報酬基準
    d["ret_next1"] = np.nan  # 佔位,稍後逐檔填

    print("\n==== A. 觸發率 & 無線比例:回顧期 W(週) × 漲幅門檻(百分比) ====")
    for W in (20, 25, 30):
        for thresh in (0.01, 0.02, 0.03, 0.04):
            none_frac, n_trig = [], 0
            for sid, g in d.groupby("sid"):
                line, trig = build_line_series(g, W, thresh)
                none_frac.append(np.mean(np.isnan(line)))
                n_trig += trig.sum()
            print(f"  W={W:2d} thresh={thresh*100:.0f}%: 平均無線比例 {np.mean(none_frac)*100:5.1f}%  觸發次數(全樣本){n_trig:5d}")

    W, THRESH = 20, 0.02
    print(f"\n==== B. 採用 W={W}(≈一週)、thresh={THRESH*100:.0f}% 的完整結果 ====")
    rows = []
    for sid, g in d.groupby("sid"):
        g = g.reset_index(drop=True)
        line, trig = build_line_series(g, W, THRESH)
        g = g.assign(line=line, trig=trig)
        rows.append(g)
    full = pd.concat(rows, ignore_index=True)
    # 宇宙每個 (date,hour) 等權指數,供未來報酬扣基準
    idx = full.pivot_table(index=["date", "hour_ord"], values="close", aggfunc=lambda s: s.mean())
    full = full.sort_values(["sid", "date", "hour_ord"]).reset_index(drop=True)
    full["fwd1"] = full.groupby("sid")["close"].shift(-1) / full["close"] - 1
    full["fwd2"] = full.groupby("sid")["close"].shift(-2) / full["close"] - 1
    full["fwd4"] = full.groupby("sid")["close"].shift(-4) / full["close"] - 1
    mkt = idx.reset_index().rename(columns={"close": "mkt_close"})
    mkt = mkt.sort_values(["date", "hour_ord"]).reset_index(drop=True)
    mkt["mkt_fwd1"] = mkt["mkt_close"].shift(-1) / mkt["mkt_close"] - 1
    mkt["mkt_fwd2"] = mkt["mkt_close"].shift(-2) / mkt["mkt_close"] - 1
    mkt["mkt_fwd4"] = mkt["mkt_close"].shift(-4) / mkt["mkt_close"] - 1
    full = full.merge(mkt[["date", "hour_ord", "mkt_fwd1", "mkt_fwd2", "mkt_fwd4"]], on=["date", "hour_ord"], how="left")
    for h in (1, 2, 4):
        full[f"ex{h}"] = (full[f"fwd{h}"] - full[f"mkt_fwd{h}"]) * 1e4

    none_pct = full["line"].isna().mean() * 100
    print(f"整體無線比例(股-小時K 層級) {none_pct:.1f}%")

    full["dist"] = full["close"] / full["line"] - 1
    full["is_"] = full["date"] <= IS_END
    zone = (full["dist"] >= -0.03) & (full["dist"] <= 0.01) & full["line"].notna()
    first_entry = zone & ~(zone.shift(1).fillna(False) & (full["sid"] == full["sid"].shift(1)) & (full["line"] == full["line"].shift(1)))
    ev = full[first_entry].dropna(subset=["ex1", "ex2", "ex4"])
    print(f"\n==== C. 貼近線(−3%~+1%)首次進入事件:n={len(ev)}(IS {ev['is_'].sum()} / OOS {(~ev['is_']).sum()}) ====")
    for h in (1, 2, 4):
        for sl, m in (("IS", ev["is_"]), ("OOS", ~ev["is_"])):
            g_ = ev[m]
            if len(g_) < 15:
                print(f"  持{h}小時 {sl}: n<15({len(g_)})"); continue
            mu, t = cl_t(g_[f"ex{h}"].values, g_["date"].values)
            print(f"  持{h}小時 {sl}: n={len(g_):4d} 超額{mu:+6.1f}bps(t{t:+4.2f}) 勝率{(g_[f'ex{h}']>0).mean()*100:3.0f}%")

    print("\n==== D. 對照:同一貼近線事件,原始報酬(不扣大盤)供比較 ====")
    for h in (1, 2, 4):
        g_ = ev
        raw = (g_[f"fwd{h}"] * 1e4)
        mu, t = cl_t(raw.dropna().values, g_.loc[raw.notna(), "date"].values)
        print(f"  持{h}小時 全樣本原始: n={raw.notna().sum():4d} 均{mu:+6.1f}bps(t{t:+4.2f})")


if __name__ == "__main__":
    main()
