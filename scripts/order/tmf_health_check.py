#!/usr/bin/env python3
"""TMF micro-channel order-layer health check · read-only, no live calls required.

Checks the things that actually broke live on 2026-08-08 (silently — nothing
crashed, nothing paged anyone): a data source going stale, an NQ-gate anchor
bug, and a regime-flicker place/cancel thrash — none of which show up as a
python exception or a launchd failure. Reads the worker's own JSON log +
ledger; does not touch the broker.

Usage:
  PYTHONPATH=src .venv-fubon/bin/python scripts/order/tmf_health_check.py
  PYTHONPATH=src .venv-fubon/bin/python scripts/order/tmf_health_check.py --minutes 60 --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from stock_db import DATA_DIR, LOGS_DIR  # noqa: E402

_TZ = ZoneInfo("Asia/Taipei")


def _parse_log(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    dec = json.JSONDecoder()
    out: list[dict] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] != "{":
            i += 1
        if i >= n:
            break
        try:
            obj, end = dec.raw_decode(text, i)
            out.append(obj)
            i = end
        except json.JSONDecodeError:
            i += 1
    return out


def _recent_log_entries(minutes: int) -> list[dict]:
    now = datetime.now(tz=_TZ)
    cutoff = (now - timedelta(minutes=minutes)).isoformat()
    entries: list[dict] = []
    for d in (now.date(), (now - timedelta(days=1)).date()):
        path = LOGS_DIR / "intraday" / f"tmf_channel_live_{d.strftime('%Y%m%d')}.log"
        entries.extend(_parse_log(path))
    entries = [e for e in entries if (e.get("broadcast") or {}).get("asof", "") >= cutoff]
    entries.sort(key=lambda e: (e.get("broadcast") or {}).get("asof", ""))
    return entries


def _worker_process_alive() -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "run_tmf_channel_worker.py"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def _thrash_windows(entries: list[dict], *, window_min: int = 15, min_cycles: int = 4) -> list[dict]:
    """Flag (side, price) pairs that place+cancel repeatedly inside a short window."""
    events = []
    for e in entries:
        asof = (e.get("broadcast") or {}).get("asof", "")
        for a in e.get("actions") or []:
            if a.get("kind") in ("place", "cancel"):
                events.append((asof, a.get("side"), a.get("price"), a.get("kind")))
    events.sort()
    flagged: list[dict] = []
    seen_keys: set[tuple] = set()
    for idx, (asof, side, price, kind) in enumerate(events):
        if price is None or not asof:
            continue
        key = (side, price)
        try:
            t0 = datetime.fromisoformat(asof)
        except ValueError:
            continue
        window_end = t0 + timedelta(minutes=window_min)
        cycles = sum(
            1
            for asof2, side2, price2, kind2 in events[idx:]
            if side2 == side
            and price2 == price
            and datetime.fromisoformat(asof2) <= window_end
        )
        if cycles >= min_cycles and key not in seen_keys:
            seen_keys.add(key)
            flagged.append({"side": side, "price": price, "cycles_in_window": cycles, "first_seen": asof})
    return flagged


def run(*, minutes: int = 30) -> dict:
    checks: list[dict] = []

    def add(name: str, status: str, detail: str):
        checks.append({"check": name, "status": status, "detail": detail})

    alive = _worker_process_alive()
    add("worker_process", "PASS" if alive else "FAIL", "running" if alive else "no run_tmf_channel_worker.py process found")

    entries = _recent_log_entries(minutes)
    if not entries:
        add("recent_activity", "WARN", f"no log entries in the last {minutes} min (outside trade window, or worker not writing)")
    else:
        last = entries[-1]
        last_b = last.get("broadcast") or {}
        try:
            last_asof = datetime.fromisoformat(last_b.get("asof", ""))
            age_sec = (datetime.now(tz=_TZ) - last_asof).total_seconds()
        except ValueError:
            age_sec = None
        if age_sec is not None and age_sec > 120:
            add("recent_activity", "FAIL", f"last reconcile {age_sec:.0f}s ago (>120s stale)")
        else:
            add("recent_activity", "PASS", f"last reconcile {age_sec:.0f}s ago" if age_sec is not None else "asof unparseable")

        n_err = sum(1 for e in entries if e.get("ok") is False)
        err_rate = n_err / len(entries)
        status = "FAIL" if err_rate > 0.5 else ("WARN" if err_rate > 0.1 else "PASS")
        add("error_rate", status, f"{n_err}/{len(entries)} polls not ok ({err_rate:.0%}) in last {minutes}min")

        try:
            from order.tmf_channel_pv16_book import RECIPE_VERSION

            versions = {last_b.get("recipe_version") for e in entries[-5:] for last_b in [(e.get("broadcast") or {})]}
            versions.discard(None)
            versions.discard("")
            if not versions:
                add("recipe_version", "WARN", "no recipe_version in recent entries")
            elif versions == {RECIPE_VERSION}:
                add("recipe_version", "PASS", f"live matches code: {RECIPE_VERSION}")
            else:
                add("recipe_version", "FAIL", f"live={versions} != code RECIPE_VERSION={RECIPE_VERSION} (worker needs cutover)")
        except Exception as exc:  # noqa: BLE001
            add("recipe_version", "WARN", f"could not check: {exc}")

        nq_vals = [(e.get("broadcast") or {}).get("nq_gate") for e in entries]
        nq_none_rate = sum(1 for v in nq_vals if v in (None, "none")) / len(nq_vals) if nq_vals else 0.0
        status = "WARN" if nq_none_rate > 0.9 and len(entries) >= 10 else "PASS"
        add("nq_gate_availability", status, f"none/unavailable {nq_none_rate:.0%} of last {len(entries)} polls")

        thrash = _thrash_windows(entries)
        if thrash:
            worst = max(thrash, key=lambda t: t["cycles_in_window"])
            add(
                "order_thrash",
                "FAIL",
                f"{len(thrash)} price level(s) cycled place/cancel >=4x within 15min "
                f"(worst: {worst['side']}@{worst['price']} x{worst['cycles_in_window']})",
            )
        else:
            add("order_thrash", "PASS", "no repeated place/cancel at the same price")

        last_ledger_fields = last
        killed = ((last_ledger_fields.get("broadcast") or {}).get("killed"))
        add("kill_switch", "FAIL" if killed else "PASS", "ledger killed=True" if killed else "not killed")

        hold_min = last.get("position_hold_min")
        if hold_min is not None:
            status = "FAIL" if hold_min > 120 else ("WARN" if hold_min > 60 else "PASS")
            add("position_hold_time", status, f"open position held {hold_min:.1f} min")
        else:
            add("position_hold_time", "PASS", "flat")

        mode = "LIVE" if not last_b.get("dry_run") else "DRY_RUN"
        add("mode", "INFO", f"{mode} (order_enabled={last_b.get('order_enabled')}, auto_submit={last_b.get('auto_submit')})")

    ledger_path = DATA_DIR / "order" / "tmf_channel_ledger.json"
    if ledger_path.is_file():
        try:
            ledger = json.loads(ledger_path.read_text())
            api_day = int(ledger.get("api_calls_day") or 0)
            add("api_budget", "INFO", f"api_calls_day={api_day}")
        except Exception as exc:  # noqa: BLE001
            add("ledger_readable", "WARN", f"could not parse ledger: {exc}")

    overall = "FAIL" if any(c["status"] == "FAIL" for c in checks) else (
        "WARN" if any(c["status"] == "WARN" for c in checks) else "PASS"
    )
    return {"overall": overall, "checked_at": datetime.now(tz=_TZ).isoformat(), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TMF order-layer health check")
    ap.add_argument("--minutes", type=int, default=30, help="lookback window (default 30)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    result = run(minutes=args.minutes)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"=== TMF order layer health · {result['checked_at']} · overall={result['overall']} ===")
        for c in result["checks"]:
            print(f"  [{c['status']:4}] {c['check']}: {c['detail']}")
    return 1 if result["overall"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
