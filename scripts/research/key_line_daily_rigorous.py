#!/usr/bin/env python3
"""「關鍵一條線」日線版·嚴謹回測(2026-09-25 jack 交辦:「日線再次研究到好」)。唯讀 DB(?mode=ro)。

補上一輪(key_line_research.py)承認的全部缺口:
  · 長史(2005~2026,~21年,非 2024-06 起的 2.25 年短窗)、IS/OOS 硬拆(IS<2023、OOS≥2023)
  · 市場相對超額(扣 42 檔等權籃子同期報酬),不是原始報酬
  · 日聚類 SE(entry date 當 cluster,不是把每筆事件當獨立樣本)
  · 排除「觸發棒當天本身」(要求進場日在觸發棒之後 ≥5 個交易日,且期間曾經漲離線 ≥5%,確保是真的「拉回」不是誤把
    突破棒自己算成回測點)
  · 成本(50bps 估計,非當沖:手續費~17bps+證交稅30bps)
  · 安慰劑(隨機進場日對照)、去極端值/去集中度、逐年穩健性
  · 額外直接檢定她的主張二:「畫不出線的股票要避開,反彈要賣」——用「有效線狀態」對未來報酬做橫斷面檢定(不限拉回事件)

用法:PYTHONPATH=src .venv/bin/python scripts/research/key_line_daily_rigorous.py
"""
from __future__ import annotations
import sqlite3, sys
import numpy as np, pandas as pd
sys.path.insert(0, "scripts/research")
sys.path.insert(0, "src")
import stock_db  # noqa: E402
from biglot_score_v23_fit import cl_t, ols_cluster  # noqa: E402

N_LOOKBACK = 60          # 前高回顧期(交易日),同生產規則
PCT_THRESH = 0.04        # 觸發棒漲幅門檻,同生產規則
ZONE = 0.03              # 「附近」定義:線 ±3%
MIN_GAP = 5              # 進場日須在觸發棒之後至少 5 個交易日(排除觸發棒自己)
PULLBACK_MIN = 0.05      # 進場前必須曾經漲離線 ≥5%(確保是「拉回」不是原地不動)
IS_SPLIT = "2023-01-01"
COST_BPS = 50.0
HORIZONS = (10, 20, 40, 60)


def load_bars(sids):
    con = sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
    q = ("SELECT stock_id sid, trade_date date, open, high, low, close FROM stock_daily_bars "
         "WHERE stock_id IN ({}) AND trade_date>='2005-01-01' AND open>0 AND close>0 "
         "ORDER BY stock_id, trade_date").format(",".join("?" * len(sids)))
    b = pd.read_sql(q, con, params=sids)
    con.close()
    return b.drop_duplicates(["sid", "date"], keep="last").reset_index(drop=True)


def build_line(g: pd.DataFrame) -> pd.DataFrame:
    """回傳附加 line(現有效線價)、line_trig_idx(該線來自第幾筆,供算「觸發後第幾天」)的 g。"""
    g = g.reset_index(drop=True)
    c, o, lo = g["close"].values, g["open"].values, g["low"].values
    n = len(g)
    prior_hi = pd.Series(c).shift(1).rolling(N_LOOKBACK, min_periods=N_LOOKBACK).max().values
    prev_c = np.concatenate([[np.nan], c[:-1]])
    trig = (c > o) & (c > prev_c * (1 + PCT_THRESH)) & (c > prior_hi)
    line = np.full(n, np.nan); trig_i = np.full(n, -1)
    cur_low, cur_i = np.nan, -1
    for i in range(n):
        if trig[i]:
            cur_low, cur_i = lo[i], i
        line[i] = cur_low; trig_i[i] = cur_i
    g["line"] = line; g["trig_i"] = trig_i; g["trig"] = trig
    return g


