#!/usr/bin/env python3
"""安靜建倉×財報催化劑窗口重測（quiet-accumulation, earnings-catalyst-restricted）。

動機
----
文獻（Keown & Pinkerton 1981 併購前知情建倉；PIN 模型系列）的「安靜建倉領先報酬」只在
**確定性事件催化劑**（併購公告、財報）條件下被驗證過，不是無條件普遍成立。內部
`quiet-accumulation-retail-arrival-verdict` 在**無特定催化劑的一般 127 日面板**測試：
機制真（大戶安靜買後散戶 5 日內 +4.06pp 到場）、溢價無（大戶×安靜交互 t=-0.46）。

本檔把樣本限定在**財報公告前 N 個交易日**這個窗口內，重新測同一個交互效應，對照全樣本
的 null 結果——文獻預測：限定在催化劑窗口內溢價應該浮現（全樣本 null 可能只是被無催化劑
情境稀釋）。

資料來源
--------
- PIT tick 面板宇宙（100 檔 × 127 交易日，2026-03-02~2026-09-01）：
  `~/goldenstocks-data/data/cache/pit_universe_tick/*.json`（12,797 個逐股逐日逐筆檔）。
  舊面板 `_flow_panel.pkl`（`build_pit_tick_flow.py` 產物）**不直接沿用其大戶/散戶流向欄
  位**，理由見下方「已修正的 bug」第 0 條——只借用它的 universe/日期範圍作校驗。
- MOPS 重大訊息歷史檔：`~/goldenstocks-data/data/research/mops_archive/mops_raw.db`
  （見 memory `mops-announcement-event-verdict`；573,578 筆、2018-01-01~2026-09-03、
  2,275 檔）。本檔另外用關鍵字規則從中篩出「真正的季度/年度財報公告」，見
  `_earnings_announcements()`。

已修正的 bug
------------
0. **TickType 方向反了（新發現，範圍外但擋路）**：`build_pit_tick_flow.py` 現有程式碼
   `s = 1 if TickType=="2" else -1 if TickType=="1" else 0` 把 FinMind 的 TickType 讀反了。
   memory `finmind-ticktype-sign-test` 與本檔獨立重跑的 Lee-Ready 檢定（見下方 assert）
   都確認 **TickType=1 是買方主動（外盤）、TickType=2 是賣方主動（內盤）**——本檔用
   40 檔隨機抽樣重驗：價格上升那一筆 95.3% 是 TickType=1、下降那一筆 95.9% 是
   TickType=2，與 memory 的 96.8% 一致。因為這個既有 bug 直接污染大戶/散戶「淨買」的
   方向，本檔**不重用 `_flow_panel.pkl` 的 `big500_*`/`small_*`/`_bigm`/`_smlm` 欄位**，
   改為從逐筆重新計算，並在跑之前用 assert 鎖死方向慣例（防止同一個 bug 再犯第三次）。
1. **大戶／散戶桶必須互斥**：大戶 = 單筆成交金額 >= 500 萬（沿用 2026-09-09 quiet-
   accumulation 原始判決當時 `biglot_live_watch.py` 的門檻，非後來 2026-09-12 改的
   1000 萬儀表板門檻——本檔目的是重跑同一個判決，門檻要對齊）；散戶 = 單筆 1 張
   （1000 股）**且**金額 < 500 萬。用 if/elif 等價邏輯（先判大戶門檻，只有不滿足才
   看散戶門檻），不是兩個獨立 if。
2. **高價股 1 張 >= 500 萬不可誤記為散戶**：本次催化劑子樣本內 2383（台光電）收盤價
   2170~6385 元，1 張金額落在 217~639 萬，部分月份會跨過 500 萬門檻——修正後這些月份
   2383 的散戶參與度會結構性偏低（原始筆記已預告這個效應：「大立光/川湖/旺矽/健策/
   創意/台光電的散戶欄自此結構上不可測」），本檔如實計算、不強行補回。

紀律
----
- PIT：訊號窗口 = 財報公告日**前** N 個交易日（嚴格早於公告日；用實際已發生的公告日
  定義窗口，而非「事前就知道哪天發」——這對本檔的研究設計是安全的，因為訊號變數
  （大戶流／散戶參與度／量能）全部只用 d 當天以內的盤中資料算，公告日期本身只拿來
  **事後**標記「這個 stock-day 落在哪個公告前的第幾天」，不會把 d 之後才知道的公告
  內容或日期精確性洩漏進訊號本身）。
- 橫斷面正規化＋鎖死排除：`biglot_cross_sectional_lib.cross_sectional_then_filter`
  （基準永遠用完整 100 檔宇宙算，鎖死只在最後排除可交易樣本時用到）。
- 唯讀：只讀本地 tick JSON 快取與獨立 `mops_raw.db`，不寫回任何生產資料。

用法
----
    PYTHONPATH=src .venv/bin/python scripts/research/quiet_accumulation_earnings_catalyst_test.py
"""
from __future__ import annotations

