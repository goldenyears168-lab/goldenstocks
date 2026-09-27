"""biglot dashboard 重構最後一步：HTTP composition root。`class H`（請求處理器）、
`loop()`（背景 ingest+render 排程迴圈）從 scripts/research/biglot_dashboard.py
逐字搬移，邏輯不改一行；原本檔尾 `if __name__ == "__main__":` 區塊的內容包成這裡
的 `main()`，`biglot_dashboard.py` 對應改成 `from biglot.http_server import main;
main()` 的一行呼叫——這正是 docs/biglot-refactor-roadmap.md 從一開始就設想的
「原檔案降級成薄殼」最終型態。

依賴一律 `import biglot_dashboard` + 屬性存取（`PAGE`/`NAMES`/`TZ`/`datetime`/`ST`/
`HOLDS`/`SHELL`/`NOTES_PATH`/`REFRESH_SEC`/`PORT`），已搬移的函式/常數
（`render_history`/`render_help`/`GRID_SHELL`/`render_grid_frag`/`render_day`/
`render_stock_frag`/`render_stock`/`_load_notes`/`_save_stock_note`/`_hold_toggle`/
`_in_market`/`ingest`/`render`/`snapshot_day`/`_paper_settle`/`_oos_update_at_close`/
`_refresh_vol_risk_if_needed`）直接從各自現在的模組 import。

搬移用跟 render_main.py 同一套 AST 位元組偏移替換腳本，搬移前後用「去除前綴後
逐行比對原始碼」驗證零邏輯差異。
"""
from __future__ import annotations

