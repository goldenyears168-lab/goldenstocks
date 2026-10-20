"""每日小哥籌碼筆記本（/xiaoge）：只讀 `${DATA}/chip/xiaoge_notebook/notebook_<日期>.json`，不連外、不寫檔。

資料由 scripts/ops/xiaoge_notebook.py 每日盤後產生。做空／做多名單為代理版（缺權證買超排名）；
本頁是研究筆記，不是進場訊號（首頁固定標註）。
"""
from __future__ import annotations

import html as html_mod
import json
import re

from stock_db import DATA_DIR

DIR = DATA_DIR / "chip" / "xiaoge_notebook"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
esc = html_mod.escape

CSS = """<style>
.xg h2{font-size:15px;margin:18px 0 6px;color:#c9d1d9}
.xg table{border-collapse:collapse;font-size:12px;width:100%;margin-bottom:8px}
.xg th{position:sticky;top:0;background:#1a1a1a;color:#8b949e;padding:5px 7px;text-align:right;white-space:nowrap}
.xg td{padding:4px 7px;border-bottom:1px solid #222;text-align:right;vertical-align:top}
.xg td.l,.xg th.l{text-align:left}
.xg .up{color:var(--price-up,#ff7b72)}.xg .dn{color:var(--price-down,#3fb950)}.xg .dim{color:#6e7681;font-size:10px}
.xg .tag{display:inline-block;margin:0 3px 2px 0;padding:0 5px;border-radius:4px;background:#21262d;color:#c9d1d9;font-size:10px}
.xg .tag.h{background:#3b1d1d;color:#ffa198}.xg .tag.n{background:#2b2a14;color:#f2cc60}.xg .tag.k{background:#14301c;color:#7ee787}
.xg .warn{margin:8px 0;padding:8px 12px;background:#2a1215;border-left:4px solid var(--status-error,#f85149);font-size:12px;line-height:1.8}
.xg .note{color:#8b949e;font-size:11px;line-height:1.7;margin:8px 0;padding:8px 10px;background:#141414;border-left:3px solid #333}
.xg .fresh{font-size:11px;color:#8b949e;margin:6px 0 10px;padding:6px 10px;background:#141414;border-left:3px solid #3fb950}
</style>"""


def _dates() -> list[str]:
    if not DIR.exists():
        return []
    return sorted(p.stem.replace("notebook_", "") for p in DIR.glob("notebook_20*.json"))


def _load(date: str | None):
    ds = _dates()
    if not ds:
        return None, ds
    d = date if date in ds else ds[-1]
    try:
        return json.loads((DIR / f"notebook_{d}.json").read_text(encoding="utf-8")), ds
    except Exception:  # noqa: BLE001
        return None, ds


def _chg(v) -> str:
    if v is None:
        return "—"
    cls = "up" if v > 0 else ("dn" if v < 0 else "")
    return f"<span class='{cls}'>{v:+.2f}%</span>"


def _tags(top5: list[dict]) -> str:
    out = []
    for x in top5:
        t = f"{esc(x['name'])} {x['net_lots']:,}張({x['share_pct']}%)"
        cls = "tag"
        if x["habit"]["is_habit"]:
            cls += " h"; t += f"·慣犯{x['habit']['flips']}/{x['habit']['n']}"
        elif x["habit"]["n"]:
            t += f"·{x['habit']['flips']}/{x['habit']['n']}"
        if x.get("role"):
            cls += " n" if x["role"] in ("隔日沖", "假主力") else " k"; t += f"·{esc(x['role'])}"
        out.append(f"<span class='{cls}' title='{esc(x.get('says') or '')}'>{t}</span>")
    return "".join(out)


def _cand_table(rows: list[dict], kind: str) -> str:
    if not rows:
        return "<div class='note'>今日無符合條件者。</div>"
    h = "<table><tr><th class='l'>股票</th><th>收盤</th><th>漲跌</th><th>主力買賣超(張)</th><th>集中度</th><th>布林位置</th><th>外資／投信(張)</th><th class='l'>買超前 5 名分點</th><th class='l'>理由</th></tr>"
    for r in rows:
        why = "；".join(r.get("reasons") or []) if kind == "short" else "關鍵分點／地緣／高手在買超前 5 名，布林位置不在上軌附近"
        fi = f"{(r.get('foreign') or 0)//1000:+,}／{(r.get('trust') or 0)//1000:+,}" if r.get("foreign") is not None else "—"
        bp = "—" if r.get("boll_pos") is None else f"{r['boll_pos']:.2f}"
        h += (f"<tr><td class='l'><b>{esc(r['stock'])}</b> {esc(r['name'])}</td><td>{r['close']:g}</td><td>{_chg(r['chg_pct'])}</td>"
              f"<td>{r['main_net_lots']:+,}</td><td>{r['conc_pct']:+.1f}%</td><td>{bp}</td><td>{fi}</td>"
              f"<td class='l'>{_tags(r['top5'])}</td><td class='l'>{esc(why)}</td></tr>")
    return h + "</table>"


