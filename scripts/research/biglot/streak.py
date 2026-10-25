"""連次／連量 串流狀態機（反向工程「盤中當沖神器」；規格 docs/exhaustion-streak-spec.md）。

純函式庫：不 import biglot_dashboard、不連線、不碰 ST。呼叫端逐筆餵連續盤成交，
讀 `snapshot()` 當描述欄；語意與批次研究 `exhaust_lib.streaks` 逐窗一致
（重放比對 scripts/research/exhaust_parity.py）。

定義（官方，依「盤中當沖神器」說明欄；2026-10-18 起唯一口徑）
- 5 秒窗（time-of-day 對齊）：外盤量 > 內盤量 = 正（紅）；內 > 外 = 負（綠）；平手或無方向 = 中斷；無成交窗跳過。
- **連次** = 連續同向窗數（外攻為正、內攻為負）；達 ±10 亮燈（官方：亮燈＋音效）。
- **連量**（單窗 vs 累計**尚未定案**，2026-10-18 起並列）：`win_lots`＝最近已收 5 秒窗的主導方向張數（說明欄解讀）；`cum_lots`＝整段連攻累計（S0 畫面「每一連次累積的成交量總和」解讀；畫面數字對照本機日線較合理，見 knowledge-book 附錄 E.4）。`win_x` = 該窗 ÷ 該股每 5 秒均量（PIT）。
- **爆量窗** = 單窗主導量 ≥ BURST_WIN_X × 每 5 秒均量（門檻為本 repo 自訂；官方未揭露）；`nb5` = 目前連攻段內爆量窗數。
- **轉折（竭盡）** = 連攻段被中斷（紅轉綠／綠轉紅）的那個 5 秒窗收盤（PIT）。`status` 的「剛中斷」＝60 秒內外攻段（n≥6）被中斷；
  情境標籤 `risk` ＝ 剛中斷 ∧ 該段連次 ≥10 ∧ 爆量窗 ≥3。
- on_trade/advance 回傳「連攻段被中斷」事件（n≥BREAK_MIN_N），供批次重放比對（scripts/research/exhaust_parity.py）。
- 內外盤：價 ≥ ask → 外；≤ bid → 內；其餘 tick rule（同價承襲前一方向）。bid/ask 缺值或 ≤0 → 沿用最近已知值。
- 競價（開盤彙總量、isOpen/isClose）：`auction=True` 只更新報價與價格史，不計內外盤。

這是**描述**，不是訊號：歷史上竭盡後放空含價差淨值為負（見 docs/exhaustion-streak-spec.md）。
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import deque

BIN = 5.0
OPEN_T, CLOSE_T = 9 * 3600, 13 * 3600 + 25 * 60
WIN = 900.0
BREAK_MIN_N = 3          # 連攻段至少幾窗，被中斷時才回傳事件
ATTACK_N = 6            # 「攻擊中」門檻（連次）
JUST_BROKE_SEC = 60.0   # 「剛中斷」顯示窗
LIT_N = 10                # 官方：連次達 ±10 亮燈＋音效
BURST_WIN_X = 5.0         # 爆量窗＝單窗主導量 ≥ 5× 該股每 5 秒均量（門檻為本 repo 自訂，官方未揭露）
RISK_NB5 = 3              # 情境標籤：連次≥10 ∧ 爆量窗 ≥3 ∧ 剛中斷 ∧ 15 分漲 ≥RISE_BPS（知識書 R61）
RISE_BPS = 200.0          # R61 的『急拉』：15 分鐘漲幅 ≥200bps
# 竭盡點＝小哥三要件（2026-10-21 依 knowledge-book S13b/V10 對齊）：①連次達 ±10 後轉色 ②伴隨大量（多次爆量；門檻本 repo 自訂＝爆量窗 ≥EXH_MIN_NB5）
# ③價格『爆量漲不動』（連攻段最後 STALL_WINS 個窗沒再創段內新高/新低，且打斷窗本身也沒創；STALL_WINS 為本 repo 自訂）
# ②的『大量』：有分位數門檻時＝該窗主導量 ≥ 該股同時段前10日單窗主導量 P90（jack 2026-10-21 選定）；沒有時退回 ≥5×每5秒均量
EXH_MIN_NB5 = 3
# ── 竭盡點 v2（2026-10-25 起，jack 指示套用研究定案參數；研究見 docs/exhaust-reversal-scale-20261025.md §11–12）──
# 竭盡 ＝ 轉色（連攻後下一個有成交的窗由另一方主導，平手不算）∧ 連次 ≥EXH_MIN_N ∧ 近 30 秒相對成交量 RVOL ≥EXH_RVOL。
# RVOL ＝ 近 30 秒（含轉色窗的最後 6 個 5 秒窗）成交量 ÷ 該股平均每 30 秒成交量（基準＝過去 10 個交易日整日平均成交率，即 set_ref 的分均量÷2）。
# 33 日、1,170 股票日、919k 事件的網格（前半選／後半驗）：5 秒窗最佳（2/3 秒反而較弱），約 20 個訊號／檔／日（多空合計），毛 +5.1／+6.2／+6.6 bps（1／3／5 分）。
# 舊三要件（連次≥10、爆量窗≥3、漲不動、打斷窗有量）欄位仍保留於事件內供顯示，但不再決定『竭盡』。
EXH_MIN_N = 4
EXH_RVOL = 2.0
RVOL_WINS = 6               # 30 秒＝6 個 5 秒窗
RULES_VERSION = "exh-v2-2026-10-25"
MIN_FLIP_LOTS = 5          # 『突然轉色』要有量：打斷窗主導量 ≥ max(5 張, 該股同時段大量門檻的一半)（無分位門檻時 ≥ 2.5×每5秒均量）；jack 2026-10-21 要求，數字為自訂
FLIP_FRAC = 0.5
STALL_WINS = 2            # jack 2026-10-21 選定（基準組合 A：分位90%·爆窗≥3·末2窗）
THR_BUCKET_SEC = 1800     # 單窗『大量』門檻按 30 分鐘時段分桶（scripts/research/biglot_streak_thresholds.py）


def _ok(q):
    return q is not None and not (isinstance(q, float) and math.isnan(q)) and q > 0


def exch_tod(time_us, recv_tod: float, max_skew: float = 10.0) -> float:
    """交易所成交時間（ws `time`，µs epoch）→ 台北當日秒數。缺值、非數字或與『收到時間』差 >max_skew 秒（時鐘異常）→ 退回收到時間。
    為什麼用交易所時間：與期貨（`trade_time`）一致；收到時間系統性比交易所時間早約 0.55 秒（台虹 10/20 實測 20,275 筆，p95 僅差 0.12 秒），
    等於把 5 秒窗邊界平移 0.55 秒、約 11% 的成交會落進不同的窗。"""
    try:
        tod = (float(time_us) / 1e6 + 8 * 3600) % 86400
    except (TypeError, ValueError):
        return recv_tod
    return tod if abs(tod - recv_tod) <= max_skew else recv_tod


def is_exhaustion(ev: dict) -> bool:
    """竭盡點 v2：轉色 ∧ 連次≥EXH_MIN_N ∧ 近 30 秒 RVOL≥EXH_RVOL（參數來源與基準見檔頭 EXH_*）。三個頁面／欄位／前向帳本共用。
    缺 RVOL（沒有 PIT 分均量）→ 不判定竭盡。"""
    rv = ev.get("rvol30")
    return bool(ev.get("kind") == "flip" and ev.get("n", 0) >= EXH_MIN_N and rv is not None and rv >= EXH_RVOL)


class _Sym:
    __slots__ = ("bid", "ask", "prev_px", "prev_side", "ref", "ht", "hp",
                 "cb", "o", "i", "first", "last", "hi", "lo",
                 "d", "n", "closed_b", "last_dom", "wtot", "s_vmax", "s_nb5", "s_cum", "last_break_up", "last_break", "px0", "ev", "s_ext", "s_ext_n", "thr", "sid")

    def __init__(self):
        self.bid = self.ask = None
        self.prev_px = None
        self.prev_side = 0
        self.ref = None
        self.ht, self.hp = [], []               # 價格史（每筆，含競價）：供 ret15 取 WIN 秒前價
        self.cb = None                          # 目前未收的 5 秒窗 id
        self.o = self.i = 0.0
        self.first = self.last = self.hi = self.lo = None
        self.d = 0                              # 目前連攻方向 (+1/-1/0)
        self.n = 0
        self.closed_b = -1                      # 已收的最大窗 id（遲到成交不回頭改帳）
        self.wtot = []                          # 最近已收窗的 (窗id, 外盤+內盤成交張)：算近 30 秒 RVOL
        self.last_dom = 0.0                     # 最近一個已收窗的『主導方向』成交張數＝官方「連量」（單 5 秒窗）
        self.s_vmax = 0.0                       # 目前連攻段內單窗最大主導量（張）
        self.s_cum = 0.0                        # 目前連攻段累計主導方向量（張）＝連量的『整段累計』解讀（官方定義單窗/累計未定，並列顯示）
        self.s_nb5 = 0                          # 目前連攻段內『爆量窗』數（單窗主導量 ≥ BURST_WIN_X × 每5秒均量）
        self.last_break_up = None               # 最近一次外攻段被中斷：「剛中斷」用
        self.last_break = None                  # 最近一次任一方向連攻段被中斷（紅轉綠＝買盤竭盡、綠轉紅＝賣盤竭盡；小哥原版轉色欄用）
        self.ev = []                            # 當日事件史：('lit'|'flip', 窗收盤秒, 方向, 連次, 爆窗數, 價, 窗均張)；/grid 標注竭盡點用
        self.sid = None
        self.thr = None                         # {時段桶: 單窗『大量』門檻(張)}；None＝退回均量×5
        self.s_ext = None                       # 目前連攻段內價格極值（外攻=最高、內攻=最低）
        self.s_ext_n = 0                        # 極值最後一次更新時的連次（算『漲不動』用）
        self.px0 = None                         # 當日第一筆價（含開盤競價）＝開盤價；今日紅K/黑K判斷用


class StreakTracker:
    def __init__(self):
        self._s: dict[str, _Sym] = {}
        self._listeners: list = []

    def add_listener(self, fn) -> None:
        """事件監聽：新的 flip 事件（亮燈連攻段真轉色）產生時呼叫 fn(sid, ev)。reset() 不清監聽。"""
        self._listeners.append(fn)

    # ---- 設定 -------------------------------------------------------------
    def _st(self, sid) -> _Sym:
        st = self._s.get(sid)
        if st is None:
            st = self._s[sid] = _Sym()
            st.sid = sid
        return st

    def set_ref(self, sid: str, lots_per_min: float | None) -> None:
        """PIT 每分鐘均量（張）：前 ≤10 日連續盤日量 ÷ 265。沒設（或 ≤0）→ win_x 為 None、不算爆量窗。"""
        self._st(sid).ref = lots_per_min if _ok(lots_per_min) else None

    def set_thresholds(self, sid: str, thr: dict | None) -> None:
        """每檔『大量』門檻：{30 分鐘時段桶(int): 張}（PIT：前 10 日同時段單窗主導量 P90）。沒設的時段退回 ≥5×每 5 秒均量。"""
        self._st(sid).thr = dict(thr) if thr else None

    def reset(self) -> None:
        """換日：清所有狀態（ref 也清，呼叫端要重新 set_ref）。"""
        self._s.clear()

    # ---- 餵資料 -----------------------------------------------------------
    def on_trade(self, sid: str, t: float, px: float, dv: float,
                 bid=None, ask=None, auction: bool = False) -> list[dict]:
        """t = 當日秒數（time-of-day）。回傳此筆觸發的「連攻段被中斷」事件（通常 0 或 1 筆）。"""
        t, px, dv = float(t), float(px), float(dv)
        st = self._st(sid)
        if _ok(bid):
            st.bid = float(bid)
        if _ok(ask):
            st.ask = float(ask)
        if st.px0 is None:
            st.px0 = px
        st.ht.append(t); st.hp.append(px)
        if len(st.ht) > 40000:                   # 修剪：留 WIN+60 秒內
            cut = bisect_right(st.ht, t - WIN - 60) - 1
            if cut > 0:
                del st.ht[:cut]; del st.hp[:cut]
        if auction:
            return []
        side = 0
        if st.ask is not None and px >= st.ask:
            side = 1
        elif st.bid is not None and px <= st.bid:
            side = -1
        else:
            if st.prev_px is not None:
                side = 1 if px > st.prev_px else (-1 if px < st.prev_px else st.prev_side)
            else:
                side = st.prev_side
        st.prev_px = px
        if side != 0:
            st.prev_side = side
        if not (OPEN_T <= t <= CLOSE_T):
            return []
        b = math.floor(t / BIN)
        if b <= st.closed_b:
            return []                            # 遲到（advance 已收該窗）：忽略，避免重複分窗
        out: list[dict] = []
        if st.cb is not None and b != st.cb:
            out = self._close_bin(st)
        if st.cb is None or b != st.cb:
            st.cb = b; st.o = st.i = 0.0; st.first = px; st.hi = st.lo = px
        if side > 0:
            st.o += dv
        elif side < 0:
            st.i += dv
        st.last = px; st.hi = max(st.hi, px); st.lo = min(st.lo, px)
        return out

    def advance(self, sid: str, now: float) -> list[dict]:
        """時間驅動：目前窗的收盤時刻已過 → 收窗（讓「剛中斷」不必等下一筆成交）。
        now 請用「已讀入的最新成交時間」(水位)，不要用牆上時鐘——ingest 讀檔有落後，牆上時鐘會提早收窗。"""
        st = self._s.get(sid)
        if st is None or st.cb is None or now < (st.cb + 1) * BIN:
            return []
        return self._close_bin(st)

    # ---- 內部 -------------------------------------------------------------
    def _px_before(self, st: _Sym, t0: float):
        j = bisect_right(st.ht, t0) - 1
        return st.hp[max(j, 0)] if st.hp else None

    def _close_bin(self, st: _Sym) -> list[dict]:
        cb, o, i = st.cb, st.o, st.i
        st.cb = None
        st.closed_b = max(st.closed_b, cb)
        d = (o > i) - (o < i)
        tt = (cb + 1) * BIN
        st.wtot.append((cb, o + i))
        if len(st.wtot) > 12:
            del st.wtot[:-12]
        dom = o if d > 0 else (i if d < 0 else 0.0)
        ref5 = (st.ref / 12.0) if st.ref else None
        bk = int((cb * BIN - OPEN_T) // THR_BUCKET_SEC)
        if st.thr and bk in st.thr:
            is_burst = bool(dom > 0 and dom >= st.thr[bk])
        else:
            is_burst = bool(ref5 and dom >= BURST_WIN_X * ref5)
        out: list[dict] = []
        if d != 0 and d == st.d:
            st.s_vmax = max(st.s_vmax, dom)
            st.s_nb5 += 1 if is_burst else 0
            st.s_cum += dom
            st.n += 1
            if st.s_ext is None or (d > 0 and st.hi > st.s_ext) or (d < 0 and st.lo < st.s_ext):
                st.s_ext, st.s_ext_n = (st.hi if d > 0 else st.lo), st.n
            if st.n == LIT_N:
                st.ev.append({"kind": "lit", "t": tt, "d": d, "n": st.n, "nb5": st.s_nb5, "px": st.last,
                              "avg": st.s_cum / st.n})
        else:
            brk = None
            if st.d != 0 and st.n >= EXH_MIN_N and d == -st.d:   # 必須真的轉色（外→內／內→外）；平手只是中斷、不算轉色
                newext = (st.d > 0 and st.hi > st.s_ext) or (st.d < 0 and st.lo < st.s_ext)     # 打斷窗本身又創段內極值＝沒漲不動
                stall = bool(not newext and (st.n - st.s_ext_n) >= STALL_WINS)
                need = max(MIN_FLIP_LOTS, FLIP_FRAC * st.thr[bk]) if (st.thr and bk in st.thr) else max(MIN_FLIP_LOTS, 2.5 * ref5 if ref5 else 0.0)
                vol30 = sum(v for b_, v in st.wtot if b_ > cb - RVOL_WINS)            # 含轉色窗的最近 6 個 5 秒窗（沒成交的窗＝0）
                rvol30 = (vol30 / (st.ref / 2.0)) if st.ref else None                 # ÷ 該股平均每 30 秒成交量
                brk = {"vol30": vol30, "rvol30": rvol30, "stall": stall, "since_ext": st.n - st.s_ext_n, "newext": bool(newext), "brk_ok": bool(dom >= need), "brk_lots": dom, "brk_x": (dom / ref5) if (ref5 and dom) else None,
                       "day_up": bool(st.px0 and st.last > st.px0)}
                ev_ = {"kind": "flip", "t": tt, "d": st.d, "n": st.n, "nb5": st.s_nb5, "px": st.last,
                       "avg": st.s_cum / st.n, **brk}
                st.ev.append(ev_)                           # 連次≥EXH_MIN_N 的連攻段轉色；是否『竭盡』見 is_exhaustion（轉色∧連次∧RVOL）
                for _fn in self._listeners:
                    try:
                        _fn(st.sid, ev_)
                    except Exception:  # noqa: BLE001 -- 監聽者出錯不得影響狀態機
                        pass
            if st.d != 0 and st.n >= BREAK_MIN_N:      # 原連攻段在此被中斷或反向 → 結算事件
                out.append({"t": tt, "d": st.d, "n": st.n, "nb5": st.s_nb5, "vmax": st.s_vmax,
                            "win_lots": st.last_dom})
            if st.d != 0:
                st.last_break = {"t": tt, "d": st.d, "n": st.n, "nb5": st.s_nb5, "vmax": st.s_vmax, **(brk or {})}
            if st.d > 0:
                st.last_break_up = {"t": tt, "n": st.n, "nb5": st.s_nb5, "vmax": st.s_vmax}
            if d != 0:
                st.d, st.n = d, 1
                st.s_vmax, st.s_nb5, st.s_cum = dom, (1 if is_burst else 0), dom
                st.s_ext, st.s_ext_n = (st.hi if d > 0 else st.lo), 1
            else:
                st.d, st.n, st.s_vmax, st.s_nb5, st.s_cum = 0, 0, 0.0, 0, 0.0
                st.s_ext, st.s_ext_n = None, 0
        st.last_dom = dom
        return out

    # ---- 讀取 -------------------------------------------------------------
    def events(self, sid: str) -> list:
        """當日事件史（複本，dict）：kind=lit|flip，t=窗收盤秒，d=方向（flip 的 d＝被打斷的連攻方向，+1＝買盤竭盡側、−1＝賣盤竭盡側），
        n 連次、nb5 爆窗數、px 價、avg 窗均張；flip 另有 stall（漲不動）、brk_lots／brk_x（打斷窗量）、day_up（當時價>開盤）。"""
        st = self._s.get(sid)
        return list(st.ev) if st else []

    def snapshot(self, sid: str, now: float | None = None) -> dict | None:
        st = self._s.get(sid)
        if st is None:
            return None
        if now is not None:
            self.advance(sid, now)
        ref5 = (st.ref / 12.0) if st.ref else None
        lb = st.last_break_up
        broke = bool(lb and lb["n"] >= ATTACK_N and now is not None and now - lb["t"] <= JUST_BROKE_SEC)
        ret15 = None
        if st.hp and st.ht:
            p0 = self._px_before(st, st.ht[-1] - WIN)
            ret15 = (st.hp[-1] / p0 - 1) * 1e4 if p0 else None
        ret2m = None
        if st.hp and st.ht:
            p2 = self._px_before(st, st.ht[-1] - 120.0)
            ret2m = (st.hp[-1] / p2 - 1) * 1e4 if p2 else None
        lbk = st.last_break
        flip = None
        if lbk and now is not None and now - lbk["t"] <= JUST_BROKE_SEC and lbk["n"] >= EXH_MIN_N and lbk.get("stall") is not None:
            flip = {"d": lbk["d"], "n": lbk["n"], "nb5": lbk["nb5"], "age": now - lbk["t"], "rvol30": lbk.get("rvol30"), "vol30": lbk.get("vol30"),
                    "stall": lbk.get("stall"), "brk_ok": lbk.get("brk_ok"), "brk_lots": lbk.get("brk_lots"), "brk_x": lbk.get("brk_x"),
                    "day_up": lbk.get("day_up"), "exh": is_exhaustion({"kind": "flip", **lbk})}   # 連次≥EXH_MIN_N 的連攻段剛轉色
        status = "剛中斷" if broke else ("攻擊中" if abs(st.n) >= ATTACK_N else "")
        risk = bool(flip and flip["exh"] and flip["d"] > 0)                                 # 相容舊鍵：買盤竭盡（紅轉綠）剛出現；v2 起＝is_exhaustion 上側
        rvol30_now = None                                                                    # 目前『最近 6 個 5 秒窗』相對成交量（顯示用；水位內已收的窗）
        if st.ref and st.wtot:
            cbl = st.wtot[-1][0]
            rvol30_now = sum(v for b_, v in st.wtot if b_ > cbl - RVOL_WINS) / (st.ref / 2.0)
        win_lots = st.last_dom if st.d else 0.0
        return {"d": st.d, "n": st.n,
                "win_lots": win_lots,                                  # 連量（單窗解讀）：最近一窗主導方向成交張數
                "cum_lots": st.s_cum if st.d else 0.0,                 # 連量（累計解讀）：整段連攻主導方向累計張數
                "win_x": (win_lots / ref5) if (ref5 and win_lots) else None,   # 該窗 ÷ 每 5 秒均量
                "nb5": st.s_nb5 if st.d else 0, "vmax": st.s_vmax if st.d else 0.0,
                "lit": bool(flip and flip.get("exh")),                 # 🔔＝符合竭盡v2訊號條件（轉色∧連次≥EXH_MIN_N∧RVOL≥EXH_RVOL，剛轉色 60 秒內）；2026-10-25 起取代官方±10 亮燈
                "lit10": abs(st.n) >= LIT_N,                           # 官方 |連次|≥10 亮燈（僅診斷／相容，不再當 🔔）
                "ret15": ret15, "ret2m": ret2m, "flip": flip, "px0": st.px0, "px": (st.hp[-1] if st.hp else None),
                "status": status, "risk": risk, "rvol30_now": rvol30_now, "bid_ask": (st.bid, st.ask), "burst_src": "p90" if st.thr else "x5"}