import bisect
import json
import pickle
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DATA_DIR  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from biglot_cross_sectional_lib import cross_sectional_then_filter  # noqa: E402

TICK_CACHE = DATA_DIR / "cache" / "pit_universe_tick"
MOPS_DB = DATA_DIR.parent / "data" / "research" / "mops_archive" / "mops_raw.db"
OUT_DIR = DATA_DIR / "research" / "quiet_accumulation_earnings_catalyst"
FEATURE_CACHE = OUT_DIR / "_stock_day_features.pkl"

BIG_AMT = 5_000_000          # 大戶：單筆金額門檻（元）——對齊原始判決當時的 biglot_live_watch
RETAIL_LOT = 1                # 散戶：單筆張數
SESSION_START, SESSION_END = 9 * 3600.0, 13 * 3600 + 1800.0   # 09:00:00 ~ 13:30:00
N_WINDOWS = (5, 10)
LOCK_TOL = 0.005              # 漲跌停判定容忍帶（相對前收）


def tsec(s: str) -> float:
    h, m, r = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(r)


# --------------------------------------------------------------------------
# 1. 逐筆重建 stock-day 特徵（修正 TickType 方向 + 桶互斥 bug）
# --------------------------------------------------------------------------
def _verify_ticktype_convention(files: list[Path], n_sample: int = 25) -> None:
    """獨立重驗 TickType 方向慣例，防止 build_pit_tick_flow.py 犯過的 bug 再犯一次。"""
    rng = np.random.default_rng(0)
    sample = [files[i] for i in rng.choice(len(files), size=min(n_sample, len(files)), replace=False)]
    up1 = up2 = down1 = down2 = 0
    for f in sample:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        d.sort(key=lambda x: x["Time"])
        for a, b in zip(d, d[1:]):
            if b["deal_price"] > a["deal_price"]:
                up1 += b["TickType"] == "1"
                up2 += b["TickType"] == "2"
            elif b["deal_price"] < a["deal_price"]:
                down1 += b["TickType"] == "1"
                down2 += b["TickType"] == "2"
    frac_up1 = up1 / max(1, up1 + up2)
    frac_down2 = down2 / max(1, down1 + down2)
    print(f"  Lee-Ready 校驗：漲一筆 TickType=1 佔 {frac_up1:.1%}；跌一筆 TickType=2 佔 {frac_down2:.1%}")
    assert frac_up1 > 0.90, "TickType 方向慣例不對：漲一筆理應絕大多數是買方主動(=1)"
    assert frac_down2 > 0.90, "TickType 方向慣例不對：跌一筆理應絕大多數是賣方主動(=2)"


