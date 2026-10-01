"""大戶佔比 ⊥ 台指期夜盤：控制檢定（2026-10-01）。

問題：`大戶佔比`(T-1 13:30 已知) 對 gap_T 的預測力，在控制 `tx_night`(T 08:30 已知)
與 `smh_un`(美股 05:00→08:00) 之後還剩多少？

因果時序：bigpct_{T-1}(13:30) → 夜盤窗(P 13:46 → T 08:30) → gap_T(09:00)
→ 訊號嚴格早於控制變數，所以「控制後歸零」只能解讀為「同一份資訊被夜盤提前定價」，
   不可能是前視。

分解：bigpct = 當日橫斷面均值(市場擇時成分) + 橫斷面偏離(選股成分)。
兩者面對的對手不同：day-mean 直接與 tx_night 爭同一份變異；cs 部分對任何日層變數
在建構上正交。不分解就會把兩件事混成一個係數。

資料：
  biglot_panels/pit100_daily_panel_2026-03_08.csv  (100 檔 × 127 日, 2026-03-02~2026-09-01)
  gapfcst/nightvars.pkl['tx']  tx_night
  usnight/xun.pkl              smh_un / nq_un
  stocks.db stock_price_adjustment_events (除權息日剔除，7 月是台股除息旺季)
"""
import os
import sqlite3
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

SCR = os.path.expanduser("~/goldenstocks-data/scratch")
DB = os.path.expanduser("~/goldenstocks-data/data/stocks.db")
BPS = 10000.0


def load_panel():
    d = pd.read_csv(f"{SCR}/biglot_panels/pit100_daily_panel_2026-03_08.csv", dtype={"sid": str})
    d = d.sort_values(["sid", "date"]).reset_index(drop=True)
    d["bigpct"] = (d.big_buy - d.big_sell) / d.tot_amt * 100.0
    d["retpct"] = d.ret_net / d.tot_amt * 100.0
    d["midpct"] = d.mid_net / d.tot_amt * 100.0
    g = d.groupby("sid", sort=False)
    # gap_T = ln(open_T / close_{T-1})；訊號取前一交易日
    d["prev_close"] = g.close_px.shift(1)
    d["prev_date"] = g.date.shift(1)
    d["gap"] = np.log(d.open_px / d.prev_close) * BPS
    for c in ("bigpct", "retpct", "midpct"):
        d[f"{c}_lag"] = g[c].shift(1)
    return d


def drop_exdiv(d):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ex = pd.read_sql(
        "SELECT stock_id, ex_date FROM stock_price_adjustment_events "
        "WHERE ex_date>='2026-03-01' AND ex_date<='2026-09-30'", con)
    con.close()
    bad = set(zip(ex.stock_id.astype(str), ex.ex_date.astype(str)))
    key = list(zip(d.sid, d.date))
    mask = np.array([k not in bad for k in key])
    print(f"[清理] 除權息事件 {len(bad)} 筆 → 剔除 {(~mask).sum()} 個 stock-day")
    return d[mask].copy()


def add_controls(d):
    tx = pd.read_pickle(f"{SCR}/gapfcst/nightvars.pkl")["tx"].copy()
    tx.index = tx.index.astype(str)
    xun = pd.read_pickle(f"{SCR}/usnight/xun.pkl").copy()
    xun.index = xun.index.astype(str)
    d["tx_night"] = d.date.map(tx.tx_night) * 100.0     # %
    d["smh_un"] = d.date.map(xun.smh_un) * 100.0
    d["nq_un"] = d.date.map(xun.nq_un) * 100.0
    return d


def decompose(d, cols):
    """橫斷面分解：_dm = 當日均值(市場成分)、_cs = 偏離均值(選股成分)。"""
    for c in cols:
        m = d.groupby("date")[c].transform("mean")
        d[f"{c}_dm"] = m
        d[f"{c}_cs"] = d[c] - m
    return d


def ols(df, formula, label):
    m = smf.ols(formula, data=df).fit(cov_type="cluster", cov_kwds={"groups": df["date"]})
    print(f"\n--- {label}")
    print(f"    {formula}   n={int(m.nobs)}  clusters={df.date.nunique()}  R²={m.rsquared*100:.3f}%")
    for k in m.params.index:
        if k == "Intercept":
            continue
        print(f"    {k:<16} beta={m.params[k]:+9.2f}  t={m.tvalues[k]:+6.2f}  p={m.pvalues[k]:.4f}")
    return m


