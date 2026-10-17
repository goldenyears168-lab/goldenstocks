"""v3 訊號(2026-08-31 盲點修正版)的行為鎖定.

鎖住的是設計決策本身,每一條對應 momentum_rotation_signal_v3.py 檔頭的盲點編號:
③ 相對大盤(股漲=指漲 → 無訊號) ④ σ 正規化 ② 進場窗 09:30 起
⑧ 成本閘門 ⑥ 跨標的以 |z| 選 ⑦ 時間出場 30 分 + 災難停損(order 層)
"""

from __future__ import annotations

from dataclasses import asdict
from unittest import mock

import pytest

from order.momentum_rotation_signal_v3 import (
    HOLD_MIN,
    LOOKBACK_MIN,
    SIGMA_MIN_OBS,
    V3Signal,
    V3State,
    cost_bps,
    signal_v3_enabled,
    tick_size,
)


# --------------------------------------------------------------------------
# 純函式
# --------------------------------------------------------------------------

def test_tick_size_brackets():
    assert tick_size(9.5) == 0.01
    assert tick_size(45) == 0.05
    assert tick_size(80) == 0.10
    assert tick_size(300) == 0.50
    assert tick_size(800) == 1.00
    assert tick_size(3000) == 5.00


def test_cost_bps_matches_measured_structure():
    """2026-08-31 逐檔實算的兩個代表值:3374@318.5≈21.6、2324@40.8≈55.9。
    公式改動若讓這兩個點漂走,代表成本模型變了,要重新驗證整套結論。"""
    assert abs(cost_bps(318.5) - 21.6) < 1.0
    assert abs(cost_bps(40.8) - 55.9) < 1.5


def test_flag_default_on(monkeypatch):
    monkeypatch.delenv("ORDER_MOMENTUM_ROTATION_SIGNAL_V3", raising=False)
    assert signal_v3_enabled() is True
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_SIGNAL_V3", "0")
    assert signal_v3_enabled() is False


# --------------------------------------------------------------------------
# 訊號狀態機
# --------------------------------------------------------------------------

def _bar(hm: str, px: float) -> dict:
    return {"t": f"2026-09-01T{hm}:00.000+08:00", "o": px, "h": px, "l": px, "c": px, "v": 2.0}


def _warmup(st: V3State, sid: str = "3374", *, base=300.0, idx=22000.0,
            start_h=9, start_m=0, n=70) -> str:
    """餵 n 分鐘平盤(股與指同步),把 σ 樣本數餵滿;回傳最後分鐘 hm。"""
    last = ""
    for i in range(n):
        h, m = divmod(start_h * 60 + start_m + i, 60)
        hm = f"{h:02d}:{m:02d}"
        st.feed_index([_bar(hm, idx)])
        st.feed_stock(sid, [_bar(hm, base)])
        last = hm
    return last


def test_no_signal_when_stock_follows_index():
    """③ 核心:個股 +1% 但指數也 +1% → 相對報酬 ≈ 0 → 不觸發。
    v1 在這個情境會進場(絕對突破 0.5%),那正是「買到 beta」的盲點。"""
    st = V3State()
    _warmup(st, n=70)
    sigs = []
    for i, hm in enumerate(("10:11", "10:12", "10:13")):
        st.feed_index([_bar(hm, 22000 * 1.01)])          # 指數 +1%
        sigs += st.feed_stock("3374", [_bar(hm, 300 * 1.01)])  # 個股同步 +1%
    assert sigs == [], "與大盤同步的走勢不該出訊號"


def test_fade_signal_on_relative_spike():
    """個股 +1%、指數持平 → 相對 +100bps,σ=floor 40 → z=+2.5 → fade 做空。"""
    st = V3State()
    _warmup(st, n=70)
    st.feed_index([_bar("10:11", 22000.0)])
    sigs = st.feed_stock("3374", [_bar("10:11", 303.0)])   # +1% vs 開盤基準
    assert len(sigs) == 1
    s = sigs[0]
    assert isinstance(s, V3Signal)
    assert s.direction == "short", "相對噴高 → 逆勢做空"
    assert s.z >= 2.0
    assert s.score == pytest.approx(abs(s.z))


def test_relative_dip_gives_long():
    st = V3State()
    _warmup(st, n=70)
    st.feed_index([_bar("10:11", 22000.0)])
    sigs = st.feed_stock("3374", [_bar("10:11", 297.0)])   # −1%
    assert len(sigs) == 1 and sigs[0].direction == "long"


