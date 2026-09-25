#!/usr/bin/env python3
"""ATR(平均真實區間)盤整異常偵測 + 與「關鍵一條線」搭配研究(2026-09-25 jack 交辦)。唯讀 DB。

交辦原文(楊育華節目逐字稿,ATR 段落):「最簡單的指標叫 ATR……你給它一個簡單參數,比如說 5 或 20……
去算出最近的慣性……你給它一個倍數,比如說這個慣性超過 1.5 倍,我覺得不合理,你要立刻出場,因為方向改變了」。
Wilder 原始定義(New Concepts in Technical Trading Systems, 1978;8 或 14 期均可,業界慣例 14):
  TR_t = max(high−low, |high−prev_close|, |low−prev_close|)
  ATR_t = Wilder 平滑:ATR_t = (ATR_{t-1}×(n−1) + TR_t) / n,種子為前 n 筆 TR 的簡單平均。
文獻(2026-09-25 web 搜尋,practitioner 為主、非同儕審查論文,已誠實揭露非學術來源):
  · StockCharts/AvaTrade/FTMO Academy:低 ATR = 盤整壓縮(squeeze),必接高 ATR 突破(expansion),
    「轉折點」是交易機會——與影片說的「慣性超過倍數=方向改變」同一概念的鏡像(壓縮端 vs 突破端)。
  · LuxAlgo/TradersPost:ATR 除以收盤價正規化(ATR%)才能跨標的比較——與本案自家發現
    「固定%門檻跨時段不可比較」(scratch/zscore_normalized_spike_2026-09 系列)完全同構,是同一課題的
    日線版本:ATR% 讓「多大算異常」用該股自己的近期慣性校準,不是全宇宙同一個死板%。

本研究做兩件事,嚴謹回測(21年史·IS/OOS拆2023·日聚類SE·扣42檔等權籃子同期報酬·扣成本·安慰劑·集中度·逐年),
只限這 42 檔(_live_calib.json universe,不用更廣的 pit_universe_tick):
  A. 「盤整中異常」事件本身:近期 ATR% 處於自身歷史低檔(壓縮=盤整)∧ 當日真實區間 > 1.5×ATR14(觸發=異常),
     測突破後(依突破方向)的未來報酬,是否可交易。
  B. 與關鍵一條線搭配:把 key_line_daily_rigorous.py 的『距離%』欄位改成『距離÷ATR%』(幾倍ATR),
     重跑同一個橫斷面 IC 檢定(該研究的 E 段),比較正規化前後 IC/t 有沒有改善——這是直接檢驗
     「ATR 正規化能不能把關鍵一條線做得更好」,不是另起爐灶。

用法:PYTHONPATH=src .venv/bin/python scripts/research/atr_key_line_research.py
"""
from __future__ import annotations
import sqlite3, sys
import numpy as np, pandas as pd
sys.path.insert(0, "scripts/research")
sys.path.insert(0, "src")
import stock_db  # noqa: E402
from biglot_score_v23_fit import cl_t, ols_cluster  # noqa: E402

ATR_N = 14              # Wilder 慣例;業界標準,節目講「5或20」皆可,14 是折衷慣例值
BREAKOUT_K = 1.5         # 節目原話「超過1.5倍」
SQUEEZE_PCTL = 0.30      # 「盤整」定義:近期 ATR% 落在自身過去 120 日的後 30% 分位(自身相對壓縮,非跨股比較)
SQUEEZE_LOOKBACK = 120
N_LOOKBACK = 60          # 關鍵一條線同一規則(生產口徑)
PCT_THRESH = 0.04
IS_SPLIT = "2023-01-01"
COST_BPS = 50.0
HORIZONS = (5, 10, 20, 40, 60)


def load_bars(sids):
    con = sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
    q = ("SELECT stock_id sid, trade_date date, open, high, low, close FROM stock_daily_bars "
         "WHERE stock_id IN ({}) AND trade_date>='2005-01-01' AND open>0 AND close>0 AND high>0 AND low>0 "
         "ORDER BY stock_id, trade_date").format(",".join("?" * len(sids)))
    b = pd.read_sql(q, con, params=sids)
    con.close()
    return b.drop_duplicates(["sid", "date"], keep="last").reset_index(drop=True)


def add_atr(g: pd.DataFrame) -> pd.DataFrame:
    g = g.reset_index(drop=True)
    h, lo, c = g["high"].values, g["low"].values, g["close"].values
    prev_c = np.concatenate([[np.nan], c[:-1]])
    tr = np.maximum.reduce([h - lo, np.abs(h - prev_c), np.abs(lo - prev_c)])
    n = len(g)
    atr = np.full(n, np.nan)
    if n > ATR_N:
        atr[ATR_N] = np.nanmean(tr[1:ATR_N + 1])   # 種子=前 ATR_N 筆 TR 簡單均(略過第0筆無prev_close)
        for i in range(ATR_N + 1, n):
            atr[i] = (atr[i - 1] * (ATR_N - 1) + tr[i]) / ATR_N
    g["tr"] = tr
    g["atr"] = atr
    g["atr_pct"] = atr / c
    return g


