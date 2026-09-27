"""biglot dashboard 重構：三個「渲染單一頁面片段」的函式（個股詳情頁片段、
36 檔總覽格、個股詳情整頁），連同各自專用的 `HOVER_JS`/`GRID_SHELL` 靜態
JS/HTML 常數，一起從 scripts/research/biglot_dashboard.py 逐字搬移，邏輯不改
一行。三者都已確認「零呼叫其他仍留在 biglot_dashboard.py 裡的頂層函式」——
呼叫的全是前幾批已經搬走的函式，跟第四／五批發現的情況一樣。

依賴一律 `import biglot_dashboard` + 屬性存取（`PAGE`/`ST`/`SORT_INDEX`/
`PREV_CLOSE`/`NAMES`/`WRT_MIN`/`AGG`/`SUBCAT`/`CATS`/`AMP20`/`ARC_CSS`/`TZ`/
`datetime`/`sys`），已搬移函式（`_stock_series`/`_px_class`/`_svg_detail`/
`_svg_mini`/`_book_table`/`_book_of`/`_tx_panel`/`_stock_info_block`/
`_pe_peer_block`/`_xq_style_block`）直接從各自現在的模組 import。
`render_stock` 呼叫同檔案內的 `render_stock_frag`／`HOVER_JS`，走裸名不需前綴。
"""
from __future__ import annotations

import sys

import biglot_dashboard
from biglot.detail_charts import _stock_series, _svg_detail, _svg_mini
from biglot.utils import _px_class
from biglot.html_fragments import _book_table
from biglot.scoring_support import _book_of
from biglot.tx_panel import _tx_panel
from biglot.stock_meta import _stock_info_block
from biglot.pe_and_shadow import _pe_peer_block
from biglot.xq_style import _xq_style_block


def render_stock_frag(sid, day):
    st = _stock_series(sid, day)
    pc = biglot_dashboard.PREV_CLOSE.get(sid) if day == biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d") else st.get("px0")
    mins = st["mins"]
    last = None
    if mins:
        last = mins[sorted(mins)[-1]]["px"]
    big_day = sum(m["big"] for m in mins.values())
    ret_day = sum(m["ret"] for m in mins.values())
    tot_day = sum(m["tot"] for m in mins.values())
    chg = ((last / pc - 1) * 100) if (last and pc) else None
    _cls = _px_class(last, pc, chg) if (last and pc) else ""
    if last:
        hdr = (f"<div class='shead'><span class='{_cls}' style='font-size:22px;font-weight:700;padding:2px 6px'>"
               f"{last:g}</span>")
    else:
        hdr = "<div class='shead'><span class='dim'>無成交</span>"
    if chg is not None:
        hdr += f"<span class='{'up' if chg>0 else ('dn' if chg<0 else '')}' style='margin-left:10px;font-size:15px'>{chg:+.2f}%</span>"
    hdr += (f"<span class='dim' style='margin-left:16px'>全日大戶淨 "
            f"<b class='{'up' if big_day>0 else 'dn'}'>{big_day/1e4:+,.0f}萬</b> · "
            f"散戶淨 <b class='{'up' if ret_day>0 else 'dn'}'>{ret_day/1e4:+,.0f}萬</b> · "
            f"成交 {tot_day/1e4:,.0f}萬</span></div>")
    chart = _svg_detail(sid, day, st, pc)
    book = _book_table(_book_of(sid, day))
    return (hdr + "<div class='sgrid'><div class='schart'>" + chart + "</div>"
            "<div class='sbook'>" + book + "</div></div>")


