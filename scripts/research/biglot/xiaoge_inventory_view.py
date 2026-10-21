"""小哥盤點（/inventory）：只讀 `${DATA}/chip/xiaoge_inventory/inventory_<日>.json` 與 `_ai.md`，不連外、不寫檔。

資料由 scripts/ops/xiaoge_inventory.py（平日 22:30）產生。版面順序（使用者問卷決定）：AI 解讀 → 大盤環境 → 八張戰法卡（含近 20 日迷你折線）
→ 全部戰法合成一張可排序大表（可依戰法篩選）→ 資料缺口。這是條件是否成立的標示，不是進場訊號（頁首固定標註）。
"""
from __future__ import annotations

import html as html_mod
import json
import re

from stock_db import DATA_DIR

DIR = DATA_DIR / "chip" / "xiaoge_inventory"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
esc = html_mod.escape
PRICE_KEY = {"M01": "open_bb", "M02": "narrow", "M03": "range", "M04": "high_out", "M05": "floor"}

CSS = """<style>
.iv h2{font-size:15px;margin:18px 0 6px;color:#c9d1d9}
.iv .warn{margin:8px 0;padding:8px 12px;background:#2a1215;border-left:4px solid var(--status-error,#f85149);font-size:12px;line-height:1.8}
.iv .note{color:#8b949e;font-size:11px;line-height:1.7;margin:8px 0;padding:8px 10px;background:#141414;border-left:3px solid #333}
.iv .fresh{font-size:11px;color:#8b949e;margin:6px 0 10px;padding:6px 10px;background:#141414;border-left:3px solid #3fb950}
.iv .env{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;padding:8px 12px;background:#141414;border-radius:6px;margin:8px 0}
.iv .env b{color:#e6edf3}
.iv .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
.iv .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 12px;cursor:pointer}
.iv .card.on{border-color:#58a6ff}.iv .card.abn{border-color:#d29922}
.iv .card .t{font-size:12px;color:#8b949e}.iv .card .n{font-size:24px;font-weight:600;color:#e6edf3}
.iv .card .s{font-size:10px;color:#6e7681;line-height:1.5}
.iv table{border-collapse:collapse;font-size:12px;width:100%}
.iv th{position:sticky;top:0;background:#1a1a1a;color:#8b949e;padding:5px 7px;text-align:right;white-space:nowrap;cursor:pointer}
.iv td{padding:4px 7px;border-bottom:1px solid #222;text-align:right;vertical-align:top}
.iv td.l,.iv th.l{text-align:left}
.iv .tag{display:inline-block;margin:0 3px 2px 0;padding:0 5px;border-radius:4px;background:#21262d;color:#c9d1d9;font-size:10px}
.iv .tag.chip0{background:#2b2a14;color:#f2cc60}
.iv .up{color:var(--price-up,#ff7b72)}.iv .dn{color:var(--price-down,#3fb950)}.iv .dim{color:#6e7681;font-size:10px}
.iv details{margin:6px 0;background:#141414;border-radius:6px;padding:6px 12px}
.iv details summary{cursor:pointer;color:#c9d1d9;font-size:13px}
.iv .ai{font-size:12px;line-height:1.8;color:#c9d1d9;white-space:pre-wrap}
.iv .ai h4{margin:10px 0 2px;font-size:13px;color:#e6edf3}
.iv .filter{margin:6px 0;font-size:11px;color:#8b949e}
</style>"""

