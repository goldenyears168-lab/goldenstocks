"""複製人操盤手代理人 —— 單頁渲染（掛在 biglot 儀表板 8771 的 /agent）。

為什麼是單頁不是五個分頁：使用者明確要求「不喜歡點來點去」。盤中要的是一眼看完
「要不要下單 → 錢夠不夠 → 做過什麼」，分頁會讓第二層資訊永遠不被看到。
量化規格不常看，收在 <details> 裡。

為什麼掛在 8771 不是獨立 port：他遠端一律走 Tailscale，而 Tailscale 只通 8771。
我原本把「架構乾淨」排在「他用得到」前面，那是錯的優先順序。安全邊界仍在
——本套件不 import 富邦 SDK、不 import `src/order/`，由測試掃描釘住。

委託卡的價格／口數／理由是**可編輯**的：代理人給建議，人改完再送。他改了什麼比
他按了什麼更有價值 —— 改動量就是「規則離他的腦袋還差多遠」的量尺。
"""
from __future__ import annotations

import html
import json
from typing import Any

from clone_agent.approval import (
    SKIP_REASONS, _data_dir, load_candidates, load_decisions,
)

CSS = """
/* ★ body/html 必須自己設底色：/agent 與 /paper 回的是沒有 <html><body> 包裹的片段,
   瀏覽器會用預設白底,只有 .ag 容器內是深色 —— 外圈一圈白的就是這個原因。
   主儀表板的 SHELL 有自己的 body{background:#0d1117},這兩頁沒有,要補。 */
html,body{background:#0d1117;color:#e6edf3;margin:0;padding:0}
body{padding:10px 12px 40px}
/* 色票一律沿用 biglot 儀表板（GitHub dark）：
   #0d1117 底 · #161b22 面板 · #30363d 框 · #e6edf3 字 · #8b949e 次要
   #3fb950 綠(好) · #ff7b72 紅(壞) · #e3b341 琥珀(注意) · #79c0ff 連結 · #388bfd 焦點 */
.ag{font:13px/1.55 -apple-system,"PingFang TC",sans-serif;color:#e6edf3;max-width:1080px}
.ag h2{font-size:14px;margin:20px 0 6px;color:#e6edf3;border-bottom:1px solid #30363d;padding-bottom:4px}
.ag .sub{color:#8b949e;font-size:11px;margin:2px 0 8px;line-height:1.7}
.ag .warn{background:#161b22;border:1px solid #30363d;border-left:3px solid #e3b341;
  color:#8b949e;padding:7px 10px;border-radius:6px;font-size:11px;line-height:1.7;margin:8px 0 12px}
.ag .warn b{color:#e6edf3}
.ag table{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:6px}
.ag th{background:#161b22;color:#8b949e;text-align:right;padding:5px 7px;font-weight:600;
  border-bottom:1px solid #30363d;white-space:nowrap}
.ag th:first-child,.ag td:first-child{text-align:left}
.ag td{padding:4px 7px;border-bottom:1px solid #21262d;text-align:right;white-space:nowrap}
.ag tr:hover td{background:#161b22}
/* 兩組語意刻意分開,不要混用：
   .pos/.neg = **對錯／達標與否**（批准 vs 略過、條件命中、水位達標）—— 綠=好、紅=壞
   .pnl-up/.pnl-dn = **損益**,台股慣例 **賺紅賠綠**,跟主儀表板的 .up/.dn 同色
   直接把 .pos 改成紅色是錯的 —— 那會把「批准」「✓命中」一起翻掉。 */
.ag .pos{color:#3fb950}.ag .neg{color:#ff7b72}.ag .dim{color:#484f58}
.ag .pnl-up{color:#ff7b72}.ag .pnl-dn{color:#3fb950}
.ag .kpi{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 12px}
.ag .kpi div{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 11px;min-width:96px}
.ag .kpi b{display:block;font-size:17px;line-height:1.3;font-variant-numeric:tabular-nums}
.ag .kpi span{color:#8b949e;font-size:10px}
.ag .card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:9px 11px;margin-bottom:8px}
.ag .card.done{opacity:.42}
.ag .card.blk{border-left:3px solid #e3b341;opacity:.7}
.ag .hd{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
.ag .nm{font-size:15px;font-weight:600;color:#e6edf3}
.ag .tag{font-size:10px;padding:1px 6px;border-radius:3px;background:#1f6feb;color:#fff;vertical-align:2px}
.ag .tag.c{background:#6b4a10;color:#e3b341}
.ag .t{color:#8b949e;font-size:11px}
.ag .ed{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:5px;margin:8px 0}
.ag .ed label{background:#0d1117;border:1px solid #21262d;border-radius:5px;padding:4px 8px;display:block}
.ag .ed span{display:block;color:#8b949e;font-size:10px;margin-bottom:1px}
.ag .ed input{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:4px;
  padding:3px 6px;font-size:14px;width:100%;font-variant-numeric:tabular-nums}
.ag .ed input:focus{outline:0;box-shadow:0 0 0 1px #388bfd}
.ag .ed b{font-size:15px;display:block;padding:3px 0;font-variant-numeric:tabular-nums}
.ag .ed .chg input{border-color:#e3b341;background:#1c1708}
.ag ul{margin:6px 0 8px;padding-left:19px}
.ag li{margin:1px 0}
.ag li.hit::marker{content:"✓ "}.ag li.hit{color:#3fb950}
.ag li.bonus::marker{content:"+ "}.ag li.bonus{color:#79c0ff}
.ag li.risk::marker{content:"⚠ "}.ag li.risk{color:#e3b341}
.ag .note{width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:4px;
  padding:4px 7px;font-size:12px;margin-top:2px;font-family:inherit}
.ag .note:focus{outline:0;box-shadow:0 0 0 1px #388bfd}
.ag .btns{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}
/* 按鈕用藍不用綠:按鈕是「動作」不是「數值」,但台股語境綠色會聯想到賠錢 */
.ag button{background:#1f6feb;color:#fff;border:1px solid #388bfd;border-radius:5px;
  padding:6px 16px;font-size:13px;cursor:pointer}
.ag button:hover{background:#388bfd}
.ag button.sk{background:#21262d;border-color:#30363d;color:#8b949e}
.ag button.sk:hover{background:#30363d}
.ag button:disabled{opacity:.45;cursor:default}
.ag select{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:5px;
  padding:5px;font-size:12px}
.ag .vd{font-size:12px;font-weight:600}.ag .vd.a{color:#3fb950}.ag .vd.s{color:#ff7b72}
.ag svg{background:#0d1117;border:1px solid #30363d;border-radius:6px;display:block;max-width:100%}
.ag .lg{display:flex;gap:13px;font-size:11px;margin:5px 0 2px;flex-wrap:wrap;color:#8b949e}
.ag .lg i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.ag details{margin:8px 0}
.ag summary{cursor:pointer;color:#8b949e;font-size:13px;font-weight:600;padding:4px 0}
.ag code{background:#0d1117;border:1px solid #21262d;padding:1px 5px;border-radius:3px;
  font-size:11px;color:#8b949e}
"""

