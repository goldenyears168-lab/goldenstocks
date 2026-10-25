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

RULES_VERSION = 3        # 解析/分組規則一改就加 1;落地的檢查點(checkpoint)帶這個版本,不一致就丟掉重建
#                          v2(2026-10-19):指數期貨改『每品牌各自的分組』+ 1 口連發(延遲確認),見 group_for / StreakTagger
#                          v3(2026-10-19):大台加『連次/連量』(5 秒窗,小哥口徑),見 WindowStreak

PV = {"TXF": 200, "MXF": 50, "TMF": 10, "CDF": 2000, "QFF": 100}    # 每點新台幣(個股期貨=每口股數:台積電標準 2000、小型 100)
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


# 台積電期貨(CDF 標準 2000 股、QFF 小型 100 股)不能沿用「1 口=散戶」:1 口 QFF 約 24 萬、1 口 CDF 約 485 萬,
# 與 1 口小台(約 236 萬)根本不同級。這些品牌改用純名目分級:< RETAIL_AMT=散戶、≥ BIG_AMT=大單、其餘中單。
NOTIONAL_ROOTS = ("CDF", "QFF")
RETAIL_AMT = 500_000

# ---- 2026-10-19 起:指數期貨每個品牌用自己的分組(使用者要求『大戶/散戶兩條線不要太像』) ----
# 為什麼:只按單子大小分,三檔的主動買賣淨額都跟價格同向(各檔與同期價格相關 0.5~0.7),換門檻救不了;
# 小台 0.72→0.65、微台『大單≥1000萬』只占 9% 成交額且只有 54% 的 5 分鐘有單(近乎空線)。
# 改用『下單結構』:1 口連發(同方向同口數連續出現)對價格不敏感(相關 0.19~0.28),與孤立單口的 5 分鐘淨額相關
# 小台 0.63→0.26、大台 0.44→0.36(後半段約 18 個日盤驗證;證據與限制見記憶 futflow-tier-redundancy-20261019)。
# 槽位:B 第一線(紅)、C 連發(青)、M 其餘(灰)、R 第二線(藍)。各品牌只顯示自己有定義的槽位。
SLOTS = ("B", "C", "M", "R")
TXF_BIG_AMT = 40_000_000            # 大台『大單』門檻:單筆爆發名目 ≥ 4000 萬(前半段挑、後半段驗證)
STREAK_GAP = {True: 5.0, False: 1.0}    # True=口數≥2:相鄰同量爆發間隔 ≤5 秒;False=1 口:≤1 秒
STREAK_NEED = {True: 4, False: 5}       # 連續達幾筆才算『連發』(之前的幾筆在確認後回頭歸類)
_SPEC = {
    "MXF": (("B", "M", "R"), {"B": "連續單口", "M": "中單", "R": "零星單口"}),
    "TMF": (("B", "M", "R"), {"B": "連續單口", "M": "中單", "R": "零星單口"}),
    "TXF": (("B", "C", "M", "R"), {"B": "大單≥4000萬", "C": "連續單口", "M": "中單", "R": "零星單口"}),
    "CDF": (("B", "M", "R"), {"B": "大單", "M": "中單", "R": "散戶"}),
    "QFF": (("B", "M", "R"), {"B": "大單", "M": "中單", "R": "散戶"}),
}
SLOT_COLOR = {"B": "#ff7b72", "C": "#39c5cf", "M": "#8b949e", "R": "#58a6ff"}
STREAK_ROOTS = ("MXF", "TMF", "TXF")        # 需要連發標記(延遲確認)的品牌


def root_slots(root: str) -> tuple:
    """該品牌要顯示的槽位(依顯示順序)。"""
    return _SPEC.get(root, _SPEC["MXF"])[0]


def slot_name(root: str, g: str) -> str:
    return _SPEC.get(root, _SPEC["MXF"])[1].get(g, g)


