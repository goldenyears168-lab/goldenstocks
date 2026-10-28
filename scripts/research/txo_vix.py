"""盤中自算 VIXTWN(中價版):由 TXO 月選買賣報價每分鐘重建台指 VIX。純函式,無 I/O。

驗證(2026-10-21,1 天,對官方 15 秒檔逐分):286 分鐘全有值,偏差 -0.21、MAE 0.21、
盤中(09:15 後)MAE 0.18;開盤前 15 分鐘偏差 -0.86(報價簿尚稀疏)——所以儀表板 09:15 前不畫。
偏差一致為負(~-0.2 點),推論來自遠翼隔檔訂閱,未單獨驗證。

公式同 src/sync_vixtwn_computed.py(CBOE/TAIFEX 變異數複製 + 近/次月線性插補到 30 天),
差別:價格用買賣中價、T 用小數天(到期日 13:30 起算)、稀疏版(只訂部分履約價)、
報價短暫空白時沿用最近 HOLD 秒內的有效中價(否則平價履約價會選錯,實測會出現單分鐘假尖峰)。
R 與 sync_vixtwn_computed.R 同值(該檔 import finmind_client,這裡不引)。
"""
from __future__ import annotations

import math
import threading
from datetime import date, datetime, time, timedelta

R = 0.015
HOLD_SEC = 120
F_NEAR = 5
SPIKE_TOL = 0.6      # 偏離最近中位數超過此值(VIX 點)視為單拍雜訊
SPIKE_WIN = 8        # 參考窗:最近 8 個被接受的 15 秒值(約 2 分鐘)
SPIKE_MAX_REJECT = 6  # 連續被拒這麼多拍(約 90 秒)就視為真行情,放行