def test_sigma_warmup_blocks_early_signals():
    """σ 未滿 SIGMA_MIN_OBS 前不出訊號——開盤初期樣本太少,z 沒有意義。"""
    st = V3State()
    _warmup(st, n=SIGMA_MIN_OBS + LOOKBACK_MIN - 5)   # 差 5 分鐘才暖機完
    st.feed_index([_bar("09:56", 22000.0)])
    sigs = st.feed_stock("3374", [_bar("09:56", 303.0)])
    assert sigs == []


def test_entry_window_blocks_before_0930(monkeypatch):
    """② 09:30 前不進場(現貨 09:00 開盤 + 30 分回看窗)。
    直接餵 09:29 的極端相對走勢,應該被窗口擋下。"""
    st = V3State()
    # 從 08:45 開始餵滿 σ(σ 樣本在 09:30 前就能累積,但訊號要等窗口)
    _warmup(st, start_h=8, start_m=45, n=44)          # 08:45~09:28
    st.feed_index([_bar("09:29", 22000.0)])
    sigs = st.feed_stock("3374", [_bar("09:29", 303.0)])
    assert sigs == [], "09:30 前不得進場"


def test_cost_gate_blocks_expensive_stock():
    """⑧ 2324 型的低價股(cost≈56bps > 26)永遠不出訊號,即使 z 很大。"""
    st = V3State()
    _warmup(st, sid="2324", base=40.0, n=70)
    st.feed_index([_bar("10:11", 22000.0)])
    sigs = st.feed_stock("2324", [_bar("10:11", 40.8)])   # +2%
    assert sigs == [], "成本 > 26bps 的標的要被閘門擋下"


def test_hysteresis_no_retrigger_same_wave():
    """觸發後 disarm;z 沒回到 <1 之前,同一波不能再觸發。"""
    st = V3State()
    _warmup(st, n=70)
    st.feed_index([_bar("10:11", 22000.0)])
    s1 = st.feed_stock("3374", [_bar("10:11", 303.0)])
    st.feed_index([_bar("10:12", 22000.0)])
    s2 = st.feed_stock("3374", [_bar("10:12", 303.5)])    # 還在高檔
    assert len(s1) == 1 and s2 == [], "同一波第二根不得重複觸發"


def test_daily_reset():
    st = V3State()
    _warmup(st, n=70)
    assert st.closes.get("3374")
    st.feed_stock("3374", [{"t": "2026-09-02T09:00:00.000+08:00",
                            "o": 300, "h": 300, "l": 300, "c": 300, "v": 2}])
    assert st.date == "2026-09-02"
    assert len(st.closes.get("3374", {})) == 1, "跨日要整組重置"


# --------------------------------------------------------------------------
# 下單層整合(v3 部位的出場邏輯)
# --------------------------------------------------------------------------

