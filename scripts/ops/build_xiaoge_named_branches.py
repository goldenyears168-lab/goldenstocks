#!/usr/bin/env python3
"""由 docs/warrant-xiaoge/key-branches.json 產生每日抓取用的具名分點清單 config/xiaoge_named_branches.json。

只收 repo_match=exact 的條目（有 securities_trader_id 可對照）；一個條目可能對應多個分點代號（如國泰-敦南 8888／8889／888V）。
用法：python3 scripts/ops/build_xiaoge_named_branches.py
"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs/warrant-xiaoge/key-branches.json"
DST = ROOT / "config/xiaoge_named_branches.json"


def _first(v):
    if isinstance(v, list):
        return v[0] if v else ""
    return v or ""


def main():
    j = json.loads(SRC.read_text(encoding="utf-8"))
    out, seen = [], set()
    for e in j:
        if e.get("repo_match") != "exact":
            continue
        pairs = re.findall(r"(?:^|[、，,;；\s])([0-9A-Za-z]{4})\s+([^、，,;；\s/]+)", e.get("repo_match_note", ""))
        ids = []
        for tid, nm in pairs:
            if tid not in ids:
                ids.append(tid)
        if not ids:
            continue
        out.append({
            "name": e["branch"], "ids": ids, "role": e.get("role"), "kind": e.get("kind"),
            "stocks": e.get("stocks") or [], "says": _first(e.get("xiaoge_says") or e.get("behavior"))[:120],
            "mentions": e.get("mention_count"),
        })
        seen.update(ids)
    DST.write_text(json.dumps({"generated_from": str(SRC.relative_to(ROOT)), "branches": out,
                               "all_ids": sorted(seen)}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out)} 條目、{len(seen)} 個分點代號 → {DST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
