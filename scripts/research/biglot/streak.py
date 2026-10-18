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
RISK_NB5 = 3              # 情境標籤：連次≥10 ∧ 爆量窗 ≥3 ∧ 剛中斷


def _ok(q):
    return q is not None and not (isinstance(q, float) and math.isnan(q)) and q > 0


class _Sym:
    __slots__ = ("bid", "ask", "prev_px", "prev_side", "ref", "ht", "hp",
                 "cb", "o", "i", "first", "last", "hi", "lo",
                 "d", "n", "closed_b", "last_dom", "s_vmax", "s_nb5", "s_cum", "last_break_up")

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
        self.last_dom = 0.0                     # 最近一個已收窗的『主導方向』成交張數＝官方「連量」（單 5 秒窗）
        self.s_vmax = 0.0                       # 目前連攻段內單窗最大主導量（張）
        self.s_cum = 0.0                        # 目前連攻段累計主導方向量（張）＝連量的『整段累計』解讀（官方定義單窗/累計未定，並列顯示）
        self.s_nb5 = 0                          # 目前連攻段內『爆量窗』數（單窗主導量 ≥ BURST_WIN_X × 每5秒均量）
        self.last_break_up = None               # 最近一次外攻段被中斷：「剛中斷」用


class StreakTracker:
    def __init__(self):
        self._s: dict[str, _Sym] = {}

    # ---- 設定 -------------------------------------------------------------
    def _st(self, sid) -> _Sym:
        st = self._s.get(sid)
        if st is None:
            st = self._s[sid] = _Sym()
        return st

    def set_ref(self, sid: str, lots_per_min: float | None) -> None:
        """PIT 每分鐘均量（張）：前 ≤10 日連續盤日量 ÷ 265。沒設（或 ≤0）→ win_x 為 None、不算爆量窗。"""
        self._st(sid).ref = lots_per_min if _ok(lots_per_min) else None

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
        dom = o if d > 0 else (i if d < 0 else 0.0)
        ref5 = (st.ref / 12.0) if st.ref else None
        is_burst = bool(ref5 and dom >= BURST_WIN_X * ref5)
        out: list[dict] = []
        if d != 0 and d == st.d:
            st.s_vmax = max(st.s_vmax, dom)
            st.s_nb5 += 1 if is_burst else 0
            st.s_cum += dom
            st.n += 1
        else:
            if st.d != 0 and st.n >= BREAK_MIN_N:      # 原連攻段在此被中斷或反向 → 結算事件
                out.append({"t": tt, "d": st.d, "n": st.n, "nb5": st.s_nb5, "vmax": st.s_vmax,
                            "win_lots": st.last_dom})
            if st.d > 0:
                st.last_break_up = {"t": tt, "n": st.n, "nb5": st.s_nb5, "vmax": st.s_vmax}
            if d != 0:
                st.d, st.n = d, 1
                st.s_vmax, st.s_nb5, st.s_cum = dom, (1 if is_burst else 0), dom
            else:
                st.d, st.n, st.s_vmax, st.s_nb5, st.s_cum = 0, 0, 0.0, 0, 0.0
        st.last_dom = dom
        return out

    # ---- 讀取 -------------------------------------------------------------
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
        status = "剛中斷" if broke else ("攻擊中" if abs(st.n) >= ATTACK_N else "")
        risk = bool(broke and lb["n"] >= LIT_N and lb["nb5"] >= RISK_NB5)
        win_lots = st.last_dom if st.d else 0.0
        return {"d": st.d, "n": st.n,
                "win_lots": win_lots,                                  # 連量（單窗解讀）：最近一窗主導方向成交張數
                "cum_lots": st.s_cum if st.d else 0.0,                 # 連量（累計解讀）：整段連攻主導方向累計張數
                "win_x": (win_lots / ref5) if (ref5 and win_lots) else None,   # 該窗 ÷ 每 5 秒均量
                "nb5": st.s_nb5 if st.d else 0, "vmax": st.s_vmax if st.d else 0.0,
                "lit": abs(st.n) >= LIT_N,
                "ret15": ret15, "status": status, "risk": risk, "bid_ask": (st.bid, st.ask)}
