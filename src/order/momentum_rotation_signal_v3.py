"""momentum-rotation · v3 訊號(2026-08-31 盲點修正版).

## 為什麼有 v3

v1 訊號(momentum_rotation_signal.process_new_bar)的盲點清單與量化證據
(2026-08-31 全部實測,見當日對話與 scratch/momentum_v1_paper_trades.csv):

  盲點                                v1 的做法              v3 的修法
  ────────────────────────────────────────────────────────────────────────────
  ① 量「累積漂移」不是「動能」          跟 08:45 開盤價比        跟 30 分鐘前的自己比(滾動窗)
  ② anchor 種在現貨開盤前的真空        08:45 期貨開盤價         訊號窗 09:30 起(現貨 09:00 開
                                                            + 30 分回看窗)
  ③ 買到 beta 不是個股動能            絕對報酬                 減掉台指期同窗報酬(研究文件自己
     (研究文件:效應 2/3 是大盤同步)                          量過 2/3 是大盤;dayflip 線同法)
  ④ 固定 0.5% 門檻不看個股波動        全池同一個 %             z = 相對報酬 / 該股滾動 σ
     (筆數被波動決定:69 vs 26)                              (tx_channel 線的 amp 正規化同法)
  ⑤ 量能門檻無鑑別力                  單筆 ≥1.5×中位(=3口,    **刪除**。假門檻比沒有更糟;流動性
     (中位量 2 口,27% 天然過關)      73% tick 是 2 口)        由標的池門檻(日均≥2000口)把關
  ⑥ score 跨標的不可比、選最晚進場點   overshoot×vol_ratio      z 本身已正規化,跨標的可比;
     (實測:進場位置中位在當日 90%)   (愈晚愈高分)            多檔同時觸發取 |z| 最大
  ⑦ 持有尺度 < 價差尺度               1~10 分鐘持有            **30 分鐘時間出場**(實測移動/成本
     (5分尺度中位比值 0.77 <1)       (價格走不贏價差)         比:5分 0.77 / 30分 2.07)
  ⑧ 不看成本進場                     無                       成本閘門:cost_bps(price) ≤ 26
                                                            (tick級距+手續費176+稅,逐檔算)
  ⑨ 不看價差進場                     無(連 L1 量都不看)       進場前 L1 價差 ≤ 2 個跳動單位
                                                            (下單層 gate,見 order.py)

  方向:**逆勢(fade)** —— 21 天原型:30 分逆勢毛值 +9.58 (日叢集 t=+2.12)、
  順勢 −9.58;與 tick 尺度鏡像 +4.85(前月乾淨資料)同向。

## 誠實限制(不要當成已驗證的 edge)

  · 方向與門檻只有 21 天(2026-08-03~08-28)單一市況的證據,且 30分/60分符號
    相反(30 分內回歸、60 分走回),這是脆弱訊號的長相。
  · 就算 +9.58 全是真的,買得起的標的成本地板 21.6 bps —— **淨值仍為負**。
    v3 的目的是把 dry-run 的訊號流換成「設計上說得通」的版本繼續累積證據,
    不是宣稱可以實彈。
  · σ 用**盤中滾動中位**(當日自身,PIT 安全),與原型的全樣本 σ 不同,
    數字會有落差。

預設由 ORDER_MOMENTUM_ROTATION_SIGNAL_V3 控制(launcher 預設 1;設 0 退回 v1)。
"""

from __future__ import annotations

import os
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_TRUE = frozenset({"1", "true", "yes", "on"})

