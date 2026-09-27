#!/usr/bin/env python3
"""biglot dashboard Phase 0：起一份 fixture 版 dashboard，打完全部路由確認 200。

刻意不呼叫 bd.loop()、不跑 __main__ 區塊——只手動呼叫一次 ingest()/render() 把
PAGE 填好，然後自己開一個 ThreadingHTTPServer 掛 bd.H，確認 HTTP 層本身沒壞掉。
Port 預設 8772，只 bind 127.0.0.1（不上 Tailscale 介面），跟正式 8771 完全不衝突，
可以在正式 dashboard 跑著的同時執行這支腳本。

用法：
    PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/smoke_test.py \\
        --fixture ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-25
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture_lib import load_dashboard_module, load_entry_points  # noqa: E402

TZ = timezone(timedelta(hours=8))


def _get(base: str, path: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
        return r.status, r.read()


def _post(base: str, path: str, payload: dict) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{base}{path}", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--date", default=None)
    ap.add_argument("--hhmm", default="13:35")
    ap.add_argument("--port", type=int, default=8772,
                     help="刻意跟正式 8771 不同，兩者可同時跑")
    args = ap.parse_args()

    fixture_dir = Path(args.fixture).resolve()
    date = args.date or fixture_dir.name
    hh, mm = (int(x) for x in args.hhmm.split(":"))
    frozen_now = datetime.strptime(date, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=TZ)

    bd = load_dashboard_module(fixture_dir, frozen_now=frozen_now)
    ep = load_entry_points(bd)
    # class H(HTTP 處理器)也搬到 biglot/http_server.py 了，biglot_dashboard 模組
    # 本身不再有這個屬性，比照正式站台直接從真正的家 import。
    from biglot.http_server import H

    ep["ingest"]()
    ep["render"]()
    bd.PAGE["grid"] = ep["render_grid_frag"]("ind")  # /gridfrag?sort=ind 讀這個快取

    server = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{args.port}"

    sid = next(iter(bd.NAMES))
    checks: list[tuple[str, callable]] = [
        ("GET /", lambda: _get(base, "/")),
        ("GET /frag", lambda: _get(base, "/frag")),
        ("GET /grid", lambda: _get(base, "/grid")),
        ("GET /gridfrag?sort=ind", lambda: _get(base, "/gridfrag?sort=ind")),
        ("GET /gridfrag?sort=big", lambda: _get(base, "/gridfrag?sort=big")),
        ("GET /gridfrag?sort=chg", lambda: _get(base, "/gridfrag?sort=chg")),
        ("GET /history", lambda: _get(base, "/history")),
        ("GET /help", lambda: _get(base, "/help")),
        ("GET /day", lambda: _get(base, f"/day?d={date}")),
        ("GET /stock", lambda: _get(base, f"/stock?sid={sid}&d={date}")),
        ("GET /stockfrag", lambda: _get(base, f"/stockfrag?sid={sid}&d={date}")),
        ("POST /stocknote", lambda: _post(base, "/stocknote", {"sid": sid, "txt": "smoke-test"})),
        ("POST /hold open", lambda: _post(base, "/hold", {"sid": sid, "action": "open"})),
        ("POST /hold close", lambda: _post(base, "/hold", {"sid": sid, "action": "close"})),
    ]

    n_fail = 0
    for label, fn in checks:
        try:
            status, body = fn()
            ok = status == 200
            print(f"  {'PASS' if ok else 'FAIL'} {label}: {status} ({len(body)} bytes)")
            n_fail += 0 if ok else 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {label}: {e!r}")
            n_fail += 1

    server.shutdown()
    server.server_close()

    print(f"\n{len(checks) - n_fail}/{len(checks)} 通過")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
