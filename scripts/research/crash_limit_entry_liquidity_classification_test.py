#!/usr/bin/env python3
"""急殺後掛下方限價單:用「衝擊分類特徵」重新分組,測流動性驅動 vs 資訊驅動子組的報酬差異。

## 背景

`crash-passive-limit-entry-verdict.md`(2026-09-22):同一批急殺事件,距離改成 bps(而非
「檔」)之後 OOS 完全歸零,已判「此線終止」。唯一殘餘訊號在「急殺 ∧ 大戶淨賣≤−1千萬 ∧
掛3~5檔」這格(+31~43bps, t≈2.6, 略低於 MDE),但筆記明講「大戶門檻不敏感」——用一個
時點快照的「大戶淨賣金額」當代理變數太粗,分不出「流動性驅動」(恐慌拋售,適合接刀)
vs「資訊驅動」(真正知情賣壓,不該接)。

Kaniel & Liu (2006) 選單理論 / 流動性 vs 資訊驅動衝擊辨識文獻的教訓:要分辨這兩種價格
衝擊,得看「委託簿本身怎麼變」——流動性驅動衝擊應該是**雙邊**流動性同時撤退(市場作
手因為需要即時性而全面退,不是有人在賣方持續加碼)、沒有新賣單堆積;資訊驅動則是
**單邊**持續:賣一深度持續增厚(有人陸續掛更多限價賣單卡位)、委託簿失衡持續往賣方
惡化。這條線改用五檔快照本身的深度/價差/量能特徵做分類,而不是外部的大戶金額代理。

## 資料源

`${GOLDENSTOCKS_DATA_DIR}/cache/watchlist_books/watchlist_books_*.jsonl`
(TWSE MIS 五檔快照,45 檔固定觀察清單,~5~10 秒一次,2026-09-05~09-28 共 16 個交易日)。

**已知資料雷**(`watchlist-books-zero-price-quirk.md`):`bp[0]`/`ap[0]` 偶爾回傳 0.0 搭配
異常巨量,真正最優價在下一個 price>0 的位置——本檔 `_valid_levels()` 一律先過濾
`price>0` 再取前 5 檔,不假設 index 0 是最優價。

**已知資料侷限(誠實列出,不是事後找藉口)**:
1. `z`(成交價)欄位 93% 是 null(MIS 快照只在剛好抓到新成交時才有值),因此本檔用
   `mid=(bid0+ask0)/2` 當價格序列,而非真實成交價——3 分鐘報酬/新低判定、限價成交判定
   都繼承這個近似,精度是 bps 級而非 tick 級。
2. 沒有逐筆成交量/筆數,只有累計量 `v`(張),因此「成交筆數密度」用「量能速率」
   (Δv/Δt)近似,不是真正的筆數。
3. 沒有逐筆成交價,因此「嚴格穿過即成交」規則用「最優買價跌破限價」近似(見
   `simulate_fill()` docstring),比 `crash-passive-limit-entry-verdict.md` 的 tick 重放更
   寬鬆——本檔同時報「保守」「樂觀」兩種成交判定,並在報告中明講這個近似,不宣稱等同
   tick 級精度。
4. 16 個交易日、45 檔,不是原始筆記的 14 日 36 檔,兩者不可直接比大小,只能各自看
   方向與顯著性。

## 方法

1. 逐 (symbol, date) 建構價格/深度/價差/量能時間序列。
2. 偵測急殺事件:3 分鐘報酬 ≤ −1.5% 且創 3 分鐘新低(與原始筆記定義一致),同檔 180 秒
   內去重。
3. 用事件觸發**之前**(PIT,無未來函數)的委託簿窗口 [t−180s, t) 拆成 early
   [t−180,t−90) / late [t−90,t) 兩段,算:
   - `bid_drop_ratio` = late 買方 5 檔深度 / early 買方 5 檔深度(小=買方深度蒸發)
   - `ask_buildup_ratio` = late 賣方 5 檔深度 / early 賣方 5 檔深度(大=賣方持續堆單)
   - `spread_widen_ratio` = late 相對價差 / early 相對價差
   - `vol_accel_ratio` = late 量能速率 / early 量能速率
   - `imbalance_shift` = late 委託簿失衡 − early 委託簿失衡(更負=往賣方惡化)
   - 複合分數 `info_pressure_z` = z(ask_buildup_ratio) + z(−imbalance_shift)(高=更像
     資訊驅動的假說方向);`liquidity_vs_info_score` = −info_pressure_z(高=更像流動性
     驅動)。
4. 對每個事件模擬被動限價單(比照 v2:距離用 bps、非檔;FIFO 用「最優買價跌破限價」
   近似;延遲 0 秒;撤單 300 秒;成交後持有 5 分鐘市價出;成本 25bps)。
5. 依分類特徵中位數/三分位切組,對每組算日聚類 t 檢定與 MDE(crit=2.0,與
   `vp_g1a_summarize.py` 同一慣例),比較「假說流動性驅動」vs「假說資訊驅動」子組。

## 紀律

- 特徵與切法**事前寫死**在本檔頭(上面 4 個比率 + 1 個複合分數),不做參數掃描選最佳切
  點——避免重蹈原始筆記「大戶門檻不敏感」背後那種事後調參的坑。
- 只跑一次、不迭代調整门槛去追顯著;若無效就如實回報「此線仍終止」。
- 唯讀:只讀 jsonl cache,不寫 DB、不下單。
"""
from __future__ import annotations

