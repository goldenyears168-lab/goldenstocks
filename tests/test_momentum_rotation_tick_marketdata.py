"""驗證 momentum_rotation_tick_marketdata 的逐筆adapter跟回測邏輯等價.

2026-08-13：momentum-rotation 從1分K改逐筆(trades)當即時資料來源（使用者明確要求
「比照回測用的逐筆辦理，最細的刻度」）。這裡驗證的不是Fubon trades() API本身
（休市無法對真實API打，見momentum_rotation_tick_marketdata.py檔頭docstring的
已知殘留風險），是驗證「拿到逐筆資料之後，adapter+process_new_bar這條路徑」跟
「回測simulate_portfolio_day的inline逐筆邏輯」在同一份真實歷史tick資料上，
會不會算出同一組訊號——這是這次改動唯一新增、且今天就能驗證的部分。
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path

from order.momentum_rotation_tick_marketdata import _dedupe_same_second, _normalize_tick, _parse_trade_dt
from order.momentum_rotation_signal import StockState, process_new_bar

TICK_DIR = Path(__file__).resolve().parents[1] / "reports/research/expert_pool_futures_tick"
FIXTURE = TICK_DIR / "ctrl_3017_RAF_tick_2026-07-13_2026-08-11.csv"
FIXTURES = [
    FIXTURE,
    TICK_DIR / "ctrl_3017_RAF_tick_2025-10-20_2025-11-24.csv",
    TICK_DIR / "w3_3017_RAF_tick_2025-11-28_2025-12-19.csv",
    TICK_DIR / "w4_3017_RAF_tick_2026-02-09_2026-03-06.csv",
    TICK_DIR / "newu_2049_FFF_tick_2026-07-13_2026-08-11.csv",
    TICK_DIR / "w3_2345_OPF_tick_2025-11-28_2025-12-19.csv",
]


def _load_one_day(path: Path, target_date: str) -> list[tuple[str, float, float]]:
    rows = []
    with path.open() as f:
        for row in csv.DictReader(f):
            if not row.get("price") or row["date"][:10] != target_date:
                continue
            rows.append((row["date"], float(row["price"]), float(row.get("volume") or 0)))
    rows.sort(key=lambda r: r[0])
    return rows


def _reference_signals(rows: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """直接複製 simulate_portfolio_day 單一標的那段inline邏輯（不含跨標的搶佔/
    輪動——那層不是這次tick adapter改動的範圍），當成信任的ground truth。"""
    if not rows:
        return []
    open_price = rows[0][1]
    long_trigger = open_price * 1.005
    short_trigger = open_price * 0.995
    rearm_hi = open_price * 1.0025
    rearm_lo = open_price * 0.9975
    armed = True
    vol_hist: list[float] = []
    out = []
    for _t, p, v in rows[1:]:
        baseline = max(statistics.median(vol_hist), 1e-9) if vol_hist else 1.0
        if not armed:
            if rearm_lo <= p <= rearm_hi:
                armed = True
            vol_hist.append(v)
            continue
        hits_long, hits_short = p >= long_trigger, p <= short_trigger
        if (hits_long or hits_short) and v >= 1.5 * baseline:
            direction = "long" if hits_long else "short"
            fill = long_trigger if direction == "long" else short_trigger
            overshoot = abs(p - fill) / open_price * 100.0
            vol_ratio = v / baseline
            if overshoot >= 0.15 and vol_ratio >= 1.5:
                out.append((direction, round(fill, 4), round(overshoot, 4)))
                armed = False  # simulate_portfolio_day 進場後會把armed設False，這裡對齊同一行為
        vol_hist.append(v)
    return out


def _adapter_signals(rows: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """走這次新增的adapter路徑：raw rows -> _normalize_tick -> _dedupe_same_second
    -> process_new_bar（跟live下單層完全同一支函式，不是另外複製一份邏輯）。"""
    raw = [{"date": t, "price": p, "volume": v} for t, p, v in rows]
    normed = [_normalize_tick(r) for r in raw]
    normed = [n for n in normed if n is not None]
    bars = _dedupe_same_second(normed)

    state = StockState()
    out = []
    for bar in bars:
        sig = process_new_bar(state, bar["t"], bar["o"], bar["h"], bar["l"], bar["c"], bar["v"])
        if sig:
            out.append((sig.direction, round(sig.fill, 4), round(sig.overshoot, 4)))
            state.armed = False  # process_new_bar本身不disarm（呼叫端_enter_position才會做），
            # 這裡手動模擬呼叫端行為，才是跟production一致的比較基準
    return out


def test_tick_adapter_matches_backtest_reference_on_real_data():
    total_tested_days = 0
    for fixture in FIXTURES:
        if not fixture.exists():
            continue
        days: dict[str, list] = {}
        with fixture.open() as f:
            for row in csv.DictReader(f):
                if not row.get("price"):
                    continue
                days.setdefault(row["date"][:10], True)

        for d in sorted(days):
            rows = _load_one_day(fixture, d)
            if len(rows) < 20:
                continue
            ref = _reference_signals(rows)
            got = _adapter_signals(rows)
            assert got == ref, f"訊號不一致 file={fixture.name} date={d}: adapter={got} vs reference={ref}"
            total_tested_days += 1

    assert total_tested_days >= 20, f"測試涵蓋天數太少（{total_tested_days}天），fixture資料可能有問題"


def test_parse_trade_dt_handles_real_fugle_microsecond_epoch_row():
    """2026-08-13盤中真實API回應（RAFH6/3017）：'time'是微秒epoch整數，不是ISO
    字串——這是造成當天開盤到09:13全部12檔open_price都是None、AI watch agent
    正確觸發kill switch的根因。這裡直接用當時抓到的原始row重現，鎖住修復。"""
    real_row = {"bid": 2980, "ask": 2990, "price": 2990, "size": 1, "time": 1786583695197000, "serial": 8894101}
    dt = _parse_trade_dt(real_row)
    assert dt is not None, "微秒epoch欄位必須能被解析出時間，這是2026-08-13早上的真實bug"
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-13 09:14:55"

    normed = _normalize_tick(real_row)
    assert normed is not None
    assert normed["p"] == 2990.0
    assert normed["v"] == 1.0


def test_parse_trade_dt_falls_back_to_iso_string_when_present():
    """非trades()來源的row（例如未來欄位改版、或混用candles()格式）仍要能解析
    ISO字串格式，不因為新增微秒epoch分支就砍斷舊格式的相容性。"""
    row = {"price": 100.0, "volume": 5, "date": "2026-08-13T09:00:00+08:00"}
    dt = _parse_trade_dt(row)
    assert dt is not None
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-13 09:00:00"


def test_parse_trade_dt_returns_none_on_garbage_without_crashing():
    assert _parse_trade_dt({"price": 100.0}) is None
    assert _parse_trade_dt({"time": "not_a_number", "price": 100.0}) is None
    assert _normalize_tick({"time": "not_a_number", "price": 100.0}) is None


def test_dedupe_same_second_produces_strictly_increasing_timestamps():
    """真實tick CSV已經證實過同一秒可以有多筆成交（見momentum_rotation_tick_marketdata
    檔頭docstring）——這裡直接針對這個邊界情況做單元測試，不只靠上面的整天回放。"""
    raw = [
        {"date": "2025-11-07 09:00:00", "price": 100.0, "volume": 1},
        {"date": "2025-11-07 09:00:00", "price": 100.5, "volume": 1},
        {"date": "2025-11-07 09:00:00", "price": 101.0, "volume": 1},
        {"date": "2025-11-07 09:00:01", "price": 101.5, "volume": 1},
    ]
    normed = [_normalize_tick(r) for r in raw]
    bars = _dedupe_same_second(normed)
    ts = [b["t"] for b in bars]
    assert ts == sorted(ts), "時間戳必須嚴格遞增，否則_feed_new_bars既有的去重邏輯會誤判"
    assert len(set(ts)) == len(ts), "同一秒內的tick消歧義後不該有重複時間戳"
    assert [b["c"] for b in bars] == [100.0, 100.5, 101.0, 101.5], "價格順序不該被打亂"
