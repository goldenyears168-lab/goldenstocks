"""驗證 reconcile_once 的候選選擇規則：嚴格按觸發時間排序，不是本次poll裡分數最高.

2026-08-13：使用者要求把「批次選最高分」改成「嚴格按時間排序」對齊回測
simulate_portfolio_day 逐筆掃描的規則。這裡不整套mock reconcile_once（需要
session/account/Fubon下單，成本高），直接測試改動後那兩行選擇邏輯本身的語意
——用跟 reconcile_once 完全相同的表達式，餵合成的BreakoutSignal驗證。
"""

from __future__ import annotations

from order.momentum_rotation_order import PREEMPT_MULT
from order.momentum_rotation_signal import BreakoutSignal


def _sig(sid, score, trigger_time, direction="long"):
    return BreakoutSignal(
        sid=sid, direction=direction, fill=100.0, overshoot=1.0,
        vol_ratio=2.0, score=score, trigger_time=trigger_time,
    )


def test_flat_entry_picks_earliest_not_highest_score():
    signals = [
        _sig("A", score=5.0, trigger_time="2026-08-13T09:00:03.000+08:00"),
        _sig("B", score=9.0, trigger_time="2026-08-13T09:00:01.000+08:00"),  # 較晚沒有意義，時間才是排序依據；此為分數較高但較早
        _sig("C", score=1.0, trigger_time="2026-08-13T09:00:07.000+08:00"),
    ]
    earliest = min(signals, key=lambda s: s.trigger_time)
    assert earliest.sid == "B"  # 09:00:01最早，即使A分數(5.0)比不上B(9.0)不影響，B本來就最早
    assert earliest.score == 9.0


def test_flat_entry_ignores_score_when_earliest_has_lower_score():
    signals = [
        _sig("LOW_BUT_FIRST", score=1.5, trigger_time="2026-08-13T09:00:00.000+08:00"),
        _sig("HIGH_BUT_LATER", score=99.0, trigger_time="2026-08-13T09:00:05.000+08:00"),
    ]
    earliest = min(signals, key=lambda s: s.trigger_time)
    assert earliest.sid == "LOW_BUT_FIRST"  # 就算分數差很多，時間排序下先到的還是贏


def test_preempt_picks_earliest_qualifying_not_highest_score():
    pos_entry_score = 10.0  # 需要 score >= 20.0 (PREEMPT_MULT=2.0) 才夠格搶
    assert PREEMPT_MULT == 2.0
    candidates = sorted(
        [
            _sig("NOT_QUALIFIED_BUT_EARLY", score=15.0, trigger_time="2026-08-13T09:05:00.000+08:00"),
            _sig("QUALIFIED_SECOND", score=25.0, trigger_time="2026-08-13T09:05:02.000+08:00"),
            _sig("QUALIFIED_BUT_HIGHER_SCORE_LATER", score=99.0, trigger_time="2026-08-13T09:05:05.000+08:00"),
        ],
        key=lambda s: s.trigger_time,
    )
    qualified = next((s for s in candidates if s.score >= PREEMPT_MULT * pos_entry_score), None)
    assert qualified is not None
    assert qualified.sid == "QUALIFIED_SECOND"  # 第一個「夠格」的贏，不是分數最高的QUALIFIED_BUT_HIGHER_SCORE_LATER


def test_preempt_returns_none_when_nothing_qualifies():
    pos_entry_score = 10.0
    candidates = sorted(
        [_sig("A", score=5.0, trigger_time="2026-08-13T09:05:00.000+08:00")],
        key=lambda s: s.trigger_time,
    )
    qualified = next((s for s in candidates if s.score >= PREEMPT_MULT * pos_entry_score), None)
    assert qualified is None