import glob
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DATA_DIR  # noqa: E402

BOOKS_DIR = DATA_DIR / "cache" / "watchlist_books"
CRASH_RET = -0.015          # 3 分鐘急殺門檻(與原始筆記一致)
CRASH_WINDOW_SEC = 180
DEDUP_SEC = 180              # 同檔事件去重窗
EARLY_LATE_SPLIT_SEC = 90    # PIT 特徵窗拆成 early/late 各 90 秒
ENTRY_DELAY_SEC = 0          # 事件觸發後多久掛單(比照 v2 最佳:0 秒)
CANCEL_SEC = 300             # 撤單秒數(比照 v2「撤300」)
HOLD_SEC = 300               # 成交後持有 5 分鐘市價出
DISTANCES_BPS = (50, 75, 100)  # 掛單距離(bps),事前寫死三個值,75 為主結果
COST_BPS = 25.0              # 進場被動不吃價差、出場市價+稅費(與原始筆記一致)
T_CRIT = 2.0                 # MDE 判準(與 vp_g1a_summarize.py 同慣例)


def _valid_levels(prices: list, qtys: list) -> list[tuple[float, float]]:
    """過濾 zero-price 雜訊(watchlist-books-zero-price-quirk.md),只留 price>0 的檔位。"""
    out = []
    for p, q in zip(prices or [], qtys or []):
        if p and p > 0:
            out.append((float(p), float(q or 0.0)))
    return out


def load_snapshots() -> dict[tuple[str, str], list[dict]]:
    """回傳 {(sym, date): [snapshot,...]} 已按 ts 排序。"""
    files = sorted(glob.glob(str(BOOKS_DIR / "watchlist_books_*.jsonl")))
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for fp in files:
        with open(fp) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                bp = _valid_levels(r.get("bp"), r.get("bq"))
                ap = _valid_levels(r.get("ap"), r.get("aq"))
                if not bp or not ap:
                    continue
                try:
                    v = float(r.get("v"))
                except (TypeError, ValueError):
                    v = None
                bid0, bid0q = bp[0]
                ask0, ask0q = ap[0]
                mid = (bid0 + ask0) / 2.0
                snap = dict(
                    ts=float(r["ts"]), t=r.get("t"), mid=mid,
                    bid0=bid0, ask0=ask0,
                    bid_depth5=sum(q for _, q in bp[:5]),
                    ask_depth5=sum(q for _, q in ap[:5]),
                    v=v,
                )
                by_key[(r["sym"], r["d"])].append(snap)
    for key in by_key:
        by_key[key].sort(key=lambda x: x["ts"])
        # 前向補 v(偶有 None)
        last_v = None
        for s in by_key[key]:
            if s["v"] is None:
                s["v"] = last_v
            else:
                last_v = s["v"]
    return by_key


