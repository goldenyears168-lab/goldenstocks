#!/usr/bin/env python
"""撤單神器 worker · 常駐；預設全關（開關檔 enabled=false 時只維持心跳）。唯一可能碰券商的動作是 cancel。

用法： PYTHONPATH=src .venv-fubon/bin/python scripts/order/run_cancel_guard.py [--once]
  開關：scripts/order/cancel_guard_switch.py status|alert|on|off
  實彈鎖：.env ORDER_CANCEL_GUARD_DRY_RUN=0 且 ORDER_MASTER_ENABLED=1（皆預設不開）
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
from datetime import datetime

from order.cancel_guard import (ORDER_DIR, TZ, DryBroker, FubonBroker, Guard, env_dry_run, load_switch, mode_of)
from project_dotenv import load_project_dotenv

_STOP = False
WINDOW = ("08:40", "13:50")
TICK_SEC = 0.2


def _on_signal(_s: int, _f: object) -> None:
    global _STOP
    _STOP = True


def _atomic(path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _emit(recs: list[dict], day: str) -> None:
    if not recs:
        return
    ORDER_DIR.mkdir(parents=True, exist_ok=True)
    with open(ORDER_DIR / f"cancel_guard_log_{day}.jsonl", "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    trig = [r for r in recs if r.get("event") == "trigger"]
    if not trig:
        return
    ap = ORDER_DIR / "cancel_guard_alerts.json"
    try:
        old = json.loads(ap.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        old = []
    _atomic(ap, (old + trig)[-50:])
    for r in trig:
        print(json.dumps(r, ensure_ascii=False), flush=True)
        try:
            from notify_email import send_alert
            send_alert(f"[撤單神器] {r['code']} {r['side']} {r['price']:g} 中價往不利 {r['against_ticks']}tick → {r['action']}",
                       json.dumps(r, ensure_ascii=False, indent=1))
        except Exception as e:  # noqa: BLE001
            print(f"notify failed: {e!r}", file=sys.stderr, flush=True)


def run(once: bool = False) -> int:
    load_project_dotenv()
    signal.signal(signal.SIGTERM, _on_signal); signal.signal(signal.SIGINT, _on_signal)
    dry = env_dry_run()
    guard = Guard(broker=DryBroker() if dry else FubonBroker())
    print(json.dumps({"event": "worker_start", "dry_run_env": dry, "switch": load_switch()}, ensure_ascii=False), flush=True)
    last_beat = 0.0
    while not _STOP:
        now = datetime.now(TZ)
        sw = load_switch(); mode = mode_of(sw, dry)
        if not (now.weekday() < 5 and WINDOW[0] <= now.strftime("%H:%M") <= WINDOW[1]):
            time.sleep(30); 
            if once:
                break
            continue
        guard.set_day(now.strftime("%Y-%m-%d"))
        try:
            _emit(guard.step(sw, dry), guard.day)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            time.sleep(2)
        if time.time() - last_beat > 5:
            _atomic(ORDER_DIR / "cancel_guard_heartbeat.json", {
                "ts": now.isoformat(timespec="seconds"), "pid": os.getpid(), "mode": mode, "dry_run_env": dry,
                "open_orders": sum(o.state == "open" for o in guard.orders.values()),
                "triggered": sum(o.state == "triggered" for o in guard.orders.values()), "cancels_today": guard.cancels_today})
            last_beat = time.time()
        if once:
            break
        time.sleep(TICK_SEC)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--once", action="store_true")
    raise SystemExit(run(ap.parse_args().once))
