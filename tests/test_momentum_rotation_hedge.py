"""驗證 2026-08-30 新增的兩件事：次月反向對沖腿、以及刻意重現 8/13 anchor bug.

兩者都是使用者在 2026-08-30 明確指定的實驗設定（「明天用 8/13 當時的規格再跑
一次，同時做一個反向的避險組」），**兩者都預設關閉**，靠環境變數開。

這裡鎖住的安全邊界（照重要性排序）：
1. 對沖腿失敗時主腿一定要被回沖掉——絕不留單腿裸露曝險
2. 回沖也失敗時，裸露部位一定要被寫進 ledger 且 halted，不能變成孤兒
3. 平倉時對沖腿先走、主腿後走（失敗模式才會落在「仍然對沖」而不是「裸露」）
4. 對沖腿已平倉就不能再平第二次（對已平倉的腿送 Close 會變成反向新倉）
5. 查不到次月合約時 fail-closed，**絕不退回用近月**（同月份反向＝淨額沖銷）
"""

from __future__ import annotations

from dataclasses import asdict
from unittest import mock

import pytest

from order.momentum_rotation_hedge import (
    hedge_enabled,
    hedge_side,
    resolve_next_month_symbol,
)
from order.momentum_rotation_order import (
    LedgerState,
    PositionState,
    _close_position,
    _enter_position,
    _feed_new_bars,
)
from order.momentum_rotation_signal import UNIVERSE, BreakoutSignal, StockState

# 2026-08-31：不要寫死標的代號。UNIVERSE 換池時（v1 12 檔 → v2 19 檔）寫死的
# "3017" 讓 11 個測試同時紅掉，而失敗訊息是 KeyError 看不出根因。改成從 UNIVERSE
# 動態取，以後換池測試自動跟上。
_SID = next(iter(UNIVERSE))
_FRONT = "XXFI6"   # 近月（假代碼，測試不打真 API）
_NEXT = "XXFJ6"    # 次月


