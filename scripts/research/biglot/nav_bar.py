"""第4部門(服務生產)導覽列:8771 每個整頁共用的頂列,由 http_server 注入 <body> 之後。
主流程 儀表板 → AI分析師 → AI操盤手;其餘是輔助工具。跨埠連結由內嵌腳本用 location.hostname 補。"""
from __future__ import annotations

import html as html_mod
import re

MAIN = (
    ("📈 儀表板", "/", ("/",)),
    ("🧑‍💼 AI分析師", None, ()),
    ("🕹 AI操盤手", "/agent", ("/agent", "/agentrules")),
)
TOOLS = (
    ("處置監測", "/disposal", ("/disposal",)),
    ("小哥筆記本", "/xiaoge", ("/xiaoge",)),
    ("期貨大戶−散戶", "/futflow", ("/futflow",)),
    ("紙上模擬", "/paper", ("/paper",)),
    ("圖形總覽", "/grid", ("/grid",)),
    ("歷史回放", "/timeline", ("/timeline",)),
    ("歷史分頁", "/history", ("/history", "/day")),
    ("故事", "/stories", ("/stories",)),
    ("欄位說明", "/help", ("/help",)),
)
SKIP = {"/assets/ui.css", "/assets/live.js", "/health", "/frag", "/gridfrag", "/agentfrag", "/timelinefrag", "/stockfrag", "/agentsig", "/agentjs", "/futflowfrag"}

CSS = (
    '<link rel="stylesheet" href="/assets/ui.css?v=1">'   # 設計代幣(docs/ui-standard.md);頁面以 var(--名稱,備援值) 引用
    "<style>"
    ".gnav{display:flex;flex-wrap:wrap;align-items:center;gap:4px 14px;margin:-2px 0 8px;padding:6px 10px;"
    "background:#161b22;border:1px solid #30363d;border-radius:8px;font-size:12px}"
    ".gnav a{color:#8b949e;text-decoration:none;padding:2px 8px;border-radius:5px;white-space:nowrap}"
    ".gnav a:hover{color:#e6edf3;background:#21262d}"
    ".gnav .back{color:#79c0ff}"
    ".gnav .dept{color:#e3b341;font-weight:600;padding:0}"
    ".gnav .main{display:flex;gap:2px;align-items:center}"
    ".gnav .main a{font-size:13px;color:#c9d1d9}"
    ".gnav .main a+a::before{content:'→';color:#484f58;margin-right:10px;margin-left:-2px;display:inline-block}"
    ".gnav .tools{display:flex;flex-wrap:wrap;gap:0 2px;align-items:center;margin-left:auto}"
    ".gnav .tools i{font-style:normal;color:#6e7681;font-size:11px;margin-right:4px}"
    ".gnav a.on{background:#1f6feb;color:#fff}"
    ".gnav a.badge{color:#ffa657}"
    ".gnav b{color:#e3b341}"
    "</style>"
)
SCRIPT = (
    "<script>document.querySelectorAll('.gnav a[data-port]').forEach(function(a){"
    "a.href='http://'+location.hostname+':'+a.dataset.port+a.dataset.path})</script>"
)


def _on(path: str, pats: tuple) -> str:
    return " on" if path in pats else ""


def render(path: str, disposal_badge: str = "") -> str:
    main = []
    for label, href, pats in MAIN:
        if href is None:
            main.append(f'<a data-port="8780" data-path="/analyst/" href="#">{label}</a>')
        else:
            extra = '<b id="agbadge" style="display:none"></b>' if href == "/agent" else ""
            main.append(f'<a class="{_on(path, pats).strip()}" href="{href}">{label}{extra}</a>')
    tools = []
    for label, href, pats in TOOLS:
        text = html_mod.escape(label)
        cls = _on(path, pats).strip()
        if href == "/disposal" and disposal_badge:
            text = disposal_badge
            cls = (cls + " badge").strip()
        tools.append(f'<a class="{cls}" href="{href}">{text}</a>')
    return (
        CSS
        + '<nav class="gnav">'
        + '<a class="back" data-port="8780" data-path="/" href="#">← CEO 辦公室</a>'
        + '<span class="dept">第4部門 服務生產</span>'
        + '<span class="main">' + "".join(main) + "</span>"
        + '<span class="tools"><i>工具</i>' + "".join(tools)
        + '<a data-port="8780" data-path="/notices/" href="#">📨 通知</a></span>'
        + "</nav>" + SCRIPT
    )


_BODY = re.compile(rb"<body[^>]*>", re.I)


def inject(path: str, body: bytes, disposal_badge: str = "") -> bytes:
    if path in SKIP:
        return body
    nav = render(path, disposal_badge).encode("utf-8")
    head = body[:200].lstrip().lower()
    if not (head.startswith(b"<!doctype") or head.startswith(b"<html")):
        return nav + body
    m = _BODY.search(body)
    return body[: m.end()] + nav + body[m.end():] if m else body
