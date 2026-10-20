"""/health:各資料來源的新鮮度(最新資料的年齡),與管線是否成功分開。純讀檔,不連外。
等級:fresh(新鮮)/ stale(過舊)/ missing(交易時段內沒有資料)/ idle(非交易時段)。"""
from __future__ import annotations

import json
import os
import time

from biglot import futflow_view as F


def _file_age(path):
    try:
        return round(time.time() - os.path.getmtime(path), 1)
    except OSError:
        return None


def health() -> dict:
    now = F._now()
    status, in_session = F.market_status(now)
    out = {"now": now.isoformat(timespec="seconds"), "market": status, "futflow": {}, "futflow_ready": bool(F._LOADING.get("done"))}
    for root in F.ROOTS:
        b = F._BOOKS.get(root)
        last_ts = b.last_ts if b else ""
        fr, age = F.freshness(last_ts, in_session, now)
        files = F._files(root)
        out["futflow"][root] = {"freshness": fr, "age_sec": age, "last_trade_ts": last_ts, "latest_file": os.path.basename(files[-1]) if files else None,
                                "latest_file_age_sec": _file_age(files[-1]) if files else None, "checkpoint_age_sec": _file_age(F._ckpt_path(root)),
                                "sessions_loaded": len(b.sessions) if b else 0}
    worst = [v["freshness"] for r, v in out["futflow"].items() if r in F.CORE_ROOTS]    # 台積電期貨夜盤本來就稀疏,不拿來判斷整體是否中斷
    out["overall"] = "stale" if "stale" in worst else ("missing" if "missing" in worst else ("idle" if all(w == "idle" for w in worst) else "fresh"))
    return out


def health_json() -> bytes:
    return json.dumps(health(), ensure_ascii=False, indent=1).encode("utf-8")