# --- v3 參數(集中一處,調整不用翻程式) ---
LOOKBACK_MIN = 30          # 相對報酬回看窗(分鐘)
HOLD_MIN = 30              # 時間出場(分鐘)
Z_ENTRY = 2.0              # 進場門檻 |z|
Z_REARM = 1.0              # 重新武裝門檻 |z| <(遲滯,防同一波反覆觸發)
SIGMA_FLOOR_BPS = 40.0     # σ 下限(開盤初期樣本少時防除以極小值)
SIGMA_MIN_OBS = 30         # σ 需要的最少觀測數(≈ 09:30 後半小時才可能出訊號)
SIGMA_WINDOW = 120         # σ 滾動窗(個 1 分鐘觀測)
ENTRY_START = "09:30"      # ② 現貨 09:00 開盤 + 30 分回看窗
ENTRY_END = "12:40"        # 30 分持有 + 緩衝,確保 13:40 強平前自然出場
MAX_COST_BPS = 26.0        # ⑧ 成本閘門
DISASTER_STOP_PCT = 1.5    # 災難停損(v3 唯一價格停損;無移動停利)
MAX_SPREAD_TICKS = 2       # ⑨ 進場時 L1 價差上限(單位:跳動)
INDEX_ROOT = os.environ.get("ORDER_MOMENTUM_ROTATION_V3_INDEX_ROOT", "TMF").strip() or "TMF"

FEE_PER_SIDE_NTD = 176.0   # 2026-08-31 使用者提供實數
TAX_PER_SIDE = 0.00002
LOT_SIZE = 2000

RULE_V3 = "mr3_relz20_lb30_fade_h30_ds15_cost26_spr2t_ew0930-1240"

# --- micro30 模式(2026-08-31 使用者指定:「價跟量都改成30秒的當下峰值,持倉30秒」) ---
#
# 觸發:當下這筆 tick 同時是過去 30 秒的【峰值價】(新高或新低)與【峰值量】,
#       且 30 秒內 burst 幅度 ≥ MICRO_BURST_MIN_BPS;方向 = fade(上衝峰→空、下殺峰→多)。
# 出場:持倉 30 秒平倉(+ 災難停損與 13:40 強平沿用)。
#
# ⚠️ 部署前實測(乾淨前月 21 天,2026-08-31):
#   · 30 秒移動中位 ÷ 成本 = 0.79 —— 跟 5 分尺度(0.77)同樣 < 1,是⑦那個
#     盲點的回歸;30 分尺度才有 2.07。
#   · 這個規格的事件研究:n=10,006(500次/日)、fade 毛值 −1.06 bps、
#     淨值 −23 bps/趟。順勢版毛值 +1.06,同樣遠低於成本。
#   · 30 秒尺度落在 TAIFEX archive 秒內排序污染(p90 28bps)的量級內,
#     這裡的量測本身不可靠;能裁決的資料只有 ws 記錄器(recv_ns),尚在累積。
#   · 自家前例:fade-edge-refuted-by-delay-test —— tick 級 fade 優勢延後 1 秒
#     進場就從 +22.8 翻 −6.8。30 秒峰值 fade 是同一族。
# 保留為 dry-run 訊號流模式之一,依 ORDER_MOMENTUM_ROTATION_V3_MODE 切換。
MICRO_LOOKBACK_SEC = 30
MICRO_HOLD_SEC = 30
MICRO_BURST_MIN_BPS = 30.0
MICRO_COOLDOWN_SEC = 60          # 每檔觸發後冷卻,防同一波連環觸發(500次/日的實測頻率)
MICRO_ENTRY_START = "09:11"      # 回測時窗起點(開盤15分全窗負的舊裁決);13:35 停止進場讓 30 秒持有在強平前結束
MICRO_ENTRY_END = "13:35"


def signal_v3_enabled() -> bool:
    return os.environ.get("ORDER_MOMENTUM_ROTATION_SIGNAL_V3", "1").strip().lower() in _TRUE


def v3_mode() -> str:
    """"micro30"(30秒峰值 fade)或 "relz30"(30分相對大盤 z)。"""
    m = os.environ.get("ORDER_MOMENTUM_ROTATION_V3_MODE", "micro30").strip().lower()
    return m if m in ("micro30", "relz30") else "micro30"


RULE_V3M = "mr3m_peak30s_fade_h30s_burst30_cd60_cost26_spr2t_ew0901-1335"


