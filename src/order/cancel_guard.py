"""撤單神器（cancel guard）· 只撤單、不下單。

規則（docs/ofi-cancel-prereg-20261021.md 與 2026-10-21 模擬）：被動掛單（新倉）掛出後，
個股期貨買賣簿中價往『對自己不利』的方向走 ≥ against_ticks（預設 1 tick）→ 通知／撤單。
  買單：中價下跌 ≥1 tick 為不利；賣單：中價上漲 ≥1 tick 為不利。
模擬：延遲 2 秒時，每筆成交 60 秒後走勢 −0.58 → −0.28 tick；延遲越短越好。

資料（都是現成的，不佔新的富邦連線額度）：
  委託／成交：cache/tmf_channel/tick_seconds/futopt_fill_events_{日}.jsonl（futopt-fill-listener 寫；含手動單）
  買賣簿：cache/{root}_books/{root}_books_{日}.jsonl（futopt-books-collect-* 寫）
範圍：config/taifex_stock_futures_map.json 內的個股期貨，且該商品有買賣簿檔；其餘（指數期貨等）一律略過並記錄。

安全（預設全關，fail-closed）：
  1. 開關檔 data/order/cancel_guard_switch.json：enabled=false → 完全不動作；
     enabled=true、auto_cancel=false → 只通知；兩者皆 true 才可能撤單。
  2. .env ORDER_CANCEL_GUARD_DRY_RUN（預設 1）：=1 時即使開關全開也只記錄『會撤』，不打券商。
  3. 實際撤單走 fubon_futopt_orders.cancel_futopt_order → live_submit_guard（需 ORDER_MASTER_ENABLED=1）。
  4. 只呼叫 cancel；本模組沒有任何下單／改價程式。每日撤單上限、每張單最多嘗試一次、只處理 apply_to 內的委託類型（預設新倉＋平倉；
     平倉單被撤後部位暫無出場單，通知會註明。不想撤平倉單：cancel_guard_switch.py types New）。
  5. 收集器 30 秒內完全沒寫任何買賣簿檔（死了）→ 不撤單（只告警）；觸發時以最新一筆買賣簿為準，行情已回來就不動作。
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from stock_db import DATA_DIR

TZ = ZoneInfo("Asia/Taipei")
ORDER_DIR = DATA_DIR / "order"
CACHE_DIR = DATA_DIR.parent / "cache"
SWITCH_PATH = ORDER_DIR / "cancel_guard_switch.json"
STALE_FEED_SEC = 30.0
_MAP_PATH = Path(__file__).resolve().parents[2] / "config" / "taifex_stock_futures_map.json"
_ROOT_OVERRIDE = {"SFF": "sf"}          # 2383 台光電：sff 目錄是舊命名殘留，現行是 sf

DEFAULT_SWITCH: dict[str, Any] = {
    "enabled": False,                    # 總開關：False＝完全不動作
    "auto_cancel": False,                # False＝只通知；True＝才可能撤單（仍受 .env DRY_RUN 與 ORDER_MASTER_ENABLED 約束）
    "against_ticks": 1.0,
    "apply_to": ["New", "Close"],        # 新倉與平倉委託都監控（2026-10-21 使用者要求）；撤掉平倉單時部位會暫時沒有出場單，通知內會註明
    "symbols_allow": [],                 # 空＝所有有買賣簿的個股期貨
    "max_cancels_per_day": 20,
}


def tick_size(price: float) -> float:
    """個股期貨最小跳動點。2026-10-20 以 42 檔買賣簿實測：50–100→0.1、100–500→0.5、500–2500→1、≥2500→5；
    <50 的級距沒有樣本，採交易所標準（<10→0.01、10–50→0.05）。"""
    return 0.01 if price < 10 else 0.05 if price < 50 else 0.1 if price < 100 else 0.5 if price < 500 else 1.0 if price < 2500 else 5.0


def load_switch(path: Path | None = None) -> dict[str, Any]:
    out = dict(DEFAULT_SWITCH)
    try:
        d = json.loads((path or SWITCH_PATH).read_text(encoding="utf-8"))
        if isinstance(d, dict):
            out.update({k: d[k] for k in DEFAULT_SWITCH if k in d})
    except Exception:  # noqa: BLE001 — 檔案缺失／壞掉 → 預設（全關）
        return dict(DEFAULT_SWITCH)
    out["enabled"] = out["enabled"] is True
    out["auto_cancel"] = out["auto_cancel"] is True
    return out


def env_dry_run() -> bool:
    return str(os.environ.get("ORDER_CANCEL_GUARD_DRY_RUN", "1")).strip().lower() not in ("0", "false", "no", "off")


def mode_of(sw: dict[str, Any], dry: bool) -> str:
    """off / alert / dry_cancel / live_cancel"""
    if not sw["enabled"]:
        return "off"
    if not sw["auto_cancel"]:
        return "alert"
    return "dry_cancel" if dry else "live_cancel"


_codes: dict[str, int] | None = None


def stock_future_codes() -> dict[str, int]:
    """個股期貨商品代碼（如 LEF、CDF）→ 每口股數。"""
    global _codes
    if _codes is None:
        rows = json.loads(_MAP_PATH.read_text(encoding="utf-8"))["rows"]
        _codes = {r["futures_id"]: int(str(r.get("contract_size") or "2000").replace(",", "")) for r in rows if r.get("is_future")}
    return _codes


def product_of(symbol: str) -> str:
    """富邦期貨商品代號 → 商品代碼。FILEF→LEF；若帶月份碼（LEFJ6）一併去掉。"""
    s = symbol[2:] if symbol.startswith("FI") else symbol
    return re.sub(r"[A-Z]\d$", "", s) if len(s) > 3 and re.search(r"[A-Z]\d$", s) else s


def root_of(code: str, cache_dir: Path = CACHE_DIR) -> str | None:
    if code in _ROOT_OVERRIDE:
        return _ROOT_OVERRIDE[code]
    for c in (code.lower(), code[:-1].lower() if len(code) == 3 and code.endswith("F") else None):
        if c and (cache_dir / f"{c}_books").is_dir():
            return c
    return None


def books_live(root: str, cache_dir: Path = CACHE_DIR, max_age_sec: float = 3 * 86400) -> bool:
    """該商品的買賣簿目前還有在收（目錄內最新檔 3 天內有更新）。曾經收過、現已停收的商品（例 CKF、DQF）不算。"""
    try:
        newest = max((f.stat().st_mtime for f in (cache_dir / f"{root}_books").glob(f"{root}_books_*.jsonl")), default=0.0)
    except OSError:
        return False
    return (time.time() - newest) <= max_age_sec


def rec_will_cancel(mode: str, sw: dict[str, Any]) -> bool:
    return mode in ("dry_cancel", "live_cancel") and sw.get("auto_cancel") is True


def coverage(cache_dir: Path = CACHE_DIR) -> list[dict[str, Any]]:
    """目前可監控的個股期貨（在期交所個股期貨清單內，且買賣簿檔仍在更新）。"""
    names = {r["futures_id"]: r["name"] for r in json.loads(_MAP_PATH.read_text(encoding="utf-8"))["rows"] if r.get("is_future")}
    out = []
    for code, shares in sorted(stock_future_codes().items()):
        root = root_of(code, cache_dir)
        if root and books_live(root, cache_dir):
            out.append({"code": code, "name": names.get(code, ""), "shares": shares})
    return out


def against_ticks(sgn: int, ref_mid: float, mid: float, tick: float) -> float:
    """往不利方向的 tick 數（正＝不利）。買單 sgn=+1：中價下跌不利；賣單 sgn=-1：中價上漲不利。"""
    return -sgn * (mid - ref_mid) / tick


@dataclass
class OpenOrder:
    order_no: str
    code: str
    root: str
    sgn: int
    price: float
    otype: str                      # New / Close
    ts: float                       # 本機收到委託回報的時間
    ref_mid: float | None = None
    tick: float = 0.0
    state: str = "open"             # open / triggered / cancelled / done
    note: str = ""
    sym: str = ""                   # 掛單當下買賣簿的完整合約代碼（如 FRFJ6）


class Broker(Protocol):
    def cancel(self, order_no: str) -> dict[str, Any]: ...


class DryBroker:
    def cancel(self, order_no: str) -> dict[str, Any]:
        return {"ok": True, "status": "dry_run_cancel", "order_no": order_no}


class FubonBroker:
    """唯一會碰券商的地方，只有 cancel。登入延後到第一次真的要撤單。"""

    def __init__(self) -> None:
        self._session: Any = None

    def cancel(self, order_no: str) -> dict[str, Any]:
        from order.fubon_futopt_orders import cancel_futopt_order, get_futopt_order_results
        from order.fubon_session import connect_fubon
        from order.live_submit_guard import today_session_date

        if self._session is None:
            self._session = connect_fubon()
        for res in get_futopt_order_results(self._session):
            if str(getattr(res, "order_no", "")) == order_no:
                out = cancel_futopt_order(self._session, res, dry_run=False, session_date=today_session_date())
                return {"ok": True, "status": out.get("status"), "order_no": order_no}
        return {"ok": False, "status": "order_not_found_open", "order_no": order_no}


@dataclass
class Tail:
    path: Path
    off: int = 0

    def lines(self) -> list[str]:
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
        return [x.decode("utf-8", "ignore") for x in chunk[:nl].split(b"\n") if x]


@dataclass
class Guard:
    broker: Broker
    cache_dir: Path = CACHE_DIR
    order_dir: Path = ORDER_DIR
    clock: Any = time.time
    day: str = ""
    orders: dict[str, OpenOrder] = field(default_factory=dict)
    cancels_today: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)
    feed_alive: Any = None          # 測試用：(now)->bool；預設以今天買賣簿檔的寫入時間判斷

    def __post_init__(self) -> None:
        self._ev: Tail | None = None
        self._bk: dict[str, Tail] = {}
        self._hist: dict[str, deque] = {}
        self._last_wall: dict[str, float] = {}
        self._last_any_book = 0.0
        self._alive_t = 0.0
        self._alive_v = False

    # ---------- 日期切換 ----------
    def set_day(self, day: str) -> None:
        if day == self.day:
            return
        self.day = day
        self.orders.clear(); self._bk.clear(); self._hist.clear(); self._last_wall.clear()
        self.cancels_today = 0
        self._ev = Tail(self.cache_dir / "tmf_channel" / "tick_seconds" / f"futopt_fill_events_{day}.jsonl")

    # ---------- 委託事件 ----------
    def ingest_events(self) -> None:
        assert self._ev is not None
        for line in self._ev.lines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            p = r.get("payload")
            if not isinstance(p, dict) or not p.get("order_no"):
                continue
            kind = str(r.get("kind", "")).split("[")[0]
            no = p["order_no"]
            ts = datetime.fromisoformat(r["ts"]).timestamp()
            if kind == "futopt_filled":
                if no in self.orders:
                    self.orders[no].state = "done"
            elif kind == "order_futopt_changed" and p.get("status") == 30:
                if no in self.orders:
                    self.orders[no].state = "cancelled"
            elif kind in ("futopt_order", "order_futopt_changed") and p.get("status") == 10:
                code = product_of(str(p.get("symbol", "")))
                if no in self.orders:                    # 改價：以新價與當下中價重新起算
                    o = self.orders[no]
                    if p.get("after_price") and float(p["after_price"]) != o.price:
                        o.price, o.ts, o.ref_mid, o.state = float(p["after_price"]), ts, None, "open"
                    continue
                if "Limit" not in str(p.get("price_type", "")):
                    continue
                if code not in stock_future_codes():
                    self._note("skip_not_stock_future", order_no=no, symbol=p.get("symbol"))
                    continue
                root = root_of(code, self.cache_dir)
                if not root or not books_live(root, self.cache_dir):
                    self._note("skip_no_books", order_no=no, symbol=p.get("symbol"))
                    continue
                self.orders[no] = OpenOrder(
                    order_no=no, code=code, root=root, sgn=1 if "Buy" in str(p.get("buy_sell")) else -1,
                    price=float(p.get("after_price") or p["price"]), otype=str(p.get("order_type", "")).split(".")[-1], ts=ts)

    # ---------- 買賣簿 ----------
    def _poll_root(self, root: str) -> list[tuple[float, float, float, str]]:
        tl = self._bk.get(root)
        if tl is None:
            tl = self._bk[root] = Tail(self.cache_dir / f"{root}_books" / f"{root}_books_{self.day}.jsonl")
            self._hist[root] = deque(maxlen=20000)
        new = []
        for line in tl.lines():
            if '"day"' not in line or '"stale": true' in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b, a = r.get("bids"), r.get("asks")
            if r.get("session") != "day" or r.get("stale") or not b or not a or b[0]["price"] >= a[0]["price"]:
                continue
            wall = datetime.fromisoformat(r["ts"]).timestamp()      # 本機收到時間，與委託回報同一把時鐘
            rec = (wall, float(b[0]["price"]), float(a[0]["price"]), str(r.get("symbol", "")))
            self._hist[root].append(rec); new.append(rec)
            self._last_wall[root] = wall
        if new:
            self._last_any_book = max(self._last_any_book, new[-1][0])
        return new

    def _ref_mid(self, root: str, ts: float) -> float | None:
        h = self._hist.get(root)
        best = None
        for wall, bid, ask, _sym in (h or ()):
            if wall <= ts:
                best = (bid + ask) / 2
            else:
                break
        return best

    # ---------- 收集器是否活著 ----------
    def _collector_alive(self, now: float) -> bool:
        """今天任一個買賣簿檔 30 秒內有寫入＝收集器活著。個別商品安靜（沒有報價變動）不算收集器死掉。"""
        if self.feed_alive is not None:
            return bool(self.feed_alive(now))
        if now - self._alive_t >= 2.0:
            newest = 0.0
            for f in self.cache_dir.glob(f"*_books/*_books_{self.day}.jsonl"):
                try:
                    newest = max(newest, f.stat().st_mtime)
                except OSError:
                    pass
            self._alive_v, self._alive_t = (time.time() - newest) <= STALE_FEED_SEC, now
        return self._alive_v

    # ---------- 規則 ----------
    def step(self, sw: dict[str, Any], dry: bool, now: float | None = None) -> list[dict[str, Any]]:
        """讀新資料、評估規則、依模式動作。回傳這一步產生的紀錄。
        關閉（off）時仍會讀委託與買賣簿（只讀、供頁面顯示），但不評估、不通知、不撤單。"""
        now = self.clock() if now is None else now
        self.log = []
        self.ingest_events()
        mode = mode_of(sw, dry)
        active = {o.root for o in self.orders.values() if o.state in ("open", "triggered")}
        for root in active:
            new = self._poll_root(root)
            for o in [x for x in self.orders.values() if x.root == root and x.state == "open" and x.ref_mid is None]:
                self._init_ref(root, o)                              # 關閉時也要算參考中價（頁面顯示用）
            if mode == "off":
                continue
            h = self._hist.get(root)
            check = new if new else ([h[-1]] if h else [])          # 剛打開開關時，也要用最新一筆買賣簿檢查既有的單
            for o in [x for x in self.orders.values() if x.root == root and x.state == "open"]:
                if o.otype not in sw["apply_to"] or (sw["symbols_allow"] and o.code not in sw["symbols_allow"]):
                    continue
                if o.ref_mid is None:
                    continue
                for wall, bid, ask, sym in check:
                    if wall <= o.ts or (o.sym and sym != o.sym):      # 換月（結算日）後的紀錄是另一個合約，不能和掛單時的中價比
                        continue
                    ag = against_ticks(o.sgn, o.ref_mid, (bid + ask) / 2, o.tick)
                    if ag >= sw["against_ticks"] - 1e-9:
                        lw, lb, la, ls = h[-1]                        # 以最新一筆為準：若行情已回來就不動作
                        if ls == o.sym or not o.sym:
                            if against_ticks(o.sgn, o.ref_mid, (lb + la) / 2, o.tick) < sw["against_ticks"] - 1e-9:
                                continue
                        self._trigger(o, mode, sw, ag, wall, now)
                        break
        return self.log

    def _init_ref(self, root: str, o: OpenOrder) -> None:
        """記下掛單當下的中價與合約；已可立即成交（跨價差）的單不是被動單，標記略過。"""
        o.ref_mid = self._ref_mid(root, o.ts)
        if o.ref_mid is None:
            return
        o.tick = tick_size(o.ref_mid)
        hist = [x for x in self._hist[root] if x[0] <= o.ts]
        o.sym = hist[-1][3] if hist else ""
        if hist and ((o.sgn > 0 and o.price >= hist[-1][2]) or (o.sgn < 0 and o.price <= hist[-1][1])):
            o.state, o.note = "done", "marketable"

    def _trigger(self, o: OpenOrder, mode: str, sw: dict[str, Any], ag: float, book_wall: float, now: float) -> None:
        o.state = "triggered"
        rec = {"event": "trigger", "order_no": o.order_no, "code": o.code, "side": "買" if o.sgn > 0 else "賣", "price": o.price,
               "type": o.otype, "exit_unprotected": o.otype == "Close" and rec_will_cancel(mode, sw), "ref_mid": o.ref_mid,
               "against_ticks": round(ag, 2), "mode": mode, "detect_lag_s": round(now - book_wall, 3),
               "ts": datetime.fromtimestamp(now, TZ).isoformat(timespec="milliseconds")}
        feed_ok = self._collector_alive(now)
        if mode == "alert" or not feed_ok:
            rec["action"] = "alert" if feed_ok else "alert_feed_stale_no_cancel"
        elif self.cancels_today >= int(sw["max_cancels_per_day"]):
            rec["action"] = "alert_daily_cap_reached"
        elif mode == "dry_cancel":
            rec["action"] = "would_cancel"
            rec["result"] = DryBroker().cancel(o.order_no)
        else:
            t0 = self.clock()
            try:
                rec["result"] = self.broker.cancel(o.order_no)
                rec["action"] = "cancelled" if rec["result"].get("ok") else "cancel_failed"
            except Exception as e:  # noqa: BLE001
                rec["action"], rec["error"] = "cancel_error", repr(e)[:300]
            rec["cancel_roundtrip_s"] = round(self.clock() - t0, 3)
            self.cancels_today += 1                       # 失敗也算一次嘗試，避免連環重試
        self.log.append(rec)

    def _note(self, event: str, **kw: Any) -> None:
        self.log.append({"event": event, **kw})

    def snapshot(self) -> list[dict[str, Any]]:
        """給頁面顯示：目前監控中的委託與離觸發還差多少。"""
        out = []
        for o in self.orders.values():
            if o.state not in ("open", "triggered"):
                continue
            h = self._hist.get(o.root)
            mid = (h[-1][1] + h[-1][2]) / 2 if h else None
            ag = against_ticks(o.sgn, o.ref_mid, mid, o.tick) if (mid is not None and o.ref_mid is not None and o.tick) else None
            out.append({"order_no": o.order_no, "code": o.code, "side": "買" if o.sgn > 0 else "賣", "price": o.price, "type": o.otype,
                        "ref_mid": o.ref_mid, "mid": mid, "tick": o.tick, "against_ticks": None if ag is None else round(ag, 2), "state": o.state,
                        "age_s": round(self.clock() - o.ts, 1), "book_age_s": None if not h else round(self.clock() - h[-1][0], 1)})
        return sorted(out, key=lambda r: r["age_s"])


def save_switch(updates: dict[str, Any], by: str, path: Path | None = None) -> dict[str, Any]:
    """寫開關檔（原子寫入）。只接受 DEFAULT_SWITCH 內的欄位。"""
    sw = load_switch(path)
    for k, v in updates.items():
        if k in DEFAULT_SWITCH:
            sw[k] = v
    d = {**{k: sw[k] for k in DEFAULT_SWITCH}, "updated_at": datetime.now(TZ).isoformat(timespec="seconds"), "by": by}
    p = path or SWITCH_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return load_switch(p)