@pytest.fixture(autouse=True)
def _isolate_production_state(monkeypatch, tmp_path):
    """⚠️ 這組測試會呼叫 _enter_position/_close_position，而 reverse_mode() 與
    kill_switch_reason() 是**讀生產目錄的旗標檔**。沒有這個 fixture，測試結果會
    被 mini 上的真實狀態污染——2026-08-30 實際踩過：使用者從手機儀表板按下反向
    鈕之後，兩個測試立刻紅掉（方向被真的翻過去了）。

    任何讀 stock_db.DATA_DIR 的模組層路徑，在測試裡都必須先導到 tmp_path。
    """
    import order.momentum_rotation_order as mro

    monkeypatch.setattr(mro, "_REVERSE_PATH", tmp_path / "reverse.json")
    monkeypatch.setattr(mro, "_KILL_SWITCH_PATH", tmp_path / "kill.json")
    monkeypatch.setattr(mro, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(mro, "_BROADCAST_PATH", tmp_path / "broadcast.json")
    monkeypatch.delenv("ORDER_MOMENTUM_ROTATION_REVERSE", raising=False)
    # 本檔測的是 v1 訊號路徑（anchor/hedge/移動停利），v3 預設開會讓 _feed_new_bars
    # 改走相對大盤分支、跳過 process_new_bar——這裡固定關掉
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_SIGNAL_V3", "0")


# --------------------------------------------------------------------------
# 開關與買賣別
# --------------------------------------------------------------------------

def test_hedge_disabled_by_default(monkeypatch):
    """CLAUDE.md：下單相關程式維持 disabled 為預設，實彈只由 mini 的 .env 開。"""
    monkeypatch.delenv("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", raising=False)
    assert hedge_enabled() is False


@pytest.mark.parametrize("val,want", [("1", True), ("true", True), ("on", True),
                                      ("0", False), ("", False), ("no", False)])
def test_hedge_enabled_parsing(monkeypatch, val, want):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", val)
    assert hedge_enabled() is want


def test_hedge_side_is_opposite_of_main():
    assert hedge_side("long", closing=False) == "Sell"
    assert hedge_side("short", closing=False) == "Buy"
    # 平倉時是反向腿自己的反向
    assert hedge_side("long", closing=True) == "Buy"
    assert hedge_side("short", closing=True) == "Sell"


# --------------------------------------------------------------------------
# 次月合約解析
# --------------------------------------------------------------------------

def _mock_session_with_tickers(rows):
    session = mock.Mock()
    session.sdk.marketdata.rest_client.futopt.intraday.tickers.return_value = rows
    return session


def test_resolve_next_month_picks_second_not_front():
    """近月 = endDate 最早那個；次月 = 第二個。取錯會變成同月份反向＝淨額沖銷。"""
    session = _mock_session_with_tickers([
        {"symbol": "GHFJ6", "endDate": "2026-10-21", "name": "技嘉10"},
        {"symbol": "GHFI6", "endDate": "2026-09-16", "name": "技嘉09"},
        {"symbol": "GHFK6", "endDate": "2026-11-18", "name": "技嘉11"},
    ])
    with mock.patch("order.tmf_channel_marketdata._ensure_realtime_once"):
        sym, _name = resolve_next_month_symbol(session, "GH")
    assert sym == "GHFJ6", "應取 endDate 第二早的（次月），不是最早的（近月）"


def test_resolve_next_month_fail_closed_when_only_front_month():
    """只有近月在交易時必須回 None，呼叫端才會放棄進場——絕不可退回近月。"""
    session = _mock_session_with_tickers([
        {"symbol": "GHFI6", "endDate": "2026-09-16", "name": "技嘉09"},
    ])
    with mock.patch("order.tmf_channel_marketdata._ensure_realtime_once"):
        assert resolve_next_month_symbol(session, "GH") is None


def test_resolve_next_month_skips_expired_contracts():
    session = _mock_session_with_tickers([
        {"symbol": "GHFH6", "endDate": "2020-01-01", "name": "已到期"},
        {"symbol": "GHFI6", "endDate": "2026-09-16", "name": "技嘉09"},
        {"symbol": "GHFJ6", "endDate": "2026-10-21", "name": "技嘉10"},
    ])
    with mock.patch("order.tmf_channel_marketdata._ensure_realtime_once"):
        sym, _ = resolve_next_month_symbol(session, "GH")
    assert sym == "GHFJ6", "過期合約要被排除後才數第二個"


# --------------------------------------------------------------------------
# 進場：對沖腿失敗必須回沖主腿
# --------------------------------------------------------------------------

def _fresh_state() -> LedgerState:
    return LedgerState(date="2026-08-31",
                       stock_states={sid: {"armed": True} for sid in UNIVERSE},
                       live_symbols={_SID: "_FRONT"})


def _sig() -> BreakoutSignal:
    return BreakoutSignal(sid=_SID, direction="long", fill=100.0,
                          overshoot=1.0, vol_ratio=2.0, score=2.0)


def test_enter_unwinds_main_leg_when_hedge_fails(monkeypatch):
    """對沖腿建不起來 → 主腿立刻回沖 → 不建立部位、帳上乾淨。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", "1")
    state = _fresh_state()
    calls: list[tuple[str, str, str]] = []

    def fake_place(_s, _a, symbol, buy_sell, order_type, _live, user_def):
        calls.append((symbol, buy_sell, order_type))
        return {"ok": True, "price_source": "market_fallback_no_quote",
                "data": {"order_no": f"o{len(calls)}"}}

    with mock.patch("order.momentum_rotation_order._place_order", side_effect=fake_place), \
         mock.patch("order.momentum_rotation_order.fetch_real_fill_price", return_value=100.0), \
         mock.patch("order.momentum_rotation_hedge.resolve_hedge_symbol", return_value="_NEXT"), \
         mock.patch("order.momentum_rotation_order._place_hedge_leg",
                    return_value={"ok": False, "error": "對沖腿被拒"}):
        res = _enter_position(mock.Mock(), mock.Mock(), state, _sig(), live=True)

    assert res["ok"] is False
    assert res["hedge_failed"] is True
    assert res["main_unwound"] is True
    assert state.position["sid"] is None, "對沖失敗就不該留下部位"
    assert state.halted is False, "能乾淨回沖就不該熔斷"
    # 應該有一張主腿 Close（回沖）
    assert ("_NEXT", "Sell", "Close") in calls, f"回沖應打在次月主腿,實際 {calls}"


def test_enter_records_naked_position_and_halts_when_unwind_also_fails(monkeypatch):
    """最糟情況：對沖沒建起來、主腿也平不掉 → 必須寫進 ledger 並 halted，
    否則變成沒人管的孤兒曝險（reconcile_once 的熔斷分支才會持續重試平倉）。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", "1")
    state = _fresh_state()

    with mock.patch("order.momentum_rotation_order._place_order",
                    return_value={"ok": True, "price_source": "market_fallback_no_quote",
                                  "data": {"order_no": "oMAIN"}}), \
         mock.patch("order.momentum_rotation_order.fetch_real_fill_price", return_value=100.0), \
         mock.patch("order.momentum_rotation_hedge.resolve_hedge_symbol", return_value="_NEXT"), \
         mock.patch("order.momentum_rotation_order._place_hedge_leg",
                    return_value={"ok": False, "error": "額度不足"}), \
         mock.patch("order.momentum_rotation_order._unwind_main_leg",
                    return_value={"ok": False, "error": "回沖也被拒"}):
        res = _enter_position(mock.Mock(), mock.Mock(), state, _sig(), live=True)

    assert res["ok"] is False
    assert res["naked_exposure"] is True
    assert state.position["sid"] == _SID, "裸露部位一定要留在 ledger 裡才有人重試平倉"
    assert state.halted is True
    assert "hedge_failed_and_unwind_failed" in (state.halted_reason or "")


def test_enter_records_hedge_fields_on_success(monkeypatch):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", "1")
    state = _fresh_state()

    with mock.patch("order.momentum_rotation_order._place_order",
                    return_value={"ok": True, "price_source": "market_fallback_no_quote",
                                  "data": {"order_no": "oMAIN"}}), \
         mock.patch("order.momentum_rotation_order.fetch_real_fill_price", return_value=100.0), \
         mock.patch("order.momentum_rotation_hedge.resolve_hedge_symbol", return_value="_NEXT"), \
         mock.patch("order.momentum_rotation_order._place_hedge_leg",
                    return_value={"ok": True, "hedge_symbol": "_FRONT",
                                  "hedge_order_no": "oHEDGE", "hedge_fill": 101.0}):
        res = _enter_position(mock.Mock(), mock.Mock(), state, _sig(), live=True)

    assert res.get("ok") is True
    assert state.position["main_symbol"] == "_NEXT", "主腿應在次月"
    assert state.position["hedge_symbol"] == "_FRONT", "反向腿應在近月"
    assert state.position["hedge_order_no"] == "oHEDGE"
    assert state.position["hedge_fill"] == 101.0
    assert state.position["hedge_closed"] is False


def test_enter_skips_hedge_entirely_when_disabled(monkeypatch):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", "0")
    state = _fresh_state()
    with mock.patch("order.momentum_rotation_order._place_order",
                    return_value={"ok": True, "price_source": "market_fallback_no_quote",
                                  "data": {"order_no": "oMAIN"}}), \
         mock.patch("order.momentum_rotation_order.fetch_real_fill_price", return_value=100.0), \
         mock.patch("order.momentum_rotation_order._place_hedge_leg") as hedge:
        _enter_position(mock.Mock(), mock.Mock(), state, _sig(), live=True)
    hedge.assert_not_called()
    assert state.position["hedge_symbol"] is None


# --------------------------------------------------------------------------
# 出場：對沖腿先平、主腿後平
# --------------------------------------------------------------------------

def _held_state() -> LedgerState:
    state = _fresh_state()
    state.position = asdict(PositionState(
        sid=_SID, direction="long", fill=100.0, entry_score=2.0, peak_trough=100.0,
        entry_order_no="oMAIN", entry_time="2026-08-31T09:59:00+08:00", fill_source="real",
        main_symbol="_NEXT", hedge_symbol="_FRONT", hedge_order_no="oHEDGE", hedge_fill=101.0,
    ))
    return state


def test_close_closes_hedge_leg_before_main_leg():
    """順序是刻意的：對沖腿先平失敗 → 兩腿都還在 → 仍是對沖狀態（安全）。
    主腿先平失敗才會留裸露曝險。"""
    state = _held_state()
    calls: list[tuple[str, str]] = []

    def fake_place(_s, _a, symbol, buy_sell, order_type, _live, _user_def):
        calls.append((symbol, order_type))
        return {"ok": True, "price_source": "market_fallback_no_quote",
                "data": {"order_no": f"c{len(calls)}"}}

    with mock.patch("order.momentum_rotation_order._place_order", side_effect=fake_place), \
         mock.patch("order.momentum_rotation_order.fetch_real_fill_price", return_value=99.0), \
         mock.patch("order.momentum_rotation_order.save_ledger"):
        res = _close_position(mock.Mock(), mock.Mock(), state, 99.0, "trail_stop", live=True)

    assert res.get("ok") is True
    assert calls[0][0] == "_FRONT", f"第一張平倉單必須是對沖腿(近月)，實際是 {calls[0]}"
    assert calls[1][0] == "_NEXT", "第二張才是主腿(次月)"
    assert state.position["sid"] is None


def test_close_keeps_both_legs_when_hedge_close_rejected():
    """對沖腿平不掉 → 直接返回、主腿一張都不送 → 維持對沖狀態等下一輪重試。"""
    state = _held_state()
    calls: list[str] = []

    def fake_place(_s, _a, symbol, _bs, _ot, _live, _ud):
        calls.append(symbol)
        return {"ok": False, "message": "交易額度超過資(財)力證明額度"}

    with mock.patch("order.momentum_rotation_order._place_order", side_effect=fake_place), \
         mock.patch("order.momentum_rotation_order.save_ledger"):
        res = _close_position(mock.Mock(), mock.Mock(), state, 99.0, "trail_stop", live=True)

    assert res["ok"] is False
    assert res["leg"] == "hedge"
    assert calls == ["_FRONT"], "對沖腿失敗後絕不可以繼續送主腿平倉單"
    assert state.position["sid"] == _SID, "部位要保留供重試"
    assert state.position["hedge_closed"] is False


def test_close_does_not_double_close_hedge_on_retry():
    """對沖腿已平、主腿失敗後重試：只能再送主腿，不能再平對沖腿一次
    （對已平倉的腿送 Close 在 broker 端會變成反向新倉，不是 no-op）。"""
    state = _held_state()
    state.position["hedge_closed"] = True
    calls: list[str] = []

    def fake_place(_s, _a, symbol, _bs, _ot, _live, _ud):
        calls.append(symbol)
        return {"ok": True, "price_source": "market_fallback_no_quote",
                "data": {"order_no": "cX"}}

    with mock.patch("order.momentum_rotation_order._place_order", side_effect=fake_place), \
         mock.patch("order.momentum_rotation_order.fetch_real_fill_price", return_value=99.0), \
         mock.patch("order.momentum_rotation_order.save_ledger"):
        _close_position(mock.Mock(), mock.Mock(), state, 99.0, "trail_stop", live=True)

    assert calls == ["_NEXT"], f"對沖腿已平就只該送主腿(次月)，實際送了 {calls}"


# --------------------------------------------------------------------------
# anchor bug 重現
# --------------------------------------------------------------------------

def _bars(times_prices):
    return [{"t": f"2026-08-31T{t}:00.000", "o": p, "h": p, "l": p, "c": p, "v": 5.0}
            for t, p in times_prices]


def test_anchor_seed_after_discards_early_bars():
    """設了門檻就丟棄早於門檻的 bar，讓 open_price 被門檻後第一筆種下——
    這是 8/13「08:45–09:13 抓到 0 筆、anchor 被 09:1x 的盤中價種下」的決定性等價版。"""
    state = _fresh_state()

    state.stock_states = {sid: asdict(StockState()) for sid in UNIVERSE}
    bars = _bars([("08:45", 342.0), ("09:00", 335.0), ("09:16", 326.5), ("09:20", 330.0)])

    with mock.patch("order.momentum_rotation_order.ANCHOR_SEED_AFTER", "09:15"), \
         mock.patch("order.momentum_rotation_order._resolve_live_symbol", return_value="_FRONT"), \
         mock.patch("order.momentum_rotation_tick_marketdata.fetch_ticks_as_bars", return_value=bars), \
         mock.patch("order.momentum_rotation_websocket_feed.fetch_ticks_as_bars_ws", return_value=None):
        # UNIVERSE 其他檔沒有 live_symbol，_resolve_live_symbol 被 patch 成固定值，
        # 所以每檔都會拿到同一批 bar；只檢查 3017 的 anchor 即可
        _feed_new_bars(mock.Mock(), state)

    assert state.stock_states[_SID]["open_price"] == 326.5, (
        "anchor 應被 09:16 那筆種下（8/13 的行為），不是 08:45 的真實開盤價 342.0"
    )


def test_anchor_uses_true_open_when_threshold_unset():
    """未設門檻（預設）＝正常行為，anchor 用真實開盤價。"""
    state = _fresh_state()
    state.stock_states = {sid: asdict(StockState()) for sid in UNIVERSE}
    bars = _bars([("08:45", 342.0), ("09:16", 326.5)])

    with mock.patch("order.momentum_rotation_order.ANCHOR_SEED_AFTER", ""), \
         mock.patch("order.momentum_rotation_order._resolve_live_symbol", return_value="_FRONT"), \
         mock.patch("order.momentum_rotation_tick_marketdata.fetch_ticks_as_bars", return_value=bars), \
         mock.patch("order.momentum_rotation_websocket_feed.fetch_ticks_as_bars_ws", return_value=None):
        _feed_new_bars(mock.Mock(), state)

    assert state.stock_states[_SID]["open_price"] == 342.0


# --------------------------------------------------------------------------
# 2026-08-30 第二輪：腿的分配（主單次月／反向單近月）與強制市價
# --------------------------------------------------------------------------

def test_main_leg_goes_to_next_month_hedge_stays_front(monkeypatch):
    """使用者指定的配置：訊號看近月，但**主單下次月、反向單留近月**。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", "1")
    state = _fresh_state()          # live_symbols = {_SID: "_FRONT"}（近月）
    calls: list[tuple[str, str, str]] = []

    def fake_place(_s, _a, symbol, buy_sell, order_type, _live, _ud):
        calls.append((symbol, buy_sell, order_type))
        return {"ok": True, "price_source": "market_fallback_no_quote",
                "data": {"order_no": f"o{len(calls)}"}}

    with mock.patch("order.momentum_rotation_order._place_order", side_effect=fake_place), \
         mock.patch("order.momentum_rotation_order.fetch_real_fill_price", return_value=100.0), \
         mock.patch("order.momentum_rotation_hedge.resolve_hedge_symbol", return_value="_NEXT"):
        _enter_position(mock.Mock(), mock.Mock(), state, _sig(), live=True)

    assert calls[0] == ("_NEXT", "Buy", "New"), f"主單(long)應打次月 Buy，實際 {calls[0]}"
    assert calls[1] == ("_FRONT", "Sell", "New"), f"反向單應打近月 Sell，實際 {calls[1]}"
    assert state.position["main_symbol"] == "_NEXT"
    assert state.position["hedge_symbol"] == "_FRONT"


def test_no_order_at_all_when_next_month_unavailable(monkeypatch):
    """查不到次月 → 整筆放棄，**一張單都不能送**。
    絕不可以退回「兩腿都在近月」——同商品同月份反向＝期交所淨額計算＝直接平掉主腿。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", "1")
    state = _fresh_state()

    with mock.patch("order.momentum_rotation_order._place_order") as place, \
         mock.patch("order.momentum_rotation_hedge.resolve_hedge_symbol", return_value=None):
        res = _enter_position(mock.Mock(), mock.Mock(), state, _sig(), live=True)

    assert res["ok"] is False
    assert "次月" in res["error"]
    place.assert_not_called(), "查不到次月時連主腿都不該送出"
    assert state.position["sid"] is None


def test_force_market_sends_market_ioc_and_still_confirms_fill(monkeypatch):
    """FORCE_MARKET=1 → 跳過報價/限價路徑直接市價 IOC，但**仍然做成交確認**
    （市價在流動性差的次月一樣可能不成交；不確認就會建幻影部位、rollback 失效）。"""
    from order.momentum_rotation_order import _needs_fill_check, _place_order

    captured = {}

    def fake_submit(_s, order, acc=None, dry_run=False):
        captured["price_type"] = order.price_type
        captured["price"] = order.price
        captured["tif"] = order.time_in_force
        return {"ok": True, "data": {"order_no": "oM"}}

    with mock.patch("order.momentum_rotation_order.FORCE_MARKET", True), \
         mock.patch("order.momentum_rotation_order.place_futopt_order", side_effect=fake_submit), \
         mock.patch("order.momentum_rotation_tick_marketdata.fetch_live_quote") as quote:
        res = _place_order(mock.Mock(), mock.Mock(), "_NEXT", "Buy", "New", True, "ud")

    quote.assert_not_called(), "強制市價時不該再去查報價"
    assert captured["price_type"] == "market"
    assert captured["price"] is None
    assert captured["tif"] == "ioc"
    assert res["price_source"] == "market_forced"
    assert _needs_fill_check(res) is True, "市價 IOC 一樣要確認成交"


def test_needs_fill_check_matrix():
    from order.momentum_rotation_order import _needs_fill_check

    assert _needs_fill_check({"price_source": "limit_from_quote"}) is True
    assert _needs_fill_check({"price_source": "market_forced"}) is True
    # 查不到報價的降級路徑維持 8/13 以前的行為（不確認），不在本次改動範圍
    assert _needs_fill_check({"price_source": "market_fallback_no_quote"}) is False
    assert _needs_fill_check({}) is False


# --------------------------------------------------------------------------
# 2026-08-30：反向轉換器與強制終止（:8770 儀表板兩顆按鈕的後端）
# --------------------------------------------------------------------------

def test_reverse_mode_defaults_off(monkeypatch, tmp_path):
    import order.momentum_rotation_order as mro

    monkeypatch.setattr(mro, "_REVERSE_PATH", tmp_path / "rev.json")
    monkeypatch.delenv("ORDER_MOMENTUM_ROTATION_REVERSE", raising=False)
    on, src = mro.reverse_mode()
    assert on is False and src == "env"


def test_reverse_file_overrides_env(monkeypatch, tmp_path):
    import order.momentum_rotation_order as mro

    monkeypatch.setattr(mro, "_REVERSE_PATH", tmp_path / "rev.json")
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_REVERSE", "0")
    mro.set_reverse_mode(True, by="test")
    on, src = mro.reverse_mode()
    assert on is True
    assert src.startswith("dashboard@")


def test_reverse_file_expires_next_day(monkeypatch, tmp_path):
    """date 欄位對不上今天就失效——避免「昨天翻過去忘了翻回來」靜默殘留。"""
    import json as _json

    import order.momentum_rotation_order as mro

    p = tmp_path / "rev.json"
    p.write_text(_json.dumps({"date": "2020-01-01", "reversed": True}))
    monkeypatch.setattr(mro, "_REVERSE_PATH", p)
    monkeypatch.delenv("ORDER_MOMENTUM_ROTATION_REVERSE", raising=False)
    assert mro.reverse_mode() == (False, "env")


@pytest.mark.parametrize("orig,want_side", [("long", "Sell"), ("short", "Buy")])
def test_reverse_flips_the_order_side(monkeypatch, tmp_path, orig, want_side):
    """反向開啟時，實際送出去的買賣別要翻過來；fill 價（門檻價）不變。"""
    import order.momentum_rotation_order as mro

    monkeypatch.setattr(mro, "_REVERSE_PATH", tmp_path / "rev.json")
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_HEDGE_ENABLED", "0")
    mro.set_reverse_mode(True, by="test")

    state = _fresh_state()
    sig = BreakoutSignal(sid=_SID, direction=orig, fill=100.0,
                         overshoot=1.0, vol_ratio=2.0, score=2.0)
    calls = []

    def fake_place(_s, _a, symbol, buy_sell, order_type, _live, _ud):
        calls.append((symbol, buy_sell, order_type))
        return {"ok": True, "price_source": "market_fallback_no_quote",
                "data": {"order_no": "oX"}}

    with mock.patch("order.momentum_rotation_order._place_order", side_effect=fake_place), \
         mock.patch("order.momentum_rotation_order.fetch_real_fill_price", return_value=100.0):
        res = _enter_position(mock.Mock(), mock.Mock(), state, sig, live=True)

    assert calls[0][1] == want_side, f"{orig} 反向後應送 {want_side}，實際 {calls[0][1]}"
    assert state.position["direction"] != orig, "ledger 要記實際做的那一邊"
    assert state.position["entry_reversed"] is True
    assert state.position["fill"] == 100.0, "門檻價不因翻向而改變"
    assert res["reversed"] is True


def test_kill_switch_round_trip(monkeypatch, tmp_path):
    import order.momentum_rotation_order as mro

    monkeypatch.setattr(mro, "_KILL_SWITCH_PATH", tmp_path / "kill.json")
    assert mro.kill_switch_reason() is None
    mro.set_kill_switch("手動終止", by="test")
    assert mro.kill_switch_reason() == "手動終止"
    mro.clear_kill_switch()
    assert mro.kill_switch_reason() is None


# --------------------------------------------------------------------------
# 2026-08-31:安全閘門(處置/漲跌停)每日快取
# --------------------------------------------------------------------------

def test_safety_gates_disposal_and_limits(monkeypatch, tmp_path):
    import order.momentum_rotation_order as mro

    # 假處置檔:一檔今天在窗內、一檔已過期
    dd = tmp_path / "disposal"; dd.mkdir()
    today = mro._today()
    (dd / "disposal_windows.csv").write_text(
        "market,stock_id,start,end,measure\n"
        f"TWSE,2492,2020-01-01,2020-01-10,第一次處置\n"
        f"TWSE,3374,{today},{today},第一次處置\n")
    monkeypatch.setattr(mro.stock_db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mro, "_SAFETY_CACHE", {"date": None, "disposal": set(), "limits": {}})
    g = mro._safety_gates()
    assert "3374" in g["disposal"] and "2492" not in g["disposal"]
    # limits 走真 DB(DEFAULT_DB_PATH 不受 DATA_DIR monkeypatch 影響):
    # 有 DB 時是 {sid: (up, down)};無 DB 時 fail-open 空 dict。兩者皆合法,只驗形狀
    for _sid, band in g["limits"].items():
        up, down = band
        assert up > down > 0
    # 同日第二次呼叫走快取
    assert mro._safety_gates() is g or mro._safety_gates()["date"] == today
