#!/usr/bin/env python3
"""複製人代理人 AI 監督員 · 常駐 · 沒有任何券商 API 路徑。
階段 shadow=全部只記錄;cancel(2026-10-06 使用者授權)=通過護欄的「撤單」提案寫成請求檔,由下單層驗證後才真的撤;出場一律仍只記錄。

小腦(本檔+src/clone_agent/agent_supervisor.py)組狀態包、算觸發條件、驗證輸出、寫日誌;
大腦(headless `claude -p`,無工具)只讀狀態包、輸出結構化判斷。
迴圈:每 10 分鐘巡檢(09:50–13:30)· 送單後複核(輪詢帳本)· 收盤檢討。
用法: PYTHONPATH=src .venv/bin/python scripts/agent/clone_agent_supervisor.py [--once patrol|post_send|eod] [--day YYYY-MM-DD] [--dry-llm]
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "research"))

from project_dotenv import load_project_dotenv  # noqa: E402

load_project_dotenv()

import trader_signal_watch as w  # noqa: E402
from clone_agent import agent_supervisor as sv  # noqa: E402
from clone_agent.order_switch import append_cancel_request, read_cancel_requests, read_heartbeat, read_ledger_display, read_switch  # noqa: E402

OPEN = ("working", "partial", "unknown_submit")
SCRATCH = Path("/tmp/ca_sup")
INF = float("inf")


class Engine:
    """從 raw 逐筆檔重建每檔滾動流量(同 trader_signal_watch.Tape),並計算監督用特徵。"""

    def __init__(self, day: str):
        self.day = day
        self.tape = w.Tape(day)
        self.core = w.core_universe()
        cal = json.loads(w.CALIB.read_text(encoding="utf-8"))
        self.full_uni = {str(r["sid"]) for r in cal["universe"]}
        specs = json.loads((w.DATA / "data/cache/taifex_stock_futures_specs.json").read_text())
        self.s2r, self.s2nm, best = {}, {}, {}
        for root, sp in specs.items():
            sid = str(sp.get("underlying_id") or sp.get("stock_id") or "")
            n = sp.get("contract_shares") or 0
            if sid and n and n > best.get(sid, 0):
                best[sid] = n
                self.s2r[sid], self.s2nm[sid] = root, sp.get("name", sid)
        self.r2s = {r: s for s, r in self.s2r.items()}
        from trader_peer_group_choice import ma20_and_prevclose
        self.prev_close = {k: v[1] for k, v in ma20_and_prevclose(self.full_uni, day).items()}

    def last_event_ts(self, sid: str, now: float) -> float:
        lst = self.tape._pxev.get(sid) or []
        j = bisect.bisect_right(lst, (now, INF))
        return lst[j - 1][0] if j else 0.0

    def refresh(self) -> int:
        return self.tape.poll()

    def _win(self, sid: str, now: float, win: float | None):
        lst = self.tape._pxev.get(sid) or []
        j = bisect.bisect_right(lst, (now, INF))
        i = 0 if win is None else bisect.bisect_left(lst, (now - win, -INF), 0, j)
        seg = lst[i:j]
        last = lst[j - 1][1] if j else None
        if not seg:
            return last, last, last
        pxs = [p for _, p in seg]
        return last, max(pxs), min(pxs)

    def px(self, sid: str, now: float) -> float | None:
        return self._win(sid, now, 60)[0]

    def pools(self, now: float) -> dict[str, dict[str, float | None]]:
        t = self.tape
        fl30 = {s: t.flow(s, now, 1800) for s in t.ev}
        fld = {s: t.flow(s, now, None) for s in t.ev}
        sh = lambda x, a: (x / a) if a > 0 else None  # noqa: E731
        p = {
            "amt30": {s: v[0] for s, v in fl30.items()},
            "big_day": {s: sh(v[1], v[0]) for s, v in fld.items()},
            "big30": {s: sh(v[1], v[0]) for s, v in fl30.items()},
            "ret30": {s: sh(v[2], v[0]) for s, v in fl30.items()},
        }
        return p

    def feat(self, sid: str, now: float, pools: dict[str, dict[str, float | None]]) -> dict[str, Any] | None:
        t = self.tape
        last, hi, lo = self._win(sid, now, 1800)
        if not last or not hi or not lo:
            return None
        fl30, fld = t.flow(sid, now, 1800), t.flow(sid, now, None)
        pc = self.prev_close.get(sid)
        lst = t._pxev.get(sid) or []
        j = bisect.bisect_right(lst, (now, INF))
        seen = lst[j - 1][0] if j else None
        r = lambda v, n=3: None if v is None else round(v, n)  # noqa: E731
        return {
            "px": last, "chg_pct": r(100 * (last / pc - 1), 2) if pc else None,
            "dist_hi30": r(100 * (last / hi - 1)), "dist_lo30": r(100 * (last / lo - 1)),
            "amt_pct": r(w.pct_rank(pools["amt30"], sid), 1),
            "big30_wan": round(fl30[1] / 1e4), "ret30_wan": round(fl30[2] / 1e4),
            "bigday_wan": round(fld[1] / 1e4), "retday_wan": round(fld[2] / 1e4),
            "share_pct": {k: (None if pools[k].get(sid) is None else round(100 * pools[k][sid], 2)) for k in ("big_day", "big30", "ret30")},
            "rank": {"big_day": r(w.frac_rank(pools["big_day"], sid)), "big30": r(w.frac_rank(pools["big30"], sid)),
                     "ret30": r(w.frac_rank(pools["ret30"], sid, larger=False))},
            "last_trade_age_sec": None if seen is None else round(now - seen),
        }

    def ret_bps(self, sid: str, now: float, sec: float) -> float | None:
        a, b = self.px(sid, now), self.px(sid, now - sec)
        return round((a / b - 1) * 1e4, 1) if a and b else None

    def basket_bps(self, now: float, sec: float) -> float | None:
        v = [x for x in (self.ret_bps(s, now, sec) for s in self.core) if x is not None]
        return round(sum(v) / len(v), 1) if len(v) >= 20 else None


def card_epoch(day: str, hms: str | None) -> float | None:
    try:
        return datetime.fromisoformat(f"{day}T{hms}").timestamp()
    except (TypeError, ValueError):
        return None


def load_candidates(day: str) -> dict[str, dict[str, Any]]:
    p = w.STATE_DIR / f"candidates_{day}.jsonl"
    out: dict[str, dict[str, Any]] = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                c = json.loads(ln)
                out[c["intent_id"]] = c
            except (ValueError, KeyError):
                continue
    return out


def fetch_dashboard(sids: set[str]) -> tuple[dict[str, dict[str, str]], str | None]:
    if not sids:
        return {}, None
    err = None
    for attempt in range(2):
        try:
            html = urllib.request.urlopen("http://127.0.0.1:8771/frag", timeout=8).read().decode("utf-8", "replace")
            rows = sv.parse_dashboard_rows(html, sids)
            if rows:
                return rows, None
            err = "no_rows(儀表板回應無這些標的列,可能剛重啟)"
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}:{str(exc)[:80]}"
        if attempt == 0:
            time.sleep(3)
    return {}, err


def manual_holds() -> set[str]:
    try:
        return {str(x) for x in json.loads((sv.agent_dir() / "manual_hold.json").read_text())}
    except (OSError, ValueError):
        return set()


def build_pack(eng: Engine, cfg: dict[str, Any], now: float, kind: str, focus_iid: str | None = None) -> dict[str, Any]:
    day = eng.day
    led = read_ledger_display(day) or {"entries": {}}
    hb = read_heartbeat() or {}
    cands = load_candidates(day)
    pools_now = eng.pools(now)
    positions, orders, sids = [], [], set()
    for iid, e in (led.get("entries") or {}).items():
        st = e.get("status")
        if e.get("dry") or st not in ("filled", *OPEN):
            continue
        sid = str(e.get("sid") or "")
        t_card = card_epoch(day, e.get("card_time"))
        if e.get("carried_from"):
            t_card = None
        f_now = eng.feat(sid, now, pools_now)
        entry_feat = None
        if t_card:
            entry_feat = eng.feat(sid, t_card, eng.pools(t_card))
        entry_px = (entry_feat or {}).get("px")
        card = cands.get(iid) or {}
        base = {"sid": sid, "name": e.get("uname"), "intent_id": iid, "limit_price_fut": e.get("price"), "ours": True,
                "manual_hold": sid in manual_holds(), "status": st, "card_time": e.get("card_time"),
                "carried_from": e.get("carried_from"),
                "entry": {"cash_px": entry_px, "card_flow_share_rank": card.get("flow"), "card_dist_refs": card.get("dist_refs"),
                          "features_at_card_time": entry_feat},
                "now": f_now}
        sids.add(sid)
        if st == "filled":
            held_min = round((now - float(e.get("held_since") or now)) / 60, 1) if e.get("held_since") else None
            base.update(held_min=held_min, order_no=e.get("order_no"),
                        triggers=eng_exit(f_now, entry_px, cfg) if f_now else {})
            positions.append(base)
        else:
            age = (now - t_card) / 60 if t_card else 0.0
            tr = sv.eval_cancel_triggers(f_now or {}, entry_feat or {}, age, eng.basket_bps(now, 600),
                                         eng.ret_bps(sid, now, 600), cfg) if f_now else {}
            base.update(order_no=str(e.get("order_no") or ""), age_min=round(age, 1), filled_lot=e.get("filled_lot"), triggers=tr)
            orders.append(base)
    snap = led.get("broker") or {}
    ours_sids = {r["sid"] for r in positions}
    others = []
    for bp in snap.get("positions") or []:
        sid = eng.r2s.get(w_root(bp.get("symbol")))
        if sid in ours_sids:
            continue
        avg = bp.get("price")
        f_now = eng.feat(sid, now, pools_now) if sid else None
        side = bp.get("side")
        rec = {"sid": sid, "name": eng.s2nm.get(sid, sid) if sid else None, "ours": False, "status": "broker_position",
               "source": "券商持倉快照(手動或他袖,非本代理人下單)", "side": side, "lots": bp.get("lots"), "symbol": bp.get("symbol"),
               "entry": {"cash_px": avg, "note": "進場價=券商期貨均價(近似,與現貨有基差);進場當下的流量特徵不可得"},
               "manual_hold": bool(sid) and sid in manual_holds(), "now": f_now,
               "pnl_bps_approx": sv.pnl_bps(f_now.get("px"), avg) * (1 if side == "L" else -1) if (f_now and avg and sv.pnl_bps(f_now.get("px"), avg) is not None) else None,
               "triggers": eng_exit(f_now, avg, cfg) if (f_now and side == "L" and avg) else {}}
        if sid:
            sids.add(sid)
        others.append(rec)
    dash, dash_err = fetch_dashboard(sids)
    for rec in positions + orders + others:
        rec["dashboard_row"] = dash.get(rec["sid"], {})
    positions = positions + others
    ent = list((led.get("entries") or {}).values())
    cnt: dict[str, int] = {}
    for e in ent:
        cnt[e.get("status") or "?"] = cnt.get(e.get("status") or "?", 0) + 1
    last_ev = max((eng.last_event_ts(s, now) for s in eng.core), default=0)
    st = sv.read_state()
    return {
        "kind": kind, "focus_intent_id": focus_iid, "now": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "stage": cfg["stage"], "data_age_sec": round(now - last_ev) if last_ev else None,
        "positions": positions, "orders": orders,
        "account": {"available_margin": snap.get("available_margin"), "broker_check_at": snap.get("at"),
                    "orders_ok": snap.get("orders_ok"), "positions_ok": snap.get("positions_ok"), "errors": snap.get("errors"),
                    "worker_state": hb.get("state") or hb.get("status"), "auto_switch": read_switch().get("enabled"),
                    "halted": led.get("halted")},
        "market": {"basket_10m_bps": eng.basket_bps(now, 600), "basket_30m_bps": eng.basket_bps(now, 1800),
                   "n_core": len(eng.core), "ledger_status_counts_today": cnt, "cards_today": len(cands)},
        "dashboard_error": dash_err,
        "rules": {"live": "CA-R1 v1.2.0 做多;自動送單窗 10:00–12:00", "short_mirror": "做多篩選欄位鏡像(僅做出場判斷用)"},
        "memory": {"position_notes": st.get("notes", {}), "last_next_watch": st.get("last_next_watch", []),
                   "yesterday_review": st.get("yesterday_review")},
        "directives": sv.read_directives(),
    }


def w_root(sym: Any) -> str:
    s = str(sym or "").upper()
    return s[2:-1] if s.startswith("FI") and s.endswith("F") else s[:3]


def eng_exit(f_now: dict[str, Any], entry_px: float | None, cfg: dict[str, Any]) -> dict[str, Any]:
    return sv.eval_exit_triggers(f_now, entry_px, cfg)


def call_llm(system: str, user: str, cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    SCRATCH.mkdir(exist_ok=True)
    claude = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    cmd = [claude, "-p", user, "--model", cfg["llm"]["model"], "--tools", "", "--no-session-persistence",
           "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "",
           "--output-format", "json", "--json-schema", json.dumps(sv.REVIEW_SCHEMA),
           "--system-prompt", system, "--max-budget-usd", str(cfg["llm"]["max_budget_usd"])]
    if cfg["llm"].get("effort"):
        cmd += ["--effort", str(cfg["llm"]["effort"])]
    t0 = time.time()
    meta: dict[str, Any] = {"model": cfg["llm"]["model"]}
    try:
        cp = subprocess.run(cmd, cwd=SCRATCH, capture_output=True, text=True, timeout=cfg["llm"]["timeout_sec"],
                            env={**os.environ, "PATH": f"{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"})
        meta["latency_sec"] = round(time.time() - t0, 1)
        d = json.loads(cp.stdout)
        meta.update(cost_usd=d.get("total_cost_usd"), is_error=d.get("is_error"))
        obj = d.get("structured_output")
        if obj is None and d.get("result"):
            obj = json.loads(d["result"])
        if not isinstance(obj, dict):
            meta["error"] = f"no_structured_output rc={cp.returncode} err={cp.stderr[-200:]}"
            return None, meta
        return obj, meta
    except subprocess.TimeoutExpired:
        meta.update(latency_sec=round(time.time() - t0, 1), error="timeout")
    except Exception as exc:  # noqa: BLE001
        meta.update(latency_sec=round(time.time() - t0, 1), error=f"{type(exc).__name__}:{str(exc)[:160]}")
    return None, meta


USER_TMPL = {
    "patrol": "巡檢。請依系統提示的讀資料順序檢查狀態包並輸出 JSON。若沒有持倉也沒有委託,仍要在 market.view 寫下對盤面與整體風險的看法。\n\n狀態包:\n{pack}",
    "post_send": "送單後複核(影子期無時限壓力但請簡短)。剛送出的委託見 focus_intent_id。只判斷:是否符合 C1–C3 任一條?是→提案 cancel;否→verdict=keep 並一句理由。\n\n狀態包:\n{pack}",
    "eod": "收盤檢討。pack.day_trail 是今天每輪你的判斷與影子提案及其後續價格;請寫:哪些判斷後來證明對/錯、影子提案若被執行會怎樣(用 outcome 欄位的 bps)、明天開盤第一輪要記得的事。summary 即今日檢討;position_notes 可留跨日筆記。\n\n狀態包:\n{pack}",
}


def request_cancel(eng: Engine, cfg: dict[str, Any], action: dict[str, Any], pack: dict[str, Any], now: float) -> str:
    """把通過護欄的撤單提案寫成請求檔;回傳結果標記。這裡不碰券商,真正撤單由下單層驗證後執行。"""
    od = sv.resolve_order(action.get("target"), pack["orders"])
    if not od or not od.get("order_no"):
        return "not_sent:no_order"
    led = read_ledger_display(eng.day) or {"entries": {}}
    e = (led.get("entries") or {}).get(od["intent_id"]) or {}
    if e.get("status") not in ("working", "partial") or str(e.get("order_no")) != str(od["order_no"]):
        return f"not_sent:ledger_status_{e.get('status')}"
    reqs = read_cancel_requests(eng.day)
    if any(str(r.get("order_no")) == str(od["order_no"]) for r in reqs):
        return "not_sent:already_requested"
    if len(reqs) >= int(cfg.get("limits", {}).get("max_cancels_per_day", 5)):
        return "not_sent:daily_cap"
    rid = uuid.uuid4().hex[:10]
    append_cancel_request(eng.day, {
        "request_id": rid, "source": "ai_supervisor", "intent_id": od["intent_id"], "order_no": str(od["order_no"]),
        "sid": od["sid"], "trigger_id": (sv.split_trigger_ids(action.get("trigger_id")) or [None])[0],
        "trigger_ids": sv.split_trigger_ids(action.get("trigger_id")), "reason": action.get("reason"),
        "evidence": action.get("evidence"), "requested_at": now, "pack_now": pack["now"]})
    return f"cancel_requested:{rid}"


def run_review(eng: Engine, cfg: dict[str, Any], kind: str, now: float, focus_iid: str | None = None,
               dry_llm: bool = False, extra: dict[str, Any] | None = None, execute: bool = False) -> dict[str, Any]:
    pack = build_pack(eng, cfg, now, kind, focus_iid)
    if extra:
        pack.update(extra)
    system = sv.load_prompt()
    user = USER_TMPL[kind].format(pack=json.dumps(pack, ensure_ascii=False, separators=(",", ":")))
    if dry_llm:
        review, meta = None, {"error": "dry_llm"}
    else:
        review, meta = call_llm(system, user, cfg)
    rec: dict[str, Any] = {"kind": kind, "pack": pack, "meta": meta, "review": review, "shadow_actions": []}
    if review:
        for a in review.get("actions") or []:
            why = sv.guard_action(a, pack, review)
            px = eng.px(str(a.get("target")), now) if a.get("type") != "switch_off" else None
            tgt_sid = a.get("target")
            if a.get("type") == "cancel":
                od = sv.resolve_order(a.get("target"), pack["orders"])
                tgt_sid = od["sid"] if od else tgt_sid
                px = eng.px(str(tgt_sid), now)
            executed = None
            if execute and cfg["stage"] == "cancel" and a.get("type") == "cancel" and not why:
                executed = request_cancel(eng, cfg, a, pack, time.time())
            rec["shadow_actions"].append({**a, "sid": tgt_sid, "guard": why or "pass", "px_at_proposal": px, "t": now,
                                          "executed": executed})
        st = sv.read_state()
        st["notes"].update({k: v for k, v in (review.get("position_notes") or {}).items()})
        st["last_next_watch"] = review.get("next_watch") or []
        st["last_review_at"] = pack["now"]
        if kind == "eod":
            st["yesterday_review"] = {"day": eng.day, "text": review.get("summary")}
        sv.write_state(st)
    sv.journal_append(eng.day, rec)
    write_view(rec, cfg)
    return rec


def write_view(rec: dict[str, Any], cfg: dict[str, Any]) -> None:
    rv, pk = rec.get("review"), rec["pack"]
    view = {"updated_at": pk["now"], "kind": rec["kind"], "stage": cfg["stage"], "ok": bool(rv),
            "error": rec["meta"].get("error"), "latency_sec": rec["meta"].get("latency_sec"), "cost_usd": rec["meta"].get("cost_usd")}
    if rv:
        by = {p["sid"]: p for p in rv.get("positions") or []}
        view.update(summary=rv.get("summary"), market=rv.get("market"), insufficient_data=rv.get("insufficient_data"),
                    next_watch=rv.get("next_watch"), no_action_reason=rv.get("no_action_reason"))
        view["items"] = [{"sid": x["sid"], "name": x.get("name"), "kind": "position" if x in pk["positions"] else "order",
                          "pnl_bps": (x.get("triggers") or {}).get("pnl_bps"),
                          "triggers_met": [k for k, v in (x.get("triggers") or {}).items() if isinstance(v, dict) and v.get("met")],
                          "verdict": (by.get(x["sid"]) or {}).get("verdict"), "reason": (by.get(x["sid"]) or {}).get("reason"),
                          "confidence": (by.get(x["sid"]) or {}).get("confidence")} for x in pk["positions"] + pk["orders"]]
        view["shadow_actions"] = [{k: a.get(k) for k in ("type", "sid", "reason", "trigger_id", "guard", "executed")} for a in rec["shadow_actions"]]
    sv.write_view(view)


def day_trail(eng: Engine, now: float) -> list[dict[str, Any]]:
    out = []
    for r in sv.read_journal(eng.day):
        rv = r.get("review") or {}
        item = {"at": (r.get("pack") or {}).get("now"), "kind": r.get("kind"), "summary": rv.get("summary"),
                "verdicts": [(p["sid"], p["verdict"]) for p in rv.get("positions") or []], "shadow_actions": []}
        for a in r.get("shadow_actions") or []:
            px0, px1 = a.get("px_at_proposal"), eng.px(str(a.get("sid")), now)
            item["shadow_actions"].append({k: a.get(k) for k in ("type", "sid", "reason", "trigger_id", "guard", "executed")}
                                          | {"outcome_bps_since_proposal": sv_bps(px1, px0)})
        out.append(item)
    return out


def sv_bps(a: float | None, b: float | None) -> float | None:
    return round((a / b - 1) * 1e4, 1) if a and b else None


def slots(cfg: dict[str, Any]) -> list[str]:
    s = cfg["schedule"]
    h, m = map(int, s["patrol_first"].split(":"))
    h2, m2 = map(int, s["patrol_last"].split(":"))
    out, t = [], h * 60 + m
    while t <= h2 * 60 + m2:
        out.append(f"{t // 60:02d}:{t % 60:02d}")
        t += s["patrol_min"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", choices=["patrol", "post_send", "eod"])
    ap.add_argument("--day", default=None)
    ap.add_argument("--dry-llm", action="store_true")
    ap.add_argument("--asof", default=None, help="測試用:把 now 固定在 day 的 HH:MM(搭配 --once)")
    args = ap.parse_args()
    cfg = sv.load_config()
    if cfg["stage"] not in ("shadow", "cancel"):
        print("只實作 shadow / cancel 階段,拒絕啟動", file=sys.stderr)
        return 2
    day = args.day or datetime.now().strftime("%Y-%m-%d")
    print(f"[{datetime.now():%F %T}] 載入 {day} …", flush=True)
    eng = Engine(day)
    eng.refresh()
    print(f"[{datetime.now():%F %T}] 就緒 · 事件 {sum(len(v) for v in eng.tape.ev.values())} · 核心 {len(eng.core)} 檔 · stage={cfg['stage']}", flush=True)
    if args.once:
        now = datetime.fromisoformat(f"{day}T{args.asof}:00").timestamp() if args.asof else time.time()
        extra = {"day_trail": day_trail(eng, now)} if args.once == "eod" else None
        rec = run_review(eng, cfg, args.once, now, dry_llm=args.dry_llm, extra=extra)
        print(json.dumps({"meta": rec["meta"], "review": rec["review"], "n_pos": len(rec["pack"]["positions"]),
                          "n_ord": len(rec["pack"]["orders"])}, ensure_ascii=False, indent=1))
        return 0

    sl = slots(cfg)
    done: set[str] = set()
    reviewed: set[str] = {r["pack"].get("focus_intent_id") for r in sv.read_journal(eng.day) if r.get("kind") == "post_send"}
    eod_done = any(r.get("kind") == "eod" for r in sv.read_journal(eng.day))
    while True:
        now_dt = datetime.now()
        today = now_dt.strftime("%Y-%m-%d")
        if today != eng.day:
            eng = Engine(today); done, reviewed, eod_done = set(), set(), False
            print(f"── 換日 {today} ──", flush=True)
        hm = now_dt.strftime("%H:%M")
        if now_dt.weekday() >= 5 or hm < cfg["schedule"]["patrol_first"] or hm > "13:55":
            time.sleep(30)
            continue
        eng.refresh()
        now = time.time()
        led = read_ledger_display(eng.day) or {"entries": {}}
        for iid, e in (led.get("entries") or {}).items():
            if iid in reviewed or e.get("dry") or e.get("status") not in ("working", "partial", "filled", "unknown_submit") or e.get("carried_from"):
                continue
            reviewed.add(iid)
            eng.refresh()
            now = time.time()
            t0 = time.time()
            rec = run_review(eng, cfg, "post_send", now, focus_iid=iid, dry_llm=args.dry_llm, execute=True)
            print(f"[{datetime.now():%T}] 送單後複核 {e.get('uname')} → {rec['meta'].get('latency_sec')}s {rec['meta'].get('error') or 'ok'}", flush=True)
            now = time.time() if time.time() - t0 > 1 else now
        due = [s for s in sl if s <= hm and s not in done]
        if due:
            done.update(due)                       # 補跑期間錯過的格只做最後一格
            eng.refresh()
            rec = run_review(eng, cfg, "patrol", time.time(), dry_llm=args.dry_llm, execute=True)
            print(f"[{datetime.now():%T}] 巡檢 {due[-1]} → {rec['meta'].get('latency_sec')}s {rec['meta'].get('error') or 'ok'} · 影子提案 {len(rec['shadow_actions'])}", flush=True)
        if not eod_done and hm >= cfg["schedule"]["eod_at"]:
            eod_done = True
            nowx = time.time()
            rec = run_review(eng, cfg, "eod", nowx, dry_llm=args.dry_llm, extra={"day_trail": day_trail(eng, nowx)})
            (sv.agent_dir() / f"agent_review_{eng.day}.md").write_text(
                f"# 複製人監督員 · {eng.day} 收盤檢討\n\n{(rec['review'] or {}).get('summary', '(LLM 無輸出:' + str(rec['meta'].get('error')) + ')')}\n", encoding="utf-8")
            print(f"[{datetime.now():%T}] 收盤檢討完成", flush=True)
        time.sleep(cfg["schedule"]["post_send_poll_sec"])


if __name__ == "__main__":
    raise SystemExit(main())