def test_v3_position_time_exit(monkeypatch, tmp_path):
    """⑦ v3 部位持有滿 HOLD_MIN 分鐘 → time_exit 平倉(不走移動停利)。"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    import order.momentum_rotation_order as mro

    monkeypatch.setattr(mro, "_LEDGER_PATH", tmp_path / "l.json")
    monkeypatch.setattr(mro, "_BROADCAST_PATH", tmp_path / "b.json")
    monkeypatch.setattr(mro, "_KILL_SWITCH_PATH", tmp_path / "k.json")
    monkeypatch.setattr(mro, "_REVERSE_PATH", tmp_path / "r.json")
    tz = ZoneInfo("Asia/Taipei")
    entered = (datetime.now(tz=tz) - timedelta(minutes=HOLD_MIN + 1)).isoformat()
    state = mro.LedgerState(
        date=mro._today(), stock_states={sid: asdict(mro.StockState()) for sid in mro.UNIVERSE})
    state.position = asdict(mro.PositionState(
        sid=next(iter(mro.UNIVERSE)), direction="long", fill=100.0, entry_score=2.5,
        peak_trough=100.0, entry_time=entered, fill_source="real",
        main_symbol="XXFI6", signal_version="v3"))
    state.stock_states[state.position["sid"]]["last_price"] = 100.5

    with mock.patch.object(mro, "load_ledger", return_value=state), \
         mock.patch.object(mro, "_feed_new_bars", return_value=([], None)), \
         mock.patch.object(mro, "pick_futopt_account"), \
         mock.patch.object(mro, "_close_position",
                           return_value={"ok": True}) as close, \
         mock.patch.object(mro, "in_momentum_rotation_trade_window", return_value=True), \
         mock.patch.object(mro, "_hm_now", return_value="10:30"):
        res = mro.reconcile_once(dry_run=True, session=mock.Mock())
    assert res["action"] == "closed_time_exit"
    assert close.call_args.args[4] == "time_exit"


def test_v3_position_not_preempted(monkeypatch, tmp_path):
    """v3 部位不接受搶佔——有訊號進來也只回 noop。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import order.momentum_rotation_order as mro

    monkeypatch.setattr(mro, "_LEDGER_PATH", tmp_path / "l.json")
    monkeypatch.setattr(mro, "_BROADCAST_PATH", tmp_path / "b.json")
    monkeypatch.setattr(mro, "_KILL_SWITCH_PATH", tmp_path / "k.json")
    monkeypatch.setattr(mro, "_REVERSE_PATH", tmp_path / "r.json")
    state = mro.LedgerState(
        date=mro._today(), stock_states={sid: asdict(mro.StockState()) for sid in mro.UNIVERSE})
    sid = next(iter(mro.UNIVERSE))
    state.position = asdict(mro.PositionState(
        sid=sid, direction="long", fill=100.0, entry_score=2.1, peak_trough=100.0,
        entry_time=datetime.now(tz=ZoneInfo("Asia/Taipei")).isoformat(),
        main_symbol="XXFI6", signal_version="v3"))
    challenger = V3Signal(sid="9999", direction="short", fill=50.0, score=99.0,
                          trigger_time="10:00", z=-99.0)
    with mock.patch.object(mro, "load_ledger", return_value=state), \
         mock.patch.object(mro, "_feed_new_bars", return_value=([challenger], None)), \
         mock.patch.object(mro, "pick_futopt_account"), \
         mock.patch.object(mro, "_close_position") as close, \
         mock.patch.object(mro, "in_momentum_rotation_trade_window", return_value=True), \
         mock.patch.object(mro, "_hm_now", return_value="10:05"):
        res = mro.reconcile_once(dry_run=True, session=mock.Mock())
    close.assert_not_called()
    assert res["action"] == "noop_no_signal"


def test_v3_candidates_selected_by_abs_z(monkeypatch, tmp_path):
    """⑥ 空手時多檔 v3 訊號 → 取 |z| 最大,不是最早。"""
    import order.momentum_rotation_order as mro

    monkeypatch.setattr(mro, "_LEDGER_PATH", tmp_path / "l.json")
    monkeypatch.setattr(mro, "_BROADCAST_PATH", tmp_path / "b.json")
    monkeypatch.setattr(mro, "_KILL_SWITCH_PATH", tmp_path / "k.json")
    monkeypatch.setattr(mro, "_REVERSE_PATH", tmp_path / "r.json")
    state = mro.LedgerState(
        date=mro._today(), stock_states={sid: asdict(mro.StockState()) for sid in mro.UNIVERSE})
    early_weak = V3Signal(sid="1111", direction="long", fill=100.0, score=2.1,
                          trigger_time="10:00", z=-2.1)
    late_strong = V3Signal(sid="2222", direction="short", fill=100.0, score=3.7,
                           trigger_time="10:01", z=3.7)
    with mock.patch.object(mro, "load_ledger", return_value=state), \
         mock.patch.object(mro, "_feed_new_bars", return_value=([early_weak, late_strong], None)), \
         mock.patch.object(mro, "pick_futopt_account"), \
         mock.patch.object(mro, "_enter_position",
                           return_value={"ok": True}) as enter, \
         mock.patch.object(mro, "in_momentum_rotation_trade_window", return_value=True), \
         mock.patch.object(mro, "_hm_now", return_value="10:02"):
        res = mro.reconcile_once(dry_run=True, session=mock.Mock())
    assert res["sid"] == "2222", "應選 |z| 最大的,不是最早的"
    assert enter.call_args.args[3].sid == "2222"


# --------------------------------------------------------------------------
# micro30 模式(2026-08-31 使用者指定:30秒峰值價+峰值量 fade、持倉30秒)
# --------------------------------------------------------------------------

from order.momentum_rotation_signal_v3 import (  # noqa: E402
    MICRO_HOLD_SEC,
    Micro30State,
    v3_mode,
)


def _mbar(hms: str, px: float, vol: float = 2.0) -> dict:
    return {"t": f"2026-09-01T{hms}.000+08:00", "o": px, "h": px, "l": px, "c": px, "v": vol}


def test_mode_default_micro30(monkeypatch):
    monkeypatch.delenv("ORDER_MOMENTUM_ROTATION_V3_MODE", raising=False)
    assert v3_mode() == "micro30"
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_V3_MODE", "relz30")
    assert v3_mode() == "relz30"


