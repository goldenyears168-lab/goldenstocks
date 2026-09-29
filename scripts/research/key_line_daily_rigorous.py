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
from biglot_score_v23_fit import cl_t  # noqa: E402


def ols_cluster(X, y, cl):
    """與 biglot_score_v23_fit.ols_cluster 數學等價,但 meat 用 groupby 一次算完。

    原版 `for gid in np.unique(cl): m = cl == gid` 是 O(日數 × 列數);本案樣本 ~20 萬列 × ~5,200
    個交易日,單次呼叫要數十秒,F 段要跑 70+ 次迴歸會爆掉。改成把 Xc*e 依 cluster 分組加總後
    meat = S.T @ S(同一式子的向量化寫法),結果相同、快兩個數量級。
    """
    Xc = np.column_stack([np.ones(len(y)), X])
    XtX_inv = np.linalg.pinv(Xc.T @ Xc); b = XtX_inv @ Xc.T @ y; e = y - Xc @ b
    S = pd.DataFrame(Xc * e[:, None]).groupby(np.asarray(cl)).sum().values
    meat = S.T @ S
    se = np.sqrt(np.diag(XtX_inv @ meat @ XtX_inv))
    return b[1:], se[1:]

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

    # ==== F. 新定義(2026-09-29 jack 定案):①跌破即作廢 ②觸發棒要「近」,不是近 500 日內有就算 ====
    # 四態拆解(給定時效窗 N 個交易日):
    #   base   = 最近 N 日內沒有觸發棒,更早也沒有  → 本來就畫不出線
    #   stale  = 有觸發棒但已是 N 日以前            → 線太舊,jack 認為不該再算數
    #   broken = N 日內有觸發棒但收盤已跌破         → 破了就畫不出線
    #   alive  = N 日內有觸發棒且未跌破             → 唯一「能畫線」、才顯示距離%
    # 判準:(1) stale / broken 的未來超額要 ≈ base,合併成「不能畫線」才不損失資訊;
    #       (2) 新 has_line 的 IS/OOS 係數要同號、t 不明顯縮水,②「無線要避開」才能沿用到新口徑。
    voided = np.zeros(len(full), dtype=bool)
    _pos = 0
    for _sid, g in full.groupby("sid", sort=False):
        ln, tg, cc = g["line"].values, g["trig"].values, g["close"].values
        v = False
        arr = np.zeros(len(g), dtype=bool)
        for i in range(len(g)):
            if tg[i]:
                v = False                      # 新觸發棒 → 重新畫得出線
            if not np.isnan(ln[i]) and cc[i] < ln[i]:
                v = True                       # 收盤跌破 → 當日起作廢,直到下一根觸發棒
            arr[i] = v
        voided[_pos:_pos + len(g)] = arr
        _pos += len(g)
    full["voided"] = voided
    chk = full.dropna(subset=["ex10", "ex20", "ex60"]).copy()

    print("\n==== F1. 時效窗 N × 作廢規則:has_line 係數(橫斷面 OLS,日聚類;正=能畫線的未來相對更好) ====")
    for N in (10, 20, 60, 120, 500):
        for void_on in (False, True):
            col = (chk["days_since_trig"] <= N) & chk["line"].notna()
            if void_on:
                col = col & ~chk["voided"]
            x = col.astype(float).values.reshape(-1, 1)
            row = f"  N={N:3d}日 {'跌破作廢' if void_on else '不作廢  '}:"
            for h in (10, 20, 60):
                for sl, m in (("IS", chk["is_"].values), ("OOS", ~chk["is_"].values)):
                    b_, se_ = ols_cluster(x[m], chk[f"ex{h}"].values[m], chk["date"].values[m])
                    row += f" {h}d{sl} {b_[0]:+6.1f}(t{b_[0]/se_[0]:+5.2f})"
                row += " |"
            print(row + f" 能畫線佔{col.mean()*100:4.1f}%")

    print("\n==== F2. 四態拆解(N=10 / N=20;base 為基準組,同一迴歸放三個 dummy) ====")
    for N in (10, 20):
        recent = chk["line"].notna() & (chk["days_since_trig"] <= N)
        st = np.where(~chk["line"].notna(), "base",
              np.where(~recent, "stale",
               np.where(chk["voided"], "broken", "alive")))
        chk[f"st{N}"] = st
        X = np.column_stack([(st == "stale").astype(float),
                             (st == "broken").astype(float),
                             (st == "alive").astype(float)])
        print(f"  --- N={N} 日 · 佔比 " + " ".join(f"{k}{(st==k).mean()*100:.1f}%" for k in ("base", "stale", "broken", "alive")))
        for h in (10, 20, 60):
            for sl, m in (("IS", chk["is_"].values), ("OOS", ~chk["is_"].values)):
                b_, se_ = ols_cluster(X[m], chk[f"ex{h}"].values[m], chk["date"].values[m])
                print(f"    持{h:2d}日 {sl:3s}: stale−base {b_[0]:+7.1f}(t{b_[0]/se_[0]:+5.2f})  "
                      f"broken−base {b_[1]:+7.1f}(t{b_[1]/se_[1]:+5.2f})  alive−base {b_[2]:+7.1f}(t{b_[2]/se_[2]:+5.2f})")

    print("\n==== F3. 各態的絕對超額均值(不是對比基準組,看量級用;N=20) ====")
    for h in (10, 20, 60):
        for sl, m in (("IS", chk["is_"]), ("OOS", ~chk["is_"])):
            g_ = chk[m]
            row = f"  持{h:2d}日 {sl:3s}:"
            for k in ("base", "stale", "broken", "alive"):
                q = g_[g_["st20"] == k]
                if len(q) < 50:
                    row += f" {k} n<50 |"; continue
                mu, t = cl_t(q[f"ex{h}"].values, q["date"].values)
                row += f" {k} n={len(q):6d} {mu:+7.1f}(t{t:+5.2f}) |"
            print(row)

    print("\n==== F4. 最新交易日 42 檔狀態分佈(換定義後儀表板會怎麼顯示) ====")
    for N in (10, 20):
        recent = full["line"].notna() & (full["days_since_trig"] <= N)
        full[f"st{N}"] = np.where(~full["line"].notna(), "base",
                          np.where(~recent, "stale",
                           np.where(full["voided"], "broken", "alive")))
        last = full.sort_values("date").groupby("sid").tail(1)
        print(f"  --- N={N} 日({last['date'].max()})")
        for k, lab in (("base", "沒有(近期無觸發棒)"), ("stale", "沒有(線太舊已過期)"),
                       ("broken", "沒有(已跌破作廢)"), ("alive", "有線→顯示距離%")):
            q = last[last[f"st{N}"] == k]
            print(f"    {lab}: {len(q):2d} 檔  " + " ".join(f"{s}{names.get(s,'')}" for s in sorted(q["sid"])))


if __name__ == "__main__":
    main()