def _find_ref_index(ts_list: list[float], i: int, lookback_sec: float,
                     tol_sec: float = 30.0) -> int | None:
    """找最後一個 ts <= ts[i]-lookback_sec 的索引,容忍 tol_sec 內找不到就放棄。"""
    target = ts_list[i] - lookback_sec
    k = i
    while k >= 0 and ts_list[k] > target:
        k -= 1
    if k < 0 or ts_list[k] < target - tol_sec:
        return None
    return k


def detect_events(rows: list[dict]) -> list[int]:
    """回傳觸發急殺事件的索引清單(3分−1.5%創新低,同檔180秒內去重)。"""
    ts_list = [r["ts"] for r in rows]
    mid_list = [r["mid"] for r in rows]
    events = []
    last_event_ts = -1e18
    for i in range(len(rows)):
        if ts_list[i] - last_event_ts < DEDUP_SEC:
            continue
        ref_idx = _find_ref_index(ts_list, i, CRASH_WINDOW_SEC)
        if ref_idx is None:
            continue
        ref_price = mid_list[ref_idx]
        if ref_price <= 0:
            continue
        ret = mid_list[i] / ref_price - 1.0
        if ret > CRASH_RET:
            continue
        window_min = min(mid_list[ref_idx:i + 1])
        if mid_list[i] > window_min + 1e-9:
            continue
        events.append(i)
        last_event_ts = ts_list[i]
    return events


def _window_stats(rows: list[dict], ts_list: list[float], lo: float, hi: float) -> dict | None:
    """[lo,hi) 窗口內的平均深度/相對價差、量能速率。空窗回傳 None。"""
    idx = [j for j, t in enumerate(ts_list) if lo <= t < hi]
    if len(idx) < 2:
        return None
    bid_depth = statistics.fmean(rows[j]["bid_depth5"] for j in idx)
    ask_depth = statistics.fmean(rows[j]["ask_depth5"] for j in idx)
    spread_rel = statistics.fmean(
        (rows[j]["ask0"] - rows[j]["bid0"]) / rows[j]["mid"] for j in idx if rows[j]["mid"] > 0
    )
    v0, v1 = rows[idx[0]]["v"], rows[idx[-1]]["v"]
    dt = ts_list[idx[-1]] - ts_list[idx[0]]
    vol_rate = (v1 - v0) / dt if (v0 is not None and v1 is not None and dt > 0) else None
    return dict(bid_depth=bid_depth, ask_depth=ask_depth, spread_rel=spread_rel,
                vol_rate=vol_rate)


def compute_features(rows: list[dict], i: int) -> dict | None:
    """事件 i 的 PIT 分類特徵,只用 [t-180, t) 的資料。回傳 None 代表任一子窗資料不足。"""
    ts_list = [r["ts"] for r in rows]
    t0 = ts_list[i]
    early = _window_stats(rows, ts_list, t0 - CRASH_WINDOW_SEC, t0 - EARLY_LATE_SPLIT_SEC)
    late = _window_stats(rows, ts_list, t0 - EARLY_LATE_SPLIT_SEC, t0)
    if early is None or late is None:
        return None
    if early["bid_depth"] <= 0 or early["ask_depth"] <= 0 or early["spread_rel"] <= 0:
        return None
    if early["vol_rate"] is None or late["vol_rate"] is None or early["vol_rate"] <= 0:
        return None

    bid_drop_ratio = late["bid_depth"] / early["bid_depth"]
    ask_buildup_ratio = late["ask_depth"] / early["ask_depth"]
    spread_widen_ratio = late["spread_rel"] / early["spread_rel"]
    vol_accel_ratio = late["vol_rate"] / early["vol_rate"]

    imb_early = (early["bid_depth"] - early["ask_depth"]) / (early["bid_depth"] + early["ask_depth"])
    imb_late = (late["bid_depth"] - late["ask_depth"]) / (late["bid_depth"] + late["ask_depth"])
    imbalance_shift = imb_late - imb_early

    return dict(
        bid_drop_ratio=bid_drop_ratio,
        ask_buildup_ratio=ask_buildup_ratio,
        spread_widen_ratio=spread_widen_ratio,
        vol_accel_ratio=vol_accel_ratio,
        imbalance_shift=imbalance_shift,
    )