HOVER_JS = """<script>(function(){
  const box=document.getElementById('sd'); const tip=document.createElement('div'); const ln=document.createElement('div');
  tip.id='stip'; ln.id='sline'; document.body.appendChild(tip); document.body.appendChild(ln);
  box.addEventListener('mousemove',e=>{
    const svg=e.target.closest&&e.target.closest('svg[data-pts]'); if(!svg){tip.style.display='none';ln.style.display='none';return;}
    if(!svg._pts){try{svg._pts=JSON.parse(svg.dataset.pts);}catch(_){return;}}
    const r=svg.getBoundingClientRect(), sc=r.width/parseFloat(svg.dataset.vw||'940'), x=(e.clientX-r.left)/sc;
    let best=null,bd=1e9; for(const p of svg._pts){const d=Math.abs(p[0]-x); if(d<bd){bd=d;best=p;}}
    if(!best||bd>8){tip.style.display='none';ln.style.display='none';return;}
    tip.innerHTML=best[2]+' <b>'+best[3]+'</b><br>大戶累計 <span style="color:#ff7b72">'+best[4].toLocaleString()+'萬</span> · 散戶累計 <span style="color:#58a6ff">'+best[5].toLocaleString()+'萬</span><br>權證簽號累計 <span style="color:#d2a8ff">'+(best[7]||0).toLocaleString()+'萬</span> · 該分鐘量 '+best[6].toLocaleString()+' 張';
    tip.style.display='block'; const lx=r.left+best[0]*sc;
    ln.style.left=lx+'px'; ln.style.top=r.top+'px'; ln.style.height=r.height+'px'; ln.style.display='block';
    tip.style.left=Math.min(lx+10,window.innerWidth-230)+'px'; tip.style.top=(r.top+best[1]*sc-40)+'px';
  });
  box.addEventListener('mouseleave',()=>{tip.style.display='none';ln.style.display='none';});
})();</script>
<style>#stip{position:fixed;display:none;background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:2px 8px;font-size:11px;color:#e6edf3;pointer-events:none;z-index:9;white-space:nowrap;line-height:1.5}
#sline{position:fixed;display:none;width:1px;background:#8b949e;pointer-events:none;z-index:8}</style>"""