def test_micro_up_burst_fades_short(monkeypatch):
    """30秒內 +40bps、當下同時是峰值價與峰值量 → fade 做空。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "peak30")
    st = Micro30State()
    st.feed_stock("3374", [_mbar("10:00:00", 300.0), _mbar("10:00:10", 300.2, 4),
                           _mbar("10:00:20", 300.6, 4)])
    sigs = st.feed_stock("3374", [_mbar("10:00:25", 301.3, 8)])   # +43bps、量8=峰值
    assert len(sigs) == 1
    s = sigs[0]
    assert s.direction == "short" and s.mode == "micro30"


def test_micro_down_burst_fades_long(monkeypatch):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "peak30")
    st = Micro30State()
    st.feed_stock("3374", [_mbar("10:00:00", 300.0), _mbar("10:00:10", 299.8, 4),
                           _mbar("10:00:20", 299.4, 4)])
    sigs = st.feed_stock("3374", [_mbar("10:00:25", 298.7, 8)])
    assert len(sigs) == 1 and sigs[0].direction == "long"


def test_micro_requires_volume_peak(monkeypatch):
    """價是峰值但量不是 → 不觸發(「價跟量都」是使用者規格的原文)。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "peak30")
    st = Micro30State()
    st.feed_stock("3374", [_mbar("10:00:00", 300.0, 10), _mbar("10:00:05", 300.2, 3),
                           _mbar("10:00:10", 300.5, 4)])
    sigs = st.feed_stock("3374", [_mbar("10:00:20", 301.3, 6)])   # 量6 < 峰值10
    assert sigs == []


def test_micro_burst_too_small_no_signal(monkeypatch):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "peak30")
    st = Micro30State()
    st.feed_stock("3374", [_mbar("10:00:00", 300.0), _mbar("10:00:05", 300.05, 3),
                           _mbar("10:00:10", 300.1, 4)])
    sigs = st.feed_stock("3374", [_mbar("10:00:20", 300.5, 8)])   # 僅 +17bps < 30
    assert sigs == []


def test_micro_cooldown(monkeypatch):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "peak30")
    st = Micro30State()
    st.feed_stock("3374", [_mbar("10:00:00", 300.0), _mbar("10:00:05", 300.1, 3),
                           _mbar("10:00:10", 300.3, 4)])
    s1 = st.feed_stock("3374", [_mbar("10:00:20", 301.3, 8)])
    s2 = st.feed_stock("3374", [_mbar("10:00:40", 302.5, 16)])    # 冷卻 60 秒內
    assert len(s1) == 1 and s2 == []


def test_micro_cost_gate(monkeypatch):
    """低價股(cost>26)不觸發,同 relz 模式。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "peak30")
    st = Micro30State()
    st.feed_stock("2324", [_mbar("10:00:00", 40.0), _mbar("10:00:05", 40.05, 3),
                           _mbar("10:00:10", 40.1, 4)])
    sigs = st.feed_stock("2324", [_mbar("10:00:20", 40.4, 8)])    # +100bps 但 cost≈56
    assert sigs == []


def test_v3m_position_time_exit_after_30_seconds(monkeypatch, tmp_path):
    """v3m 部位 30 **秒** 就時間出場,不是 30 分鐘。"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    import order.momentum_rotation_order as mro

    monkeypatch.setattr(mro, "_LEDGER_PATH", tmp_path / "l.json")
    monkeypatch.setattr(mro, "_BROADCAST_PATH", tmp_path / "b.json")
    monkeypatch.setattr(mro, "_KILL_SWITCH_PATH", tmp_path / "k.json")
    monkeypatch.setattr(mro, "_REVERSE_PATH", tmp_path / "r.json")
    tz = ZoneInfo("Asia/Taipei")
    entered = (datetime.now(tz=tz) - timedelta(seconds=MICRO_HOLD_SEC + 2)).isoformat()
    state = mro.LedgerState(
        date=mro._today(), stock_states={sid: asdict(mro.StockState()) for sid in mro.UNIVERSE})
    sid = next(iter(mro.UNIVERSE))
    state.position = asdict(mro.PositionState(
        sid=sid, direction="short", fill=300.0, entry_score=1.4, peak_trough=300.0,
        entry_time=entered, main_symbol="XXFI6", signal_version="v3m"))
    state.stock_states[sid]["last_price"] = 299.5
    with mock.patch.object(mro, "load_ledger", return_value=state), \
         mock.patch.object(mro, "_feed_new_bars", return_value=([], None)), \
         mock.patch.object(mro, "pick_futopt_account"), \
         mock.patch.object(mro, "_close_position", return_value={"ok": True}) as close, \
         mock.patch.object(mro, "in_momentum_rotation_trade_window", return_value=True), \
         mock.patch.object(mro, "_hm_now", return_value="10:30"):
        res = mro.reconcile_once(dry_run=True, session=mock.Mock())
    assert res["action"] == "closed_time_exit"
    assert close.call_args.args[4] == "time_exit"


