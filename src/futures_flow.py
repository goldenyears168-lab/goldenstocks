"""指數期貨(MXF 小型台指、TMF 微型台指)逐筆成交 → 事件 的共用規則(純函式與小型狀態機,不做任何檔案或網路 I/O)。

儀表板 `/futflow`、分鐘特徵表腳本、(之後的)研究腳本都應該呼叫這裡,而不是各自再寫一份解析與分級。
規則來源:記憶 fubon-ws-trades-dual-stream-pitfalls、tmf-bigflow-size-bucket-backtest-20261008。

規則:
- 日盤 FUTURE 與夜盤 FUTURE_AH 是兩條獨立流,累計量各自計(狀態一律以 quote_type 為鍵)。
- 量一律用 Δcum_volume,不用 size 欄(富邦 ws 的 size 在期貨會低估)。
- stale 列丟棄;(quote_type, serial, cum_volume) 去重;日盤只取 08:45~13:45(濾掉殭屍重送);夜盤不取 05:00~15:00。
- 累計量往回跳(cum < 上一筆)或第一筆 = 基準列(baseline):只當基準、不計量。
- 夜盤跨日曆檔:同一個夜盤 T 的 15:00~24:00 在 T 檔、00:00~05:00 在 T+1 檔;凌晨列歸前一日曆日的夜盤。
- 買賣方向:成交價 ≥ 賣一 = 主動買(+1)、≤ 買一 = 主動賣(−1)、其餘 0(不計方向)。
- 「單」＝同毫秒(trade_time)、同方向的主動成交爆發量,不是母單、不能辨認身分。
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Callable, NamedTuple, Optional

RULES_VERSION = 1        # 解析/分組規則一改就加 1;落地的檢查點(checkpoint)帶這個版本,不一致就丟掉重建

PV = {"TXF": 200, "MXF": 50, "TMF": 10}    # 每點新台幣
DAY0, DAY1 = 8 * 3600 + 45 * 60, 13 * 3600 + 45 * 60
NIGHT_GAP = (5 * 3600, 15 * 3600)           # 夜盤在這段時間沒有成交
BIG_AMT = 10_000_000                        # 大戶門檻:名目 ≥ 1000 萬(與主頁期貨欄同口徑)
TIERS = ("S", "M1", "M2", "L", "XL")        # 研究用五級(百萬元):<2.5 | 2.5~5 | 5~12 | 12~25 | ≥25
TIER_EDGES_M = (0, 2.5, 5, 12, 25)


class Tick(NamedTuple):
    qt: str                 # FUTURE / FUTURE_AH
    stype: str              # day / night
    T: str                  # 盤別日期(夜盤=開始日)
    mk: int                 # 分鐘鍵(夜盤午夜後 +1440,讓同一夜盤單調遞增)
    sec: float              # 當日秒數(日曆日)
    ts: str                 # 原始接收時間字串
    cv: int                 # 累計量
    px: float
    dv: int                 # Δ量(基準列為 0)
    baseline: bool          # 第一筆或累計量往回跳
    reset: bool             # 往回跳(非第一筆)
    side: int               # +1 主動買 / −1 主動賣 / 0 無法判定
    trade_time: Optional[int]
    bid: Optional[float]
    ask: Optional[float]


def group_of(lots: int, notional: float) -> str:
    """儀表板三組:散戶=1 口、大戶=名目≥BIG_AMT、其餘中單。"""
    if lots == 1:
        return "R"
    return "B" if notional >= BIG_AMT else "M"


def tier_of(notional_m: float) -> str:
    """研究用五級,輸入單位為百萬元。"""
    t = 0
    for i, e in enumerate(TIER_EDGES_M):
        if notional_m >= e:
            t = i
    return TIERS[t]


def session_of(qt: str, file_date: str, sec: float):
    """(盤別, T)。夜盤凌晨(<12:00)屬前一日曆日的夜盤。"""
    if qt == "FUTURE":
        return "day", file_date
    if sec >= 12 * 3600:
        return "night", file_date
    return "night", (dt.date.fromisoformat(file_date) - dt.timedelta(days=1)).isoformat()


def minute_key(stype: str, sec: float) -> int:
    m = int(sec // 60)
    return m if (stype == "day" or m >= 720) else m + 1440


def parse_ts(ts: str) -> float:
    """'2026-10-14T00:00:00.061+08:00' → 當日秒數。"""
    return int(ts[11:13]) * 3600 + int(ts[14:16]) * 60 + int(ts[17:19]) + int(ts[20:23]) / 1000


class TradeStream:
    """把 ws 逐筆成交的原始 JSON 行,正規化成 Tick。累計量狀態跨檔延續;去重集合每個檔各自一份。"""

    def __init__(self):
        self.prev: dict = {}                 # quote_type → 上一筆累計量
        self._seen: dict = {}                # 檔識別 → set

    def seen_for(self, file_key) -> set:
        return self._seen.setdefault(file_key, set())

    def drop_file(self, file_key) -> None:
        self._seen.pop(file_key, None)

    def feed(self, line, file_date: str, file_key=None) -> Optional[Tick]:
        try:
            r = json.loads(line)
        except ValueError:
            return None
        if r.get("stale"):
            return None
        qt, cv, px = r.get("quote_type"), r.get("cum_volume"), r.get("price")
        if qt not in ("FUTURE", "FUTURE_AH") or cv is None or px is None:
            return None
        seen = self.seen_for(file_key if file_key is not None else file_date)
        k = (qt, r.get("serial"), cv)
        if k in seen:
            return None
        seen.add(k)
        ts = r["ts"]
        sec = parse_ts(ts)
        if qt == "FUTURE" and not (DAY0 <= sec < DAY1):
            return None
        if qt == "FUTURE_AH" and NIGHT_GAP[0] <= sec < NIGHT_GAP[1]:
            return None
        stype, T = session_of(qt, file_date, sec)
        p = self.prev.get(qt)
        self.prev[qt] = cv
        baseline = p is None or cv < p
        dv = 0 if baseline else cv - p
        b, a = r.get("bid"), r.get("ask")
        side = 1 if (a is not None and px >= a) else (-1 if (b is not None and px <= b) else 0)
        return Tick(qt, stype, T, minute_key(stype, sec), sec, ts, cv, px, dv, baseline, baseline and p is not None, side,
                    r.get("trade_time"), b, a)


class Burst(NamedTuple):
    qt: str
    side: int
    trade_time: Optional[int]
    stype: str
    T: str
    mk: int                 # 第一筆所在分鐘
    lots: int
    notional_sum: float     # Σ 每筆 dv × 點值 × 該筆成交價
    last_px: float


class BurstMerger:
    """同毫秒(trade_time)、同方向的主動成交合併成「爆發量」。

    streaming=True(即時用):同一 (qt, 方向) 遇到不同 trade_time 就把舊的送出;配合 flush_old 把落後太久的送出。
    streaming=False(批次用):以 (qt, trade_time, 方向) 為鍵累加到 flush_all 才送出(與舊版研究腳本一致)。
    """

    def __init__(self, pv: int, on_burst: Callable[[Burst], None], streaming: bool = True):
        self.pv, self.on_burst, self.streaming = pv, on_burst, streaming
        self.open: dict = {}
        self.last_tt: dict = {}

    def add(self, t: Tick) -> None:
        if t.dv <= 0 or t.side == 0:
            return
        self.last_tt[t.qt] = t.trade_time if t.trade_time is not None else self.last_tt.get(t.qt)
        key = (t.qt, t.side) if self.streaming else (t.qt, t.trade_time, t.side)
        e = self.open.get(key)
        if e is not None and self.streaming and e[0] != t.trade_time:
            self._emit(key)
            e = None
        if e is None:
            self.open[key] = [t.trade_time, t.stype, t.T, t.mk, t.dv, t.dv * self.pv * t.px, t.px]
        else:
            e[4] += t.dv
            e[5] += t.dv * self.pv * t.px
            e[6] = t.px

    def _emit(self, key) -> None:
        e = self.open.pop(key, None)
        if e:
            self.on_burst(Burst(key[0], key[-1], e[0], e[1], e[2], e[3], e[4], e[5], e[6]))

    def flush_old(self, lag_us: int = 2_000_000) -> None:
        for key in list(self.open):
            tt, lt = self.open[key][0], self.last_tt.get(key[0])
            if tt is not None and lt is not None and lt - tt > lag_us:
                self._emit(key)

    def flush_all(self) -> None:
        for key in list(self.open):
            self._emit(key)