def slot_color(root: str, g: str) -> str:
    # 指數期貨 MXF/TMF 的 B 槽是『連發』,用連發的青色,避免與大台的紅色『大單』混淆
    if g == "B" and root in ("MXF", "TMF"):
        return SLOT_COLOR["C"]
    return SLOT_COLOR[g]


def group_for(root: str, lots: int, notional: float, streak: bool = False) -> str:
    """依品牌選分組規則。

    TSMC 期貨(CDF/QFF)= 純名目;TXF = 大單≥4000 萬 > 1 口連發 > 孤立單口 > 其餘;
    MXF/TMF = 1 口連發(B)> 孤立單口(R)> 其餘(M)。`streak` 由 StreakTagger 延遲確認後提供;預設 False。
    """
    if root in NOTIONAL_ROOTS:
        return "R" if notional < RETAIL_AMT else ("B" if notional >= BIG_AMT else "M")
    if root == "TXF":
        if notional >= TXF_BIG_AMT:
            return "B"
        if streak:
            return "C"
        return "R" if lots == 1 else "M"
    if root in ("MXF", "TMF"):
        if streak:
            return "B"
        return "R" if lots == 1 else "M"
    return group_of(lots, notional)


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


class StreakTagger:
    """把爆發單串成『連發』並延遲確認(2026-10-19)。

    連發＝同 (盤別流, 方向)、同口數、相鄰兩筆爆發時間差 ≤ STREAK_GAP 的連續序列;1 口需連續 ≥5 筆(間隔 ≤1 秒),
    ≥2 口需連續 ≥4 筆(間隔 ≤5 秒)。達標時,串內之前尚未歸類的幾筆『回頭』一併算連發,所以每筆爆發最晚在
    串結束(或超時)後才確定類別——實測一串從第 1 筆到第 4 筆中位數不到 2 秒、P99 約 9 秒,延遲確認版與
    『事後看完整串』的標記一致率 99.96%(W=3 秒)、100%(W=20 秒)。
    ⚠ 若改成『第 N 筆起才算』的位置式即時標記,低相關優勢幾乎消失(小台 5 分鐘淨額相關 0.26→0.52),原因是
    事後標記偷看了未來;因此必須延遲確認,而不能立即歸類。

    用法:`tagger.add(burst)` 餵爆發單(依各 (qt, 方向) 的時間序);歸類完成時呼叫 `on_tagged(burst, is_streak)`。
    `flush_old(last_tt)` 以各盤別流最新 trade_time(µs)結算超時未續的串;`flush_all()` 全部結算。
    """

    def __init__(self, on_tagged: Callable[["Burst", bool], None]):
        self.on_tagged = on_tagged
        self.runs: dict = {}          # (qt, side) → [lots, last_t(秒), n, confirmed, [待歸類的爆發]]

    def add(self, b: "Burst") -> None:
        if b.trade_time is None:
            self.on_tagged(b, False)
            return
        t = b.trade_time / 1e6
        key = (b.qt, b.side)
        multi = b.lots >= 2
        r = self.runs.get(key)
        if r is not None and r[0] == b.lots and 0 <= t - r[1] <= STREAK_GAP[multi]:
            r[1] = t
            r[2] += 1
            if r[3]:
                self.on_tagged(b, True)
                return
            r[4].append(b)
            if r[2] >= STREAK_NEED[multi]:
                r[3] = True
                for m in r[4]:
                    self.on_tagged(m, True)
                r[4] = []
            return
        self._close(key)
        self.runs[key] = [b.lots, t, 1, False, [b]]

    def _close(self, key) -> None:
        r = self.runs.pop(key, None)
        if r and not r[3]:
            for m in r[4]:
                self.on_tagged(m, False)

    def flush_old(self, last_tt: dict) -> None:
        for key in list(self.runs):
            lt = last_tt.get(key[0])
            r = self.runs[key]
            if lt is not None and lt / 1e6 - r[1] > STREAK_GAP[r[0] >= 2]:
                self._close(key)

    def flush_all(self) -> None:
        for key in list(self.runs):
            self._close(key)

    # ---- 檢查點:只存尚未確定的串(已確認的串只需續接資訊) ----
    def to_state(self) -> dict:
        return {f"{k[0]}|{k[1]}": [r[0], r[1], r[2], r[3], [list(m) for m in r[4]]] for k, r in self.runs.items()}

    def load_state(self, st: dict) -> None:
        for k, r in (st or {}).items():
            qt, side = k.split("|")
            self.runs[(qt, int(side))] = [r[0], float(r[1]), int(r[2]), bool(r[3]), [Burst(*m) for m in r[4]]]