# --------------------------------------------------------------------------
# volburst10x 觸發(2026-08-31 使用者指定:5秒量 ≥ 10× 過去3分鐘均量)
# --------------------------------------------------------------------------

from order.momentum_rotation_signal_v3 import micro_trigger  # noqa: E402


def _feed_baseline(st, sid="3374", *, base_px=300.0, start=36000, secs=300, lots=2.0):
    """5 分鐘、每 5 秒一筆 2 口的安靜 baseline。start=10:00:00。
    (2026-08-31 隨 VOLBURST_BASE_SEC 180→300 同步拉長)"""
    bars=[]
    for s in range(start-secs, start, 5):
        hms=f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}"
        bars.append(_mbar(hms, base_px, lots))
    st.feed_stock(sid, bars)


def test_volburst_default_trigger(monkeypatch):
    monkeypatch.delenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", raising=False)
    assert micro_trigger() == "volburst10x"


def test_volburst_follows_up_move(monkeypatch):
    """爆量 + 5秒內上漲 → 【順勢做多】(方向來自實測:fade 是 −3.01/t=−7)。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State()
    _feed_baseline(st)                      # 均量 2口/5秒
    sigs = st.feed_stock("3374", [_mbar("10:00:02", 300.5, 100)])  # 100口:≥20×2 且金額 6,010萬 ≥5,000萬
    assert len(sigs) == 1
    assert sigs[0].direction == "long", "順勢:爆量上漲 → 做多"
    assert sigs[0].mode == "micro30"


def test_volburst_follows_down_move(monkeypatch):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State()
    _feed_baseline(st)
    sigs = st.feed_stock("3374", [_mbar("10:00:02", 299.5, 100)])
    assert len(sigs) == 1 and sigs[0].direction == "short"


def test_volburst_needs_ntd_floor(monkeypatch):
    """2026-08-31 使用者指定:5秒金額 <5,000萬 → 不觸發(即使倍數已過 20×)。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State()
    _feed_baseline(st)
    sigs = st.feed_stock("3374", [_mbar("10:00:02", 300.5, 50)])   # 50口=3,000萬 <5,000萬金額地板
    assert sigs == []


def test_volburst_flat_price_no_signal(monkeypatch):
    """爆量但價格全平 → 無方向可言,不觸發。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State()
    _feed_baseline(st)
    sigs = st.feed_stock("3374", [_mbar("10:00:02", 300.0, 100)])
    assert sigs == []


def test_peak30_still_available(monkeypatch):
    """MICRO_TRIGGER=peak30 → 退回 30秒峰值 fade(先前規格保留)。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "peak30")
    st = Micro30State()
    st.feed_stock("3374", [_mbar("10:00:00", 300.0), _mbar("10:00:05", 300.1, 3),
                           _mbar("10:00:10", 300.3, 4)])
    sigs = st.feed_stock("3374", [_mbar("10:00:20", 301.3, 8)])
    assert len(sigs) == 1 and sigs[0].direction == "short", "peak30 是 fade"


# --------------------------------------------------------------------------
# 單筆大額列印豁免路徑(2026-08-31:單筆 ≥5,000萬 → 不檢查 20× 與 5 秒窗)
# --------------------------------------------------------------------------

def test_single_print_50m_bypasses_multiplier(monkeypatch):
    """一筆 90口@300(=5,400萬)直接觸發,即使 baseline 很活躍(倍數遠不到 20×)。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State()
    # 活躍 baseline:每 5 秒 50 口(avg5=50,20× 門檻=1000 口——窗版絕對過不了)
    _feed_baseline(st, lots=50.0)
    st.feed_stock("3374", [_mbar("10:00:01", 299.5, 2)])   # 先給一個不同價當方向參考
    sigs = st.feed_stock("3374", [_mbar("10:00:03", 300.5, 90)])   # 單筆 5,409 萬
    assert len(sigs) == 1
    assert sigs[0].direction == "long"      # vs 前一個不同價 299.5 → 上
    assert sigs[0].score >= 1.0


def test_single_print_below_50m_no_bypass(monkeypatch):
    """單筆 80口@300 = 4,800萬 < 5,000萬 → 不走豁免;窗版倍數也不足 → 無訊號。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State()
    _feed_baseline(st, lots=50.0)
    st.feed_stock("3374", [_mbar("10:00:01", 299.5, 2)])
    sigs = st.feed_stock("3374", [_mbar("10:00:03", 300.5, 80)])
    assert sigs == []


