"""複製人代理人 · 出場規則（小哥方法）。純判斷＋讀檔，不連券商、不下單。

SSOT = config/clone_agent_exit.json（來源：權證小哥 knowledge-book S16/V10、R61b、T-75～T-77）。
小哥的停損：「我大部分的抓停損，是抓買賣盤的竭盡點」；多單的反向竭盡＝買盤竭盡（連次≥+10 紅量後突然轉綠、
量大、價漲不動）；最大停損大概 5%；日內波 13:15 前平倉。竭盡事件由儀表板 `biglot.streak` 寫進
`data/biglot_forward/exhaustion_events.jsonl`（JSON 契約，本層不 import 儀表板）。

出場優先序（先到先出）：time_flat（13:15）＞ stop_loss（-5%）＞ reverse_exhaustion。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "clone_agent_exit.json"


def load_policy(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or POLICY_PATH).read_text(encoding="utf-8"))


def _data_root() -> Path:
    from clone_agent.approval import _data_dir
    return _data_dir().parent


def events_path() -> Path:
    return _data_root() / "biglot_forward" / "exhaustion_events.jsonl"


def read_exhaustion_events(day: str, path: Path | None = None) -> list[dict[str, Any]]:
    p = path or events_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("date") == day and isinstance(r.get("t"), (int, float)):
            out.append(r)
    return out


def event_epoch(day: str, t_sec: float) -> float:
    d = datetime.fromisoformat(day).replace(tzinfo=TZ)
    return d.timestamp() + float(t_sec)


def last_bid(root: str, day: str, now_ts: float, max_age_sec: float, cache_dir: Path | None = None) -> float | None:
    """期貨買一（books 檔最後一筆有效、未 stale、夠新）。讀不到/太舊 → None（呼叫端不賣，fail-closed）。"""
    base = cache_dir or (_data_root() / "cache")
    for cand in (root.lower(), root.lower() + "f"):
        path = base / f"{cand}_books" / f"{cand}_books_{day}.jsonl"
        if not path.exists():
            continue
        try:
            size = path.stat().st_size
            with open(path, "rb") as fh:
                fh.seek(max(0, size - 65536))
                lines = fh.read().decode("utf-8", "ignore").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            if '"bids"' not in line:
                continue
            try:
                r = json.loads(line)
                ts = datetime.fromisoformat(r["ts"]).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if r.get("stale"):
                continue
            bids = [float(x["price"]) for x in (r.get("bids") or []) if float(x.get("price") or 0) > 0]
            if not bids:
                continue
            return max(bids) if now_ts - ts <= max_age_sec else None
        return None
    return None


def evaluate_exit(entry: dict[str, Any], bid: float | None, now_ts: float, events: list[dict[str, Any]],
                  policy: dict[str, Any]) -> dict[str, Any] | None:
    """對一筆持有中的多單回傳 {"reason", ...} 或 None。bid 為 None 時只可能因時限出場。"""
    now = datetime.fromtimestamp(now_ts, TZ)
    if now.strftime("%H:%M") >= policy["time_flat_hm"]:
        return {"reason": "time_flat", "at_hm": now.strftime("%H:%M")}
    px = entry.get("price")
    if bid is None or not isinstance(px, (int, float)) or px <= 0:
        return None
    pnl_pct = (bid - float(px)) / float(px) * 100
    if pnl_pct <= -float(policy["stop_loss_pct"]):
        return {"reason": "stop_loss", "pnl_pct": round(pnl_pct, 2)}
    sig = policy["exit_signal"]
    if sig.get("when", "underwater_only") == "underwater_only" and pnl_pct >= 0:
        return None
    held = float(entry.get("held_since") or 0)
    sid = str(entry.get("sid") or "")
    for ev in events:
        if str(ev.get("sid")) != sid or ev.get("d") != 1:
            continue
        ev_ts = event_epoch(str(ev["date"]), ev["t"])
        if ev_ts <= held or now_ts - ev_ts > float(sig["event_max_age_sec"]):
            continue
        return {"reason": "reverse_exhaustion", "pnl_pct": round(pnl_pct, 2), "event_t": ev["t"], "event_px": ev.get("px")}
    return None
