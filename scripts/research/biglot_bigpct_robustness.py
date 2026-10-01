"""大戶佔比橫斷面訊號：穩健性第二輪（2026-10-01）。

第一輪（`biglot_bigpct_vs_txnight_orthogonality.py`）已證：大戶佔比對次日開盤跳空的
預測力全部落在橫斷面選股成分（t+8.02），市場擇時成分為零（t+0.06），且與台指期夜盤
正交（corr −0.061）、控制後 t 值上升。

本輪補三件第一輪沒做的事：
  ① 窗口延長到 2026-09（日層面板只到 08，改從 bucket5 面板重聚合，並與舊日層面板對帳）
  ② IS/OOS 拆分 + 逐月 IC 穩定性
  ③ **前一日價量控制** —— 這是最可能殺掉訊號的一關。大戶淨買常發生在上漲日，
     若 bigpct 只是「前一日漲幅」的代理，控制後應歸零（參照 chip-score-is-vol-gap-proxy
     的前例：風險中性後 t 3.20→0.05）。
  ④ 安慰劑：當日內隨機置換 bigpct（破壞 sid 對應、保留日層分布）應得 t≈0。

口徑一律橫斷面去當日均值（_cs），因為第一輪已證日層成分為零、原始口徑會混入夜盤 beta。
"""
import os
import sqlite3
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

SCR = os.path.expanduser("~/goldenstocks-data/scratch")
DB = os.path.expanduser("~/goldenstocks-data/data/stocks.db")
BPS = 10000.0
OOS_START = "2026-07-01"          # IS: 2026-03~06 / OOS: 2026-07~09
CTRL = ["s_ret1", "s_gap1", "s_amp1", "s_volr", "s_ma20"]


def daily_from_bucket5():
    b = pd.read_csv(f"{SCR}/biglot_panels/pit100_bucket5_panel_2026-03_09.csv",
                    dtype={"sid": str}, usecols=["sid", "date", "big_net", "ret_net",
                                                 "mid_net", "tot_amt"])
    d = b.groupby(["sid", "date"], as_index=False).sum(numeric_only=True)
    old = pd.read_csv(f"{SCR}/biglot_panels/pit100_daily_panel_2026-03_08.csv", dtype={"sid": str})
    old["big_net_old"] = old.big_buy - old.big_sell
    chk = d.merge(old[["sid", "date", "big_net_old", "tot_amt"]], on=["sid", "date"],
                  suffixes=("", "_old"))
    print(f"[對帳] 與舊日層面板重疊 {len(chk)} 列: "
          f"corr(big_net)={chk.big_net.corr(chk.big_net_old):.6f}  "
          f"corr(tot_amt)={chk.tot_amt.corr(chk.tot_amt_old):.6f}  "
          f"max|Δbig/tot|={(chk.big_net - chk.big_net_old).abs().div(chk.tot_amt).max():.2e}")
    return d


def prices(sids):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ph = ",".join("?" * len(sids))
    px = pd.read_sql(
        f"SELECT stock_id AS sid, trade_date AS date, open, high, low, close, volume "
        f"FROM stock_daily_bars WHERE stock_id IN ({ph}) AND source='finmind' "
        f"AND trade_date>='2026-01-01' AND trade_date<='2026-09-30' AND close>0 AND open>0",
        con, params=list(sids))
    ex = pd.read_sql(
        "SELECT stock_id AS sid, ex_date FROM stock_price_adjustment_events "
        "WHERE ex_date>='2026-01-01' AND ex_date<='2026-09-30'", con)
    con.close()
    px = px.drop_duplicates(["sid", "date"]).sort_values(["sid", "date"])
    g = px.groupby("sid", sort=False)
    px["gap"] = np.log(px.open / g.close.shift(1)) * BPS
    c2c = np.log(px.close / g.close.shift(1))
    px["s_ret1"] = g.apply(lambda x: np.log(x.close / x.close.shift(1)).shift(1),
                           include_groups=False).reset_index(level=0, drop=True) * 100
    px["s_gap1"] = g.gap.shift(1) / 100.0
    px["s_amp1"] = g.apply(lambda x: ((x.high - x.low) / x.close).shift(1),
                           include_groups=False).reset_index(level=0, drop=True) * 100
    px["s_volr"] = g.apply(lambda x: np.log(x.volume / x.volume.rolling(20).mean()).shift(1),
                           include_groups=False).reset_index(level=0, drop=True)
    px["s_ma20"] = g.apply(lambda x: (np.log(x.close) - np.log(x.close.rolling(20).mean())).shift(1),
                           include_groups=False).reset_index(level=0, drop=True) * 100
    px["_c2c"] = c2c
    bad = set(zip(ex.sid.astype(str), ex.ex_date.astype(str)))
    px["_exdiv"] = [k in bad for k in zip(px.sid, px.date)]
    return px


