"""momentum-rotation sleeve · 訊號邏輯（12檔開盤突破+量能確認+重新武裝）.

研究 SSOT：reports/research/momentum_breakout_expert_pool_futures_summary.md
回測 SSOT：scripts/research/momentum_breakout_strategy.py（simulate_portfolio_day）

2026-08-13：即時偵測已改用逐筆成交（order/momentum_rotation_tick_marketdata.py
fetch_ticks_as_bars），跟回測simulate_portfolio_day同一種粒度，不再是1分K近似
——process_new_bar本身是通用的「餵一筆觀測值、回不回訊號」函式，不管餵進來的是
1分K還是單筆tick都適用，這次只換了資料來源，這支函式邏輯沒變。等同性驗證見
tests/test_momentum_rotation_tick_marketdata.py（拿真實歷史逐筆資料重放兩條路徑
比對訊號是否一致；驗證過程中抓到並修掉一個process_new_bar跟回測不一致的volume
baseline seeding bug）。

⚠️ 仍然誠實記錄兩個換成逐筆之後**沒辦法在今天開盤前驗證**的殘留風險（跟資料
「來源」本身有關，不是這支檔案的訊號邏輯問題）：
1. Fugle/富邦 `intraday.trades()` 這支REST endpoint真實回傳的筆數上限未知，會不
   會只給「最近N筆」、兩次poll之間漏抓中間tick——市場休市無法對真實API打一次
   確認，只能等開盤後看log。
2. `trades()` 回傳的實際欄位名稱是防禦性猜測，比照candles/tickers兩支endpoint
   已知的命名慣例——如果實際欄位名不同，會安靜地抓不到資料（見
   momentum_rotation_tick_marketdata.py檔頭的失敗模式說明），不是抓錯資料下錯單。

`BreakoutSignal.trigger_time` 2026-08-13新增：momentum_rotation_order.py的
reconcile_once同一次poll若收到多檔候選，改成嚴格按這個欄位（觸發那筆tick/K的
時間戳）排序、先到先贏，不是本次poll裡分數最高的那筆——對齊回測
simulate_portfolio_day逐筆掃描的規則。單元測試見
tests/test_momentum_rotation_entry_selection.py。

標的池：12檔，見 UNIVERSE（跟 momentum_breakout_strategy.UNIVERSE 保持一致，
本檔獨立定義是為了不讓下單層 import 研究腳本——CLAUDE.md 規則：src/order/ 不得
被策略/研究腳本 import，下單層也不應該反過來 import scripts/research/ 底下的
研究程式碼）。
"""

from __future__ import annotations

import os

import statistics
from dataclasses import dataclass, field
from typing import Any

from order.config import load_order_config

# stock_id -> (FinMind式代碼，非下單用；下單用代碼由 resolve_live_futures_symbol 即時解析)
#
# 2026-08-31 換池（v2）。v1 那 12 檔在**它自己宣稱的篩選標準**下只有 2 檔合格：
#   · 5 檔單口保證金超過 sleeve 預算 110,000 → 2026-08-13 那天 22 次進場有 15 次
#     被券商當場拒絕（「交易額度超過資(財)力證明額度」），成功與被拒依價格完全
#     分離、時間上交錯，是乾淨的自然實驗（見 order.affordable() docstring）
#   · 3 檔借券餘額過高（2376 高達 7.77 天成交量），違反 limits-to-arbitrage 的
#     edge 假設——研究文件說「沒有專業套利力量盯著的標的，短期動能不會被抹平」
#   · 其餘期貨端流動性不足（3532 近月日均僅 652 口；3035 8/13 全天只有 195 筆 tick）
#
# v2 的五道門檻（可重跑，資料日 2026-08-28）：
#   ① 有個股期貨（TAIFEX 251 檔）
#   ② 價格 ≤ 407 元 → 單口估保證金 ≤ 110,000（= MARGIN_BUDGET_NTD，事前買得起）
#   ③ 股票日成交額 ≥ 1.5 億（研究原始門檻）
#   ④ 借券餘額 ≤ 2.87 天成交量（最低三分位）
#   ⑤ 個股期貨近月日均 ≥ 2,000 口（回測填價假設可信的下限）
#
# ⚠️ **這個池尚未做過方向檢定。** v1 的結論（原方向與反向毛值都是負的）不能沿用，
# 因為 v1 根本沒照篩選標準選出來；但也不能因此假設 v2 是正的。送真單前必須先累積
# 逐筆資料重驗，逐筆快取目前只有 v1 的 12 檔 + TX。
UNIVERSE: dict[str, str] = {
    "2303": "聯電",     # CCF 期貨近月日均 35,698 口
    "2344": "華邦電",   # FZF 16,174
    "6770": "力積電",   # QZF 15,395
    "1303": "南亞",     # CAF 12,348
    "6182": "合晶",     # PLF  8,775
    "2337": "旺宏",     # DIF  7,058
    "2492": "華新科",   # HBF  4,633  ← v1 留任
    "2324": "仁寶",     # CGF  4,439
    "2301": "光寶科",   # FQF  3,916
    "6147": "頎邦",     # NSF  3,645
    "2609": "陽明",     # DAF  3,572
    "8039": "台虹",     # KIF  3,316
    "3374": "精材",     # QLF  2,541  ← v1 留任
    "2313": "華通",     # FTF  2,485
    "2615": "萬海",     # QXF  2,480
    "3042": "晶技",     # ITF  2,249
    "6173": "信昌電",   # PKF  2,243
    "2481": "強茂",     # GYF  2,236
    "5483": "中美晶",   # NOF  2,145
}

