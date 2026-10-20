#!/usr/bin/env python
"""撤單神器開關。 status | off | alert（只通知）| on（符合條件就撤；仍受 .env DRY_RUN 與 ORDER_MASTER_ENABLED 約束）。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from order.cancel_guard import DEFAULT_SWITCH, SWITCH_PATH, TZ, env_dry_run, load_switch, mode_of
from project_dotenv import load_project_dotenv


def main(argv: list[str]) -> int:
    load_project_dotenv()
    cmd = argv[1] if len(argv) > 1 else "status"
    sw = load_switch()
    if cmd in ("off", "alert", "on"):
        sw.update(enabled=cmd != "off", auto_cancel=cmd == "on")
        d = {**{k: sw[k] for k in DEFAULT_SWITCH}, "updated_at": datetime.now(TZ).isoformat(timespec="seconds"), "by": "cli"}
        SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SWITCH_PATH.with_suffix(".tmp"); tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8"); os.replace(tmp, SWITCH_PATH)
        sw = load_switch()
    elif cmd != "status":
        print(__doc__); return 2
    dry = env_dry_run()
    print(json.dumps({"switch": sw, "env_dry_run": dry, "ORDER_MASTER_ENABLED": os.environ.get("ORDER_MASTER_ENABLED", "0"),
                      "effective_mode": mode_of(sw, dry)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
