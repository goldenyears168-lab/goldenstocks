"""中實戶(中單)隔夜/5日訊號按波動 regime 分層檢定(Barclay & Warner 1993 stealth trading)。

背景
----
`biglot-mid-bucket-stealth-signal.md`(2026-09-22)在**未分波動regime**的 127 日全樣本上
測出:中實淨流控大戶後,隔夜 +9.8bps/1z(t3.97,剔鎖死),5日 +29.5bps/1z(t2.83,剛過MDE)、
且唯一在 5 日存活的桶(大戶淨 5 日歸零)。stealth trading hypothesis(Barclay & Warner 1993、
Chakravarty 2001)預測這個「中單優於大單」的模式應該是**波動 regime 依存**的:低波動期知情
者用中單分批隱藏;高波動期波動本身提供偽裝,知情者改用大單——即中單訊號應該在低波動期
更強、高波動期減弱。本檔把同一份 127 日樣本按波動 regime 分高/低兩組重跑同一條回歸,檢查
這個依存關係是否存在,並誠實報告分層後的檢定力是否足夠。

資料與方法(逐項比照原始程式碼,不自創新方法)
----------------------------------------
- 原始分析程式碼從未存檔,已從 session transcript 逐字核回(見下方"原始程式碼出處")。
- 面板:`~/goldenstocks-data/scratch/biglot_panels/pit100_daily_panel_2026-03_08.csv`
  (100 檔 × 127 個交易日,2026-03-02~2026-09-01)+ `pit100_bucket5_dual_2026-03_08.csv`
  (取 `big1000` 還原 1000萬口徑:`mid1000 = mid_net + (big500 - big1000)`,`big500 = big_buy-big_sell`)。
- 鎖死定義:當日收盤對前一有資料交易日收盤 `|chg| >= 9.5%`。隔夜報酬 `|ov| > 1500bps` 視為
  跳空異常值排除(與原始腳本一致)。
- **本檔與原始 `/tmp/mid_robust.py` 的唯一方法論差異**:原始腳本對「②剔除鎖死」那組是先
  `pool=[r for r in recs if not r["locked"]]`,再對這個已過濾的 pool 逐日算 z-score——這正是
  `biglot-cross-sectional-lookahead-bug-and-fix.md` 稽核抓到的「先剔鎖死股才算橫斷面基準」
  反樣式(用「今天誰鎖死了」這個收盤才確定的資訊去污染其他股票當天的常態化基準)。本檔改用
  `biglot_cross_sectional_lib.cross_sectional_then_filter`:z-score 一律先對**當天完整** 100
  檔母體算(不論鎖不鎖死),鎖死股只在算完 z 之後、切出 `tradable` 子集那一步才剔除。因為標
  準化樣本(全宇宙)≠迴歸樣本(`tradable`,已剔鎖死),迴歸**一律**帶截距項(同一份稽核筆記
  的第二條教訓)。這個方法差異預期會讓數字跟舊筆記的 9.8/29.5 有些微出入,屬預期之內,不是
  新 bug。
- 回歸設計:逐日橫斷面 OLS `target ~ 1 + mid_z + big_z`(Fama-MacBeth),取每日 `mid_z` 係數,
  對日期做 t 檢定;`t = mean/(std/sqrt(n_days))`,`MDE = 2.8 * std/sqrt(n_days)`
  (`2.8 ≈ z_0.975 + z_0.80`,兩篇 stealth 相關研究筆記沿用的既有慣例,見 `mid_test.py`)。
- 波動 regime 兩種定義(依交辦要求都做):
  1. **VIXTWN 日頻中位數切**(主要):對面板涵蓋的 127 個交易日,用 `market_vix_daily`
     (`symbol='VIXTWN'`,優先取 `source='finmind'`,缺值退回 `computed`)的收盤值做中位數
     切分成高/低兩組"日期",各約 63~64 天;另加前後 1/3 切分(丟中間 1/3)作為切法穩健性檢查。
     這種切法是**按日期**分組,樣本量的天數維度會被砍半,是本檔最主要的檢定力風險。
  2. **個股自身 20 日已實現波動率橫斷面分位數**(穩健性檢查):對每檔股票用其自身日對數報酬
     計算「不含當天」的過去 20 個交易日已實現波動(`shift(1).rolling(20).std()`,需要 20 筆
     歷史,面板前段會損失部分早期觀測),再對**每一天**橫斷面分成高/低兩半。這種切法是按
     "股票-日"切,不犧牲天數維度(127 天都在),檢定力理論上優於 VIXTWN 切法,可互相對照。
- MDE 先行原則:程式輸出**一律先印全樣本(未分regime)基準的 SE**,再印分層後的 SE/MDE 供
  對照,任何一格都同時印 MDE,不因為顯著才印、不顯著就不印。

原始程式碼出處(逐字核回,未經轉述)
--------------------------------
`~/.claude/projects/-Users-jackm4-goldenstocks/c1eb2de7-71ee-517d-870a-128c1b9bc0db.jsonl`
第 11800 行 heredoc(`/tmp/mid_robust.py`,產出 `biglot-mid-bucket-stealth-signal.md` 引用
的 9.8/29.5 數字)。

用法
----
    PYTHONPATH=src .venv/bin/python scripts/research/biglot_stealth_vol_regime_test.py

只做唯讀查詢(SQLite `mode=ro`),不寫入任何資料庫,不下單。
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DEFAULT_DB_PATH  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from biglot_cross_sectional_lib import cross_sectional_then_filter  # noqa: E402

PANEL_DIR = Path(DEFAULT_DB_PATH).resolve().parents[1] / "scratch" / "biglot_panels"
DAILY_PANEL = PANEL_DIR / "pit100_daily_panel_2026-03_08.csv"
BUCKET5_DUAL = PANEL_DIR / "pit100_bucket5_dual_2026-03_08.csv"

MDE_MULT = 2.8  # z_0.975 + z_0.80 ≈ 1.96 + 0.84；沿用 mid_test.py / mid_robust.py 既有慣例
LOCK_THRESHOLD = 0.095
OV_ABS_CAP_BPS = 1500.0
RV_WINDOW = 20


def load_panel() -> pd.DataFrame:
    day_df = pd.read_csv(DAILY_PANEL)
    bucket = pd.read_csv(BUCKET5_DUAL, usecols=["sid", "date", "big1000"])
    big1000 = bucket.groupby(["sid", "date"], as_index=False)["big1000"].sum()
    df = day_df.merge(big1000, on=["sid", "date"], how="inner")
    df = df[df["tot_amt"] > 0].copy()

    df["b500"] = df["big_buy"] - df["big_sell"]
    df["big"] = df["big1000"] / df["tot_amt"]
    df["mid"] = (df["mid_net"] + (df["b500"] - df["big1000"])) / df["tot_amt"]

    df = df.sort_values(["sid", "date"]).reset_index(drop=True)
    g = df.groupby("sid")
    df["prev_close"] = g["close_px"].shift(1)
    df["next_open"] = g["open_px"].shift(-1)
    df["fwd_close_5"] = g["close_px"].shift(-5)
    df["log_ret"] = np.log(df["close_px"]) - np.log(df["prev_close"])

    df["chg"] = df["close_px"] / df["prev_close"] - 1.0
    df["locked"] = df["chg"].abs() >= LOCK_THRESHOLD

    df["ov"] = (df["next_open"] / df["close_px"] - 1.0) * 1e4
    df["d5"] = (df["fwd_close_5"] / df["close_px"] - 1.0) * 1e4

    df = df.dropna(subset=["prev_close", "next_open"]).copy()
    df = df[(df["open_px"] > 0) & (df["close_px"] > 0)]
    df = df[df["ov"].abs() <= OV_ABS_CAP_BPS].copy()
    df["locked"] = df["locked"].fillna(False)
    return df


def add_stock_vol_regime(df: pd.DataFrame) -> pd.DataFrame:
    """個股自身 20 日已實現波動(不含當天)，每日橫斷面中位數切高/低。"""
    df = df.sort_values(["sid", "date"]).copy()

    def _rv(s: pd.Series) -> pd.Series:
        return s.shift(1).rolling(RV_WINDOW, min_periods=RV_WINDOW).std()

    df["rv20"] = df.groupby("sid")["log_ret"].transform(_rv)
    df["rv20_rank"] = df.groupby("date")["rv20"].rank(pct=True)
    regime = pd.Series(np.where(df["rv20_rank"] >= 0.5, "high", "low"), index=df.index)
    df["stockvol_regime"] = regime.where(df["rv20_rank"].notna(), other=None)
    return df


def load_vixtwn(start: str, end: str) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        raw = pd.read_sql_query(
            "SELECT date, close, source FROM market_vix_daily "
            "WHERE symbol='VIXTWN' AND date BETWEEN ? AND ? "
            "ORDER BY date, CASE source WHEN 'finmind' THEN 0 ELSE 1 END",
            con, params=(start, end),
        )
    finally:
        con.close()
    raw = raw.drop_duplicates(subset="date", keep="first")
    return raw[["date", "close"]].rename(columns={"close": "vixtwn_close"})


def add_vixtwn_regime(df: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    med = vix["vixtwn_close"].median()
    q1, q2 = vix["vixtwn_close"].quantile([1 / 3, 2 / 3])
    vix = vix.copy()
    vix["vix_median_regime"] = np.where(vix["vixtwn_close"] >= med, "high", "low")
    vix["vix_tercile_regime"] = np.select(
        [vix["vixtwn_close"] <= q1, vix["vixtwn_close"] >= q2],
        ["low", "high"], default="mid",
    )
    out = df.merge(vix, on="date", how="left")
    return out, med, (float(q1), float(q2))


def fm_regression(df: pd.DataFrame, target: str, min_names: int) -> dict:
    """逐日橫斷面 OLS: target ~ 1 + mid_z + big_z, Fama-MacBeth t 檢定。"""
    coefs = []
    for _, g in df.groupby("date"):
        gg = g.dropna(subset=[target, "mid_z", "big_z"])
        if len(gg) < min_names:
            continue
        X = np.column_stack([np.ones(len(gg)), gg["mid_z"].values, gg["big_z"].values])
        Y = gg[target].values
        beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
        coefs.append(beta[1])
    coefs = np.array(coefs, dtype=float)
    n = len(coefs)
    if n < 3:
        return {"n_days": n, "mean": float("nan"), "t": float("nan"),
                "se": float("nan"), "mde": float("nan")}
    se = coefs.std(ddof=1) / math.sqrt(n)
    mean = float(coefs.mean())
    return {"n_days": n, "mean": mean, "t": mean / se if se > 0 else float("nan"),
            "se": se, "mde": MDE_MULT * se}


def fmt(cell: dict) -> str:
    if cell["n_days"] < 3:
        return f"n_days={cell['n_days']} (不足以估計)"
    return f"{cell['mean']:+6.1f}bps t={cell['t']:+5.2f} (n_days={cell['n_days']}, MDE={cell['mde']:4.1f})"


def main() -> None:
    panel = load_panel()
    panel = add_stock_vol_regime(panel)

    dmin, dmax = panel["date"].min(), panel["date"].max()
    vix = load_vixtwn(dmin, dmax)
    n_dates = panel["date"].nunique()
    print(f"面板日期範圍 {dmin} ~ {dmax}，共 {n_dates} 個交易日 · VIXTWN 取到 {len(vix)} 天"
          f"（缺 {n_dates - len(vix)} 天）")

    panel, vix_med, vix_terciles = add_vixtwn_regime(panel, vix)
    print(f"VIXTWN 中位數 = {vix_med:.2f}；前/後 1/3 分位 = {vix_terciles[0]:.2f} / {vix_terciles[1]:.2f}\n")

    # 橫斷面正規化：一律用「當天完整母體」(cross_sectional_then_filter)，鎖死股只在
    # 算完 z-score 之後才被排除到 tradable。
    _, tradable = cross_sectional_then_filter(
        panel, date_col="date", value_cols=["big", "mid"], lock_col="locked",
        methods=("zscore",),
    )
    tradable = tradable.rename(columns={"big_z": "big_z", "mid_z": "mid_z"})

    targets = ["ov", "d5"]

    print("=" * 78)
    print("步驟 1／MDE 先行：全樣本（未分 regime）基準，作為分層後 SE 惡化幅度的參照")
    print("=" * 78)
    baseline = {}
    for t in targets:
        cell = fm_regression(tradable, t, min_names=20)
        baseline[t] = cell
        label = "隔夜(ov)" if t == "ov" else "5日(d5)"
        print(f"  [事實] 全樣本 中實淨|控大戶 → {label}: {fmt(cell)}")
        if cell["n_days"] >= 3:
            proj_mde_half = MDE_MULT * cell["se"] * math.sqrt(2)
            print(f"    [推論](假設：分兩組後每組 SE 約放大 sqrt(2)≈1.41 倍，"
                  f"若母體效應大小不變) 半樣本預期 MDE ≈ {proj_mde_half:4.1f}bps"
                  f"（是否小於半樣本實際估計，見下方步驟 2/3 對照）")
    print()

    print("=" * 78)
    print("步驟 2／VIXTWN 日頻分層（按「日期」分組，天數維度被砍半 → 主要檢定力風險）")
    print("=" * 78)
    for cut_col, cut_name in [("vix_median_regime", "中位數切"),
                               ("vix_tercile_regime", "前後1/3切(丟中間1/3)")]:
        print(f"-- {cut_name} --")
        for regime in ("low", "high"):
            sub = tradable[tradable[cut_col] == regime]
            n_days_regime = sub["date"].nunique()
            row = [f"    VIXTWN={regime:4s} (n_days={n_days_regime})"]
            for t in targets:
                cell = fm_regression(sub, t, min_names=20)
                label = "隔夜" if t == "ov" else "5日"
                row.append(f"{label}: {fmt(cell)}")
            print("  " + "  ｜  ".join(row))
        print()

    print("=" * 78)
    print("步驟 3／個股自身 20 日已實現波動分層（按「股票-日」分組，不犧牲天數維度）")
    print("=" * 78)
    n_missing_rv = tradable["stockvol_regime"].isna().sum()
    print(f"  [事實] {n_missing_rv}/{len(tradable)} 筆因前 {RV_WINDOW} 日歷史不足無法計算 rv20，已排除")
    for regime in ("low", "high"):
        sub = tradable[tradable["stockvol_regime"] == regime]
        n_days_regime = sub["date"].nunique()
        row = [f"    stockvol={regime:4s} (n_days={n_days_regime}, n_obs={len(sub)})"]
        for t in targets:
            cell = fm_regression(sub, t, min_names=15)
            label = "隔夜" if t == "ov" else "5日"
            row.append(f"{label}: {fmt(cell)}")
        print("  " + "  ｜  ".join(row))
    print()

    print("=" * 78)
    print("附註：VIXTWN regime 與個股自身波動 regime 的重疊率（同一天多數股票落在哪一半）")
    print("=" * 78)
    day_majority = (
        tradable.dropna(subset=["stockvol_regime"])
        .groupby("date")["stockvol_regime"]
        .apply(lambda s: (s == "high").mean())
    )
    day_majority = day_majority.to_frame("pct_high_stockvol").reset_index()
    merged = day_majority.merge(
        panel[["date", "vix_median_regime"]].drop_duplicates(), on="date", how="left"
    )
    corr = merged["pct_high_stockvol"].corr(
        (merged["vix_median_regime"] == "high").astype(float)
    )
    print(f"  [事實] 「當天多數股票落在高波動半邊」的比例，與 VIXTWN high/low 的點二列相關 = {corr:+.3f}")


if __name__ == "__main__":
    main()