def main():
    d = load_panel()
    d = drop_exdiv(d)
    d = add_controls(d)
    d = d.dropna(subset=["gap", "bigpct_lag", "tx_night", "smh_un"]).copy()
    d = decompose(d, ["gap", "bigpct_lag", "retpct_lag", "midpct_lag"])
    print(f"[樣本] {len(d)} stock-days · {d.sid.nunique()} 檔 · {d.date.nunique()} 日 "
          f"({d.date.min()}~{d.date.max()})")
    print(f"[gap] mean={d.gap.mean():+.1f}bps sd={d.gap.std():.1f}  "
          f"[bigpct_lag] mean={d.bigpct_lag.mean():+.2f}% sd={d.bigpct_lag.std():.2f}")
    print(f"[tx_night] sd={d.groupby('date').tx_night.first().std():.3f}%  "
          f"[smh_un] sd={d.groupby('date').smh_un.first().std():.3f}%")

    print("\n" + "=" * 78)
    print("A. 池化迴歸：原始口徑（儀表板淨分用的就是這個，未做橫斷面去均值）")
    print("=" * 78)
    m1 = ols(d, "gap ~ bigpct_lag", "A1 基準")
    m2 = ols(d, "gap ~ tx_night", "A2 只有夜盤")
    m3 = ols(d, "gap ~ bigpct_lag + tx_night", "A3 兩者同時")
    m4 = ols(d, "gap ~ bigpct_lag + tx_night + smh_un", "A4 加 SMH 盤後")
    print(f"\n    >>> bigpct beta 變化：{m1.params['bigpct_lag']:+.2f} (t{m1.tvalues['bigpct_lag']:+.2f})"
          f" → 控夜盤 {m3.params['bigpct_lag']:+.2f} (t{m3.tvalues['bigpct_lag']:+.2f})"
          f" → 再控SMH {m4.params['bigpct_lag']:+.2f} (t{m4.tvalues['bigpct_lag']:+.2f})")
    print(f"    >>> R²: 只有bigpct {m1.rsquared*100:.2f}% | 只有夜盤 {m2.rsquared*100:.2f}% | "
          f"合併 {m3.rsquared*100:.2f}% → bigpct 增量 ΔR²={(m3.rsquared-m2.rsquared)*100:+.3f}pp")

    print("\n" + "=" * 78)
    print("B. 分解：選股成分（橫斷面偏離）vs 市場擇時成分（當日均值）")
    print("=" * 78)
    ols(d, "gap_cs ~ bigpct_lag_cs", "B1 選股：橫斷面偏離 → 橫斷面 gap")
    ols(d, "gap_cs ~ bigpct_lag_cs + tx_night", "B2 同上加夜盤(建構上應無影響)")
    ols(d, "gap ~ bigpct_lag_cs + bigpct_lag_dm", "B3 兩成分並列 → 原始 gap")
    ols(d, "gap ~ bigpct_lag_cs + bigpct_lag_dm + tx_night + smh_un", "B4 兩成分＋全控制")

    print("\n" + "=" * 78)
    print("C. 日層（n=交易日）：市場擇時成分是否真的預測大盤跳空")
    print("=" * 78)
    dd = d.groupby("date").agg(gap_dm=("gap", "mean"), big_dm=("bigpct_lag", "mean"),
                               ret_dm=("retpct_lag", "mean"), mid_dm=("midpct_lag", "mean"),
                               tx_night=("tx_night", "first"), smh_un=("smh_un", "first"),
                               nq_un=("nq_un", "first")).reset_index()
    print(f"    n={len(dd)} 日;  corr(big_dm, tx_night)={dd.big_dm.corr(dd.tx_night):+.3f}")
    for f, lab in [("gap_dm ~ big_dm", "C1 大戶日均 → 當日籃子跳空"),
                   ("gap_dm ~ tx_night", "C2 夜盤 → 籃子跳空"),
                   ("gap_dm ~ big_dm + tx_night", "C3 兩者同時"),
                   ("gap_dm ~ big_dm + tx_night + smh_un", "C4 全控制")]:
        m = smf.ols(f, data=dd).fit(cov_type="HC1")
        print(f"\n--- {lab}\n    {f}   n={int(m.nobs)}  R²={m.rsquared*100:.2f}%")
        for k in m.params.index:
            if k != "Intercept":
                print(f"    {k:<12} beta={m.params[k]:+9.2f}  t={m.tvalues[k]:+6.2f}  p={m.pvalues[k]:.4f}")

    print("\n" + "=" * 78)
    print("D. 可交易版：13:30 大戶日均 → 當晚台指期夜盤報酬（TMF 現成載具）")
    print("=" * 78)
    for f, lab in [("tx_night ~ big_dm", "D1 大戶日均 → 夜盤"),
                   ("tx_night ~ big_dm + ret_dm", "D2 加散戶日均"),
                   ("tx_night ~ big_dm + mid_dm", "D3 加中實日均")]:
        m = smf.ols(f, data=dd).fit(cov_type="HC1")
        print(f"\n--- {lab}\n    {f}   n={int(m.nobs)}  R²={m.rsquared*100:.2f}%")
        for k in m.params.index:
            if k != "Intercept":
                print(f"    {k:<12} beta={m.params[k]:+9.4f}  t={m.tvalues[k]:+6.2f}  p={m.pvalues[k]:.4f}")
    sd_x, sd_y = dd.big_dm.std(), dd.tx_night.std()
    mde_beta = 2.8 * sd_y / (sd_x * np.sqrt(len(dd)))
    print(f"\n    [MDE] n={len(dd)} 日、sd(big_dm)={sd_x:.3f}%、sd(tx_night)={sd_y:.3f}%")
    print(f"    → 80% 檢定力/雙尾5% 可偵測的最小 beta ≈ {mde_beta:.4f}"
          f"（每 1% 大戶佔比對應 {mde_beta*100:.1f}bps 夜盤）")

    print("\n" + "=" * 78)
    print("E. 橫斷面 IC（Spearman，每日一個，日聚類 t）")
    print("=" * 78)
    for xc, yc, lab in [("bigpct_lag", "gap", "bigpct → gap（原始）"),
                        ("bigpct_lag", "gap_cs", "bigpct → gap 去日均"),
                        ("retpct_lag", "gap_cs", "散戶 → gap 去日均"),
                        ("midpct_lag", "gap_cs", "中實 → gap 去日均")]:
        ic = d.groupby("date").apply(lambda g: g[xc].corr(g[yc], method="spearman"),
                                     include_groups=False).dropna()
        t = ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic)))
        print(f"    {lab:<26} IC={ic.mean():+.4f}  t={t:+.2f}  n={len(ic)} 日  勝率={(ic>0).mean()*100:.1f}%")


if __name__ == "__main__":
    main()
