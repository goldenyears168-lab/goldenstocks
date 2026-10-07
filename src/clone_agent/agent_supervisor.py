"""複製人代理人 AI 監督員 —— 小腦(純邏輯、可測):觸發條件計算、儀表板列解析、輸出驗證、日誌/記憶/看法檔。

分工:LLM(大腦)只讀這裡產生的狀態包並輸出結構化判斷;所有數字、護欄、檔案寫入都在這裡。
影子階段沒有任何券商 API 路徑;未來進撤單/出場階段時,提案要先過 `guard_action` 才可能被機械層執行。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

import yaml

from clone_agent.approval import _data_dir
from clone_agent.order_switch import atomic_write

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "clone_agent_supervisor.yaml"
PROMPT_PATH = ROOT / "config" / "clone_agent_supervisor_prompt.md"


def agent_dir() -> Path:
    d = _data_dir().parent / "agent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_prompt(path: Path = PROMPT_PATH) -> str:
    return path.read_text(encoding="utf-8")


# ───────────────────────── 觸發條件(預先登記,由小腦算值) ─────────────────────────

def mirror_short(feat: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """做空前置條件 = 做多篩選欄位的鏡像。feat['rank'] 是做多方向的排名分數(越小越前面),
    鏡像排名 = 1 − 做多排名(frac_rank 的 larger=True/False 互為 1−x)。"""
    t = cfg["triggers"]
    keep = t["mirror_keep_pct"] / 100 + 1e-12
    rk = feat.get("rank") or {}

    def m(k: str) -> float | None:
        v = rk.get(k)
        return None if v is None else 1 - v

    conds = {
        "c1_big_day_bottom70": (m("big_day") is not None and m("big_day") <= keep),
        "c2_dist_lo30_ge_0.5": (feat.get("dist_lo30") is not None and feat["dist_lo30"] >= t["mirror_dist_lo_pct"]),
        "c3_big30_bottom70": (m("big30") is not None and m("big30") <= keep),
        "c4_ret30_top70": (m("ret30") is not None and m("ret30") <= keep),
        "c5_amt_pct_ge_30": (feat.get("amt_pct") is not None and feat["amt_pct"] >= t["mirror_amt_pct_min"]),
    }
    return {
        "conds": conds,
        "all_met": all(conds.values()),
        "flow_met": conds["c3_big30_bottom70"] and conds["c4_ret30_top70"],
        "price_only_met": conds["c2_dist_lo30_ge_0.5"],
        "values": {"mirror_rank": {k: (None if m(k) is None else round(m(k), 3)) for k in ("big_day", "big30", "ret30")},
                   "dist_lo30": feat.get("dist_lo30"), "amt_pct": feat.get("amt_pct")},
    }


def pnl_bps(px: float | None, entry_px: float | None) -> float | None:
    if not px or not entry_px:
        return None
    return round((px / entry_px - 1) * 1e4, 1)


def eval_exit_triggers(feat: dict[str, Any], entry_px: float | None, cfg: dict[str, Any]) -> dict[str, Any]:
    """持倉的 E1/E2。E 系列對「做多持倉」才有意義(反向=對持有部位不利)。"""
    t = cfg["triggers"]
    pnl = pnl_bps(feat.get("px"), entry_px)
    ms = mirror_short(feat, cfg)
    e1 = bool(ms["flow_met"] and pnl is not None and pnl <= -t["e1_price_below_entry_bps"])
    return {
        "pnl_bps": pnl,
        "E1": {"met": e1, "rule": f"流量鏡像(③④)成立 ∧ 現價低於進場價 ≥{t['e1_price_below_entry_bps']}bps", "flow_mirror": ms["flow_met"], "pnl_bps": pnl},
        "E2": {"met": ms["all_met"], "rule": "做空鏡像規則五條全成立(做多篩選欄位反向)", "conds": ms["conds"], "values": ms["values"]},
        "price_only_mirror": ms["price_only_met"],
    }


def eval_cancel_triggers(feat: dict[str, Any], entry_feat: dict[str, Any], age_min: float,
                         basket_10m_bps: float | None, stock_10m_bps: float | None, cfg: dict[str, Any]) -> dict[str, Any]:
    t = cfg["triggers"]
    big_now, big_then = feat.get("big30_wan"), (entry_feat or {}).get("big30_wan")
    c1 = bool(age_min >= t["c1_min_age_min"] and big_now is not None and big_now < 0 and (big_then is None or big_then > 0))
    stale = feat.get("last_trade_age_sec")
    chg = feat.get("chg_pct")
    c2 = bool((chg is not None and chg >= t["limit_up_pct"]) or (stale is not None and stale >= t["c2_stale_min"] * 60))
    c3 = bool(basket_10m_bps is not None and stock_10m_bps is not None
              and basket_10m_bps <= t["c3_basket_10m_bps"] and stock_10m_bps < basket_10m_bps)
    return {
        "C1": {"met": c1, "rule": f"掛單≥{t['c1_min_age_min']}分 ∧ 30分大戶淨額由正轉負", "age_min": round(age_min, 1), "big30_wan_now": big_now, "big30_wan_entry": big_then},
        "C2": {"met": c2, "rule": f"漲停鎖死(≥{t['limit_up_pct']}%) 或 近{t['c2_stale_min']}分無成交", "chg_pct": chg, "last_trade_age_sec": stale},
        "C3": {"met": c3, "rule": f"籃子10分≤{t['c3_basket_10m_bps']}bps ∧ 該檔弱於籃子", "basket_10m_bps": basket_10m_bps, "stock_10m_bps": stock_10m_bps},
    }


# ───────────────────────── 儀表板列(讀 HTML 其他欄位) ─────────────────────────

_TAG = re.compile(r"<[^>]+>")


def _txt(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", s))).strip()


def parse_dashboard_rows(html: str, sids: set[str]) -> dict[str, dict[str, str]]:
    """把 /frag 的表格解析成 {sid: {欄名: 文字}},只留指定標的、略過「—」空欄。欄名取 <th> 第一段文字(去掉小字說明)。"""
    heads = [_txt(re.sub(r"<span[^>]*>.*?</span>", "", h, flags=re.S)) for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)]
    out: dict[str, dict[str, str]] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        m = re.search(r"/stock\?sid=(\w+)", row)
        if not m or m.group(1) not in sids:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        rec: dict[str, str] = {}
        for h, c in zip(heads, cells):
            v = _txt(c)
            if v and v != "—" and h not in ("筆記", ""):
                rec[h[:24]] = v[:140]
        out[m.group(1)] = rec
    return out


# ───────────────────────── 輸出 schema 與護欄 ─────────────────────────

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "insufficient_data": {"type": "boolean"},
        "summary": {"type": "string"},
        "market": {"type": "object", "properties": {"view": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["view"]},
        "positions": {"type": "array", "items": {"type": "object", "properties": {
            "sid": {"type": "string"}, "kind": {"type": "string", "enum": ["position", "order"]},
            "verdict": {"type": "string", "enum": ["hold", "keep", "cancel", "exit", "no_data"]},
            "confidence": {"type": "number"}, "reason": {"type": "string"},
            "facts": {"type": "array", "items": {"type": "string"}},
            "inferences": {"type": "array", "items": {"type": "string"}},
            "thesis_status": {"type": "string"}}, "required": ["sid", "kind", "verdict", "reason"]}},
        "actions": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["cancel", "exit", "switch_off"]},
            "target": {"type": "string", "description": "cancel 只填狀態包 orders 內的 order_no 原值(不加股名/說明);exit 只填持倉 sid;switch_off 填 all"},
            "reason": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}},
            "trigger_id": {"type": "string", "description": "單一觸發代號,例如 C1;多條同時成立時只填最能支持的那一條,其餘寫進 evidence"}},
            "required": ["type", "target", "reason", "evidence"]}},
        "no_action_reason": {"type": "string"},
        "position_notes": {"type": "object", "additionalProperties": {"type": "string"}},
        "next_watch": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["insufficient_data", "summary", "market", "positions", "actions", "next_watch"],
}


def resolve_order(target: Any, orders: list[dict[str, Any]]) -> dict[str, Any] | None:
    """LLM 的 target 常夾帶股名/說明(實測 2026-10-06 兩筆真提案皆如此)。先精確比對,再從文字抽出
    英數 token 比對 order_no/sid/intent_id;唯一命中才算,多筆命中視為對不上(寧可不撤)。"""
    t = str(target or "").strip()
    hit = [o for o in orders if o.get("order_no") == t or o.get("sid") == t]
    if hit:
        return hit[0]
    toks = set(re.findall(r"[A-Za-z0-9]+", t))
    hit = [o for o in orders if str(o.get("order_no")) in toks or str(o.get("sid")) in toks or str(o.get("intent_id")) in toks]
    return hit[0] if len({h.get("order_no") for h in hit}) == 1 else None


def split_trigger_ids(tid: Any) -> list[str]:
    return [x for x in re.split(r"[,\s/+、]+", str(tid or "")) if x]


def triggers_all_met(item: dict[str, Any], tid: Any) -> bool:
    """trigger_id 可能是 "C1,C3":所列的每一條都必須 met(宣稱什麼就要成立什麼)。"""
    ids = split_trigger_ids(tid)
    trig = item.get("triggers", {})
    return bool(ids) and all((trig.get(i) or {}).get("met") for i in ids)


def guard_action(action: dict[str, Any], pack: dict[str, Any], review: dict[str, Any]) -> str | None:
    """提案護欄:回 None=通過,否則回拒絕原因。未來階段由小腦在執行前再跑一次。"""
    typ, target = action.get("type"), str(action.get("target") or "")
    if review.get("insufficient_data"):
        return "insufficient_data"
    if (pack.get("data_age_sec") or 0) > 90:
        return "stale_data"
    if len(action.get("evidence") or []) < 2:
        return "evidence<2"
    if typ == "switch_off":
        return None
    tid = action.get("trigger_id")
    if typ == "exit":
        pos = next((p for p in pack.get("positions", []) if p["sid"] == target), None)
        if not pos:
            return "target_not_in_positions"
        if pos.get("manual_hold"):
            return "manual_hold"
        if not tid or not (pos.get("triggers", {}).get(tid) or {}).get("met"):
            return "trigger_not_met"
        return None
    if typ == "cancel":
        od = resolve_order(target, pack.get("orders", []))
        if not od:
            return "target_not_in_orders"
        if not od.get("ours"):
            return "not_ours"
        if not triggers_all_met(od, tid):
            return "trigger_not_met"
        return None
    return "unknown_type"


# ───────────────────────── 日誌 / 記憶 / 看法 / 指令 ─────────────────────────

def journal_path(day: str) -> Path:
    return agent_dir() / f"agent_journal_{day}.jsonl"


def journal_append(day: str, rec: dict[str, Any]) -> None:
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), **rec}
    with open(journal_path(day), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_journal(day: str) -> list[dict[str, Any]]:
    p = journal_path(day)
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def state_path() -> Path:
    return agent_dir() / "agent_state.json"


def read_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"notes": {}, "last_next_watch": [], "last_review_at": None, "yesterday_review": None}


def write_state(st: dict[str, Any]) -> None:
    atomic_write(state_path(), st)


def view_path() -> Path:
    return agent_dir() / "clone_agent_view.json"


def write_view(view: dict[str, Any]) -> None:
    atomic_write(view_path(), view)


def read_view() -> dict[str, Any] | None:
    try:
        return json.loads(view_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def directives_path() -> Path:
    return agent_dir() / "agent_directives.md"


def read_directives() -> str:
    p = directives_path()
    if not p.exists():
        p.write_text("# 給監督員的指令(每輪必讀,最高優先但不得突破權限)\n\n(目前無)\n", encoding="utf-8")
    return p.read_text(encoding="utf-8")[:4000]
