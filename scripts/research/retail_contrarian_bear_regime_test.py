#!/usr/bin/env python3
"""空頭/高波動 regime 版「散戶反調極限」重測 —— 2026-09-28 交辦。

## 背景

`~/.claude/projects/-Users-jackm4-goldenstocks/memory/retail-contrarian-extreme-refuted.md`
記錄 127 日面板（2026-03~09，純多頭 drift）四項檢定全滅：D=散戶淨買%−大戶淨買% 無臨界
點反轉、同日 fade 是機械假象、隔日 excess gap 效應 100% 由大戶淨賣主導（極端組內散戶
淨賣子集比淨買子集更負，方向與「散戶是反指標」假說相反）。筆記自己標記「唯一翻案 =
空頭 regime」為未測維度——因為 127 日樣本全程多頭，沒有空頭段可測，文獻（Barrot,
Kaniel & Sraer）預測散戶當流動性提供者的報酬效應在市場壓力期會放大到 2 倍。

## 本腳本要回答的問題

台股資料庫涵蓋範圍內，有沒有 tick 級（單筆逐筆，用來做大戶/散戶分類）資料可用的
空頭/高波動窗口？如果有，重跑同一套定義（大戶=單筆≥500萬、散戶=單筆≤2張），看
壓力期是否出現多頭樣本沒有的訊號。

## 資料可用性調查結果（2026-09-28 手動 API 探測，見交付對話紀錄）

- `market_vix_daily`（symbol='VIXTWN'，mini 自算台版 VIX，2003-01-02 起）史上最高點在
  2008 年金融海嘯（2008-10-28 = 59.98），其次是 2020-03-23 COVID 崩盤（53.58）。
- FinMind `TaiwanStockPriceTick`（逐筆單筆，含 deal_price/volume/Time/TickType）實測
  邊界：2018-10-11 = 0 筆、2019-01-04 = 3,157 筆 → 覆蓋起點約在 2018 年底/2019 年初。
  因此 **2008 金融海嘯完全不在 tick 覆蓋範圍內**（2008-10-28 實測 0 筆），唯一可用的
  空頭/高波動窗口是 **2020-02-20~2020-03-31 COVID 崩盤**（VIXTWN 15.6→53.6、2330
  同期 -26%），這段確定有 tick 資料（2020-01-02／02-03／03-12／03-19 均實測 3,000+
  筆/檔/天）。
- 用同一年（2020）1 月當 calm 對照組（VIXTWN 12~20），避免拿 2026 年不同總體環境、
  不同執行層基礎設施的樣本硬比 calm vs stress。

## 方法論與原始 127 日面板的差異（皆為刻意選擇，非疏漏）

1. **方向判斷用 FinMind `TickType` 直接讀**（1=買方主動、2=賣方主動），不用「下一筆
   價格漂移」重建 Lee-Ready —— 對照 memory `FinMind TickType 決定性驗法`：那個方向
   已被踩雷驗證過，此法對，另一個方向（用漂移猜）是錯的。原始 127 日研究用什麼分類
   法不可考（session 已無存檔），這裡採用目前 codebase 已驗證的方法。
2. **跨日比較用 Fama-MacBeth**（先算「當日」跨股統計量，再跨日對這批日層級數字做
   t 檢定），不做未分層跨日池化迴歸 —— 呼應 memory `warrant-daily-callput-verdict`：
   「跨日池化分桶必配日聚類」。
3. **MDE 先算再看結果**——calm 只有 ~15 個交易日、stress ~28 個交易日，用「日」當
   聚類單位下,自由度天生就少,可能後驗证「不足以偵測 Barrot 等人宣稱的 2 倍放大」。
4. **鎖漲跌停排除用 `biglot_cross_sectional_lib.cross_sectional_then_filter`**——
   基準（demean/rank/z）用全部股票算,鎖死只在最後排除,避免用「剩餘股票」重算基準
   洩漏「今天誰鎖死」這個要收盤才確定的資訊（`biglot-overnight-limitup-proxy-verdict`
   教訓）。

## 執行方式

    PYTHONPATH=src .venv/bin/python scripts/research/retail_contrarian_bear_regime_test.py

唯讀查詢 `stock_daily_bars`／`market_vix_daily`，不寫 DB；tick 落地快取在
`DATA_DIR/cache/retail_contrarian_bear_tick/`（與生產用 `pit_universe_tick` 分開）。
FinMind 逐筆是「一天一請求」的重 API，預設節流 1500 req/hr，避免跟盤中其他排程
搶配額；可續跑（已快取的檔案會跳過）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from biglot_cross_sectional_lib import cross_sectional_then_filter  # noqa: E402

from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

# ---------------------------------------------------------------------------
# 事前登記參數
# ---------------------------------------------------------------------------
FORM_START, FORM_END = "2019-12-02", "2019-12-31"  # 宇宙形成窗（早於任一測試窗，避免前視）
CALM_START, CALM_END = "2020-01-02", "2020-01-31"  # calm 對照（VIXTWN ~12~20）
STRESS_START, STRESS_END = "2020-02-20", "2020-03-31"  # COVID 崩盤（VIXTWN 15.6→53.6）
TOP_N = int(os.environ.get("RETAIL_BEAR_TOP_N", "40"))
# ⚠ 2026-09-28 單位事故：FinMind `TaiwanStockPriceTick` 的 `volume` 欄位是「張」
# （1 張 = 1000 股），不是股數！用 2330 2020-01-02 驗證：tick 逐筆加總量 = 31,698，
# 對照 stock_daily_bars 當日成交量 = 33,282,120 股，比值 ~1050（同一單位下應該
# ≈1）。2026 年 pit_universe_tick 正式快取的 2330 樣本也是同樣比值（~1180~1280，
# 略高於 1000 可能是盤後鉅額交易未進逐筆 feed）。第一版程式碼誤把 volume 當成股數，
# 導致「單筆>=500萬」用 price*volume（少乘 1000）判定，全樣本 0 筆命中；
# 「單筆<=2張」用 vol<=2000（多乘 1000）判定，變成幾乎全部 tick 都算散戶——
# 兩個方向都錯,但恰好讓「大戶」桶變空、蓋掉了真正的訊號。已修正為 vol 直接以
# 「張」為單位比較,notional 額外乘 1000。
BIG_NOTIONAL = 5_000_000.0  # 大戶：單筆 >= 500 萬（NT$，= price * volume(張) * 1000）
RETAIL_LOTS = 2  # 散戶：單筆 <= 2 張（volume 欄位本身就是張數，直接比較）
LOCK_THRESHOLD = 0.095  # 漲跌停鎖死判定（同 biglot_diff_normalization_refit_v2 慣例）
D_EXTREME_Q = 0.20  # 極端組門檻：D 最高/最低 20% 分位（原始面板用 D>=20 的類比）

CACHE = DATA_DIR / "cache" / "retail_contrarian_bear_tick"
REQ_PER_HOUR = float(os.environ.get("RETAIL_BEAR_REQ_PER_HOUR", "1500"))
SLEEP = 3600.0 / REQ_PER_HOUR


def ro_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# 1. 宇宙 + 價格面板
# ---------------------------------------------------------------------------
def build_universe(conn: sqlite3.Connection, top_n: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT stock_id, AVG(amount) a, COUNT(*) n
        FROM stock_daily_bars
        WHERE trade_date BETWEEN ? AND ?
          AND source = 'finmind'
          AND amount IS NOT NULL
          AND stock_id NOT LIKE '00%'
          AND LENGTH(stock_id) = 4
        GROUP BY stock_id
        HAVING n >= 10
        ORDER BY a DESC
        LIMIT ?
        """,
        (FORM_START, FORM_END, top_n),
    ).fetchall()
    return [r["stock_id"] for r in rows]


