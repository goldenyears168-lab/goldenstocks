"""momentum-rotation 專用：逐筆成交(trades)當即時資料來源，取代1分K.

2026-08-13 使用者明確要求「比照回測用的逐筆辦理，最細的刻度」——回測
(`scripts/research/momentum_breakout_strategy.py::simulate_portfolio_day`)本來就是
逐筆算的，momentum_rotation_signal.process_new_bar 之前用 fetch_1m_bars(已收盤1分K)
當資料來源，兩邊粒度不一致（見該檔頂部docstring的已知落差）。

刻意不改 tmf_channel_marketdata.py：那支檔案被 tmf-channel/dayflip-short/
dayflip-post-dump-long 三個現在正在跑的live sleeve共用，這裡另開一個新檔，只有
momentum_rotation_order.py會import，把改動範圍鎖死在momentum-rotation自己身上，
不影響其他已經在跑的live系統。

輸出跟 fetch_1m_bars 完全同一個shape（list[{t,o,h,l,c,v}]），o=h=l=c=單筆成交價、
v=單筆成交量——這樣 process_new_bar / _feed_new_bars 完全不用改，直接把「1分K」
換成「每一筆成交自己就是一根寬度為0的K」即可套用既有（已測試過）的狀態機邏輯。
等同性驗證見 tests/test_momentum_rotation_tick_marketdata.py：拿真實逐筆CSV重放，
比對這條路徑 vs simulate_portfolio_day 產生的交易是否一致。

⚠️ 誠實記錄兩個沒辦法在今天開盤前驗證的殘留風險：
1. Fugle/富邦 `intraday.trades()` 這支REST endpoint的真實回傳筆數上限未知
   （會不會只給「最近N筆」，兩次poll之間如果成交超過N筆會漏掉中間的tick）——
   本檔已經把worker輪詢間隔從20秒建議縮短到3秒降低這個風險發生的機率，但沒辦法
   完全消除，因為市場休市無法對真實API打一次來確認。
2. 回傳欄位名稱是防禦性猜測（比照candles/tickers兩支endpoint已知的欄位命名慣例
   date/price/volume），如果實際欄位名不同，_normalize_tick會回傳None、
   該筆invalid直接跳過——失敗模式設計成「安靜地少抓到訊號」而不是「用錯資料下錯單」，
   但仍然是沒有實測過的假設。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from order.fubon_session import FubonSession
from order.tmf_channel_marketdata import _ensure_realtime_once, _in_day_window

_TZ = ZoneInfo("Asia/Taipei")


def _parse_trade_dt(row: dict[str, Any]) -> datetime | None:
    """2026-08-13盤中真實資料證實：intraday.trades()的't'/'time'欄位是微秒epoch
    整數（例：1786583695197000，不是candles()那種ISO日期字串），這裡專門處理
    這個格式——原本借用tmf_channel_marketdata._row_dt（設計給candles()用）完全
    解不出來，靜默回傳None，導致這支函式從開盤到09:13每一筆都被_normalize_tick
    丟棄，watch agent的AI kill switch正確抓到這個狀況並在09:13觸發暫停（見
    momentum_rotation_watch_20260813.log）。這個bug只在真實開盤trades()回應才
    暴露得出來，休市測試看不到，是檔頭docstring原本承認的殘留風險真的發生了。
    """
    raw = row.get("time")
    if raw is None:
        raw = row.get("date") or row.get("datetime")
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(_TZ)
        except ValueError:
            return None
    try:
        micros = int(raw)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(micros / 1_000_000, tz=_TZ)
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_tick(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        p = float(row.get("price") or row.get("lastPrice") or row.get("closePrice") or 0)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    v = float(row.get("volume") or row.get("size") or row.get("tradeVolume") or row.get("qty") or 0)
    dt = _parse_trade_dt(row)
    if dt is None:
        return None
    return {"dt": dt, "p": p, "v": v}


def _dedupe_same_second(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一秒內可能有多筆成交（真實tick CSV已經證實過這件事），用遞增毫秒offset
    讓每一筆的't'字串嚴格遞增——_feed_new_bars/process_new_bar都是用't'字串比較
    做去重（'bar_time <= last_bar_time'），時間戳完全相同會被誤判成同一筆重複處理
    而被跳過，這裡在來源端就先處理掉，state機邏輯完全不用碰。
    """
    out: list[dict[str, Any]] = []
    last_second: str | None = None
    sub_ms = 0
    for tk in ticks:
        sec_key = tk["dt"].strftime("%Y-%m-%dT%H:%M:%S")
        if sec_key == last_second:
            sub_ms = min(sub_ms + 1, 999)
        else:
            sub_ms = 0
            last_second = sec_key
        t_str = tk["dt"].replace(microsecond=sub_ms * 1000).isoformat(timespec="milliseconds")
        out.append({"t": t_str, "o": tk["p"], "h": tk["p"], "l": tk["p"], "c": tk["p"], "v": tk["v"]})
    return out