def test_single_print_no_direction_reference_no_signal(monkeypatch):
    """單筆過 5,000萬但 60 秒內全平(找不到不同價)→ 無方向可言,不觸發。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State()
    _feed_baseline(st, base_px=300.0, lots=2.0)     # baseline 全是 300.0 同價
    sigs = st.feed_stock("3374", [_mbar("10:00:03", 300.0, 90)])
    assert sigs == []


def test_signal_carries_volume_trend_observations(monkeypatch):
    """2026-08-31:vol_r1/vol_r5 觀測欄位要跟著訊號走(記錄用,不影響觸發)。
    情境:前 10 分鐘穩定量、最後 1 分鐘轉安靜 → r1 應 <1(量縮)。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State()
    start = 36000                     # 10:00:00
    bars = []
    for s in range(start - 610, start - 60, 5):      # s-610 ~ s-60:每5秒 4 口
        hms = f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}"
        bars.append(_mbar(hms, 300.0, 4.0))
    bars.append(_mbar("09:59:30", 299.5, 2.0))       # 最後 1 分鐘只有零星 2 口(量縮)
    st.feed_stock("3374", bars)
    sigs = st.feed_stock("3374", [_mbar("10:00:00", 300.5, 90)])   # 單筆 5,400 萬觸發
    assert len(sigs) == 1
    s0 = sigs[0]
    assert s0.vol_r1 is not None and s0.vol_r1 < 0.7, f"最後1分鐘安靜,r1 應顯示量縮,得到 {s0.vol_r1}"
    assert s0.vol_r5 is not None and s0.vol_r5 > 0.5, f"5分/前5分應接近 1,得到 {s0.vol_r5}"


def test_signal_carries_idx_5m_observation(monkeypatch):
    """idx_5m 觀測欄位:有餵指數 → 訊號帶台指5分變動;沒餵 → None(不影響觸發)。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State()
    # 指數:5 分鐘前 22000 → 現在 22044(+20bp)
    st.feed_index([_mbar("09:54:50", 22000.0), _mbar("09:59:50", 22044.0)])
    _feed_baseline(st, lots=2.0)
    st.feed_stock("3374", [_mbar("09:59:55", 299.5, 2)])
    sigs = st.feed_stock("3374", [_mbar("10:00:00", 300.5, 90)])
    assert len(sigs) == 1
    assert sigs[0].idx_5m is not None and abs(sigs[0].idx_5m - 20.0) < 3.0

    st2 = Micro30State()          # 沒餵指數
    _feed_baseline(st2, lots=2.0)
    st2.feed_stock("3374", [_mbar("09:59:55", 299.5, 2)])
    sigs2 = st2.feed_stock("3374", [_mbar("10:00:00", 300.5, 90)])
    assert len(sigs2) == 1 and sigs2[0].idx_5m is None, "沒有指數也要照常觸發"


# --------------------------------------------------------------------------
# 訊號源搬遷(2026-08-31 盲點檢定後):現貨觸發 · 單位換算 · 安全閘門
# --------------------------------------------------------------------------

from order.momentum_rotation_signal_v3 import v3_source  # noqa: E402


def test_source_default_spot(monkeypatch):
    monkeypatch.delenv("ORDER_MOMENTUM_ROTATION_V3_SOURCE", raising=False)
    assert v3_source() == "spot"
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_V3_SOURCE", "futures")
    assert v3_source() == "futures"


def test_unit_shares_spot_vs_futures(monkeypatch):
    """單位地雷測試:同樣 90 單位 @300 —— 現貨(張=1000)=2,700萬 不觸發;
    期貨(口=2000)=5,400萬 觸發。unit_shares 弄反,門檻靜默偏 2 倍。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    fut = Micro30State(unit_shares=2000.0)
    _feed_baseline(fut, lots=2.0)
    fut.feed_stock("3374", [_mbar("10:00:01", 299.5, 2)])
    assert len(fut.feed_stock("3374", [_mbar("10:00:03", 300.5, 90)])) == 1

    spot = Micro30State(unit_shares=1000.0)
    _feed_baseline(spot, lots=2.0)
    spot.feed_stock("3374", [_mbar("10:00:01", 299.5, 2)])
    assert spot.feed_stock("3374", [_mbar("10:00:03", 300.5, 90)]) == []   # 2,700萬 < 5,000萬
    # 現貨要 167 張 @300 才過 5,000 萬
    spot2 = Micro30State(unit_shares=1000.0)
    _feed_baseline(spot2, lots=2.0)
    spot2.feed_stock("3374", [_mbar("10:00:01", 299.5, 2)])
    assert len(spot2.feed_stock("3374", [_mbar("10:00:03", 300.5, 170)])) == 1


