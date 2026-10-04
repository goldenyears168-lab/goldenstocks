#!/usr/bin/env python3
"""複製人操盤手代理人頁面 —— 獨立 HTTP 服務（預設 port 8772）。

五個分頁：待批准 / 資金水位 / 批准紀錄 / 成交紀錄 / 量化規格。

為什麼獨立跑、不塞進 biglot 儀表板（port 8771）：
  1. **架構邊界**：審核層屬於下單側，研究儀表板屬於展示側。CLAUDE.md 的 import 規則
     要求下單層不被策略／research 腳本 import；反向也該成立——審核層不該被
     儀表板的 render 迴圈綁住生死。
  2. **獨立維護**：儀表板盤中每 5 秒 ingest+render，重啟要 ~60 秒才 bind。審核頁
     要能隨時改隨時重啟，不能因為改一個按鈕就讓盤中大戶流收集斷掉。
  3. **安全**：本服務（含 src/clone_agent/approval.py）**不 import 富邦 SDK、
     不 import src/order/**，構造上無法送單。

跑法：
    GOLDENSTOCKS_DATA_DIR=~/goldenstocks-data PYTHONPATH=src \
      .venv/bin/python scripts/order/run_clone_approval_ui.py

只綁 127.0.0.1（要從手機看請走 Tailscale 轉發，不要改成 0.0.0.0）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, "src")

from clone_agent.approval import (  # noqa: E402
    SKIP_REASONS, agreement_stats, record_decision,
)
from clone_agent.panels import (  # noqa: E402
    render_approvals, render_capital, render_fills, render_pending, render_spec, shell,
)

ROUTES = {"/": render_pending, "/capital": render_capital, "/approvals": render_approvals,
          "/fills": render_fills, "/spec": render_spec}

PORT = int(os.environ.get("CLONE_APPROVAL_PORT", "8772"))
BIND = os.environ.get("CLONE_APPROVAL_BIND", "127.0.0.1")


class H(BaseHTTPRequestHandler):
    def _send(self, body: bytes, code: int = 200, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        q = {k: v[0] for k, v in parse_qs(qs).items()}
        if path == "/stats":
            days = [d for d in str(q.get("days", "")).split(",") if d]
            self._send(json.dumps(agreement_stats(days), ensure_ascii=False, indent=1).encode(),
                       ctype="application/json; charset=utf-8")
            return
        day = str(q.get("day", ""))[:10] or datetime.now().astimezone().strftime("%Y-%m-%d")
        fn = ROUTES.get(path)
        if fn is None:
            self._send(b"not found", 404, "text/plain; charset=utf-8")
            return
        try:
            body = shell(path, fn(day), day)
        except Exception as exc:  # noqa: BLE001
            body = shell(path, f"<h1>render error</h1><pre>{exc!r}</pre>", day)
        self._send(body.encode("utf-8"))

    def do_POST(self):
        if self.path.partition("?")[0] != "/decide":
            self._send(b"not found", 404, "text/plain; charset=utf-8")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            q = json.loads(self.rfile.read(n).decode("utf-8", "replace")[:4000])
            day = str(q["day"])[:10]
            iid = str(q["intent_id"])[:32]
            dec = str(q["decision"])
            sr = str(q.get("skip_reason", ""))[:60]
            if dec == "skip" and sr and sr not in SKIP_REASONS:
                raise ValueError("unknown skip_reason")
            rec = record_decision(day, iid, dec, sr)
            self._send(json.dumps({"ok": True, **rec}, ensure_ascii=False).encode(),
                       ctype="application/json; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            self._send(f"err {exc!r}".encode(), 500, "text/plain; charset=utf-8")

    def log_message(self, *a):
        pass


def main() -> int:
    print(f"clone approval UI on http://{BIND}:{PORT}  (不具送單能力)")
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