def simulate_fill(rows: list[dict], i: int, distance_bps: float):
    """從事件觸發(+ENTRY_DELAY_SEC)起掛限價買單,回傳 (touch_fill, conservative_fill)。

    每個 fill 是 (fill_ts, fill_price) 或 None。

    委託簿快照沒有逐筆成交價(z 93% 缺值),只能從最優報價序列近似判定:
    - **樂觀(touch)**:最優賣價 ask0 <= 限價,視為「價格已經來到我掛的位置,可能成交」
      (與 v2 筆記的「樂觀規則」精神一致:排在隊首的假設)。
    - **保守(conservative)**:最優買價 bid0 < 限價(嚴格),代表整個買方五檔在我掛的
      價位之上都已經被吃掉/撤走,等於這個價位已經被交易穿過,保證成交
      (比 v2 的 tick 級「嚴格穿過」更寬鬆一版近似,見檔頭侷限說明 3)。
    """
    ts_list = [r["ts"] for r in rows]
    entry_ts = ts_list[i] + ENTRY_DELAY_SEC
    ref_idx = i
    while ref_idx + 1 < len(rows) and ts_list[ref_idx] < entry_ts:
        ref_idx += 1
    if ref_idx >= len(rows):
        return None, None
    ref_price = rows[ref_idx]["mid"]
    limit_price = ref_price * (1 - distance_bps / 10000.0)
    deadline = entry_ts + CANCEL_SEC

    touch_fill = None
    cons_fill = None
    for j in range(ref_idx, len(rows)):
        tj = ts_list[j]
        if tj < entry_ts:
            continue
        if tj > deadline:
            break
        if touch_fill is None and rows[j]["ask0"] <= limit_price:
            touch_fill = (tj, limit_price)
        if cons_fill is None and rows[j]["bid0"] < limit_price - 1e-9:
            cons_fill = (tj, limit_price)
        if touch_fill is not None and cons_fill is not None:
            break
    return touch_fill, cons_fill


def simulate_exit(rows: list[dict], fill_ts: float) -> float | None:
    """成交後持有 HOLD_SEC 秒,市價(mid)出場,回傳出場價;超出當天資料範圍回傳 None。"""
    ts_list = [r["ts"] for r in rows]
    target = fill_ts + HOLD_SEC
    j = 0
    while j < len(ts_list) and ts_list[j] < target:
        j += 1
    if j >= len(ts_list):
        return None
    if ts_list[j] - target > 60:  # 找到的下一筆離目標超過 60 秒,資料斷了,不採用
        return None
    return rows[j]["mid"]


def day_clustered(values_by_day: dict[str, list[float]]) -> dict:
    day_means = [statistics.fmean(v) for v in values_by_day.values() if v]
    n_days = len(day_means)
    n_events = sum(len(v) for v in values_by_day.values())
    if n_days < 2:
        return dict(mean=float("nan"), t=float("nan"), n_days=n_days,
                    n_events=n_events, sd=float("nan"), mde=float("nan"))
    mean = statistics.fmean(day_means)
    sd = statistics.stdev(day_means)
    se = sd / math.sqrt(n_days)
    t = mean / se if se > 0 else float("inf")
    mde = T_CRIT * sd / math.sqrt(n_days) if sd else float("nan")
    return dict(mean=mean, t=t, n_days=n_days, n_events=n_events, sd=sd, mde=mde)


def zscore(vals: list[float]) -> list[float]:
    arr = np.asarray(vals, dtype=float)
    m, s = arr.mean(), arr.std(ddof=1)
    if s == 0 or math.isnan(s):
        return [0.0] * len(vals)
    return list((arr - m) / s)


