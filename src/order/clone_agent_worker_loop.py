"""複製人代理人實單 worker · KeepAlive 常駐;預設 dry-run(不登入券商)。"""
from __future__ import annotations

import json
import os
import signal
import time
import traceback
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from clone_agent.order_switch import atomic_write, heartbeat_path
from order.clone_agent_order import Config, DryBroker, FubonBroker, load_config, tick
from project_dotenv import load_project_dotenv

TZ = ZoneInfo("Asia/Taipei")
_STOP = False
WINDOW = ("08:55", "13:35")


def _on_signal(signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True


def in_window(now: datetime) -> bool:
    return now.weekday() < 5 and WINDOW[0] <= now.strftime("%H:%M") <= WINDOW[1]


def beat(cfg: Config, **extra: Any) -> None:
    atomic_write(heartbeat_path(), {
        "ts": datetime.now(TZ).isoformat(timespec="seconds"), "pid": os.getpid(),
        "mode": "dry" if cfg.dry_run else "live", "margin_cap": cfg.margin_cap,
        "window": list(cfg.window), **extra})


def run_forever(*, once: bool = False) -> int:
    global _STOP
    _STOP = False
    load_project_dotenv()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    cfg = load_config()
    broker = DryBroker() if cfg.dry_run else FubonBroker()
    print(json.dumps({"event": "worker_start", "mode": "dry" if cfg.dry_run else "live", "cap": cfg.margin_cap,
                      "max_lot": cfg.max_lot}, ensure_ascii=False), flush=True)
    last_sig = None
    while not _STOP:
        now = datetime.now(TZ)
        if not in_window(now):
            beat(cfg, idle=True)
            if once:
                break
            time.sleep(30)
            continue
        try:
            out = tick(broker, cfg, now)
            beat(cfg, **{k: v for k, v in out.items() if k != "day"}, day=out["day"])
            sig = (out["sent"], out["skipped"], out["reconciled"], str(out["halted"]), out["switch"])
            if sig != last_sig and (out["sent"] or out["skipped"] or out["reconciled"] or out["halted"]):
                print(json.dumps({"ts": now.isoformat(timespec="seconds"), **out}, ensure_ascii=False, default=str), flush=True)
            last_sig = sig
        except Exception:  # noqa: BLE001  常駐 worker 不可因單輪例外崩潰
            traceback.print_exc()
            beat(cfg, error=True)
        if once:
            break
        time.sleep(2)
    return 0


def main() -> int:
    import sys
    return run_forever(once="--once" in sys.argv)