def tick_size(price: float) -> float:
    """個股期貨最小跳動(跟隨現股級距)。"""
    if price < 10: return 0.01
    if price < 50: return 0.05
    if price < 100: return 0.10
    if price < 500: return 0.50
    if price < 1000: return 1.00
    return 5.00


def cost_bps(price: float) -> float:
    """來回成本下限 = 跨一檔 + 手續費(來回)+ 期交稅(來回),bps。
    這是 2026-08-31 逐檔實算後確認的結構:tick 級距是主體(7.9~41 bps),
    手續費 176/口對低價股是第二殺手(2324@41 元 → 43 bps)。"""
    if price <= 0: return float("inf")
    notional = price * LOT_SIZE
    return (tick_size(price) / price + 2 * FEE_PER_SIDE_NTD / notional + 2 * TAX_PER_SIDE) * 1e4


@dataclass
class V3Signal:
    """欄位刻意與 BreakoutSignal 對齊(sid/direction/fill/score/trigger_time),
    下單層 _enter_position/affordable() 不用改;score = |z|(跨標的可比,修⑥)。
    micro30 模式沿用同一型別:z=burst/30(粗正規化)、mode="micro30"。"""
    sid: str
    direction: str          # fade:相對強→short、相對弱→long
    fill: float
    score: float
    trigger_time: str = ""
    z: float = 0.0
    rel_bps: float = 0.0
    mode: str = "relz30"
    # 2026-08-31 觀測欄位(不影響觸發,dry-run 逐筆記錄供日後驗證):
    # vol_r1 = 爆量前1分鐘量/其前4分鐘(每5秒均量比);vol_r5 = 前5分鐘/其前5分鐘。
    # 量測:r1<0.7(量縮)毛值 +4.51(t=5.06)且 120s 僅衰減 9%;r5>1.4(量增)+1.53。
    vol_r1: float | None = None
    vol_r5: float | None = None
    idx_5m: float | None = None   # 觸發當下台指期 5 分鐘變動(原始 bp,未按方向調整)。
    idx_1m: float | None = None   # 同上但 1 分鐘(2026-09-01 加:回測顯示「5分順∧1分逆」
                                  # 歸零,1分可能是否決權;n=91 不夠格進規則,先記錄)。
    amt: float | None = None      # 事件金額(單筆列印額或5秒窗額,NTD)。V4 用它排
                                  # 終點單;同時是觀測欄位(巨單=滿單/掃穿的解剖)。
    # 量測:5分同向 +4.4~4.8;「1h 也要同向」反效果(+4.79→+3.94),故只記 5 分。
    fut_confirm: bool | None = None  # 期貨 tape ±30s 內是否也爆量(全戰役最強過濾:
    # 雙爆 ret30 +9.85/ret120 +11.34;僅現貨 +3.23;僅期貨 −6.73)。觀測欄位,不影響觸發。


def _mins(hm: str) -> int:
    return int(hm[:2]) * 60 + int(hm[3:5])


_ENTRY_START_M = _mins(ENTRY_START)
_ENTRY_END_M = _mins(ENTRY_END)
_SPOT_OPEN_M = _mins("09:00")