JS = """<script>
(function(){
var tb=document.getElementById('ivtab');if(!tb)return;
var active=null;
function apply(){var rows=tb.tBodies[0].rows;for(var i=0;i<rows.length;i++){var m=rows[i].getAttribute('data-m').split(',');rows[i].style.display=(!active||m.indexOf(active)>=0)?'':'none';}
document.querySelectorAll('.iv .card').forEach(function(c){c.classList.toggle('on',c.getAttribute('data-k')===active);});
var f=document.getElementById('ivf');if(f)f.textContent=active?('篩選：'+active+'（再點一次取消）'):'顯示全部命中';}
document.querySelectorAll('.iv .card').forEach(function(c){c.onclick=function(){var k=c.getAttribute('data-k');active=(active===k)?null:k;apply();};});
var th=tb.tHead.rows[0].cells;
for(let j=0;j<th.length;j++){th[j].onclick=function(){var rows=Array.prototype.slice.call(tb.tBodies[0].rows);var dir=th[j].getAttribute('data-d')==='1'?-1:1;th[j].setAttribute('data-d',dir===1?'1':'0');
rows.sort(function(a,b){var x=a.cells[j].getAttribute('data-v'),y=b.cells[j].getAttribute('data-v');var nx=parseFloat(x),ny=parseFloat(y);if(!isNaN(nx)&&!isNaN(ny))return (nx-ny)*dir*-1;return x.localeCompare(y)*dir*-1;});
rows.forEach(function(r){tb.tBodies[0].appendChild(r);});};}
})();
</script>"""


def _dates() -> list[str]:
    if not DIR.exists():
        return []
    return sorted(p.stem.replace("inventory_", "") for p in DIR.glob("inventory_20??-??-??.json"))


def _spark(vals: list[int], w=180, h=30) -> str:
    if not vals or max(vals) == 0 and len(set(vals)) == 1:
        return "<span class='dim'>近20日皆 0</span>"
    mx = max(vals) or 1
    pts = " ".join(f"{i * w / (len(vals) - 1):.1f},{h - 3 - (v / mx) * (h - 6):.1f}" for i, v in enumerate(vals))
    return f"<svg width='{w}' height='{h}'><polyline points='{pts}' fill='none' stroke='#58a6ff' stroke-width='1.5'/><circle cx='{w}' cy='{h - 3 - (vals[-1] / mx) * (h - 6):.1f}' r='2.5' fill='#f2cc60'/></svg>"


def _ai_html(day: str) -> str:
    p = DIR / f"inventory_{day}_ai.md"
    if not p.exists():
        return "<div class='note'>尚無 AI 解讀（_ai.md）。排程 22:30 會在資料產生後解讀；若沒有請看 logs/xiaoge_inventory_*.log。</div>"
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            out.append(f"<h4>{esc(line.lstrip('# ').strip())}</h4>")
        else:
            out.append(esc(line))
    return "<div class='ai'>" + "\n".join(out) + "</div>"


def _ledger_html() -> str:
    p = DATA_DIR / "chip" / "xiaoge_ledger" / "summary.json"
    if not p.exists():
        return "<h2>訊號帳本</h2><div class='note'>尚無帳本（scripts/ops/xiaoge_ledger.py）。</div>"
    s = json.loads(p.read_text(encoding="utf-8"))

    def fmt(r, h):
        o = (r or {}).get(h)
        if not o or "t" not in o:
            return "<td>—</td>"
        cls = "up" if o["excess_pct"] > 0 else "dn"
        return f"<td class='{cls}'>{o['excess_pct']:+.2f}% <span class='dim'>t={o['t']:+.1f}</span></td>"

    def table(title, d, note):
        if not d:
            return f"<h3 style='font-size:13px'>{title}</h3><div class='note'>{note}</div>"
        t = (f"<h3 style='font-size:13px'>{title}</h3><div class='note'>{note}</div><table><tr><th class='l'>清單</th><th>觀測</th><th>訊號日數</th><th>T收→T+1開</th>"
             "<th>T+1 開→收（超額）</th><th>5日（超額）</th><th>20日（超額）</th></tr>")
        for k, r in d.items():
            t += (f"<tr><td class='l'>{esc(k)}</td><td>{r['n_obs']:,}</td><td>{r['n_days']}</td><td>{(r.get('gap_mean_pct') or 0):+.2f}%</td>"
                  f"{fmt(r, 'oc')}{fmt(r, 'o5')}{fmt(r, 'o20')}</tr>")
        return t + "</table>"
    return ("<h2>訊號帳本：條件成立後的成績</h2><div class='note'>超額＝扣掉同日流動性母體等權平均；T+1 開盤進場（分點收盤後才有）；t 為「訊號日平均」的 HAC t（|t|≥2 才值得看，"
            "且共比較多組）。價格未還原除權息。</div>"
            + table("歷史回補（價格型 P01–P05＝M01–M05 去掉分點條件，2024-10 起）", s.get("backfill"), "尚無")
            + table("即時帳本（每日盤點／筆記本真實命中）", s.get("live"), "剛開始累積：需 T+1 日線入庫後才有成績，約 1 個交易日後出現第一列。"))