def render_grid_frag(sort="ind"):
    """36 檔 6×6 迷你圖(每 5 秒由 loop 重建快取)。sort: ind=產業鏈固定 / big=全日大戶淨 / chg=漲跌%。"""
    rows = biglot_dashboard.PAGE.get("rows") or []
    if not rows:
        return "<div class='meta'>初始化中…</div>"
    day = biglot_dashboard.ST.date
    key = {"big": lambda r: -(r.get("bigday") or 0), "chg": lambda r: -(r.get("chg_pct") or 0)}.get(sort, lambda r: biglot_dashboard.SORT_INDEX.get(r["sid"], 999))
    cells = []
    for r in sorted(rows, key=key):
        sid = r["sid"]
        st = _stock_series(sid, day)
        pc = biglot_dashboard.PREV_CLOSE.get(sid)
        px = r.get("px"); chg = r.get("chg_pct")
        cls = "up" if (chg or 0) > 0 else ("dn" if (chg or 0) < 0 else "")
        pxs = f"<span class='{cls}' style='font-weight:700'>{px:g}</span> <span class='{cls}'>{chg:+.2f}%</span>" if (px and chg is not None) else "<span class='dim'>—</span>"
        bd = r.get("bigday") or 0; rd = r.get("retday") if r.get("retday") is not None else (st.get("ret_day") or 0)
        tags = (r.get("bull_txt") or "").split("·")[:1] + (r.get("bear_txt") or "").split("·")[:1]
        tagh = "".join(f"<span class='{'sigup' if i == 0 else 'sigdn'}'>{t}</span>" for i, t in enumerate(tags) if t)
        st["sid"] = sid
        svg, amax, wmax = _svg_mini(st, pc)
        cells.append(f"<a class='cell' href='/stock?sid={sid}' target='_blank'>"
                     f"<div class='ch'><b>{sid} {biglot_dashboard.NAMES.get(sid, '')}</b> {pxs} · 大戶 <span class='{'up' if bd > 0 else 'dn'}'>{bd/1e4:+,.0f}</span>"
                     f" 散 <span class='{'up' if rd > 0 else 'dn'}'>{rd/1e4:+,.0f}</span> {tagh}"
                     f"<span class='dim' style='float:right'>尺±{amax/1e4:,.0f}萬"
                     + (f" <span style='color:#d2a8ff'>權±{wmax/1e4:,.0f}萬</span>" if wmax else "") + "</span></div>"
                     f"<div class='cc'>{svg}</div></a>")
    # 36 檔分鐘加總累計(大戶/散戶/權證)→ AGG,供台指面板紅/藍/紫線
    try:
        allk = sorted({k for r in rows for k in (_stock_series(r["sid"], day)["mins"] or {})})
        big = {k: 0.0 for k in allk}; ret = {k: 0.0 for k in allk}; wrt = {k: 0.0 for k in allk}
        for r in rows:
            mm = _stock_series(r["sid"], day)["mins"]
            for k, v in mm.items():
                big[k] += v["big"]; ret[k] += v["ret"]
            wd = biglot_dashboard.WRT_MIN["data"].get(r["sid"]) or {}
            for k, v in wd.items():
                if k in wrt:
                    wrt[k] += v
        cb = cr = cw = 0.0; B = []; Rr = []; Wv = []
        for k in allk:
            cb += big[k]; cr += ret[k]; cw += wrt[k]; B.append(cb); Rr.append(cr); Wv.append(cw)
        biglot_dashboard.AGG.update({"mins": allk, "big": B, "ret": Rr, "wrt": Wv, "t": biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%H:%M:%S")})
    except Exception as _e:  # noqa: BLE001
        print(f"[agg] {_e!r}", file=sys.stderr)
    tx_row = ""
    try:
        _tp = _tx_panel(biglot_dashboard.datetime.now(biglot_dashboard.TZ))
        if _tp:
            tx_row = "<div class='txrow'>" + _tp.replace("<div id='txsrc' hidden>", "<div>", 1) + "</div>"
    except Exception as _e:  # noqa: BLE001
        print(f"[grid-tx] {_e!r}", file=sys.stderr)
    return tx_row + f"<div class='meta' style='margin:0 0 2px'>更新 {biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime('%H:%M:%S')} · 黃=價 · 紅=累計大戶淨 · 藍=累計散戶淨 · 紫=累計權證簽號淨 · 底=量 · 點格子開詳情</div>" + "".join(cells)


GRID_SHELL = """<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>36檔圖形總覽</title><style>
html,body{height:100%;margin:0;background:#0d1117;color:#c9d1d9;font:11px/1.35 -apple-system,'PingFang TC',monospace}
#g{display:grid;grid-template-columns:repeat(6,1fr);grid-template-rows:auto 16px repeat(6,1fr);gap:4px;height:calc(100vh - 6px);padding:2px 4px 4px}
#g .txrow{grid-column:1/-1;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:4px 8px;font-size:12px;line-height:1.5}
#g .meta{grid-column:1/-1;height:16px;color:#8b949e;font-size:10px}
.cell{display:flex;flex-direction:column;min-height:0;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:2px 4px;color:inherit;text-decoration:none}
.cell:hover{border-color:#58a6ff}
.ch{flex:0 0 auto;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cc{flex:1 1 auto;min-height:0}
.up{color:#ff7b72}.dn{color:#3fb950}.dim{color:#484f58}.sigup{color:#ff7b72;margin-left:4px}.sigdn{color:#3fb950;margin-left:4px}
#gtip{position:fixed;display:none;background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:1px 6px;font-size:11px;color:#e6edf3;pointer-events:none;z-index:9;white-space:nowrap}
#gline{position:fixed;display:none;width:1px;background:#8b949e;pointer-events:none;z-index:8}
</style></head><body>
<div id='g'><div class='meta'>載入中…</div></div>
<script>
const Q=new URLSearchParams(location.search).get('sort')||'ind';
async function t(){try{const r=await fetch('/gridfrag?sort='+Q+'&_='+Date.now());document.getElementById('g').innerHTML=await r.text();}catch(e){}
  setTimeout(t,(document.getElementById('g').dataset.closed==='1')?30000:5000);}
t();
// 輕量 hover:只顯示 時間/價(data-pts=[[分鐘序,價]],x 依 viewBox 320 寬換算)
(function(){const g=document.getElementById('g'); const tip=document.createElement('div'); const ln=document.createElement('div');
 tip.id='gtip'; ln.id='gline'; document.body.appendChild(tip); document.body.appendChild(ln);
 g.addEventListener('mousemove',e=>{const svg=e.target.closest&&e.target.closest('svg[data-pts]'); if(!svg){tip.style.display='none';ln.style.display='none';return;}
  if(!svg._pts){try{svg._pts=JSON.parse(svg.dataset.pts);}catch(_){return;}}
  const r=svg.getBoundingClientRect(); let best=null,bd=1e9,hm='',val='',lx=0;
  if(svg.closest('.txrow')){   // 頂部台指圖:data-pts=[x像素,y,時間,價](無 viewBox,x 直接是像素)
    const x=e.clientX-r.left; for(const p of svg._pts){const d=Math.abs(p[0]-x); if(d<bd){bd=d;best=p;}}
    if(!best||bd>12){tip.style.display='none';ln.style.display='none';return;}
    hm=best[2]; val=best[3].toLocaleString(); lx=r.left+best[0];
  }else{                      // 個股迷你圖:data-pts=[分鐘序,價],viewBox 320 寬
    const idx=(e.clientX-r.left)/r.width*270; for(const p of svg._pts){const d=Math.abs(p[0]-idx); if(d<bd){bd=d;best=p;}}
    if(!best||bd>3){tip.style.display='none';ln.style.display='none';return;}
    const m=540+best[0]; hm=String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0'); val=best[1]; lx=r.left+best[0]/270*r.width;
  }
  tip.textContent=hm+'  '+val; tip.style.display='block';
  ln.style.left=lx+'px'; ln.style.top=r.top+'px'; ln.style.height=r.height+'px'; ln.style.display='block';
  tip.style.left=Math.min(lx+8,window.innerWidth-110)+'px'; tip.style.top=(r.top+4)+'px';});
 g.addEventListener('mouseleave',()=>{tip.style.display='none';ln.style.display='none';});})();
</script></body></html>"""


def render_stock(sid, day):
    name = biglot_dashboard.NAMES.get(sid, sid)
    cat = biglot_dashboard.SUBCAT.get(sid) or biglot_dashboard.CATS.get(sid, "")
    amp = biglot_dashboard.AMP20.get(sid)
    today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
    live = (day == today)
    ampx = f" · 振幅{amp:.1f}%" if amp is not None else ""
    frag = render_stock_frag(sid, day)
    js = ""
    if live:
        js = (f"<script>async function u(){{try{{const r=await fetch('/stockfrag?sid={sid}&d={day}&_='+Date.now());"
              f"document.getElementById('sd').innerHTML=await r.text();}}catch(e){{}}setTimeout(u,2000);}}setTimeout(u,2000);</script>")
    js += HOVER_JS
    return (f"<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{sid} {name}</title>{biglot_dashboard.ARC_CSS}"
            "<style>.shead{padding:6px 2px;border-bottom:1px solid #21262d;margin-bottom:8px}"
            ".sgrid{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}"
            ".schart{flex:1 1 620px;min-width:320px}.sbook{flex:0 0 220px}"
            "table.book{border-collapse:collapse}table.book td,table.book th{padding:2px 12px;text-align:right}"
            ".lup{background:#d1242f;color:#fff;font-weight:700}.ldn{background:#1a7f37;color:#fff;font-weight:700}"
            ".nlup{color:#ff7b72;font-weight:700}.nldn{color:#3fb950;font-weight:700}"
            ".cat{color:#8b949e;font-weight:400;font-size:11px}.warnv{color:#e3b341}"
            "a.bk{color:#79c0ff;text-decoration:none}</style></head><body>"
            f"<div style='margin-bottom:6px'><a class='bk' href='/'>← 返回總表</a>"
            f"<span style='font-size:17px;font-weight:700;margin-left:12px'>{sid} {name}</span>"
            f"<span class='cat' style='margin-left:6px'>{cat}{ampx}</span>"
            f"{'' if live else ' · <span class=warnv>歷史回放 '+day+'</span>'}</div>"
            f"{_stock_info_block(sid)}{_pe_peer_block(sid)}{_xq_style_block(sid)}"
            f"<div id='sd'>{frag}</div>{js}</body></html>")