JS = """
function mg(el){
  const c=el.closest('.card'), p=+c.querySelector('.f-px').value, l=+c.querySelector('.f-lot').value;
  const sh=+c.dataset.sh, rt=+c.dataset.rate, eq=+c.dataset.eq;
  const m=p*sh*rt*l;
  c.querySelector('.f-mg').textContent=isFinite(m)?Math.round(m).toLocaleString():'—';
  c.querySelector('.f-leg').textContent=isFinite(m)?(100*m/eq).toFixed(1)+'%':'—';
  const cap=+c.dataset.cap;
  c.querySelector('.f-lot').parentElement.classList.toggle('chg', l!==+c.dataset.lot0);
  c.querySelector('.f-px').parentElement.classList.toggle('chg', p!==+c.dataset.px0);
  const w=c.querySelector('.f-warn');
  w.textContent = l>cap ? ('⚠ 超過價格帶上限 '+cap+' 口') : '';
}
async function agdec(iid,d,el){
  const c=el.closest('.card'); c.querySelectorAll('button').forEach(x=>x.disabled=true);
  const sel=c.querySelector('select.f-sr');
  const r=await fetch('/agentdecide',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({day:AGDAY,intent_id:iid,decision:d,
      skip_reason:d==='skip'&&sel?sel.value:'',
      edited:{price:+c.querySelector('.f-px').value,lot:+c.querySelector('.f-lot').value,
              note:c.querySelector('.f-note').value}})});
  if(r.ok){location.reload();}
  else{const t=await r.text();alert('寫入失敗: '+t);c.querySelectorAll('button').forEach(x=>x.disabled=false);}
}
"""


def _j(name: str) -> Any:
    p = _data_dir() / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _n(v: Any, d: int = 0, sign: bool = False) -> str:
    if v is None or v != v:
        return "—"
    return f"{v:+,.{d}f}" if sign else f"{v:,.{d}f}"


def _c(v: Any) -> str:
    """**對錯／達標**用。綠=好、紅=壞。不要拿來標損益。"""
    return "" if v is None or v != v else ("pos" if v > 0 else ("neg" if v < 0 else "dim"))


def _p(v: Any) -> str:
    """**損益**用。台股慣例賺紅賠綠,跟主儀表板的 .up/.dn 一致。"""
    return "" if v is None or v != v else ("pnl-up" if v > 0 else ("pnl-dn" if v < 0 else "dim"))


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


# ------------------------------------------------------------ 待批准卡

def _card(c: dict[str, Any], dec: dict[str, Any] | None) -> str:
    iid = c["intent_id"]
    blk = c.get("verdict") == "blocked"
    px, lot = c.get("price") or 0, c.get("lot", 1) or 1
    sh, rate = c.get("shares", 2000), c.get("rate", 0.2025)
    eq, cap = c.get("equity", 1_748_587), c.get("lot_cap", 1)
    mg = px * sh * rate * lot
    st = c.get("stars")
    star_cls = {4: "background:#1f6feb", 3: "background:#2d7d46",
                2: "background:#6b4a10;color:#e3b341",
                1: "background:#30363d;color:#8b949e",
                0: "background:#21262d;color:#484f58"}.get(st, "background:#30363d")
    hits = "".join(f"<li class='hit'>{_esc(x)}</li>" for x in c.get("hits", []))
    bon = "".join(f"<li class='bonus'>{_esc(x)}</li>" for x in c.get("bonus", []))
    rk = "".join(f"<li class='risk'>{_esc(x)}</li>" for x in c.get("risks", []))

    if dec:
        e = dec.get("edited") or {}
        chg = "．".join(f"{k} 改成 {v}" for k, v in e.items() if k != "note")
        note = e.get("note", "")
        lab = "已批准" if dec["decision"] == "approve" else f"已略過（{dec.get('skip_reason') or '未填'}）"
        extra = (f"<span class='t'>{_esc(chg)}</span>" if chg else "") + \
                (f"<span class='t'>備註：{_esc(note)}</span>" if note else "")
        body = (f"<div class='btns'><span class='vd {'a' if dec['decision']=='approve' else 's'}'>"
                f"{_esc(lab)}</span>{extra}<span class='t'>{_esc(dec.get('decided_at',''))[:19]}</span></div>")
    else:
        opts = "".join(f"<option>{_esc(r)}</option>" for r in SKIP_REASONS)
        body = f"""<div class='ed'>
 <label><span>倉別·方向</span><b>{_esc(c.get('action'))}</b></label>
 <label><span>委託價格（可改）</span><input class='f-px' type='number' step='any' value='{px}' oninput='mg(this)'></label>
 <label><span>口數（可改，上限 {cap}）</span><input class='f-lot' type='number' min='1' max='20' value='{lot}' oninput='mg(this)'></label>
 <label><span>使用保證金（{rate*100:.2f}%）</span><b class='f-mg'>{_n(mg)}</b></label>
 <label><span>佔權益</span><b class='f-leg'>{100*mg/eq:.1f}%</b></label>
</div>
<div class='t f-warn' style='color:#e3b341'></div>
<div class='t'>{_esc(c.get('price_note'))}</div>
<ul>{hits}{bon}{rk}</ul>
<input class='note f-note' placeholder='理由／備註（可補你自己的判斷，會一起存進帳本）'>
<div class='btns'><button onclick="agdec('{iid}','approve',this)">批准（記錄）</button>
<select class='f-sr'>{opts}</select>
<button class='sk' onclick="agdec('{iid}','skip',this)">略過</button></div>"""

    return f"""<div class='card{' done' if dec else ''}{' blk' if blk else ''}'
 data-sh='{sh}' data-rate='{rate}' data-eq='{eq}' data-cap='{cap}' data-px0='{px}' data-lot0='{lot}'>
 <div class='hd'><span class='nm'>{_esc(c.get('uname'))}
  <span class='t'>{_esc(c.get('symbol'))}</span>
  <span class='tag{' c' if blk else ''}'>{_esc(c.get('action'))}</span>
  {f"<span class='tag' style='{star_cls}'>{_esc(c.get('tier'))}</span>" if st is not None else ""}</span>
  <span class='t'>{_esc(c.get('time'))}</span></div>
 {body}</div>"""