def render_inventory(date: str | None = None) -> str:
    import biglot_dashboard

    ds = _dates()
    head = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>小哥盤點</title>{biglot_dashboard.ARC_CSS}{CSS}</head><body>"
    top = "<div class='meta'><a href='/'>← 大戶儀表板</a> ｜ <b>小哥盤點</b></div>"
    if not ds:
        return head + top + "<div class='iv'><div class='warn'>尚無盤點資料（排程 xiaoge-inventory 平日 22:30；或手動 scripts/ops/xiaoge_inventory.py --md）。</div></div></body></html>"
    day = date if date in ds else ds[-1]
    try:
        d = json.loads((DIR / f"inventory_{day}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return head + top + "<div class='iv'><div class='warn'>盤點檔讀取失敗。</div></div></body></html>"
    nav = " ".join(f"<a href='/inventory?date={x}'>{x[5:]}</a>" if x != day else f"<b>{x[5:]}</b>" for x in ds[-10:])
    u, env = d["universe"], d.get("market_env", {})
    b = [head, top, "<div class='iv'>",
         "<div class='warn'>這是「書中戰法條件是否成立」的標示，不是進場訊號。實測：關鍵分點日線進場無額外預測力；慣犯分點隔日更常賣但價格不更差。AI 解讀只標事實與推論。</div>",
         f"<div class='fresh'>資料日 {esc(d['date'])} · 產生 {esc(d['generated_at'])} · 日期：{nav}</div>"]
    b.append("<h2>AI 解讀</h2>" + _ai_html(day))
    b.append("<h2>大盤環境（先分多空，再選招）</h2><div class='env'>"
             f"<span>收盤 <b>{env.get('close', '—')}</b></span><span>月線 <b>{esc(str(env.get('ma20_dir', '—')))}</b></span>"
             f"<span>帶寬 <b>{env.get('bandwidth_pct', '—')}%</b>（{esc(str(env.get('bandwidth_level', '')))}）</span>"
             f"<span>帶內位置 <b>{env.get('pos_in_band', '—')}</b></span>"
             f"<span class='dim'>母體 {u['liquid_stocks']} 檔，有分點資料 {u['with_chip']} 檔</span></div>")
    series = d.get("price_signal_20d_counts", {})
    b.append("<h2>八個戰法（點卡片篩選下方大表）</h2><div class='cards'>")
    for k, m in d["methods"].items():
        typ = m.get("typical_daily_hits_20d"); n = m["hits_today"]
        abn = typ is not None and ((n > typ * 2 and n >= 3) or (n == 0 and typ >= 3))
        sp = _spark(series.get(PRICE_KEY.get(k, ""), [])) if k in PRICE_KEY else "<span class='dim'>尚無歷史（僅今日可算）</span>"
        b.append(f"<div class='card{' abn' if abn else ''}' data-k='{k}'><div class='t'>{k} {esc(m['name'])}</div><div class='n'>{n}</div>"
                 f"<div class='s'>近20日常態 {'—' if typ is None else f'{typ:g}'}{' · 異常' if abn else ''}<br>{sp}<br>書中：{esc(m['book'])}</div></div>")
    b.append("</div><details><summary>各戰法的量化規則（哪些是書中數字、哪些是我的詮釋見 daily-inventory-spec.md）</summary><ul class='note'>"
             + "".join(f"<li><b>{k}</b> {esc(m['rule'])}</li>" for k, m in d["methods"].items()) + "</ul></details>")
    # 合成大表
    rows: dict[str, dict] = {}
    for k, m in d["methods"].items():
        for h in m["hits"]:
            r = rows.setdefault(h["stock"], {"h": h, "m": [], "br": []})
            r["m"].append(k)
            if h.get("branch"):
                r["br"].append(f"{k}:{h['branch']}")
    b.append(f"<h2>全部命中（{len(rows)} 檔，點表頭排序）</h2><div class='filter' id='ivf'>顯示全部命中</div>")
    t = ("<table id='ivtab'><thead><tr><th class='l'>股票</th><th>收盤</th><th>命中數</th><th class='l'>命中戰法</th><th>帶寬%</th><th>乖離20%</th><th>量比</th>"
         "<th>主力集中度%</th><th class='l'>分點資料</th><th class='l'>具名分點／缺口</th></tr></thead><tbody>")
    for sid, r in sorted(rows.items(), key=lambda kv: -len(kv[1]["m"])):
        h = r["h"]; ch = h.get("chip")
        conc = None if not ch else round(ch["conc"] * 100, 1)
        tags = "".join(f"<span class='tag'>{k}</span>" for k in r["m"])
        chip_cell = "<span class='tag'>有</span>" if ch else "<span class='tag chip0'>無</span>"
        gap = []
        if "M07" in r["m"]:
            gap.append("大股東賣：缺資料")
        if "M01" in r["m"] and h.get("chip_confirm") is None:
            gap.append("M01 籌碼未判")
        note = esc("；".join(r["br"] + gap))
        cc = "up" if (conc or 0) > 0 else ("dn" if (conc or 0) < 0 else "")
        t += (f"<tr data-m='{','.join(r['m'])}'><td class='l' data-v='{esc(sid)}'><b>{esc(sid)}</b> {esc(h['name'])}</td><td data-v='{h['close']}'>{h['close']:g}</td>"
              f"<td data-v='{len(r['m'])}'>{len(r['m'])}</td><td class='l' data-v='{','.join(r['m'])}'>{tags}</td><td data-v='{h['bw_pct']}'>{h['bw_pct']:g}</td>"
              f"<td data-v='{h['bias20_pct']}'>{h['bias20_pct']:+g}</td><td data-v='{h['vr'] if h['vr'] is not None else ''}'>{'—' if h['vr'] is None else h['vr']}</td>"
              f"<td data-v='{'' if conc is None else conc}' class='{cc}'>{'—' if conc is None else f'{conc:+.1f}'}</td><td class='l' data-v='{1 if ch else 0}'>{chip_cell}</td><td class='l'>{note}</td></tr>")
    b.append(t + "</tbody></table>")
    b.append(_ledger_html())
    b.append("<h2>資料缺口</h2><div class='note'>" + "<br>".join(esc(g) for g in d.get("gaps", [])) +
             "<br>「分點資料：無」的股票，M01／M04／M07／M08 的籌碼條件是<b>無法判定</b>，不是沒出現。</div>")
    st = d.get("stories_today", [])
    if st:
        b.append("<h2>具名分點今日動作</h2><div class='note'>" + "<br>".join(
            f"{esc(s['branch'])}（{esc(s.get('role') or '')}）× {esc(s['stock'])} {esc(s['name'])}：淨 {s['net_lots']:+,} 張 — {esc(s['says'])}" for s in st[:15]) + "</div>")
    b.append("</div>" + JS + "</body></html>")
    return "".join(b)