def build():
    d = daily_from_bucket5()
    px = prices(sorted(d.sid.unique()))
    d = d.sort_values(["sid", "date"])
    d["bigpct"] = d.big_net / d.tot_amt * 100
    d["retpct"] = d.ret_net / d.tot_amt * 100
    d["midpct"] = d.mid_net / d.tot_amt * 100
    g = d.groupby("sid", sort=False)
    for c in ("bigpct", "retpct", "midpct"):
        d[f"{c}_lag"] = g[c].shift(1)
    d["prev_date"] = g.date.shift(1)
    m = d.merge(px, on=["sid", "date"], how="inner")
    n0 = len(m)
    m = m[~m._exdiv].dropna(subset=["gap", "bigpct_lag"] + CTRL)
    print(f"[清理] {n0} → {len(m)} 列（剔除除權息日與缺控制變數列）")
    # 橫斷面去當日均值
    for c in ["gap", "bigpct_lag", "retpct_lag", "midpct_lag"] + CTRL:
        m[f"{c}_cs"] = m[c] - m.groupby("date")[c].transform("mean")
    m["seg"] = np.where(m.date < OOS_START, "IS", "OOS")
    return m


def ols(df, formula, label, key="bigpct_lag_cs"):
    mod = smf.ols(formula, data=df).fit(cov_type="cluster", cov_kwds={"groups": df["date"]})
    b, t = mod.params.get(key, np.nan), mod.tvalues.get(key, np.nan)
    print(f"  {label:<40} beta={b:+7.3f} t={t:+6.2f}  R²={mod.rsquared*100:5.2f}%  n={int(mod.nobs)}")
    return mod


def ic(df, x="bigpct_lag", y="gap_cs"):
    s = df.groupby("date").apply(lambda g: g[x].corr(g[y], method="spearman"),
                                 include_groups=False).dropna()
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if len(s) > 2 else np.nan
    return s.mean(), t, len(s), (s > 0).mean()


def ls_spread(df, x="bigpct_lag", y="gap_cs", q=10):
    d = df.copy()
    d["dec"] = d.groupby("date")[x].transform(
        lambda s: pd.qcut(s.rank(method="first"), q, labels=False) + 1)
    per = d[d.dec.isin([1, q])].groupby(["date", "dec"])[y].mean().unstack()
    sp = (per[q] - per[1]).dropna()
    return sp.mean(), sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp))), len(sp), (sp > 0).mean()


