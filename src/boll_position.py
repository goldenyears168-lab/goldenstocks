"""小哥版布林通道位置（knowledge-book 2.1、R01／R03／R14、E.2）。純函式。

定義（全部出自書；書沒給數字的地方明說「操作化」）：
- 通道：中軌＝20 日簡單均線（月線）；上軌＝中軌＋2σ、下軌＝中軌−2σ；σ 取母體標準差（ddof=0，同 xiaoge_inventory），倍數固定 2.0（V1）。
- 帶寬 BW＝上軌 ÷ 下軌 − 1（V1 [7:50]，不是 (上−下)÷中）。分級（使用者 2026-10-21 依書裁決）：
  <5% 非常窄、≤10% 窄（含 10%）、10–20% 正常、≥20% 寬。
- 月線斜率＝(今日月線 − 昨日月線) ÷ 昨日月線（V16 畫面，E.2）；月線方向＝斜率 >0 向上、否則下彎。
- 位置：收盤在 上軌之上／月線~上軌／下軌~月線／下軌之下；「高檔」＝收盤 ≥ 月線（通道上半）、「低檔」＝收盤 < 月線（下半）。
  （書的「高檔／低檔」另有 3～5 年線型右下角用法，那是價值面，不在此函式。）
- 開布林（R03）：書只說「帶寬由窄往上打開、一開就進」。操作化為：昨日 BW ≤10%（窄）∧ 今日 BW 擴大 ∧ 上軌上彎 ∧ 收盤 ≥ 月線。
- 觸下軌（R14「大帶寬的下軌是好買點」）：今日最低 ≤ 下軌。
"""
from __future__ import annotations

import math
from typing import Any, Sequence

N = 20
K = 2.0
BW_VERY_NARROW = 0.05
BW_NARROW = 0.10
BW_WIDE = 0.20


def _band(closes: Sequence[float], n: int = N, k: float = K) -> tuple[float, float, float] | None:
    if len(closes) < n or any(c is None or (isinstance(c, float) and math.isnan(c)) or c <= 0 for c in closes[-n:]):
        return None
    w = [float(c) for c in closes[-n:]]
    ma = sum(w) / n
    sd = math.sqrt(sum((c - ma) ** 2 for c in w) / n)
    return ma, ma + k * sd, ma - k * sd


def bw_level(bw: float | None) -> str | None:
    if bw is None:
        return None
    if bw < BW_VERY_NARROW:
        return "非常窄"
    if bw <= BW_NARROW:
        return "窄"
    if bw < BW_WIDE:
        return "正常"
    return "寬"


def boll_position(closes: Sequence[float], low_today: float | None = None, n: int = N) -> dict[str, Any] | None:
    """closes 由舊到新，最後一根是訊號日。少於 n+1 根回 None。"""
    cur, prev = _band(closes, n), _band(closes[:-1], n)
    if cur is None or prev is None:
        return None
    ma, ub, lb = cur
    ma0, ub0, lb0 = prev
    if lb <= 0 or lb0 <= 0:
        return None
    close = float(closes[-1])
    bw, bw0 = ub / lb - 1, ub0 / lb0 - 1
    slope = ma / ma0 - 1
    zone = "above_ub" if close > ub else "ma_to_ub" if close >= ma else "lb_to_ma" if close >= lb else "below_lb"
    upper = close >= ma
    return {
        "ma20": round(ma, 3), "ub": round(ub, 3), "lb": round(lb, 3),
        "bw_pct": round(bw * 100, 2), "bw_prev_pct": round(bw0 * 100, 2), "bw_level": bw_level(bw),
        "ma_slope_pct": round(slope * 100, 3), "ma_dir": "up" if slope > 0 else "down",
        "zone": zone, "upper_half": upper,
        "open_boll": bool(bw0 <= BW_NARROW and bw > bw0 and ub > ub0 and upper),
        "touch_lb": bool(low_today is not None and low_today <= lb),
    }


def bias_year(closes: Sequence[float], n: int = 240) -> float | None:
    """乖離年線＝收盤 ÷ 240 日均線 − 1（R12 高檔出貨股：>40%）。不足 n 根回 None。"""
    if len(closes) < n:
        return None
    ma = sum(float(c) for c in closes[-n:]) / n
    return float(closes[-1]) / ma - 1 if ma > 0 else None
