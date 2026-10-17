"""momentum-rotation sleeve · 次月反向對沖腿（2026-08-30 使用者指定）.

⚠️ 這支模組的存在理由是「實驗期間不承擔方向風險，只付交易成本」，不是為了改善
策略績效。使用者 2026-08-30 明確要求：明天（08-31）用 8/13 當時的規格再跑一次，
同時在另一個月份合約建立反向部位當避險組。

## 為什麼是次月不是遠月

原始要求是「遠月」。實測 8/13 tick 快取（排除價差合約）後改成次月，因為遠月
在物理上不會成交：

| 標的      | 近月(口)  | 次月(口) | 次月佔比 | 遠月(202610+) |
|-----------|----------|---------|---------|--------------|
| 2049 FFF  |  21,654  |  6,810  |  31%    | 全日 4 筆     |
| 3035 IPF  |  22,556  |  3,536  |  16%    | 全日 8 筆     |
| 3017 RAF  |  11,540  |  5,690  |  49%    | 全日 9 筆     |
| 2345 OPF  |   6,404  |  1,360  |  21%    | 全日 9 筆     |
| 2376 GHF  |  42,966  | 10,934  |  25%    | 全日 20 筆    |
| 2455 GUF  |  58,506  | 41,110  |  70%    | 全日 29 筆    |
| 2492 HBF  | 202,424  | 82,814  |  41%    | 全日 464 筆   |
| 6488 OWF  |  87,272  | 19,996  |  23%    | 全日 121 筆   |

8/13 那 7 筆的實際持有時間是 6 秒 / 11 秒 / 12 秒 / 53 秒 / 4.5 分 / 8 分。遠月
每 10~60 分鐘才成交一筆——在一個 6 秒的部位存續期間，遠月很可能一筆成交都沒有，
對沖腿進不去也出不來。次月有近月的 21~70%，是唯一有機會同步進出的選擇。

## 次月仍然沒有解決的三件事（誠實記錄，不要當成已對沖就放心）

1. **6 秒持有期對次月仍然偏短。** 次月佔比最低的 3035 只有 16%，換算約每分鐘
   個位數筆成交，6 秒窗口內可能一筆都沒有。雙腿同步不是保證，是機率。
2. **半套曝險。** 主腿成交、對沖腿被拒（8/13 單腿就有 15/22 次被「交易額度超過
   資(財)力證明額度」擋掉）會留下裸露部位。本模組的處理見
   momentum_rotation_order._enter_position 的 rollback 路徑：對沖腿失敗就立刻把
   主腿平掉，平不掉才記進 ledger 並 halted。
3. **近次月價差不是零。** 個股期貨的近次月價差受除權息預期驅動，8~9 月是台股
   除權息旺季。所以這不是乾淨的方向對沖，殘留一個除權息基差部位——金額遠小於
   方向曝險，但不是 0。

預設關閉（ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED 未設或非真值即為關）。
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

_TRUE = frozenset({"1", "true", "yes", "on"})


def hedge_enabled() -> bool:
    """對沖腿總開關。預設關——比照 CLAUDE.md「下單相關程式維持 dry-run/disabled
    為預設，實彈只由 mini 的 .env 開」。"""
    return os.environ.get("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", "0").strip().lower() in _TRUE


def hedge_side(main_direction: str, *, closing: bool) -> str:
    """對沖腿的買賣別＝主腿的反向。

    main long  → 對沖 Sell New  / 平倉 Buy Close
    main short → 對沖 Buy New   / 平倉 Sell Close
    """
    if closing:
        return "Buy" if main_direction == "long" else "Sell"
    return "Sell" if main_direction == "long" else "Buy"


def resolve_next_month_symbol(session: Any, root: str) -> tuple[str, str] | None:
    """回傳「次月」完整月合約代碼（如 GHFI6），不是近月。

    邏輯刻意跟 dayflip_short_order.resolve_live_futures_symbol 平行——那支排序後
    取 cands[0]（近月），這支取 cands[1]（次月）。兩支都必須用 tickers() 查當下
    真正在交易的完整月合約代碼，FinMind 式代碼（GHF）在 Fugle marketdata 一律
    404，見該函式 docstring。

    fail-closed：查不到第二個月份就回 None，呼叫端必須放棄這次進場，
    **絕不可以拿近月代碼去當對沖腿**（那會變成同商品同月份的反向單，在期交所
    是淨額計算＝直接把主腿平掉，不是對沖）。
    """
    from order.tmf_channel_marketdata import _ensure_realtime_once

    _ensure_realtime_once(session)
    fut = session.sdk.marketdata.rest_client.futopt
    data = fut.intraday.tickers(type="FUTURE", exchange="TAIFEX", product=root)
    rows = data if isinstance(data, list) else (data.get("data") or data.get("tickers") or [])
    today = date.today().isoformat()
    cands: list[tuple[str, str, str]] = []
    for r in rows or []:
        sym = str(r.get("symbol") or "")
        end = str(r.get("endDate") or r.get("end_date") or "")[:10]
        name = str(r.get("name") or sym)
        if not sym.startswith(root):
            continue
        if end and end < today:
            continue
        cands.append((end or "9999-99-99", sym, name))
    if len(cands) < 2:
        return None  # 只有近月在交易，沒有次月可對沖
    cands.sort()
    _end, sym, name = cands[1]  # [0]=近月，[1]=次月
    return sym, name


def resolve_hedge_symbol(session: Any, sid: str, cache: dict[str, str]) -> str | None:
    """對沖腿代碼（帶 ledger 快取，比照 _resolve_live_symbol 的做法）。"""
    from order.momentum_rotation_signal import FUTURES_ROOT

    cached = cache.get(sid)
    if cached:
        return cached
    root = FUTURES_ROOT.get(sid)
    if not root:
        return None
    resolved = resolve_next_month_symbol(session, root)
    if resolved is None:
        return None
    sym, _name = resolved
    cache[sid] = sym
    return sym
