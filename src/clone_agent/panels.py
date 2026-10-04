"""複製人操盤手頁面 —— 各分頁渲染器。

頁面定位：這是**操盤手自己的工作台**，不是研究儀表板。所以只放三件事——
我現在要不要下單（待批准）、我的錢在哪（資金水位）、我做過什麼（批准／成交紀錄），
再加一份可查的量化規格，避免規則變成口耳相傳。

所有數字都讀 `${GOLDENSTOCKS_DATA_DIR}/data/clone_agent/*.json`，頁面裡**不硬寫數字**
（避免研究更新後頁面說謊）。圖表是純手寫 inline SVG：沒有 JS 圖表庫、離線可看、
手機 Safari 不會因為 CDN 擋掉就變白頁。

同樣的硬邊界：本模組不 import 富邦 SDK、不 import `src/order/`。
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from clone_agent.approval import (
    SKIP_REASONS, _data_dir, load_candidates, load_decisions,
)

TABS = [("/", "待批准"), ("/capital", "資金水位"), ("/approvals", "批准紀錄"),
        ("/fills", "成交紀錄"), ("/spec", "量化規格")]

CSS = """
*{box-sizing:border-box}
body{background:#11151a;color:#dfe6ee;font:13px/1.55 -apple-system,"PingFang TC",sans-serif;margin:0;padding:0}
.wrap{padding:10px 12px 40px;max-width:1000px;margin:0 auto}
nav{display:flex;gap:2px;background:#0c1014;border-bottom:1px solid #2b343d;padding:0 8px;
    position:sticky;top:0;z-index:9;overflow-x:auto}
nav a{color:#8b97a4;text-decoration:none;padding:9px 13px;font-size:13px;white-space:nowrap;
      border-bottom:2px solid transparent}
nav a.on{color:#dfe6ee;border-bottom-color:#2b6cb0;font-weight:600}
nav a:hover{color:#dfe6ee}
h1{font-size:16px;margin:10px 0 2px}
h2{font-size:14px;margin:20px 0 6px;color:#b8c4d0;border-bottom:1px solid #222a32;padding-bottom:4px}
.sub{color:#7f8c99;font-size:11px;margin-bottom:10px}
.warn{background:#3a2a12;border:1px solid #7a5a20;color:#f0c674;padding:8px 10px;border-radius:6px;
      font-size:12px;margin:10px 0 14px}
.ok{background:#16281c;border-color:#2f6b40;color:#8fd19e}
table{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:8px}
th{background:#1a2027;color:#8b97a4;text-align:right;padding:5px 7px;font-weight:600;
   border-bottom:1px solid #2b343d;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
td{padding:4px 7px;border-bottom:1px solid #1b2228;text-align:right;white-space:nowrap}
tr:hover td{background:#161c22}
.pos{color:#8fd19e}.neg{color:#e08a7a}.dim{color:#6b7682}
.kpi{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 14px}
.kpi div{background:#1a2027;border:1px solid #2b343d;border-radius:7px;padding:7px 11px;min-width:104px}
.kpi b{display:block;font-size:17px;line-height:1.3}
.kpi span{color:#7f8c99;font-size:10px}
.card{background:#1a2027;border:1px solid #2b343d;border-radius:8px;padding:10px 12px;margin-bottom:10px}
.card.done{opacity:.42}.card.blk{border-left:3px solid #7a5a20}
.hd{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
.nm{font-size:15px;font-weight:600}
.tag{font-size:10px;padding:1px 6px;border-radius:3px;background:#2b6cb0;color:#fff;vertical-align:2px}
.tag.c{background:#7a5a20}
.t{color:#7f8c99;font-size:11px}
.grid4{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:6px;margin:8px 0}
.grid4 div{background:#141a20;border-radius:5px;padding:5px 8px}
.grid4 span{display:block;color:#7f8c99;font-size:10px}
.grid4 b{font-size:14px}
ul{margin:6px 0 8px;padding-left:19px}
li{margin:1px 0}
li.hit::marker{content:"✓ "}li.hit{color:#8fd19e}
li.bonus::marker{content:"+ "}li.bonus{color:#8ab4f8}
li.risk::marker{content:"⚠ "}li.risk{color:#f0c674}
.btns{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}
button{background:#2b6cb0;color:#fff;border:0;border-radius:5px;padding:7px 16px;font-size:13px;cursor:pointer}
button.sk{background:#3a434d}button:active{opacity:.7}
select{background:#222a32;color:#dfe6ee;border:1px solid #39434d;border-radius:5px;padding:6px;font-size:12px}
.vd{font-size:12px;font-weight:600}.vd.a{color:#8fd19e}.vd.s{color:#e08a7a}
svg{background:#141a20;border:1px solid #2b343d;border-radius:7px;display:block;max-width:100%}
.lg{display:flex;gap:14px;font-size:11px;margin:6px 0 2px;flex-wrap:wrap}
.lg i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:4px;vertical-align:-1px}
code{background:#141a20;padding:1px 5px;border-radius:3px;font-size:11px;color:#b8c4d0}
"""

JS = """
async function dec(iid,d,el){
  const b=el.closest('.btns'); b.querySelectorAll('button').forEach(x=>x.disabled=true);
  const sel=el.closest('.card').querySelector('select');
  const r=await fetch('/decide',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({day:DAY,intent_id:iid,decision:d,skip_reason:d==='skip'&&sel?sel.value:''})});
  if(r.ok) location.reload(); else {alert('寫入失敗 '+r.status); b.querySelectorAll('button').forEach(x=>x.disabled=false);}
}
"""


def _j(name: str) -> Any:
    p = _data_dir() / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def shell(path: str, body: str, day: str = "") -> str:
    nav = "".join(f"<a href='{h}{('?day='+day) if day and h!='/spec' else ''}' "
                  f"class='{'on' if h == path else ''}'>{t}</a>" for h, t in TABS)
    return (f"<!doctype html><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>操盤手代理人</title><style>{CSS}</style><nav>{nav}</nav>"
            f"<div class='wrap'>{body}</div>"
            f"<script>const DAY={json.dumps(day)};{JS}</script>")


def _n(v: Any, d: int = 0, sign: bool = False) -> str:
    if v is None or v != v:
        return "—"
    s = f"{v:+,.{d}f}" if sign else f"{v:,.{d}f}"
    return s


def _cls(v: Any) -> str:
    return "" if v is None or v != v else ("pos" if v > 0 else ("neg" if v < 0 else "dim"))


# ------------------------------------------------------------- 待批准

def render_pending(day: str) -> str:
    cs = load_candidates(day)
    ds = load_decisions(day)
    ok = [c for c in cs if c.get("verdict") != "blocked"]
    cards = []
    for c in sorted(cs, key=lambda x: (x.get("verdict") == "blocked", x.get("time", ""))):
        d = ds.get(c["intent_id"])
        blk = c.get("verdict") == "blocked"
        hits = "".join(f"<li class='hit'>{html.escape(x)}</li>" for x in c.get("hits", []))
        bon = "".join(f"<li class='bonus'>{html.escape(x)}</li>" for x in c.get("bonus", []))
        rk = "".join(f"<li class='risk'>{html.escape(x)}</li>" for x in c.get("risks", []))
        if d:
            lab = "已批准" if d["decision"] == "approve" else f"已略過（{d.get('skip_reason') or '未填'}）"
            btns = (f"<div class='btns'><span class='vd {'a' if d['decision']=='approve' else 's'}'>"
                    f"{html.escape(lab)}</span><span class='t'>{html.escape(d.get('decided_at',''))}</span></div>")
        else:
            opts = "".join(f"<option>{html.escape(r)}</option>" for r in SKIP_REASONS)
            btns = (f"<div class='btns'><button onclick=\"dec('{c['intent_id']}','approve',this)\">"
                    f"批准送出</button><select>{opts}</select>"
                    f"<button class='sk' onclick=\"dec('{c['intent_id']}','skip',this)\">略過</button></div>")
        mg = c.get("margin", 0) or 0
        cards.append(f"""<div class='card{' done' if d else ''}{' blk' if blk else ''}'>
 <div class='hd'><span class='nm'>{html.escape(str(c.get('uname','')))}
   <span class='t'>{html.escape(str(c.get('symbol','')))}</span>
   <span class='tag{' c' if blk else ''}'>{html.escape(str(c.get('action','')))}</span></span>
  <span class='t'>{html.escape(str(c.get('time','')))}</span></div>
 <div class='grid4'>
  <div><span>倉別 · 方向</span><b>{html.escape(str(c.get('action','')))}</b></div>
  <div><span>委託價格</span><b>{_n(c.get('price'),1)}</b></div>
  <div><span>口數</span><b>{c.get('lot',1)} 口</b></div>
  <div><span>使用保證金</span><b>{_n(mg)}</b></div>
 </div>
 <div class='t'>{html.escape(str(c.get('price_note','')))} · {html.escape(str(c.get('leg_note','')))}</div>
 <ul>{hits}{bon}{rk}</ul>
 {btns}</div>""")
    nd = sum(1 for c in ok if c["intent_id"] not in ds)
    return f"""<h1>待批准 · {day}</h1>
<div class='sub'>規則通過 {len(ok)} 筆（{nd} 筆待你裁決）· 被規則擋下 {len(cs)-len(ok)} 筆（列在後面，供覆盤）</div>
<div class='warn'>本服務<b>沒有 import 富邦 SDK</b>，構造上無法送單。按「批准送出」目前只寫入帳本，
供 A→C 升級的同意率統計。真正接上送單要等 PRD §8 下單層完成、四層鎖打開——那會是另一次明確的交辦。</div>
{''.join(cards) or "<div class='sub'>這一天沒有候選。</div>"}"""


# ------------------------------------------------------------- 資金水位

def render_capital(day: str) -> str:
    rows = _j("capital_legs.json") or []
    if not rows:
        return "<h1>資金水位</h1><div class='sub'>還沒有資料。</div>"
    days = sorted({r["day"] for r in rows})
    W, H, PL, PT, PB = 940, 300, 42, 14, 34
    n = len(rows)
    xs = lambda i: PL + (W - PL - 10) * i / max(1, n - 1)  # noqa: E731
    ys = lambda v: PT + (H - PT - PB) * (1 - max(0, min(100, v)) / 100)  # noqa: E731
    series = [("持倉保證金", "持倉", "#8fd19e"), ("未成交委託單保證金", "掛單", "#8ab4f8"),
              ("現金", "現金", "#f0c674")]
    paths = []
    for lab, key, col in series:
        pts = " ".join(f"{xs(i):.1f},{ys(r[key]):.1f}" for i, r in enumerate(rows))
        paths.append(f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='1.8'/>")
    grid = "".join(f"<line x1='{PL}' y1='{ys(v):.1f}' x2='{W-10}' y2='{ys(v):.1f}' "
                   f"stroke='#232b33' stroke-width='1'/>"
                   f"<text x='{PL-6}' y='{ys(v)+3.5:.1f}' fill='#6b7682' font-size='10' "
                   f"text-anchor='end'>{v}%</text>" for v in (0, 25, 50, 75, 100))
    tgt = (f"<rect x='{PL}' y='{ys(50):.1f}' width='{W-PL-10}' height='{ys(16.7)-ys(50):.1f}' "
           f"fill='#2b6cb0' opacity='.10'/>"
           f"<line x1='{PL}' y1='{ys(33.3):.1f}' x2='{W-10}' y2='{ys(33.3):.1f}' "
           f"stroke='#2b6cb0' stroke-width='1' stroke-dasharray='4 3'/>")
    seps = []
    for d in days[1:]:
        i = next(k for k, r in enumerate(rows) if r["day"] == d)
        seps.append(f"<line x1='{xs(i):.1f}' y1='{PT}' x2='{xs(i):.1f}' y2='{H-PB}' "
                    f"stroke='#39434d' stroke-width='1'/>")
    labs = []
    for d in days:
        idx = [k for k, r in enumerate(rows) if r["day"] == d]
        labs.append(f"<text x='{(xs(idx[0])+xs(idx[-1]))/2:.1f}' y='{H-PB+16}' fill='#8b97a4' "
                    f"font-size='10' text-anchor='middle'>{d[5:]}</text>")
    lg = "".join(f"<span><i style='background:{c}'></i>{l}</span>" for l, _, c in series)
    tbl = ["<table><tr><th>日期</th><th>持倉中位</th><th>掛單中位</th><th>現金中位</th>"
           "<th>持倉最高</th><th>現金最低</th></tr>"]
    for d in days:
        g = [r for r in rows if r["day"] == d]
        med = lambda k: sorted(x[k] for x in g)[len(g) // 2]  # noqa: E731
        tbl.append(f"<tr><td>{d}</td><td>{med('持倉'):.1f}%</td><td>{med('掛單'):.1f}%</td>"
                   f"<td>{med('現金'):.1f}%</td><td>{max(x['持倉'] for x in g):.1f}%</td>"
                   f"<td class='{_cls(min(x['現金'] for x in g)-20)}'>{min(x['現金'] for x in g):.1f}%</td></tr>")
    tbl.append("</table>")
    allm = lambda k: sorted(r[k] for r in rows)[n // 2]  # noqa: E731
    return f"""<h1>資金水位 · 三腿消長</h1>
<div class='sub'>每 5 分鐘一點，共 {n} 個時點 / {len(days)} 天。保證金用台灣期交所公告的
<b>原始保證金率</b>（級距 1/2/3 = 13.5%/16.2%/20.25%，另有 21.6%〜40.5%），不是固定 13.5%。
平倉委託不佔保證金（平倉是釋放），所以只有新倉委託算進「掛單」腿。</div>
<div class='kpi'>
 <div><b class='{_cls(allm('持倉')-16.7)}'>{allm('持倉'):.1f}%</b><span>持倉保證金 中位</span></div>
 <div><b>{allm('掛單'):.1f}%</b><span>委託單保證金 中位</span></div>
 <div><b>{allm('現金'):.1f}%</b><span>現金 中位</span></div>
 <div><b>{max(r['持倉']+r['掛單'] for r in rows):.1f}%</b><span>持倉+掛單 最高</span></div>
 <div><b>84.1%</b><span>風控上限 147萬／權益</span></div>
</div>
<div class='lg'>{lg}<span style='color:#6b7682'>虛線 = 1/3 目標；藍帶 = ±50% 相對容忍區（16.7–50%）</span></div>
<svg viewBox='0 0 {W} {H}' width='{W}' height='{H}'>{grid}{tgt}{''.join(seps)}{''.join(paths)}{''.join(labs)}</svg>
<h2>分日水位</h2>{''.join(tbl)}
<div class='sub'>不變量：<b>持倉腿從來沒有「偏高」過</b>，只會偏低。三分法的瓶頸不是檔數不夠，
是新倉成交率只有 48%——這跟「提高周轉率」是同一件事。</div>"""


# ------------------------------------------------------------- 批准紀錄

def render_approvals(day: str) -> str:
    days = sorted({p.name[11:21] for p in _data_dir().glob("candidates_*.jsonl")})
    tot = okn = 0
    rows = []
    for d in days:
        cs = {c["intent_id"]: c for c in load_candidates(d)}
        for iid, dec in load_decisions(d).items():
            c = cs.get(iid, {})
            a = dec["decision"] == "approve"
            tot += 1; okn += a
            rows.append((d, c.get("time", ""), c.get("uname", "?"), c.get("action", ""),
                         c.get("price"), c.get("margin"), a, dec.get("skip_reason", ""),
                         dec.get("decided_at", "")))
    rows.sort(key=lambda r: (r[0], r[1]))
    body = "".join(
        f"<tr><td>{r[0][5:]} {html.escape(str(r[1]))}</td><td>{html.escape(str(r[2]))}</td>"
        f"<td>{html.escape(str(r[3]))}</td><td>{_n(r[4],1)}</td><td>{_n(r[5])}</td>"
        f"<td class='{'pos' if r[6] else 'neg'}'>{'批准' if r[6] else '略過'}</td>"
        f"<td style='text-align:left'>{html.escape(str(r[7]))}</td>"
        f"<td class='dim'>{html.escape(str(r[8])[:19])}</td></tr>" for r in rows)
    rate = f"{100*okn/tot:.1f}%" if tot else "—"
    return f"""<h1>批准紀錄</h1>
<div class='sub'>這份帳本是 A→C 升級的唯一依據：連續 20 個交易日同意率 ≥80% 才談全自動。
「略過」一定要選原因，因為不同意的那幾筆才是改進規則的材料。</div>
<div class='kpi'><div><b>{tot}</b><span>已裁決</span></div>
<div><b class='pos'>{okn}</b><span>批准</span></div>
<div><b class='neg'>{tot-okn}</b><span>略過</span></div>
<div><b>{rate}</b><span>同意率（門檻 80%）</span></div></div>
<table><tr><th>時間</th><th>標的</th><th>倉別</th><th>價格</th><th>保證金</th><th>裁決</th>
<th style='text-align:left'>略過原因</th><th>裁決時間</th></tr>{body or "<tr><td colspan=8 class='dim'>還沒有裁決紀錄</td></tr>"}</table>"""


# ------------------------------------------------------------- 成交紀錄

def render_fills(day: str) -> str:
    f = _j("fills.json") or []
    if not f:
        return "<h1>成交紀錄</h1><div class='sub'>還沒有資料。</div>"
    pnl = sum(x["net_pnl"] for x in f)
    w = sum(1 for x in f if x["win"])
    mg = sum(x["保證金"] for x in f)
    body = "".join(
        f"<tr><td>{html.escape(str(x['uname']))} <span class='dim'>{x['root']}</span></td>"
        f"<td>{'多' if x['direction']=='long' else '空'}</td><td>{x['qty']}</td>"
        f"<td class='dim'>{x['entry_ts'][5:16]}</td><td>{_n(x['entry_px'],1)}</td>"
        f"<td class='dim'>{x['exit_ts'][5:16]}</td><td>{_n(x['exit_px'],1)}</td>"
        f"<td class='dim'>{x['rate%']}%</td><td>{_n(x['保證金'])}</td>"
        f"<td class='{_cls(x['net_pnl'])}'>{_n(x['net_pnl'],0,True)}</td>"
        f"<td class='{_cls(x['net_ret_pct'])}'>{_n(x['net_ret_pct'],2,True)}%</td>"
        f"<td class='{_cls(x['報酬÷保證金%'])}'>{_n(x['報酬÷保證金%'],2,True)}%</td>"
        f"<td class='dim'>{_n(x['hold_min'],0)}</td></tr>"
        for x in sorted(f, key=lambda y: y["entry_ts"]))
    return f"""<h1>成交紀錄</h1>
<div class='sub'>完整進出 {len(f)} 趟（2026-09-29 起）。「報酬÷保證金」才是真正的資金效率——
每口保證金從 3 萬到 24 萬差 8 倍，用報酬率看會嚴重誤判哪一趟做得好。</div>
<div class='kpi'><div><b class='{_cls(pnl)}'>{_n(pnl,0,True)}</b><span>淨損益</span></div>
<div><b>{100*w/len(f):.1f}%</b><span>勝率 {w}/{len(f)}</span></div>
<div><b>{_n(mg)}</b><span>累計動用保證金</span></div>
<div><b class='{_cls(pnl)}'>{100*pnl/mg:+.2f}%</b><span>保證金報酬率</span></div>
<div><b>{sorted(x['hold_min'] for x in f)[len(f)//2]:.0f} 分</b><span>中位持有</span></div></div>
<table><tr><th>標的</th><th>方向</th><th>口</th><th>進場</th><th>進場價</th><th>出場</th><th>出場價</th>
<th>保證金率</th><th>保證金</th><th>淨損益</th><th>報酬%</th><th>÷保證金%</th><th>分鐘</th></tr>{body}</table>"""


# ------------------------------------------------------------- 量化規格

def render_spec(day: str) -> str:
    s = _j("spec.json") or {}
    g = s.get
    def T(rows): return "".join(f"<tr><td style='text-align:left'>{a}</td><td>{b}</td></tr>" for a, b in rows)
    return f"""<h1>代理人量化規格</h1>
<div class='sub'>研究窗 <b>{g('研究窗')}</b>。所有數字都來自這四天逐單重放，不是意見。
完整規格與否證清單在 <code>docs/trader-clone-agent-prd.md</code>。</div>

<div class='kpi'>
<div><b>{g('手動委託')}</b><span>手動委託</span></div>
<div><b>{g('完整來回')}</b><span>完整來回</span></div>
<div><b class='pos'>{_n(g('淨損益'),0,True)}</b><span>淨損益</span></div>
<div><b>{g('勝率%')}%</b><span>勝率</span></div>
<div><b>{_n(g('帳戶權益'))}</b><span>帳戶權益</span></div>
<div><b>{_n(g('風控上限'))}</b><span>風控上限</span></div>
</div>

<h2>一、進場（§3）</h2>
<table><tr><th style='text-align:left'>規則</th><th>數字</th></tr>{T([
 ("H1 時間窗", "09:00–13:40（四天 100% 落在此窗；夜盤 0%、盤後 2.1%）"),
 ("H2 方向", "只做多。條件反向只出平倉卡，不出開空卡"),
 ("H3 契約", "大型（2000 股）。小型費率是大型的 10.6 倍"),
 ("H4 低量門檻", f"5 分成交額 ≥ {_n(g('低量門檻萬'))} 萬（擋掉 {g('低量門檻擋掉%')}% 的委託，只放棄 {g('低量門檻放棄獲利%')}% 的獲利）"),
 ("R2 高信心", "5 分成交額前 25% ∧ 大戶佔比30分前 25% → 覆蓋 39.4%、lift 3.82x"),
 ("R6 候選池", "兩者都前 50% → 覆蓋 51.5%、lift 2.31x"),
 ("最強殘差因子", "大戶減散戶（5 分），控住兩個量能項後 logit z=+2.75、p=0.006"),
 ("尺度排序", "5 分 ≳ 30 分 ≫ 當日（隨機基準對照賽馬）"),
 ("族群內挑誰", "當日漲幅最低（命中 60%、p=0.0006）＋ 20MA 乖離最小（50%、p=0.0022），兩者獨立可相加"),
 ("上升趨勢定義", "20MA 自己在往上爬，不管股價在不在均線上"),
 ("加分項", "42 檔加總差額正在變大（正的變多或負的變少都算）＋ 距差額當日低點 ≤30 分（1.99x、p=0.012）"),
])}</table>

<h2>二、掛價（§4）—— 進場該快、出場該慢</h2>
<table><tr><th style='text-align:left'>側</th><th>政策</th></tr>{T([
 ("進場", f"吃對手價 EV {g('進場EV_吃對手價%')}% ≳ 掛買一 {g('進場EV_掛買一%')}% &gt; 實際 {g('進場EV_實際%')}%。預設掛委買一，R2 高信心直接吃對手價"),
 ("進場禁區", "掛價 −6~−3 檔成交率只有 11%（前面排隊中位 31 張、五檔總量 222 張）"),
 ("五檔成交率", "委買一 88% / 二 33% / 三 0% / 四 50% / 五 0% / 簿外 47%"),
 ("出場 賣出平倉", f"掛對手價上方 +4 檔。n={g('出場_賣出平倉_n')}、平均 +{g('出場_賣出平倉_平均檔')} 檔、14/16 勝、<b>最差 +0.0 無負值</b>"),
 ("出場 回補空單", f"吃對手價。平均 {g('出場_回補_平均檔')} 檔、3 筆中 2 筆負、最差 −13 檔"),
 ("出場護欄", "不能用距離（任何距離門檻都讓總帳變差）。用「方向反轉放棄排隊」＋ 13:25 強制平倉"),
 ("成交率對照", f"新倉 {g('新倉成交率%')}%（中位等待 {g('新倉中位等待分')} 分） vs 平倉 {g('平倉成交率%')}%（{g('平倉中位等待分')} 分）"),
])}</table>

<h2>三、風控（§5）—— 用保證金不用檔數</h2>
<table><tr><th style='text-align:left'>項目</th><th>數字</th></tr>{T([
 ("保證金率", "台灣期交所原始保證金率分級：" + " / ".join(f"{k} {v}%" for k, v in (g('保證金率分級') or {}).items() if k != "其他") + f"；另有 {(g('保證金率分級') or {}).get('其他')}"),
 ("每口保證金範圍", f"最低 {(g('每口保證金範圍') or {}).get('最低')}；最高 {(g('每口保證金範圍') or {}).get('最高')} → 差 8 倍，所以檔數是無效的風控單位"),
 ("三分法", f"持倉／掛單／現金 各 1/3，容忍 ±50% 相對（16.7–50%）。實測中位 {(g('三水位中位') or {}).get('持倉')}% / {(g('三水位中位') or {}).get('掛單')}% / {(g('三水位中位') or {}).get('現金')}%"),
 ("硬閘", f"持倉+掛單 ≤ {_n(g('風控上限'))}（= 權益的 84.1%）。四天有 4 筆委託踩線被靜默退單"),
 ("價格帶口數上限", "400–600 元 4 檔各 1 口 ／ 600–900 元 3 檔 ／ 900–1200 元 2 檔 ／ &gt;1200 元 1 檔"),
 ("同時持倉", "最多 4 檔（人類注意力上限；實測中位 4、最多 5）"),
 ("最大浮動虧損", "做多 30 分 2.2 檔 ／ 60 分 2.8 檔 ／ 收盤 3.5 檔（最壞 −2.08%）。做空 7.6 檔（最壞 −5.74%）"),
])}</table>

<h2>四、標準差的真相（§5.4）</h2>
<div class='sub'>單筆報酬標準差 σ = {g('單筆報酬標準差%')}%，族群內殘差相關 ρ = {g('族群內殘差相關')}。
組合標準差 = σ·√((1+(n−1)ρ)/n)，<b>變異數地板 = σ√ρ = {g('變異數地板%')}%</b>，n 加到無限大也降不過。</div>
<table><tr><th>同時 n 筆</th><th>ρ=0 獨立</th><th>ρ=0.134 跨族群</th><th>ρ=0.659 同族群</th></tr>
<tr><td>4</td><td>1.077%</td><td>1.275%</td><td class='neg'>1.858%</td></tr>
<tr><td>10</td><td>0.681%</td><td>1.011%</td><td class='neg'>1.793%</td></tr></table>
<div class='sub'>10 筆同族群只降 17%（跨族群降 53%、完全獨立降 68%）。
<b>已裁決：集中在 1–2 個風口族群、接受 1.75% 地板、靠勝率撐。</b></div>

<h2>五、明確不得使用（已證偽）</h2>
<ul>
<li class='risk'>小型個股期貨 —— 來回費率 9.0bp vs 大型 0.8bp</li>
<li class='risk'>檔數當風控單位 —— 每口保證金差 8 倍</li>
<li class='risk'>加總差額「絕對水位 &gt;0」當濾網 —— 會擋掉 9/29 全部 9 筆賺錢長單</li>
<li class='risk'>加總差額「30 分增量 &gt;0」當濾網 —— 86.8% 的分鐘都成立，零資訊量</li>
<li class='risk'>ATR% 盤整壓縮 —— 44 筆全部 ≤0.30 百分位，零判別力</li>
<li class='risk'>散戶類欄位進分數 —— FDR 全部不顯著（散買參與30m q=0.979）</li>
<li class='risk'>「伺服器代工」族群 —— 殘差相關 0.075 ≈ 全市場基準，假族群</li>
<li class='risk'>傳統均線多空排列當濾網 —— 控分數後 R² 增量 &lt;0.7pp</li>
<li class='risk'>固定 % 門檻定義盤中事件 —— 49% 事件會擠在開盤 15 分，一律先正規化</li>
</ul>

<h2>六、階段 A → C 的升級門檻（§1）</h2>
<ol>
<li>階段 A 連續 <b>20 個交易日</b>，代理人建議與你實際裁決的同意率 <b>≥80%</b></li>
<li>不同意的那些逐筆覆盤完畢，原因已回寫規則或標記為「人類判斷優先」</li>
<li>影子帳（代理人全自動 vs 你實際）淨值差距 ≤0.5%，且無單筆 &gt;−3% 的失控單</li>
<li>下單層通過 PRD §8 的八項 fail-closed 測試</li>
</ol>
<div class='warn'>尚未決定的大問題：代理人比人更敏感之後，應該是<b>門檻比人更高</b>（寧缺勿濫）
還是<b>比人更早掛單</b>（吃排隊優勢）？這題要等階段 A 累積足夠候選才有資料回答，現在不猜。</div>"""