@dataclass
class V3State:
    """單日訊號狀態。**不進 ledger**(bar 序列太大);worker 重啟時由 REST 全日
    逐筆重建(與 v1 重啟行為等價)。跨日由 date 欄位自動重置。"""
    date: str = ""
    closes: dict = field(default_factory=dict)       # sid -> {minute_str: close}
    index_closes: dict = field(default_factory=dict)  # {minute_str: close}
    abs_rel: dict = field(default_factory=dict)      # sid -> deque of |rel_bps|
    armed: dict = field(default_factory=dict)        # sid -> bool
    last_eval: dict = field(default_factory=dict)    # sid -> minute_str 已評估過

    def _roll_date(self, day: str) -> None:
        if day != self.date:
            self.date = day
            self.closes.clear(); self.index_closes.clear()
            self.abs_rel.clear(); self.armed.clear(); self.last_eval.clear()

    @staticmethod
    def _day_hm(bar_time: str) -> tuple[str, str]:
        # bar_time 形如 "2026-08-31T09:31:07.000+08:00" 或 "2026-08-31 09:31:07"
        t = bar_time.replace("T", " ")
        return t[:10], t[11:16]

    def feed_index(self, bars: list[dict[str, Any]]) -> None:
        for b in bars:
            day, hm = self._day_hm(str(b.get("t") or ""))
            if not hm: continue
            self._roll_date(day)
            try: self.index_closes[hm] = float(b["c"])
            except (KeyError, TypeError, ValueError): continue

    def feed_stock(self, sid: str, bars: list[dict[str, Any]]) -> list[V3Signal]:
        """餵入一批逐筆(shape 同 fetch_ticks_as_bars),回傳新產生的訊號。
        每檔每分鐘至多評估一次。"""
        out: list[V3Signal] = []
        book = self.closes.setdefault(sid, {})
        for b in bars:
            day, hm = self._day_hm(str(b.get("t") or ""))
            if not hm: continue
            self._roll_date(day)
            book = self.closes.setdefault(sid, {})   # roll 後重取
            try: px = float(b["c"])
            except (KeyError, TypeError, ValueError): continue
            book[hm] = px
            if self.last_eval.get(sid) == hm:
                continue
            self.last_eval[sid] = hm
            sig = self._evaluate(sid, hm, px)
            if sig: out.append(sig)
        return out

    def _at_or_before(self, series: dict, target_m: int, *, not_before: int) -> float | None:
        best = None; best_m = -1
        for k, v in series.items():
            m = _mins(k)
            if not_before <= m <= target_m and m > best_m:
                best, best_m = v, m
        return best

    def _evaluate(self, sid: str, hm: str, px: float) -> V3Signal | None:
        m = _mins(hm)
        if m < _SPOT_OPEN_M + LOOKBACK_MIN:
            return None                                  # ② 回看窗要完全落在現貨盤內
        book = self.closes.get(sid) or {}
        p0 = self._at_or_before(book, m - LOOKBACK_MIN, not_before=_SPOT_OPEN_M)
        q0 = self._at_or_before(self.index_closes, m - LOOKBACK_MIN, not_before=_SPOT_OPEN_M)
        q1 = self._at_or_before(self.index_closes, m, not_before=_SPOT_OPEN_M)
        if not (p0 and q0 and q1):
            return None
        rel = (px / p0 - 1) * 1e4 - (q1 / q0 - 1) * 1e4   # ③ 相對台指期
        hist = self.abs_rel.setdefault(sid, deque(maxlen=SIGMA_WINDOW))
        try:
            if len(hist) < SIGMA_MIN_OBS:
                return None                              # σ 未暖機
            sigma = max(statistics.median(hist), SIGMA_FLOOR_BPS)   # ④ 波動正規化
            z = rel / sigma
            if sid not in self.armed:
                self.armed[sid] = True
            if not self.armed[sid]:
                if abs(z) < Z_REARM:
                    self.armed[sid] = True               # 遲滯重新武裝
                return None
            if not (_ENTRY_START_M <= m <= _ENTRY_END_M):
                return None
            if abs(z) < Z_ENTRY:
                return None
            if cost_bps(px) > MAX_COST_BPS:
                return None                              # ⑧ 成本閘門
            self.armed[sid] = False
            direction = "short" if rel > 0 else "long"   # fade:噴太多→空、殺太深→多
            return V3Signal(sid=sid, direction=direction, fill=px, score=abs(z),
                            trigger_time=hm, z=z, rel_bps=rel)
        finally:
            hist.append(abs(rel))                        # 評估後才入樣,避免自我污染


_MICRO_ENTRY_START_S = _mins(MICRO_ENTRY_START) * 60
_MICRO_ENTRY_END_S = _mins(MICRO_ENTRY_END) * 60


