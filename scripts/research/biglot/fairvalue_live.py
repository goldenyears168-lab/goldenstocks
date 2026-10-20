"""儀表板『期貨中價／校正合理價』即時資料（背景執行緒，每秒前進一次）。

與離線驗證共用同一個濾波器：`fairvalue_kalman.Filter`（規格見 docs/futures-fairvalue-prereg-20261020.md）。
資料全部是即時逐筆，不用 10 秒樣本：
  台指   ← cache/txf_trades_{日}.jsonl（ws 逐筆，買一賣一平均）
  買賣簿 ← cache/{root}_books/{root}_books_{日}.jsonl（每次報價更新）
  現股   ← cache/biglot_live_watch/raw_{日}.jsonl（ws 逐筆，排除試撮）
啟動時從 09:00:15 重播到現在，所以重啟儀表板後狀態與一路跑下來相同。
參數用 cache/fairvalue/params_*.json（最新一份，已凍結）。

顯示規則（預註冊）：平常顯示買賣簿中價；只有「報價已 ≥10 秒沒更新 且 台指自上次報價累計 ≥4 bps」才顯示校正價。
⚠ 校正價尚未通過前向驗證（預計 2026-11-03 判決），使用者要求先顯示，欄位標『實驗』。
唯讀、不碰 DB／下單。
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys
import threading
import time
from datetime import datetime

import numpy as np

import fairvalue_kalman as fk

CACHE = fk.CACHE
TZ = fk.TZ
LAG_SEC = 2            # 等 2 秒讓檔案尾巴落地再前進
STALE_SEC = 30         # 收集器（台指或任一買賣簿）沉默超過此秒數 → 不顯示
SANITY_BPS = 100       # 校正價偏離中價超過此值視為模型失常 → 不顯示

_lock = threading.Lock()
_snap: dict = {"day": None, "stocks": {}, "meta": {}}
_started = False


def tick_size(price: float) -> float:
    """個股期貨最小跳動點。2026-10-20 以 42 檔買賣簿實測：50–100→0.1、100–500→0.5、500–2500→1、≥2500→5；
    <50 的級距沒有樣本，採交易所標準(<10→0.01、10–50→0.05)，上線前待核對。"""
    return 0.01 if price < 10 else 0.05 if price < 50 else 0.1 if price < 100 else 0.5 if price < 500 else 1.0 if price < 2500 else 5.0


def snapshot() -> dict:
    return _snap


def ensure_started():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="fairvalue-live", daemon=True).start()


class _Tail:
    def __init__(self, path):
        self.path, self.off = path, 0

    def lines(self):
        try:
            with open(self.path, "rb") as f:
                f.seek(self.off)
                chunk = f.read()
        except OSError:
            return []
        nl = chunk.rfind(b"\n")
        if nl < 0:
            return []
        self.off += nl + 1
        return chunk[:nl].split(b"\n")


class _State:
    def __init__(self, day, sids, params):
        self.day, self.sids = day, sids
        self.params = params
        self.t0 = datetime.fromisoformat(day).replace(tzinfo=TZ, hour=9, minute=0, second=15).timestamp()
        n = fk.N_SEC
        self.tx_sec = np.full(n, np.nan); self.tx_pre = None; self.tx_last_t = 0.0
        self.mid = {s: np.full(n, np.nan) for s in sids}
        self.sp = {s: np.full(n, np.nan) for s in sids}
        self.top = {}                       # sid -> (bid, ask, bidsz, asksz)
        self.book_t = {}                    # sid -> 最近一次買賣簿到達的牆鐘時間
        self.sym = {}
        self.filters = {s: fk.Filter([params[s][k] for k in fk.PARAM_NAMES]) for s in sids if s in params}
        self.tail_tx = _Tail(f"{CACHE}/txf_trades/txf_trades_{day}.jsonl")
        self.tail_raw = _Tail(f"{CACHE}/biglot_live_watch/raw_{day}.jsonl")
        self.tail_bk = {s: _Tail(f"{CACHE}/{r}_books/{r}_books_{day}.jsonl") for s, r in sids.items()}
        self.done = -1                      # 已前進到的秒索引
        self.tx_cur = None
        self.last_book_wall = 0.0

    # ---- 讀新資料 ----
    def read(self):
        for ln in self.tail_tx.lines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            t = r.get("trade_time")
            if r.get("quote_type") == "FUTURE_AH" or not isinstance(t, (int, float)):
                continue
            b, a = r.get("bid"), r.get("ask")
            v = math.log((b + a) / 2 if b and a and a >= b else r["price"])
            t /= 1e6; i = int(t - self.t0)
            self.tx_last_t = max(self.tx_last_t, t)
            if i < 0:
                self.tx_pre = v
            elif i < fk.N_SEC:
                self.tx_sec[i] = v
        for ln in self.tail_raw.lines():
            if b'"EQUITY"' not in ln or b'"trades"' not in ln:
                continue
            try:
                d = (json.loads(ln).get("payload") or {}).get("data") or {}
            except ValueError:
                continue
            s = d.get("symbol")
            if s in self.sp and not d.get("isTrial") and d.get("price") and d.get("time"):
                i = int(d["time"] / 1e6 - self.t0)
                if 0 <= i < fk.N_SEC:
                    self.sp[s][i] = math.log(d["price"])
        for s, tl in self.tail_bk.items():
            for ln in tl.lines():
                if b'"day"' not in ln or b'"stale": true' in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                b, a, t = r.get("bids"), r.get("asks"), r.get("book_time")
                if r.get("session") != "day" or r.get("stale") or not b or not a or not t:
                    continue
                self.sym.setdefault(s, r["symbol"])
                if r["symbol"] != self.sym[s] or b[0]["price"] >= a[0]["price"] or b[0]["size"] <= 0 or a[0]["size"] <= 0:
                    continue
                i = int(t / 1e6 - self.t0)
                self.top[s] = (b[0]["price"], a[0]["price"], b[0]["size"], a[0]["size"])
                self.book_t[s] = time.time()
                self.last_book_wall = self.book_t[s]
                if 0 <= i < fk.N_SEC:
                    self.mid[s][i] = math.log((b[0]["price"] + a[0]["price"]) / 2)

    # ---- 逐秒前進 ----
    def advance(self, now_i):
        now_i = min(now_i, fk.N_SEC - 1)
        for i in range(self.done + 1, now_i + 1):
            cur = self.tx_sec[i]
            if cur != cur:
                cur = self.tx_cur if self.tx_cur is not None else self.tx_pre
            if cur is None:
                self.done = i
                continue
            prev = self.tx_cur if self.tx_cur is not None else cur
            dtx = (cur - prev) * 1e4
            self.tx_cur = cur
            if i >= 1:
                for s, f in self.filters.items():
                    f.step(i, dtx, cur, self.mid[s][i], self.sp[s][i])
            self.done = i

    def publish(self, now):
        stocks = {}
        closed = self.done >= fk.N_SEC - 1          # 收盤後定格：沿用最後狀態，不當成斷線
        tx_alive = closed or (now - self.tx_last_t) <= STALE_SEC
        coll_alive = closed or (now - self.last_book_wall) <= STALE_SEC
        for s, f in self.filters.items():
            top = self.top.get(s)
            if f.x is None or top is None or self.tx_cur is None:
                continue
            mid = (top[0] + top[1]) / 2          # 顯示用中價=最新買一賣一平均(濾波有 2 秒落後,不拿來當中價)
            fair = math.exp(f.x / 1e4)
            age = max(0, self.done - f.last)
            move = (self.tx_cur - f.tx_at) * 1e4
            why = None
            if not coll_alive:
                why = "收集器沉默"
            elif not tx_alive:
                why = "台指斷線"
            elif abs(fair / mid - 1) * 1e4 > SANITY_BPS:
                why = "模型失常"
            stocks[s] = dict(fair=fair, mid=mid, bid=top[0], ask=top[1], bidsz=top[2], asksz=top[3],
                             age=age, move=move, trig=(age >= fk.MIN_GAP and abs(move) >= fk.TRIGGER_BPS), why=why)
        return stocks


def _load_params():
    paths = sorted(glob.glob(f"{CACHE}/fairvalue/params_*.json"))
    if not paths:
        return None, None
    d = json.load(open(paths[-1]))
    return d["params"], d["asof"]


def _loop():
    st = None; params = asof = None
    while True:
        try:
            now = time.time()
            day = datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")
            if st is None or st.day != day:
                params, asof = _load_params()
                st = _State(day, fk.universe(), params) if params else None
            if st is not None and 0 <= now - st.t0 < 6 * 3600:
                st.read()
                st.advance(int(now - st.t0) - LAG_SEC)
                stocks = st.publish(now)
                _snap.update(day=day, stocks=stocks, meta=dict(asof=asof, t=now, done=st.done))
        except Exception as e:  # noqa: BLE001
            print(f"[fairvalue-live] {e!r}", file=sys.stderr)
        time.sleep(1.0)