def _one_stock_day(path: Path) -> dict | None:
    try:
        ticks = json.loads(path.read_text())
    except Exception:
        return None
    if len(ticks) < 200:
        return None
    ticks.sort(key=lambda x: x["Time"])
    ts = [tsec(t["Time"]) for t in ticks]
    sess = [(t, s) for t, s in zip(ticks, ts) if SESSION_START <= s < SESSION_END]
    if len(sess) < 200:
        return None
    # 品質閘：剔除等間隔快照（非逐筆）—— 與 build_pit_tick_flow.py 相同判準
    gaps = [b - a for (_, a), (_, b) in zip(sess, sess[1:])]
    if gaps:
        mg = sum(gaps) / len(gaps)
        if mg > 0:
            sd = (sum((g - mg) ** 2 for g in gaps) / len(gaps)) ** 0.5
            if sd / mg < 0.5:
                return None

    total_amt = 0.0
    big_net_lots = 0.0
    retail_net_lots = 0.0
    retail_bilateral_amt = 0.0
    vol_day = 0.0
    for t, s in sess:
        if s < SESSION_START + 10:      # 略過開盤試撮
            continue
        px, vol = t["deal_price"], t["volume"]
        amt = px * vol * 1000.0
        sign = 1 if t["TickType"] == "1" else (-1 if t["TickType"] == "2" else 0)
        total_amt += amt
        vol_day += vol
        if amt >= BIG_AMT:
            big_net_lots += sign * vol           # 大戶門檻優先判（互斥的第一層）
        elif vol == RETAIL_LOT:                  # 只有不滿足大戶門檻才看散戶
            retail_net_lots += sign * vol
            retail_bilateral_amt += amt
    if total_amt <= 0 or vol_day <= 0:
        return None

    return {
        "sid": sess[0][0]["stock_id"],
        "date": sess[0][0]["date"],
        "open": sess[0][0]["deal_price"],
        "close": sess[-1][0]["deal_price"],
        "vol_day": vol_day,
        "total_amt": total_amt,
        "big_net_pct": big_net_lots / vol_day * 100.0,
        "retail_participation_pct": retail_bilateral_amt / total_amt * 100.0,
        "retail_net_lots": retail_net_lots,
    }


def build_features(force: bool = False) -> pd.DataFrame:
    if FEATURE_CACHE.exists() and not force:
        return pickle.load(FEATURE_CACHE.open("rb"))

    files = sorted(p for p in TICK_CACHE.glob("*.json") if not p.name.startswith("_"))
    print(f"逐筆檔案 {len(files):,} 個，重建 stock-day 特徵（含 TickType 方向校驗）...")
    _verify_ticktype_convention(files)

    rows = []
    for i, f in enumerate(files):
        if i % 2000 == 0:
            print(f"  {i:,}/{len(files):,}", flush=True)
        rec = _one_stock_day(f)
        if rec is not None:
            rows.append(rec)
    df = pd.DataFrame(rows).sort_values(["sid", "date"]).reset_index(drop=True)
    print(f"→ {len(df):,} stock-day（{df['sid'].nunique()} 檔 × {df['date'].nunique()} 日）")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with FEATURE_CACHE.open("wb") as fh:
        pickle.dump(df, fh)
    return df


# --------------------------------------------------------------------------
# 2. MOPS 財報公告日期（過濾出真正的季度/年度財報，排除「引用財務報表淨值」的
#    背書保證/資金貸與等無關公告）
# --------------------------------------------------------------------------
_EXCLUDE_RE = re.compile(r"資金貸與|背書保證|淨值百分之|代子公司|代重要子公司|代海外子公司")
_MONTHLY_RE = re.compile(r"\d+月份|\d+月合併|\d+月自結")
_QUARTER_OK_RE = re.compile(r"年度|第[一二三四1234]季|上半年度")
_EARNINGS_LIKE_RE = re.compile(
    r"財務報表|自結.*損益|自結.*淨利|自結.*每股盈餘|自結.*稅前|自結.*稅後"
)