def main() -> None:
    print(f"讀取 {BOOKS_DIR} …")
    by_key = load_snapshots()
    n_days = len({d for _, d in by_key})
    n_syms = len({s for s, _ in by_key})
    print(f"symbol-day 組數 {len(by_key)}（{n_syms} 檔 × 最多 {n_days} 日）")

    # 逐 symbol-day 偵測事件 + 算特徵 + 模擬三個距離的成交/報酬
    trades = []  # list of dict: date, sym, features(dict or None), per-distance results
    n_events_total = 0
    n_feat_ok = 0
    for (sym, date), rows in by_key.items():
        events = detect_events(rows)
        n_events_total += len(events)
        for i in events:
            feats = compute_features(rows, i)
            if feats is not None:
                n_feat_ok += 1
            rec = dict(date=date, sym=sym, event_idx=i, features=feats, fills={})
            for dist in DISTANCES_BPS:
                touch, cons = simulate_fill(rows, i, dist)
                res = {}
                for label, fill in (("touch", touch), ("cons", cons)):
                    if fill is None:
                        res[label] = None
                        continue
                    fill_ts, fill_price = fill
                    exit_price = simulate_exit(rows, fill_ts)
                    if exit_price is None:
                        res[label] = None
                        continue
                    gross_bps = (exit_price / fill_price - 1.0) * 10000.0
                    res[label] = dict(gross_bps=gross_bps, net_bps=gross_bps - COST_BPS)
                rec["fills"][dist] = res
            trades.append(rec)

    print(f"\n偵測到急殺事件 {n_events_total} 筆（{n_days} 日 × {n_syms} 檔，"
          f"3分-1.5%創新低，180秒去重）")
    print(f"PIT 特徵可算（early/late 兩窗都有 >=2 筆快照）: {n_feat_ok} 筆"
          f"（{n_feat_ok/n_events_total*100:.0f}%）")

    # ---- headline：全樣本三個距離、兩種成交規則的基準結果 ----
    print("\n" + "=" * 78)
    print("【基準】全樣本(未分類)，三個距離 × 兩種成交規則")
    print("=" * 78)
    for dist in DISTANCES_BPS:
        for label in ("touch", "cons"):
            by_day = defaultdict(list)
            n_filled = 0
            for r in trades:
                res = r["fills"][dist][label]
                if res is not None:
                    by_day[r["date"]].append(res["net_bps"])
                    n_filled += 1
            stats = day_clustered(by_day)
            fill_rate = n_filled / n_events_total * 100 if n_events_total else 0.0
            print(f"  {dist:>3}bps {label:>4}: 成交率{fill_rate:5.1f}%  n={stats['n_events']:4d}  "
                  f"日數={stats['n_days']:2d}  net均值={stats['mean']:+7.2f}bps  "
                  f"t={stats['t']:+5.2f}  MDE={stats['mde']:6.1f}")

    # ---- 只留有特徵的事件，用 75bps/樂觀 當主結果距離做分類測試 ----
    feat_trades = [r for r in trades if r["features"] is not None]
    print(f"\n分類測試樣本數：{len(feat_trades)}（有 PIT 特徵）")

    if len(feat_trades) < 30:
        print("\n[事實] 有效特徵樣本 < 30，統計檢定力太弱，以下分組結果僅供參考，"
              "不足以支撐任何方向性結論。")

    feature_names = ["bid_drop_ratio", "ask_buildup_ratio", "spread_widen_ratio",
                      "vol_accel_ratio", "imbalance_shift"]

    # 複合分數：info_pressure_z = z(ask_buildup_ratio) + z(-imbalance_shift)
    ask_vals = [r["features"]["ask_buildup_ratio"] for r in feat_trades]
    imb_vals = [-r["features"]["imbalance_shift"] for r in feat_trades]
    ask_z = zscore(ask_vals)
    imb_z = zscore(imb_vals)
    for r, az, iz in zip(feat_trades, ask_z, imb_z):
        r["info_pressure_z"] = az + iz
        r["liquidity_vs_info_score"] = -(az + iz)

    MAIN_DIST = 75
    MAIN_RULE = "touch"

    def group_stats(subset, dist=MAIN_DIST, rule=MAIN_RULE):
        by_day = defaultdict(list)
        n_filled = 0
        for r in subset:
            res = r["fills"][dist][rule]
            if res is not None:
                by_day[r["date"]].append(res["net_bps"])
                n_filled += 1
        stats = day_clustered(by_day)
        fill_rate = n_filled / len(subset) * 100 if subset else 0.0
        return stats, fill_rate, by_day

    def paired_day_diff(by_day_a, by_day_b):
        """同一天在 A、B 兩組都有事件時，算(A均值-B均值)的日聚類 t 檢定。"""
        common_days = sorted(set(by_day_a) & set(by_day_b))
        diffs = [statistics.fmean(by_day_a[d]) - statistics.fmean(by_day_b[d]) for d in common_days]
        if len(diffs) < 2:
            return dict(mean=float("nan"), t=float("nan"), n_days=len(diffs))
        mean = statistics.fmean(diffs)
        sd = statistics.stdev(diffs)
        se = sd / math.sqrt(len(diffs))
        t = mean / se if se > 0 else float("inf")
        return dict(mean=mean, t=t, n_days=len(diffs), sd=sd)

    print("\n" + "=" * 78)
    print(f"【分類特徵】中位數切組（{MAIN_DIST}bps / {MAIN_RULE} 規則, n={len(feat_trades)}）")
    print("=" * 78)
    for feat in feature_names + ["liquidity_vs_info_score"]:
        vals = [r[feat] if feat in r else r["features"][feat] for r in feat_trades]
        med = statistics.median(vals)
        top = [r for r, v in zip(feat_trades, vals) if v >= med]   # 高值組
        bot = [r for r, v in zip(feat_trades, vals) if v < med]    # 低值組
        st_top, fr_top, byday_top = group_stats(top)
        st_bot, fr_bot, byday_bot = group_stats(bot)
        diff = paired_day_diff(byday_top, byday_bot)
        print(f"\n--- {feat}（中位數={med:.3f}）---")
        print(f"  高值組: n={st_top['n_events']:3d} 成交率{fr_top:5.1f}%  "
              f"net={st_top['mean']:+7.2f}bps  t={st_top['t']:+5.2f}  MDE={st_top['mde']:6.1f}")
        print(f"  低值組: n={st_bot['n_events']:3d} 成交率{fr_bot:5.1f}%  "
              f"net={st_bot['mean']:+7.2f}bps  t={st_bot['t']:+5.2f}  MDE={st_bot['mde']:6.1f}")
        print(f"  高−低（同日配對）: 差={diff['mean']:+7.2f}bps  t={diff['t']:+5.2f}  "
              f"共同日數={diff['n_days']}")

    # ---- 主假說檢定：liquidity_vs_info_score 三分位（去掉中段，對比兩端更乾淨）----
    print("\n" + "=" * 78)
    print("【主假說】liquidity_vs_info_score 三分位：流動性驅動 vs 資訊驅動")
    print("=" * 78)
    vals = [r["liquidity_vs_info_score"] for r in feat_trades]
    q1, q2 = np.percentile(vals, [33.3, 66.7])
    liquidity_grp = [r for r, v in zip(feat_trades, vals) if v >= q2]   # 高分=假說流動性驅動
    info_grp = [r for r, v in zip(feat_trades, vals) if v <= q1]        # 低分=假說資訊驅動
    for dist in DISTANCES_BPS:
        for rule in ("touch", "cons"):
            st_l, fr_l, byday_l = group_stats(liquidity_grp, dist, rule)
            st_i, fr_i, byday_i = group_stats(info_grp, dist, rule)
            diff = paired_day_diff(byday_l, byday_i)
            print(f"\n  距離={dist}bps 規則={rule}")
            print(f"    流動性驅動組(頂三分位): n={st_l['n_events']:3d} 成交率{fr_l:5.1f}%  "
                  f"net={st_l['mean']:+7.2f}bps  t={st_l['t']:+5.2f}  MDE={st_l['mde']:6.1f}")
            print(f"    資訊驅動組(底三分位):   n={st_i['n_events']:3d} 成交率{fr_i:5.1f}%  "
                  f"net={st_i['mean']:+7.2f}bps  t={st_i['t']:+5.2f}  MDE={st_i['mde']:6.1f}")
            print(f"    差（配對日）: {diff['mean']:+7.2f}bps  t={diff['t']:+5.2f}  "
                  f"共同日數={diff['n_days']}")

    print("\n完成。以上為單次預先指定切法的結果，不做二次調參去追顯著。")


if __name__ == "__main__":
    main()
