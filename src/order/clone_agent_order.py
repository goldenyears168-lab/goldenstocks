"""複製人代理人 · 實單下單層(階段 B:只送「新倉買進限價單」,出場一律人工)。

資料契約:讀 clone_agent 的候選卡片(candidates_{day}.jsonl)與人的裁決(decisions_*.jsonl),
寫 data/order/clone_agent_ledger_{day}.json;開關由儀表板寫 clone_agent_switch.json(預設開、檔壞=關)。
本模組與 HTML/儀表板互不 import;儀表板只讀寫上述 JSON。

安全護欄(任一不過就不送,並把原因寫進帳本):
  開關 · 當日未熔斷 · 卡片 verdict=ok 且為新倉買進 · 人未略過 · 卡片年齡 ≤ 120 秒 · 價格對齊跳動單位 ·
  口數 ≤ MAX_LOT · 當日張數上限 · 同標的沒有未了結委託 · 保證金上限(預設 147 萬,先到先得) ·
  券商端即時核對(送單前強制查一次,平時每 5 分鐘一次):同根標的在券商端沒有任何持倉/委託(不分來源:手動、其他袖)、
  券商可用保證金 ≥ 這筆所需;查不到就不送(fail-closed)。持倉釋放以券商端「已無部位」為準,不再用固定 60 分鐘假設。
  冪等:同一 intent_id 只會有一筆帳本紀錄;送單結果不明(網路例外)絕不重送。
  連續 3 次失敗 → 當日熔斷;券商回「額度/資力」類帳戶級錯誤 → 該筆失敗並冷卻 5 分鐘(額度可能被別的 sleeve 釋放,之後新卡片會再試)。
AI 撤單(2026-10-06 使用者授權):AI 監督員只能 append 請求到 cancel_requests_{day}.jsonl,本層驗證(來源/觸發 C1-C3/年齡≤120s/
帳本內仍在掛單且 order_no 相符/當日≤5 次)後才呼叫券商撤單;只撤自家委託、不改價、不出場、不新單。
實彈另需 ORDER_MASTER_ENABLED 與 ORDER_CLONE_AGENT_DRY_RUN=0(預設 dry-run,不登入券商)。
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from clone_agent.approval import load_candidates, load_decisions
from order.clone_agent_exit import evaluate_exit, last_bid, load_policy as load_exit_policy, read_exhaustion_events
from clone_agent.order_switch import (
    LedgerUnreadable, atomic_write, ledger_path, read_cancel_requests, read_ledger, read_switch,
)

TZ = ZoneInfo("Asia/Taipei")
#: 2026-10-06(同日稍早在儀表板 `biglot/futures_pnl.py` 抓到同一種 fail-open:富邦 API
#: 卡住、完全不丟例外也不回應,把呼叫端的執行緒整個卡死)——`broker.fetch_account()`
#: 底下是同一套 SDK,沒有理由這裡不會發生同樣的事,而這裡卡住的代價更高:整個 `tick()`
#: 停擺等於保證金對帳/部位釋放/AI 撤單處理全部停擺,KeepAlive 不會重啟(程式沒有當掉,
#: 只是邏輯卡住)。用 thread 包一層強制逾時,逾時視同既有的例外處理路徑(acct 全部設
#: None,下面的程式碼本來就會對應正確處理成「這次查不到」)。刻意不用模組層級共用的
#: ThreadPoolExecutor——理由同 futures_pnl.py:固定 1-worker 的共用池會被永久卡住的
#: 任務佔死,之後永遠排不到;改成每次呼叫都開一支用完即丟的。
FETCH_ACCOUNT_TIMEOUT = 20.0
SENT = ("working", "partial", "filled", "closed", "cancelled", "failed", "unknown_submit", "dry_run")
OPEN = ("working", "partial", "unknown_submit")
HELD = ("filled", "dry_run")
WORKING_STATES = ("working", "partial")
ACCOUNT_COOLDOWN_SEC = 300
ACCOUNT_LEVEL_WORDS = ("額度", "資力", "保證金", "margin", "insufficient")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _env_hm(name: str, default: str) -> str:
    v = str(os.environ.get(name) or "").strip()
    try:
        datetime.strptime(v, "%H:%M")
        return v
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    dry_run: bool = True
    margin_cap: float = 1_470_000
    max_lot: int = 1
    card_max_age_sec: float = 120
    daily_max_orders: int = 15
    fail_halt: int = 3
    window: tuple[str, str] = ("10:00", "12:00")
    hold_release_min: float = 60          # 只用在 dry-run 假部位;實倉改以券商端確認為準
    snapshot_sec: float = 300             # 券商端持倉/委託/可用保證金 定期核對間隔
    snapshot_busy_sec: float = 20         # 有未了結委託時加密(盡快看到成交/撤單)
    snapshot_max_age_sec: float = 420     # 超過就視為沒核對過 → 不送新單
    release_grace_sec: float = 120        # 成交後多久才允許「券商端查無部位」→ 釋放
    cancel_max_age_sec: float = 120       # AI 撤單請求超過這個年齡就作廢(狀況已變)
    daily_max_cancels: int = 5            # 當日 AI 撤單上限
    exit_enabled: bool = False            # 小哥出場(反向竭盡/5%/13:15)是否真的送賣單;False=只在帳本記 exit.signal。env ORDER_CLONE_AGENT_EXIT_ENABLED
    min_stars: int = 4                   # 只送嚴格條件 ★ 數 >= 此值的卡(SSOT=rules entry.live_min_stars;卡片沒帶 stars 視為 0)


def _rules_min_stars() -> int:
    try:
        from clone_agent.rules_version import load_rules
        return int(load_rules()["entry"]["live_min_stars"])
    except Exception:
        return 4


def load_config() -> Config:
    dry = str(os.environ.get("ORDER_CLONE_AGENT_DRY_RUN", "1")).strip().lower() not in ("0", "false", "no", "off")
    return Config(
        dry_run=dry,
        margin_cap=_env_float("ORDER_CLONE_AGENT_MARGIN_CAP", 1_470_000),
        max_lot=int(_env_float("ORDER_CLONE_AGENT_MAX_LOT", 1)),
        daily_max_orders=int(_env_float("ORDER_CLONE_AGENT_DAILY_MAX", 15)),
        window=(_env_hm("ORDER_CLONE_AGENT_START", "10:00"), _env_hm("ORDER_CLONE_AGENT_END", "12:00")),
        exit_enabled=str(os.environ.get("ORDER_CLONE_AGENT_EXIT_ENABLED", "0")).strip().lower() in ("1", "true", "yes", "on"),
        min_stars=int(_env_float("ORDER_CLONE_AGENT_MIN_STARS", _rules_min_stars())),
    )


class BrokerRejected(RuntimeError):
    """券商(或本地護欄)明確拒單 —— 確定沒有委託成立,與「結果不明」區分。"""


class Broker(Protocol):
    def resolve_contract(self, root: str) -> str | None: ...
    def place_buy(self, contract: str, lot: int, price_text: str) -> dict[str, Any]: ...
    def fetch_results(self) -> list[dict[str, Any]]: ...
    def cancel_order(self, order_no: str) -> dict[str, Any]: ...
    def place_sell(self, contract: str, lot: int, price_text: str) -> dict[str, Any]: ...


def tick_size(px: float) -> float:
    for lim, t in ((10, .01), (50, .05), (100, .1), (500, .5)):
        if px < lim:
            return t
    return 1.0


def tick_aligned(px: float) -> bool:
    t = tick_size(px)
    q = px / t
    return abs(q - round(q)) < 1e-6


def price_text(px: float) -> str:
    return f"{px:.2f}".rstrip("0").rstrip(".")


def root_of(card: dict[str, Any]) -> str | None:
    s = str(card.get("symbol") or "")
    r = s.split("(")[0].strip()
    return r or None


def card_epoch(card: dict[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(f"{card['day']}T{card['time']}").replace(tzinfo=TZ).timestamp()
    except (KeyError, ValueError, TypeError):
        return None


def card_margin(card: dict[str, Any]) -> float:
    m = card.get("margin")
    if isinstance(m, (int, float)) and m > 0:
        return float(m)
    return float(card.get("price") or 0) * float(card.get("shares") or 2000) * float(card.get("rate") or 0.2025) * float(card.get("lot") or 1)


def new_ledger(day: str) -> dict[str, Any]:
    return {"day": day, "halted": None, "consec_fail": 0, "entries": {}}


def load_ledger(day: str) -> dict[str, Any]:
    try:
        led = read_ledger(day)
    except LedgerUnreadable:
        # fail-closed(2026-10-06):帳本讀不出來,不能猜是不是新的一天——直接給一本新帳本
        # 但立刻熔斷,逼人工介入看壞掉的檔案,而不是悄悄重置當日計數/同標的防重複繼續送單。
        led = new_ledger(day)
        _halt(led, "ledger_unreadable", datetime.now(TZ).timestamp())
        return led
    if not led or not isinstance(led.get("entries"), dict):
        return new_ledger(day)
    led.setdefault("halted", None)
    led.setdefault("consec_fail", 0)
    return led


def save_ledger(led: dict[str, Any]) -> None:
    atomic_write(ledger_path(led["day"]), led)


def holding(e: dict[str, Any], now_ts: float, cfg: Config) -> bool:
    """實倉(filled)只有券商端確認已無部位才釋放;dry-run 假部位仍用固定時間。"""
    st = e.get("status")
    if st == "filled":
        return True
    if st == "dry_run":
        return now_ts - float(e.get("held_since") or now_ts) < cfg.hold_release_min * 60
    return False


def reserved_margin(led: dict[str, Any], now_ts: float, cfg: Config) -> float:
    tot = 0.0
    for e in led["entries"].values():
        if e.get("status") in OPEN or holding(e, now_ts, cfg):
            tot += e.get("margin", 0)
    return tot


def classify_result(status: Any, filled_lot: int, lot: int) -> str:
    s = str(status).strip().lower()
    if lot > 0 and filled_lot >= lot:
        return "filled"
    if s in ("90", "failed", "fail", "rejected"):
        return "failed"
    if s in ("30", "cancelled", "canceled", "deleted"):
        return "filled" if filled_lot > 0 else "cancelled"
    if s == "50":
        return "filled"
    return "partial" if filled_lot > 0 else "working"


def reconcile_rows(led: dict[str, Any], rows: list[dict[str, Any]], now_ts: float) -> int:
    live_open = [e for e in led["entries"].values() if e.get("status") in OPEN and not e.get("dry")]
    if not live_open:
        return 0
    changed = 0
    for e in live_open:
        ids = {str(x) for x in (e.get("order_no"), e.get("seq_no")) if x}
        if not ids:
            continue
        for r in rows:
            rid = {str(x) for x in (r.get("order_no"), r.get("seq_no")) if x}
            if not (ids & rid):
                continue
            st = classify_result(r.get("status"), int(r.get("filled_lot") or 0), int(e.get("lot") or 1))
            if st != e["status"] or int(r.get("filled_lot") or 0) != e.get("filled_lot", 0):
                e["status"], e["filled_lot"] = st, int(r.get("filled_lot") or 0)
                e["updated_at"] = datetime.fromtimestamp(now_ts, TZ).isoformat(timespec="seconds")
                if st == "filled":
                    e["held_since"] = now_ts
                changed += 1
    return changed


def sym_hit(sym: Any, root: str | None) -> bool:
    """券商帳務/委託結果的個股期貨代號是 FI+根+F(例 FIRKF,2026-10 broker_history 實證);也容錯行情式 RKFJ6。"""
    s, r = str(sym or "").upper(), str(root or "").upper()
    return bool(r) and (s.startswith("FI" + r) or s.startswith(r + "F"))


def release_flat(led: dict[str, Any], positions: list[dict[str, Any]], now_ts: float, cfg: Config) -> int:
    """券商端已查無該標的部位(你手動平倉/被別處平掉)→ 帳本釋放保證金。符號格式不認得時一律不釋放。"""
    if positions and not all(str(p.get("symbol") or "").upper().startswith("FI") for p in positions):
        return 0
    n = 0
    for e in led["entries"].values():
        if e.get("status") != "filled" or e.get("dry"):
            continue
        if now_ts - float(e.get("held_since") or now_ts) < cfg.release_grace_sec:
            continue
        if any(sym_hit(p.get("symbol"), root_of({"symbol": e.get("symbol")})) for p in positions):
            continue
        e["status"], e["reason"] = "closed", "broker_flat"
        e["updated_at"] = datetime.fromtimestamp(now_ts, TZ).isoformat(timespec="seconds")
        n += 1
    return n


def refresh_snapshot(led: dict[str, Any], broker: Broker, cfg: Config, now_ts: float, *, force: bool = False) -> bool:
    """向券商取持倉/委託/可用保證金,寫進 led['broker'];回傳這次是否真的去查了。
    對帳(委託狀態→帳本)與釋放(查無部位→closed)都從這裡走,所以一次查詢同時更新兩件事。"""
    fn = getattr(broker, "fetch_account", None)
    if fn is None:
        return False
    snap = led.get("broker") or {}
    busy = any(e.get("status") in OPEN and not e.get("dry") for e in led["entries"].values())
    gap = cfg.snapshot_busy_sec if busy else cfg.snapshot_sec
    age = now_ts - float(snap.get("ts") or 0)
    if not (force and age >= 5) and age < gap:
        return False
    try:
        ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fetch-account")
        try:
            future = ex.submit(fn)
            try:
                acct = future.result(timeout=FETCH_ACCOUNT_TIMEOUT) or {}
            except _FutureTimeoutError as texc:
                raise TimeoutError(f"fetch_account exceeded {FETCH_ACCOUNT_TIMEOUT:g}s, abandoning wait") from texc
        finally:
            ex.shutdown(wait=False)
    except Exception as exc:  # noqa: BLE001
        acct = {"orders": None, "positions": None, "available_margin": None, "errors": {"all": f"{type(exc).__name__}:{str(exc)[:100]}"}}
    orders, positions, avail = acct.get("orders"), acct.get("positions"), acct.get("available_margin")
    mine = {str(x) for e in led["entries"].values() for x in (e.get("order_no"), e.get("seq_no")) if x}
    open_orders = []
    if orders is not None:
        reconcile_rows(led, orders, now_ts)
        for r in orders:
            st = classify_result(r.get("status"), int(r.get("filled_lot") or 0), int(r.get("lot") or 1))
            if st in WORKING_STATES:
                rid = {str(x) for x in (r.get("order_no"), r.get("seq_no")) if x}
                open_orders.append({**{k: r.get(k) for k in ("order_no", "symbol", "side", "price", "lot", "filled_lot", "status")},
                                    "state": st, "ours": bool(rid & mine)})
    released = release_flat(led, positions, now_ts, cfg) if positions is not None else 0
    led["broker"] = {
        "ts": now_ts, "at": datetime.fromtimestamp(now_ts, TZ).isoformat(timespec="seconds"),
        "orders_ok": orders is not None, "positions_ok": positions is not None, "margin_ok": avail is not None,
        "errors": acct.get("errors") or {}, "open_orders": open_orders,
        "positions": positions or [], "available_margin": avail, "released": released,
    }
    save_ledger(led)
    return True


# 第三道閘(2026-10-06):券商「交易額度超過資(財)力證明額度」[8481329] 是全帳戶口徑:
# Σ(所有持倉原始保證金,含手動倉) + 未成交委託 + 新單 ≤ 資力證明額度。自家帳本只算自己的單、券商
# available_margin 是權益口徑,兩者都量不到這個額度(9 筆送單重建 9/9 吻合,見 scratch/clone_agent_quota)。
# 原始%以 TAIFEX 表為準;全新(GUF)表上 40.5% 但券商實際扣約 20.25%(10:54 通過、重建吻合),
# 以經驗值覆寫;之後如發現其他標的表與券商不符,加進這張表。
QUOTA_RATE_OVERRIDE = {"GUF": 0.2025}


def _quota_cache_dir() -> Path:
    root = os.environ.get("GOLDENSTOCKS_DATA_DIR")
    return (Path(root) if root else Path(__file__).resolve().parents[2]) / "data" / "cache"


def load_quota_tables() -> tuple[dict[str, float], dict[str, int]]:
    """回 (商品代號如 'HBF' → 原始保證金率, 根代號如 'HB' → 契約股數)。讀不到就丟例外(呼叫端 fail-closed)。"""
    d = _quota_cache_dir()
    with open(d / "taifex_stock_futures_margin_rates.json", encoding="utf-8") as f:
        rates = {k: float(v["原始%"]) / 100 for k, v in json.load(f).items() if v.get("原始%")}
    with open(d / "taifex_stock_futures_specs.json", encoding="utf-8") as f:
        shares = {k: int(v["contract_shares"]) for k, v in json.load(f).items() if v.get("contract_shares")}
    rates.update(QUOTA_RATE_OVERRIDE)
    return rates, shares


def _sym_product(sym: Any) -> str | None:
    s = str(sym or "").upper()
    return s[2:] if s.startswith("FI") and s.endswith("F") and len(s) >= 5 else None


def _lot_margin(product: str | None, root: str | None, price: Any, lots: Any,
                rates: dict[str, float], shares: dict[str, int]) -> float:
    rate = rates.get(product or "")
    if rate is None or not isinstance(price, (int, float)) or price <= 0:
        raise ValueError(f"unpriced:{product}")
    return float(price) * shares.get(root or "", 2000) * rate * int(lots or 0)


def quota_exposure(card: dict[str, Any], led: dict[str, Any], snap: dict[str, Any]) -> float:
    """全帳戶口徑的占用額(含這張新單)。非 FI..F 的商品(如指數期貨)這裡不建模,由券商端把關。
    算不出來(查無原始%/價格)丟 ValueError,呼叫端擋單。"""
    rates, shares = load_quota_tables()
    tot = 0.0
    for p in snap.get("positions") or []:
        prod = _sym_product(p.get("symbol"))
        if prod:
            tot += _lot_margin(prod, prod[:-1], p.get("price"), p.get("lots"), rates, shares)
    for o in snap.get("open_orders") or []:
        prod = _sym_product(o.get("symbol"))
        if prod:
            rest = max(int(o.get("lot") or 0) - int(o.get("filled_lot") or 0), 0)
            tot += _lot_margin(prod, prod[:-1], o.get("price"), rest, rates, shares)
    snap_ts = float(snap.get("ts") or 0)
    seen_roots = {(_sym_product(x.get("symbol")) or "")[:-1] for x in list(snap.get("positions") or []) + list(snap.get("open_orders") or [])}
    for e in led["entries"].values():
        if e.get("dry") or e.get("status") not in OPEN:
            continue
        eroot = root_of({"symbol": e.get("symbol")})
        # 快照之後才送出、或結果不明的單,券商快照還看不到 → 補算,否則連送兩單會各自通過
        if float(e.get("sent_at") or 0) > snap_ts or (e.get("status") == "unknown_submit" and eroot not in seen_roots):
            tot += _lot_margin((eroot or "") + "F", eroot, e.get("price"), e.get("lot"), rates, shares)
    root = root_of(card)
    tot += _lot_margin((root or "") + "F", root, card.get("price"), card.get("lot") or 1, rates,
                       {**shares, **({root: int(card["shares"])} if root and card.get("shares") else {})})
    return tot


def broker_gate(card: dict[str, Any], led: dict[str, Any], cfg: Config, now_ts: float) -> str | None:
    """送單前核對:券商端同根標的有任何持倉/委託(含手動、其他袖)就不送;可用保證金不足不送;查不到不送。"""
    snap = led.get("broker")
    if not snap or now_ts - float(snap.get("ts") or 0) > cfg.snapshot_max_age_sec:
        return "broker_check_stale"
    if not (snap.get("orders_ok") and snap.get("positions_ok")):
        return "broker_check_failed:" + ",".join(f"{k}" for k in (snap.get("errors") or {"unknown": 1}))[:60]
    root = root_of(card)
    if any(sym_hit(p.get("symbol"), root) for p in snap.get("positions") or []):
        return "broker_position_same_root"
    if any(sym_hit(o.get("symbol"), root) for o in snap.get("open_orders") or []):
        return "broker_order_same_root"
    if not snap.get("margin_ok"):
        # fail-closed(2026-10-06):可用保證金這一項單獨查詢失敗時(orders/positions 仍 ok),
        # 舊版只是讓這個 if 短路成 False、整個不攔——等於用本地 margin_cap(不知道其他袖
        # 共用同一個券商保證金池)當唯一防線。查不到真實可用保證金就不送,跟其他三項同標準。
        return "broker_margin_unknown"
    am = snap.get("available_margin")
    if isinstance(am, (int, float)) and am < card_margin(card):
        return f"broker_margin_short:{int(am)}"
    try:
        exposure = quota_exposure(card, led, snap)
    except (OSError, ValueError, KeyError) as exc:
        return f"quota_unknown:{type(exc).__name__}:{str(exc)[:40]}"
    if exposure > cfg.margin_cap:
        return f"quota_over:{int(exposure)}>{int(cfg.margin_cap)}"
    return None


def carry_over(led: dict[str, Any], day: str) -> None:
    """留倉到隔日:把前一份帳本裡仍持有的實倉帶進今天,保證金與同標的防重複才不會一過午夜就歸零。

    2026-10-06 修正:往回找的某一天帳本讀不出來(壞掉,不是沒有)不能跳過裝作沒事——
    跳過去可能剛好漏掉那天真的有留倉,同標的防重複/保證金占用就會悄悄失效。遇到讀不出來
    直接熔斷等人工看,不要猜著往更早的日子找。"""
    from datetime import date, timedelta
    led["carried_checked"] = True
    d = date.fromisoformat(day)
    for i in range(1, 8):
        try:
            prev = read_ledger((d - timedelta(days=i)).isoformat())
        except LedgerUnreadable as exc:
            _halt(led, f"carry_over_ledger_unreadable:{str(exc)[:160]}", datetime.now(TZ).timestamp())
            return
        if not prev:
            continue
        for iid, e in (prev.get("entries") or {}).items():
            if e.get("status") == "filled" and not e.get("dry") and iid not in led["entries"]:
                led["entries"][iid] = {**e, "carried_from": prev.get("day")}
        return


def _entry(card: dict[str, Any], status: str, reason: str, now_ts: float) -> dict[str, Any]:
    return {
        "intent_id": card["intent_id"], "sid": card.get("sid"), "uname": card.get("uname"), "symbol": card.get("symbol"),
        "price": card.get("price"), "lot": int(card.get("lot") or 1), "margin": card_margin(card), "card_time": card.get("time"),
        "status": status, "reason": reason, "order_no": None, "seq_no": None, "contract": None, "filled_lot": 0,
        "dry": False, "sent_at": None, "held_since": None,
        "updated_at": datetime.fromtimestamp(now_ts, TZ).isoformat(timespec="seconds"),
    }


def _halt(led: dict[str, Any], reason: str, now_ts: float) -> None:
    led["halted"] = {"reason": reason, "at": datetime.fromtimestamp(now_ts, TZ).isoformat(timespec="seconds")}


def _check_card(card: dict[str, Any], led: dict[str, Any], cfg: Config, now_ts: float, decisions: dict[str, Any]) -> str | None:
    if card.get("verdict") != "ok":
        return "verdict_blocked"
    if card.get("side") != "Buy" or card.get("action") != "新倉買進":
        return "not_new_buy"
    stars = card.get("stars")
    if not isinstance(stars, int) or stars < cfg.min_stars:
        return f"stars_below_{cfg.min_stars}"
    dec = decisions.get(card["intent_id"])
    if dec and dec.get("decision") == "skip":
        return "user_skipped"
    ts = card_epoch(card)
    if ts is None:
        return "bad_card_time"
    age = now_ts - ts
    if age > cfg.card_max_age_sec:
        return f"stale_card_{int(age)}s"
    if age < -5:
        return "card_from_future"
    hm = datetime.fromtimestamp(now_ts, TZ).strftime("%H:%M")
    if not (cfg.window[0] <= hm <= cfg.window[1]):
        return "outside_window"
    px = card.get("price")
    if not isinstance(px, (int, float)) or px <= 0 or not tick_aligned(float(px)):
        return "price_not_tick_aligned"
    if int(card.get("lot") or 1) > cfg.max_lot or int(card.get("lot") or 1) < 1:
        return "lot_over_max"
    if not root_of(card):
        return "no_root"
    ents = led["entries"].values()
    if sum(1 for e in ents if e.get("status") in SENT and not e.get("carried_from")) >= cfg.daily_max_orders:
        return "daily_cap"
    sid = card.get("sid")
    if any(e.get("sid") == sid and (e.get("status") in OPEN or holding(e, now_ts, cfg)) for e in ents):
        return "same_sid_active"
    if reserved_margin(led, now_ts, cfg) + card_margin(card) > cfg.margin_cap:
        return "margin_cap"
    try:
        cap = int(load_exit_policy()["max_concurrent_positions"])
    except Exception:  # noqa: BLE001  政策檔壞掉 → 不送新單(fail-closed)
        return "exit_policy_unreadable"
    if sum(1 for e in ents if e.get("status") in OPEN or holding(e, now_ts, cfg)) >= cap:
        return "max_concurrent_positions"
    return None


def _submit(card: dict[str, Any], led: dict[str, Any], broker: Broker, cfg: Config, now_ts: float) -> None:
    e = _entry(card, "working", "", now_ts)
    led["entries"][card["intent_id"]] = e
    save_ledger(led)  # 先落帳再送:就算送單途中 process 被殺,重啟後也不會對同一張卡重送
    root = root_of(card)
    try:
        contract = broker.resolve_contract(root)
        if not contract:
            raise BrokerRejected(f"no_live_contract:{root}")
        e["contract"] = contract
        e["sent_at"] = now_ts
        res = broker.place_buy(contract, e["lot"], price_text(float(card["price"])))
        e["order_no"], e["seq_no"] = res.get("order_no"), res.get("seq_no")
        e["dry"] = bool(res.get("dry"))
        e["status"] = "dry_run" if e["dry"] else "working"
        if e["dry"]:
            e["held_since"] = now_ts
        led["consec_fail"] = 0
    except BrokerRejected as exc:
        e["status"], e["reason"] = "failed", str(exc)[:200]
        if any(w in str(exc) for w in ACCOUNT_LEVEL_WORDS):
            led["cooldown_until"] = now_ts + ACCOUNT_COOLDOWN_SEC
            led["cooldown_reason"] = str(exc)[:80]
        else:
            led["consec_fail"] += 1
    except Exception as exc:  # noqa: BLE001  結果不明:保留保證金、絕不重送
        e["status"], e["reason"] = "unknown_submit", f"{type(exc).__name__}:{str(exc)[:160]}"
        led["consec_fail"] += 1
    if led["consec_fail"] >= cfg.fail_halt and not led["halted"]:
        _halt(led, f"consecutive_failures:{led['consec_fail']}", now_ts)
    e["updated_at"] = datetime.fromtimestamp(now_ts, TZ).isoformat(timespec="seconds")
    save_ledger(led)


def process_exits(led: dict[str, Any], broker: Broker, cfg: Config, now_ts: float, day: str) -> int:
    """小哥出場(config/clone_agent_exit.json):對仍持有的多單依 time_flat > stop_loss > reverse_exhaustion 判斷。
    cfg.exit_enabled=False(預設)只把 exit.signal 記進帳本、不送賣單。送賣單另需券商端確認仍持有、報價夠新、
    assert_live_submit_allowed;任何查不到一律不賣(fail-closed)。IOC 限價掛買一下 N 檔,最多重試 max_attempts 次。
    回傳這輪新記下的 signal 數。"""
    try:
        pol = load_exit_policy()
    except Exception:  # noqa: BLE001
        return 0
    snap = led.get("broker") or {}
    events = read_exhaustion_events(day)
    n = 0
    for e in led["entries"].values():
        if e.get("status") != "filled" and not (e.get("status") == "dry_run" and holding(e, now_ts, cfg)):
            continue
        root = root_of({"symbol": e.get("symbol")})
        ex = e.setdefault("exit", {})
        if not root or ex.get("done"):
            continue
        bid = last_bid(root, day, now_ts, float(pol["bid_max_age_sec"]))
        dec = evaluate_exit(e, bid, now_ts, events, pol)
        if not dec:
            continue
        if not ex.get("signal"):
            ex["signal"] = {**dec, "at": datetime.fromtimestamp(now_ts, TZ).isoformat(timespec="seconds"), "bid": bid}
            save_ledger(led)
            n += 1
        has_pos = bool(snap.get("positions_ok")) and any(sym_hit(p.get("symbol"), root) for p in snap.get("positions") or [])
        if not cfg.exit_enabled or not e.get("contract") or bid is None or not has_pos:
            continue
        if int(ex.get("attempts") or 0) >= int(pol["max_attempts"]) or now_ts - float(ex.get("last_try") or 0) < float(pol["retry_gap_sec"]):
            continue
        px = bid - tick_size(bid) * int(pol["ioc_ticks_below_bid"])
        ex["attempts"] = int(ex.get("attempts") or 0) + 1
        ex["last_try"] = now_ts
        save_ledger(led)  # 先落帳再送
        try:
            res = broker.place_sell(e["contract"], int(e.get("lot") or 1), price_text(px))
            ex["last_result"] = {"order_no": res.get("order_no"), "dry": bool(res.get("dry")), "px": px}
            if res.get("dry"):
                ex["done"] = True
        except Exception as exc:  # noqa: BLE001  結果不明:不立刻重送,等 retry_gap
            ex["last_error"] = f"{type(exc).__name__}:{str(exc)[:160]}"
        save_ledger(led)
    return n


CANCEL_TRIGGERS = ("C1", "C2", "C3")


def _cancel_reject(led: dict[str, Any], rid: str, reason: str, now_ts: float) -> None:
    led.setdefault("cancels", {})[rid] = {"status": "rejected", "reason": reason,
                                         "at": datetime.fromtimestamp(now_ts, TZ).isoformat(timespec="seconds")}


def process_cancels(led: dict[str, Any], broker: Broker, cfg: Config, now_ts: float, day: str) -> int:
    """執行 AI 監督員寫下的撤單請求。AI 只能「請求」;這裡逐筆驗證後才撤,且只撤本層自己送出、尚未成交的委託。
    不受自動送單開關/熔斷限制(撤單是降低風險);每個 request_id 只處理一次(先落帳再送),結果不明絕不重送。"""
    done = 0
    cancels = led.setdefault("cancels", {})
    for req in read_cancel_requests(day):
        rid = str(req["request_id"])
        if rid in cancels:
            continue
        e = led["entries"].get(str(req.get("intent_id")))
        if req.get("source") != "ai_supervisor" or req.get("trigger_id") not in CANCEL_TRIGGERS:
            _cancel_reject(led, rid, "bad_request", now_ts)
        elif now_ts - float(req.get("requested_at") or 0) > cfg.cancel_max_age_sec:
            _cancel_reject(led, rid, "request_stale", now_ts)
        elif e is None or not e.get("order_no") or str(e["order_no"]) != str(req.get("order_no")):
            _cancel_reject(led, rid, "order_mismatch", now_ts)
        elif e.get("status") not in WORKING_STATES:
            _cancel_reject(led, rid, f"not_working:{e.get('status')}", now_ts)
        elif sum(1 for c in cancels.values() if c.get("status") in ("sending", "cancel_sent", "cancel_failed", "dry_run_cancel")) >= cfg.daily_max_cancels:
            _cancel_reject(led, rid, "daily_cancel_cap", now_ts)
        else:
            cancels[rid] = {"status": "sending", "order_no": e["order_no"], "intent_id": req.get("intent_id"),
                            "trigger_id": req.get("trigger_id"), "reason": str(req.get("reason") or "")[:200],
                            "at": datetime.fromtimestamp(now_ts, TZ).isoformat(timespec="seconds")}
            save_ledger(led)
            try:
                res = broker.cancel_order(str(e["order_no"]))
                cancels[rid]["status"] = "dry_run_cancel" if res.get("dry") else "cancel_sent"
                e["cancel_requested_at"] = cancels[rid]["at"]
            except Exception as exc:  # noqa: BLE001  撤單失敗(多半是已成交)只記錄,不重試
                cancels[rid]["status"] = "cancel_failed"
                cancels[rid]["error"] = f"{type(exc).__name__}:{str(exc)[:160]}"
            done += 1
        save_ledger(led)
    return done


def tick(broker: Broker, cfg: Config, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(TZ)
    now_ts = now.timestamp()
    day = now.strftime("%Y-%m-%d")
    led = load_ledger(day)
    if not led.get("carried_checked"):
        carry_over(led, day)
        save_ledger(led)
    sw = read_switch()
    out: dict[str, Any] = {"day": day, "switch": sw["enabled"], "switch_state": sw["state"], "halted": led["halted"],
                           "sent": 0, "skipped": 0, "reconciled": 0}
    try:
        if refresh_snapshot(led, broker, cfg, now_ts):
            out["reconciled"] = 1
    except Exception as exc:  # noqa: BLE001  對帳失敗只影響保證金釋放速度,不影響安全
        out["reconcile_error"] = f"{type(exc).__name__}:{str(exc)[:120]}"
    try:
        if process_cancels(led, broker, cfg, now_ts, day):
            out["cancelled_by_ai"] = led.get("cancels")
            refresh_snapshot(led, broker, cfg, now_ts, force=True)
    except Exception as exc:  # noqa: BLE001
        out["cancel_error"] = f"{type(exc).__name__}:{str(exc)[:120]}"
    try:
        if process_exits(led, broker, cfg, now_ts, day):
            out["exit_signals"] = sum(1 for e in led["entries"].values() if (e.get("exit") or {}).get("signal"))
    except Exception as exc:  # noqa: BLE001
        out["exit_error"] = f"{type(exc).__name__}:{str(exc)[:120]}"
    decisions = load_decisions(day)
    dirty = False
    for card in load_candidates(day):
        iid = card["intent_id"]
        if iid in led["entries"]:
            continue
        reason = "switch_off" if not sw["enabled"] else ("halted:" + led["halted"]["reason"] if led["halted"] else
                                          ("account_cooldown" if now_ts < float(led.get("cooldown_until") or 0) else
                                           _check_card(card, led, cfg, now_ts, decisions)))
        if reason:
            led["entries"][iid] = _entry(card, "skipped", reason, now_ts)
            out["skipped"] += 1
            dirty = True
            continue
        if hasattr(broker, "fetch_account"):
            try:
                refresh_snapshot(led, broker, cfg, now_ts, force=True)
            except Exception as exc:  # noqa: BLE001
                out["reconcile_error"] = f"{type(exc).__name__}:{str(exc)[:120]}"
            gate = broker_gate(card, led, cfg, now_ts)
            if gate:
                led["entries"][iid] = _entry(card, "skipped", gate, now_ts)
                out["skipped"] += 1
                dirty = True
                continue
        _submit(card, led, broker, cfg, now_ts)
        out["sent"] += 1
        dirty = False
    if dirty:
        save_ledger(led)
    out["halted"] = led["halted"]
    out["reserved_margin"] = round(reserved_margin(led, now_ts, cfg))
    out["entries"] = len(led["entries"])
    snap = led.get("broker") or {}
    if snap:
        out["broker"] = {"at": snap.get("at"), "ok": bool(snap.get("orders_ok") and snap.get("positions_ok")),
                         "positions": len(snap.get("positions") or []), "open_orders": len(snap.get("open_orders") or []),
                         "available_margin": snap.get("available_margin")}
    return out


class DryBroker:
    """dry-run:不登入、不連券商;行為與真券商同介面,委託永遠不會成交。"""

    def __init__(self) -> None:
        self.n = 0

    def resolve_contract(self, root: str) -> str | None:
        return f"{root}(dry)"

    def place_buy(self, contract: str, lot: int, price_text: str) -> dict[str, Any]:
        self.n += 1
        return {"order_no": f"DRY-{self.n}", "seq_no": None, "dry": True}

    def fetch_results(self) -> list[dict[str, Any]]:
        return []

    def cancel_order(self, order_no: str) -> dict[str, Any]:
        return {"dry": True, "status": "dry_run_cancel", "order_no": order_no}

    def place_sell(self, contract: str, lot: int, price_text: str) -> dict[str, Any]:
        self.n += 1
        return {"order_no": f"DRY-SELL-{self.n}", "seq_no": None, "dry": True}


class FubonBroker:
    """實彈券商介面。session 於第一次真的要送單/對帳時才登入;所有送單都先過 assert_live_submit_allowed。"""

    def __init__(self, session_getter: Any = None) -> None:
        self._get = session_getter
        self._contracts: dict[str, str | None] = {}

    def _session(self) -> Any:
        if self._get is None:
            from tmf_channel.session_pool import get_fubon_session
            self._get = get_fubon_session
        return self._get()

    def resolve_contract(self, root: str) -> str | None:
        if root not in self._contracts:
            from order.tmf_channel_marketdata import resolve_live_futures_symbol
            hit = resolve_live_futures_symbol(self._session(), root)
            self._contracts[root] = hit[0] if hit else None
        return self._contracts[root]

    def place_buy(self, contract: str, lot: int, price_text: str) -> dict[str, Any]:
        from order.live_submit_guard import assert_live_submit_allowed
        try:
            assert_live_submit_allowed()
        except RuntimeError as exc:
            raise BrokerRejected(str(exc)) from exc
        from fubon_neo.constant import BSAction, FutOptMarketType, FutOptOrderType, FutOptPriceType, TimeInForce
        from fubon_neo.sdk import FutOptOrder
        from order.fubon_futopt_orders import _result_ok, _serialize, pick_futopt_account

        sess = self._session()
        acc = pick_futopt_account(sess)
        order = FutOptOrder(
            market_type=FutOptMarketType.Future, price_type=FutOptPriceType.Limit, time_in_force=TimeInForce.ROD,
            order_type=FutOptOrderType.New, buy_sell=BSAction.Buy, symbol=contract, lot=int(lot), price=price_text,
            user_def="cloneagt",
        )
        res = sess.sdk.futopt.place_order(acc, order)
        if not _result_ok(res):
            raise BrokerRejected(getattr(res, "message", "") or "futopt place_order failed")
        data = _serialize(getattr(res, "data", None))
        d = data if isinstance(data, dict) else {}
        return {"order_no": d.get("order_no") or d.get("orderNo"), "seq_no": d.get("seq_no") or d.get("seqNo"), "raw": d}

    def place_sell(self, contract: str, lot: int, price_text: str) -> dict[str, Any]:
        """平倉賣出(只平多單):IOC 限價、order_type=Close,user_def=cloneagt。先過 assert_live_submit_allowed。"""
        from order.live_submit_guard import assert_live_submit_allowed
        try:
            assert_live_submit_allowed()
        except RuntimeError as exc:
            raise BrokerRejected(str(exc)) from exc
        from fubon_neo.constant import BSAction, FutOptMarketType, FutOptOrderType, FutOptPriceType, TimeInForce
        from fubon_neo.sdk import FutOptOrder
        from order.fubon_futopt_orders import _result_ok, _serialize, pick_futopt_account

        sess = self._session()
        acc = pick_futopt_account(sess)
        order = FutOptOrder(
            market_type=FutOptMarketType.Future, price_type=FutOptPriceType.Limit, time_in_force=TimeInForce.IOC,
            order_type=FutOptOrderType.Close, buy_sell=BSAction.Sell, symbol=contract, lot=int(lot), price=price_text,
            user_def="cloneagt",
        )
        res = sess.sdk.futopt.place_order(acc, order)
        if not _result_ok(res):
            raise BrokerRejected(getattr(res, "message", "") or "futopt place_order(sell) failed")
        data = _serialize(getattr(res, "data", None))
        d = data if isinstance(data, dict) else {}
        return {"order_no": d.get("order_no") or d.get("orderNo"), "seq_no": d.get("seq_no") or d.get("seqNo"), "raw": d}

    def cancel_order(self, order_no: str) -> dict[str, Any]:
        """只撤「仍在掛單中、且是本層送出(user_def=cloneagt)」的委託;查不到/已成交都拒絕。"""
        from fubon_neo.constant import FutOptMarketType
        from order.fubon_futopt_orders import cancel_futopt_order, get_futopt_order_results, pick_futopt_account

        sess = self._session()
        acc = pick_futopt_account(sess)
        rows = get_futopt_order_results(sess, acc=acc, market_type=FutOptMarketType.Future)
        raw = next((r for r in rows if str(getattr(r, "order_no", "")) == str(order_no)), None)
        if raw is None:
            raise BrokerRejected("order_not_found")
        ud = getattr(raw, "user_def", None)
        if ud not in (None, "", "cloneagt"):
            raise BrokerRejected(f"not_clone_agent_order:{ud}")
        st = classify_result(getattr(raw, "status", None), int(getattr(raw, "filled_lot", 0) or 0), int(getattr(raw, "lot", 1) or 1))
        if st not in WORKING_STATES:
            raise BrokerRejected(f"not_working:{st}")
        return cancel_futopt_order(sess, raw, acc=acc, dry_run=False)

    def fetch_results(self) -> list[dict[str, Any]]:
        from fubon_neo.constant import FutOptMarketType
        from order.fubon_futopt_orders import get_futopt_order_results

        out = []
        for r in get_futopt_order_results(self._session(), market_type=FutOptMarketType.Future):
            out.append({
                "order_no": getattr(r, "order_no", None), "seq_no": getattr(r, "seq_no", None),
                "status": getattr(r, "status", None), "filled_lot": getattr(r, "filled_lot", 0) or 0,
                "lot": getattr(r, "lot", None), "symbol": getattr(r, "symbol", None),
                "side": str(getattr(getattr(r, "buy_sell", None), "name", getattr(r, "buy_sell", None)) or ""),
                "price": getattr(r, "price", None),
            })
        return out

    def fetch_positions(self) -> list[dict[str, Any]]:
        """整個期貨帳戶的未平倉部位(不分來源:自動袖、手動、其他袖)。唯讀。"""
        from order.fubon_futopt_orders import _bs_to_side, _result_data, pick_futopt_account

        sess = self._session()
        acc = pick_futopt_account(sess)
        res = sess.sdk.futopt_accounting.query_hybrid_position(acc)
        out = []
        for r in list(_result_data(res) or []):
            try:
                lots = int(getattr(r, "orig_lots", None) or 0)
            except (TypeError, ValueError):
                lots = 0
            if lots <= 0:
                continue
            out.append({"symbol": str(getattr(r, "symbol", "") or ""), "side": _bs_to_side(getattr(r, "buy_sell", None)),
                        "lots": lots, "price": getattr(r, "price", None)})
        return out

    def fetch_available_margin(self) -> float | None:
        from order.fubon_futopt_orders import _result_data, pick_futopt_account

        sess = self._session()
        res = sess.sdk.futopt_accounting.query_margin_equity(pick_futopt_account(sess))
        rows = list(_result_data(res) or [])
        for r in rows:
            if str(getattr(r, "currency", "") or "").upper() in ("NTD", "TWD", "NT$", ""):
                v = getattr(r, "available_margin", None)
                return float(v) if v is not None else None
        return None

    def fetch_account(self) -> dict[str, Any]:
        """三項各自獨立 try:任何一項失敗只讓那一項為 None(送單前核對會 fail-closed,不會誤當成「空」)。"""
        acct: dict[str, Any] = {"orders": None, "positions": None, "available_margin": None, "errors": {}}
        for key, fn in (("orders", self.fetch_results), ("positions", self.fetch_positions), ("available_margin", self.fetch_available_margin)):
            try:
                acct[key] = fn()
            except Exception as exc:  # noqa: BLE001
                acct["errors"][key] = f"{type(exc).__name__}:{str(exc)[:80]}"
        return acct