def test_spot_entry_end_cap(monkeypatch):
    """現貨實例 entry_end_s=13:24:59:13:25 後不觸發(避開 13:30 收盤撮合列印)。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State(unit_shares=1000.0, entry_end_s=13 * 3600 + 24 * 60 + 59)
    start = 13 * 3600 + 26 * 60          # 13:26
    bars = []
    for s in range(start - 610, start - 60, 5):
        hms = f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}"
        bars.append(_mbar(hms, 300.0, 2.0))
    bars.append(_mbar("13:25:30", 299.5, 2.0))
    st.feed_stock("3374", bars)
    sigs = st.feed_stock("3374", [_mbar("13:26:00", 300.5, 170)])
    assert sigs == [], "13:24:59 之後現貨不得觸發"


def test_last_trigger_recorded(monkeypatch):
    """last_trigger 要記下觸發秒(fut_confirm 的依據)。"""
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", "volburst10x")
    st = Micro30State(unit_shares=2000.0)
    _feed_baseline(st, lots=2.0)
    st.feed_stock("3374", [_mbar("10:00:01", 299.5, 2)])
    st.feed_stock("3374", [_mbar("10:00:03", 300.5, 90)])
    assert abs(st.last_trigger.get("3374", 0) - 36003) < 1


# --------------------------------------------------------------------------
# 2026-09-01:現貨五檔觀察欄位(books 頻道 → book_snapshot)
# --------------------------------------------------------------------------

def test_spot_feed_books_snapshot():
    from order.momentum_rotation_spot_feed import SpotTradesFeed

    feed = SpotTradesFeed.__new__(SpotTradesFeed)
    import threading
    feed._symbols = ["2492"]
    feed._buffers = {"2492": []}
    feed._books = {}
    feed._lock = threading.Lock()
    feed._started = True
    feed._last_msg_mono = 0.0
    feed._on_message({"channel": "books", "event": "data", "data": {
        "symbol": "2492",
        "bids": [{"price": 244.0, "size": 10}, {"price": 243.5, "size": 20}],
        "asks": [{"price": 244.5, "size": 5}, {"price": 245.0, "size": 5}],
    }})
    bk = feed.book_snapshot("2492")
    assert bk is not None
    assert bk["b1"] == 244.0 and bk["a1"] == 244.5
    assert bk["bv"] == 30 and bk["av"] == 10
    assert bk["imb"] == 0.5                       # (30−10)/(30+10)
    assert abs(bk["spr_bp"] - (0.5 / 244.0 * 1e4)) < 0.11
    assert bk["age_s"] >= 0
    # trades buffer 不受 books 訊息污染
    assert feed._buffers["2492"] == []


def test_spot_feed_books_malformed_returns_none():
    from order.momentum_rotation_spot_feed import SpotTradesFeed
    import threading

    feed = SpotTradesFeed.__new__(SpotTradesFeed)
    feed._symbols = ["2492"]
    feed._buffers = {"2492": []}
    feed._books = {}
    feed._lock = threading.Lock()
    feed._started = True
    feed._last_msg_mono = 0.0
    assert feed.book_snapshot("2492") is None     # 沒收過 books
    feed._on_message({"channel": "books", "event": "data", "data": {
        "symbol": "2492", "bids": [{"px": "x"}], "asks": []}})
    assert feed.book_snapshot("2492") is None     # 壞格式不炸、回 None


def test_peek_spot_feed_never_creates():
    import order.momentum_rotation_spot_feed as sf
    old = sf._FEED
    try:
        sf._FEED = None
        assert sf.peek_spot_feed() is None
    finally:
        sf._FEED = old


# --------------------------------------------------------------------------
# 2026-09-01:池 v3 env 閘門(預設 v2,盤中 crash-restart 不得意外換池)
# --------------------------------------------------------------------------

def test_pool_default_is_v2():
    import subprocess, sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import os; os.environ.pop('ORDER_MOMENTUM_ROTATION_POOL', None); "
         "from order.momentum_rotation_signal import UNIVERSE; "
         "print('2303' in UNIVERSE, len(UNIVERSE))"],
        capture_output=True, text=True, env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"})
    assert out.stdout.strip() == "True 19", out.stderr


def test_pool_v3_via_env():
    import subprocess, sys
    out = subprocess.run(
        [sys.executable, "-c",
         "from order.momentum_rotation_signal import UNIVERSE, FUTURES_ROOT; "
         "print('3406' in UNIVERSE, len(UNIVERSE), FUTURES_ROOT['3189'])"],
        capture_output=True, text=True,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin",
             "ORDER_MOMENTUM_ROTATION_POOL": "v3"})
    assert out.stdout.strip() == "True 8 IX", out.stderr


# --------------------------------------------------------------------------
# 2026-09-01 深夜:V4 凍結候選(四層過濾+持有300秒)
# --------------------------------------------------------------------------

def _mkstate():
    from order.momentum_rotation_signal_v3 import Micro30State
    st = Micro30State(unit_shares=1000.0)
    st.date = "2026-09-01"
    return st


def _feed_burst(st, sid, hm_s, px, lots, monkeypatch=None):
    """餵 5 分鐘安靜基準後打一筆大單,回傳訊號 list。"""
    bars = []
    base = hm_s - 360
    for k in range(0, 355, 5):
        bars.append({"t": f"2026-09-01T{(base+k)//3600:02d}:{((base+k)%3600)//60:02d}:{(base+k)%60:02d}", "c": px, "v": 1})
    bars.append({"t": f"2026-09-01T{hm_s//3600:02d}:{(hm_s%3600)//60:02d}:{hm_s%60:02d}", "c": px, "v": lots})
    # 前一筆不同價(方向判定用)
    bars.insert(-1, {"t": f"2026-09-01T{(hm_s-2)//3600:02d}:{((hm_s-2)%3600)//60:02d}:{(hm_s-2)%60:02d}", "c": px - 0.5, "v": 1})
    return st.feed_stock(sid, bars)


def test_v4_off_passes_flat_r1(monkeypatch):
    monkeypatch.delenv("ORDER_MOMENTUM_ROTATION_V4", raising=False)
    st = _mkstate()
    sigs = _feed_burst(st, "2492", 10 * 3600, 300.0, 200)   # 200張×300=6000萬
    assert len(sigs) == 1                                    # V4 關:量能持平也放行


def test_v4_blocks_flat_r1_and_endprint(monkeypatch):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_V4", "1")
    st = _mkstate()
    # 量能持平(基準每5秒1張,觸發前1分鐘也一樣)→ r1≈1 → 擋
    sigs = _feed_burst(st, "2492", 10 * 3600, 300.0, 200)
    assert sigs == []
    # 終點單:400張×300元=1.2億 → 擋(即使其他條件過)
    st2 = _mkstate()
    sigs2 = _feed_burst(st2, "2492", 10 * 3600, 300.0, 400)
    assert sigs2 == []


def test_v4_solo_blocks_repeat(monkeypatch):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_V4", "1")
    from order.momentum_rotation_signal_v3 import Micro30State
    st = _mkstate()
    st.last_dir_trigger[("2492", "long")] = 10 * 3600 - 100   # 100秒前同向觸發過
    # 做一個量縮前置的合格爆量:基準活躍、最後一分鐘安靜
    bars = []
    base = 10 * 3600 - 360
    for k in range(0, 295, 5):
        bars.append({"t": f"2026-09-01T{(base+k)//3600:02d}:{((base+k)%3600)//60:02d}:{(base+k)%60:02d}", "c": 300.0, "v": 10})
    bars.append({"t": f"2026-09-01T09:59:58", "c": 299.5, "v": 1})
    bars.append({"t": f"2026-09-01T10:00:00", "c": 300.0, "v": 200})
    sigs = st.feed_stock("2492", bars)
    assert sigs == []                                         # 單發擋掉
    assert st.last_dir_trigger[("2492", "long")] == 10 * 3600  # 但時鐘有更新


def test_v4_hold_300(monkeypatch):
    monkeypatch.setenv("ORDER_MOMENTUM_ROTATION_V4", "1")
    from order.momentum_rotation_signal_v3 import v4_mode, V4_HOLD_SEC
    assert v4_mode() and V4_HOLD_SEC == 300