@dataclass
class Micro30State:
    """micro30 模式的單日狀態(秒級)。跟 V3State 一樣不進 ledger,重啟由 REST 重建。

    2026-08-31 訊號源搬遷:同一個類別跑兩條 tape ——
      現貨實例:unit_shares=1000(張)、entry_end_s 提早到 13:24:59(避開 13:30
                收盤撮合列印——那是未驗證的新事件型態,見盲點檢定)
      期貨實例:unit_shares=2000(口)、僅供 fut_confirm 確認欄位(期貨獨有爆量
                ret120 −6.73 顯著為負,不當觸發)
    金額門檻 = vol × price × unit_shares ≥ VOLBURST_MIN_NTD ——
    **unit_shares 弄錯單位,門檻會靜默偏 2 倍**(已知地雷,測試釘住)。"""
    date: str = ""
    win: dict = field(default_factory=dict)       # sid -> deque[(sec, price, vol)]
    cooldown: dict = field(default_factory=dict)  # sid -> 冷卻到幾秒
    idx: deque = field(default_factory=deque)     # (sec, price) 台指基準,保留 ~310 秒
    unit_shares: float = 2000.0                   # 每單位股數:期貨口=2000、現貨張=1000
    entry_end_s: int = 0                          # 0 = 用全域 _MICRO_ENTRY_END_S
    last_trigger: dict = field(default_factory=dict)  # sid -> 最近一次觸發秒(fut_confirm 用)
    last_dir_trigger: dict = field(default_factory=dict)  # (sid,direction) -> 秒(V4 單發用)

    def feed_index(self, bars: list) -> None:
        """觀測用台指序列(idx_5m 欄位)。不影響觸發。"""
        for b in bars:
            day, s = self._day_sec(str(b.get("t") or ""))
            if s < 0: continue
            self._roll_date(day)
            try: self.idx.append((s, float(b["c"])))
            except (KeyError, TypeError, ValueError): continue
        while self.idx and self.idx[-1][0] - self.idx[0][0] > 310:
            self.idx.popleft()

    def _idx_1m(self, s: int) -> float | None:
        return self._idx_move(s, 60)

    def _idx_move(self, s: int, win: int) -> float | None:
        if not self.idx: return None
        cur = None; base = None
        for is_, ip_ in self.idx:
            if is_ <= s: cur = ip_
            if is_ <= s - win: base = ip_
        if cur is None or base is None or base <= 0: return None
        return (cur / base - 1) * 1e4

    def _idx_5m(self, s: int) -> float | None:
        return self._idx_move(s, 300)

    @staticmethod
    def _day_sec(bar_time: str) -> tuple[str, int]:
        t = bar_time.replace("T", " ")
        try:
            return t[:10], int(t[11:13]) * 3600 + int(t[14:16]) * 60 + int(t[17:19])
        except (ValueError, IndexError):
            return t[:10], -1

    def _roll_date(self, day: str) -> None:
        if day != self.date:
            self.date = day
            self.win.clear(); self.cooldown.clear(); self.idx.clear(); self.last_trigger.clear()
            self.last_dir_trigger.clear()

    def feed_stock(self, sid: str, bars: list) -> list[V3Signal]:
        out: list[V3Signal] = []
        for b in bars:
            day, s = self._day_sec(str(b.get("t") or ""))
            if s < 0:
                continue
            self._roll_date(day)
            try:
                px = float(b["c"]); vol = float(b.get("v") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            w = self.win.setdefault(sid, deque())
            _trig = micro_trigger()
            _keep = (2 * VOLBURST_BASE_SEC + VOLBURST_WIN_SEC + 5) if _trig == "volburst10x" else MICRO_LOOKBACK_SEC
            # (2×300+5+5=610 秒:r5 觀測欄位需要 s-605 的量能歷史)
            while w and s - w[0][0] > _keep:
                w.popleft()
            _end = self.entry_end_s or _MICRO_ENTRY_END_S
            if (_trig == "volburst10x"
                    and w and _MICRO_ENTRY_START_S <= s <= _end
                    and s >= self.cooldown.get(sid, 0)
                    and cost_bps(px) <= MAX_COST_BPS):
                sig = _volburst_eval(w, s, px, vol, idx_5m=self._idx_5m(s), idx_1m=self._idx_1m(s),
                                     unit_shares=self.unit_shares)
                if sig:
                    sig.sid = sid
                    # V4 四層過濾(env 閘門;不過的訊號整筆丟棄,但單發時鐘照記,
                    # 因為「前例」的定義是觸發本身不是成交)
                    _v4_ok = True
                    if v4_mode():
                        _prior = self.last_dir_trigger.get((sid, sig.direction), -99999)
                        if sig.amt is not None and sig.amt >= V4_MAX_NTD:
                            _v4_ok = False                       # 終點單
                        elif not (sig.vol_r1 is not None
                                  and (sig.vol_r1 < V4_R1_LO or sig.vol_r1 > V4_R1_HI)):
                            _v4_ok = False                       # 量能持平
                        elif (sig.idx_5m is not None
                              and (1 if sig.direction == "long" else -1) * sig.idx_5m <= V4_IDX_OPP_BPS):
                            _v4_ok = False                       # 台指5分逆向
                        elif s - _prior <= V4_SOLO_SEC:
                            _v4_ok = False                       # 連環(同向前例)
                    self.last_dir_trigger[(sid, sig.direction)] = s
                    if _v4_ok:
                        out.append(sig)
                    self.cooldown[sid] = s + MICRO_COOLDOWN_SEC
                    self.last_trigger[sid] = s
            elif (_trig == "peak30"           # 2026-08-31 修:volburst 條件不成立時
                    # 曾漏到本分支發出 peak30 fade 訊號(測試抓到)——分支必須綁觸發型別
                    and w and len(w) >= 3
                    and _MICRO_ENTRY_START_S <= s <= (self.entry_end_s or _MICRO_ENTRY_END_S)
                    and s >= self.cooldown.get(sid, 0)
                    and cost_bps(px) <= MAX_COST_BPS):
                pmax = max(x[1] for x in w); pmin = min(x[1] for x in w)
                vmax = max(x[2] for x in w)
                up_burst = (px - pmin) / pmin * 1e4 if pmin > 0 else 0.0
                dn_burst = (pmax - px) / px * 1e4 if px > 0 else 0.0
                if vol >= vmax:                            # 量 = 30 秒峰值
                    sig = None
                    if px >= pmax and up_burst >= MICRO_BURST_MIN_BPS:      # 價 = 30 秒峰值(高)
                        sig = V3Signal(sid=sid, direction="short", fill=px,
                                       score=up_burst / MICRO_BURST_MIN_BPS,
                                       trigger_time=f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}",
                                       z=up_burst / MICRO_BURST_MIN_BPS, rel_bps=up_burst,
                                       mode="micro30")
                    elif px <= pmin and dn_burst >= MICRO_BURST_MIN_BPS:    # 價 = 30 秒峰值(低)
                        sig = V3Signal(sid=sid, direction="long", fill=px,
                                       score=dn_burst / MICRO_BURST_MIN_BPS,
                                       trigger_time=f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}",
                                       z=-dn_burst / MICRO_BURST_MIN_BPS, rel_bps=-dn_burst,
                                       mode="micro30")
                    if sig:
                        out.append(sig)
                        self.cooldown[sid] = s + MICRO_COOLDOWN_SEC
                        self.last_trigger[sid] = s
            w.append((s, px, vol))
        return out