def render_xiaoge(date: str | None = None) -> str:
    import biglot_dashboard

    d, ds = _load(date if date and DATE_RE.match(date) else None)
    head = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>小哥籌碼筆記本</title>{biglot_dashboard.ARC_CSS}{CSS}</head><body>"
    top = "<div class='meta'><a href='/'>← 大戶儀表板</a> ｜ <b>每日小哥籌碼筆記本</b></div>"
    if not d:
        return head + top + "<div class='xg'><div class='warn'>尚無筆記本（先跑 scripts/ops/xiaoge_chip_daily.py 與 xiaoge_notebook.py）。</div></div></body></html>"
    nav = " ".join(f"<a href='/xiaoge?date={x}'>{x[5:]}</a>" if x != d["date"] else f"<b>{x[5:]}</b>" for x in ds[-10:])
    cov = d.get("coverage", {})
    warn = ("<div class='warn'>" + esc(d.get("disclaimer", "")) + "</div>")
    b = [head, top, "<div class='xg'>", warn,
         f"<div class='fresh'>資料日 {esc(d['date'])} · 產生 {esc(d['generated_at'])} · 日期：{nav}</div>"]
    m = d["market"]
    b.append(f"<h2>一、大盤籌碼（流動性前 {m['n']} 檔）</h2><div class='note'>主力集中度 ≥ +10% 的股票 <b>{m['stocks_conc_ge10']}</b> 檔；"
             f"≤ −10% 的 <b>{m['stocks_conc_le_m10']}</b> 檔。主力買賣超＝買超前 15 名合計＋賣超前 15 名合計（負值）；集中度＝÷當日成交量。</div>")
    b.append("<h2>二、今日名單（代理版，行為標示）</h2><div class='note'>小哥原做法是從「主力收購權證監控表」的買超認購權證排名挑一檔；我們尚無該資料，"
             "以下用現貨分點代理：買超前 5 名中本檔隔日沖慣犯／小哥點名的隔日沖分點 ≥2 個且當日上漲者列為「明日隔日沖賣壓」觀察；"
             "關鍵分點／地緣／高手在買超前 5 名且布林位置不在上軌附近者列為「關鍵分點低檔買進」觀察。慣犯需該股歷史 ≥30 日才判讀。"
             "<b>全市場檢定顯示：慣犯隔天確實更常賣（A 口徑 +30pp），但次日價格沒有更差——這是籌碼行為標示，不是放空訊號。</b></div>")
    b.append("<h3 style='font-size:13px'>明日隔日沖賣壓觀察（空方線索，代理版）</h3>" + _cand_table(d.get("shorts", []), "short"))
    b.append("<h3 style='font-size:13px'>關鍵分點低檔買進觀察（多方線索）</h3>" + _cand_table(d.get("longs", []), "long"))
    b.append("<h2>三、分點雷達（集中度絕對值前 60 檔）</h2>" + _cand_table(d.get("radar", []), "radar"))
    st = d.get("stories", [])
    b.append("<h2>四、故事重放（小哥點名的分點 × 個股，今日有動作者）</h2>")
    if st:
        t = "<table><tr><th class='l'>分點</th><th class='l'>性質</th><th class='l'>個股</th><th>買／賣(張)</th><th>淨(張)</th><th class='l'>小哥怎麼說</th></tr>"
        for s in st:
            cls = "up" if s["net_lots"] > 0 else "dn"
            t += (f"<tr><td class='l'>{esc(s['branch'])}</td><td class='l'>{esc(s.get('role') or '')}</td><td class='l'>{esc(s['stock'])} {esc(s['name'])}</td>"
                  f"<td>{s['buy']:,}／{s['sell']:,}</td><td class='{cls}'>{s['net_lots']:+,}</td><td class='l'>{esc(s['says'])}</td></tr>")
        b.append(t + "</table>")
    else:
        b.append("<div class='note'>今日名單內無符合者（個股前 15 名涵蓋有限；分點頁涵蓋範圍見資料覆蓋）。</div>")
    rv = d.get("review", [])
    b.append("<h2>五、昨日覆盤</h2>")
    if rv:
        t = "<table><tr><th class='l'>方向</th><th class='l'>股票</th><th>昨收</th><th>今開</th><th>今收</th><th>開→收</th><th>收→收</th></tr>"
        for r in rv:
            t += (f"<tr><td class='l'>{'空' if r['side']=='short' else '多'}</td><td class='l'>{esc(r['stock'])} {esc(r['name'])}</td><td>{r['prev_close']:g}</td>"
                  f"<td>{r['open']:g}</td><td>{r['close']:g}</td><td>{_chg(r['oc_pct'])}</td><td>{_chg(r['cc_pct'])}</td></tr>")
        b.append(t + "</table>")
    else:
        b.append("<div class='note'>尚無昨日名單可覆盤（首日或昨日未產生筆記本）。</div>")
    sc = d.get("scoreboard") or {}
    b.append("<h2>六、方法論記分板</h2>")
    if sc.get("H1"):
        o = sc["H1"].get("OOS_A") or {}
        b.append(f"<div class='note'>隔日沖慣性全市場檢定（OOS、流動性母體，口徑 A）：慣犯隔日沖率 {o.get('rate_hab', float('nan')):.1%} "
                 f"vs 非慣犯 {o.get('rate_non', float('nan')):.1%}，差 {o.get('diff', float('nan'))*100:+.1f} 個百分點（95% CI {o.get('ci_lo', float('nan'))*100:+.1f}～{o.get('ci_hi', float('nan'))*100:+.1f}）；"
                 f"事件 {o.get('n_hab', 0) + o.get('n_non', 0):,}。完整報告見 docs/warrant-xiaoge/flip-habit-study-results.md。</div>")
    else:
        b.append("<div class='note'>檢定結果尚未產出。</div>")
    b.append(f"<h2>七、資料覆蓋</h2><div class='note'>個股頁 {cov.get('stocks_with_rows')}／{cov.get('universe')} 檔；分點頁 {cov.get('branch_ids')} 個分點、{cov.get('branch_rows')} 列；"
             f"慣犯判讀需歷史 ≥30 日：目前 {cov.get('stocks_hist_ge_30')} 檔達標，歷史天數中位數 {cov.get('history_median_days')}。"
             f"{('錯誤：' + esc(str(cov.get('errors')))) if cov.get('errors') else ''}</div>")
    b.append("</div></body></html>")
    return "".join(b)
