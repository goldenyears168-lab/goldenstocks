"""連次/連量 即時接線層（只讀、不下單、不進分數；2026-10-18 起連量改官方口徑＝單 5 秒窗量）：把 biglot.streak.StreakTracker 掛進儀表板 ingest。

- `TRACKER`：模組單例；換日時 `reset_for_day()` 清空並重設 PIT 分均量。
- 分均量 ref = 前 ≤10 個交易日 DB 日量(張)均值 × 0.89 ÷ 265。0.89 = 連續盤逐筆累計量 ÷ DB 日量的實測中位數
  （9/3～10/16、1,232 股票日；DB 日量含收盤競價）。研究面板用的是連續盤逐筆量本身，所以兩者門檻約差 ±10%。
- 任何例外都吞掉並計數（`ERR`），不得影響逐筆 ingest 與主頁渲染。
- `snapshot()` 的時間用「已讀入的最新成交時間」(水位 `WM`)，不用牆上時鐘（ingest 讀檔有落後）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from biglot.streak import StreakTracker, is_exhaustion, RULES_VERSION
from stock_db import DEFAULT_DB_PATH

CONT_VOL_RATIO = 0.89
SESSION_MIN = 265.0
TRACKER = StreakTracker()
WM = {"t": 0.0}                       # 水位：已餵入的最新成交 time-of-day 秒
ERR = {"n": 0, "last": None, "ref_n": 0}
LOWVOL: set = set()                   # 近 10 日日均量 <3,000 張（作者適用範圍外：不標竭盡、連次欄灰化）
LOWVOL_LOTS = 3000.0
THR_DIR = Path(DEFAULT_DB_PATH).parent / "biglot_streak"
THR_INFO = {"state": "none", "dates": None, "n": 0}
_RAW_DIR = Path(DEFAULT_DB_PATH).parent.parent / "cache" / "biglot_live_watch"


EXH_LEDGER = Path(DEFAULT_DB_PATH).parent / "biglot_forward" / "exhaustion_events.jsonl"
_EXH_SEEN: set = set()


def _on_flip(sid: str, ev: dict) -> None:
    """竭盡點前向帳本（只記即時事件；啟動時重放舊 raw 的事件距牆上時間 >90 秒不寫，避免重複）。條件＝is_exhaustion ∧ 非低量標的。例外自吞。"""
    try:
        if sid in LOWVOL or not is_exhaustion(ev):
            return
        n = datetime.now()
        if (n.hour * 3600 + n.minute * 60 + n.second) - ev["t"] > 90:
            return
        day = n.strftime("%Y-%m-%d")
        key = (day, sid, round(ev["t"]))
        if key in _EXH_SEEN:
            return
        _EXH_SEEN.add(key)
        EXH_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        rec = {"date": day, "sid": sid, "t": round(ev["t"], 1), "d": ev["d"], "n": ev["n"], "nb5": ev["nb5"], "avg": round(ev["avg"], 1), "rvol30": round(ev["rvol30"], 2) if ev.get("rvol30") is not None else None, "rule": RULES_VERSION,
               "px": ev["px"], "brk_lots": ev.get("brk_lots"), "day_up": ev.get("day_up"),
               "aligned": (ev["d"] > 0) != bool(ev.get("day_up")),     # 順大勢＝新倉方向；否則是平倉／回補提示
               "thr_src": "p90" if TRACKER._s.get(sid) and TRACKER._s[sid].thr else "x5", "source": "live", "logged_at": time.time()}
        with open(EXH_LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        ERR["n"] += 1; ERR["last"] = repr(e)


TRACKER.add_listener(_on_flip)


def _load_thresholds(sids) -> None:
    """載入 thresholds.json（每檔×30 分鐘時段單窗主導量 P90）。缺檔/壞檔 → 退回均量×5，狀態寫進 THR_INFO。"""
    try:
        d = json.loads((THR_DIR / "thresholds.json").read_text(encoding="utf-8"))
        n = 0
        for sid in sids:
            t = d["thr"].get(sid)
            if t:
                TRACKER.set_thresholds(sid, {int(k): v for k, v in t.items()})
                n += 1
        THR_INFO.update({"state": "p90", "dates": d.get("dates"), "n": n})
    except Exception as e:  # noqa: BLE001
        THR_INFO.update({"state": "fallback_x5", "n": 0})
        ERR["n"] += 1; ERR["last"] = repr(e)


def _refresh_thresholds_async(sids, today: str) -> None:
    """thresholds.json 落後最新 raw 日時，背景（nice）重算；完成後套用到之後的窗。盤中重啟的重放窗仍用當下載入的門檻。"""
    def run():
        try:
            root = Path(__file__).resolve().parents[3]
            env = dict(os.environ, PYTHONPATH=f"{root}/src:{root}/scripts/research")
            subprocess.run(["nice", "-n", "15", sys.executable, str(root / "scripts/research/biglot_streak_thresholds.py"), today],
                           env=env, check=True, timeout=1800)
            _load_thresholds(sids)
        except Exception as e:  # noqa: BLE001
            ERR["n"] += 1; ERR["last"] = repr(e)
    threading.Thread(target=run, daemon=True, name="streak-thr-refresh").start()


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
        LOWVOL.clear()
        LOWVOL.update(sid for sid, v in per.items() if len(v) >= 3 and sum(v) / len(v) < LOWVOL_LOTS)
    except Exception as e:  # noqa: BLE001
        ERR["n"] += 1; ERR["last"] = repr(e)
    _load_thresholds(sids)
    try:                                                      # 門檻檔是否落後最新 raw 日（昨天以前）
        last_raw = max((p.stem.replace("raw_", "") for p in _RAW_DIR.glob("raw_2026-*.jsonl") if p.stem.replace("raw_", "") < today), default=None)
        if last_raw and (not THR_INFO["dates"] or max(THR_INFO["dates"]) < last_raw):
            _refresh_thresholds_async(list(sids), today)
    except Exception as e:  # noqa: BLE001
        ERR["n"] += 1; ERR["last"] = repr(e)


def on_trade(sid: str, t: datetime, px: float, dv: float, bid, ask) -> None:
    try:
        ts = t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6
        TRACKER.on_trade(sid, ts, px, dv, bid, ask)
        if ts > WM["t"]:
            WM["t"] = ts
        st = TRACKER._s.get(sid)
        if st is not None and st.d > 0 and st.n >= 10:                 # P1 前向帳本：只在外攻連次≥10 時才做完整 snapshot
            from biglot import xiaoge_cols
            xiaoge_cols.maybe_p1(sid, TRACKER.snapshot(sid, WM["t"]), ts)
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
           "每5秒窗外盤量>內盤量=紅(+1)、內>外=綠(−1)；連次＝連續同向窗數，達±10亮燈(🔔，官方)；"
           "連量＝『最近一個5秒窗』主導方向成交張數（官方），『窗×N』＝該窗÷該股每5秒均量；『累計』＝整段連攻累計張數；連量單窗/累計尚未定案，兩者並列。\n"
           "RVOL＝最近6個5秒窗(30秒)成交量÷該股過去10日平均每30秒成交量。\n"
           "竭盡v2（2026-10-25 研究定案參數，非小哥數字）＝轉色∧連次≥4∧RVOL≥2×：紅轉綠＝買盤竭盡、綠轉紅＝賣盤竭盡。\n"
           "⚠純描述：33日網格含零毛 +5.1／+6.2／+6.6 bps（1／3／5分），進場價差約20bps+ → 不構成下單訊號，不進分數。")
    if not s or not s["d"]:
        return f"<td class='dim' data-sort='0' title='{tip}'>—</td>"
    n, d = s["n"], s["d"]
    cls = "up" if d > 0 else "dn"
    bell = "🔔" if s["lit"] else ""
    sub = f"<span class='dim' style='font-size:9px'> 窗×{s['win_x']:.0f}</span>" if s["win_x"] is not None else ""
    if s.get("rvol30_now") is not None:
        sub += f"<span class='dim' style='font-size:9px'> RVOL {s['rvol30_now']:.1f}×</span>"
    extra = ""
    if s["nb5"]:
        extra += f"<span class='dim' style='font-size:9px'> 爆窗×{s['nb5']}</span>"
    if s.get("cum_lots"):
        extra += f"<span class='dim' style='font-size:9px'> 累計{s['cum_lots']:,.0f}</span>"
    st = f"<div style='font-size:9px'>{s['status']}</div>" if s["status"] else ""
    f_ = s.get("flip")
    risk = (f"<div style='font-size:9px;color:#d29922' title='竭盡v2＝轉色∧連次≥4∧RVOL≥2×；描述性時機提示，非下單訊號（毛利小於價差）'>"
            f"{'買盤竭盡' if f_['d'] > 0 else '賣盤竭盡'}<span class='dim'>({f_['n']}連·RVOL {f_['rvol30']:.1f}×)</span></div>"
            if (f_ and f_.get("exh") and f_.get("rvol30") is not None) else "")
    return (f"<td class='{cls}' data-sort='{abs(n) * d}' title='{tip}'>{bell}{abs(n)}/{s['win_lots']:,.0f}{sub}{extra}{st}{risk}</td>")
