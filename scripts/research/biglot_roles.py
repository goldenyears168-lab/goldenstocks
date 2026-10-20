"""監測名單角色 SSOT 讀取(config/biglot_roles.json)。儀表板與收集器共用;檔案缺失/損毀時退回「無參考列」。"""
from __future__ import annotations

import json
from pathlib import Path

from stock_db import DATA_DIR

ROLES_PATH = Path(__file__).resolve().parents[2] / "config" / "biglot_roles.json"
#: 處置/注意股(有期貨)監測池:disposal_risk_watch.py 收盤後產生,盤中不變(換池一律收盤後)
DISPOSAL_POOL_PATH = DATA_DIR / "disposal" / "disposal_futures_pool.json"
DISPOSAL_GROUP = "處置監測(有期貨)"


def _disposal_pool_rows(path: Path = DISPOSAL_POOL_PATH) -> list[dict]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return [{"sid": r["sid"], "name": r["name"], "group": DISPOSAL_GROUP, "fut_code": r["fut_code"],
                 "note": f"{r['kind']} · {r['label']}"} for r in d.get("stocks", [])]
    except Exception:  # noqa: BLE001 -- 池檔缺失/損毀 = 沒有處置監測列,不影響其他
        return []


def load_roles(path: Path = ROLES_PATH) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- 缺檔不影響收集/儀表板
        return {"reference_in_pool": [], "reference_off_pool": [], "independent": [], "group_strength": {}}
    seen = {r["sid"] for k in ("reference_in_pool", "reference_off_pool", "independent") for r in d.get(k, [])}
    d["reference_off_pool"] = list(d.get("reference_off_pool", [])) + [
        r for r in _disposal_pool_rows() if r["sid"] not in seen]
    return d


def off_pool_sids(path: Path = ROLES_PATH) -> list[str]:
    return [r["sid"] for r in load_roles(path).get("reference_off_pool", [])]
