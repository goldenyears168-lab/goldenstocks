"""驗證2026-08-13改市價單為限價單這次修復：限價計算+沒成交不能悄悄建倉/平倉.

背景：使用者發現當天4筆交易進場滑價0.5%~2.7%（市價IOC完全沒有價格保護），改用
限價單（Level-1 bid/ask + 緩衝，逼近買二/賣二的效果——這個repo沒有真正的Level-2
深度資料，2026-08-13使用者明確選擇這個替代方案）。改用限價單後新增的風險：
「送單成功」不再等於「真的成交」（市價單幾乎必成交，限價IOC沒撮合到會直接取消），
這裡鎖住兩件事：限價計算公式本身、以及「查不到真實成交紀錄時，進場不建倉/出場
不清空部位」這個安全邊界。
"""

from __future__ import annotations

from unittest import mock

from order.momentum_rotation_order import (
    _close_position,
    _compute_marketable_limit_price,
    _enter_position,
    LedgerState,
    PositionState,
)
from order.momentum_rotation_signal import BreakoutSignal, UNIVERSE
from dataclasses import asdict

# 2026-08-31：改成從 UNIVERSE 動態取，不寫死代號（換池時才不會整批紅掉）
_SID = next(iter(UNIVERSE))
_FRONT = "XXFI6"


def test_compute_marketable_limit_price_buy_is_above_ask():
    price = _compute_marketable_limit_price(bid=100.0, ask=102.0, buy_sell="Buy")
    assert price == 104.0  # ask + spread(2.0)


def test_compute_marketable_limit_price_sell_is_below_bid():
    price = _compute_marketable_limit_price(bid=100.0, ask=102.0, buy_sell="Sell")
    assert price == 98.0  # bid - spread(2.0)


def test_compute_marketable_limit_price_has_minimum_buffer_when_spread_near_zero():
    """spread接近0時要有下限（ask的0.1%），避免緩衝退化成沒有保護。"""
    price = _compute_marketable_limit_price(bid=100.0, ask=100.0, buy_sell="Buy")
    assert price >= 100.1  # 至少0.1%緩衝


def _fresh_state() -> LedgerState:
    return LedgerState(date="2026-08-13", stock_states={sid: {"armed": True} for sid in UNIVERSE})


def test_enter_position_does_not_build_phantom_position_when_limit_order_unfilled():
    """限價IOC送單成功(ok=True)但查不到真實成交(fetch_real_fill_price回None)時，
    絕對不能建立state.position——這是2026-08-13新增限價單風險裡最危險的一種，
    ledger會出現broker端根本不存在的幻影部位。"""
    session = mock.Mock()
    account = mock.Mock()
    state = _fresh_state()
    sig = BreakoutSignal(sid=_SID, direction="long", fill=100.0, overshoot=1.0, vol_ratio=2.0, score=2.0)
    state.live_symbols = {_SID: _FRONT}

    with mock.patch(
        "order.momentum_rotation_order._place_order",
        return_value={"ok": True, "price_source": "limit_from_quote", "data": {"order_no": "sTEST"}},
    ), mock.patch(
        "order.momentum_rotation_order.fetch_real_fill_price", return_value=None,
    ):
        res = _enter_position(session, account, state, sig, live=True)

    assert res["ok"] is False
    assert res.get("no_fill_confirmed") is True
    assert state.position["sid"] is None, "沒真的成交就不該建立部位"


def test_enter_position_builds_real_position_when_limit_order_filled():
    session = mock.Mock()
    account = mock.Mock()
    state = _fresh_state()
    sig = BreakoutSignal(sid=_SID, direction="long", fill=100.0, overshoot=1.0, vol_ratio=2.0, score=2.0)
    state.live_symbols = {_SID: _FRONT}

    with mock.patch(
        "order.momentum_rotation_order._place_order",
        return_value={"ok": True, "price_source": "limit_from_quote", "data": {"order_no": "sTEST"}},
    ), mock.patch(
        "order.momentum_rotation_order.fetch_real_fill_price", return_value=101.5,
    ):
        res = _enter_position(session, account, state, sig, live=True)

    assert res["ok"] is True
    assert state.position["sid"] == _SID
    assert state.position["fill"] == 101.5
    assert state.position["fill_source"] == "real"


def test_close_position_keeps_position_when_limit_order_unfilled():
    """出場限價單沒真的成交時，絕對不能清空position/寫入trades_today——照本函式
    docstring的既有原則（出場失敗絕不放棄），沒成交等同失敗，要留給下一輪poll重試。"""
    session = mock.Mock()
    account = mock.Mock()
    state = _fresh_state()
    state.position = asdict(PositionState(sid=_SID, direction="long", fill=100.0, peak_trough=100.0))
    state.live_symbols = {_SID: _FRONT}
    state.stock_states[_SID] = {"armed": False}

    with mock.patch(
        "order.momentum_rotation_order._place_order",
        return_value={"ok": True, "price_source": "limit_from_quote", "data": {"order_no": "sCLOSE"}},
    ), mock.patch(
        "order.momentum_rotation_order.fetch_real_fill_price", return_value=None,
    ):
        res = _close_position(session, account, state, exit_price=105.0, reason="trail_stop", live=True)

    assert res["ok"] is False
    assert res.get("no_fill_confirmed") is True
    assert state.position["sid"] == _SID, "沒真的成交，部位必須維持原樣"
    assert len(state.trades_today) == 0, "沒真的成交不能記成一筆完成的交易"
