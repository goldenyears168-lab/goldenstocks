"""dayflip-post-dump-long v1 · order sleeve（多口多日持倉、共用保證金池30萬）.

規格研究：config/order.yaml 的 strategies.dayflip-post-dump-long notes（含完整
walk-forward驗證歷程、trial registry topic_id見該區塊）。核心：dayflip-futures-
short抓到的隔日沖倒貨事件，倒貨完成後1-5個交易日常有反彈，用滾動窗口相對弱勢
訊號（rolling_relative_dip，見 dayflip_post_dump_long_signal.py）找反彈起點，
移動停利5%（進場以來最高收盤回檔達5%出場，只在收盤前檢查一次——研究證實盤中
連續追蹤反而更差，見 post-dump-long-intraday-exit-precision）或最長持有10個
交易日出場。

2026-08-09 使用者明確定案：不做dry-run先行驗證，程式碼一寫完直接送實單，資金
規模300,000 NTD、單一標的名目曝險上限15%（4.5萬/檔）。這跟其餘sleeve的
dry-run-first慣例不同，是使用者在被明確告知「這支程式碼從零開始寫、沒有實盤
驗證」的風險後做出的知情選擇（比照dayflip-futures-short 2026-08-07的同類型
明確override）。

安全閥：
  - live_submit_guard.assert_live_submit_allowed()（經 place_futopt_order 自動
    呼叫）：ORDER_MASTER_ENABLED=0 時 fail-closed，即使 dry_run=False 也一樣
  - 本檔自己的 sleeve 旗標 ORDER_DAYFLIP_POST_DUMP_LONG_AUTO_SUBMIT
  - 保證金池硬上限 300,000、單一標的曝險硬上限 15%（4.5萬）、每個部位固定1口

跟 dayflip-futures-short 的關鍵差異：短邊是「單一交易日狀態機」（一天一定平倉、
ledger只存當天一筆）；本檔是「多部位、跨日持倉」——ledger 存一個 positions
list，每個部位獨立生命週期（進場日到出場日可能橫跨10個交易日），任何一次poll
都可能同時有「檢查既有部位出場」跟「掃描新進場訊號」兩件事在做。

2026-08-09 使用者追加明確指示：跟 dayflip-futures-short **共用同一筆 300,000
NTD**，不是各自獨立準備一份——呼應 short-long-shared-capital-handoff 那輪研究
（74/74短邊交易的股票同時是長邊候選，57%同一天短邊出場後長邊才進場，共用池
資金效率可比獨立雙池翻倍）。實作方式：本檔在算「可用保證金」時，會額外扣掉
dayflip-short-ledger.json 裡「今天status=entered」的短邊部位保證金——單向讀取
（本檔讀短邊ledger，不寫），完全不改動已經在跑的 dayflip_short_order.py，避免
為了這個新sleeve去動一個已經實單在跑的既有系統。⚠️已知殘留風險（誠實記錄，
不是完美的雙向鎖）：短邊自己完全不知道長邊用了多少保證金——短邊MARGIN_CAP_NTD=
300,000是「單筆交易」的獨立上限檢查，不會因為長邊已經用掉一部份300,000而縮小。
也就是說如果長邊已經用滿300,000、當天短邊又剛好觸發訊號，短邊仍會照樣進場
（自己那筆不超過300,000就會過），兩邊合計會短暫超過名義上的300,000共用上限。
這是刻意接受的風險（短邊本來就設計成很少真的逼近300,000上限），不是遺漏；
之後如果要做到真正雙向即時鎖，需要回頭改 dayflip_short_order.py，現在不做。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import stock_db
from order.config import load_order_config
from order.dayflip_post_dump_long_signal import (
    build_candidates,
    estimate_margin_ntd,
    find_rolling_dip_signal,
    futures_root,
    last_close,
)
from order.dayflip_short_order import (
    _prior_trading_day,
    fetch_last_price,
    fetch_open_price,
    query_real_margin_ntd,
    resolve_live_futures_symbol,
)
from order.fubon_futopt_orders import FutOptResolvedOrder, pick_futopt_account, place_futopt_order
from order.fubon_session import FubonSession
from order.json_state import atomic_write_json, load_json_or_default
from order.oms_lifecycle import build_client_intent_id

_TZ = ZoneInfo("Asia/Taipei")
# 2026-08-09 修正架構債：改用 stock_db.DATA_DIR（同一輪也同步修正了
# dayflip_short_order.py 的 _LEDGER_PATH，見該檔案同一天的註解），跟
# detach-gate/leading-dip 等其餘 sleeve 一致，不再硬寫 PROJECT_ROOT / "data"。
_LEDGER_PATH = stock_db.DATA_DIR / "order" / "dayflip_post_dump_long_ledger.json"
_TRUE = frozenset({"1", "true", "yes", "on"})


def _yaml_block() -> dict[str, Any]:
    cfg = load_order_config()
    block = (cfg.get("strategies") or {}).get("dayflip-post-dump-long")
    return block if isinstance(block, dict) else {}


_YAML = _yaml_block()
_SIZING = _YAML.get("position_sizing") if isinstance(_YAML.get("position_sizing"), dict) else {}

TOTAL_CAPITAL_NTD = float(_SIZING.get("total_capital_ntd") or 300_000)
SINGLE_NAME_CAP_PCT = float(_SIZING.get("single_name_notional_cap_pct") or 15)
LOTS = int(_SIZING.get("lots_per_position") or 1)
LOT_SIZE = int(_SIZING.get("lot_shares") or 2000)
# 2026-08-11：原本直接沿用短邊dayflip_short_signal.FGAP_MIN(7%，跟短邊共用同一個
# 門檻)——使用者問「做多不一定要7%才對」，先用單筆平均驗證證實7%確實比4%好
# （bootstrap 4%贏7%只有26.4%），但那只看單筆平均，不是資金池層級。改用
# capital_simulation.py同款逐日資金排程模擬比較組合表現後，使用者選擇採納
# 4%（總報酬+120.8% vs 現行+90.8%，代價是Sharpe較低0.183 vs 0.218、
# 最大回撤較深-15.6% vs -11.0%——這是知情選擇後的風險/報酬取捨，不是研究
# 證明4%全面更好）。從此獨立於短邊的FGAP_MIN，各自可調，不再共用同一個常數。
FGAP_MIN_LONG = float(_YAML.get("fgap_min") or 0.04)
# 2026-08-12：個股層級白名單——使用者要求「只交易過去會贏的，過去容易輸的先
# 排除掉」，具體指定2327/3189/3481。已用walk-forward驗證不是全樣本cherry-pick：
# 只用train期(153筆,2025-08~2026-05)資料選出的前3名(6274/2327/2492)，在
# 完全沒看過的test期(66筆,2026-05~2026-07)均報酬+7.50%/勝率80.0%，明顯贏過
# 其餘股票(均+2.88%/勝率50.8%)；使用者指定的2327/3189/3481個別在test期也都
# 是正報酬，方向持續。⚠️樣本數仍偏薄(test期三檔合計僅13筆)，是目前驗證過的
# 發現裡證據較強的一個，但不是大樣本鐵證——空清單(未在config設定)時預設不
# 套用白名單(維持原行為)，避免忘記設定時意外清空整個候選池。
# 2026-08-12（同日第二次）：使用者要求「放進去一起排」，加入6274/8046擴大到
# 5檔，見config/order.yaml notes「個股白名單擴大到5檔」段落。⚠️這兩檔在
# fgap>=4%子集裡樣本數<6，連train/test walk-forward都做不到，全樣本「均+33%/
# 100%勝率」「均+18%/80%勝率」是小樣本結果，5檔併測後test期品質沒有明顯提升
# （n=11→13、勝率72.7%→69.2%）——是使用者被告知證據不足後的明確知情選擇，
# 不是研究證實的edge，之後若拖累白名單整體表現要先檢查這裡。
LONG_STOCK_WHITELIST: frozenset[str] = frozenset(_YAML.get("stock_whitelist") or ())
TRAIL_PCT = 5.0
MAX_HOLD_DAYS = 10
ENTRY_SCAN_END = "13:20"  # 留給出場檢查窗口的緩衝，避免臨收盤前才新進場
EXIT_CHECK_START = "13:25"  # 只在收盤前檢查一次移動停利，比照EOD-only已驗證結論
# ⚠️ 2026-08-13健檢發現：只有這個時間常數本身不夠——沒有state.last_eod_check_date
# 那道guard之前，hm>=EXIT_CHECK_START的判斷式每輪poll(≈60s)都會通過，13:25~13:41
# 這16分鐘窗口內實際檢查次數≈16次，不是1次，已修正（見LedgerState.last_eod_
# check_date與reconcile_once()）。
ENTRY_LIMIT_BUFFER_PCT = 0.3  # 2026-08-09使用者定案：進場改marketable limit（現價+0.3%、IOC）
# 控制滑價，比照短邊「不急迫用限價、真的要即刻成交才用市價」的慣例（見dayflip_
# short_order.py：cover用limit、force_close才用market）。出場（移動停利/滿10天）
# 維持市價不變——那兩種出場本質都是『時效到必須出』，跟短邊force_close同一類，
# 不能冒限價掛著不成交、隔天還抱著部位的風險。
USER_DEF_PREFIX = "dfpdlong"
# ⚠️ fubon_futopt_orders.build_futopt_order() 會把 user_def 截斷成10字
# （(resolved.user_def or "tmfch")[:10]）——build_client_intent_id() 產生的完整
# ID（如 dfpdlong_20260810_0925_2330F6，29字）送到broker只會剩前10碼，等於
# 白白截斷成無意義的字串。broker端user_def因此改用下面兩個固定短tag（跟短邊
# dfshort/dfshortcv/dfshortfc同一種「短literal」設計），完整可稽核ID只留在
# 本地ledger的position_id欄位，不指望broker端能存得下。
USER_DEF_ENTRY_TAG = "dfpdlong"  # 8字，進場單
USER_DEF_EXIT_TAG = "dfpdlongx"  # 9字，出場單（trailing_stop或max_hold都用同一個）

# 2026-08-11：fgap_min從共用短邊的7%改成獨立的4%（見FGAP_MIN_LONG常數docstring）
# ——版本號dfpdlong1→dfpdlong2，新增g4 token。
# 2026-08-11（同日第二次）：single_name_notional_cap_pct從15%改成100%（=取消
# 單股子上限，只受300,000總池約束，見config/order.yaml notes「單股上限取消」
# 段落）——版本號再遞增dfpdlong2→dfpdlong3，snc15→snc100 token。
# 2026-08-11（同日第三次）：使用者要求「去頭去尾」重新檢驗後，100%上限在任何
# 縮尾/加止損情境下max_dd都追不上15%上限，改回15%（見config/order.yaml notes
# 「單股上限取消後又改回」段落）——版本號dfpdlong3→dfpdlong4，snc100→snc15。
# 2026-08-12：新增LONG_STOCK_WHITELIST個股白名單(2327/3189/3481，見
# config/order.yaml notes「個股白名單」段落，已用walk-forward驗證)——版本號
# dfpdlong4→dfpdlong5，新增wl3token。使用者確認空單資料不支持同樣的白名單
# （樣本太薄、報酬幾乎二元化無法拆解），只套用在多單。
# 2026-08-12（同日第二次）：使用者要求「放進去一起排」，白名單擴大到5檔
# (+6274/8046，見config/order.yaml notes「個股白名單擴大到5檔」段落，
# 樣本數<6無法walk-forward，是知情選擇非研究證實)——版本號dfpdlong5→
# dfpdlong6，wl3→wl5 token。
# 2026-08-13健檢發現並修正：EXIT_CHECK_START="13:25"原本每輪poll(≈60s)只要
# hm>=13:25就會重新觸發_check_exits()，13:25~13:41窗口內實際檢查≈16次，
# 不是docstring寫的「只在收盤前檢查一次」——新增LedgerState.last_eod_check_
# date guard，同一天只真正執行一次——版本號dfpdlong6→dfpdlong7，新增eod1
# token。
FREEZE_RULE = "dfpdlong7_g4_wl5_rrd15w03t10c_trail5_hold10_cap300k_snc15_eod1"


def _hm_now() -> str:
    return datetime.now(tz=_TZ).strftime("%H:%M")


def _today() -> str:
    return datetime.now(tz=_TZ).strftime("%Y-%m-%d")


def in_dayflip_post_dump_long_trade_window(hm: str | None = None, *, weekday: int | None = None) -> bool:
    """跟 dayflip_short_order.in_dayflip_short_trade_window 同一套窗口/星期判斷。"""
    hm = hm or _hm_now()
    if weekday is None:
        weekday = datetime.now(tz=_TZ).weekday()
    if weekday > 4:
        return False
    return "08:44" <= hm <= "13:41"


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUE


def sleeve_auto_submit_enabled() -> bool:
    """本 sleeve 專屬旗標。預設 1（依使用者 2026-08-09 明確指示，不預設dry-run）。
    ORDER_MASTER_ENABLED 仍是唯一無法被此旗標繞過的總開關。"""
    raw = os.environ.get("ORDER_DAYFLIP_POST_DUMP_LONG_AUTO_SUBMIT", "1")
    return str(raw).strip().lower() in _TRUE


@dataclass
class Position:
    position_id: str
    stock_id: str
    futures_symbol: str
    entry_day: str
    entry_minute: str
    entry_price: float
    peak_price: float
    margin_ntd: float
    margin_source: str = "estimate_fallback"  # "broker_api" | "estimate_fallback"
    lots: int = LOTS
    status: str = "entered"  # entered|closed|entry_failed
    entry_order_no: str | None = None
    exit_order_no: str | None = None
    exit_price: float | None = None
    exit_day: str | None = None
    close_reason: str | None = None  # trailing_stop|max_hold
    last_action: str | None = None
    updated_at: str | None = None


@dataclass
class LedgerState:
    schema: str = "dayflip-post-dump-long-order-v1"
    positions: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str | None = None
    # 2026-08-13健檢發現並修正：EXIT_CHECK_START的docstring寫「只在收盤前檢查
    # 一次移動停利」，但原本的if hm >= EXIT_CHECK_START沒有任何「今天做過了」
    # 的記憶，worker每輪poll（≈60s一次）只要時間在13:25~13:41窗口內就會重跑一次
    # ，13:25~13:41這16分鐘內實際會檢查≈16次，不是1次——研究結論「盤中連續
    # 追蹤反而更差」本來就是要避免這種情況。這個欄位記錄「今天的EOD出場檢查
    # 做過了沒」，讓_check_exits在同一天只真正執行一次。
    last_eod_check_date: str | None = None


def load_ledger() -> LedgerState:
    raw = load_json_or_default(_LEDGER_PATH, {})
    if not isinstance(raw, dict):
        return LedgerState()
    positions = raw.get("positions")
    return LedgerState(
        schema=str(raw.get("schema") or "dayflip-post-dump-long-order-v1"),
        positions=list(positions) if isinstance(positions, list) else [],
        updated_at=raw.get("updated_at"),
        last_eod_check_date=raw.get("last_eod_check_date"),
    )


def save_ledger(state: LedgerState) -> None:
    state.updated_at = datetime.now(tz=_TZ).isoformat()
    atomic_write_json(_LEDGER_PATH, asdict(state), indent=1)


def open_positions(state: LedgerState) -> list[dict[str, Any]]:
    return [p for p in state.positions if p.get("status") == "entered"]


def margin_used_ntd(state: LedgerState) -> float:
    return sum(float(p.get("margin_ntd") or 0) for p in open_positions(state))


_SHORT_LEDGER_PATH = stock_db.DATA_DIR / "order" / "dayflip_short_ledger.json"  # 必須跟 dayflip_short_order.py 的 _LEDGER_PATH 同步修正，兩者已一起改


def short_side_margin_used_today() -> float:
    """讀（不寫）dayflip-short-poll 自己的 ledger，算今天短邊佔用了共用300,000
    池子裡的多少保證金——單向依賴，完全不改動 dayflip_short_order.py 本身。
    讀取失敗（檔案不存在/格式異常）一律視為0，不讓長邊因為讀不到短邊ledger而
    完全卡死（fail-open，理由：這只是一個風險預算軟上限，不是broker端的硬約束，
    寧可長邊照常運作、事後對帳，也不要因為短邊ledger暫時讀不到就整個長邊停擺）。
    """
    raw = load_json_or_default(_SHORT_LEDGER_PATH, {})
    if not isinstance(raw, dict):
        return 0.0
    if raw.get("date") != _today():
        return 0.0
    if raw.get("status") != "entered":
        return 0.0
    try:
        return float(raw.get("margin_ntd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def short_side_open_stock_id_today() -> str | None:
    """讀（不寫）短邊ledger，回傳今天短邊目前持有部位的stock_id（若有）。

    2026-08-09 補上（原本沒有）：長邊掃候選時完全沒排除短邊當天正在放空的
    那一檔，理論上會出現同一個帳戶對同一張個股期貨合約同時掛多單+空單——
    這個情境broker端會怎麼處理（自動軋平還是視為兩筆獨立部位、甚至直接
    拒單）完全沒驗證過。與其day 1就去賭這個未知行為，直接讓長邊跳過短邊
    當天持有的那一檔（等短邊平倉、stock_id從ledger消失後，下一輪poll長邊
    自然就能再考慮這檔）——多等一輪的成本遠低於賭一個未驗證的broker行為。
    """
    raw = load_json_or_default(_SHORT_LEDGER_PATH, {})
    if not isinstance(raw, dict):
        return None
    if raw.get("date") != _today():
        return None
    if raw.get("status") != "entered":
        return None
    sid = raw.get("stock_id")
    return str(sid) if sid else None


def available_pool_ntd(state: LedgerState) -> float:
    """共用池300,000扣掉：本sleeve自己在場部位的保證金 + 短邊今天在場那一筆的
    保證金（單向讀取，見 short_side_margin_used_today() docstring）。"""
    return TOTAL_CAPITAL_NTD - margin_used_ntd(state) - short_side_margin_used_today()


def per_name_cap_ntd() -> float:
    return TOTAL_CAPITAL_NTD * SINGLE_NAME_CAP_PCT / 100.0


def trading_days_held(entry_day: str, as_of: str) -> int:
    """entry_day (T0+1進場日) 到 as_of 之間經過幾個交易日，用 0050 日線當交易日曆
    （跟 scripts/research/dayflip_short_post_dump_long_capital_simulation.py 的
    build_calendar() 同一套慣例）。entry_day 當天本身算 0。"""
    con = sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT COUNT(DISTINCT trade_date) FROM stock_daily_bars "
            "WHERE stock_id='0050' AND source='finmind' AND trade_date > ? AND trade_date <= ?",
            (entry_day, as_of),
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row and row[0] is not None else 0


def _acquire_session(session: FubonSession | None, use_session_pool: bool) -> FubonSession:
    if session is not None:
        return session
    if use_session_pool:
        from order.dayflip_short_session_pool import get_fubon_session

        return get_fubon_session()
    from order.fubon_session import connect_fubon

    return connect_fubon()


def _fetch_bench_bars(session: FubonSession) -> list[dict[str, Any]]:
    """用TMF（台指期微型）即時1分K當rolling_relative_dip的大盤基準——理由見
    dayflip_post_dump_long_signal.py 模組docstring第2點。"""
    from order.tmf_channel_marketdata import fetch_1m_bars, resolve_front_symbol

    sym, _name, _end = resolve_front_symbol(session, product="TMF")
    return fetch_1m_bars(session, sym)


def _fetch_stock_spot_1m_bars(session: FubonSession, stock_id: str) -> list[dict[str, Any]]:
    """2026-08-10修正：使用者指出訊號偵測應該用個股現貨，下單當下才切期貨——
    回測本來就是用現貨1分K（stock_kbar_1m）驗證的，即時版原本改用期貨1分K
    純粹是「反正我們是對期貨下單」的圖方便，模組docstring自己承認「預期影響
    很小，但沒有專門重驗過」，不是真的驗證過現貨/期貨在rolling_relative_dip
    這個specific用途上等價。現在改回跟回測一致：訊號用現貨（本函式），下單價格
    基準（entry_price_basis/entry_limit_price）跟實際送單符號仍然是期貨，
    未變動——只有訊號輸入資料源改了，執行層不受影響。
    """
    from ops_live_ta import fetch_fubon_candles

    bars, _meta = fetch_fubon_candles(stock_id, timeframe="1", session=session)
    return [{"t": b["ts"].isoformat(), "c": b["close"]} for b in bars]


def _check_exits(state: LedgerState, session: FubonSession, hm: str, live: bool) -> list[dict[str, Any]]:
    today = _today()
    account = pick_futopt_account(session)
    summary = []
    for p in open_positions(state):
        px = fetch_last_price(session, p["futures_symbol"])
        if px is None:
            continue
        peak = max(float(p["peak_price"]), px)
        p["peak_price"] = peak
        pullback = (peak - px) / peak * 100 if peak > 0 else 0.0
        hold_days = trading_days_held(p["entry_day"], today)
        if pullback < TRAIL_PCT and hold_days < MAX_HOLD_DAYS:
            continue
        reason = "trailing_stop" if pullback >= TRAIL_PCT else "max_hold"
        # cid（完整可稽核ID）只留在本地ledger；broker端user_def欄位會被
        # fubon_futopt_orders.build_futopt_order()截斷成10字，見下方
        # USER_DEF_EXIT_TAG的說明，不能直接把cid塞進user_def。
        cid = build_client_intent_id(
            strategy_id=USER_DEF_PREFIX, session_date=today, poll_minute=hm, symbol=p["futures_symbol"],
        )
        close_order = FutOptResolvedOrder(
            symbol=p["futures_symbol"], buy_sell="Sell", lot=int(p["lots"]),
            price=None, price_type="market", time_in_force="ioc",
            order_type="Close", market_type="future", user_def=USER_DEF_EXIT_TAG,
            session_date=today,
        )
        try:
            res = place_futopt_order(session, close_order, acc=account, dry_run=not live)
        except Exception as ex:  # noqa: BLE001
            p["last_action"] = f"exit_exception:{ex}"
            summary.append({"stock_id": p["stock_id"], "action": "exit_exception", "error": str(ex)})
            continue
        p["status"] = "closed"
        p["exit_order_no"] = str((res.get("data") or {}).get("order_no") or "") or None
        p["exit_price"] = px
        p["exit_day"] = today
        p["close_reason"] = reason
        p["last_action"] = f"closed_{reason}"
        summary.append({
            "stock_id": p["stock_id"], "action": f"closed_{reason}",
            "exit_price": px, "hold_days": hold_days, "pullback_pct": round(pullback, 2),
        })
    return summary


def _check_entries(state: LedgerState, session: FubonSession, hm: str, live: bool) -> list[dict[str, Any]]:
    today = _today()
    t0 = _prior_trading_day()
    candidates = build_candidates(t0)
    open_stock_ids = {p["stock_id"] for p in open_positions(state)}
    short_stock_id = short_side_open_stock_id_today()
    account = pick_futopt_account(session)
    summary = []

    bench_bars = _fetch_bench_bars(session)

    for c in candidates:
        if LONG_STOCK_WHITELIST and c.stock_id not in LONG_STOCK_WHITELIST:
            continue
        if c.stock_id in open_stock_ids:
            continue
        if short_stock_id is not None and c.stock_id == short_stock_id:
            continue
        root = futures_root(c.stock_id)
        if not root:
            continue
        resolved = resolve_live_futures_symbol(session, root)
        if resolved is None:
            continue
        live_symbol, _live_name = resolved

        t0_close = last_close(c.stock_id, t0)
        open_px = fetch_open_price(session, live_symbol)
        if t0_close is None or t0_close <= 0 or open_px is None or open_px <= 0:
            continue
        fgap = open_px / t0_close - 1
        if fgap < FGAP_MIN_LONG:
            continue

        # 訊號偵測用個股現貨（跟回測一致，見_fetch_stock_spot_1m_bars docstring）；
        # live_symbol（期貨月合約）只用在下面的下單/進場價基準，不用在這裡。
        stock_bars = _fetch_stock_spot_1m_bars(session, c.stock_id)
        sig = find_rolling_dip_signal(stock_bars, bench_bars)
        if sig is None:
            continue

        # 2026-08-09複查發現的另一個bug：sig.price是「訊號被偵測到的那一分鐘」
        # 的價格，但下面送出的是市價單（price=None, price_type="market"）——
        # 正常情況(沒被guard/資金擋住)這兩者只差≤一個poll interval，可以忽略；
        # 但只要進場被同股互斥guard或資金池不足擋過一陣子，guard/資金解除後
        # 重新掃描時，sig仍然會找到「guard擋住之前就已經形成」的那個歷史訊號
        # （偵測器對整天資料做確定性掃描，不會因為之前被擋住就忘記），此時
        # sig.price可能是幾十分鐘甚至幾小時前的價格，拿來當entry_price/
        # peak_price（移動停利的起點）會跟市價單實際成交價脫節，讓移動停利
        # 的觸發時機失真。改成用送單當下的即時報價當進場價基準，sig只當
        # 「確認型態存在」的觸發條件，不當價格來源。
        # 2026-08-10：sig.price現在來自現貨1分K（見_fetch_stock_spot_1m_bars），
        # 不能再當期貨訂單的價格fallback——期貨/現貨價格不相等，混用會讓
        # entry_limit_price基準跟實際下單的期貨市場脫節。查不到即時期貨價
        # 就直接跳過這個候選，不用現貨價頂替，寧可少一次進場也不要用錯誤
        # 基準的價格下單（fail-closed，比照本檔其餘價格查詢的一貫原則）。
        current_px = fetch_last_price(session, live_symbol)
        if current_px is None or current_px <= 0:
            continue
        entry_price_basis = current_px

        # 2026-08-09複查發現：粗估公式(價格×2000×13.5%)對某些高價股（如8299）
        # 嚴重高估真實保證金（backtest對照發現8299粗估340k~786k，遠超短邊自己
        # 300,000上限卻仍出現在短邊實際交易紀錄裡，代表真實保證金遠低於粗估）。
        # 比照短邊pick_signal()的做法：優先查真實保證金，查不到才退回粗估，避免
        # 拿明顯不準的數字做300,000共用池/15%單股上限這種真金白銀的判斷。
        real_margin = query_real_margin_ntd(session, account, live_symbol)
        margin = real_margin if real_margin is not None else estimate_margin_ntd(entry_price_basis)
        margin_source = "broker_api" if real_margin is not None else "estimate_fallback"
        cap = per_name_cap_ntd()
        if margin > cap:
            continue
        available = available_pool_ntd(state)
        if margin > available:
            continue

        # cid：完整可稽核ID，存進本地ledger的position_id（見下方Position()）；
        # broker端user_def用短literal（USER_DEF_ENTRY_TAG），理由同exit那側。
        cid = build_client_intent_id(
            strategy_id=USER_DEF_PREFIX, session_date=today, poll_minute=hm, symbol=live_symbol,
        )
        # 2026-08-09使用者定案：進場改marketable limit（現價+0.3%、IOC）取代市價，
        # 控制個股期貨較薄流動性下的滑價；出場維持市價（見上方ENTRY_LIMIT_BUFFER_PCT
        # 註解）。IOC選擇理由：這是『幾乎一定會成交』的價格，若萬一沒成交，寧可
        # 讓下一輪poll用新的即時價重新判斷，也不要留一張過時價位的resting單。
        entry_limit_price = round(entry_price_basis * (1 + ENTRY_LIMIT_BUFFER_PCT / 100), 1)
        entry_order = FutOptResolvedOrder(
            symbol=live_symbol, buy_sell="Buy", lot=LOTS,
            price=entry_limit_price, price_type="limit", time_in_force="ioc",
            order_type="New", market_type="future", user_def=USER_DEF_ENTRY_TAG,
            session_date=today,
        )
        try:
            res = place_futopt_order(session, entry_order, acc=account, dry_run=not live)
        except Exception as ex:  # noqa: BLE001
            summary.append({"stock_id": c.stock_id, "action": "entry_exception", "error": str(ex)})
            continue

        pos = Position(
            position_id=cid, stock_id=c.stock_id, futures_symbol=live_symbol,
            entry_day=today, entry_minute=sig.minute, entry_price=entry_price_basis,
            peak_price=entry_price_basis, margin_ntd=round(margin), margin_source=margin_source, lots=LOTS,
            entry_order_no=str((res.get("data") or {}).get("order_no") or "") or None,
            last_action="entered", updated_at=datetime.now(tz=_TZ).isoformat(),
        )
        state.positions.append(asdict(pos))
        open_stock_ids.add(c.stock_id)
        summary.append({
            "stock_id": c.stock_id, "action": "entered",
            "entry_price": entry_price_basis, "margin_ntd": pos.margin_ntd, "entry_minute": sig.minute,
        })
    return summary


def reconcile_once(
    *,
    dry_run: bool | None = None,
    override_hm: str | None = None,
    use_session_pool: bool = False,
    session: FubonSession | None = None,
) -> dict[str, Any]:
    """單次 reconcile：先查既有部位是否該出場（僅收盤前窗口），再掃新進場訊號
    （僅收盤前緩衝窗口內），兩者共用同一個 300,000 NTD 保證金池、同一個15%
    單一標的曝險上限。dry_run=None 時依 sleeve_auto_submit_enabled() 決定。"""
    state = load_ledger()
    hm = override_hm or _hm_now()
    live = (not dry_run) if dry_run is not None else sleeve_auto_submit_enabled()
    result: dict[str, Any] = {
        "hm": hm, "live": live,
        "open_before": len(open_positions(state)),
        "margin_used_before": margin_used_ntd(state),
        "short_side_margin_used_today": short_side_margin_used_today(),
        "available_pool_before": available_pool_ntd(state),
    }

    if not in_dayflip_post_dump_long_trade_window(hm):
        result["action"] = "noop_outside_window"
        return result

    exits: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []

    today = _today()
    if hm >= EXIT_CHECK_START and open_positions(state) and state.last_eod_check_date != today:
        session = _acquire_session(session, use_session_pool)
        exits = _check_exits(state, session, hm, live)
        state.last_eod_check_date = today

    if hm <= ENTRY_SCAN_END:
        session = _acquire_session(session, use_session_pool)
        entries = _check_entries(state, session, hm, live)

    save_ledger(state)
    result.update(
        action="reconciled",
        exits=exits, entries=entries,
        open_after=len(open_positions(state)),
        margin_used_after=margin_used_ntd(state),
    )
    return result