def add_key_line(g: pd.DataFrame) -> pd.DataFrame:
    c, o, lo = g["close"].values, g["open"].values, g["low"].values
    n = len(g)
    prior_hi = pd.Series(c).shift(1).rolling(N_LOOKBACK, min_periods=N_LOOKBACK).max().values
    prev_c = np.concatenate([[np.nan], c[:-1]])
    trig = (c > o) & (c > prev_c * (1 + PCT_THRESH)) & (c > prior_hi)
    line = np.full(n, np.nan)
    cur_low = np.nan
    for i in range(n):
        if trig[i]:
            cur_low = lo[i]
        line[i] = cur_low
    g["line"] = line
    return g


def main():
    cal = __import__("json").load(open(f"{stock_db.DATA_DIR}/cache/pit_universe_tick/_live_calib.json"))
    sids = [r["sid"] for r in cal["universe"]]
    assert len(sids) == 42, f"universe 應為 42 檔,實得 {len(sids)}——研究範圍必須限定這 42 檔"
    b = load_bars(sids)
    print(f"{len(sids)} 檔(僅限儀表板 42 檔宇宙),日線 {b['date'].min()}~{b['date'].max()}")

    parts = []
    for sid, g in b.groupby("sid"):
        g = add_atr(g)
        g = add_key_line(g)
        parts.append(g.assign(sid=sid))
    full = pd.concat(parts, ignore_index=True)

    # 42 檔等權籃子市場相對超額(同 key_line_daily_rigorous.py 手法)
    ret1 = full.assign(r1=full.groupby("sid")["close"].pct_change())
    mkt_r1 = ret1.groupby("date")["r1"].mean().rename("mkt_r1")
    dates = sorted(full["date"].unique())
    mkt_cum = (1 + mkt_r1.reindex(dates).fillna(0)).cumprod()
    mkt_cum = pd.Series(mkt_cum.values, index=dates)
    for h in HORIZONS:
        full[f"fwd{h}"] = full.groupby("sid")["close"].shift(-h) / full["close"] - 1
    full = full.sort_values(["sid", "date"]).reset_index(drop=True)
    full["_mkt_cum"] = full["date"].map(mkt_cum).astype(float)
    for h in HORIZONS:
        mkt_fwd = full.groupby("sid")["_mkt_cum"].shift(-h) / full["_mkt_cum"] - 1
        full[f"ex{h}"] = (full[f"fwd{h}"] - mkt_fwd) * 1e4
    full["is_"] = full["date"] < IS_SPLIT
    full["dist_pct"] = full["close"] / full["line"] - 1
    full["dist_atr"] = (full["close"] - full["line"]) / full["atr"]   # 用ATR(價格單位)正規化距離,而非用百分比

    # ---- A. 盤整中異常(壓縮→TR突破ATR倍數)事件 ----
    full["atr_roll_pctl"] = full.groupby("sid")["atr_pct"].transform(
        lambda s: s.rolling(SQUEEZE_LOOKBACK, min_periods=60).apply(
            lambda w: (w.iloc[:-1] < w.iloc[-1]).mean() if len(w) > 1 else np.nan, raw=False))
    full["squeeze"] = full["atr_roll_pctl"] <= SQUEEZE_PCTL
    full["prev_squeeze"] = full.groupby("sid")["squeeze"].shift(1).fillna(False).astype(bool)
    full["tr_ratio_prevatr"] = full["tr"] / full.groupby("sid")["atr"].shift(1)   # 用「昨天已知的ATR」判斷今天,避免用到當天資訊污染
    full["abnormal"] = full["prev_squeeze"] & (full["tr_ratio_prevatr"] > BREAKOUT_K)
    full["dir_up"] = full["close"] > full["open"]

    print(f"\n==== A. 盤整中異常事件(近{SQUEEZE_LOOKBACK}日ATR%分位≤{SQUEEZE_PCTL*100:.0f}% ∧ 當日真實區間>{BREAKOUT_K}×前日ATR{ATR_N}) ====")
    ev = full[full["abnormal"]].dropna(subset=[f"ex{h}" for h in HORIZONS])
    print(f"事件數 n={len(ev)}(IS {ev['is_'].sum()} / OOS {(~ev['is_']).sum()}),{ev['sid'].nunique()} 檔,"
          f"其中收紅(向上突破) {ev['dir_up'].sum()} / 收黑(向下突破) {(~ev['dir_up']).sum()}")
    for updn, m0 in (("向上突破(收紅)", ev["dir_up"]), ("向下突破(收黑)", ~ev["dir_up"])):
        print(f"  -- {updn} --")
        sub = ev[m0]
        for h in HORIZONS:
            row = f"    持{h:2d}日:"
            for sl, m in (("IS", sub["is_"]), ("OOS", ~sub["is_"])):
                g_ = sub[m]
                if len(g_) < 20:
                    row += f" {sl} n<20({len(g_)}) |"; continue
                mu, t = cl_t(g_[f"ex{h}"].values, g_["date"].values)
                net = mu - COST_BPS
                row += f" {sl} n={len(g_):4d} 超額{mu:+6.1f}(t{t:+4.2f}) 扣成本{net:+6.1f} 勝{(g_[f'ex{h}']>0).mean()*100:3.0f}% |"
            print(row)

    print("\n==== A2. 安慰劑:隨機日 vs 真實異常事件(持20日,IS) ====")
    rng = np.random.default_rng(42)
    pool = full.dropna(subset=["ex20"])
    for updn, m0 in (("向上", ev["dir_up"]), ("向下", ~ev["dir_up"])):
        sub_is = ev[m0 & ev["is_"]]["ex20"].dropna()
        if len(sub_is) < 20:
            print(f"  {updn}: n<20,略過安慰劑"); continue
        placebo = [pool.sample(n=len(sub_is), random_state=rng.integers(1e6))["ex20"].mean() for _ in range(5)]
        print(f"  {updn} IS: 真實均{sub_is.mean():+.1f} vs 安慰劑5組均{np.mean(placebo):+.1f}(範圍{min(placebo):+.1f}~{max(placebo):+.1f})")

    print("\n==== A3. 集中度(持20日超額,IS,向上突破) ====")
    g20 = ev[ev["dir_up"] & ev["is_"]].dropna(subset=["ex20"])
    if len(g20) >= 20:
        by_sid = g20.groupby("sid")["ex20"].sum().sort_values(ascending=False)
        print(f"  前5檔佔比: {by_sid.head(5).sum() / g20['ex20'].sum() * 100:.0f}%(檔數 {g20['sid'].nunique()})")
        g20y = g20.assign(year=pd.to_datetime(g20["date"]).dt.year)
        print("  逐年(持20日超額均值/事件數):")
        for y, gy in g20y.groupby("year"):
            if len(gy) >= 10:
                mu, t = cl_t(gy["ex20"].values, gy["date"].values)
                print(f"    {y}: n={len(gy):3d} 均{mu:+6.1f} t{t:+.2f}")
            else:
                print(f"    {y}: n={len(gy):3d} 均{gy['ex20'].mean():+6.1f}(樣本太小未算t)")
    else:
        print("  n<20,略過")

    # ---- B. 關鍵一條線距離% → 改用「距離÷ATR%」(幾倍ATR),重跑橫斷面IC ----
    chk = full.dropna(subset=["dist_pct", "dist_atr", "ex20"])
    print("\n==== B. 關鍵一條線『距離』正規化前後對照:橫斷面 IC(對未來20日超額) ====")
    print("  (同 key_line_daily_rigorous.py 的 E 段方法,原始版=距離%,新版=距離÷ATR)")
    for label, col in (("原始:距離%", "dist_pct"), ("新版:距離÷ATR(幾倍ATR)", "dist_atr")):
        for sl, m in (("IS", chk["is_"]), ("OOS", ~chk["is_"])):
            g_ = chk[m]
            r = g_.groupby("date").apply(lambda q: q[col].corr(q["ex20"], method="spearman") if len(q) > 15 else np.nan).dropna()
            if len(r) < 4:
                print(f"  {label} {sl}: 樣本不足"); continue
            ic, t = r.mean(), r.mean() / (r.std() / np.sqrt(len(r)))
            print(f"  {label} {sl}: IC {ic:+.4f}(t{t:+.2f}, {len(r)}個交易日)")

    print("\n==== B2. 『異常』事件發生時剛好貼近關鍵一條線(±1倍ATR內) vs 遠離線,未來報酬是否有別 ====")
    ev2 = ev.dropna(subset=["dist_atr", "ex20"])
    near = ev2[ev2["dist_atr"].abs() <= 1.0]
    far = ev2[ev2["dist_atr"].abs() > 1.0]
    for lbl, g_ in (("貼近線(|距離|≤1倍ATR)", near), ("遠離線(>1倍ATR)", far)):
        if len(g_) < 20:
            print(f"  {lbl}: n<20({len(g_)}),樣本不足"); continue
        for sl, m in (("IS", g_["is_"]), ("OOS", ~g_["is_"])):
            gg = g_[m]
            if len(gg) < 15:
                print(f"    {lbl} {sl}: n<15({len(gg)})"); continue
            mu, t = cl_t(gg["ex20"].values, gg["date"].values)
            print(f"    {lbl} {sl}: n={len(gg):4d} 持20日超額{mu:+6.1f}(t{t:+4.2f})")

    print("\n==== C. 補充:ATR%本身(不看突破,純連續值)對未來報酬的橫斷面IC(她說ATR不判方向,驗證是否真的無方向資訊) ====")
    chk3 = full.dropna(subset=["atr_pct", "ex20"])
    for sl, m in (("IS", chk3["is_"]), ("OOS", ~chk3["is_"])):
        g_ = chk3[m]
        r = g_.groupby("date").apply(lambda q: q["atr_pct"].corr(q["ex20"], method="spearman") if len(q) > 15 else np.nan).dropna()
        ic, t = r.mean(), r.mean() / (r.std() / np.sqrt(len(r))) if len(r) > 3 else (np.nan, np.nan)
        print(f"  {sl}: ATR% vs 未來20日超額 IC {ic:+.4f}(t{t:+.2f})")


if __name__ == "__main__":
    main()