#: 首頁 agentbox 是嵌在主儀表板裡的片段,主頁已有 body 樣式,這裡不可重設 body
#: （重設會把主表的邊距一起改掉）。只有獨立頁 /agent /paper 才需要 body 底色。
FRAG_CSS = CSS.split("/* 色票", 1)[1]
FRAG_CSS = "/* 色票" + FRAG_CSS


def render_pending_block(day: str) -> str:
    """儀表板首頁最上方的精簡待批准區（由 /agentfrag 輪詢換內容）。

    沒有候選時只留一行細字,不佔版面 —— 盤中主表才是他真正在盯的東西。
    有候選時直接展開成可編輯的完整卡片,**不需要點進任何連結**：他說會在數秒內
    確認批准,任何需要切視窗或點連結的設計都做不到數秒。
    """
    cs = [c for c in load_candidates(day) if c.get("verdict") != "blocked"]
    ds = load_decisions(day)
    pend = [c for c in cs if c["intent_id"] not in ds]
    if not pend:
        done = len(cs) - len(pend)
        return (f"<style>{FRAG_CSS}</style><div class='ag'><div class='sub' style='margin:0'>"
                f"🤖 代理人：{day} 無待批准委託"
                f"{f'（已裁決 {done} 筆）' if done else ''}　"
                f"<a href='/agent?day={day}' style='color:#79c0ff'>完整紀錄與規格 →</a>"
                f"</div></div>")
    # 排序＝幫他做完第一關：★ 多的在最上面,同星級再按時間。
    pend.sort(key=lambda c: (-(c.get("stars") or 0), c.get("time", "")))
    cards = "".join(_card(c, None) for c in pend)
    return f"""<style>{FRAG_CSS}</style><div class='ag'>
<div class='hd' style='margin-bottom:6px'>
 <span class='nm'>🤖 代理人待批准
  <span class='tag' style='background:#9e6a03'>{len(pend)}</span></span>
 <span class='t'>{day}　<a href='/agent?day={day}' style='color:#79c0ff'>完整紀錄與規格 →</a></span></div>
<div class='sub' style='margin:0 0 6px'>價格／口數／理由都可改 —— 代理人給建議,你改完再送。
<b>此頁不會送出委託單</b>（<code>src/clone_agent/</code> 不 import 富邦 SDK）,按鈕只寫帳本。</div>
{cards}
<script>const AGDAY={json.dumps(day)};{JS}</script></div>"""


# ------------------------------------------------------------ 資金水位圖