# v1 池（2026-08-12 ~ 2026-08-30）保留供對照，勿刪——8/13 唯一那天實彈跑的是它
UNIVERSE_V1_RETIRED: dict[str, str] = {
    "3017": "奇鋐", "6213": "聯茂", "2345": "智邦", "2383": "台光電",
    "3532": "台勝科", "2455": "全新", "3374": "精材", "6488": "環球晶",
    "2492": "華新科", "2376": "技嘉", "3035": "智原", "2049": "上銀",
}

FUTURES_ROOT: dict[str, str] = {
    "2303": "CC", "2344": "FZ", "6770": "QZ", "1303": "CA", "6182": "PL",
    "2337": "DI", "2492": "HB", "2324": "CG", "2301": "FQ", "6147": "NS",
    "2609": "DA", "8039": "KI", "3374": "QL", "2313": "FT", "2615": "QX",
    "3042": "IT", "6173": "PK", "2481": "GY", "5483": "NO",
}

# ── 池 v3(2026-09-01 · 50萬保證金甜蜜帶)────────────────────────────────
# 篩選:tick成本≤14.7bp ∧ 標準型契約(2000股) ∧ 日盤量中位≥300口 ∧ 保證金≤50萬。
# 全市場342檔重篩+TAIFEX規格表判別標準/小型(QJ/PB/SZ/QI/UQ/VJ都是小型100股,
# 手續費占比35-70bp,剔除)。訊號複製(21天現貨側):池合計+8.44(t=4.08);
# 威剛最佳+9.99(t=3.28);玉晶光平(-3.11,n=28)靠紙上裁決。
# 切換:ORDER_MOMENTUM_ROTATION_POOL=v3(預設 v2,收盤後才由 launcher 切,
# 盤中 crash-restart 不會意外換池——8/31 盤中換池事故的護欄)。
UNIVERSE_V3: dict[str, str] = {
    "3406": "玉晶光",   # LEF 998元 · 牆12.2bp · 764口
    "6488": "環球晶",   # OWF 903 · 13.4bp · 1,822
    "3189": "景碩",     # IX1 823 · 14.7bp · 5,027
    "2455": "全新",     # GUF 481 · 14.5bp · 4,471
    "3105": "穩懋",     # NAF 450 · 15.4bp · 4,016
    "3374": "精材",     # QLF 425.5 · 16.3bp · 5,031 ← v2 留任
    "3260": "威剛",     # NDF 413.5 · 16.7bp · 1,441
    "3532": "台勝科",   # QDF 376.5 · 18.4bp · 1,238
}
FUTURES_ROOT_V3: dict[str, str] = {
    "3406": "LE", "6488": "OW", "3189": "IX", "2455": "GU",
    "3105": "NA", "3374": "QL", "3260": "ND", "3532": "QD",
}

if os.environ.get("ORDER_MOMENTUM_ROTATION_POOL", "v2").strip().lower() == "v3":
    UNIVERSE = UNIVERSE_V3
    FUTURES_ROOT = FUTURES_ROOT_V3


def _yaml_block() -> dict[str, Any]:
    cfg = load_order_config()
    block = (cfg.get("strategies") or {}).get("momentum-rotation")
    return block if isinstance(block, dict) else {}


_YAML = _yaml_block()

BREAKOUT_PCT = float(_YAML.get("breakout_pct") or 0.5)
TRAIL_PCT = float(_YAML.get("trail_pct") or 1.0)
VOL_CONFIRM_MULT = float(_YAML.get("vol_confirm_mult") or 1.5)
REARM_PCT = float(_YAML.get("rearm_pct") or 0.25)
MIN_OVERSHOOT_PCT = float(_YAML.get("min_overshoot_pct") or 0.15)
MIN_VOL_RATIO = float(_YAML.get("min_vol_ratio") or 1.5)
PREEMPT_MULT = float(_YAML.get("preempt_mult") or 2.0)
MARGIN_BUDGET_NTD = float(_YAML.get("margin_budget_twd") or 110_000)
LOT_SIZE = 2000
MARGIN_RATE = 0.135  # 個股期貨保證金粗估率（跟 dayflip_short_signal.estimate_margin_ntd 同一套近似值）