def fetch_ticks_as_bars(session: FubonSession, symbol: str) -> list[dict[str, Any]]:
    """逐筆成交，包裝成跟 fetch_1m_bars 完全同一個 shape 回傳。

    只抓當天日盤時窗（momentum-rotation 無夜盤，跟 fetch_1m_bars 不同，這裡不用
    處理跨日夜盤的複雜度）。真實API呼叫失敗會直接往外拋例外——不在這裡吞掉，
    刻意讓呼叫端（_feed_new_bars既有的 per-stock try/except: continue）接手，
    跟 fetch_1m_bars 對candles()呼叫失敗時的行為完全一致，不引入新的錯誤處理慣例。
    """
    _ensure_realtime_once(session)
    fut = session.sdk.marketdata.rest_client.futopt
    res = fut.intraday.trades(symbol=symbol)
    raw = list(res.get("data") or []) if isinstance(res, dict) else list(res or [])

    ticks: list[dict[str, Any]] = []
    for row in raw:
        norm = _normalize_tick(row)
        if norm is None:
            continue
        ticks.append(norm)
    if not ticks:
        return []

    # trades() 通常按時間新→舊排序（比照多數行情API慣例），這裡強制轉成舊→新，
    # 且只留當天日盤時窗（08:45-13:45），避免昨天殘留資料或非交易時段雜訊混入
    ticks.sort(key=lambda x: x["dt"])
    today = ticks[-1]["dt"].date()
    ticks = [tk for tk in ticks if _in_day_window(tk["dt"], today)]
    return _dedupe_same_second(ticks)


def fetch_live_quote(session: FubonSession, symbol: str) -> tuple[float, float] | None:
    """回傳 (bid, ask) — 2026-08-13 早上真金實測發現市價單IOC完全沒有價格保護，
    進場滑價實測最高到2.73%（見momentum_rotation_order.py改用限價單那次commit
    說明），改用限價單需要知道目前的buy/sell報價當基準。

    ⚠️ 這不是真正的五檔深度（Level-2）——查過`intraday.quote()`只給最後一筆
    成交搭配的Level-1買賣價（`lastTrade.bid`/`lastTrade.ask`），這個Fubon REST
    介面沒有暴露五檔深度欄位；連這個repo最成熟的tmf_channel_order.py自己下限價單
    也不是用真正買賣二價、是用最後成交價(spot)±手動offset。2026-08-13使用者
    確認這個限制後，明確選擇用「Level-1 bid/ask + 緩衝」當替代方案，不是我自己
    決定的權宜之計。
    """
    _ensure_realtime_once(session)
    fut = session.sdk.marketdata.rest_client.futopt
    res = fut.intraday.quote(symbol=symbol)
    if not isinstance(res, dict):
        return None
    last_trade = res.get("lastTrade") or {}
    try:
        bid = float(last_trade.get("bid") or 0)
        ask = float(last_trade.get("ask") or 0)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return bid, ask