# --- volburst10x 觸發(2026-08-31 使用者指定:「5秒內的量是過去3分鐘平均的10倍」) ---
#
# 事件研究(乾淨 21 天,n=8,407 · 420次/日):
#   持有 30s:順勢 +3.01 bps(日叢集 t=+7.11)· 逆勢 −3.01 —— 方向是【順勢】。
#   持有 60s:+2.84(t=+6.29);300s:+0.20(t=0.04)→ 效應只活 30~60 秒。
#   分層:爆量 10×~50×+ 效應平坦(+2.3~+4.4);5秒移動 ≥60bps 翻負(−2.92);
#         ≥150 口歸零(−0.00);三強交集(≥25×∩≥30bps∩≥50口)= −0.76 ——
#         **不隨強度放大,極端處消失**,是小而均勻的微結構延續,沒有放大桿。
#   毛值 ~3 bps vs 成本 ~22 bps → 淨值約 −19 bps/趟。統計上最顯著、經濟上最不可行。
MICRO_TRIGGER_DEFAULT = "volburst10x"

# ── V4 模式(2026-09-01 深夜):凍結候選 v4b 搬進 worker 本體 ──────────────
# 動機:兩份獨立重建同一規格差 7bp/筆(實作敏感度),9 月裁決必須以 worker 單一
# 實作為準。規格=21 天回測凍結版:金額<1億(排終點單/滿單)∧ 量能U(r1<0.7或>1.4)
# ∧ 台指5分不逆(方向×idx_5m>−3bp,無資料放行)∧ 單發(同檔同向300秒內無前例)
# ∧ 持有300秒 ∧ 災難停損1.5%(既有)。樣本內:8.3筆/日·毛+20.87bp(t=4.91)·
# 月淨+168,851@費176。被否決不裝的:移動停利/名目均衡/家族一切用法/搶佔。
def v4_mode() -> bool:
    return os.environ.get("ORDER_MOMENTUM_ROTATION_V4", "0").strip() == "1"

