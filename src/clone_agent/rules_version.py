"""代理人規則版本（SSOT = config/clone_agent_rules.json）。

改任何門檻／名單／資金／成本參數 → 必須改 version，否則 fingerprint 會變但版本號沒變，測試會擋。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "clone_agent_rules.json"
_META = ("status", "frozen_on", "backtest_window_start")


def load_rules(path: Path | None = None) -> dict:
    return json.loads((path or RULES_PATH).read_text(encoding="utf-8"))


def fingerprint(rules: dict) -> str:
    body = {k: v for k, v in rules.items() if k not in _META}
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:10]


def label(rules: dict | None = None) -> str:
    r = rules or load_rules()
    return f"{r['id']} v{r['version']} · {fingerprint(r)}"


def universe_sids(rules: dict | None = None) -> set[str]:
    r = rules or load_rules()
    return {m["sid"] for m in r["universe"]["members"]}
