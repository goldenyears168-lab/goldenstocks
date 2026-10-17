"""momentum-rotation sleeve · order state machine（12檔單槽位輪動+動態搶佔）.

研究 SSOT：reports/research/momentum_breakout_expert_pool_futures_summary.md
訊號邏輯：momentum_rotation_signal.py（12檔開盤突破+量能確認+重新武裝+搶佔評分）

⚠️ **此 sleeve 預設 dry-run，比照 repo 其餘 sleeve 慣例（leading-dip/songshan/
expert-pool/detach-gate），不是 dayflip-futures-short 那種「使用者明確要求不
dry-run-first」的例外**。要送真實委託單，需要使用者另外明確把
ORDER_MOMENTUM_ROTATION_AUTO_SUBMIT 設為 1（且 ORDER_MASTER_ENABLED 也要開）。
2026-08-12 使用者決策：第一階段固定每檔 1 口，不按保證金打滿（回測的高報酬率
建立在保證金打滿+極端樂觀成本假設上，已知不可信，見研究摘要「可信的數字」段落）。

單一交易日狀態機（存於 data/order/momentum_rotation_ledger.json）：
  每個標的獨立追蹤 armed/rearm 狀態；同一時間至多一個持倉（單槽位）。
  持倉觸發移動停利、時間到強制平倉、或有分數≥進場分數2倍的新訊號時觸發搶佔平倉。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import stock_db
from order.config import load_order_config
from order.fubon_futopt_orders import (
    FutOptResolvedOrder,
    fetch_real_fill_price,
    get_futopt_order_results,
    pick_futopt_account,
    place_futopt_order,
)
from order.fubon_session import FubonSession
from order.json_state import atomic_write_json, load_json_or_default
from order.momentum_rotation_hedge import hedge_enabled
from order.momentum_rotation_signal_v3 import (
    DISASTER_STOP_PCT,
    HOLD_MIN,
    MAX_SPREAD_TICKS,
    MICRO_HOLD_SEC,
    RULE_V3,
    RULE_V3M,
    Micro30State,
    V3State,
    signal_v3_enabled,
    v3_mode,
    v3_source,
)
from order.momentum_rotation_signal_v3 import (
    INDEX_ROOT as V3_INDEX_ROOT,
)
from order.momentum_rotation_signal_v3 import (
    tick_size as v3_tick_size,
)
from order.momentum_rotation_signal import (
    FUTURES_ROOT,
    LOT_SIZE,
    MARGIN_BUDGET_NTD,
    MARGIN_RATE,
    PREEMPT_MULT,
    UNIVERSE,
    BreakoutSignal,
    StockState,
    position_ret_pct,
    process_new_bar,
    trailing_stop_hit,
)

_TZ = ZoneInfo("Asia/Taipei")
_LEDGER_PATH = stock_db.DATA_DIR / "order" / "momentum_rotation_ledger.json"
_BROADCAST_PATH = stock_db.DATA_DIR / "order" / "momentum_rotation_broadcast.json"
_KILL_SWITCH_PATH = stock_db.DATA_DIR / "order" / "momentum_rotation_kill_switch.json"
# 2026-08-30：反向轉換器。訊號規則完全不動，只在**送單那一刻**把方向翻過來
# （long→short / short→long）。fill 價（突破門檻價）與 score 都不變，因為那是
# 「在這個價位觸發」這件事本身，跟站哪一邊無關。
#
# 由 :8770 儀表板的按鈕寫入，reconcile_once 每輪 poll（≈20 秒）重讀一次，所以
# 按下去約 20 秒內生效。跟 kill switch 一樣用 date 欄位自然過期——隔天沒人重按
# 就自動回到 env 預設，避免「昨天翻過去忘了翻回來」這種靜默狀態殘留。
_REVERSE_PATH = stock_db.DATA_DIR / "order" / "momentum_rotation_reverse.json"
_TRUE = frozenset({"1", "true", "yes", "on"})

LOTS = 1  # 第一階段固定1口，不按保證金打滿（見檔案頂部 docstring）
ENTRY_WINDOW_START = "08:45"
FORCE_CLOSE_HHMM = "13:40"

# 2026-08-30：刻意重現 2026-08-13 的 anchor bug（使用者指定）。
#
# 8/13 當天 fetch_ticks_as_bars 有解析 bug（intraday.trades() 回傳微秒 epoch 整數，
# 解析器當 ISO 字串處理→每筆都被丟棄），08:45–09:13 抓到 0 筆 tick，state.open_price
# 一路是 None；bug 修好後 anchor 被「當下第一筆 tick」（09:1x 的盤中價）種下。
# 那天跑的不是「開盤動能突破」，是「從資料管線復活那一刻起算的 0.5% 突破」。
#
# 原始 bug 的觸發時間取決於當天部署修補的時刻，本質上不可重現。這裡改成一個
# **決定性**的等價版本：丟棄所有早於 ANCHOR_SEED_AFTER 的 bar，讓 open_price 被
# 該時刻之後的第一筆 tick 種下。設成 "09:15" 就對應 8/13 的實際情況。
#
# 空字串／未設 = 關閉 = 正常行為（用真實 08:45 開盤價當 anchor）。
ANCHOR_SEED_AFTER = os.environ.get("ORDER_MOMENTUM_ROTATION_ANCHOR_SEED_AFTER", "").strip()

# 2026-08-30：兩腿都強制市價 IOC（使用者指定：「市價單才能確保同時成交」）。
#
# ⚠️ 代價已知並已告知：8/13 在流動性好得多的**近月**用市價，實測進場滑價最高
# 2.73%（正是當天盤中把市價改成限價的原因）。次月流動性只有近月的 16~70%，
# 滑價只會更大。開這個旗標等於放棄 _compute_marketable_limit_price 的價格上限。
#
# 開啟時 price_source 標記成 "market_forced"，且**仍然做成交確認**——市價 IOC
# 在流動性差的次月一樣可能不成交，不確認就會建出幻影部位、讓對沖 rollback 失效。
# 這跟 8/13 之前的市價單時代不同（那時完全不確認）。
FORCE_MARKET = os.environ.get("ORDER_MOMENTUM_ROTATION_FORCE_MARKET", "0").strip().lower() in _TRUE
FREEZE_RULE = "mr1_bo05_tr10_vc15_rearm025_mos015_mvr15_pm20_lot1_fc1340_cf5_dl3"

# 2026-08-31：v3 訊號（盲點修正版，見 momentum_rotation_signal_v3.py 檔頭對照表）。
# 訊號狀態刻意放在**行程記憶體**（不進 ledger，bar 序列太大）；worker 盤中重啟時
# 由 REST 全日逐筆重建，行為與 v1 的重啟等價。跨日由 V3State.date 自動重置。
_V3 = V3State()
_V3M = Micro30State()   # 期貨 tape 實例(口=2000):source=futures 時當觸發;
                        # source=spot 時只餵不觸發,供 fut_confirm 確認欄位
_V3M_SPOT = Micro30State(unit_shares=1000.0,               # 現貨(張=1000 股)
                         entry_end_s=13 * 3600 + 24 * 60 + 59)  # 避開 13:30 收盤撮合列印
# 2026-08-31 安全閘門的每日快取(處置名單/漲跌停價),lazy load
_SAFETY_CACHE: dict[str, Any] = {"date": None, "disposal": set(), "limits": {}}


def _safety_gates() -> dict[str, Any]:
    """處置名單 + 漲跌停價,每日載一次(唯讀)。任何失敗 → 空集合(fail-open:
    這是品質過濾;dry-run 下寧可多看訊號,live 前應改 fail-closed)。"""
    today = _today()
    if _SAFETY_CACHE["date"] == today:
        return _SAFETY_CACHE
    disposal: set[str] = set()
    limits: dict[str, tuple[float, float]] = {}
    try:
        import csv as _csv

        with open(stock_db.DATA_DIR / "disposal" / "disposal_windows.csv", encoding="utf-8") as f:
            for r in _csv.DictReader(f):
                if str(r.get("start", "")) <= today <= str(r.get("end", "")):
                    disposal.add(str(r.get("stock_id", "")))
    except Exception:  # noqa: BLE001
        pass
    try:
        import sqlite3 as _sq

        conn = _sq.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
        q = ",".join("?" * len(UNIVERSE))
        rows = conn.execute(
            f"SELECT stock_id, close, MAX(trade_date) FROM stock_daily_bars "
            f"WHERE stock_id IN ({q}) AND trade_date < ? GROUP BY stock_id",
            (*UNIVERSE, today)).fetchall()
        for sid, close, _d in rows:
            if close:
                limits[sid] = (close * 1.10, close * 0.90)
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    _SAFETY_CACHE.update(date=today, disposal=disposal, limits=limits)
    return _SAFETY_CACHE


def _yaml_block() -> dict[str, Any]:
    cfg = load_order_config()
    block = (cfg.get("strategies") or {}).get("momentum-rotation")
    return block if isinstance(block, dict) else {}


_YAML = _yaml_block()

# 2026-08-12 補上：dayflip-short-poll 2026-08-10 真實事故（券商帳戶額度問題導致
# worker一天內對API打了44次注定失敗的重試）加了ENTRY_FAILURE_LIMIT，本sleeve原本
# 沒有同類保護——這裡補上進場端的連續失敗熔斷。出場端故意不設上限、不會「放棄」
# （見_close_position：出場失敗必須持續重試，放棄等於留下一筆沒人管的曝險，
# 比繼續重試危險得多，兩者不能套用同一套邏輯）。
MAX_CONSECUTIVE_ENTRY_FAILURES = int(_YAML.get("max_consecutive_entry_failures") or 5)
# 每日虧損上限（trades_today的ret_pct加總，非精確金額——固定1口下用%近似排序
# 影響已經足夠明確）。達到就停止新進場，且若當下有持倉立刻強制平倉，不等13:40。
DAILY_LOSS_LIMIT_PCT = float(_YAML.get("daily_loss_limit_pct") or -3.0)

# 2026-08-12 補上：momentum-rotation-watch（盤中每15分鐘讀log/ledger的headless
# claude agent）判斷異常時寫這個檔案，reconcile_once下一輪poll（≈20秒後）就會讀到
# 並轉成跟halted一樣的強制平倉+擋新進場路徑——AI只負責「判斷+寫旗標」，實際平倉
# 手續全部沿用_close_position既有的重試/紀錄邏輯，不讓AI直接呼叫下單API。
# 用date欄位自然過期（不用在狀態機裡刪檔案，避免刪檔race condition），隔天
# ledger重置後舊檔案的date對不上today()就自動失效。


def kill_switch_reason() -> str | None:
    payload = load_json_or_default(_KILL_SWITCH_PATH, {})
    if payload.get("date") != _today():
        return None
    reason = payload.get("reason")
    return str(reason) if reason else None


def reverse_mode() -> tuple[bool, str]:
    """反向轉換器目前狀態，回傳 (是否反向, 來源)。

    優先序：今天的儀表板覆寫 > .env 預設 > 關。每輪 poll 重讀，不快取——按鈕
    按下去要在下一輪（≈20 秒）生效，快取會讓它看起來壞掉。
    """
    payload = load_json_or_default(_REVERSE_PATH, {})
    if payload.get("date") == _today() and "reversed" in payload:
        src = f"dashboard@{str(payload.get('set_at') or '?')[11:19]}"
        return bool(payload.get("reversed")), src
    return _env_truthy("ORDER_MOMENTUM_ROTATION_REVERSE"), "env"


def set_reverse_mode(on: bool, *, by: str = "dashboard") -> dict[str, Any]:
    """儀表板按鈕呼叫。只寫旗標檔，**不碰任何部位**——已持有的部位不會因為切換
    方向而被平掉或反手，切換只影響之後的新進場。要處理既有部位請用 kill switch。
    """
    payload = {"date": _today(), "reversed": bool(on),
               "set_at": datetime.now(tz=_TZ).isoformat(timespec="seconds"), "by": by}
    atomic_write_json(_REVERSE_PATH, payload, indent=1)
    return payload


def set_kill_switch(reason: str, *, by: str = "dashboard") -> dict[str, Any]:
    """強制終止：寫 kill switch 旗標。reconcile_once 下一輪讀到就 halted=True，
    **有持倉會立刻強制平倉**（走 _close_position 既有的重試邏輯），之後不再進場。

    注意這不會殺掉 worker 行程——launchd 是 KeepAlive，殺了也會被拉起來。這裡是
    讓狀態機停止動作，比殺行程乾淨（殺行程會留下沒人管的部位）。
    """
    payload = {"date": _today(), "reason": str(reason)[:200],
               "set_at": datetime.now(tz=_TZ).isoformat(timespec="seconds"), "by": by}
    atomic_write_json(_KILL_SWITCH_PATH, payload, indent=1)
    return payload


def clear_kill_switch() -> None:
    atomic_write_json(_KILL_SWITCH_PATH, {"date": _today(), "reason": None}, indent=1)


def _hm_now() -> str:
    return datetime.now(tz=_TZ).strftime("%H:%M")


def _today() -> str:
    return datetime.now(tz=_TZ).strftime("%Y-%m-%d")


def in_momentum_rotation_trade_window(hm: str | None = None, *, weekday: int | None = None) -> bool:
    """個股期貨日盤，週一至週五，無夜盤（比照 dayflip_short_order 同構檢查）."""
    hm = hm or _hm_now()
    if weekday is None:
        weekday = datetime.now(tz=_TZ).weekday()
    if weekday > 4:
        return False
    return "08:44" <= hm <= "13:41"


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUE


def sleeve_auto_submit_enabled() -> bool:
    """預設 0（dry-run-first，比照 repo 大多數 sleeve；跟 dayflip-futures-short 的
    預設 1 不同，見檔案頂部 docstring 說明差異理由）。"""
    raw = os.environ.get("ORDER_MOMENTUM_ROTATION_AUTO_SUBMIT", "0")
    return str(raw).strip().lower() in _TRUE


@dataclass
class PositionState:
    sid: str | None = None
    direction: str | None = None
    fill: float | None = None
    entry_score: float | None = None
    peak_trough: float | None = None
    entry_order_no: str | None = None
    entry_time: str | None = None
    fill_source: str | None = None  # "real"（broker實際成交價）|"theoretical_fallback"
    # 2026-08-13新增：查不到真實成交價時的fallback必須留痕跡，不能悄悄退回理論價
    # 卻讓ret_pct看起來像是精確數字——見fetch_real_fill_price docstring的事故記錄。
    # 2026-08-30新增：雙腿。使用者指定的配置是**訊號看近月、主單下次月、反向單
    # 留近月**，所以主腿代碼不再等於 live_symbols[sid]（那是近月），必須自己記。
    # 對沖關閉時 main_symbol = 近月、hedge_* 全部 None（維持原行為）。
    main_symbol: str | None = None
    entry_reversed: bool = False  # 2026-08-30：這筆是不是在反向轉換器開啟時進場的
    signal_version: str | None = None  # 2026-08-31："v3" = 相對大盤 z 訊號進場（時間出場
    # + 災難停損，不走移動停利/搶佔）。None/"v1" = 舊開盤突破。出場路徑依這個欄位
    # 決定，不依環境旗標——盤中切旗標不會讓既有部位換出場邏輯。
    hedge_symbol: str | None = None
    hedge_order_no: str | None = None
    hedge_fill: float | None = None
    hedge_closed: bool = False  # 對沖腿已平倉但主腿還沒——避免重試時把對沖腿平第二次
    # （對已平倉的腿再送一張 Close，在 broker 端會變成反向新倉，不是 no-op）


@dataclass
class LedgerState:
    date: str
    position: dict[str, Any] = field(default_factory=lambda: asdict(PositionState()))
    stock_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    live_symbols: dict[str, str] = field(default_factory=dict)
    hedge_symbols: dict[str, str] = field(default_factory=dict)  # 2026-08-30：次月代碼快取
    trades_today: list[dict[str, Any]] = field(default_factory=list)
    consecutive_entry_failures: int = 0
    halted: bool = False
    halted_reason: str | None = None
    updated_at: str | None = None
    rule_version: str | None = None


def _fresh_stock_states() -> dict[str, dict[str, Any]]:
    return {sid: asdict(StockState()) for sid in UNIVERSE}


def load_ledger() -> LedgerState:
    today = _today()
    raw = load_json_or_default(_LEDGER_PATH, {})
    if raw.get("date") == today:
        merged = {**asdict_default(), **raw}
        state = LedgerState(**merged)
        # 2026-08-31：UNIVERSE 換池時，當天既有 ledger 的 stock_states 只有舊池那幾檔，
        # `{**default, **raw}` 會讓舊的整個蓋掉新的 → _feed_new_bars 迭代新 UNIVERSE 時
        # `state.stock_states[sid]` 直接 KeyError，worker 每個 poll 週期都拋一次。
        # （2026-08-31 11:02 盤中換 v1→v2 池時實際發生，interval=1s 所以每秒一次。）
        # 這裡補齊缺的 key、並丟掉已不在池內的殘留，讓換池在盤中也是安全的。
        for sid in UNIVERSE:
            if sid not in state.stock_states:
                state.stock_states[sid] = asdict(StockState())
        # ⚠️ 清掉殘留時**絕不能刪掉持倉中那一檔**——換池當下若還抱著舊池標的，
        # 平倉路徑（force_close / halted_close / trail_stop）都要讀
        # state.stock_states[pos["sid"]]，刪掉它 = 部位變成平不掉的孤兒。
        # （2026-08-31 11:06 實際踩到：ledger 抱著舊池 2376，清理後每個 poll 都
        # KeyError: '2376'，強制平倉分支直接掛掉。）
        held = state.position.get("sid")
        if held and held not in state.stock_states:
            # 前一輪已經把它刪掉並存檔了 → 這裡要**補建**，不能只是「保留」。
            state.stock_states[held] = asdict(StockState())
        for sid in list(state.stock_states):
            if sid not in UNIVERSE and sid != held:
                del state.stock_states[sid]
        return state
    return LedgerState(date=today, stock_states=_fresh_stock_states())


def asdict_default() -> dict[str, Any]:
    return asdict(LedgerState(date=_today(), stock_states=_fresh_stock_states()))


def save_ledger(state: LedgerState) -> None:
    state.updated_at = datetime.now(tz=_TZ).isoformat()
    state.rule_version = ((RULE_V3M if v3_mode() == "micro30" else RULE_V3)
                          if signal_v3_enabled() else FREEZE_RULE)
    atomic_write_json(_LEDGER_PATH, asdict(state), indent=1)


def _write_broadcast(state: LedgerState, extra: dict[str, Any] | None = None) -> None:
    """寫給 port 8770 儀表板讀的即時狀態快照（schema momentum-rotation-broadcast-v1）.

    ⚠️ 儀表板讀取端尚未確認接法——本檔只負責誠實寫出 JSON，實際加一個新分頁
    讀這個檔案，需要找到 port 8770 服務本身的程式碼位置後另外處理。
    """
    payload = {
        "schema": "momentum-rotation-broadcast-v1",
        "date": state.date,
        "updated_at": datetime.now(tz=_TZ).isoformat(),
        "position": state.position,
        "trades_today": state.trades_today,
        "stock_armed": {sid: st.get("armed") for sid, st in state.stock_states.items()},
        "dry_run": not sleeve_auto_submit_enabled(),
        "halted": state.halted,
        "halted_reason": state.halted_reason,
        "consecutive_entry_failures": state.consecutive_entry_failures,
        "daily_pnl_pct": round(sum(t["ret_pct"] for t in state.trades_today), 3) if state.trades_today else 0.0,
        # 2026-08-30：儀表板兩顆按鈕的當前狀態。worker 每輪 poll 重寫，所以 UI 顯示的
        # 是「worker 實際看到的值」，不是「按鈕以為自己設了什麼」——按下去沒生效時
        # 這個差異就是第一個線索。
        "reversed": reverse_mode()[0],
        "reverse_source": reverse_mode()[1],
        "hedge_enabled": hedge_enabled(),
        "force_market": FORCE_MARKET,
        "anchor_seed_after": ANCHOR_SEED_AFTER or None,
        "signal_v3": signal_v3_enabled(),
        "v3_mode": v3_mode() if signal_v3_enabled() else None,
    }
    if extra:
        payload.update(extra)
    atomic_write_json(_BROADCAST_PATH, payload, indent=1)


def _resolve_live_symbol(session: FubonSession, sid: str, state: LedgerState) -> str | None:
    cached = state.live_symbols.get(sid)
    if cached:
        return cached
    from order.dayflip_short_order import resolve_live_futures_symbol

    root = FUTURES_ROOT.get(sid)
    if not root:
        return None
    resolved = resolve_live_futures_symbol(session, root)
    if resolved is None:
        return None
    live_symbol, _name = resolved
    state.live_symbols[sid] = live_symbol
    return live_symbol


def _feed_new_bars(
    session: FubonSession, state: LedgerState,
) -> tuple[list[BreakoutSignal], dict[str, Any] | None]:
    """對12檔各自抓最新逐筆成交（每筆自己就是一根寬度0的K），餵進process_new_bar，
    收集這次poll新產生的訊號.

    2026-08-13：資料來源從1分K（fetch_1m_bars）換成逐筆（fetch_ticks_as_bars）
    ——使用者明確要求比照回測simulate_portfolio_day的逐筆粒度，不要用1分鐘聚合
    近似。等同性驗證見tests/test_momentum_rotation_tick_marketdata.py（拿真實
    歷史逐筆資料重放，過程中也順手抓到並修掉了process_new_bar一個跟回測不一致的
    volume baseline bug，見momentum_rotation_signal.py該處的commit說明）。這裡
    只換了資料來源函式，process_new_bar本身邏輯完全沒變（本來就是「餵一根K/一筆
    tick，回不回訊號」的通用函式，不在乎粒度）。

    若目前有持倉，同時逐筆檢查移動停利是否觸發，不是等這批tick全部餵完才用
    最終價格檢查一次——2026-08-12手動測試（override_hm一次補進整天資料）發現這個
    落差：一次補進多筆時，只看最終價格會跳過中間真正觸發停利的那一筆。這是移動
    停利機制的核心風控，逐筆版本延續同一個修正。

    回傳 (signals, stop_hit)。stop_hit 非 None 時代表持倉已在這批資料中觸發移動
    停利，呼叫端要直接用 stop_hit 的資訊平倉，不要再另外查一次目前價格。

    2026-08-13：資料來源改成「優先websocket 'trades'頻道、新鮮度不足才退回
    REST」（fetch_ticks_as_bars_ws，見 momentum_rotation_websocket_feed.py
    docstring），比照TMF sleeve已在production驗證過的同一套手法。預設
    ORDER_MOMENTUM_ROTATION_WS_MARKETDATA_ENABLED=0（關），開關切換前後
    process_new_bar以下的狀態機邏輯完全不變——ws路徑吐出的shape跟REST路徑
    完全一樣，只是資料來源不同。
    """
    from order.momentum_rotation_tick_marketdata import fetch_ticks_as_bars
    from order.momentum_rotation_websocket_feed import fetch_ticks_as_bars_ws

    held_sid = state.position.get("sid")
    held_direction = state.position.get("direction")
    peak_trough = state.position.get("peak_trough")

    signals: list[BreakoutSignal] = []
    stop_hit: dict[str, Any] | None = None

    live_symbols_by_sid: dict[str, str] = {}
    for sid in UNIVERSE:
        live_symbol = _resolve_live_symbol(session, sid, state)
        if live_symbol:
            live_symbols_by_sid[sid] = live_symbol
    all_live_symbols = list(live_symbols_by_sid.values())

    # 2026-08-31 v3：基準指數 feed（③ 相對大盤）。用 TMF 前月當台指基準——
    # dayflip 線已驗證過「TMF 追蹤同一個加權指數、%報酬層面可代用」。解析結果
    # 快取在 live_symbols["_v3_index"]，跟個股同一套快取生命週期。
    use_v3 = signal_v3_enabled()
    _mode = v3_mode() if use_v3 else ""
    if use_v3:
        # relz30:index 是訊號的一部分;micro30:index 只供 idx_5m 觀測欄位
        idx_symbol = state.live_symbols.get("_v3_index")
        if not idx_symbol:
            from order.dayflip_short_order import resolve_live_futures_symbol

            resolved = resolve_live_futures_symbol(session, V3_INDEX_ROOT)
            if resolved:
                idx_symbol = resolved[0]
                state.live_symbols["_v3_index"] = idx_symbol
        if idx_symbol:
            all_live_symbols = all_live_symbols + [idx_symbol]
            try:
                idx_bars = fetch_ticks_as_bars_ws(session, idx_symbol, all_symbols=all_live_symbols)
                if idx_bars is None:
                    idx_bars = fetch_ticks_as_bars(session, idx_symbol)
            except Exception:  # noqa: BLE001
                idx_bars = None
            if idx_bars:
                if _mode == "micro30":
                    _V3M.feed_index(idx_bars); _V3M_SPOT.feed_index(idx_bars)
                else:
                    _V3.feed_index(idx_bars)

    for sid, live_symbol in live_symbols_by_sid.items():
        try:
            bars = fetch_ticks_as_bars_ws(session, live_symbol, all_symbols=all_live_symbols)
            if bars is None:
                bars = fetch_ticks_as_bars(session, live_symbol)
        except Exception:  # noqa: BLE001
            continue
        if not bars:
            continue
        st = StockState(**state.stock_states[sid])
        is_held = sid == held_sid
        held_is_v3 = is_held and str(state.position.get("signal_version") or "").startswith("v3")
        held_fill = state.position.get("fill")
        new_bars: list[dict[str, Any]] = []
        for bar in bars:
            bar_time = str(bar.get("t") or "")
            if not bar_time or bar_time <= (st.last_bar_time or ""):
                continue
            # 2026-08-30：刻意重現 8/13 anchor bug（見 ANCHOR_SEED_AFTER docstring）。
            # 丟棄早於門檻的 bar，讓 open_price 被門檻後第一筆 tick 種下，而不是
            # 真實 08:45 開盤價。這個 continue 在 last_bar_time 更新之前，所以被丟掉
            # 的 bar 完全不留痕跡——跟當天「解析器把每筆都丟掉」的行為一致。
            if ANCHOR_SEED_AFTER and bar_time.split("T")[-1][:5] < ANCHOR_SEED_AFTER:
                continue
            try:
                o, h, l, c, v = (
                    float(bar["o"]), float(bar["h"]), float(bar["l"]),
                    float(bar["c"]), float(bar.get("v") or 0),
                )
            except (KeyError, TypeError, ValueError):
                continue

            if is_held:
                st.last_bar_time = bar_time
                st.last_price = c
                new_bars.append(bar)   # 持倉中也要餵 v3，維持 bar 歷史連續
                if held_is_v3:
                    # v3 部位：無移動停利，只有災難停損（時間出場在 reconcile_once）。
                    if held_fill:
                        r = (c - held_fill) / held_fill * (1 if held_direction == "long" else -1) * 100
                        if r <= -DISASTER_STOP_PCT:
                            stop_hit = {"bar_time": bar_time, "stop_price": c,
                                        "peak_trough": peak_trough, "reason": "disaster_stop"}
                            break
                    continue
                if held_direction == "long":
                    peak_trough = max(peak_trough, c)
                else:
                    peak_trough = min(peak_trough, c)
                hit, stop_price = trailing_stop_hit(held_direction, peak_trough, c)
                if hit:
                    stop_hit = {"bar_time": bar_time, "stop_price": stop_price, "peak_trough": peak_trough}
                    break
                continue  # 持倉中的標的unarmed，不判斷新進場訊號，只做停利檢查

            if use_v3:
                new_bars.append(bar)   # v3：進場判斷交給 V3State（分鐘級、相對大盤）
                continue
            sig = process_new_bar(st, bar_time, o, h, l, c, v)
            if sig:
                sig.sid = sid
                signals.append(sig)
        if use_v3 and new_bars:
            if _mode == "micro30":
                for v3sig in _V3M.feed_stock(sid, new_bars):
                    # source=spot 時期貨爆量只記錄(last_trigger→fut_confirm),不觸發
                    if v3_source() == "futures" and not is_held:
                        signals.append(v3sig)
            else:
                for v3sig in _V3.feed_stock(sid, new_bars):
                    if not is_held:
                        signals.append(v3sig)
        state.stock_states[sid] = asdict(st)

    # 2026-08-31 訊號源搬遷:source=spot(預設)時,觸發來自現貨逐筆 ws。
    # 現貨無 REST fallback:feed 不新鮮 → 這輪沒有現貨訊號(fail-safe,dry-run 可見)。
    if use_v3 and _mode == "micro30" and v3_source() == "spot":
        from order.momentum_rotation_spot_feed import get_spot_feed

        feed = None
        try:
            feed = get_spot_feed(session, list(UNIVERSE))
        except Exception:  # noqa: BLE001
            feed = None
        if feed is not None:
            gates = _safety_gates()
            for sid in UNIVERSE:
                try:
                    sbars = feed.drain_as_bars(sid)
                except Exception:  # noqa: BLE001
                    continue
                if not sbars:
                    continue
                for v3sig in _V3M_SPOT.feed_stock(sid, sbars):
                    if sid == held_sid:
                        continue
                    if sid in gates["disposal"]:
                        continue                     # 處置中:分盤撮合,掃單形態無意義
                    lim = gates["limits"].get(sid)
                    if lim and v3sig.fill > 0:
                        up, dn = lim
                        room = (up - v3sig.fill) / v3sig.fill if v3sig.direction == "long"                             else (v3sig.fill - dn) / v3sig.fill
                        if room < 0.005:
                            continue                 # 距停板 <0.5%:ret60/120 轉負+出場延遲
                    # fut_confirm(PIT:只看進場時已知的期貨爆量,過去 30 秒內)
                    ft = _V3M.last_trigger.get(sid)
                    try:
                        _hh, _mm, _ss = v3sig.trigger_time.split(":")
                        _sig_s = int(_hh) * 3600 + int(_mm) * 60 + int(_ss)
                        v3sig.fut_confirm = (ft is not None and 0 <= _sig_s - ft <= 30)
                    except (ValueError, AttributeError):
                        v3sig.fut_confirm = None
                    signals.append(v3sig)

    if held_sid:
        state.position["peak_trough"] = peak_trough
    return signals, stop_hit


def _compute_marketable_limit_price(bid: float, ask: float, buy_sell: str) -> float:
    """2026-08-13：改掉市價單，市價IOC完全沒有價格保護，真金實測進場滑價最高
    2.73%（見_place_order docstring的事故記錄）。這裡沒有真正的五檔深度資料
    （見fetch_live_quote的已知限制），用Level-1 bid/ask + 緩衝逼近「買二/賣二」
    的效果——緩衝至少取一個spread寬度（模擬跨過大約2檔深度），下限0.1%避免
    spread=0這種退化情況。Buy單掛在ask之上、Sell單掛在bid之下，讓IOC限價單
    仍然有很高機率成交，但成交價有硬上限/下限，不會再無限追價。
    """
    spread = max(ask - bid, ask * 0.001)
    if buy_sell == "Buy":
        return ask + spread
    return bid - spread


def _place_order(
    session: FubonSession, account: Any, live_symbol: str, buy_sell: str,
    order_type: str, live: bool, user_def: str,
) -> dict[str, Any]:
    """2026-08-13從市價單(IOC)改成限價單(IOC)：使用者發現當天4筆交易滑價嚴重
    （進場成交價普遍比理論觸發價貴0.5%~2.7%），根因是market order完全沒有價格
    上限，追多遠都會成交。改法：先查即時報價(fetch_live_quote)，用
    _compute_marketable_limit_price算出一個「大概率成交、但有價格硬上限」的
    限價，仍然用IOC（沒成交就立刻取消，不留掛單風險）。查不到報價（quote() API
    失敗）才退回原本的市價單當fallback——這個fallback會在回傳值裡用
    "price_source":"market_fallback_no_quote"標記，不悄悄假裝成限價單成功。
    """
    from order.momentum_rotation_tick_marketdata import fetch_live_quote

    # 2026-08-30：FORCE_MARKET 開啟時完全跳過報價/限價路徑，直接市價 IOC。
    # price_source 用獨立標記 "market_forced"，不跟 "market_fallback_no_quote"
    # 混淆——後者是「查不到報價的降級」，前者是「使用者刻意指定」，兩者在事後
    # 檢討時意義完全不同，而且只有前者要做成交確認（見 _needs_fill_check）。
    if FORCE_MARKET:
        order = FutOptResolvedOrder(
            symbol=live_symbol, buy_sell=buy_sell, lot=LOTS,
            price=None, price_type="market", time_in_force="ioc",
            order_type=order_type, market_type="future", user_def=user_def,
            session_date=_today(),
        )
        res = place_futopt_order(session, order, acc=account, dry_run=not live)
        res["price_source"] = "market_forced"
        return res

    quote = None
    try:
        quote = fetch_live_quote(session, live_symbol)
    except Exception:  # noqa: BLE001
        quote = None

    if quote is None:
        order = FutOptResolvedOrder(
            symbol=live_symbol, buy_sell=buy_sell, lot=LOTS,
            price=None, price_type="market", time_in_force="ioc",
            order_type=order_type, market_type="future", user_def=user_def,
            session_date=_today(),
        )
        res = place_futopt_order(session, order, acc=account, dry_run=not live)
        res["price_source"] = "market_fallback_no_quote"
        return res

    bid, ask = quote
    limit_price = _compute_marketable_limit_price(bid, ask, buy_sell)
    order = FutOptResolvedOrder(
        symbol=live_symbol, buy_sell=buy_sell, lot=LOTS,
        price=limit_price, price_type="limit", time_in_force="ioc",
        order_type=order_type, market_type="future", user_def=user_def,
        session_date=_today(),
    )
    res = place_futopt_order(session, order, acc=account, dry_run=not live)
    res["price_source"] = "limit_from_quote"
    res["quote_bid"] = bid
    res["quote_ask"] = ask
    res["limit_price"] = limit_price
    return res


def _needs_fill_check(res: dict[str, Any]) -> bool:
    """這張單「送單成功」是否**不等於**「真的成交」，因此必須查 broker 成交紀錄。

    - limit_from_quote：限價 IOC，沒撮合到就取消 → 要查
    - market_forced   ：2026-08-30 使用者指定的市價 IOC。市價在流動性差的次月
                        一樣可能不成交，不查就會建出幻影部位、讓對沖 rollback
                        失效 → 要查
    - market_fallback_no_quote：查不到報價時的降級路徑，維持 8/13 以前的行為
                        （不查），不在這次改動範圍內
    """
    return res.get("price_source") in ("limit_from_quote", "market_forced")


def estimated_margin(price: float) -> float:
    """單口估算保證金 = 契約金額 × MARGIN_RATE（跟 signal.estimate_lots 同一套近似）。"""
    return price * LOT_SIZE * MARGIN_RATE


def affordable(sig: BreakoutSignal) -> bool:
    """這筆訊號的單口保證金是否在 sleeve 預算內。

    2026-08-30 新增，根因是 2026-08-13 的實測：那天 22 次進場嘗試有 15 次被券商
    當場拒絕（「交易額度超過資(財)力證明額度(後檯)[8481329]」），而成功與被拒
    **完全依標的價格分離、沒有重疊**：

        成功那批 估保證金  48,465 ~ 106,650
        被拒那批 估保證金 125,685 ~ 843,750

    分界線正好落在 MARGIN_BUDGET_NTD（110,000）上。而且成功與被拒在時間軸上是
    **交錯**的（10:35 一筆成功後面緊接 5 筆被拒、10:58 成功後立刻被拒），所以
    不是「額度被先前部位佔滿」的累積效應，是純粹的單筆金額。

    當時的程式碼完全沒有事前檢查——`LOTS = 1` 是寫死的，`estimate_lots()` 存在
    但沒有任何呼叫端。結果是 3017（估保證金 84 萬）明明不可能成交，仍然被送出
    去 6 次、白白累積 consecutive_entry_failures 逼近熔斷。

    這裡改成**事前過濾**：買不起的標的在選候選階段就排除，讓狀態機去挑下一個
    買得起的，而不是送出去被拒、浪費 API 呼叫與熔斷額度。
    """
    price = float(sig.fill or 0)
    if price <= 0:
        return False
    return estimated_margin(price) <= MARGIN_BUDGET_NTD


def _place_hedge_leg(
    session: FubonSession, account: Any, state: LedgerState, sig: BreakoutSignal,
    hedge_symbol: str, live: bool,
) -> dict[str, Any]:
    """建立反向對沖腿（2026-08-30 起是**近月**，主腿在次月——見 _enter_position）。

    成功回傳 ok=True + hedge_symbol/hedge_order_no/hedge_fill。
    fail-closed：任何一步失敗都回 ok=False，呼叫端必須把主腿回沖掉。
    """
    from order.momentum_rotation_hedge import hedge_side

    if not hedge_symbol:
        return {"ok": False, "error": f"{sig.sid} 對沖腿代碼缺失，放棄本次進場"}

    buy_sell = hedge_side(sig.direction, closing=False)
    try:
        res = _place_order(session, account, hedge_symbol, buy_sell, "New", live, "mromentum_hedge_entry")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"對沖腿送單例外:{str(exc)[:200]}", "hedge_symbol": hedge_symbol}
    if not res.get("ok"):
        return {"ok": False, "error": f"對沖腿送單被拒:{res.get('message') or res.get('error')}",
                "hedge_symbol": hedge_symbol, "raw": res}

    hedge_order_no = str((res.get("data") or {}).get("order_no") or "") or None
    hedge_fill = None
    if live and hedge_order_no:
        hedge_fill = fetch_real_fill_price(session, hedge_order_no, acc=account)
    # 跟主腿同一套判斷：限價IOC送單成功≠真的成交，查不到成交紀錄就當沒成交
    if live and _needs_fill_check(res) and hedge_fill is None:
        return {"ok": False, "no_fill_confirmed": True,
                "error": "對沖腿限價IOC送單成功但查不到真實成交紀錄，視為未成交",
                "hedge_symbol": hedge_symbol, "hedge_order_no": hedge_order_no}

    return {"ok": True, "hedge_symbol": hedge_symbol,
            "hedge_order_no": hedge_order_no, "hedge_fill": hedge_fill, "raw": res}


def _unwind_main_leg(
    session: FubonSession, account: Any, state: LedgerState, sig: BreakoutSignal,
    main_symbol: str, live: bool,
) -> dict[str, Any]:
    """對沖腿建立失敗時，把已成交的主腿立刻回沖平倉。

    這條路徑跑的時候 state.position 還沒被設定（刻意的：能平掉就當這次進場從未
    發生），所以不能走 _close_position，那支依賴 state.position。
    main_symbol 由呼叫端傳入——2026-08-30 起主腿在次月，不能再從 live_symbols
    （近月快取）取。
    """
    buy_sell = "Sell" if sig.direction == "long" else "Buy"
    try:
        res = _place_order(session, account, main_symbol, buy_sell, "Close", live,
                           "mromentum_hedge_rollback")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"主腿回沖例外:{str(exc)[:200]}"}
    if not res.get("ok"):
        return {"ok": False, "error": f"主腿回沖被拒:{res.get('message') or res.get('error')}", "raw": res}
    # 回沖也是限價IOC，一樣要確認真的成交，否則不能宣稱已平倉
    order_no = str((res.get("data") or {}).get("order_no") or "") or None
    if live and _needs_fill_check(res):
        if order_no is None or fetch_real_fill_price(session, order_no, acc=account) is None:
            return {"ok": False, "no_fill_confirmed": True,
                    "error": "主腿回沖單查不到成交紀錄，視為未平倉", "raw": res}
    return {"ok": True, "raw": res}


def _enter_position(
    session: FubonSession, account: Any, state: LedgerState, sig: BreakoutSignal, live: bool,
) -> dict[str, Any]:
    """進場失敗（下單API本身拋例外，非結構化ok=False回應）時：不建立部位（沒有
    曝險風險，可以安全放棄重試）、累加state.consecutive_entry_failures，達到
    MAX_CONSECUTIVE_ENTRY_FAILURES就把state.halted=True（今天不再嘗試新進場）。
    比照dayflip_short_order.ENTRY_FAILURE_LIMIT的教訓（見該常數docstring）。
    """
    # 2026-08-30 使用者指定的雙腿配置：
    #   訊號 → 近月（_feed_new_bars 不變）
    #   主單 → **次月**（帶訊號方向）
    #   反向單 → **近月**
    # 對沖關閉時退回原行為：主單就在近月、沒有反向腿。
    # 2026-08-30 反向轉換器：在送單前把方向翻過來。刻意**就地改 sig.direction**，
    # 讓 reconcile_once 回傳的 result["direction"] 與 log 記的是「實際下出去的那一邊」，
    # 而不是訊號原本那一邊——事後檢討時 log 必須誠實反映帳戶真的做了什麼。
    reversed_on, rev_src = reverse_mode()
    if reversed_on:
        sig.direction = "short" if sig.direction == "long" else "long"

    front_symbol = state.live_symbols.get(sig.sid)
    hedging = hedge_enabled()
    hedge_target: str | None = None
    if hedging:
        from order.momentum_rotation_hedge import resolve_hedge_symbol

        try:
            next_symbol = resolve_hedge_symbol(session, sig.sid, state.hedge_symbols)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"次月合約解析失敗，放棄進場:{str(exc)[:200]}"}
        if not next_symbol:
            # fail-closed：查不到次月就整筆放棄。絕不可以退回「兩腿都在近月」，
            # 那是同商品同月份的反向單＝期交所淨額計算＝直接把主腿平掉，不是對沖。
            return {"ok": False,
                    "error": f"{sig.sid} 查不到次月合約（只有近月在交易），放棄本次進場"}
        main_symbol, hedge_target = next_symbol, front_symbol
    else:
        main_symbol = front_symbol

    # 2026-08-31 v3（⑨ 價差閘門）：進場前看一眼 L1 價差，寬過 MAX_SPREAD_TICKS 個
    # 跳動就放棄這筆——在 2 口 tape 的商品上盲跨未知寬度的價差,是 8/13 滑價事故
    # 的成因之一。查不到報價時**放行**（fail-open）：這是品質過濾不是安全鎖,
    # 查不到報價時 _place_order 本來就會退回有標記的 fallback 路徑。
    if getattr(sig, "z", None) is not None:
        from order.momentum_rotation_tick_marketdata import fetch_live_quote

        try:
            _q = fetch_live_quote(session, main_symbol)
        except Exception:  # noqa: BLE001
            _q = None
        if _q:
            _bid, _ask = _q
            if _bid and _ask and (_ask - _bid) > MAX_SPREAD_TICKS * v3_tick_size((_ask + _bid) / 2):
                return {"ok": False, "skipped": "spread_too_wide",
                        "spread": round(_ask - _bid, 4), "bid": _bid, "ask": _ask,
                        "error": f"L1 價差 {(_ask - _bid):.2f} > {MAX_SPREAD_TICKS} 檔，放棄進場"}

    buy_sell = "Buy" if sig.direction == "long" else "Sell"
    try:
        res = _place_order(session, account, main_symbol, buy_sell, "New", live, "mromentum_entry")
    except Exception as exc:  # noqa: BLE001
        state.consecutive_entry_failures += 1
        if state.consecutive_entry_failures >= MAX_CONSECUTIVE_ENTRY_FAILURES:
            state.halted = True
            state.halted_reason = f"entry_exception_limit_reached({state.consecutive_entry_failures}x):{exc}"
        return {"ok": False, "error": str(exc)[:300], "exception": True,
                "consecutive_entry_failures": state.consecutive_entry_failures}

    state.consecutive_entry_failures = 0  # 成功一次就歸零，跟dayflip同一套邏輯
    if not res.get("ok"):
        return res  # 結構化失敗(ok=False，非例外)：不算進連續失敗熔斷，維持既有no_signal類行為

    entry_order_no = str((res.get("data") or {}).get("order_no") or "") or None
    fill_price = sig.fill
    fill_source = "theoretical_fallback"
    real_price = None
    if live and entry_order_no:
        real_price = fetch_real_fill_price(session, entry_order_no, acc=account)
        if real_price is not None:
            fill_price = real_price
            fill_source = "real"

    # 2026-08-13：改用限價單之後，「送單成功(res.ok=True)」不再等於「真的成交」
    # ——限價IOC沒撮合到會直接取消、零成交，這在市價單時代不會發生（市價單幾乎
    # 保證成交，所以原本的程式碼沒有檢查這件事）。如果送單走的是限價單路徑
    # （price_source=="limit_from_quote"）卻查不到真實成交紀錄，代表這張單很可能
    # 沒真的成交——不能照舊建立state.position，否則ledger會出現一筆broker端根本
    # 不存在的幻影部位，之後想平倉會平掉不存在的東西。這裡當作結構化失敗處理
    # （不計入連續失敗熔斷，因為這不是下單API出錯，是正常的「掛的價格沒吃到」）。
    if live and _needs_fill_check(res) and real_price is None:
        return {
            "ok": False, "no_fill_confirmed": True,
            "error": "限價IOC送單成功但查不到真實成交紀錄，視為未成交，不建立部位",
            "entry_order_no": entry_order_no,
        }

    # 2026-08-30：次月反向對沖腿。主腿已確認成交，這裡補上反向腿。
    # 原則——**雙腿都成交才算數**：對沖腿沒建立起來就立刻把主腿平掉，絕不留
    # 單腿裸露曝險（使用者要的是「實驗期間只付成本、不承擔方向風險」）。
    hedge_symbol = hedge_order_no = None
    hedge_fill = None
    if hedging:
        hedge_res = _place_hedge_leg(session, account, state, sig, hedge_target, live)
        if not hedge_res.get("ok"):
            unwind = _unwind_main_leg(session, account, state, sig, main_symbol, live)
            if unwind.get("ok"):
                # 主腿已平掉，帳上乾淨，當成一次結構化失敗（不計入連續失敗熔斷，
                # 因為主腿本身送單是成功的，失敗的是對沖腿）
                return {"ok": False, "hedge_failed": True, "main_unwound": True,
                        "error": "對沖腿未成交，主腿已回沖平倉，不建立部位",
                        "hedge_res": hedge_res, "unwind_res": unwind,
                        "entry_order_no": entry_order_no}
            # ⚠️ 最糟情況：對沖腿沒成交、主腿也平不掉 → 現在有一筆裸露部位。
            # 必須把它寫進 ledger（否則變成沒人管的孤兒曝險，下一輪 poll 找不到它），
            # 並且 halted=True 讓 reconcile_once 的熔斷分支每 20 秒重試平倉。
            state.position = asdict(
                PositionState(
                    sid=sig.sid, direction=sig.direction, fill=fill_price,
                    entry_score=sig.score, peak_trough=fill_price,
                    entry_order_no=entry_order_no,
                    entry_time=datetime.now(tz=_TZ).isoformat(),
                    fill_source=fill_source, main_symbol=main_symbol,
                    entry_reversed=reversed_on,
                    signal_version=("v3m" if getattr(sig, "mode", "") == "micro30" else "v3")
                    if getattr(sig, "z", None) is not None else "v1",
                )
            )
            state.stock_states[sig.sid]["armed"] = False
            state.halted = True
            state.halted_reason = (
                f"hedge_failed_and_unwind_failed:{sig.sid} 主腿裸露中，"
                f"熔斷分支會持續重試平倉"
            )
            return {"ok": False, "hedge_failed": True, "main_unwound": False,
                    "naked_exposure": True,
                    "error": "對沖腿未成交且主腿回沖失敗——主腿裸露，已 halted 並持續重試平倉",
                    "hedge_res": hedge_res, "unwind_res": unwind}
        hedge_symbol = hedge_res.get("hedge_symbol")
        hedge_order_no = hedge_res.get("hedge_order_no")
        hedge_fill = hedge_res.get("hedge_fill")

    # peak_trough的起點也必須用真實成交價，不是只有記錄用的fill——移動停利
    # (trailing_stop_hit)是從peak_trough往回算門檻，如果起點是偏掉的理論價，
    # 停利價本身也會跟著偏掉，不只是ret_pct事後算錯而已。
    state.position = asdict(
        PositionState(
            sid=sig.sid, direction=sig.direction, fill=fill_price,
            entry_score=sig.score, peak_trough=fill_price,
            entry_order_no=entry_order_no,
            entry_time=datetime.now(tz=_TZ).isoformat(),
            fill_source=fill_source, main_symbol=main_symbol,
            entry_reversed=reversed_on,
            signal_version=("v3m" if getattr(sig, "mode", "") == "micro30" else "v3")
            if getattr(sig, "z", None) is not None else "v1",
            hedge_symbol=hedge_symbol, hedge_order_no=hedge_order_no,
            hedge_fill=hedge_fill,
        )
    )
    # 進場的標的要unarm，跟出場端_close_position對稱（回測simulate_portfolio_day
    # 同一個位置也是這樣做）——沒有這行，這檔標的下一輪poll若價格仍在門檻外會
    # 一直被判定成候選，雖然目前的搶佔邏輯有排除「自己搶自己」（reconcile_once
    # candidates = [s for s in signals if s.sid != pos["sid"]]），狀態本身仍是錯的，
    # 2026-08-12 手動測試（override_hm模擬盤中）第一次跑通後才發現這個漏洞。
    state.stock_states[sig.sid]["armed"] = False
    res["reversed"] = reversed_on
    res["reverse_source"] = rev_src
    return res


def _close_position(
    session: FubonSession, account: Any, state: LedgerState, exit_price: float, reason: str, live: bool,
) -> dict[str, Any]:
    """出場失敗（下單API拋例外）時**絕不放棄重試**——跟_enter_position的熔斷邏輯
    刻意不對稱：進場失敗代表沒有曝險，放棄很安全；出場失敗代表還有一筆真實部位
    沒人管，放棄等於留一筆孤兒曝險，比繼續重試危險得多。因此這裡失敗時：
    - state.position 保持原樣（不清空，下一輪poll的force-close/trail-stop/preempt
      分支會用同一個pos再試一次，reconcile_once本身已經是每20秒重跑一次的迴圈，
      等同天然的重試機制）
    - 不寫入trades_today（這筆還沒真的平倉，不能當成完成的交易記錄）
    - 回傳ok=False+exception=True，呼叫端會把這個action標記出來供告警機制攔截
    """
    pos = state.position

    # 2026-08-30：**先平對沖腿、再平主腿**，順序是刻意的。
    # 兩種失敗模式比較：
    #   先平對沖失敗 → 兩腿都還在 → 仍然是對沖狀態 → 沒有方向曝險，安全重試 ✅
    #   先平主腿失敗 → 主腿還在、對沖已平 → 裸露曝險 ❌
    # 所以對沖腿先走。hedge_closed 旗標避免重試時把已平倉的對沖腿再平一次
    # （對已平倉的腿再送 Close 會變成反向新倉，不是 no-op）。
    if pos.get("hedge_symbol") and not pos.get("hedge_closed"):
        from order.momentum_rotation_hedge import hedge_side

        hedge_bs = hedge_side(pos["direction"], closing=True)
        try:
            h_res = _place_order(session, account, pos["hedge_symbol"], hedge_bs, "Close", live,
                                 f"mromentum_hedge_{reason}")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"對沖腿平倉例外:{str(exc)[:250]}", "exception": True,
                    "close_reason": reason, "leg": "hedge"}
        if not h_res.get("ok"):
            return {"ok": False, "error": f"對沖腿平倉被拒:{h_res.get('message') or h_res.get('error')}",
                    "close_reason": reason, "leg": "hedge", "raw": h_res}
        h_order_no = str((h_res.get("data") or {}).get("order_no") or "") or None
        if live and _needs_fill_check(h_res):
            if h_order_no is None or fetch_real_fill_price(session, h_order_no, acc=account) is None:
                return {"ok": False, "no_fill_confirmed": True, "leg": "hedge",
                        "error": "對沖腿平倉單查不到成交紀錄，視為未平倉，兩腿維持不變",
                        "close_reason": reason}
        pos["hedge_closed"] = True
        state.position = pos
        save_ledger(state)  # 立刻落地：主腿若接著失敗，重試時才知道對沖腿已經平掉

    # 2026-08-30：主腿代碼用進場時記錄的 main_symbol（對沖開啟時是次月），
    # 舊部位沒有這個欄位就退回近月快取，維持向後相容。
    main_symbol = pos.get("main_symbol") or state.live_symbols.get(pos["sid"])
    buy_sell = "Sell" if pos["direction"] == "long" else "Buy"
    try:
        res = _place_order(session, account, main_symbol, buy_sell, "Close", live, f"mromentum_{reason}")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300], "exception": True, "close_reason": reason}

    # 2026-08-13修復：exit_price原本是呼叫端傳進來的理論價（最後觀察tick價/
    # 理論停利價），跟真實broker成交價可能差很多——2026-08-13早上實測4筆交易
    # ret_pct方向全部算錯就是這裡的問題（連帶daily_loss_limit_pct熔斷跟著失準）。
    # 這裡改成優先查broker真實成交價，查不到才退回呼叫端傳入的理論價當fallback，
    # 並且用exit_source留痕跡，不讓fallback悄悄冒充精確數字。
    close_order_no = str((res.get("data") or {}).get("order_no") or "") or None
    real_exit_price = None
    exit_source = "theoretical_fallback"
    if live and close_order_no:
        real_exit_price = fetch_real_fill_price(session, close_order_no, acc=account)

    # 2026-08-13：改用限價單後，出場單也可能送單成功但沒真的成交（限價IOC沒撮合
    # 到就直接取消）。這裡沿用跟_enter_position一樣的判斷，但**動作完全不同**：
    # 進場沒成交=安全不建倉；出場沒成交=部位還在、絕對不能當成已經平倉——照本函式
    # docstring本來的原則，維持state.position不變、不寫trades_today、回傳ok=False
    # 讓下一輪poll的force-close/trail-stop/preempt分支自然重試。
    if live and _needs_fill_check(res) and real_exit_price is None:
        return {
            "ok": False, "no_fill_confirmed": True,
            "error": "限價IOC出場單送單成功但查不到真實成交紀錄，視為未成交，部位維持不變",
            "close_order_no": close_order_no, "close_reason": reason,
        }

    final_exit_price = exit_price
    if real_exit_price is not None:
        final_exit_price = real_exit_price
        exit_source = "real"

    ret_pct = position_ret_pct(pos["direction"], pos["fill"], final_exit_price)
    state.trades_today.append(
        {
            "sid": pos["sid"], "direction": pos["direction"], "fill": pos["fill"],
            "fill_source": pos.get("fill_source"),
            "exit": final_exit_price, "exit_source": exit_source, "ret_pct": ret_pct, "reason": reason,
            "entry_time": pos["entry_time"], "exit_time": datetime.now(tz=_TZ).isoformat(),
            # 2026-08-30：對沖腿留痕。⚠️ ret_pct 只是**主腿**的報酬，不是這組配對的
            # 淨損益——對沖腿的損益要另外從 broker 對帳單取（次月成交價本模組沒有
            # 逐筆追蹤）。有 hedge_symbol 時，帳戶實際損益 ≈ 兩腿相抵後只剩交易成本，
            # 不要拿 ret_pct 當這次實驗的結果讀。
            "hedge_symbol": pos.get("hedge_symbol"),
            "hedge_fill": pos.get("hedge_fill"),
            "reversed": pos.get("entry_reversed", False),
        }
    )
    state.position = asdict(PositionState())
    state.stock_states[pos["sid"]]["armed"] = False  # 出場後需回檔重置才重新武裝

    daily_pnl_pct = sum(t["ret_pct"] for t in state.trades_today)
    if daily_pnl_pct <= DAILY_LOSS_LIMIT_PCT:
        state.halted = True
        state.halted_reason = f"daily_loss_limit_reached({daily_pnl_pct:.2f}%<= {DAILY_LOSS_LIMIT_PCT}%)"
    return res


def reconcile_once(
    *,
    dry_run: bool | None = None,
    override_hm: str | None = None,
    use_session_pool: bool = False,
    session: FubonSession | None = None,
) -> dict[str, Any]:
    state = load_ledger()
    hm = override_hm or _hm_now()
    live = (not dry_run) if dry_run is not None else sleeve_auto_submit_enabled()
    result: dict[str, Any] = {"hm": hm, "live": live}

    if not in_momentum_rotation_trade_window(hm):
        result["action"] = "noop_outside_window"
        return result

    def _acquire_session() -> FubonSession:
        nonlocal session
        if session is not None:
            return session
        if use_session_pool:
            from order.momentum_rotation_session_pool import get_fubon_session

            session = get_fubon_session()
            return session
        from order.fubon_session import connect_fubon

        session = connect_fubon()
        return session

    session = _acquire_session()
    account = pick_futopt_account(session)

    if not state.halted:
        ks_reason = kill_switch_reason()
        if ks_reason:
            state.halted = True
            state.halted_reason = f"ai_kill_switch:{ks_reason}"

    signals, stop_hit = _feed_new_bars(session, state)

    # 2026-08-30：事前過濾買不起的標的（見 affordable() docstring 的 8/13 實測）。
    # 在選候選之前就排除，狀態機才會去挑下一個買得起的，而不是送出去被券商拒絕、
    # 白白累積 consecutive_entry_failures。被濾掉的留在 result 裡供事後檢討——
    # 靜默丟棄會讓「今天怎麼都不進場」變成查不出原因的謎。
    if signals:
        _skipped = [f"{s.sid}@{s.fill:.0f}" for s in signals if not affordable(s)]
        signals = [s for s in signals if affordable(s)]
        if _skipped:
            result["margin_filtered"] = _skipped
            result["margin_budget_twd"] = MARGIN_BUDGET_NTD

    # 1. 持倉中：移動停利已經在_feed_new_bars逐根K棒檢查過了（不是只看最終價格），
    # stop_hit非None代表這批K棒裡已經觸發
    if stop_hit is not None:
        _reason = stop_hit.get("reason") or "trail_stop"   # v3 災難停損共用此路徑
        close_res = _close_position(session, account, state, stop_hit["stop_price"], _reason, live)
        save_ledger(state)
        _write_broadcast(state)
        result.update(action=f"closed_{_reason}", close_res=close_res, stop_hit=stop_hit)
        return result

    # 1.5 v3 部位：時間出場（⑦ 尺度修正——持有固定 HOLD_MIN 分鐘，讓價格移動
    # 有機會走贏來回價差；5分尺度中位移動/成本比 0.77、30分 2.07，見 v3 檔頭）。
    _sv = str(state.position.get("signal_version") or "")
    if state.position.get("sid") and _sv.startswith("v3"):
        et = state.position.get("entry_time")
        try:
            entered = datetime.fromisoformat(et) if et else None
        except (TypeError, ValueError):
            entered = None
        from order.momentum_rotation_signal_v3 import v4_mode as _v4m, V4_HOLD_SEC as _V4H
        _hold_sec = (_V4H if _v4m() else MICRO_HOLD_SEC) if _sv == "v3m" else HOLD_MIN * 60
        if entered and (datetime.now(tz=_TZ) - entered).total_seconds() >= _hold_sec:
            pos = state.position
            cur_price = state.stock_states.get(pos["sid"], {}).get("last_price") or pos["fill"]
            close_res = _close_position(session, account, state, cur_price, "time_exit", live)
            save_ledger(state)
            _write_broadcast(state)
            result.update(action="closed_time_exit", close_res=close_res)
            return result

    # 2. 強制平倉時間到
    if hm >= FORCE_CLOSE_HHMM and state.position.get("sid"):
        pos = state.position
        cur_price = state.stock_states[pos["sid"]].get("last_price") or pos["fill"]
        close_res = _close_position(session, account, state, cur_price, "day_end_forced", live)
        save_ledger(state)
        _write_broadcast(state)
        result.update(action="closed_force_close", close_res=close_res)
        return result

    # 2.5 熔斷中（連續進場失敗達上限，或當日虧損達上限）：若還有殘留持倉，立刻
    # 強制平倉（不等13:40），且不再嘗試任何新進場——見_enter_position/
    # _close_position 對兩種熔斷的觸發邏輯與MAX_CONSECUTIVE_ENTRY_FAILURES/
    # DAILY_LOSS_LIMIT_PCT 的docstring。
    if state.halted:
        if state.position.get("sid"):
            pos = state.position
            cur_price = state.stock_states[pos["sid"]].get("last_price") or pos["fill"]
            close_res = _close_position(session, account, state, cur_price, "halted_close", live)
            save_ledger(state)
            _write_broadcast(state)
            result.update(action="closed_halted", close_res=close_res, halted_reason=state.halted_reason)
            return result
        save_ledger(state)
        _write_broadcast(state)
        result.update(action="noop_halted", halted_reason=state.halted_reason)
        return result

    # 3. 空手且有合格訊號：嚴格按觸發時間排序、取最早那筆進場（不是本次poll裡分數
    # 最高的）——2026-08-13對齊回測simulate_portfolio_day逐筆掃描「先到先贏」的
    # 規則。回測是tick-by-tick即時評估、reconcile_once是每次poll批次拿到一批新
    # 訊號，兩者只有在「同一次poll內剛好有≥2檔不同標的都合格」時才會有差異；拿
    # 全部75天真實逐筆資料重放過，這個情境目前為止從沒發生過（見config/order.yaml
    # notes），但邏輯本身現在對齊，不只是機率低。
    if not state.position.get("sid") and signals:
        # 2026-08-31 v3：z 已波動正規化、跨標的可比（修⑥），多檔同時觸發取 |z| 最大；
        # v1 維持「先到先贏」。用訊號型別（有沒有 z 屬性）判斷，不用環境旗標——
        # 測試裡 mock 訊號時行為才可預測。
        if any(hasattr(s, "z") for s in signals):
            earliest = max(signals, key=lambda s: s.score)
        else:
            earliest = min(signals, key=lambda s: s.trigger_time)
        entry_res = _enter_position(session, account, state, earliest, live)
        save_ledger(state)
        _write_broadcast(state)
        result.update(action="entered", sid=earliest.sid, direction=earliest.direction,
                      score=earliest.score, entry_res=entry_res)
        if getattr(earliest, "z", None) is not None:
            # 2026-08-31:v3 訊號的觀測欄位逐筆進 log(不影響行為,供日後驗證
            # 量縮條件 +4.51/t=5.06 等預註冊項)
            result["v3_meta"] = {"ts": datetime.now(tz=_TZ).isoformat(timespec="milliseconds"),
                                 "mode": getattr(earliest, "mode", None),
                                 "z": round(earliest.z, 3), "rel_bps": round(earliest.rel_bps, 2),
                                 "vol_r1": getattr(earliest, "vol_r1", None),
                                 "vol_r5": getattr(earliest, "vol_r5", None),
                                 "idx_5m": getattr(earliest, "idx_5m", None),
                                 "idx_1m": getattr(earliest, "idx_1m", None),
                                 "amt": getattr(earliest, "amt", None),
                                 "v4": __import__("order.momentum_rotation_signal_v3",fromlist=["v4_mode"]).v4_mode(),
                                 "source": v3_source(),
                                 "fut_confirm": getattr(earliest, "fut_confirm", None)}
            # 2026-09-01:現貨五檔觀察欄位(記錄不行動)。簿類是整條線最大的觀測
            # 盲區——回測沒有歷史簿,只能從 live 開始累積;40 天 OOS 一併裁決
            # 失衡/價差是否帶延續資訊。peek 不建連線,拿不到就記 None。
            try:
                from order.momentum_rotation_spot_feed import peek_spot_feed
                _pf = peek_spot_feed()
                result["v3_meta"]["book"] = _pf.book_snapshot(earliest.sid) if _pf else None
            except Exception:  # noqa: BLE001 -- 觀察欄位絕不能弄死下單路徑
                result["v3_meta"]["book"] = None
        return result

    # 4. 持倉中且有更好的訊號：搶佔。同樣按觸發時間排序，取「時間最早、且分數有
    # 過搶佔門檻」的那一筆——不是批次裡分數最高的那筆。對應回測「每筆候選一來就
    # 立刻判斷夠不夠格搶」，不是「等一批候選到齊後選最強的」。
    if state.position.get("sid") and signals:
        pos = state.position
        if str(pos.get("signal_version") or "").startswith("v3"):
            # v3 不搶佔：時間出場前部位不被打斷。鏡像實測搶佔出場那半是虧的
            # （−17.58 × 459 筆），且 30 分持有下搶佔會讓實際持有退化回 5 分尺度。
            save_ledger(state)
            _write_broadcast(state)
            result["action"] = "noop_no_signal"
            return result
        candidates = sorted(
            (s for s in signals if s.sid != pos["sid"]), key=lambda s: s.trigger_time
        )
        qualified = next((s for s in candidates if s.score >= PREEMPT_MULT * pos["entry_score"]), None)
        if qualified is not None:
            cur_price = state.stock_states[pos["sid"]].get("last_price") or pos["fill"]
            close_res = _close_position(session, account, state, cur_price, "preempted", live)
            entry_res = _enter_position(session, account, state, qualified, live)
            save_ledger(state)
            _write_broadcast(state)
            result.update(action="preempted", new_sid=qualified.sid, score=qualified.score,
                          close_res=close_res, entry_res=entry_res)
            return result

    save_ledger(state)
    _write_broadcast(state)
    result["action"] = "noop_no_signal"
    return result