@dataclass
class StockState:
    """單一標的的即時狀態（一天內持續累積，跨poll保留）."""

    open_price: float | None = None
    armed: bool = True
    bar_volumes: list[float] = field(default_factory=list)  # 已收盤1分K的量，供中位數baseline
    last_price: float | None = None
    last_bar_time: str | None = None  # 已處理過的最新一根1分K時間戳，避免重複處理同一根


def new_stock_states() -> dict[str, StockState]:
    return {sid: StockState() for sid in UNIVERSE}


@dataclass
class BreakoutSignal:
    sid: str
    direction: str  # "long" | "short"
    fill: float
    overshoot: float
    vol_ratio: float
    score: float
    trigger_time: str = ""  # 2026-08-13新增：觸發這筆訊號的那根K/那筆tick的時間戳，
    # 供reconcile_once在同一次poll收到多檔候選時「嚴格按時間排序、先到先贏」用，
    # 對齊回測simulate_portfolio_day逐筆掃描的規則（不是挑本次poll裡分數最高的那個）


def estimate_lots(price: float, margin_budget: float = MARGIN_BUDGET_NTD) -> int:
    """用保證金預算換算固定口數（第一階段固定1口，不按保證金打滿，見研究摘要
    「先不要打滿口數」的結論）。這裡仍提供依保證金計算的版本，供之後驗證過真實
    滑價、決定要不要切換到「打滿」模式時使用；預設呼叫端應該用 lots=1，不是
    這個函式的回傳值——見 momentum_rotation_order.py 的 LOTS 常數。"""
    margin_per_lot = price * LOT_SIZE * MARGIN_RATE
    if margin_per_lot <= 0:
        return 0
    return int(margin_budget // margin_per_lot)


def process_new_bar(state: StockState, bar_time: str, o: float, h: float, l: float, c: float, v: float) -> BreakoutSignal | None:
    """餵入一根已收盤的1分K，更新該標的狀態，回傳這根K是否觸發突破訊號.

    對應回測（find_day_events）的逐筆邏輯，改用bar close當「這根K的代表價格」、
    bar volume當「這根K的量」——是tick版邏輯的1分K近似，見檔案頂部docstring的
    已知落差說明。
    """
    if bar_time == state.last_bar_time:
        return None  # 同一根K已處理過，避免重複觸發
    state.last_bar_time = bar_time
    state.last_price = c

    if state.open_price is None:
        state.open_price = o
        return None  # 第一根K只拿來定開盤價，不判斷突破——2026-08-13發現：這裡原本
        # 會把開盤那根的volume也塞進bar_volumes，導致第二根候選的量能baseline用的是
        # 「開盤量」而不是回測simulate_portfolio_day真正用的「空歷史預設1.0」，兩邊
        # 從第一筆候選訊號就可能不一致（見tests/test_momentum_rotation_tick_marketdata.py
        # 用真實逐筆資料重放時抓到的實際案例）。這個落差在1分K版本上線期間就已經存在，
        # 不是這次改逐筆才有的新bug，藉著這次驗證一併修掉。

    long_trigger = state.open_price * (1 + BREAKOUT_PCT / 100.0)
    short_trigger = state.open_price * (1 - BREAKOUT_PCT / 100.0)
    rearm_hi = state.open_price * (1 + REARM_PCT / 100.0)
    rearm_lo = state.open_price * (1 - REARM_PCT / 100.0)

    signal: BreakoutSignal | None = None

    if not state.armed:
        if rearm_lo <= c <= rearm_hi:
            state.armed = True
    else:
        baseline = max(statistics.median(state.bar_volumes), 1e-9) if state.bar_volumes else 1.0
        price_hits_long = c >= long_trigger
        price_hits_short = c <= short_trigger
        if (price_hits_long or price_hits_short) and v >= VOL_CONFIRM_MULT * baseline:
            direction = "long" if price_hits_long else "short"
            fill = long_trigger if direction == "long" else short_trigger
            overshoot = abs(c - fill) / state.open_price * 100.0
            vol_ratio = v / baseline
            if overshoot >= MIN_OVERSHOOT_PCT and vol_ratio >= MIN_VOL_RATIO:
                signal = BreakoutSignal(
                    sid="",  # 呼叫端補上
                    direction=direction, fill=float(fill),
                    overshoot=overshoot, vol_ratio=vol_ratio,
                    score=overshoot * vol_ratio,
                    trigger_time=bar_time,
                )

    state.bar_volumes.append(v)
    return signal


def trailing_stop_hit(direction: str, peak_trough: float, current_price: float, trail_pct: float = TRAIL_PCT) -> tuple[bool, float]:
    """回傳 (是否觸發, 停利價)."""
    if direction == "long":
        stop = peak_trough * (1 - trail_pct / 100.0)
        return current_price <= stop, stop
    stop = peak_trough * (1 + trail_pct / 100.0)
    return current_price >= stop, stop


def position_ret_pct(direction: str, fill: float, exit_price: float) -> float:
    if direction == "long":
        return (exit_price - fill) / fill * 100.0
    return (fill - exit_price) / fill * 100.0
