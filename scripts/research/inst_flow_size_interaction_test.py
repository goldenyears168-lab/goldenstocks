#!/usr/bin/env python3
"""法人淨買超→隔日跳空：按規模/流動性分層重測（文獻預測小型股增量更高）。

**動機**：`inst-net-buy-next-open-gap.md`（2026-09-03 檢定）在 PIT 流動性前 500 大全樣本測出
三大法人淨買超橫斷面排序 → 隔日開盤跳空 t=40.3，但增量僅 ~15bps、遠低於 47.1bps 成本，
判定不可交易。該檢定**沒有按規模分層**——Taiwan/Asian institutional herding 文獻（機構流的
預測效應集中在小型/低週轉股票，大型股被套利掉）尚未被驗證或否證。原始程式碼在 `/tmp/instgap/`
已隨開發機清理消失，本檔按筆記描述的規格重新建構（非重跑舊碼）。

## 口徑（沿用原筆記以避免引入新差異）
- 上市普通股（4 碼、非 00 開頭）；`stock_daily_bars` 只用 `source='twse_mi_index'`（TWSE 官方
  日成交明細，天然排除上櫃／warrant／ETF；[[institutional-daily-dual-source-lookahead]] 沒提到
  這張表的雙來源坑，但仍顯式鎖 source 以防萬一）。
- `stock_institutional_daily` 只用 `source='twse_t86'`（TWSE 官方三大法人買賣超日報表），
  避開 finmind+twse_t86 雙來源疊列的未來函數陷阱。
- X = 三大法人淨買超 / 20 日中位量（股數，PIT：`shift(1).rolling(20)`，不含訊號當日，比原筆記
  更保守一格），橫斷面依「當日 × 規模分組」做百分位排序（用 `biglot_cross_sectional_lib`，
  基準用完整分組樣本算、今日漲跌停鎖死只在最後排進迴歸樣本那一步剔除）。
- Y = 隔日開盤 / 隔日基準（除權息用 `stock_ex_adjust_event.ref_price`，否則用今收）− 1，
  用交易日曆顯式驗證「下一列」真的是次一交易日（[[institutional-daily-dual-source-lookahead]]
  的第二個坑）。
- 控制：今日報酬、今日自己的跳空、20 日已實現波動（皆為訊號日 T 已知資訊，PIT 合格）。
- t 值：Newey-West(lag=5) 對「每日橫斷面迴歸係數」序列做時間序列校正（沿用 `chip_horizon.py`
  的 `nw_t` 寫法）。

## 規模分組（PIT 流動性排名，依 20 日成交金額中位數，非同日）
- G1 大型：排名 1–100（原筆記母體的最前段）
- G2 中型：排名 101–500（原筆記母體「後 400」）
- G3 小型：排名 501–1500（**原筆記母體排除在外**——這才是文獻講的「小型/低週轉」的真正對照組；
  G1/G2 皆在原本 500 大池子「裡面」再切，本質仍是大中型股，光切 G1 vs G2 測不出文獻預測的效應，
  所以本檔額外建了 G3）

## 散戶反向流交互項（step 5）— 資料限制務必先讀
全市場融資日增減 `stock_margin_daily(source='twse_mi_margn')` 只有 2026-06-01~2026-08-19
（~55 個交易日）有完整覆蓋；`finmind` 來源歷史雖回溯到 2015，但只涵蓋 ETF 成分股宇宙的
90~160 檔（[[margin-daytrade-dual-source-trap]]），拿來對 G3（501–1500 名）做交互項會是
嚴重的樣本選擇偏誤（ETF 成分股本質仍是大中型股，不是真小型股）。因此散戶交互項**只能**在
2026-06~08 這個短窗內、用全市場 `twse_mi_margn` 跑，樣本天數先天不足，MDE 會很大——這是
資料限制，不是效應消失，報告會如實揭露。

用法::

    PYTHONPATH=src .venv/bin/python scripts/research/inst_flow_size_interaction_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
from biglot_cross_sectional_lib import cross_sectional_then_filter  # noqa: E402

from stock_db import connect_ro  # noqa: E402

RAW_START = "2016-06-01"        # 抓取起點，留 20 日滾動窗緩衝
STUDY_START = "2018-01-09"      # 對齊原筆記研究窗
MIN_CLOSE = 10.0
NW_LAG = 5
T_CRIT = 2.0                    # MDE 用的臨界 t（雙尾 ~95%）
COST_RT_BPS = 47.1              # 47.1bps 來回成本（手續費 6 折 0.171% + 證交稅 0.3%，沿用原筆記）
MIN_GROUP_N = 30                # 單日單組最少檔數才跑迴歸／分位
STOCK_RE = r"[1-9]\d{3}"

GROUP_DEFS = [
    ("G1_large_top100", 1, 100),
    ("G2_mid_101_500", 101, 500),
    ("G3_small_501_1500", 501, 1500),
]

MARGIN_START = "2026-06-01"
MARGIN_END = "2026-08-19"


# --------------------------------------------------------------------------
# 資料載入
# --------------------------------------------------------------------------

def load_price_panel(start: str = RAW_START) -> pd.DataFrame:
    conn = connect_ro()
    px = pd.read_sql_query(
        """SELECT stock_id, trade_date, open, high, low, close, volume, amount
             FROM stock_daily_bars
            WHERE source='twse_mi_index' AND trade_date>=?
              AND open>0 AND close>0 AND high>0 AND low>0""",
        conn, params=(start,),
    )
    ex = pd.read_sql_query(
        "SELECT stock_id, anchor_date AS trade_date, ref_price FROM stock_ex_adjust_event",
        conn,
    )
    conn.close()

    px = px[px.stock_id.str.fullmatch(STOCK_RE)].copy()
    px = px.sort_values(["stock_id", "trade_date"]).reset_index(drop=True)
    dup = px.duplicated(["stock_id", "trade_date"]).sum()
    assert dup == 0, f"stock_daily_bars(twse_mi_index) 出現重複 stock-day：{dup} 筆"

    g = px.groupby("stock_id", group_keys=False)
    px["prev_close"] = g["close"].shift(1)
    px["ret_t"] = px["close"] / px["prev_close"] - 1.0
    # 未還原分割等資料異常防呆（沿用 low_vol_premium_monthly_rebal_test.py 慣例）
    px.loc[px["ret_t"].abs() >= 0.5, "ret_t"] = np.nan
    px["vol20"] = g["ret_t"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).std())
    px["adv20"] = g["amount"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    px["volmed20"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).median())

    ex = ex.drop_duplicates(["stock_id", "trade_date"], keep="last")
    px = px.merge(ex, on=["stock_id", "trade_date"], how="left")
    px["is_ex"] = px["ref_price"].notna()
    px["base"] = px["ref_price"].where(px["is_ex"], px["prev_close"])
    px["gap_t"] = px["open"] / px["base"] - 1.0          # 訊號日 T 自己的跳空（控制變數用）

    px["locked_up"] = (px["close"] >= px["high"]) & (px["ret_t"] >= 0.095)
    px["locked_down"] = (px["close"] <= px["low"]) & (px["ret_t"] <= -0.095)
    px["locked"] = px["locked_up"] | px["locked_down"]

    # 次一交易日跳空（Y）：先算好每列自己的 gap_t，Y 是「下一列」的 gap_t，
    # 再用交易日曆驗證「下一列」真的是次一交易日（institutional-daily-dual-source-lookahead 第二坑）
    g2 = px.groupby("stock_id", group_keys=False)
    px["next_date_actual"] = g2["trade_date"].shift(-1)
    px["next_gap_bps"] = g2["gap_t"].shift(-1) * 1e4
    calendar = sorted(px["trade_date"].unique())
    next_date_map = {d: calendar[i + 1] for i, d in enumerate(calendar[:-1])}
    px["next_date_expected"] = px["trade_date"].map(next_date_map)
    bad_next = px["next_date_actual"].notna() & (px["next_date_actual"] != px["next_date_expected"])
    px.loc[bad_next, "next_gap_bps"] = np.nan
    n_bad = int(bad_next.sum())
    n_checked = int(px["next_date_actual"].notna().sum())
    print(f"[PIT check] 下一列非次一交易日而剔除：{n_bad}/{n_checked} "
          f"({n_bad / max(n_checked,1):.2%})")

    return px


def assign_liquidity_group(px: pd.DataFrame) -> pd.DataFrame:
    eligible = px["close"].ge(MIN_CLOSE) & px["adv20"].notna() & px["volmed20"].gt(0)
    px = px.copy()
    px["liq_rank"] = np.nan
    px.loc[eligible, "liq_rank"] = (
        px.loc[eligible].groupby("trade_date")["adv20"].rank(ascending=False, method="first")
    )
    px["group_label"] = None
    for label, lo, hi in GROUP_DEFS:
        m = px["liq_rank"].between(lo, hi)
        px.loc[m, "group_label"] = label
    return px


def load_inst_panel(start: str = RAW_START) -> pd.DataFrame:
    conn = connect_ro()
    inst = pd.read_sql_query(
        """SELECT stock_id, trade_date, foreign_net, investment_trust_net,
                  dealer_self_net, three_institution_net
             FROM stock_institutional_daily
            WHERE source='twse_t86' AND trade_date>=?""",
        conn, params=(start,),
    )
    conn.close()
    inst = inst[inst.stock_id.str.fullmatch(STOCK_RE)].copy()
    dup = inst.duplicated(["stock_id", "trade_date"]).sum()
    assert dup == 0, f"stock_institutional_daily(twse_t86) 出現重複 stock-day：{dup} 筆"
    return inst


def load_margin_panel(start: str = MARGIN_START, end: str = MARGIN_END) -> pd.DataFrame:
    conn = connect_ro()
    m = pd.read_sql_query(
        """SELECT stock_id, trade_date, margin_change
             FROM stock_margin_daily
            WHERE source='twse_mi_margn' AND trade_date BETWEEN ? AND ?""",
        conn, params=(start, end),
    )
    conn.close()
    m = m[m.stock_id.str.fullmatch(STOCK_RE)].copy()
    dup = m.duplicated(["stock_id", "trade_date"]).sum()
    assert dup == 0, f"stock_margin_daily(twse_mi_margn) 出現重複 stock-day：{dup} 筆"
    return m


def build_panel() -> pd.DataFrame:
    px = load_price_panel()
    px = assign_liquidity_group(px)
    inst = load_inst_panel()
    df = px.merge(inst, on=["stock_id", "trade_date"], how="inner")
    df = df[df["trade_date"] >= STUDY_START].copy()
    df = df[df["group_label"].notna()].copy()
    df = df[df["volmed20"] > 0].copy()
    df["x_raw"] = df["three_institution_net"] / df["volmed20"]
    df["grp_key"] = df["trade_date"] + "|" + df["group_label"]
    full_stats, tradable = cross_sectional_then_filter(
        df, date_col="grp_key", value_cols=["x_raw"], lock_col="locked", methods=("rank",)
    )
    return tradable


# --------------------------------------------------------------------------
# 迴歸 / 統計小工具
# --------------------------------------------------------------------------

def nw_stats(s: pd.Series, lag: int = NW_LAG) -> dict:
    a = np.asarray(pd.Series(s).dropna(), float)
    n = len(a)
    if n < 20:
        return dict(mean=np.nan, se=np.nan, t=np.nan, n=n, mde=np.nan)
    e = a - a.mean()
    v = (e @ e) / n
    for lag_i in range(1, lag + 1):
        v += 2 * (1 - lag_i / (lag + 1)) * ((e[lag_i:] @ e[:-lag_i]) / n)
    se = np.sqrt(v / n) if v > 0 else np.nan
    t = a.mean() / se if se and se > 0 else np.nan
    mde = T_CRIT * se if se == se else np.nan
    return dict(mean=a.mean(), se=se, t=t, n=n, mde=mde)


def daily_group_ols(data: pd.DataFrame, y_col: str, x_cols: list[str]) -> pd.DataFrame:
    rows = []
    for date, sub in data.groupby("trade_date"):
        sub = sub.dropna(subset=[y_col] + x_cols)
        if len(sub) < MIN_GROUP_N:
            continue
        X = np.column_stack([np.ones(len(sub))] + [sub[c].to_numpy() for c in x_cols])
        Y = sub[y_col].to_numpy()
        try:
            beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
        except np.linalg.LinAlgError:
            continue
        row = {"trade_date": date, "n": len(sub), "intercept": beta[0]}
        row.update({c: beta[i + 1] for i, c in enumerate(x_cols)})
        rows.append(row)
    return pd.DataFrame(rows)


def decile_bucket_series(data: pd.DataFrame, x_col: str, y_col: str, q: float = 0.9) -> pd.DataFrame:
    rows = []
    for date, sub in data.groupby("trade_date"):
        sub = sub.dropna(subset=[y_col, x_col])
        if len(sub) < MIN_GROUP_N:
            continue
        d10 = sub[sub[x_col] >= q]
        rows.append({
            "trade_date": date,
            "n": len(sub),
            "n_d10": len(d10),
            "d10_mean": d10[y_col].mean() if len(d10) else np.nan,
            "all_mean": sub[y_col].mean(),
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out["increment"] = out["d10_mean"] - out["all_mean"]
    return out


# --------------------------------------------------------------------------
# 報告
# --------------------------------------------------------------------------

def report_group(label: str, sub: pd.DataFrame) -> None:
    n_days = sub["trade_date"].nunique()
    n_rows = len(sub)
    avg_n_per_day = n_rows / max(n_days, 1)
    print(f"\n=== {label} ===  日數={n_days}  stock-day={n_rows}  平均每日檔數={avg_n_per_day:.1f}")

    # MDE 先算（用回歸係數序列的 NW SE，t=2 臨界值）
    reg = daily_group_ols(sub, "next_gap_bps", ["x_raw_rank", "ret_t", "gap_t", "vol20"])
    if reg.empty:
        print("  [資料不足] 每日合格檔數 < 30，無法跑橫斷面迴歸")
        return
    coef_stats = nw_stats(reg["x_raw_rank"])
    print(f"  [MDE] 迴歸係數序列 {len(reg)} 天 NW({NW_LAG}) SE={coef_stats['se']:.2f}bps → "
          f"t=2 门槛可偵測最小效應 = {coef_stats['mde']:.2f}bps")
    print(f"  [事實] 控制後迴歸係數(x_raw_rank, 0→1 全距) mean={coef_stats['mean']:.2f}bps "
          f"t={coef_stats['t']:.2f} (n_days={coef_stats['n']})")

    dec = decile_bucket_series(sub, "x_raw_rank", "next_gap_bps", q=0.9)
    if dec.empty:
        print("  [資料不足] 分位樣本每日 < 30 檔，無法算 D10")
        return
    d10 = nw_stats(dec["d10_mean"])
    base = nw_stats(dec["all_mean"])
    inc = nw_stats(dec["increment"])
    print(f"  [事實] D10(top decile x_raw_rank) 次日跳空 gross mean={d10['mean']:.2f}bps "
          f"t={d10['t']:.2f} (MDE={d10['mde']:.2f}bps)")
    print(f"  [事實] 該組無條件基準（全部合格股，不分訊號）次日跳空 mean={base['mean']:.2f}bps "
          f"t={base['t']:.2f}")
    print(f"  [事實] 增量(D10−基準，逐日配對後 NW) mean={inc['mean']:.2f}bps t={inc['t']:.2f} "
          f"(MDE={inc['mde']:.2f}bps)")
    net = d10["mean"] - COST_RT_BPS
    print(f"  [推論|假設=各組成本率相同=47.1bps 來回，未計價差] "
          f"D10 gross − 成本 = {d10['mean']:.2f} − {COST_RT_BPS} = {net:.2f}bps"
          f"{'（可能過成本）' if net > 0 else '（仍不可交易）'}")


def run_size_stratified(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("Step 4：按規模/流動性分層 — 三大法人淨買超 → 隔日跳空")
    print("=" * 70)
    for label, _, _ in GROUP_DEFS:
        sub = df[df["group_label"] == label]
        report_group(label, sub)


def run_retail_interaction(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("Step 5：散戶反向流交互項（僅 2026-06~08 全市場融資窗，樣本天數先天受限）")
    print("=" * 70)
    margin = load_margin_panel()
    if margin.empty:
        print("  [資料不足] twse_mi_margn 窗內無資料")
        return

    window = df[(df["trade_date"] >= MARGIN_START) & (df["trade_date"] <= MARGIN_END)].copy()
    merged = window.merge(margin, on=["stock_id", "trade_date"], how="inner")
    merged["grp_key"] = merged["trade_date"] + "|" + merged["group_label"]
    full_stats, _ = cross_sectional_then_filter(
        merged, date_col="grp_key", value_cols=["margin_change"], lock_col=None, methods=("rank",)
    )
    merged = full_stats
    merged["retail_buy"] = merged["margin_change_rank"] > 0.5
    merged["inst_buy"] = merged["x_raw_rank"] > 0.5
    merged["retail_oppose"] = (merged["retail_buy"] != merged["inst_buy"]).astype(float)
    merged["x_rank_c"] = merged["x_raw_rank"] - 0.5
    merged["inter"] = merged["x_rank_c"] * merged["retail_oppose"]

    for label, _, _ in GROUP_DEFS[-2:]:  # 只看 G2 尾段代表性不足，主看 G2/G3
        sub = merged[merged["group_label"] == label]
        n_days = sub["trade_date"].nunique()
        n_rows = len(sub)
        print(f"\n--- {label}（散戶交互項，短窗）--- 日數={n_days} stock-day={n_rows}")
        if n_days < 10:
            print("  [資料不足] 窗內天數過少，無法評估")
            continue
        reg = daily_group_ols(
            sub, "next_gap_bps", ["x_rank_c", "retail_oppose", "inter", "ret_t", "gap_t", "vol20"]
        )
        if reg.empty:
            print("  [資料不足] 每日合格檔數 < 30")
            continue
        base_stats = nw_stats(reg["x_rank_c"])
        inter_stats = nw_stats(reg["inter"])
        print(f"  [MDE] inter 係數序列 {len(reg)} 天 NW({NW_LAG}) SE={inter_stats['se']:.2f}bps "
              f"→ t=2 門檻可偵測最小效應 = {inter_stats['mde']:.2f}bps")
        print(f"  [事實] 基準斜率(法人訊號、非反向日) mean={base_stats['mean']:.2f}bps "
              f"t={base_stats['t']:.2f}")
        print(f"  [事實] 交互項(散戶反向時的額外斜率) mean={inter_stats['mean']:.2f}bps "
              f"t={inter_stats['t']:.2f} (n_days={inter_stats['n']})")
        if abs(inter_stats["t"]) < 2 and n_days < 60:
            print("  [推論|假設=真效應存在但短窗檢定力不足] 樣本僅 ~2.5 個月，"
                  "即使真有交互作用也大概率測不出來——不足以支撐結論，需等全市場融資資料回補更長歷史")


def main() -> None:
    print("載入資料中...")
    df = build_panel()
    print(f"合格 stock-day 總數：{len(df)}，日期範圍 {df.trade_date.min()}~{df.trade_date.max()}")
    run_size_stratified(df)
    run_retail_interaction(df)


if __name__ == "__main__":
    main()
