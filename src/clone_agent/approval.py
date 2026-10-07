"""複製人代理人 —— 候選卡與裁決帳本的純邏輯層。

2026-10-06 起沒有待批准卡片頁、也沒有批准／略過按鈕（實單由下單層自動送出）。
本模組保留：候選讀取（下單層與研究腳本使用）、`intent_id`、裁決帳本讀寫（下單層仍會讀既有的「略過」）。

硬邊界：本模組不 import 任何富邦 SDK，也不 import `src/order/`。

帳本位置：`${GOLDENSTOCKS_DATA_DIR}/data/clone_agent/`
  · `candidates_YYYY-MM-DD.jsonl` —— 代理人產生的候選（唯讀，由產生器寫）
  · `decisions_YYYY-MM-DD.jsonl`  —— 裁決（append-only；現在沒有 UI 寫入）
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

SKIP_REASONS = [
    "不同意條件判斷（族群看錯）",
    "不同意條件判斷（大戶數字看錯）",
    "掛價太遠／掛不到",
    "掛價太近／不划算",
    "保證金不夠／已滿檔",
    "這檔我不碰",
    "時機不對（想再等）",
    "其他",
]


def _data_dir() -> Path:
    root = os.environ.get("GOLDENSTOCKS_DATA_DIR")
    base = Path(root) if root else Path(__file__).resolve().parents[2]
    d = base / "data" / "clone_agent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def intent_id(c: dict[str, Any]) -> str:
    key = f"{c.get('day')}|{c.get('time')}|{c.get('symbol')}|{c.get('side')}|{c.get('price')}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def load_candidates(day: str) -> list[dict[str, Any]]:
    p = _data_dir() / f"candidates_{day}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        c["intent_id"] = c.get("intent_id") or intent_id(c)
        out.append(c)
    return out


def load_decisions(day: str) -> dict[str, dict[str, Any]]:
    """後寫的覆蓋先寫的 —— 同一個 intent_id 只算最後一次裁決。"""
    p = _data_dir() / f"decisions_{day}.jsonl"
    if not p.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("intent_id"):
            out[d["intent_id"]] = d
    return out


def record_decision(day: str, iid: str, decision: str, skip_reason: str = "",
                    edited: dict[str, Any] | None = None) -> dict[str, Any]:
    """寫入一筆裁決。**這個函式不會送出任何委託單**，只寫帳本。

    `iid` 必須存在於當天的候選檔，否則拒絕。沒有這道檢查的話，任何字串都會被寫進
    帳本，而 `agreement_stats()` 的分母是候選數、分子是比對得上的裁決數 —— 垃圾列
    會讓同意率看起來比實際低，而 A→C 升級門檻（≥80%）就是靠這個數字。
    """
    if decision not in ("approve", "skip"):
        raise ValueError(f"bad decision {decision!r}")
    if iid not in {c["intent_id"] for c in load_candidates(day)}:
        raise ValueError(f"unknown intent_id {iid!r} for {day}")
    rec: dict[str, Any] = {
        "intent_id": iid, "decision": decision,
        "skip_reason": skip_reason if decision == "skip" else "",
        "decided_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    # 他改了什麼比他按了什麼更有價值 —— 改動量就是「規則離他的腦袋還差多遠」的量尺，
    # 所以只記真的跟建議值不同的欄位，沒改的不寫（避免帳本充滿雜訊）。
    if edited:
        c = next((x for x in load_candidates(day) if x["intent_id"] == iid), {})
        diff = {}
        for k, cast in (("price", float), ("lot", int)):
            if edited.get(k) is None:
                continue
            try:
                v = cast(edited[k])
            except (TypeError, ValueError):
                raise ValueError(f"bad {k}={edited[k]!r}") from None
            if k == "price" and not (0 < v < 100_000):
                raise ValueError(f"price out of range: {v}")
            if k == "lot" and not (1 <= v <= 20):
                raise ValueError(f"lot out of range: {v}")
            # 候選本身沒有這個欄位時，無從判斷「改了沒」—— 不能把預設值當成人改的，
            # 否則帳本會把每一筆都標成有編輯，改動量這個訊號就廢了。
            sug = c.get(k, 1 if k == "lot" else None)
            if sug is not None and abs(v - float(sug)) > 1e-9:
                diff[k] = v
        note = str(edited.get("note") or "").strip()[:300]
        if note:
            diff["note"] = note
        if diff:
            rec["edited"] = diff
    p = _data_dir() / f"decisions_{day}.jsonl"
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def agreement_stats(days: list[str]) -> dict[str, Any]:
    """PRD §1 的升級門檻：同意率 ≥80% 連續 20 個交易日。"""
    n = ok = 0
    per_day = []
    for d in days:
        cs = load_candidates(d)
        ds = load_decisions(d)
        dec = [ds[c["intent_id"]] for c in cs if c["intent_id"] in ds]
        a = sum(1 for x in dec if x["decision"] == "approve")
        n += len(dec); ok += a
        per_day.append({"day": d, "候選": len(cs), "已裁決": len(dec), "同意": a,
                        "同意率": round(100 * a / len(dec), 1) if dec else None})
    return {"總裁決": n, "總同意": ok,
            "同意率": round(100 * ok / n, 1) if n else None, "分日": per_day}
