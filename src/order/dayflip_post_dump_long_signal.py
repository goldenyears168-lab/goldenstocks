"""dayflip-post-dump-long v1 · 即時訊號計算.

候選股/保證金估算/期貨代碼解析全部沿用 dayflip_short_signal.py（同一份
FROZEN_SPEC_V1.json 分點篩選規則，本策略不重新篩一次）。本檔只新增
rolling_relative_dip 的即時（線上）版本——研究驗證見
scripts/research/dayflip_short_rolling_relative_dip_signal.py 與
scripts/research/dayflip_short_tx_real_rolling_dip_signal_v1.py：用滾動窗口
（預設15分鐘）比較個股 vs 大盤的區間報酬，找『個股區間報酬明顯輸給大盤』的
那一段，落後幅度確認收斂過半即視為賣壓消化完成、進場訊號成立。

⚠️ 即時版跟回測版的差異（誠實記錄，不是同一份東西）：
  1) 回測用個股「現貨」1分K（stock_kbar_1m）；即時版改用個股「期貨」1分K
     （resolve_live_futures_symbol 解出的月合約），因為我們本來就是在對期貨
     下單、且期貨/現貨報酬相關係數已驗證0.987-0.994（見
     dayflip_short_futures_long_verify.py），改用期貨價格對訊號偵測本身的
     影響預期很小，但沒有專門重驗過。
  2) 回測用真台指期(tx_1m_tick_built_582d)或0050當大盤基準；即時版改用TMF
     （台指期微型）即時報價——TMF追蹤的就是台股加權指數本身，跟TX同一個
     指數、只是契約乘數不同，rolling lag只看%報酬不看絕對點數，用TMF取代
     TX理論上不影響訊號本質，但同樣沒有專門即時重驗過。
  3) 本模組只讀 DB／Fubon marketdata，不管理 session 生命週期，可離線單元
     測試（除了直接呼叫 Fubon API 的函式）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from order.dayflip_short_signal import (  # noqa: F401  (re-export for order module)
    FGAP_MIN,
    Candidate,
    build_candidates,
    estimate_margin_ntd,
    futures_root,
    last_close,
    resolve_futures_symbol,
)

ROLLING_WINDOW_MIN = 15
LAG_THRESHOLD_PCT = 0.3
CONFIRM_MINUTES = 10


@dataclass(frozen=True)
class DipSignal:
    minute: str  # "HH:MM"，確認收斂的那一分鐘
    price: float  # 該分鐘個股期貨收盤價（近似進場價）


def _closes_by_minute(bars: list[dict[str, Any]]) -> dict[str, float]:
    """bars: tmf_channel_marketdata.fetch_1m_bars() 回傳格式 [{"t": iso8601, "c": float}, ...]."""
    out: dict[str, float] = {}
    for b in bars:
        t = str(b.get("t") or "")
        c = b.get("c")
        if not t or c is None:
            continue
        hm = t.split("T")[-1][:5]
        try:
            px = float(c)
        except (TypeError, ValueError):
            continue
        if px > 0:
            out[hm] = px
    return out


def find_rolling_dip_signal(
    stock_bars: list[dict[str, Any]],
    bench_bars: list[dict[str, Any]],
    *,
    window_min: int = ROLLING_WINDOW_MIN,
    lag_threshold_pct: float = LAG_THRESHOLD_PCT,
    confirm_minutes: int = CONFIRM_MINUTES,
) -> DipSignal | None:
    """滾動窗口相對弱勢段訊號——即時版，邏輯對齊
    scripts/research/dayflip_short_tx_real_rolling_dip_signal_v1.py 的
    find_rolling_dip_signal()，只是輸入格式從 DB 查詢結果換成即時 API 回傳的
    bars list。全函式無副作用、不呼叫任何外部 API，方便離線單元測試。
    """
    stock_closes = _closes_by_minute(stock_bars)
    bench_closes = _closes_by_minute(bench_bars)
    minutes = sorted(set(stock_closes) & set(bench_closes))
    if len(minutes) < window_min + confirm_minutes + 5:
        return None

    rolling_lag: dict[str, float] = {}
    for i, m in enumerate(minutes):
        if i < window_min:
            continue
        m0 = minutes[i - window_min]
        stock_ret = (stock_closes[m] / stock_closes[m0] - 1) * 100
        bench_ret = (bench_closes[m] / bench_closes[m0] - 1) * 100
        rolling_lag[m] = stock_ret - bench_ret

    lag_minutes = [m for m in minutes if m in rolling_lag]
    if not lag_minutes:
        return None

    worst_idx: int | None = None
    worst_val = 0.0
    for i, m in enumerate(lag_minutes):
        if rolling_lag[m] < -lag_threshold_pct and rolling_lag[m] < worst_val:
            worst_val = rolling_lag[m]
            worst_idx = i
    if worst_idx is None:
        return None

    worst_minute = lag_minutes[worst_idx]
    for i in range(worst_idx + 1, len(lag_minutes)):
        m = lag_minutes[i]
        elapsed = i - worst_idx
        if elapsed >= confirm_minutes and rolling_lag[m] > rolling_lag[worst_minute] * 0.5:
            return DipSignal(minute=m, price=stock_closes[m])
    return None