# ======================================================================================================
# 連次／連量(小哥口徑,5 秒窗)—— 2026-10-19,只對大台使用
# 定義(docs/exhaustion-streak-spec.md,官方說明欄):每 5 秒一個窗,外盤(主動買)量 > 內盤(主動賣)量 → +1(紅);
# 內 > 外 → −1(綠);平手中斷;無成交的窗跳過(不計、不中斷)。連次 = 連續同向窗數,|連次| ≥ 10 亮燈;
# 連量 = 最近一個已結算窗的主導方向張數。轉色 = 連攻段(≥10)被反向窗打斷,在該窗結算時觸發。
# 為什麼只有大台:2026-10-19 探索(36 個日盤)——小台/微台的 |連次|≥10 每天亮 12~17 次且各視窗都沒有訊號;
# 大台每天 6 次,亮燈後 5/10/15 分鐘平均反向 −2.3/−3.3/−5.0 bps(探索,待預註冊確認,見
# docs/futures-streak-prereg-20261019.md)。這是『描述欄』,不進分數、不是訊號。
# ======================================================================================================
WINDOW_SEC = 5
STREAK_LIGHT = 10            # |連次| ≥ 10 亮燈
LIGHT_COOLDOWN = 300.0       # 同一盤別內,兩次亮燈(或兩次轉色)至少間隔 300 秒才記錄
WINDOW_SETTLE = 3.0          # 窗結束後再等 3 秒才結算:買、賣兩側爆發的輸出時間可差約 2 秒,避免窗未收齊就結算
STREAK_ROOT = "TXF"          # 目前只在大台顯示(見上)

# ── 竭盡 v2(期貨,2026-10-25,jack 指示『連次≥10 ∧ RVOL≥2 倍』;探索性,不屬 futures-streak-prereg-20261019 的凍結檢定,凍結的 WindowStreak 不動)──
# 訊號 = 轉色(連攻段被反向窗打斷,平手不算)∧ 連次≥V2_MIN_N ∧ 近 30 秒 RVOL≥V2_RVOL;RVOL = 最近 V2_WINS 個 5 秒窗(含轉色窗)可判定方向的買賣口數
# ÷ 該商品過去 ≤10 個日盤『每 30 秒平均口數』(日盤 300 分 = 600 個 30 秒;分子分母同口徑:只計可判定方向的主動成交口數)。
# 研究(scripts/research/futures_exhaust_v2_eval.py,37 個大台日盤):連次≥10∧RVOL≥2×,每日 2.6 個,反轉方向含零毛 3/5/15 分 +1.4/+1.6/+3.9 bps(t 1.1~1.4,未達顯著)。
# 同樣參數(連次≥4)在期貨上沒有反轉優勢(−0.4~−1.2 bps),故門檻採 10。僅日盤。
V2_MIN_N = 10
V2_RVOL = 2.0
V2_WINS = 6
V2_SESSION_30S = 600.0       # 日盤 08:45~13:45 共 300 分鐘 = 600 個 30 秒