class MidBook:
    """各 (到期日, 履約價, C/P) 的報價狀態。

    有效 = b>0 且 a>=b;委託簿『沒變動就代表報價還在』,所以有效報價**不會因為久沒更新而過期**
    (ws 只在變動時推)。報價閃空(買價變 0/空)時沿用最近有效中價 hold 秒,過了才視為無報價。
    收錄器要自己檢查 ws feed 是否還活著(整條 feed 沉默就別算)。執行緒安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> [mid, invalid_since(None=目前有效)]
        self._st: dict[tuple[str, int, str], list] = {}
        self.subscribed: dict[str, set[int]] = {}                          # expiry -> strikes

    def register(self, expiry: str, strike: int) -> None:
        self.subscribed.setdefault(expiry, set()).add(strike)

    def update(self, expiry: str, strike: int, cp: str, bid, ask, t_sec: float) -> None:
        try:
            b, a = float(bid), float(ask)
        except (TypeError, ValueError):
            b = a = 0.0
        ok = b > 0 and a >= b
        key = (expiry, strike, cp)
        with self._lock:
            st = self._st.get(key)
            if ok:
                self._st[key] = [(b + a) / 2.0, None]
            elif st is not None and st[1] is None:
                st[1] = t_sec                      # 剛轉為無效,從此刻起算 hold

    def chains(self, now_sec: float, hold: float = HOLD_SEC) -> dict[str, dict[int, dict[str, float]]]:
        out: dict[str, dict[int, dict[str, float]]] = {}
        with self._lock:
            items = list(self._st.items())
        for (e, k, cp), (mid, inv) in items:
            if inv is None or now_sec - inv <= hold:
                out.setdefault(e, {}).setdefault(k, {})[cp] = mid
        return out


def sigma2(chain: dict[int, dict[str, float]], strikes_sub: list[int], T: float):
    both = [k for k in strikes_sub if chain.get(k, {}).get("C") and chain.get(k, {}).get("P")]
    if not both:
        return None
    # F 取 |C-P| 最小的 F_NEAR 檔各自推得之遠期價的中位數:單一履約價報價閃動(2026-10-28 實測
    # 會讓 argmin 跳到偏 100~150 點的履約價、VIX 單拍 +1.5)不再左右 F/K0。
    near = sorted(both, key=lambda k: abs(chain[k]["C"] - chain[k]["P"]))[:F_NEAR]
    fs = sorted(k + math.exp(R * T) * (chain[k]["C"] - chain[k]["P"]) for k in near)
    F = fs[len(fs) // 2] if len(fs) % 2 else (fs[len(fs) // 2 - 1] + fs[len(fs) // 2]) / 2
    below = [k for k in strikes_sub if k <= F]
    if not below:
        return None
    K0 = max(below)
    pts: dict[int, float] = {}
    if chain.get(K0, {}).get("C") and chain[K0].get("P"):
        pts[K0] = (chain[K0]["C"] + chain[K0]["P"]) / 2
    # CBOE:自 K0 往外,連續兩個已訂閱履約價無有效價就截斷
    for cp, seq in (("P", [k for k in strikes_sub if k < K0][::-1]), ("C", [k for k in strikes_sub if k > K0])):
        miss = 0
        for k in seq:
            q = chain.get(k, {}).get(cp)
            if q:
                pts[k] = q
                miss = 0
            else:
                miss += 1
                if miss >= 2:
                    break
    ks = sorted(pts)
    if len(ks) < 5:
        return None
    s = 0.0
    for i, k in enumerate(ks):
        dK = (ks[1] - ks[0]) if i == 0 else (ks[-1] - ks[-2]) if i == len(ks) - 1 else (ks[i + 1] - ks[i - 1]) / 2
        s += dK / (k * k) * math.exp(R * T) * pts[k]
    v = (2 / T) * s - (1 / T) * ((F / K0 - 1) ** 2)
    return v if v > 0 else None


def days_to_expiry(expiry: str, day: date, now_sec: float) -> float:
    """到期日 13:30 起算的小數天;now_sec = 當日 00:00 起的秒數。"""
    end = datetime.combine(date.fromisoformat(expiry), time(13, 30))
    return (end - datetime.combine(day, time(0, 0)) - timedelta(seconds=now_sec)).total_seconds() / 86400


def minute_vix(book: MidBook, day: date, now_sec: float, hold: float = HOLD_SEC):
    """近/次月(book.subscribed 的兩個到期日)插補到 30 天。算不出來回 None。"""
    expiries = sorted(book.subscribed)
    if len(expiries) != 2:
        return None
    chains = book.chains(now_sec, hold)
    legs = []
    for e in expiries:
        nd = days_to_expiry(e, day, now_sec)
        s2 = sigma2(chains.get(e, {}), sorted(book.subscribed[e]), nd / 365) if nd > 0 else None
        if s2 is None:
            return None
        legs.append((nd, s2))
    (n1, a), (n2, b) = legs
    if n2 <= n1:
        return None
    w1, w2 = (n2 - 30) / (n2 - n1), (30 - n1) / (n2 - n1)
    var = (n1 / 365 * a * w1 + n2 / 365 * b * w2) * (365 / 30)
    return round(100 * math.sqrt(var), 2) if var > 0 else None


class SpikeFilter:
    """15 秒值的時間軸濾波:偏離最近中位數 > SPIKE_TOL 的單拍丟掉(報價鏈更新不同步、ATM 選錯等雜訊,
    2026-10-28 實測單拍 +1.5 點);連續偏離超過 SPIKE_MAX_REJECT 拍才當真行情放行並重設參考窗。"""

    def __init__(self) -> None:
        self._hist: list[float] = []
        self._rej = 0

    def accept(self, v: float | None) -> float | None:
        if v is None:
            return None
        if self._hist:
            h = sorted(self._hist)
            med = h[len(h) // 2] if len(h) % 2 else (h[len(h) // 2 - 1] + h[len(h) // 2]) / 2
            if abs(v - med) > SPIKE_TOL:
                self._rej += 1
                if self._rej < SPIKE_MAX_REJECT:
                    return None
                self._hist = []
        self._rej = 0
        self._hist = (self._hist + [v])[-SPIKE_WIN:]
        return v
