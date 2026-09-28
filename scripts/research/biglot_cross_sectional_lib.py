#!/usr/bin/env python3
"""biglot 橫斷面正規化共用函式庫——把「漲跌停鎖死排除必須晚於橫斷面基準計算」這條
不變量,從「靠人記得」升級成「靠函式簽章逼你做對」。

## 背景（2026-09-28 稽核事故）

`biglot-overnight-limitup-proxy-verdict.md` 最早記錄這個教訓：剔除漲跌停鎖死股票後，
若用「剩餘股票」重新計算橫斷面基準（demean/z-score/排名），等於篩選條件本身洩漏了
「今天誰鎖死了」這個要收盤才確定的資訊。正確做法：基準永遠用**完整、不受當天鎖死結果
影響的宇宙**去算，鎖死股只在最後「能不能交易/能不能算報酬」這一步才被剔除。

同一天稽核掃過 13 份相關研究筆記背後的實際程式碼，抓到兩個真的踩雷（
`biglot-diff-normalization-verdict.md`、`biglot-triple-window-accumulation-marker.md`）、
一個部分踩雷（`biglot-flow-is-foreign-inst-proxy.md` 的「盤中目標矩陣」子分析），另外三份
（`biglot-triple-window-accumulation-marker.md` 除外皆同一批）因為原始程式碼只在
`/tmp` 或對話 session 裡跑過一次就消失，連稽核都做不到。

`biglot_live_watch.py::early_report()`（2026-09-03 起的生產程式碼）從一開始就做對了：
`rows` 對全部股票建構、`av`/`gv`/`rv` 百分位基準對完整 `rows` 計算，`locked` 這個旗標
只在最後「排進前 8 名 vs 跳過」那一步才用到。本檔把這個既有的正確模式抽成共用函式，
避免下一份新研究又要重新發明一次、又要重新踩一次同一個坑。

## 使用方式

不要自己手動 `df[~df.locked]` 之後才算 rank/zscore/demean。改用：

    from biglot_cross_sectional_lib import cross_sectional_then_filter

    full_with_stats, tradable = cross_sectional_then_filter(
        df, date_col="date", value_cols=["big_share", "diff_pct"], lock_col="locked",
    )

`full_with_stats` 保留全部列（含鎖死股）+ 新增的 `{col}_rank` / `{col}_z` / `{col}_dm` 欄；
`tradable` 是排除 `lock_col=True` 之後的子集，統計欄數值與 `full_with_stats` 完全相同
（因為基準本來就是用同一批資料算的）——如果你的研究需要拿統計欄去跑報酬迴歸，一律用
`tradable`，不要用自己另外 filter 出來的 df 重算一次。

⚠️ **迴歸一定要帶截距項**（2026-09-28 `biglot-diff-normalization-verdict` 重跑時發現）：
一旦「算基準的樣本」（全宇宙）跟「拿去跑迴歸的樣本」（`tradable`，已排除鎖死股）不再是
同一批，原本「不帶截距項的迴歸」隱含依賴的「樣本均值≈0」這個前提就不成立了（`tradable`
排除鎖死股後，均值不再精確等於全宇宙算基準時用的那個均值）。不加截距項會讓係數被機械性
放大，容易誤讀成「修正後訊號變強了」。用 `tradable` 跑迴歸時**務必**加截距項，不要沿用
舊研究裡「通過原點迴歸」的寫法。

跑 `python biglot_cross_sectional_lib.py` 會執行自我驗證：構造一批合成資料，證明
「换一批股票被標成鎖死」不會動到其他股票的 rank/zscore/demean 數值——這就是本檔存在
的唯一理由，任何修改這支檔案的人都應該先確保這個自我驗證還會過。
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pandas as pd

_METHOD_SUFFIX = {"rank": "rank", "zscore": "z", "demean": "dm"}


def cross_sectional_then_filter(
    df: pd.DataFrame,
    date_col: str,
    value_cols: Sequence[str],
    lock_col: str | None = None,
    methods: Iterable[str] = ("rank", "zscore", "demean"),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """對 `value_cols` 依 `date_col` 分組計算橫斷面統計量,基準永遠是傳入的**全部**列。

    `lock_col`（若給定）只在計算完成"之後"用來切出 `tradable` 子集，絕不影響統計量本身。

    Args:
        df: 輸入資料，每列一個 stock-day（或 stock-bucket）。**呼叫前不要自己先過濾掉
            鎖死股**——這正是本函式要防止的錯誤用法；把完整 df（含鎖死股）傳進來。
        date_col: 用來分組的橫斷面欄位（通常是日期，但同樣適用於同一天內的其他分組鍵）。
        value_cols: 要計算橫斷面統計量的欄位清單。
        lock_col: 布林欄位名稱，`True` 表示「當下不可交易」（例如收盤鎖漲跌停）。
            傳 `None` 表示這份資料不需要做鎖死排除，`tradable` 會等於 `full_with_stats`。
        methods: 要計算哪些統計量，預設全部三種都算：
            - "rank"：`groupby(date_col)[col].rank(pct=True)`，百分位排名（0~1）
            - "zscore"：`(x - 當日均值) / 當日標準差`
            - "demean"：`x - 當日均值`

    Returns:
        (full_with_stats, tradable) — 兩個 DataFrame，欄位結構相同；`tradable` 是
        `full_with_stats` 排除 `lock_col=True` 之後的子集（若 `lock_col is None` 則
        `tradable is full_with_stats` 的複本，兩者相等）。
    """
    unknown = set(methods) - set(_METHOD_SUFFIX)
    if unknown:
        raise ValueError(f"不支援的 method: {unknown}，只接受 {sorted(_METHOD_SUFFIX)}")

    out = df.copy()
    for col in value_cols:
        g = out.groupby(date_col)[col]
        if "rank" in methods:
            out[f"{col}_rank"] = g.rank(pct=True)
        if "zscore" in methods or "demean" in methods:
            mean = g.transform("mean")
            if "demean" in methods:
                out[f"{col}_dm"] = out[col] - mean
            if "zscore" in methods:
                std = g.transform("std")
                out[f"{col}_z"] = (out[col] - mean) / std.replace(0.0, np.nan)

    if lock_col is None:
        tradable = out.copy()
    else:
        if lock_col not in out.columns:
            raise KeyError(f"lock_col={lock_col!r} 不在 df 欄位裡：{list(out.columns)}")
        tradable = out.loc[~out[lock_col].astype(bool)].copy()

    return out, tradable


def _self_test() -> None:
    """構造合成資料，證明「誰被標成鎖死」不會動到其他股票的橫斷面統計量。"""
    rng = np.random.default_rng(0)
    n_days, n_stocks = 5, 20
    rows = []
    for d in range(n_days):
        for s in range(n_stocks):
            rows.append({"date": d, "sid": s, "x": rng.normal(), "y": rng.normal()})
    base = pd.DataFrame(rows)

    # 情境 A：沒有任何股票鎖死
    a_full, a_tradable = cross_sectional_then_filter(
        base.assign(locked=False), "date", ["x"], lock_col="locked"
    )

    # 情境 B：隨機挑幾檔標成鎖死（模擬「今天誰鎖死了」是隨機、事後才知道的）
    locked_mask = rng.random(len(base)) < 0.3
    b_full, b_tradable = cross_sectional_then_filter(
        base.assign(locked=locked_mask), "date", ["x"], lock_col="locked"
    )

    # 不變量：不管誰被標成鎖死，「full_with_stats」裡每一列的統計量都應該跟情境A完全一樣
    # ——因為基準是用完整宇宙算的，不該被「誰鎖死」這個標記影響。
    merged = a_full.merge(b_full, on=["date", "sid"], suffixes=("_a", "_b"))
    for suffix in ("rank", "z", "dm"):
        col = f"x_{suffix}"
        diff = (merged[f"{col}_a"] - merged[f"{col}_b"]).abs()
        assert diff.max() < 1e-9, f"不變量被打破：{col} 因為鎖死標記不同而改變了（max diff={diff.max()})"

    # 額外檢查陷阱本身會製造多大的假象：如果誤用「先過濾再算」的錯誤寫法，
    # 同一批資料算出來的 rank 應該會跟正確版不一樣（用來確認這個測試真的有偵測力）。
    wrong = base.assign(locked=locked_mask)
    wrong_filtered = wrong.loc[~wrong["locked"]].copy()
    wrong_filtered["x_rank_buggy"] = wrong_filtered.groupby("date")["x"].rank(pct=True)
    correct_ranks = b_tradable.set_index(["date", "sid"])["x_rank"]
    buggy_ranks = wrong_filtered.set_index(["date", "sid"])["x_rank_buggy"]
    common = correct_ranks.index.intersection(buggy_ranks.index)
    any_diff = (correct_ranks.loc[common] - buggy_ranks.loc[common]).abs().max()
    assert any_diff > 0, "測試資料量太小，錯誤寫法與正確寫法剛好算出一樣的結果，測試沒有偵測力，加大 n_stocks"

    print(f"自我驗證通過：{n_days} 天 × {n_stocks} 檔合成資料，"
          f"正確寫法對鎖死標記免疫（max diff={diff.max():.2e}），"
          f"錯誤寫法會偏移（max diff={any_diff:.3f}，證明測試有偵測力）。")


if __name__ == "__main__":
    _self_test()