def main():
    m = build()
    print(f"[樣本] {len(m)} stock-days · {m.sid.nunique()} 檔 · {m.date.nunique()} 日 "
          f"({m.date.min()}~{m.date.max()})")
    for s, g in m.groupby("seg"):
        print(f"         {s}: {len(g)} 列 · {g.date.nunique()} 日 "
              f"({g.date.min()}~{g.date.max()}) 籃子均跳空 {g.gap.mean():+.1f}bps")

    print("\n" + "=" * 86)
    print("① 前一日價量控制（最關鍵一關）—— 目標 gap_cs，全部變數橫斷面去均值")
    print("=" * 86)
    base = "gap_cs ~ bigpct_lag_cs"
    ols(m, base, "A 裸訊號")
    for c in CTRL:
        ols(m, f"{base} + {c}_cs", f"B 加 {c}")
    full = base + " + " + " + ".join(f"{c}_cs" for c in CTRL)
    mf = ols(m, full, "C 五個控制全加")
    print("    控制變數自身係數（C 式）：")
    for c in CTRL:
        k = f"{c}_cs"
        print(f"      {c:<8} beta={mf.params[k]:+8.3f}  t={mf.tvalues[k]:+6.2f}")
    ols(m, full + " + retpct_lag_cs + midpct_lag_cs", "D 再加散戶/中實桶")

    print("\n" + "=" * 86)
    print("② IS / OOS 拆分（切點 2026-07-01）")
    print("=" * 86)
    for s in ("IS", "OOS"):
        g = m[m.seg == s]
        print(f"  --- {s} ({g.date.nunique()} 日)")
        ols(g, base, f"{s} 裸訊號")
        ols(g, full, f"{s} 五控制")
        i, t, n, w = ic(g)
        sp, tsp, nsp, wsp = ls_spread(g)
        print(f"      IC={i:+.4f} t={t:+.2f} 勝率={w*100:.0f}%  |  "
              f"D10−D1={sp:+.1f}bps t={tsp:+.2f} 勝率={wsp*100:.0f}% n={nsp}夜")

    print("\n" + "=" * 86)
    print("③ 逐月 IC（bigpct → gap_cs）")
    print("=" * 86)
    m["ym"] = m.date.str[:7]
    for ym, g in m.groupby("ym"):
        i, t, n, w = ic(g)
        sp, tsp, _, _ = ls_spread(g)
        print(f"  {ym}  n={n:>2}日  IC={i:+.4f} t={t:+5.2f} 勝率={w*100:>3.0f}%   "
              f"D10−D1={sp:+7.1f}bps t={tsp:+5.2f}")

    print("\n" + "=" * 86)
    print("④ 安慰劑：日內隨機置換 bigpct（保留日層分布、破壞 sid 對應）")
    print("=" * 86)
    rng = np.random.default_rng(20261001)
    ts, ics = [], []
    for _ in range(20):
        p = m.copy()
        p["bigpct_lag"] = p.groupby("date").bigpct_lag.transform(
            lambda s: s.to_numpy()[rng.permutation(len(s))])
        p["bigpct_lag_cs"] = p.bigpct_lag - p.groupby("date").bigpct_lag.transform("mean")
        mod = smf.ols(base, data=p).fit(cov_type="cluster", cov_kwds={"groups": p["date"]})
        ts.append(mod.tvalues["bigpct_lag_cs"]); ics.append(ic(p)[0])
    print(f"  20 次置換 t 值: 中位 {np.median(ts):+.2f}  範圍 [{min(ts):+.2f},{max(ts):+.2f}]")
    print(f"  20 次置換 IC  : 中位 {np.median(ics):+.5f} 範圍 [{min(ics):+.5f},{max(ics):+.5f}]")
    i, t, n, w = ic(m)
    print(f"  真實值        : IC={i:+.4f} t={t:+.2f}  → 真實值"
          f"{'在' if min(ts) <= t <= max(ts) else '完全在'}置換範圍"
          f"{'內（警訊）' if min(ts) <= t <= max(ts) else '外（通過）'}")

    print("\n" + "=" * 86)
    print("⑤ 全樣本總表")
    print("=" * 86)
    for x, lab in [("bigpct_lag", "大戶"), ("midpct_lag", "中實"), ("retpct_lag", "散戶")]:
        i, t, n, w = ic(m, x)
        sp, tsp, nsp, wsp = ls_spread(m, x)
        print(f"  {lab}  IC={i:+.4f} t={t:+6.2f} 勝率={w*100:>3.0f}%   "
              f"D10−D1={sp:+7.1f}bps t={tsp:+6.2f} 勝率={wsp*100:>3.0f}%  n={nsp}夜")


if __name__ == "__main__":
    main()
