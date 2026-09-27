"""biglot dashboard 重構：`_stock_info_block`/`render_help` 兩個零依賴葉節點，逐字搬移。

`_stock_info_block` 讀 `STOCK_INFO`/`STOCK_BLOCKS`——這兩個名字在 biglot_dashboard.py
頂層是靠一個 `try: from biglot_stock_info import INFO as STOCK_INFO, BLOCKS as STOCK_BLOCKS
... except: STOCK_INFO, STOCK_BLOCKS = {}, {}` 決定最終綁定哪一份物件。這裡不重新
`import biglot_stock_info` 自己判斷 try/except 走哪一支，而是照 docs/biglot-refactor-roadmap.md
訂的規則只 `import biglot_dashboard`，在函式本體用 `biglot_dashboard.STOCK_INFO`/
`biglot_dashboard.STOCK_BLOCKS` 屬性存取——這樣永遠讀到 biglot_dashboard.py 自己那次
try/except 實際解析出的結果，不會有兩份判斷邏輯分岔的風險。

`render_help` 讀 `_HELP_GROUPS`，是 biglot_dashboard.py 的模組層級常數，同樣用
`biglot_dashboard._HELP_GROUPS` 屬性存取。

兩者皆確認零呼叫其他頂層函式（dep_graph.py 靜態依賴圖），可安全獨立搬移。
"""
from __future__ import annotations

import html as html_mod

import biglot_dashboard


def render_help():
    css = ("body{background:#0d1117;color:#c9d1d9;font:13px/1.7 -apple-system,'PingFang TC',"
           "sans-serif;margin:0;padding:14px 16px 40px}"
           "h2{font-size:17px;margin:2px 0 4px}h3{font-size:14px;color:#79c0ff;margin:18px 0 4px;"
           "border-bottom:1px solid #30363d;padding-bottom:3px}"
           "a{color:#79c0ff}.meta{color:#8b949e;font-size:12px;margin-bottom:10px}"
           "table{border-collapse:collapse;width:100%;max-width:1000px;margin:2px 0}"
           "td{border-bottom:1px solid #21262d;padding:5px 8px;vertical-align:top}"
           "td.c{color:#e6edf3;font-weight:600;white-space:nowrap;width:96px}"
           "td.d{color:#adbac7;width:44%}td.u{color:#8b949e}"
           ".up{color:#ff7b72}.dn{color:#3fb950}.leg{background:#161b22;border:1px solid #30363d;"
           "border-radius:6px;padding:8px 12px;margin:10px 0;font-size:12px;max-width:1000px}")
    parts = [f"<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>欄位說明</title><style>{css}</style></head><body>"
             f"<h2>📖 大戶儀表板 · 欄位說明</h2>"
             f"<div class='meta'><a href='/'>← 回儀表板</a>　每欄:定義(怎麼算) / 怎麼讀(用途)。"
             f"顏色慣例:<span class='up'>紅=漲/正值</span>、<span class='dn'>綠=跌/負值</span>(台股慣例)、灰=無資料或不可測。</div>"
             f"<div class='leg'><b>表頭顏色</b>=尺度分層:<span style='color:#e3b341'>黃=5分窗</span>·"
             f"<span style='color:#79c0ff'>藍=30分窗(主尺度)</span>·<span style='color:#d2a8ff'>紫=全日/隔夜</span>。"
             f"<b>列的每5列一條粗分隔線</b>,方便橫向對到同一檔;滑鼠移到列上會整列highlight。</div>"]
    for title, rows in biglot_dashboard._HELP_GROUPS:
        parts.append(f"<h3>{html_mod.escape(title)}</h3><table>")
        for col, dfn, howto in rows:
            parts.append(f"<tr><td class='c'>{html_mod.escape(col)}</td>"
                         f"<td class='d'>{html_mod.escape(dfn)}</td>"
                         f"<td class='u'>{html_mod.escape(howto)}</td></tr>")
        parts.append("</table>")
    parts.append("<div class='leg' style='margin-top:18px'><b>一句紀律</b>:盤中只在已驗證格觸發時喊方向、"
                 "且附數字＋基準率＋t;無觸發＝棄權。方向重倉判斷留到收盤(隔夜候選才進OOS記分)。"
                 "詳見 docs/biglot-broadcast-protocol.md。</div>")
    parts.append("</body></html>")
    return "".join(parts)


def _stock_info_block(sid):
    """詳情頁:公司描述 + 看盤標籤 + 族群看盤核心(靜態,不含訊號)。"""
    rec = biglot_dashboard.STOCK_INFO.get(sid)
    if not rec:
        return ""
    block, desc, tags = rec
    tag_html = "".join(f"<span class='tag'>{html_mod.escape(t)}</span>" for t in tags)
    return ("<div class='sinfo'>"
            f"<span class='blk'>{html_mod.escape(block)}</span>{tag_html}"
            f"<div class='desc'>{html_mod.escape(desc)}</div>"
            f"<div class='core'>族群看盤核心:{html_mod.escape(biglot_dashboard.STOCK_BLOCKS.get(block, ''))}</div></div>"
            "<style>.sinfo{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 10px;"
            "margin-bottom:8px;font-size:12px;line-height:1.6}"
            ".sinfo .blk{color:#d2a8ff;font-weight:700;margin-right:8px}"
            ".sinfo .tag{display:inline-block;background:#21262d;color:#79c0ff;border-radius:4px;padding:0 6px;margin-right:4px;font-size:11px}"
            ".sinfo .desc{color:#e6edf3;margin-top:2px}.sinfo .core{color:#8b949e;font-size:11px}</style>")