V4_MAX_NTD = 100_000_000    # ≥1億=終點單(滿單499張/掃穿),巨單衰減快
V4_R1_LO, V4_R1_HI = 0.7, 1.4   # 量能U:持平帶=無異常前置,排除
V4_IDX_OPP_BPS = -3.0       # 台指5分逆向門檻
V4_SOLO_SEC = 300           # 單發:同檔同向冷卻
V4_HOLD_SEC = 300           # 持有5分鐘(30秒版毛+8 vs 300秒+20.9)
VOLBURST_WIN_SEC = 5
VOLBURST_BASE_SEC = 300      # 2026-08-31 定版:baseline 5 分鐘(使用者最終規格 20×/5s/5m)
VOLBURST_MULT = 20.0         # 同上;10~50× 掃描證實效應對倍數不敏感(+3.0~4.3 平坦)
# 2026-08-31 使用者指定:地板改成**金額** ≥ 5,000 萬(取代原 ≥20 口)。
# 量測依據(現貨側事件研究,21 天):≥1,000萬 ≈ 原張數地板(+3.13);≥5,000萬
# 毛值 +4.55(t=4.03)、事件 76次/日、top3 檔集中度 26%→39%。
# 誠實註記:①該量測在現貨爆量上做,本觸發目前讀的是期貨 tape,金額換算
# (口×價×2000)是忠實翻譯但非同一量測 ②+4.55 vs +3.17 的增益屬預註冊清單
# 第 6 條,尚未經新樣本確認 ③金額地板同時吃掉死 tape 假爆量,原 MIN_LOTS 廢除。
VOLBURST_MIN_NTD = 50_000_000
VOLBURST_MIN_MOVE_BPS = 1.0  # 全平的爆量無方向可言
RULE_V3M_VOLBURST = "mr3m_sp50m_or_volburst20x_5s_5m_ntd50m_follow_h30s_cd60_cost26_spr2t"


def v3_source() -> str:
    """觸發 tape:"spot"(現貨,2026-08-31 起預設——期貨獨有爆量 ret120 −6.73)
    或 "futures"(舊行為,退回用)。"""
    m = os.environ.get("ORDER_MOMENTUM_ROTATION_V3_SOURCE", "spot").strip().lower()
    return m if m in ("spot", "futures") else "spot"


def micro_trigger() -> str:
    m = os.environ.get("ORDER_MOMENTUM_ROTATION_MICRO_TRIGGER", MICRO_TRIGGER_DEFAULT).strip().lower()
    return m if m in ("volburst10x", "peak30") else MICRO_TRIGGER_DEFAULT