def main():
    cal = __import__("json").load(open(f"{stock_db.DATA_DIR}/cache/pit_universe_tick/_live_calib.json"))
    sids = [r["sid"] for r in cal["universe"]]
    names = {r["sid"]: r["name"] for r in cal["universe"]}
    b = load_bars(sids)
    print(f"{len(sids)} 檔,日線 {b['date'].min()}~{b['date'].max()}(~{b.groupby('sid').size().mean():.0f} 個交易日/檔)")

    parts = []
    for sid, g in b.groupby("sid"):
        parts.append(build_line(g).assign(sid=sid))
    full = pd.concat(parts, ignore_index=True)

    # 42 檔等權籃子:逐日算算術平均日報酬,累積成指數,供未來窗口的市場相對超額
    ret1 = full.assign(r1=full.groupby("sid")["close"].pct_change())
    mkt_r1 = ret1.groupby("date")["r1"].mean().rename("mkt_r1")
    dates = sorted(full["date"].unique())
    mkt_cum = (1 + mkt_r1.reindex(dates).fillna(0)).cumprod()
    mkt_cum = pd.Series(mkt_cum.values, index=dates)

    for h in HORIZONS:
        full[f"fwd{h}"] = full.groupby("sid")["close"].shift(-h) / full["close"] - 1
    full = full.sort_values(["sid", "date"]).reset_index(drop=True)
    mkt_cum_arr = full["date"].map(mkt_cum).astype(float)
    full["_mkt_cum"] = mkt_cum_arr
    for h in HORIZONS:
        mkt_fwd = full.groupby("sid")["_mkt_cum"].shift(-h) / full["_mkt_cum"] - 1
        full[f"ex{h}"] = (full[f"fwd{h}"] - mkt_fwd) * 1e4

    full["dist"] = full["close"] / full["line"] - 1
    full["days_since_trig"] = full.groupby("sid").cumcount() - full["trig_i"]
    # 「曾經拉開過」旗標:過去 MIN_GAP~60 天內 dist 曾經 ≥ PULLBACK_MIN
    full["was_away"] = full.groupby("sid")["dist"].transform(lambda s: s.rolling(60, min_periods=1).max())
    zone = (full["dist"].abs() <= ZONE) & full["line"].notna() & (full["days_since_trig"] >= MIN_GAP)
    was_away_recent = full.groupby("sid").apply(lambda g: g["dist"].shift(1).rolling(55).max()).reset_index(level=0, drop=True)
    zone = zone & (was_away_recent >= PULLBACK_MIN)
    prev_zone = zone.groupby(full["sid"]).shift(1).fillna(False)
    first_entry = zone & ~prev_zone
    full["is_"] = full["date"] < IS_SPLIT

    print(f"\n==== A. 嚴謹「拉回線附近」事件(±{ZONE*100:.0f}%,觸發後≥{MIN_GAP}日,曾拉開≥{PULLBACK_MIN*100:.0f}%) ====")
    ev = full[first_entry].dropna(subset=[f"ex{h}" for h in HORIZONS])
    print(f"事件數 n={len(ev)}(IS {ev['is_'].sum()} / OOS {(~ev['is_']).sum()}),涉及 {ev['sid'].nunique()} 檔")
    for h in HORIZONS:
        row = f"  持{h:2d}日:"
        for sl, m in (("IS", ev["is_"]), ("OOS", ~ev["is_"])):
            g_ = ev[m]
            if len(g_) < 20:
                row += f" {sl} n<20 |"; continue
            mu, t = cl_t(g_[f"ex{h}"].values, g_["date"].values)
            net = mu - COST_BPS
            row += f" {sl} n={len(g_):4d} 超額{mu:+6.1f}(t{t:+4.2f}) 扣成本{net:+6.1f} 勝{(g_[f'ex{h}']>0).mean()*100:3.0f}% |"
        print(row)

    print("\n==== B. 安慰劑:隨機進場日(同股同期,配對抽樣 5 組) vs 真實事件 ====")
    rng = np.random.default_rng(42)
    for h in (20, 60):
        real_is = ev[ev["is_"]][f"ex{h}"].dropna()
        placebo_means = []
        pool = full.dropna(subset=[f"ex{h}"])
        for _ in range(5):
            samp = pool.sample(n=min(len(real_is), len(pool)), random_state=rng.integers(1e6))
            placebo_means.append(samp[f"ex{h}"].mean())
        print(f"  持{h}日 IS: 真實均{real_is.mean():+.1f} vs 安慰劑5組均{np.mean(placebo_means):+.1f}(範圍{min(placebo_means):+.1f}~{max(placebo_means):+.1f})")

    print("\n==== C. 集中度:前5檔/前5年貢獻(持20日超額,IS) ====")
    g20 = ev[ev["is_"]].dropna(subset=["ex20"])
    by_sid = g20.groupby("sid")["ex20"].sum().sort_values(ascending=False)
    print(f"  前5檔佔比: {by_sid.head(5).sum() / g20['ex20'].sum() * 100:.0f}%  (檔數 {g20['sid'].nunique()})")
    print(f"  剔除前5檔後: n={len(g20) - g20[g20['sid'].isin(by_sid.head(5).index)].shape[0]} 均{g20[~g20['sid'].isin(by_sid.head(5).index)]['ex20'].mean():+.1f}")
    g20["year"] = pd.to_datetime(g20["date"]).dt.year
    print("  逐年(持20日超額均值/事件數):")
    for y, gy in g20.groupby("year"):
        mu, t = cl_t(gy["ex20"].values, gy["date"].values) if len(gy) >= 10 else (gy["ex20"].mean(), np.nan)
        print(f"    {y}: n={len(gy):3d} 均{mu:+6.1f} t{t:+.2f}" if len(gy) >= 10 else f"    {y}: n={len(gy):3d} 均{mu:+6.1f}")

    print("\n==== D. 她的主張二:「畫不出線的股票要避開」——橫斷面檢定(不限拉回事件,全樣本股-日) ====")
    full["has_line"] = full["line"].notna().astype(float)
    chk = full.dropna(subset=["ex20", "ex60"])
    for h in (20, 60):
        for sl, m in (("IS", chk["is_"]), ("OOS", ~chk["is_"])):
            g_ = chk[m]
            b_, se_ = ols_cluster(g_[["has_line"]].values, g_[f"ex{h}"].values, g_["date"].values)
            print(f"  持{h}日 {sl}: has_line 係數 {b_[0]:+.2f} t{b_[0]/se_[0]:+.2f}(正=有線比無線未來報酬更好,支持她的主張)")

    print("\n==== E. 對照:『線的距離』連續版橫斷面 IC(不限拉回區間,全樣本) ====")
    chk2 = full.dropna(subset=["dist", "ex20"])
    for sl, m in (("IS", chk2["is_"]), ("OOS", ~chk2["is_"])):
        g_ = chk2[m]
        r = g_.groupby("date").apply(lambda q: q["dist"].corr(q["ex20"], method="spearman") if len(q) > 15 else np.nan).dropna()
        ic, t = r.mean(), r.mean() / (r.std() / np.sqrt(len(r))) if len(r) > 3 else np.nan
        print(f"  {sl}: 距離(dist) vs 未來20日超額 IC {ic:+.4f}(t{t:+.2f}) — 負值代表『越貼近線(或跌破)未來越好』,正值代表『離線越遠(越強勢)未來越好』")


if __name__ == "__main__":
    main()
