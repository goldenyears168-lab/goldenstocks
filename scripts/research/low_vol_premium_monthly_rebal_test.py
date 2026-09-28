#!/usr/bin/env python3
"""低波動溢酬——月頻重新平衡是否讓它變得可交易？（執行框架重測，非新因子）

**背景**：`chip-score-is-vol-gap-proxy` 用 45 萬 stock-day 面板（2023-01~2026-08）
找到低波動−高波動（開→收）+0.334%/日 t=+7.03，但那是**用當日 60 日波動排序、
每日全部重排**的框架，換手 ~100%/日、成本 1.17%/日，遠大於毛利，判定不可交易。
同一份分析也記錄了一個關鍵警訊：**收→收 是 −0.093%（t=−1.62）**——效應是純粹的
日內現象，不是持有期報酬。

Frazzini & Pedersen (2014) BAB／Baker-Bradley-Wurgler (2011) 的原始設計是**低頻、
長期持有**（月/季重新平衡）＋用**總報酬（收→收）波動**排序，不是日內波動、
也不是每日重排。本檔照文獻設計重測：

  - 用 20 日已實現波動（收→收報酬滾動 std，非單日波動，不含當日）排序
  - 5 分位，Q1=最低波動、Q5=最高波動
  - **每 20 個交易日重新平衡一次**（近似月頻），期間內不動倉
  - 多：Q1 vs 全市場等權基準；空：Q1−Q5（若做多空，量化空頭腿額外借券成本）

**這是誠實的複驗，不是為了讓故事成立而調參**：如果月頻收→收框架下這個效應
本身就弱或不存在（因為原始效應被證實只活在日內），淨值轉正的機率很低——
換手率下降只解決「成本殺死一切」，解決不了「效應本來就不在這個口徑裡」。

PIT：決策日 D 用 D（含）以前資料算 20 日波動；D+1 開盤才進場（無法搶跑）；
鎖死排除用 `biglot_cross_sectional_lib.cross_sectional_then_filter`——排序基準
永遠用完整合格宇宙（流動性＋價格門檻，決策日當下已知），鎖死只在「能不能在
進場日成交」這一步剔除，不回頭汙染排序本身。

用法::

    PYTHONPATH=src .venv/bin/python scripts/research/low_vol_premium_monthly_rebal_test.py
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

START = "2015-01-01"
MIN_CLOSE = 10.0
MIN_AMT20 = 3.0e7          # 20 日中位成交金額門檻（沿用 fullmarket_overnight_winrate.py 慣例）
VOL_WINDOW = 20
REBAL_EVERY = 20           # 交易日 ≈ 1 個月
MIN_UNIVERSE = 150         # 分位有意義所需的最小合格檔數
LONG_LEG_COST = 0.00471    # 多頭腿來回（6 折手續費 2×0.0855% + 證交稅 0.3%，專案慣例）
SHORT_LEG_COST = 0.0070    # 空頭腿來回（多空合計 ≈1.17% 減掉多頭腿 0.47%，同慣例）
T_CRIT = 2.0               # MDE 用的臨界 t（雙尾 ~95%）


def load_panel(start: str = START) -> pd.DataFrame:
    conn = connect_ro()
    d = pd.read_sql_query(
        """SELECT stock_id, trade_date, open, high, low, close, volume
             FROM stock_daily_bars
            WHERE source='finmind' AND trade_date>=? AND close>0 AND volume>0""",
        conn, params=(start,),
    )
    conn.close()
    d = d[d.stock_id.str.fullmatch(r"[1-9]\d{3}")].copy()
    d = d.sort_values(["stock_id", "trade_date"]).reset_index(drop=True)
    g = d.groupby("stock_id", group_keys=False)
    d["prev_close"] = g.close.shift(1)
    d["ret_cc"] = d.close / d.prev_close - 1
    # 未還原分割等資料錯誤：單日 |報酬| >= 50% 直接判為異常，剔除該筆報酬（保留價格列）
    d.loc[d.ret_cc.abs() >= 0.5, "ret_cc"] = np.nan
    d["dollar_vol"] = d.close * d.volume
    d["amt20"] = g.dollar_vol.transform(lambda s: s.rolling(20, min_periods=15).median())
    d["vol20"] = g.ret_cc.transform(lambda s: s.rolling(VOL_WINDOW, min_periods=15).std())
    d["r_day"] = d.ret_cc
    d["locked_up"] = (d.close >= d.high) & (d.r_day >= 0.095)
    d["locked_down"] = (d.close <= d.low) & (d.r_day <= -0.095)
    d["locked"] = d.locked_up | d.locked_down
    return d


def build_day_return_lookup(d: pd.DataFrame) -> dict[tuple[str, str], tuple[float, float]]:
    """(stock_id, trade_date) -> (open, close)，供逐日報酬路徑組裝。"""
    return {(r.stock_id, r.trade_date): (r.open, r.close) for r in d.itertuples()}


def period_daily_returns(
    basket: list[str],
    dates: list[str],
    px: dict[tuple[str, str], tuple[float, float]],
) -> pd.Series:
    """組裝一個持有期內，等權(進場時等權、期間不動倉)組合的逐日報酬。

    進場日＝open→close；之後每日＝close→close(前一日)。
    """
    if not basket or len(dates) < 2:
        return pd.Series(dtype=float)
    rows = []
    for i, dt in enumerate(dates):
        rets = []
        for sid in basket:
            if i == 0:
                bar = px.get((sid, dt))
                prev = None
            else:
                bar = px.get((sid, dt))
                prev = px.get((sid, dates[i - 1]))
            if bar is None or bar[0] is None or bar[1] is None:
                continue
            if i == 0:
                if bar[0] in (0, None):
                    continue
                rets.append(bar[1] / bar[0] - 1)
            else:
                if prev is None or prev[1] in (0, None):
                    continue
                rets.append(bar[1] / prev[1] - 1)
        rows.append(np.mean(rets) if rets else np.nan)
    return pd.Series(rows, index=dates)


def run() -> pd.DataFrame:
    d = load_panel()
    print(f"原始面板 {len(d):,} stock-day · {d.stock_id.nunique():,} 檔 · "
          f"{d.trade_date.nunique():,} 日 · {d.trade_date.min()}~{d.trade_date.max()}")

    calendar = sorted(d.trade_date.unique())
    px = build_day_return_lookup(d)
    by_date = {dt: g for dt, g in d.groupby("trade_date")}

    decision_idxs = list(range(25, len(calendar) - 1, REBAL_EVERY))
    print(f"重新平衡次數 = {len(decision_idxs)}（每 {REBAL_EVERY} 個交易日一次 · "
          f"對照組每日重排 ≈ {len(calendar)} 次）\n")

    records = []
    prev_q1: set[str] = set()
    prev_q5: set[str] = set()
    universe_daily_ret = d.groupby("trade_date").ret_cc.mean()

    for k, idx in enumerate(decision_idxs):
        decision_date = calendar[idx]
        entry_idx = idx + 1
        exit_idx = min(idx + REBAL_EVERY, len(calendar) - 1)
        if entry_idx >= exit_idx:
            continue
        entry_date = calendar[entry_idx]
        hold_dates = calendar[entry_idx: exit_idx + 1]

        cs = by_date.get(decision_date)
        if cs is None:
            continue
        cs = cs[(cs.close >= MIN_CLOSE) & (cs.amt20 >= MIN_AMT20) & cs.vol20.notna()].copy()
        if len(cs) < MIN_UNIVERSE:
            continue

        # 進場日是否鎖死（決策日已知合格宇宙 + 進場日才知道的鎖死狀態）——
        # 用 cross_sectional_then_filter：排序基準用決策日「完整合格宇宙」算，
        # 鎖死（entry_locked）只在最後「能不能買到」這一步剔除，不回頭動排序。
        entry_cs = by_date.get(entry_date)
        entry_locked_map = (
            dict(zip(entry_cs.stock_id, entry_cs.locked)) if entry_cs is not None else {}
        )
        cs["entry_locked"] = cs.stock_id.map(entry_locked_map).fillna(True)  # 查無進場日資料＝視同不可交易

        full, tradable = cross_sectional_then_filter(
            cs, date_col="trade_date", value_cols=["vol20"], lock_col="entry_locked",
            methods=("rank",),
        )
        bucket = np.minimum((tradable.vol20_rank * 5).astype(int), 4)
        tradable = tradable.assign(bucket=bucket)

        q1 = set(tradable.loc[tradable.bucket == 0, "stock_id"])
        q5 = set(tradable.loc[tradable.bucket == 4, "stock_id"])
        if not q1 or not q5:
            continue

        turnover_q1 = len(q1 - prev_q1) / len(q1) if k > 0 else 1.0
        turnover_q5 = len(q5 - prev_q5) / len(q5) if k > 0 else 1.0

        r_q1 = period_daily_returns(sorted(q1), hold_dates, px)
        r_q5 = period_daily_returns(sorted(q5), hold_dates, px)
        r_uni = universe_daily_ret.reindex(hold_dates)

        if r_q1.empty or r_q5.empty:
            continue

        gross_q1 = (1 + r_q1.fillna(0)).prod() - 1
        gross_q5 = (1 + r_q5.fillna(0)).prod() - 1
        gross_uni = (1 + r_uni.fillna(0)).prod() - 1

        cost_q1 = turnover_q1 * LONG_LEG_COST
        cost_q5 = turnover_q5 * SHORT_LEG_COST
        net_q1 = gross_q1 - cost_q1
        net_q1_minus_q5_gross = gross_q1 - gross_q5
        net_q1_minus_q5_net = (gross_q1 - cost_q1) - (gross_q5 - cost_q5)

        records.append(dict(
            k=k, decision_date=decision_date, entry_date=entry_date, exit_date=hold_dates[-1],
            n_days=len(hold_dates), n_q1=len(q1), n_q5=len(q5),
            turnover_q1=turnover_q1, turnover_q5=turnover_q5,
            gross_q1=gross_q1, gross_q5=gross_q5, gross_uni=gross_uni,
            net_q1=net_q1,
            gross_q1_minus_uni=gross_q1 - gross_uni,
            net_q1_minus_uni=net_q1 - gross_uni,
            gross_spread=net_q1_minus_q5_gross, net_spread=net_q1_minus_q5_net,
        ))
        prev_q1, prev_q5 = q1, q5

    out = pd.DataFrame.from_records(records)
    return out


def tstat(s: pd.Series) -> float:
    s = s.dropna()
    if len(s) < 3:
        return float("nan")
    return s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))


def mde(s: pd.Series, t_crit: float = T_CRIT) -> float:
    s = s.dropna()
    if len(s) < 3:
        return float("nan")
    return t_crit * s.std(ddof=1) / np.sqrt(len(s))


def annualize_period(mean_period: float, n_days_per_period: float) -> float:
    if pd.isna(mean_period) or n_days_per_period <= 0:
        return float("nan")
    periods_per_year = 252.0 / n_days_per_period
    return (1 + mean_period) ** periods_per_year - 1


def report(out: pd.DataFrame) -> None:
    if out.empty:
        print("無有效重新平衡期，檢查資料/門檻。")
        return
    n = len(out)
    avg_days = out.n_days.mean()
    print(f"有效重新平衡期數 n={n}（每期約 {avg_days:.1f} 個交易日 · "
          f"合計涵蓋 {out.entry_date.min()}~{out.exit_date.max()}）\n")

    print("=== 換手率（本檔月頻框架 vs 原日頻框架 100%/日）===")
    print(f"  Q1（低波動組）每期換手 {out.turnover_q1.mean()*100:.1f}%"
          f"（中位數 {out.turnover_q1.median()*100:.1f}%）")
    print(f"  Q5（高波動組）每期換手 {out.turnover_q5.mean()*100:.1f}%"
          f"（中位數 {out.turnover_q5.median()*100:.1f}%）")
    ann_days = 252 / avg_days
    print(f"  換算年化換手次數 ≈ {ann_days:.1f} 次/年（原日頻框架 ≈252 次/年）\n")

    print("=== A. 純多：Q1（低波動）vs 全市場等權基準（收→收，月頻持有）===")
    for label, col in (("gross", "gross_q1_minus_uni"), ("net(扣多頭腿成本)", "net_q1_minus_uni")):
        s = out[col]
        t = tstat(s)
        ann = annualize_period(s.mean(), avg_days)
        m = mde(s)
        print(f"  {label:<18} 每期均 {s.mean()*100:+.3f}%  t={t:+.2f}  "
              f"年化≈{ann*100:+.2f}%  MDE(每期,t={T_CRIT:.1f})={m*100:.3f}%"
              f"  {'>MDE通過' if abs(s.mean())>m else '<=MDE 未過'}")

    print("\n=== B. 多空：Q1−Q5（BAB 式，含空頭腿借券成本）===")
    for label, col in (("gross", "gross_spread"), ("net(扣多空兩腳成本)", "net_spread")):
        s = out[col]
        t = tstat(s)
        ann = annualize_period(s.mean(), avg_days)
        m = mde(s)
        print(f"  {label:<18} 每期均 {s.mean()*100:+.3f}%  t={t:+.2f}  "
              f"年化≈{ann*100:+.2f}%  MDE(每期,t={T_CRIT:.1f})={m*100:.3f}%"
              f"  {'>MDE通過' if abs(s.mean())>m else '<=MDE 未過'}")

    print("\n=== 成本改善幅度（vs 原每日重排框架）===")
    old_daily_cost = 0.0117  # 原框架多空合計來回估計，chip_score_turnover_variants.py 慣例
    old_daily_turnover = 1.0
    new_cost_per_period = out.turnover_q1.mean() * LONG_LEG_COST + out.turnover_q5.mean() * SHORT_LEG_COST
    new_daily_equiv_cost = new_cost_per_period / avg_days
    print(f"  原框架：換手 ~100%/日 · 成本 ~{old_daily_cost*100:.2f}%/日"
          f"（年化 ≈ {old_daily_cost*252*100:.0f}%）")
    print(f"  本框架：換手 Q1 {out.turnover_q1.mean()*100:.1f}%/期、Q5 {out.turnover_q5.mean()*100:.1f}%/期"
          f" · 成本 {new_cost_per_period*100:.4f}%/期 ≈ {new_daily_equiv_cost*100:.4f}%/日等效"
          f"（年化 ≈ {new_cost_per_period*ann_days*100:.2f}%）")
    print(f"  成本降幅 ≈ {(1 - new_daily_equiv_cost/old_daily_cost)*100:.1f}%")

    print("\n=== 誠實檢查：效應是否隨頻率衰減/反轉？===")
    print("  （對照 chip-score-is-vol-gap-proxy：同一份日頻面板裡，開→收 +0.334%/日 t=+7.03，"
          "但收→收僅 −0.093% t=−1.62——效應被證實只活在日內）")
    print(f"  本檔（月頻・全程收→收）Q1−Q5 gross 每期 {out.gross_spread.mean()*100:+.3f}% "
          f"t={tstat(out.gross_spread):+.2f} —— 若接近 0 或變號，"
          f"代表換頻率沒有『撿到同一個效應』，而是換了一個更弱/不同的效應在測。")

    print("\n[事實] 以上 gross/net/turnover 數字可由本腳本重算。")
    print("[推論]（假設：本研究未對進場滑價、拆單衝擊成本額外建模，只計手續費＋證交稅＋"
          "空頭腿估計借券費；台股實際放空另受平盤下不得放空限制、當沖券源限制未模擬——"
          "若 B 段有效，實盤可執行性應再打折）")


def main() -> int:
    out = run()
    out_path = ROOT / "reports" / "research" / "low-vol-premium-monthly" / "monthly_rebal_periods.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"逐期明細 → {out_path}\n")
    report(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
