"""連次/連量 即時接線層（只讀、不下單、不進分數；2026-10-18 起連量改官方口徑＝單 5 秒窗量）：把 biglot.streak.StreakTracker 掛進儀表板 ingest。

- `TRACKER`：模組單例；換日時 `reset_for_day()` 清空並重設 PIT 分均量。
- 分均量 ref = 前 ≤10 個交易日 DB 日量(張)均值 × 0.89 ÷ 265。0.89 = 連續盤逐筆累計量 ÷ DB 日量的實測中位數
  （9/3～10/16、1,232 股票日；DB 日量含收盤競價）。研究面板用的是連續盤逐筆量本身，所以兩者門檻約差 ±10%。
- 任何例外都吞掉並計數（`ERR`），不得影響逐筆 ingest 與主頁渲染。
- `snapshot()` 的時間用「已讀入的最新成交時間」(水位 `WM`)，不用牆上時鐘（ingest 讀檔有落後）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from biglot.streak import StreakTracker
from stock_db import DEFAULT_DB_PATH

CONT_VOL_RATIO = 0.89
SESSION_MIN = 265.0
TRACKER = StreakTracker()
WM = {"t": 0.0}                       # 水位：已餵入的最新成交 time-of-day 秒
ERR = {"n": 0, "last": None, "ref_n": 0}


def reset_for_day(sids, today: str) -> None:
    TRACKER.reset()
    WM["t"] = 0.0
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        ph = ",".join("?" * len(sids))
        rows = conn.execute(
            f"SELECT stock_id, trade_date, MAX(volume) FROM stock_daily_bars "
            f"WHERE stock_id IN ({ph}) AND trade_date<? AND trade_date>=date(?,'-30 day') AND volume>0 "
            f"GROUP BY stock_id, trade_date ORDER BY stock_id, trade_date DESC", (*sids, today, today)).fetchall()
        conn.close()
        per: dict[str, list[float]] = {}
        for sid, _d, vol in rows:
            if len(per.setdefault(sid, [])) < 10:
                per[sid].append(vol / 1000.0)           # 股 → 張
        n = 0
        for sid, v in per.items():
            if len(v) >= 3:
                TRACKER.set_ref(sid, sum(v) / len(v) * CONT_VOL_RATIO / SESSION_MIN)
                n += 1
        ERR["ref_n"] = n
    except Exception as e:  # noqa: BLE001
        ERR["n"] += 1; ERR["last"] = repr(e)


def on_trade(sid: str, t: datetime, px: float, dv: float, bid, ask) -> None:
    try:
        ts = t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6
        TRACKER.on_trade(sid, ts, px, dv, bid, ask)
        if ts > WM["t"]:
            WM["t"] = ts
    except Exception as e:  # noqa: BLE001
        ERR["n"] += 1; ERR["last"] = repr(e)


def snapshot(sid: str):
    try:
        return TRACKER.snapshot(sid, WM["t"] or None)
    except Exception as e:  # noqa: BLE001
        ERR["n"] += 1; ERR["last"] = repr(e)
        return None


def cell_html(sid: str) -> str:
    """欄位 <td>（官方口徑）：主文字 連次/連量（連量＝最近一窗主導方向張數）；🔔＝連次達 ±10 亮燈；
    副字 窗×N（該窗÷每5秒均量）、爆窗×k；狀態字；紅=外盤連攻、綠=內盤連攻（台股慣例）。"""
    s = snapshot(sid)
    tip = ("連次/連量（反向工程「盤中當沖神器」；定義依說明欄 2026-10-18 更正，規格 docs/exhaustion-streak-spec.md）：\n"
           "每5秒窗外盤量>內盤量=紅(+1)、內>外=綠(−1)；連次＝連續同向窗數，達±10亮燈(🔔)；"
           "連量＝『最近一個5秒窗』主導方向成交張數（官方），『窗×N』＝該窗÷該股每5秒均量；"
           "爆窗×k＝本段內單窗≥5×均量的窗數（門檻本repo自訂）；『累計』＝整段連攻累計張數。連量單窗/累計尚未定案，兩者並列。\n"
           "轉折＝連攻段轉色（紅→綠＝買盤竭盡）。「拉升力竭風險」＝剛中斷∧連次≥10∧爆窗≥3。\n"
           "⚠純描述：歷史上竭盡後放空含價差(~25bps)淨值為負（官方定義版：連次≥10∧爆窗≥3 共515筆，5分鐘毛+8.5/淨−20.6bps，t−4.5），"
           "不構成放空訊號，不進分數。")
    if not s or not s["d"]:
        return f"<td class='dim' data-sort='0' title='{tip}'>—</td>"
    n, d = s["n"], s["d"]
    cls = "up" if d > 0 else "dn"
    bell = "🔔" if s["lit"] else ""
    sub = f"<span class='dim' style='font-size:9px'> 窗×{s['win_x']:.0f}</span>" if s["win_x"] is not None else ""
    extra = ""
    if s["nb5"]:
        extra += f"<span class='dim' style='font-size:9px'> 爆窗×{s['nb5']}</span>"
    if s.get("cum_lots"):
        extra += f"<span class='dim' style='font-size:9px'> 累計{s['cum_lots']:,.0f}</span>"
    st = f"<div style='font-size:9px'>{s['status']}</div>" if s["status"] else ""
    risk = ("<div style='font-size:9px;color:#d29922' title='剛中斷∧連次≥10∧爆窗≥3：描述性情境標籤，非放空訊號（歷史5分鐘淨−20.6bps）'>拉升力竭風險</div>"
            if s["risk"] else "")
    return (f"<td class='{cls}' data-sort='{abs(n) * d}' title='{tip}'>{bell}{abs(n)}/{s['win_lots']:,.0f}{sub}{extra}{st}{risk}</td>")