def _capital() -> str:
    """資金三腿視覺化：上方「當下水平堆疊條」＋ 下方「歷史堆疊面積」。

    為什麼換掉原本的三條折線（2026-10-04 jack：「比例都是 100%,有沒有更直覺的圖形」）：
      · 三腿相加**恆等 100.00%**,但畫成三條獨立折線完全沒表達「這是組成」
      · 三條互相交叉 **46 次**（持倉×掛單 14、持倉×現金 18、掛單×現金 14）—— 難讀
      · 看不出「總共押了多少」,而那才是風控要看的

    堆疊的可行性已驗：恆等 100%、**現金從未為負**（最低 21.0%）,不會破圖。
    堆疊後「持倉層＋掛單層」的上緣**就是**持倉+掛單,可以直接跟 84.1% 風控上限比 —— 
    那是折線圖做不到的。

    配色刻意用中性色（藍／紫／灰）**不用紅綠** —— 水位不是損益,用紅綠會跟
    台股慣例的「賺紅賠綠」打架。風控上限線用琥珀（警戒,不是損益）。
    """
    rows = _j("capital_legs.json") or []
    if not rows:
        return "<div class='sub'>還沒有資金水位資料。</div>"
    C_HOLD, C_PEND, C_CASH, C_LIMIT = "#1f6feb", "#8957e5", "#30363d", "#e3b341"
    LIMIT = 84.1                      # 147 萬 ÷ 權益 174.9 萬
    days = sorted({r["day"] for r in rows})
    cur = rows[-1]
    used = cur["持倉"] + cur["掛單"]

    # ── 上層：當下水平堆疊條（盤中真正要看的那一眼）
    def seg(w, color, label, val):
        return (f"<div style='width:{w:.4f}%;background:{color};height:26px;position:relative;"
                f"min-width:0;overflow:hidden' title='{label} {val:.1f}%'>"
                f"<span style='position:absolute;left:5px;top:5px;font-size:11px;color:#fff;"
                f"white-space:nowrap'>{label} {val:.1f}%</span></div>")
    bar = (f"<div style='display:flex;border:1px solid #30363d;border-radius:5px;"
           f"overflow:hidden;position:relative'>"
           + seg(cur["持倉"], C_HOLD, "持倉", cur["持倉"])
           + seg(cur["掛單"], C_PEND, "掛單", cur["掛單"])
           + seg(cur["現金"], C_CASH, "現金", cur["現金"])
           + f"<div style='position:absolute;left:{LIMIT}%;top:-3px;bottom:-3px;width:2px;"
             f"background:{C_LIMIT}' title='風控上限 147 萬 = 權益的 {LIMIT}%'></div>"
           + "</div>")

    # ── 下層：歷史堆疊面積
    W, H, PL, PT, PB = 1040, 230, 40, 10, 28
    n = len(rows)
    xs = lambda i: PL + (W - PL - 10) * i / max(1, n - 1)        # noqa: E731
    ys = lambda v: PT + (H - PT - PB) * (1 - max(0, min(100, v)) / 100)   # noqa: E731

    def band(lo_fn, hi_fn, color, opacity=".85"):
        up = " ".join(f"{xs(i):.1f},{ys(hi_fn(r)):.1f}" for i, r in enumerate(rows))
        dn = " ".join(f"{xs(i):.1f},{ys(lo_fn(r)):.1f}" for i, r in reversed(list(enumerate(rows))))
        return f"<polygon points='{up} {dn}' fill='{color}' opacity='{opacity}'/>"

    areas = (band(lambda r: 0, lambda r: r["持倉"], C_HOLD)
             + band(lambda r: r["持倉"], lambda r: r["持倉"] + r["掛單"], C_PEND)
             + band(lambda r: r["持倉"] + r["掛單"], lambda r: 100, C_CASH, ".55"))
    # 持倉+掛單 的上緣描粗 —— 它就是「總共押了多少」,是唯一要跟上限比的那條
    edge = ("<polyline points='"
            + " ".join(f"{xs(i):.1f},{ys(r['持倉'] + r['掛單']):.1f}" for i, r in enumerate(rows))
            + "' fill='none' stroke='#e6edf3' stroke-width='1.6'/>")
    grid = "".join(f"<line x1='{PL}' y1='{ys(v):.1f}' x2='{W-10}' y2='{ys(v):.1f}' "
                   f"stroke='#30363d' stroke-width='.5' opacity='.6'/>"
                   f"<text x='{PL-5}' y='{ys(v)+3.5:.1f}' fill='#8b949e' font-size='10' "
                   f"text-anchor='end'>{v}%</text>" for v in (0, 25, 50, 75, 100))
    lim = (f"<line x1='{PL}' y1='{ys(LIMIT):.1f}' x2='{W-10}' y2='{ys(LIMIT):.1f}' "
           f"stroke='{C_LIMIT}' stroke-width='1.2' stroke-dasharray='5 3'/>"
           f"<text x='{W-12}' y='{ys(LIMIT)-4:.1f}' fill='{C_LIMIT}' font-size='10' "
           f"text-anchor='end'>風控上限 {LIMIT}%（147 萬）</text>")
    seps = labs = ""
    for d in days:
        idx = [k for k, r in enumerate(rows) if r["day"] == d]
        if d != days[0]:
            seps += (f"<line x1='{xs(idx[0]):.1f}' y1='{PT}' x2='{xs(idx[0]):.1f}' "
                     f"y2='{H-PB}' stroke='#0d1117' stroke-width='1.5'/>")
        labs += (f"<text x='{(xs(idx[0])+xs(idx[-1]))/2:.1f}' y='{H-PB+15}' fill='#8b949e' "
                 f"font-size='10' text-anchor='middle'>{d[5:]}</text>")
    lg = "".join(f"<span><i style='background:{c}'></i>{lab}</span>"
                 for lab, c in (("持倉保證金", C_HOLD), ("未成交委託單", C_PEND),
                                ("現金", C_CASH)))

    med = lambda k, g: sorted(x[k] for x in g)[len(g) // 2]       # noqa: E731
    tb = "".join(
        f"<tr><td>{d}</td>"
        f"<td>{med('持倉', [r for r in rows if r['day']==d]):.1f}%</td>"
        f"<td>{med('掛單', [r for r in rows if r['day']==d]):.1f}%</td>"
        f"<td>{med('現金', [r for r in rows if r['day']==d]):.1f}%</td>"
        f"<td>{max(r['持倉']+r['掛單'] for r in rows if r['day']==d):.1f}%</td></tr>"
        for d in days)
    return f"""<div class='sub' style='margin-bottom:4px'>最後時點 <b>{_esc(cur['day'])} {_esc(cur['hm'])}</b>
　持倉+掛單 <b>{used:.1f}%</b>（上限 {LIMIT}%，還有 {LIMIT-used:.1f}pp 空間）</div>
{bar}
<div class='lg' style='margin-top:6px'>{lg}
 <span style='color:{C_LIMIT}'>▌風控上限 {LIMIT}%</span>
 <span class='dim'>白線＝持倉+掛單（總共押了多少）</span></div>
<svg viewBox='0 0 {W} {H}' style='margin-top:6px'>{grid}{areas}{edge}{lim}{seps}{labs}</svg>
<table style='margin-top:8px'><tr><th>日期</th><th>持倉中位</th><th>掛單中位</th>
<th>現金中位</th><th>持倉+掛單 最高</th></tr>{tb}</table>
<div class='sub'>保證金用台灣期交所公告的<b>原始保證金率</b>（級距 1/2/3 = 13.5/16.2/20.25%，
另有 21.6–40.5%），不是固定 13.5%。平倉委託不佔保證金（平倉是釋放），只有新倉算進掛單腿。
<b>不變量：持倉腿從沒「偏高」過，只會偏低</b> —— 瓶頸是新倉成交率只有 48%。</div>"""


# ------------------------------------------------------------ 主頁

def render_agent_page(day: str) -> str:
    cs = load_candidates(day)
    ds = load_decisions(day)
    okc = [c for c in cs if c.get("verdict") != "blocked"]
    pend = [c for c in okc if c["intent_id"] not in ds]
    cards = "".join(_card(c, ds.get(c["intent_id"]))
                    for c in sorted(cs, key=lambda x: (x.get("verdict") == "blocked", x.get("time", ""))))
    days = sorted({p.name[11:21] for p in _data_dir().glob("candidates_*.jsonl")})
    sel = "".join(f"<option{' selected' if d == day else ''}>{d}</option>" for d in days)

    # 批准紀錄（全期）
    ar, tot, apr = [], 0, 0
    for d in days:
        m = {c["intent_id"]: c for c in load_candidates(d)}
        for iid, de in load_decisions(d).items():
            c = m.get(iid, {})
            a = de["decision"] == "approve"
            tot += 1; apr += a
            e = de.get("edited") or {}
            ar.append((d, c.get("time", ""), c.get("uname", "?"), c.get("action", ""),
                       e.get("price", c.get("price")), e.get("lot", c.get("lot")),
                       a, de.get("skip_reason", ""),
                       "．".join(f"{k}→{v}" for k, v in e.items() if k != "note") or
                       (e.get("note", "") and "有備註") or ""))
    ar.sort()
    arb = "".join(f"<tr><td>{r[0][5:]} {_esc(r[1])}</td><td>{_esc(r[2])}</td><td>{_esc(r[3])}</td>"
                  f"<td>{_n(r[4],1)}</td><td>{_n(r[5])}</td>"
                  f"<td class='{'pos' if r[6] else 'neg'}'>{'批准' if r[6] else '略過'}</td>"
                  f"<td style='text-align:left'>{_esc(r[7])}</td>"
                  f"<td style='text-align:left' class='dim'>{_esc(r[8])}</td></tr>" for r in ar)

    # 成交紀錄
    f = _j("fills.json") or []
    fb = "".join(
        f"<tr><td>{_esc(x['uname'])} <span class='dim'>{x['root']}</span></td>"
        f"<td>{'多' if x['direction']=='long' else '空'}</td><td>{x['qty']}</td>"
        f"<td class='dim'>{x['entry_ts'][5:16]}</td><td>{_n(x['entry_px'],1)}</td>"
        f"<td class='dim'>{x['exit_ts'][5:16]}</td><td>{_n(x['exit_px'],1)}</td>"
        f"<td class='dim'>{x['rate%']}%</td><td>{_n(x['保證金'])}</td>"
        f"<td class='{_p(x['net_pnl'])}'>{_n(x['net_pnl'],0,True)}</td>"
        f"<td class='{_p(x['報酬÷保證金%'])}'>{_n(x['報酬÷保證金%'],2,True)}%</td>"
        f"<td class='dim'>{_n(x['hold_min'],0)}</td></tr>"
        for x in sorted(f, key=lambda y: y["entry_ts"]))
    pnl = sum(x["net_pnl"] for x in f) if f else 0
    mgs = sum(x["保證金"] for x in f) if f else 1
    wn = sum(1 for x in f if x["win"])

    return f"""<style>{CSS}</style><div class='ag'>
<div class='hd'><h2 style='border:0;margin:4px 0'>🤖 操盤手代理人 · 階段 A</h2>
<span class='t'>資料日
<select onchange="location.href='/agent?day='+this.value">{sel}</select></span></div>

<div class='warn'>這個頁面<b>不會送出任何委託單</b>。<code>src/clone_agent/</code> 沒有 import 富邦 SDK，
構造上無法下單（有測試掃描釘住）。按「批准」只是把你的裁決＋你改過的價格口數寫進帳本，
供 A→C 升級的同意率統計。真正接上送單要等 PRD §8 下單層完成、四層鎖打開。</div>

<h2>① 待批准 · {day}　<span class='t'>規則通過 {len(okc)} 筆（{len(pend)} 筆待裁決）·
被規則擋下 {len(cs)-len(okc)} 筆（排在後面供覆盤）</span></h2>
<div class='sub'>價格、口數、理由都可以改 —— 代理人給建議，你改完再送。
改動會一起存進帳本：<b>你改了什麼，比你按了什麼更有價值</b>，那是規則離你腦袋還差多遠的量尺。</div>
{cards or "<div class='sub'>這一天沒有候選。</div>"}

<h2>② 資金水位 · 三腿消長</h2>
{_capital()}

<h2>③ 成交紀錄　<span class='t'>{len(f)} 趟（2026-09-29 起）</span></h2>
<div class='kpi'><div><b class='{_p(pnl)}'>{_n(pnl,0,True)}</b><span>淨損益</span></div>
<div><b>{100*wn/len(f) if f else 0:.1f}%</b><span>勝率 {wn}/{len(f)}</span></div>
<div><b>{_n(mgs)}</b><span>累計動用保證金</span></div>
<div><b class='{_p(pnl)}'>{100*pnl/mgs:+.2f}%</b><span>保證金報酬率</span></div></div>
<div class='sub'>「報酬÷保證金」才是真正的資金效率——每口保證金從 3 萬到 24 萬差 8 倍，
用報酬率看會誤判哪一趟做得好。</div>
<table><tr><th>標的</th><th>方向</th><th>口</th><th>進場</th><th>進場價</th><th>出場</th><th>出場價</th>
<th>保證金率</th><th>保證金</th><th>淨損益</th><th>÷保證金%</th><th>分鐘</th></tr>{fb}</table>

<h2>④ 批准紀錄　<span class='t'>已裁決 {tot} · 批准 {apr} ·
同意率 {f'{100*apr/tot:.1f}%' if tot else '—'}（升級門檻：連續 20 日 ≥80%）</span></h2>
<table><tr><th>時間</th><th>標的</th><th>倉別</th><th>價格</th><th>口</th><th>裁決</th>
<th style='text-align:left'>略過原因</th><th style='text-align:left'>我改了什麼</th></tr>
{arb or "<tr><td colspan=8 class='dim'>還沒有裁決紀錄</td></tr>"}</table>

<div class='sub'>機器人規則、回測研究與「最常見故事」→ <a href='/agentrules'>代理人規則頁</a>（下方 ⑤ 是依人類下單習慣整理的舊規格）</div>
<details><summary>⑤ 量化規格（點開）</summary>{_spec()}</details>
<script>const AGDAY={json.dumps(day)};{JS}</script></div>"""


def _spec() -> str:
    s = _j("spec.json") or {}
    g = s.get
    T = lambda rows: "".join(f"<tr><td style='text-align:left'>{a}</td>"  # noqa: E731
                            f"<td style='text-align:left'>{b}</td></tr>" for a, b in rows)
    mrs = g("保證金率分級") or {}
    return f"""<div class='sub'>研究窗 <b>{g('研究窗')}</b>。數字全來自四天逐單重放。
完整規格與否證清單在 <code>docs/trader-clone-agent-prd.md</code>。</div>
<h2>進場</h2><table>{T([
 ("H1 時間窗", "09:00–13:40（四天 100% 落在此窗）"),
 ("H2 方向", "只做多；條件反向只出平倉卡"),
 ("H3 契約", "大型（2000 股）—— 小型費率是大型的 10.6 倍"),
 ("H4 低量門檻", f"5 分成交額 ≥ {_n(g('低量門檻萬'))} 萬（擋掉 {g('低量門檻擋掉%')}%、只放棄 {g('低量門檻放棄獲利%')}% 獲利）"),
 ("R2 高信心", "5分成交額前 25% ∧ 大戶佔比30分前 25% → 覆蓋 39.4%、lift 3.82x"),
 ("R6 候選池", "兩者都前 50% → 覆蓋 51.5%、lift 2.31x"),
 ("最強殘差因子", "大戶減散戶（5分），控住量能後 logit z=+2.75、p=0.006"),
 ("族群內挑誰", "漲幅最低（60%、p=0.0006）＋ 20MA 乖離最小（50%、p=0.0022），兩者獨立"),
 ("加分項", "42檔加總差額正在變大 ＋ 距差額當日低點 ≤30 分（1.99x、p=0.012）"),
])}</table>
<h2>掛價 —— 進場該快、出場該慢</h2><table>{T([
 ("進場", f"吃對手價 EV {g('進場EV_吃對手價%')}% ≳ 掛買一 {g('進場EV_掛買一%')}% &gt; 實際 {g('進場EV_實際%')}%"),
 ("進場禁區", "−6~−3 檔成交率只有 11%（前面排隊中位 31 張）"),
 ("五檔成交率", "委買一 88% / 二 33% / 三 0% / 四 50% / 五 0% / 簿外 47%"),
 ("出場 賣出平倉", f"掛對手價上方 +4 檔。n={g('出場_賣出平倉_n')}、平均 +{g('出場_賣出平倉_平均檔')} 檔、<b>最差 +0.0 無負值</b>"),
 ("出場 回補空單", f"吃對手價。平均 {g('出場_回補_平均檔')} 檔、3 筆中 2 筆負"),
 ("出場護欄", "不能用距離（任何距離門檻都讓總帳變差）；用方向反轉放棄排隊 ＋ 13:25 強制平倉"),
])}</table>
<h2>風控 —— 用保證金不用檔數</h2><table>{T([
 ("保證金率", " / ".join(f"{k} {v}%" for k, v in mrs.items() if k != "其他") + f"；另有 {mrs.get('其他')}"),
 ("每口範圍", f"{(g('每口保證金範圍') or {}).get('最低')} ～ {(g('每口保證金範圍') or {}).get('最高')} → 差 8 倍，所以檔數是無效的風控單位"),
 ("三分法", "持倉目標 35–50%（下限 20%）／掛單上限 35%／現金硬下限 20%"),
 ("硬閘", f"持倉+掛單 ≤ {_n(g('風控上限'))}（權益的 84.1%）—— 四天有 4 筆委託踩線被靜默退單"),
 ("價格帶口數", "400–600 元 4 檔各 1 口／600–900 元 3 檔／900–1200 元 2 檔／&gt;1200 元 1 檔"),
 ("最大浮動虧損", "做多 30分 2.2 檔／60分 2.8／收盤 3.5（最壞 −2.08%）；做空 7.6 檔"),
 ("標準差地板", f"σ={g('單筆報酬標準差%')}%、族群內 ρ={g('族群內殘差相關')} → 地板 σ√ρ={g('變異數地板%')}%。10 筆同族群只降 17%"),
])}</table>
<h2>明確不得使用（已證偽）</h2><ul>
<li class='risk'>小型個股期貨（來回費率 9.0bp vs 大型 0.8bp）</li>
<li class='risk'>檔數當風控單位（每口保證金差 8 倍）</li>
<li class='risk'>加總差額「絕對水位 &gt;0」當濾網（會擋掉 9/29 全部 9 筆賺錢長單）</li>
<li class='risk'>加總差額「30 分增量 &gt;0」當濾網（86.8% 的分鐘都成立，零資訊量）</li>
<li class='risk'>ATR% 盤整壓縮（44 筆全部 ≤0.30 百分位）</li>
<li class='risk'>散戶類欄位進分數（FDR 全部不顯著）</li>
<li class='risk'>「伺服器代工」族群（殘差相關 0.075 ≈ 全市場基準）</li>
<li class='risk'>傳統均線多空排列當濾網（控分數後 R² 增量 &lt;0.7pp）</li>
<li class='risk'>固定 % 門檻定義盤中事件（49% 事件擠在開盤 15 分）</li></ul>
<h2>階段 A → C 升級門檻</h2><ol>
<li>連續 <b>20 個交易日</b>，代理人建議與你裁決的同意率 <b>≥80%</b></li>
<li>不同意的逐筆覆盤完畢，原因回寫規則或標記「人類判斷優先」</li>
<li>影子帳淨值差距 ≤0.5%，無單筆 &gt;−3% 的失控單</li>
<li>下單層通過 PRD §8 八項 fail-closed 測試</li></ol>"""


# ------------------------------------------------------------ 紙上模擬

_LVL = {"致命": "#ff7b72", "高": "#e3b341", "中": "#79c0ff", "低": "#8b949e"}


def render_paper_page(day: str = "") -> str:
    """全自動紙上模擬（零人工審核）。

    這一頁的任務不是展示績效,是**在上線前找出模擬騙了我們的地方**。所以異常區放最前面,
    而且每一條都寫出門檻與為什麼那個門檻合理 —— 看起來漂亮的回測比看起來難看的危險。
    """
    d = _j("paper_stats.json") or {}
    S, A = d.get("stats") or {}, d.get("anomalies") or []
    tr = _j("paper_trades.json") or []
    if not S:
        return ("<style>" + CSS + "</style><div class='ag'><h2>紙上模擬</h2>"
                "<div class='sub'>還沒有結果。跑 "
                "<code>scripts/research/trader_paper_sim.py</code> 產生。</div></div>")

    g = S.get
    anom = "".join(
        f"<div class='card' style='border-left:3px solid {_LVL.get(a['level'],'#8b949e')};margin-bottom:6px'>"
        f"<div><span class='tag' style='background:{_LVL.get(a['level'],'#484f58')};color:#0d1117'>"
        f"{_esc(a['level'])}</span> <b>{_esc(a['item'])}</b> — {_esc(a['detail'])}</div>"
        f"<div class='sub' style='margin:4px 0 0'>→ {_esc(a['why'])}</div></div>" for a in A)

    rows = "".join(
        f"<tr><td>{_esc(x['nm'])} <span class='dim'>{_esc(x['root'])}</span></td>"
        f"<td><span class='tag'{'' if x['tier']=='R2' else ' style=background:#30363d'}>{_esc(x['tier'])}</span></td>"
        f"<td class='dim'>{_esc(x['進場'])}</td><td>{_n(x['entry'],1)}</td>"
        f"<td class='dim'>{_esc(x['出場'])}</td><td>{_n(x['exit'],1)}</td>"
        f"<td class='dim'>{_n(x['持有分'],0)}</td><td>{_n(x['margin'])}</td>"
        f"<td class='{_p(x['淨損益'])}'>{_n(x['淨損益'],0,True)}</td>"
        f"<td class='{_p(x['淨報酬%'])}'>{_n(x['淨報酬%'],2,True)}%</td>"
        f"<td class='{_p(x['報酬÷保證金%'])}'>{_n(x['報酬÷保證金%'],2,True)}%</td>"
        f"<td style='text-align:left' class='dim'>{_esc(x['why'])}</td>"
        f"<td style='text-align:left' class='dim'>{_esc(x['出場原因'])}</td></tr>"
        for x in sorted(tr, key=lambda y: y["進場"]))

    PNL_KEYS = {"淨損益", "保證金報酬率%", "最大單筆虧損", "最大單筆獲利",
                "日內淨損益", "跨日留倉淨損益"}

    def kpi(label, key, d_=0, sign=False, good=None):
        v = g(key)
        if key in PNL_KEYS:                       # 損益:台股慣例賺紅賠綠
            cls = _p(v)
        elif good is None or v is None:
            cls = ""
        else:                                     # 達標與否:綠=好
            cls = "pos" if (v > good) == (good >= 0) else "neg"
        return f"<div><b class='{cls}'>{_n(v,d_,sign) if isinstance(v,(int,float)) else _esc(v)}</b><span>{label}</span></div>"

    return f"""<style>{CSS}</style><div class='ag'>
<h2>🧪 紙上模擬 · 全自動（零人工審核）</h2>
<div class='sub'>2026-09-29 → 10-02 · 掃全宇宙 42 檔 × 每分鐘 · 完全照 PRD 規則執行,
沒有任何人工批改。<b>進場價一律用訊號分鐘結束後第 1 秒之後的第一筆成交價</b>
（延後 1 秒是刻意的:同秒成交會製造假 alpha）。成本 0.8bp 來回,保證金用期交所公告費率。</div>

<h2 style='color:#ff7b72'>⚠ 異常 {len(A)} 項 —— 請先看這裡</h2>
<div class='sub'>這一頁的任務不是展示績效,是在上線前找出模擬騙了我們的地方。
<b>看起來漂亮的回測比看起來難看的危險。</b></div>
{anom or "<div class='sub'>沒有偵測到異常。</div>"}

<h2>績效總覽</h2>
<div class='kpi'>
{kpi("候選訊號數","候選訊號數")}{kpi("實際進場","實際進場筆數")}
{kpi("訊號→進場轉換率%","訊號→進場轉換率%",2)}
{kpi("淨損益","淨損益",0,True,0)}{kpi("勝率%","勝率%",1)}
{kpi("保證金報酬率%","保證金報酬率%",2,True,0)}
{kpi("最大回撤","最大回撤")}{kpi("盈虧比","盈虧比",2)}
</div>

<h2 style='color:#ff7b72'>致命拆帳：日內 vs 跨日留倉</h2>
<div class='sub'>進場規則是<b>日內</b>訊號（5 分／30 分大戶流）。如果獲利全在隔夜,
賺的就是隔夜漂移而不是規則 —— 而隔夜曝險完全不在規則的解釋與風控範圍內。</div>
<table><tr><th>分類</th><th>筆數</th><th>淨損益</th><th>佔總損益%</th></tr>
<tr><td>日內進出</td><td>{_n(g('日內筆數'))}</td>
 <td class='{_p(g('日內淨損益'))}'>{_n(g('日內淨損益'),0,True)}</td>
 <td class='dim'>—</td></tr>
<tr><td><b>跨日留倉</b></td><td>{_n(g('跨日留倉筆數'))}</td>
 <td class='{_p(g('跨日留倉淨損益'))}'>{_n(g('跨日留倉淨損益'),0,True)}</td>
 <td class='neg'>{_n(g('跨日佔總損益%'),1)}%</td></tr></table>

<h2>風險與資金</h2>
<div class='kpi'>
{kpi("單筆報酬÷保證金 標準差%","單筆報酬標準差%",2)}
{kpi("每日損益標準差","每日損益標準差")}
{kpi("淨損益÷每日損益標準差","夏普式比值(淨損益÷每日損益標準差)",2)}
{kpi("最大單筆虧損","最大單筆虧損",0,True)}
{kpi("最大同時持倉","最大同時持倉")}
{kpi("平均持倉腿%","平均持倉保證金佔權益%",1)}
{kpi("最高持倉腿%","最高持倉保證金佔權益%",1)}
{kpi("中位持有分","中位持有分鐘",0)}
</div>
<div class='sub'>「夏普式比值」= 四天淨損益 ÷ 每日損益標準差。樣本只有 4 天,
比值 &lt;2 就跟運氣區分不開 —— 這不是年化夏普值,不要拿去跟別的策略比。</div>

<h2>分散度</h2>
<div class='kpi'>{kpi("交易日數","交易日數")}{kpi("每日平均筆數","每日平均筆數",1)}
{kpi("不重複標的","不重複標的數")}{kpi("不重複族群","不重複族群數")}
{kpi("累計動用保證金","累計動用保證金")}</div>
<div class='sub'>族群數要配合族群內殘差相關 ρ=0.659 一起看:同族群的筆數不是獨立賭注,
筆數多不等於分散,日報酬標準差會被低估。</div>

<h2>逐筆交易 · {len(tr)} 筆</h2>
<table><tr><th>標的</th><th>層級</th><th>進場</th><th>進場價</th><th>出場</th><th>出場價</th>
<th>分鐘</th><th>保證金</th><th>淨損益</th><th>淨報酬%</th><th>÷保證金%</th>
<th style='text-align:left'>進場理由</th><th style='text-align:left'>出場原因</th></tr>{rows}</table>
</div>"""


# ------------------------------------------------------------ 主表亮燈用的輕端點

#: 訊號在主表上亮多久（分鐘）。跟監看程式的 20 分鐘冷卻對齊 —— 燈滅了就代表
#: 那張卡已經過了冷卻窗,同一檔若條件還在會再跳一張新卡、燈再亮。
SIGNAL_TTL_MIN = 20


def active_signals(day: str, now_hms: str | None = None) -> dict:
    """回傳 {sid: 最高星數} —— 只含「還在有效期內且尚未裁決」的訊號。

    刻意做成獨立的輕端點（不是把資料塞進每秒刷新的主表 fragment）：
      · 主表 fragment 每秒重算一次,塞進去等於每秒多做一次檔案 IO
      · 亮燈是**前端**的事,後端只回狀態;主表被整包換掉後由 JS 重套,
        跟既有的 __applySort / __applyFreeze 同一個模式
    """
    import datetime as _dt
    cs = load_candidates(day)
    if not cs:
        return {"sids": {}, "n": 0, "ttl_min": SIGNAL_TTL_MIN}
    ds = load_decisions(day)
    now = now_hms or _dt.datetime.now().strftime("%H:%M:%S")
    out: dict[str, int] = {}
    for c in cs:
        if c["intent_id"] in ds:          # 已裁決的不再亮
            continue
        t = str(c.get("time") or "")[:8]
        if not t:
            continue
        try:
            a = _dt.datetime.strptime(t, "%H:%M:%S")
            b = _dt.datetime.strptime(now, "%H:%M:%S")
        except ValueError:
            continue
        if not (0 <= (b - a).total_seconds() <= SIGNAL_TTL_MIN * 60):
            continue
        sid = str(c.get("symbol", "")).split("(")[0]
        # symbol 欄是「期貨root(名稱)」,主表要的是證券代號 → 用 uname 反查更穩
        key = str(c.get("sid") or c.get("uname") or sid)
        out[key] = max(out.get(key, 0), int(c.get("stars") or 0))
    return {"sids": out, "n": len(out), "ttl_min": SIGNAL_TTL_MIN}