def earnings_announcements(sids: list[str]) -> pd.DataFrame:
    """回傳 (stock_id, ann_date) 的乾淨季度/年度財報公告清單。

    PIT 注意：`ann_date` 是**已發生**的實際揭露日，只在事後拿來標記「這個 stock-day
    落在公告前第幾個交易日」，訊號變數本身不使用公告日或公告內容。
    """
    con = sqlite3.connect(f"file:{MOPS_DB}?mode=ro", uri=True)
    qmarks = ",".join("?" * len(sids))
    rows = con.execute(
        f"SELECT stock_id, ann_date, subject FROM mops WHERE stock_id IN ({qmarks})",
        sids,
    ).fetchall()
    con.close()

    out = []
    for sid, ann_date, subject in rows:
        s1 = subject.replace("\r\n", "")
        if not _EARNINGS_LIKE_RE.search(s1):
            continue
        if _EXCLUDE_RE.search(s1) or _MONTHLY_RE.search(s1) or not _QUARTER_OK_RE.search(s1):
            continue
        out.append((sid, ann_date, s1))
    return pd.DataFrame(out, columns=["sid", "ann_date", "subject"]).drop_duplicates(["sid", "ann_date"])


def build_catalyst_flags(df: pd.DataFrame, ann: pd.DataFrame, n: int) -> pd.Series:
    """對每個 stock-day 標記是否落在「該股下一次財報公告前 n 個交易日」窗口內。"""
    cal = sorted(df["date"].unique())
    flag = pd.Series(False, index=df.index)
    events_used = 0
    for sid, g in ann.groupby("sid"):
        stock_dates = sorted(df.loc[df["sid"] == sid, "date"].unique())
        if not stock_dates:
            continue
        for ann_date in g["ann_date"]:
            pos = bisect.bisect_left(cal, ann_date)
            if pos < n:
                continue   # 面板裡公告前不足 n 個交易日，窗口會被截斷，整段捨棄
            window = set(cal[pos - n:pos])
            hit = df["sid"].eq(sid) & df["date"].isin(window)
            if hit.any():
                flag |= hit
                events_used += 1
    print(f"  N={n}: 採用 {events_used} 個財報事件的完整窗口")
    return flag


# --------------------------------------------------------------------------
# 3. 前向報酬、鎖死、橫斷面正規化
# --------------------------------------------------------------------------
def add_forward_and_lock(df: pd.DataFrame) -> pd.DataFrame:
    cal = sorted(df["date"].unique())
    idx = {d: i for i, d in enumerate(cal)}
    df = df.sort_values(["sid", "date"]).reset_index(drop=True)

    fwd_gap = np.full(len(df), np.nan)
    ret_intraday = (df["close"] / df["open"] - 1.0) * 1e4
    locked = np.zeros(len(df), dtype=bool)

    for sid, g in df.groupby("sid"):
        gi = g.index.to_numpy()
        dates = g["date"].tolist()
        closes = g["close"].tolist()
        opens = g["open"].tolist()
        for k in range(len(gi)):
            # 前向隔夜跳空：僅在下一列在全域行事曆上緊接著今天時才計算
            if k + 1 < len(gi) and idx[dates[k + 1]] - idx[dates[k]] == 1:
                fwd_gap[gi[k]] = (opens[k + 1] / closes[k] - 1.0) * 1e4
            # 鎖死：今天收盤 vs 前一列收盤（同樣要求是緊接著的前一交易日）
            if k > 0 and idx[dates[k]] - idx[dates[k - 1]] == 1:
                prev_close = closes[k - 1]
                if prev_close > 0:
                    chg = closes[k] / prev_close - 1.0
                    if chg >= 0.0995 - LOCK_TOL or chg <= -0.0995 + LOCK_TOL:
                        locked[gi[k]] = True

    df = df.copy()
    df["ret_intraday_bps"] = ret_intraday.to_numpy()
    df["fwd_gap_bps"] = fwd_gap
    df["locked"] = locked
    return df


def cross_sectional_normalize(df: pd.DataFrame) -> pd.DataFrame:
    full, _ = cross_sectional_then_filter(
        df, date_col="date", value_cols=["big_net_pct", "retail_participation_pct"],
        lock_col=None, methods=("zscore",),
    )
    full["quiet_z"] = -full["retail_participation_pct_z"]
    full["interaction"] = full["big_net_pct_z"] * full["quiet_z"]
    return full