class WindowStreak:
    """5 秒窗連次狀態機。`add(burst)` 把爆發單累進所屬窗;`flush(last_tt)` 結算已確定收齊的窗並更新連次。

    與頁面、預註冊評估腳本共用同一份邏輯(批次評估把所有爆發單餵完後 `flush_all()`,結果與即時一致)。
    狀態以盤別 (stype, T) 分開;日盤用 FUTURE 流的時間、夜盤用 FUTURE_AH 流的時間判斷窗是否已收齊。
    """

    QT = {"day": "FUTURE", "night": "FUTURE_AH"}

    def __init__(self):
        self.win: dict = {}       # (stype, T) → {窗編號: [買張, 賣張]}  尚未結算
        self.st: dict = {}        # (stype, T) → 連次狀態(見 _new_state)

    @staticmethod
    def _new_state() -> dict:
        return {"n": 0, "d": 0, "last_dom": 0.0, "seg": 0.0, "last_end": None,
                "lit_t": -1e18, "turn_t": -1e18, "lits": [], "turns": []}   # lits/turns:[結算時刻(epoch 秒), 方向, 連次]

    def add(self, b: "Burst") -> None:
        if b.trade_time is None or b.side == 0:
            return
        wid = int((b.trade_time / 1e6) // WINDOW_SEC)
        w = self.win.setdefault((b.stype, b.T), {}).setdefault(wid, [0, 0])
        w[0 if b.side > 0 else 1] += b.lots

    def _settle(self, key, wid: int, w: list) -> None:
        st = self.st.setdefault(key, self._new_state())
        end = (wid + 1) * WINDOW_SEC
        buy, sell = w
        dom = float(max(buy, sell))
        d = (buy > sell) - (buy < sell)
        st["last_dom"], st["last_end"] = dom, end
        if d == 0:                                       # 平手:中斷
            if st["n"] >= STREAK_LIGHT and end - st["turn_t"] >= LIGHT_COOLDOWN:
                st["turns"].append([end, 0, st["n"]])
                st["turn_t"] = end
            st["n"], st["d"], st["seg"] = 0, 0, 0.0
            return
        if d == st["d"]:
            st["n"] += 1
            st["seg"] += dom
            if st["n"] == STREAK_LIGHT and end - st["lit_t"] >= LIGHT_COOLDOWN:
                st["lits"].append([end, d, st["n"]])
                st["lit_t"] = end
            return
        if st["n"] >= STREAK_LIGHT and end - st["turn_t"] >= LIGHT_COOLDOWN:
            st["turns"].append([end, d, st["n"]])        # 方向=新顏色(連攻段被打斷後的第一個反向窗)
            st["turn_t"] = end
        st["n"], st["d"], st["seg"] = 1, d, dom

    def flush(self, last_tt: dict) -> None:
        for key in list(self.win):
            now = last_tt.get(self.QT.get(key[0], "FUTURE"))
            if now is None:
                continue
            now /= 1e6
            ws = self.win[key]
            for wid in sorted(w for w in ws if (w + 1) * WINDOW_SEC + WINDOW_SETTLE <= now):
                self._settle(key, wid, ws.pop(wid))

    def flush_all(self) -> None:
        for key in list(self.win):
            ws = self.win[key]
            for wid in sorted(ws):
                self._settle(key, wid, ws.pop(wid))

    def snapshot(self) -> dict:
        """給頁面讀的複本:{(stype,T): 狀態}。"""
        return {k: {**v, "lits": [list(x) for x in v["lits"]], "turns": [list(x) for x in v["turns"]]} for k, v in self.st.items()}

    def to_state(self, keep: int = 24) -> dict:
        ks = sorted(set(self.st) | set(self.win), key=lambda k: (k[1], 0 if k[0] == "day" else 1))[-keep:]
        return {"st": {f"{k[0]}|{k[1]}": self.st[k] for k in ks if k in self.st},
                "win": {f"{k[0]}|{k[1]}": {str(w): v for w, v in self.win[k].items()} for k in ks if self.win.get(k)}}

    def load_state(self, state: dict) -> None:
        for k, v in ((state or {}).get("st") or {}).items():
            stype, T = k.split("|")
            self.st[(stype, T)] = {**self._new_state(), **v}
        for k, v in ((state or {}).get("win") or {}).items():
            stype, T = k.split("|")
            self.win[(stype, T)] = {int(w): list(x) for w, x in v.items()}


class WindowStreakV2(WindowStreak):
    """期貨竭盡 v2:在凍結的 WindowStreak 之外另跑一份(不改它的任何行為)。每個轉色(連次≥V2_MIN_N 的連攻段被反向窗打斷)記一筆
    [窗末 epoch 秒, 新顏色方向, 被打斷的連次, 近 30 秒 RVOL 或 None];`sigs()` 只回 RVOL≥V2_RVOL 者。無冷卻(與研究一致)。
    `ref30_for(T)` 回傳日盤 T 的『每 30 秒平均口數』(PIT:T 之前 ≤10 個日盤),無資料回 None → RVOL=None、不判訊號。只對日盤判定。"""

    def __init__(self, ref30_for=None):
        super().__init__()
        self.ref30_for = ref30_for
        self.recent: dict = {}     # (stype,T) → [(窗編號, 買+賣口數)] 最近 12 窗
        self.turns2: dict = {}     # (stype,T) → [[窗末, 新顏色, 連次, rvol]]
        self.day_tot: dict = {}    # T → 日盤可判定口數合計(算次日起的基準)

    def _settle(self, key, wid, w):
        st0 = self.st.get(key)
        d_new = (w[0] > w[1]) - (w[0] < w[1])
        prev_d, prev_n = (st0["d"], st0["n"]) if st0 else (0, 0)
        tot = float(w[0] + w[1])
        rec = self.recent.setdefault(key, [])
        rec.append((wid, tot))
        if len(rec) > 12:
            del rec[:-12]
        if key[0] == "day":
            self.day_tot[key[1]] = self.day_tot.get(key[1], 0.0) + tot
        if key[0] == "day" and d_new != 0 and prev_d != 0 and d_new == -prev_d and prev_n >= V2_MIN_N:
            ref = self.ref30_for(key[1]) if self.ref30_for else None
            v30 = sum(v for w_, v in rec if w_ > wid - V2_WINS)
            self.turns2.setdefault(key, []).append([(wid + 1) * WINDOW_SEC, d_new, prev_n, (v30 / ref) if ref else None])
        super()._settle(key, wid, w)

    def sigs(self, key) -> list:
        return [x for x in self.turns2.get(key, []) if x[3] is not None and x[3] >= V2_RVOL]

    def snapshot2(self) -> dict:
        return {k: {"turns": [list(x) for x in v], "sigs": [list(x) for x in v if x[3] is not None and x[3] >= V2_RVOL]} for k, v in self.turns2.items()}

    def to_state(self, keep: int = 24) -> dict:
        base = super().to_state(keep)
        ks = sorted(set(self.turns2) | set(self.recent), key=lambda k: (k[1], 0 if k[0] == "day" else 1))[-keep:]
        base["v2"] = {"turns2": {f"{k[0]}|{k[1]}": self.turns2[k] for k in ks if k in self.turns2},
                      "recent": {f"{k[0]}|{k[1]}": [list(x) for x in self.recent[k]] for k in ks if k in self.recent},
                      "day_tot": dict(sorted(self.day_tot.items())[-keep:])}
        return base

    def load_state(self, state: dict) -> None:
        super().load_state(state)
        v2 = (state or {}).get("v2") or {}
        for k, v in (v2.get("turns2") or {}).items():
            stype, T = k.split("|"); self.turns2[(stype, T)] = [list(x) for x in v]
        for k, v in (v2.get("recent") or {}).items():
            stype, T = k.split("|"); self.recent[(stype, T)] = [(int(a), float(b)) for a, b in v]
        self.day_tot.update({k: float(v) for k, v in (v2.get("day_tot") or {}).items()})


def ref30_from_totals(totals: dict, T: str, days: int = 10, min_days: int = 3):
    """日盤 T 的『每 30 秒平均口數』:T 之前 ≤days 個日盤合計的平均 ÷ 600。不足 min_days 回 None。"""
    prev = [v for d, v in sorted(totals.items()) if d < T and v > 0][-days:]
    return (sum(prev) / len(prev) / V2_SESSION_30S) if len(prev) >= min_days else None