def _volburst_eval(w: deque, s: int, px: float, vol: float,
                   idx_5m: float | None = None, idx_1m: float | None = None,
                   unit_shares: float = 2000.0) -> V3Signal | None:
    """w = 過去 ≤(BASE+WIN) 秒的 (sec, px, vol)。回傳順勢訊號或 None。

    2026-08-31 使用者指定:**單筆列印金額 ≥ VOLBURST_MIN_NTD 直接觸發**,不再檢查
    20× 倍數與 5 秒窗 —— 一張五千萬的單自己就是證據,不需要統計比較。方向 = 該筆
    vs 60 秒內最近一個不同價(全平則放棄)。現貨側量測:66次/日 · 毛值 +3.65 ·
    t=3.21 · **top3 檔集中度 65%**(只有高價股印得出單筆五千萬,分散度代價要記得)。
    窗版(20×/5m ∧ 窗金額≥5,000萬)保留為第二路徑,兩者 OR。"""
    if not w:
        return None
    # 量能趨勢觀測欄位(單次掃描,PIT:全在爆量窗之前)
    b_1m = b_4m = b_prev5 = 0.0
    for ws_, _, wv_ in w:
        if ws_ > s - 65 and ws_ <= s - VOLBURST_WIN_SEC:
            b_1m += wv_
        elif ws_ > s - 305 and ws_ <= s - 65:
            b_4m += wv_
        elif ws_ > s - 605 and ws_ <= s - 305:
            b_prev5 += wv_
    _r1 = (b_1m / 12.0) / (b_4m / 48.0) if b_4m > 0 else None
    _r5 = ((b_1m + b_4m) / 60.0) / (b_prev5 / 60.0) if b_prev5 > 0 else None
    # --- 路徑一:單筆大額列印(豁免倍數與窗檢查) ---
    if vol * px * unit_shares >= VOLBURST_MIN_NTD:
        prev = None
        for ws_, wp_, _ in reversed(w):
            if s - ws_ > 60:
                break
            if wp_ != px:
                prev = wp_
                break
        if prev is not None:
            mv = (px - prev) / prev * 1e4
            return V3Signal(sid="", direction="long" if mv > 0 else "short", fill=px,
                            score=vol * px * unit_shares / VOLBURST_MIN_NTD,
                            trigger_time=f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}",
                            z=mv / 10.0, rel_bps=mv, mode="micro30",
                            vol_r1=_r1, vol_r5=_r5, idx_5m=idx_5m, idx_1m=idx_1m,
                            amt=vol * px * unit_shares)
    # --- 路徑二:5 秒窗爆量(維持 20×/5m + 窗金額地板) ---
    v5 = vol; p0 = px; base = 0.0; n_base = 0
    for ws, wp, wv in w:
        if ws > s - VOLBURST_WIN_SEC:
            v5 += wv
        elif ws > s - VOLBURST_BASE_SEC - VOLBURST_WIN_SEC:
            base += wv; n_base += 1
        if ws <= s - VOLBURST_WIN_SEC:
            p0 = wp                      # 5 秒窗前的最後一筆價
    if n_base < 5 or base <= 0:
        return None
    avg5 = base / (VOLBURST_BASE_SEC / VOLBURST_WIN_SEC)
    if v5 < VOLBURST_MULT * avg5 or v5 * px * unit_shares < VOLBURST_MIN_NTD:
        return None
    move5 = (px - p0) / p0 * 1e4 if p0 > 0 else 0.0
    if abs(move5) < VOLBURST_MIN_MOVE_BPS:
        return None
    direction = "long" if move5 > 0 else "short"      # 順勢(實測 fade 為 −3.01)
    return V3Signal(sid="", direction=direction, fill=px,
                    score=v5 / max(avg5, 1e-9) / VOLBURST_MULT,
                    trigger_time=f"{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}",
                    z=move5 / 10.0, rel_bps=move5, mode="micro30",
                    vol_r1=_r1, vol_r5=_r5, idx_5m=idx_5m, idx_1m=idx_1m,
                    amt=v5 * px * unit_shares)