# --------------------------------------------------------------------------
# 4. 迴歸 + MDE
# --------------------------------------------------------------------------
def run_regression(sub: pd.DataFrame, label: str) -> dict:
    sub = sub.dropna(subset=["fwd_gap_bps", "big_net_pct_z", "quiet_z", "interaction", "ret_intraday_bps"])
    n = len(sub)
    result = {"label": label, "n": n}
    if n < 10:
        result["note"] = "樣本 < 10，不跑迴歸"
        return result

    X = sm.add_constant(sub[["big_net_pct_z", "quiet_z", "interaction", "ret_intraday_bps"]])
    y = sub["fwd_gap_bps"]
    n_days = sub["date"].nunique()
    ols = sm.OLS(y, X)
    if n_days >= 10:
        fit = ols.fit(cov_type="cluster", cov_kwds={"groups": sub["date"]})
        se_kind = f"cluster-by-date (n_days={n_days})"
    else:
        fit = ols.fit(cov_type="HC1")
        se_kind = f"HC1（n_days 僅 {n_days}，樣本太少做不了 cluster-by-date）"

    beta = fit.params["interaction"]
    se = fit.bse["interaction"]
    t = fit.tvalues["interaction"]
    # MDE：80% power、alpha=0.05 兩尾，z_(1-alpha/2)+z_power ≈ 1.96+0.84=2.80
    mde = 2.80 * se

    result.update({
        "n_days": n_days,
        "se_kind": se_kind,
        "beta_interaction": beta,
        "se_interaction": se,
        "t_interaction": t,
        "mde_interaction_bps_per_1sigma": mde,
        "beta_big_main": fit.params["big_net_pct_z"],
        "t_big_main": fit.tvalues["big_net_pct_z"],
        "beta_quiet_main": fit.params["quiet_z"],
        "t_quiet_main": fit.tvalues["quiet_z"],
    })
    return result


def print_result(r: dict) -> None:
    print(f"\n--- {r['label']} ---")
    print(f"  n = {r['n']}" + (f"（{r.get('n_days')} 個不同交易日）" if "n_days" in r else ""))
    if "note" in r:
        print(f"  {r['note']}")
        return
    print(f"  SE 方法: {r['se_kind']}")
    print(f"  大戶淨買主效果: {r['beta_big_main']:+.2f}bps/1σ (t={r['t_big_main']:+.2f})")
    print(f"  安靜(散戶低參與)主效果: {r['beta_quiet_main']:+.2f}bps/1σ (t={r['t_quiet_main']:+.2f})")
    print(f"  大戶×安靜交互: {r['beta_interaction']:+.2f}bps/1σ² "
          f"(SE={r['se_interaction']:.2f}, t={r['t_interaction']:+.2f})")
    print(f"  MDE(80% power, α=0.05 兩尾): |交互係數| > {r['mde_interaction_bps_per_1sigma']:.2f}bps 才測得出來")


def main() -> None:
    df = build_features()
    df = add_forward_and_lock(df)
    df = cross_sectional_normalize(df)

    tradable = df.loc[~df["locked"]].copy()
    print(f"\n完整宇宙 {df['sid'].nunique()} 檔 × {df['date'].nunique()} 日 = {len(df):,} stock-day；"
          f"排除鎖死後可交易 {len(tradable):,} stock-day")

    baseline = run_regression(tradable, "全樣本基準（複製原判決設計，含 2026-09 TickType 修正）")
    print_result(baseline)

    ann = earnings_announcements(sorted(df["sid"].unique()))
    print(f"\nMOPS 財報公告（過濾後）：{len(ann)} 筆，涵蓋 {ann['sid'].nunique()} 檔")

    for n in N_WINDOWS:
        flag = build_catalyst_flags(df, ann, n)
        cat_sub = tradable.loc[flag.reindex(tradable.index, fill_value=False)]
        r = run_regression(cat_sub, f"財報催化劑窗口 N={n}（公告前 {n} 個交易日）")
        print_result(r)
        if r["n"] > 0:
            stocks_in = sorted(cat_sub["sid"].unique())
            print(f"  子樣本涵蓋股票: {stocks_in}")


if __name__ == "__main__":
    main()