import html as html_mod
import json
import sys
import threading
import time
import urllib.parse as urllib_parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import biglot_dashboard
from biglot.day_views import render_history, render_day, snapshot_day
from biglot.stock_meta import render_help
from biglot.render_views import GRID_SHELL, render_grid_frag, render_stock_frag, render_stock
from biglot.user_state import _load_notes, _save_stock_note, _hold_toggle, _oos_update_at_close
from biglot.utils import _in_market
from biglot.ingest import ingest
from biglot.render_main import render
from biglot.paper_trading import _paper_settle
from biglot.reference_loaders import _refresh_vol_risk_if_needed


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path == "/frag":
            body = biglot_dashboard.PAGE["frag"].encode("utf-8")
        elif path == "/history":
            body = render_history().encode("utf-8")
        elif path == "/help":
            body = render_help().encode("utf-8")
        elif path == "/grid":
            body = GRID_SHELL.encode("utf-8")
        elif path == "/gridfrag":
            q = {k: v[0] for k, v in urllib_parse.parse_qs(qs).items()}
            body = (biglot_dashboard.PAGE.get("grid") or render_grid_frag(str(q.get("sort", "ind"))[:4])).encode("utf-8") if str(q.get("sort", "ind")) == "ind" else render_grid_frag(str(q.get("sort", "ind"))[:4]).encode("utf-8")
        elif path == "/day":
            d = qs.split("d=")[-1][:10] if "d=" in qs else ""
            body = render_day(d).encode("utf-8")
        elif path in ("/stock", "/stockfrag"):
            q = {k: v[0] for k, v in urllib_parse.parse_qs(qs).items()}
            sid = str(q.get("sid", ""))[:8]
            day = str(q.get("d", ""))[:10] or biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
            if sid not in biglot_dashboard.NAMES:
                body = "<div class='meta'>未知代碼</div>".encode("utf-8")
            elif path == "/stockfrag":
                body = render_stock_frag(sid, day).encode("utf-8")
            else:
                body = render_stock(sid, day).encode("utf-8")
        else:
            body = biglot_dashboard.SHELL.replace("{NOTES}", _load_notes()).encode("utf-8")   # SHELL 是 f-string,{{NOTES}} 已成 {NOTES}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 手機瀏覽器(尤其 Safari over Tailscale)會積極快取整份 HTML,
        # 導致看到舊紀律條+卡在「載入中…」。強制不快取。
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """/notes:儲存頁首可編輯筆記(本機/Tailscale 私網用,內容原樣存 HTML,不做權限控制)。"""
        path, _, _ = self.path.partition("?")
        if path == "/stocknote":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                q = json.loads(self.rfile.read(n).decode("utf-8", "replace")[:10_000])
                sid = str(q.get("sid", ""))[:8]
                if sid not in biglot_dashboard.NAMES:
                    raise ValueError("unknown sid")
                t = _save_stock_note(sid, str(q.get("txt", "")))
                body, code = json.dumps({"ok": True, "t": t}).encode(), 200
            except Exception as exc:  # noqa: BLE001
                body, code = f"err {exc!r}".encode(), 500
        elif path == "/hold":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                q = json.loads(self.rfile.read(n).decode("utf-8", "replace")[:1000])
                sid = str(q.get("sid", ""))[:8]; act = str(q.get("action", ""))
                if sid not in biglot_dashboard.NAMES or act not in ("open", "close"):
                    raise ValueError("bad sid/action")
                _hold_toggle(sid, act, biglot_dashboard.ST.last_px.get(sid))
                body, code = json.dumps({"ok": True, "holds": list(biglot_dashboard.HOLDS)}).encode(), 200
            except Exception as exc:  # noqa: BLE001
                body, code = f"err {exc!r}".encode(), 500
        elif path == "/notes":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8", "replace")[:200_000]
                biglot_dashboard.NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
                biglot_dashboard.NOTES_PATH.write_text(raw, encoding="utf-8")
                body = b"ok"
                code = 200
            except Exception as exc:  # noqa: BLE001
                body, code = f"err {exc!r}".encode(), 500
        else:
            body, code = b"not found", 404
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def loop():
    done_close = False
    while True:
        try:
            if _in_market():
                ingest()
                render()
                done_close = False
                if time.time() - biglot_dashboard.PAGE.get("grid_t", 0) >= 5:
                    try:
                        biglot_dashboard.PAGE["grid"] = render_grid_frag("ind")
                    except Exception as _ge:  # noqa: BLE001
                        print(f"[grid] {_ge!r}", file=sys.stderr)
                    biglot_dashboard.PAGE["grid_t"] = time.time()
            elif not done_close:
                ingest()          # 收盤後補跑一次定格,之後停工
                render()
                try:              # 盤後定格也要有 36 檔加總(台指面板紅/藍/紫線)與總覽快取:先建 AGG 再重繪一次
                    biglot_dashboard.PAGE["grid"] = render_grid_frag("ind")
                    biglot_dashboard.PAGE["grid_t"] = time.time()
                    render()
                except Exception as _ge:  # noqa: BLE001
                    print(f"[grid-close] {_ge!r}", file=sys.stderr)
                snapshot_day()
                try:
                    _paper_settle(biglot_dashboard.ST.date)
                except Exception as _pe:  # noqa: BLE001
                    print(f"[paper-settle] {_pe!r}", file=sys.stderr)
                try:
                    _oos_update_at_close()
                except Exception:
                    pass
                done_close = True
            elif _refresh_vol_risk_if_needed():
                # 盤前/盤後定格期間:tick 沒得更新,但 T-1 籌碼分數只要 DB 有新資料
                # 就該顯示,不用等開盤——重繪一次讓「盤後定格」頁面秀出當天分數
                render()
        except Exception as e:
            biglot_dashboard.PAGE["frag"] = f"<div class='meta'>render error: {html_mod.escape(str(e))}</div>"
        time.sleep(biglot_dashboard.REFRESH_SEC if _in_market() else 300)


def main():
    threading.Thread(target=loop, daemon=True).start()
    print(f"biglot dashboard on :{biglot_dashboard.PORT}")
    ThreadingHTTPServer(("", biglot_dashboard.PORT), H).serve_forever()
