"""Long-lived momentum-rotation poll worker · single launchd label, reused session.

同構複製 dayflip_short_worker_loop.py 的 KeepAlive worker 骨架（見該檔頂部
docstring 沿革），沿用同一套 session-reuse + 例外時重置 pool + 每日一封告警
dedup 的手法，讓這個 repo 的多支 KeepAlive worker 保持同一種形狀、好一起除錯。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import stock_db
from order.momentum_rotation_order import in_momentum_rotation_trade_window, reconcile_once
from order.momentum_rotation_session_pool import get_fubon_session, pool_stats, reset_session_pool
from order.momentum_rotation_websocket_feed import stop_feed as stop_ws_feed
from project_dotenv import load_project_dotenv

_TZ = ZoneInfo("Asia/Taipei")
_STOP = False

_ALERT_ACTIONS = frozenset({"entered", "preempted", "closed_trail_stop", "closed_force_close", "worker_exception"})
_FAILURE_ACTIONS = frozenset({"worker_exception"})


def _on_signal(signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True
    print(f"momentum-rotation-worker signal {signum} -> stop after tick", flush=True)


def _sleep_sec(*, in_window: bool) -> float:
    key, default = (
        ("ORDER_MOMENTUM_ROTATION_WORKER_INTERVAL", 20.0)
        if in_window
        else ("ORDER_MOMENTUM_ROTATION_WORKER_IDLE", 60.0)
    )
    try:
        val = float(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _hm_now() -> str:
    return datetime.now(tz=_TZ).strftime("%H:%M")


def _tick_log_path() -> Path:
    stamp = datetime.now(tz=_TZ).strftime("%Y%m%d")
    return stock_db.LOGS_DIR / "intraday" / f"momentum_rotation_{stamp}.log"


def _maybe_alert(summary: dict[str, Any], *, ok: bool) -> None:
    action = str(summary.get("action") or "")
    if action not in _ALERT_ACTIONS and ok:
        return
    if os.environ.get("RUN_MOMENTUM_ROTATION_EMAIL", "1").strip().lower() in ("0", "false"):
        return
    stamp = datetime.now(tz=_TZ).strftime("%Y%m%d")
    flag = stock_db.LOGS_DIR / "intraday" / f"momentum_rotation_mail_{stamp}.flag"
    if flag.exists():
        return
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        success = ok and action not in _FAILURE_ACTIONS
        subprocess.run(
            [
                str(stock_db.PROJECT_ROOT / ".venv" / "bin" / "python"),
                str(stock_db.PROJECT_ROOT / "scripts" / "notify_job_result.py"),
                "--subject-prefix", "動能輪動 sleeve",
                "--exit-code", "0" if success else "1",
                "--log-path", str(_tick_log_path()),
                "--extra", json.dumps(summary, ensure_ascii=False, default=str),
                "--env-flag", "RUN_MOMENTUM_ROTATION_EMAIL",
            ],
            check=False,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"alert_error: {exc}", flush=True)


def run_forever(*, once: bool = False) -> int:
    global _STOP
    _STOP = False
    load_project_dotenv()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    print(
        json.dumps(
            {
                "event": "worker_start",
                "ts": datetime.now(tz=_TZ).isoformat(),
                "interval": _sleep_sec(in_window=True),
                "idle": _sleep_sec(in_window=False),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    while not _STOP:
        hm = _hm_now()
        win = in_momentum_rotation_trade_window(hm)
        if not win:
            if once:
                break
            time.sleep(_sleep_sec(in_window=False))
            continue
        t0 = time.monotonic()
        summary: dict[str, Any]
        ok = True
        try:
            get_fubon_session()
            summary = reconcile_once(use_session_pool=True)
        except Exception as exc:  # noqa: BLE001
            reset_session_pool()
            stop_ws_feed()  # session換了，舊feed綁的websocket連線一併明確關閉
            ok = False
            summary = {
                "action": "worker_exception",
                "error": str(exc)[:300],
                "trace": traceback.format_exc()[-500:],
            }
        elapsed = round(time.monotonic() - t0, 2)
        summary["worker_elapsed_sec"] = elapsed
        summary["session_pool"] = pool_stats()
        print(json.dumps(summary, ensure_ascii=False, default=str), flush=True)
        _maybe_alert(summary, ok=ok)
        if once:
            return 0 if ok else 1
        deadline = time.monotonic() + _sleep_sec(in_window=True)
        while not _STOP and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    stop_ws_feed()
    print(
        json.dumps(
            {"event": "worker_stop", "ts": datetime.now(tz=_TZ).isoformat()},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="momentum-rotation long-lived poll worker")
    ap.add_argument("--once", action="store_true", help="single tick then exit")
    args = ap.parse_args(argv)
    return run_forever(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
