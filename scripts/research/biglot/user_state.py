"""biglot dashboard 重構：使用者手動輸入的持久化狀態——頁首自由筆記(`_load_notes`)、
個股筆記(`_stock_note_td`/`_save_stock_note`)、持倉監控事件日誌(`_hold_log`/`_hold_save`)、
OOS 帳本讀取(`_oos_load`)。

依 docs/biglot-refactor-roadmap.md 訂下的規則搬移：只 `import biglot_dashboard`
（模組本身，不指名字），函式本體內對 `biglot_dashboard.py` 自己定義的全域一律用
`biglot_dashboard.NAME` 屬性存取（`NOTES_PATH`/`DEFAULT_NOTES`/`STOCK_NOTES_PATH`/
`STOCK_NOTES`/`HOLDS_PATH`/`HOLDS`/`OOS_FILE`/`TZ`）——不只是「危險全域」（會被
`ingest()` 整包重新賦值的那 18 個），是全部，因為屬性存取本來就不比 bare name
貴，一律用同一個安全模式比逐一分類「這個要不要小心」更不容易出錯。`DATA_DIR`
例外：它來自 `stock_db`、不是 `biglot_dashboard.py` 自己定義的全域，直接
`from stock_db import DATA_DIR` 匯入。

參見 scripts/research/biglot/xq_style.py 開頭的完整說明（同一套規則第一次
被端到端驗證的地方）。
"""
from __future__ import annotations

import html as html_mod
import json

from stock_db import DATA_DIR

import biglot_dashboard


def _load_notes():
    try:
        s = biglot_dashboard.NOTES_PATH.read_text(encoding="utf-8")
        return s if s.strip() else biglot_dashboard.DEFAULT_NOTES
    except Exception:  # noqa: BLE001
        return biglot_dashboard.DEFAULT_NOTES


def _stock_note_td(sid):
    n = biglot_dashboard.STOCK_NOTES.get(sid) or {}
    txt = html_mod.escape(n.get("txt") or "")
    when = (n.get("t") or "")
    if n.get("d") and n["d"] != biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d"):
        when = f"{n['d'][5:]} {when}"
    return (f"<td class='snote'><span class='ne' contenteditable='true' spellcheck='false' data-sid='{sid}'>{txt}</span>"
            f"<span class='nt dim'>{when}</span></td>")


def _hold_save():
    biglot_dashboard.HOLDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    biglot_dashboard.HOLDS_PATH.write_text(json.dumps(biglot_dashboard.HOLDS, ensure_ascii=False, indent=0), encoding="utf-8")


def _hold_log(rec: dict):
    try:
        f = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"hold_events_{biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime('%Y-%m-%d')}.jsonl"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%H:%M:%S"), **rec}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _save_stock_note(sid, txt):
    now = biglot_dashboard.datetime.now(biglot_dashboard.TZ)
    biglot_dashboard.STOCK_NOTES[sid] = {"txt": txt[:2000], "t": now.strftime("%H:%M:%S"), "d": now.strftime("%Y-%m-%d")}
    biglot_dashboard.STOCK_NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    biglot_dashboard.STOCK_NOTES_PATH.write_text(json.dumps(biglot_dashboard.STOCK_NOTES, ensure_ascii=False, indent=0), encoding="utf-8")
    return biglot_dashboard.STOCK_NOTES[sid]["t"]


def _oos_load():
    try:
        return json.load(open(biglot_dashboard.OOS_FILE))
    except Exception:
        return {"intraday": [], "overnight": [], "overnight_pending": [],
            "overnight_short": [], "overnight_short_pending": []}