def trading_dates(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily_bars "
        "WHERE trade_date BETWEEN ? AND ? AND source='finmind' ORDER BY trade_date",
        (start, end),
    ).fetchall()
    return [r[0] for r in rows]


def load_price_panel(conn: sqlite3.Connection, stocks: list[str], start: str, end: str) -> pd.DataFrame:
    """含 start 前 5 個交易日、end 後 5 個交易日的緩衝，供隔日/3日前瞻與昨收鎖死判定用。"""
    q_marks = ",".join("?" * len(stocks))
    rows = conn.execute(
        f"""
        SELECT stock_id, trade_date, open, high, low, close, volume
        FROM stock_daily_bars
        WHERE source = 'finmind' AND stock_id IN ({q_marks})
          AND trade_date BETWEEN date(?, '-12 day') AND date(?, '+12 day')
        ORDER BY stock_id, trade_date
        """,
        (*stocks, start, end),
    ).fetchall()
    df = pd.DataFrame(rows, columns=["stock_id", "trade_date", "open", "high", "low", "close", "volume"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.drop_duplicates(["stock_id", "trade_date"]).sort_values(["stock_id", "trade_date"])
    return df


def add_forward_returns_and_lock(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for sid, g in df.groupby("stock_id"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        g["prev_close"] = g["close"].shift(1)
        g["chg_today"] = g["close"] / g["prev_close"] - 1.0
        g["locked_today"] = g["chg_today"].abs() >= LOCK_THRESHOLD
        g["next_open"] = g["open"].shift(-1)
        g["next_close"] = g["close"].shift(-1)
        g["close_3"] = g["close"].shift(-3)
        g["overnight_gap_next"] = g["next_open"] / g["close"] - 1.0
        g["ret_next_close"] = g["next_close"] / g["close"] - 1.0
        g["ret_cum3"] = g["close_3"] / g["close"] - 1.0
        out.append(g)
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------------------
# 2. Tick 抓取（快取、可續跑、節流）
# ---------------------------------------------------------------------------
def fetch_tick_day(sid: str, day: str, hdr: dict) -> list:
    import requests

    path = CACHE / f"{sid}_{day}.json"
    if path.exists():
        return json.loads(path.read_text())
    backoff = 5.0
    for _ in range(6):
        try:
            r = requests.get(
                "https://api.finmindtrade.com/api/v4/data",
                params={"dataset": "TaiwanStockPriceTick", "data_id": sid, "start_date": day},
                headers=hdr,
                timeout=180,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    網路錯誤 {sid} {day}: {exc}", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        if r.status_code in (402, 429):
            print(f"    額度上限，退避 {backoff:.0f}s（{sid} {day}）", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 900)
            continue
        if r.status_code != 200:
            print(f"    HTTP {r.status_code} {sid} {day}", flush=True)
            return []
        data = r.json().get("data", [])
        path.write_text(json.dumps(data))
        return data
    return []


def fetch_all_ticks(stocks: list[str], dates: list[str]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    from project_dotenv import finmind_token_from_env

    hdr = {"Authorization": f"Bearer {finmind_token_from_env()}"}
    jobs = [(s, d) for d in dates for s in stocks]
    todo = [(s, d) for s, d in jobs if not (CACHE / f"{s}_{d}.json").exists()]
    print(f"總任務 {len(jobs):,}  已快取 {len(jobs) - len(todo):,}  待抓 {len(todo):,}", flush=True)
    print(f"節流 {REQ_PER_HOUR:.0f} req/hr（{SLEEP:.2f}s/次），預估 {len(todo) * SLEEP / 3600:.2f} 小時", flush=True)
    t0 = time.time()
    for i, (sid, day) in enumerate(todo, 1):
        fetch_tick_day(sid, day, hdr)
        if i % 100 == 0:
            el = time.time() - t0
            print(
                f"  {i:,}/{len(todo):,}  已耗 {el / 60:.1f} 分  "
                f"剩餘約 {(len(todo) - i) * el / max(i, 1) / 60:.1f} 分",
                flush=True,
            )
        time.sleep(SLEEP)


# ---------------------------------------------------------------------------
# 3. 大戶/散戶流量分類
# ---------------------------------------------------------------------------
def daily_flow_from_ticks(ticks: list) -> dict | None:
    if not ticks:
        return None
    total_vol = 0
    retail_buy = retail_sell = 0
    big_buy = big_sell = 0
    n_signed = 0
    for t in ticks:
        vol = t.get("volume") or 0
        price = t.get("deal_price") or 0.0
        ttype = t.get("TickType")
        total_vol += vol
        notional = price * vol * 1000.0  # volume 欄位單位是「張」，乘 1000 還原成 NT$
        signed = ttype in ("1", "2")
        if signed:
            n_signed += 1
        if vol <= RETAIL_LOTS:
            if ttype == "1":
                retail_buy += vol
            elif ttype == "2":
                retail_sell += vol
        if notional >= BIG_NOTIONAL:
            if ttype == "1":
                big_buy += vol
            elif ttype == "2":
                big_sell += vol
    if total_vol == 0:
        return None
    return {
        "total_volume": total_vol,
        "n_ticks": len(ticks),
        "n_signed": n_signed,
        "retail_net_pct": (retail_buy - retail_sell) / total_vol * 100.0,
        "big_net_pct": (big_buy - big_sell) / total_vol * 100.0,
        "retail_buy_vol": retail_buy,
        "retail_sell_vol": retail_sell,
        "big_buy_vol": big_buy,
        "big_sell_vol": big_sell,
    }


def build_flow_panel(stocks: list[str], dates: list[str]) -> pd.DataFrame:
    rows = []
    for d in dates:
        for s in stocks:
            path = CACHE / f"{s}_{d}.json"
            if not path.exists():
                continue
            ticks = json.loads(path.read_text())
            flow = daily_flow_from_ticks(ticks)
            if flow is None:
                continue
            flow["stock_id"] = s
            flow["trade_date"] = d
            rows.append(flow)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["D"] = df["retail_net_pct"] - df["big_net_pct"]
    return df


PIT_SPLIT_TIME = "11:00:00.000"


def intraday_pit_flow(ticks: list) -> dict | None:
    """同日 PIT 版本——用 09:00~11:00 的流量分類，對 11:00→收盤 的報酬做前瞻。

    原始 127 日面板的「同日 fade 是機械假象」教訓：用整天流量解釋整天報酬是同構的
    （散戶在盤中買、大戶在盤中賣，價格當然反映這筆量——這是恆等式不是訊號）。唯一
    乾淨的做法是用**前半場**流量預測**後半場**報酬，兩段不重疊。
    """
    if not ticks:
        return None
    morning = [t for t in ticks if t.get("Time", "") < PIT_SPLIT_TIME]
    if len(morning) < 20:
        return None
    flow = daily_flow_from_ticks(morning)
    if flow is None:
        return None
    # 11:00 當下價格：取 <= 11:00 最後一筆；收盤價：取全天最後一筆
    mid_price = morning[-1]["deal_price"]
    close_price = ticks[-1]["deal_price"]
    if not mid_price or not close_price:
        return None
    flow["afternoon_return"] = close_price / mid_price - 1.0
    flow["mid_price"] = mid_price
    flow["close_price"] = close_price
    return flow


def build_pit_panel(stocks: list[str], dates: list[str]) -> pd.DataFrame:
    rows = []
    for d in dates:
        for s in stocks:
            path = CACHE / f"{s}_{d}.json"
            if not path.exists():
                continue
            ticks = json.loads(path.read_text())
            flow = intraday_pit_flow(ticks)
            if flow is None:
                continue
            flow["stock_id"] = s
            flow["trade_date"] = d
            rows.append(flow)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["D"] = df["retail_net_pct"] - df["big_net_pct"]
    return df


# ---------------------------------------------------------------------------
# 4. Fama-MacBeth 式日層級檢定 + 事前 MDE
# ---------------------------------------------------------------------------
def mde(daily_stats: np.ndarray, alpha: float = 0.05, power: float = 0.80) -> float:
    n = len(daily_stats)
    if n < 3:
        return float("nan")
    sd = np.nanstd(daily_stats, ddof=1)
    t_alpha = stats.t.ppf(1 - alpha / 2, df=n - 1)
    t_power = stats.t.ppf(power, df=n - 1)
    return (t_alpha + t_power) * sd / np.sqrt(n)


def fmb_spread(panel: pd.DataFrame, ret_col: str, q: float = D_EXTREME_Q, d_col: str = "D") -> pd.DataFrame:
    """每日：D 最高 q 分位 vs 最低 q 分位的 excess forward return 價差（可交易子集,鎖死已排除）。"""
    recs = []
    for day, g in panel.groupby("trade_date"):
        g = g.dropna(subset=[ret_col, d_col])
        if len(g) < 8:
            continue
        hi_thr = g[d_col].quantile(1 - q)
        lo_thr = g[d_col].quantile(q)
        hi = g.loc[g[d_col] >= hi_thr, ret_col]
        lo = g.loc[g[d_col] <= lo_thr, ret_col]
        if len(hi) < 2 or len(lo) < 2:
            continue
        recs.append(
            {
                "trade_date": day,
                "n": len(g),
                "n_hi": len(hi),
                "n_lo": len(lo),
                "spread": hi.mean() - lo.mean(),
                "big_net_ctrl_hi": g.loc[g[d_col] >= hi_thr, "big_net_pct"].mean(),
                "big_net_ctrl_lo": g.loc[g[d_col] <= lo_thr, "big_net_pct"].mean(),
            }
        )
    return pd.DataFrame(recs)


def add_per_stock_zscore(df: pd.DataFrame, col: str = "D") -> pd.DataFrame:
    """逐檔（跨日）z 化——比照原始 127 日面板第二輪校正一：把 D 換成該股自己在樣本
    期間的 z-score，去除「500萬/2張」定義天生跟股價/單位股數綁死造成的混池分位偏差。
    """
    out = df.copy()
    g = out.groupby("stock_id")[col]
    out[f"{col}_z"] = (out[col] - g.transform("mean")) / g.transform("std").replace(0.0, np.nan)
    return out


def report_regime(name: str, spread_df: pd.DataFrame) -> None:
    print(f"\n--- {name}（{len(spread_df)} 個交易日,D 極端 {int(D_EXTREME_Q * 100)}% 分位價差） ---")
    if spread_df.empty:
        print("  [事實] 無可用日（每日跨股 N 不足 8 或極端組不足 2 檔），無法計算。")
        return
    vals_bp = spread_df["spread"].to_numpy() * 1e4
    n = len(vals_bp)
    mean_bp = np.nanmean(vals_bp)
    sd_bp = np.nanstd(vals_bp, ddof=1) if n > 1 else float("nan")
    tstat = mean_bp / (sd_bp / np.sqrt(n)) if n > 1 and sd_bp > 0 else float("nan")
    mde_bp = mde(vals_bp)
    print(f"  [事實] 日數 n={n}，日均跨股 N（可交易）中位數={spread_df['n'].median():.0f}")
    print(f"  [事實] MDE（alpha=.05, power=.80, 日聚類）= {mde_bp:.1f} bp")
    print(f"  [事實] 實測 spread 均值 = {mean_bp:+.1f} bp（sd={sd_bp:.1f} bp, t={tstat:+.2f}）")
    verdict = "未達 MDE，不得宣稱效應" if abs(mean_bp) < mde_bp else "達 MDE 門檻，可進一步看方向與符號一致性"
    print(f"  [推論]（假設：日聚類為主要相關來源，樣本內近似常態）{verdict}")
    print(
        f"  [事實] 高 D 組大戶淨買% 均值={spread_df['big_net_ctrl_hi'].mean():+.2f}pp，"
        f"低 D 組={spread_df['big_net_ctrl_lo'].mean():+.2f}pp"
        "（原判決：效應 100% 是大戶淨賣的影子——此處同步報告控制變數，供比對是否重演同一模式）"
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    conn = ro_conn()

    # 0. 重申資料可用性判斷（純資訊性 print，實測數字見檔頭 docstring）
    print("=" * 78)
    print("空頭/高波動 regime 版散戶反調極限重測")
    print("=" * 78)

    stocks = build_universe(conn, TOP_N)
    print(f"\n宇宙：{FORM_START}~{FORM_END} 平均日成交值前 {TOP_N} 大（排除 00 開頭 ETF），"
          f"共 {len(stocks)} 檔")

    calm_dates = trading_dates(conn, CALM_START, CALM_END)
    stress_dates = trading_dates(conn, STRESS_START, STRESS_END)
    print(f"calm 窗：{CALM_START}~{CALM_END}（{len(calm_dates)} 個交易日）")
    print(f"stress 窗：{STRESS_START}~{STRESS_END}（{len(stress_dates)} 個交易日）")

    all_dates = calm_dates + stress_dates
    if os.environ.get("RETAIL_BEAR_SKIP_FETCH") != "1":
        fetch_all_ticks(stocks, all_dates)
    else:
        print("[RETAIL_BEAR_SKIP_FETCH=1] 跳過抓取，只用既有快取")

    price = load_price_panel(conn, stocks, CALM_START, STRESS_END)
    price = add_forward_returns_and_lock(price)

    flow = build_flow_panel(stocks, all_dates)
    if flow.empty:
        print("\n[事實] tick 快取目前是空的——本次執行尚未完成抓取，無法產出結果。")
        return

    merged = flow.merge(
        price[
            [
                "stock_id",
                "trade_date",
                "locked_today",
                "overnight_gap_next",
                "ret_next_close",
                "ret_cum3",
            ]
        ],
        on=["stock_id", "trade_date"],
        how="left",
    )

    full, tradable = cross_sectional_then_filter(
        merged,
        date_col="trade_date",
        value_cols=["overnight_gap_next", "ret_next_close", "ret_cum3"],
        lock_col="locked_today",
    )
    # excess return = demean 後的欄位（{col}_dm），用可交易子集
    tradable = tradable.rename(
        columns={
            "overnight_gap_next_dm": "excess_overnight_gap_next",
            "ret_next_close_dm": "excess_ret_next_close",
            "ret_cum3_dm": "excess_ret_cum3",
        }
    )

    n_locked = int(full["locked_today"].sum())
    print(f"\n[事實] 面板總筆數（可交易）={len(tradable):,}，鎖漲跌停排除={n_locked:,} 筆")

    calm_panel = tradable[tradable["trade_date"].isin(pd.to_datetime(calm_dates))]
    stress_panel = tradable[tradable["trade_date"].isin(pd.to_datetime(stress_dates))]

    print("\n" + "=" * 78)
    print("檢定一：D 極端分位 → 隔夜 excess gap")
    print("=" * 78)
    report_regime("calm（2020-01，對照組）", fmb_spread(calm_panel, "excess_overnight_gap_next"))
    report_regime("stress（2020-02/03 COVID 崩盤）", fmb_spread(stress_panel, "excess_overnight_gap_next"))

    print("\n" + "=" * 78)
    print("檢定二：D 極端分位 → 隔日收盤 excess return")
    print("=" * 78)
    report_regime("calm（2020-01，對照組）", fmb_spread(calm_panel, "excess_ret_next_close"))
    report_regime("stress（2020-02/03 COVID 崩盤）", fmb_spread(stress_panel, "excess_ret_next_close"))

    print("\n" + "=" * 78)
    print("檢定三：D 極端分位 → 3 日累積 excess return")
    print("=" * 78)
    report_regime("calm（2020-01，對照組）", fmb_spread(calm_panel, "excess_ret_cum3"))
    report_regime("stress（2020-02/03 COVID 崩盤）", fmb_spread(stress_panel, "excess_ret_cum3"))

    print("\n" + "=" * 78)
    print("校正一（比照原始第二輪 4 項校正之一）：逐檔 z 化 D，重跑檢定二/三")
    print("（原判決：z 化後量測變乾淨，但仍是同一效應——這裡看是否在空頭/calm 子樣本重演）")
    print("=" * 78)
    tradable_z = add_per_stock_zscore(tradable, col="D")
    calm_panel_z = tradable_z[tradable_z["trade_date"].isin(pd.to_datetime(calm_dates))]
    stress_panel_z = tradable_z[tradable_z["trade_date"].isin(pd.to_datetime(stress_dates))]
    report_regime(
        "calm（2020-01，對照組，D_z→隔日收盤 excess）",
        fmb_spread(calm_panel_z, "excess_ret_next_close", d_col="D_z"),
    )
    report_regime(
        "stress（COVID 崩盤，D_z→隔日收盤 excess）",
        fmb_spread(stress_panel_z, "excess_ret_next_close", d_col="D_z"),
    )
    report_regime(
        "calm（2020-01，對照組，D_z→3日累積 excess）",
        fmb_spread(calm_panel_z, "excess_ret_cum3", d_col="D_z"),
    )
    report_regime(
        "stress（COVID 崩盤，D_z→3日累積 excess）",
        fmb_spread(stress_panel_z, "excess_ret_cum3", d_col="D_z"),
    )
    print(
        "\n  [事實] 原始四項校正的另外三項（三聯交集、散戶主買占比版、量能分層）"
        "與姊妹研究「大戶套牢投降點」（需連續交易日的 K 日大戶買 VWAP）本次未重跑——"
        "後者需要 calm/stress 兩窗之間（2020-02-01~02-19）的逐筆資料補上滾動窗口的"
        "連續性，本次抓取刻意只取 calm/stress 兩段、中間留白，不足以支撐滾動 VWAP；"
        "前者在本樣本規模（40 檔×2 regime）下切出的儲存格 n 會遠低於 8，事前就會被"
        "MDE 判定為不可解讀，故未列入，不是遺漏。"
    )

    print("\n" + "=" * 78)
    print("檢定四：同日 PIT fade（09:00-11:00 流量 → 11:00→收盤報酬，非重疊時段避免恆等式）")
    print("=" * 78)
    pit = build_pit_panel(stocks, all_dates)
    if pit.empty:
        print("  [事實] PIT 面板為空（tick 內無足夠 09:00-11:00 筆數），無法計算。")
    else:
        pit_merged = pit.merge(
            price[["stock_id", "trade_date", "locked_today"]],
            on=["stock_id", "trade_date"],
            how="left",
        )
        pit_full, pit_tradable = cross_sectional_then_filter(
            pit_merged,
            date_col="trade_date",
            value_cols=["afternoon_return"],
            lock_col="locked_today",
        )
        pit_tradable = pit_tradable.rename(columns={"afternoon_return_dm": "excess_afternoon_return"})
        pit_calm = pit_tradable[pit_tradable["trade_date"].isin(pd.to_datetime(calm_dates))]
        pit_stress = pit_tradable[pit_tradable["trade_date"].isin(pd.to_datetime(stress_dates))]
        report_regime("calm（2020-01，對照組）", fmb_spread(pit_calm, "excess_afternoon_return"))
        report_regime("stress（2020-02/03 COVID 崩盤）", fmb_spread(pit_stress, "excess_afternoon_return"))
        pit_tradable.to_csv(CACHE / "pit_panel_summary.csv", index=False)

    out_csv = CACHE / "panel_summary.csv"
    tradable.to_csv(out_csv, index=False)
    print(f"\n[事實] 面板已存：{out_csv}")


if __name__ == "__main__":
    main()
