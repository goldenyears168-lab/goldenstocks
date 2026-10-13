"""複製人操盤手代理人 —— 單頁渲染（掛在 biglot 儀表板 8771 的 /agent）。

為什麼是單頁不是五個分頁：使用者明確要求「不喜歡點來點去」。盤中要的是一眼看完
「要不要下單 → 錢夠不夠 → 做過什麼」，分頁會讓第二層資訊永遠不被看到。
量化規格不常看，收在 <details> 裡。

為什麼掛在 8771 不是獨立 port：他遠端一律走 Tailscale，而 Tailscale 只通 8771。
我原本把「架構乾淨」排在「他用得到」前面，那是錯的優先順序。安全邊界仍在
——本套件不 import 富邦 SDK、不 import `src/order/`，由測試掃描釘住。

2026-10-06 起頁面不再有待批准卡片與批准／略過按鈕：實單由下單層自動送出，
能擋單的只有「關閉自動送單」與 AI 監督員撤單。
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from clone_agent.approval import _data_dir, load_candidates, load_decisions
from clone_agent.order_switch import read_heartbeat, read_ledger_display, read_switch

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
(function(){
if(window.__agInit) return; window.__agInit=true;
async function ordsw(btn){
  const want=btn.dataset.sw==='on';
  if(want && btn.dataset.mode==='live' && !confirm('開啟後,新卡片會用真實帳戶自動送出限價買單。確定開啟?')) return;
  btn.disabled=true;
  try{
    const r=await fetch('/agentorderswitch',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({enabled:want})});
    if(!r.ok) throw new Error(r.status+' '+await r.text());
    if(window.__agRefresh) await window.__agRefresh(true); else location.reload();
  }catch(e){alert('切換失敗: '+e.message); btn.disabled=false;}
}
document.addEventListener('click',function(e){
  const sw=e.target.closest('button[data-sw]');
  if(sw) ordsw(sw);
});
})();
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


#: 首頁 agentbox 是嵌在主儀表板裡的片段,主頁已有 body 樣式,這裡不可重設 body
#: （重設會把主表的邊距一起改掉）。只有獨立頁 /agent /paper 才需要 body 底色。
FRAG_CSS = CSS.split("/* 色票", 1)[1]
FRAG_CSS = "/* 色票" + FRAG_CSS


def render_ai_view(day: str | None = None) -> str:
    """AI 監督員(影子階段)看法:只讀 data/agent/clone_agent_view.json,沒有按鈕、沒有任何寫入。"""
    from datetime import datetime
    from clone_agent.agent_supervisor import read_view
    v = read_view()
    if not v:
        return "<div class='sub' style='margin:0 0 6px;color:#8b949e'>🧠 AI 監督員:尚無看法(影子階段,09:50 起每 10 分鐘巡檢)</div>"
    age = None
    try:
        age = (datetime.now() - datetime.fromisoformat(v["updated_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        pass
    stale = age is not None and age > 1500 and "09:50" <= datetime.now().strftime("%H:%M") <= "13:40"
    head = (f"🧠 AI 監督員 <b style='color:#e3b341'>{'撤單已授權·出場影子' if v.get('stage') == 'cancel' else '影子'}</b>　{_esc(str(v.get('updated_at'))[11:19])}"
            f"　{_esc({'patrol': '巡檢', 'post_send': '送單後複核', 'eod': '收盤檢討'}.get(v.get('kind'), v.get('kind')))}"
            + (" <b style='color:#ff7b72'>（超過 25 分鐘沒更新）</b>" if stale else "")
            + ("" if v.get("ok") else f" <b style='color:#ff7b72'>（本輪無輸出:{_esc(v.get('error'))}）</b>")
            + f"　<span style='color:#8b949e'>延遲 {v.get('latency_sec')}s／不執行任何動作,只記錄</span>")
    col = {"hold": "#8b949e", "keep": "#8b949e", "cancel": "#e3b341", "exit": "#ff7b72", "no_data": "#8b949e"}
    items = "".join(
        f"<div>・<b>{_esc(i.get('name') or i.get('sid'))}</b> {'持倉' if i.get('kind') == 'position' else '委託'}"
        + (f" {i['pnl_bps']:+.0f}bps" if isinstance(i.get("pnl_bps"), (int, float)) else "")
        + f" <b style='color:{col.get(i.get('verdict'), '#8b949e')}'>{_esc(i.get('verdict'))}</b>"
        + (f"（信心 {i['confidence']:.2f}）" if isinstance(i.get("confidence"), (int, float)) else "")
        + (f" 觸發 {_esc('/'.join(i['triggers_met']))}" if i.get("triggers_met") else "")
        + f" <span class='t'>{_esc(i.get('reason'))}</span></div>" for i in v.get("items") or [])
    acts = "".join(f"<div style='color:#e3b341'>{_act_label(a)}:{_esc(a.get('type'))} {_esc(a.get('sid'))} {_esc(a.get('trigger_id'))}"
                   f"（護欄:{_esc(a.get('guard'))}）{_esc(a.get('reason'))}</div>" for a in v.get("shadow_actions") or [])
    mk = (v.get("market") or {}).get("view")
    return (f"<div class='sub' style='margin:0 0 6px;border:1px solid #30363d;border-radius:5px;padding:6px 8px'>{head}"
            f"<div style='margin-top:4px'>{_esc(v.get('summary'))}</div>"
            + (f"<div class='t' style='margin-top:2px'>盤面：{_esc(mk)}</div>" if mk else "")
            + items + acts
            + (f"<div class='t' style='margin-top:2px'>下輪看:{_esc('；'.join(v.get('next_watch') or []))}</div>" if v.get("next_watch") else "")
            + _ai_basis(day)
            + _ai_history(day)
            + "</div>")


def _ai_basis(day: str | None) -> str:
    """AI 最近一輪實際讀到的內容:觸發規則文字 + 完整狀態包(與 journal 的 pack 欄位同一份)。"""
    import json
    from datetime import datetime
    from clone_agent.agent_supervisor import read_journal
    recs = [r for r in read_journal(day or datetime.now().strftime("%Y-%m-%d")) if r.get("pack")]
    if not recs:
        return ""
    pk = recs[-1]["pack"]
    rules: dict[str, str] = {}
    for it in (pk.get("positions") or []) + (pk.get("orders") or []):
        for k, v in (it.get("triggers") or {}).items():
            if isinstance(v, dict) and v.get("rule"):
                rules.setdefault(k, v["rule"])
    rule_html = "".join(f"<div><b>{_esc(k)}</b> {_esc(v)}</div>" for k, v in sorted(rules.items()))
    raw = json.dumps(pk, ensure_ascii=False, indent=1)
    return (f"<details style='margin-top:6px'><summary style='cursor:pointer;color:#79c0ff'>AI 判斷依據（{_esc(str(pk.get('now'))[11:19])} 那輪讀到的原文）</summary>"
            f"<div class='t' style='margin:2px 0 4px 14px'>{rule_html or '(該輪無觸發規則)'}</div>"
            f"<pre class='t' style='white-space:pre-wrap;max-height:360px;overflow:auto;margin:2px 0 4px 14px'>{_esc(raw)}</pre></details>")


def _act_label(a: dict[str, Any]) -> str:
    ex = str(a.get("executed") or "")
    if ex.startswith("cancel_requested"):
        return "<b style='color:#f85149'>已請求撤單</b>"
    return f"影子提案{'(未送出:' + _esc(ex) + ')' if ex else ''}"


def _ai_history(day: str | None) -> str:
    from datetime import datetime
    from clone_agent.agent_supervisor import read_journal
    recs = read_journal(day or datetime.now().strftime("%Y-%m-%d"))
    if not recs:
        return ""
    kind_t = {"patrol": "巡檢", "post_send": "送單後複核", "eod": "收盤檢討"}
    rows = []
    for r in reversed(recs[-40:]):
        rv, meta = r.get("review") or {}, r.get("meta") or {}
        acts = r.get("shadow_actions") or []
        head = (f"{_esc(str(r.get('ts'))[11:19])} {_esc(kind_t.get(r.get('kind'), r.get('kind')))}"
                + ("" if not meta.get("is_error") else " <b style='color:#ff7b72'>無輸出</b>")
                + (f" <b style='color:#e3b341'>提案×{len(acts)}</b>" if acts else ""))
        body = (f"<div>{_esc(rv.get('summary') or '(無摘要)')}</div>"
                + (f"<div class='t'>盤面：{_esc((rv.get('market') or {}).get('view'))}</div>" if (rv.get("market") or {}).get("view") else "")
                + "".join(f"<div style='color:#e3b341'>{_act_label(a)}:{_esc(a.get('type'))} {_esc(a.get('sid'))} {_esc(a.get('trigger_id'))} {_esc(a.get('reason'))}</div>" for a in acts)
                + (f"<div class='t'>下輪看:{_esc('；'.join(rv.get('next_watch') or []))}</div>" if rv.get("next_watch") else ""))
        rows.append(f"<details style='margin-top:3px'><summary style='cursor:pointer'>{head}</summary>"
                    f"<div style='margin:2px 0 4px 14px'>{body}</div></details>")
    return (f"<details style='margin-top:6px'><summary style='cursor:pointer;color:#79c0ff'>今日 AI 紀錄（{len(recs)} 輪，點開看每輪寫了什麼）</summary>"
            + "".join(rows) + "</details>")


def _order_window() -> str:
    """實單視窗文字：優先讀下單層心跳（來自 ORDER_CLONE_AGENT_START/END），舊心跳沒有就退回下單層預設。"""
    w = read_heartbeat().get("window")
    if isinstance(w, list) and len(w) == 2:
        return f"{w[0]}–{w[1]}"
    return "10:00–12:00"


def render_order_strip(day: str) -> str:
    """實單層狀態列:只讀 order_switch 的三個 JSON,不碰券商。按鈕只改開關檔。"""
    from datetime import datetime
    sw, hb, led = read_switch(), read_heartbeat(), read_ledger_display(day)
    ents = list((led.get("entries") or {}).values())
    cap = float(hb.get("margin_cap") or 1_470_000)
    mode = hb.get("mode") or "?"
    age = None
    try:
        age = (datetime.now().astimezone() - datetime.fromisoformat(hb["ts"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        pass
    alive = age is not None and age < 90
    if not alive:
        life = "<span style='color:#ff7b72'>● 下單層未運行</span>"
    else:
        life = "<span style='color:#3fb950'>● 運行中</span>"
    mode_t = {"live": "<b style='color:#ff7b72'>實單</b>", "dry": "<b style='color:#e3b341'>演練(不送單)</b>"}.get(mode, "?")
    n = lambda *st: sum(1 for e in ents if e.get("status") in st)    # noqa: E731
    held = sum(float(e.get("margin") or 0) for e in ents if e.get("status") in ("working", "partial", "unknown_submit", "filled", "dry_run"))
    bk = led.get("broker") or {}
    if bk:
        bk_t = (f"<div class='sub' style='margin-top:4px'>券商核對 {_esc(str(bk.get('at') or '')[11:19])}：持倉 {len(bk.get('positions') or [])} 檔"
                f"／未了結委託 {len(bk.get('open_orders') or [])} 筆"
                f"／可用保證金 {(float(bk['available_margin'])/1e4 if bk.get('available_margin') is not None else float('nan')):,.0f} 萬"
                + ("" if bk.get("orders_ok") and bk.get("positions_ok") else " <b style='color:#ff7b72'>（查詢失敗:暫不送新單）</b>")
                + "</div>")
    else:
        bk_t = ""
    halted = led.get("halted")
    if sw["state"] == "corrupt":
        sw_t = "<b style='color:#ff7b72'>開關檔損毀 → 已視為關閉</b>"
    else:
        sw_t = "<b style='color:#3fb950'>自動送單：開</b>" if sw["enabled"] else "<b style='color:#ff7b72'>自動送單：關</b>"
    btn = (f"<button data-sw='off' data-mode='{mode}' style='margin-left:8px'>關閉自動送單</button>" if sw["enabled"] else
           f"<button data-sw='on' data-mode='{mode}' style='margin-left:8px'>開啟自動送單</button>")
    import time as _t
    cd = float(led.get("cooldown_until") or 0)
    cool_t = (f"<div style='color:#e3b341;margin-top:4px'>⏸ 券商回額度不足,暫停送單至 {datetime.fromtimestamp(cd).strftime('%H:%M:%S')}："
              f"{_esc(led.get('cooldown_reason'))}</div>" if cd > _t.time() else "")
    halt_t = (f"<div style='color:#ff7b72;margin-top:4px'>⛔ 今日已停單：{_esc(halted.get('reason'))}（{_esc(halted.get('at'))}）</div>"
              if halted else "")
    return (f"<div class='sub' style='margin:0 0 6px;border:1px solid #30363d;border-radius:5px;padding:6px 8px'>"
            f"{life}　模式 {mode_t}　{sw_t}{btn}<br>"
            f"今日 送出 {n('working', 'partial', 'filled', 'closed', 'cancelled', 'failed', 'unknown_submit', 'dry_run')}"
            f"（掛單中 {n('working', 'partial')}／持倉 {n('filled')}／已平倉 {n('closed')}／撤單 {n('cancelled')}／失敗 {n('failed')}／狀態不明 {n('unknown_submit')}）"
            f"　略過 {n('skipped')}　佔用保證金 {held/1e4:,.0f} 萬／上限 {cap/1e4:,.0f} 萬"
            f"　<span style='color:#8b949e'>出場手動；僅 ★4（嚴格條件全過）自動送單、掛買一；AI 監督員可請求撤單（每日 ≤5 次）；可在此關閉自動送單後到券商 App 撤單</span>"
            f"{bk_t}{halt_t}{cool_t}</div>")


def render_pending_block(day: str) -> str:
    """儀表板首頁最上方的代理人區塊（由 /agentfrag 輪詢換內容）：下單層狀態列＋AI 監督員檢視。

    沿用舊函式名以免動到 http_server 的路由；已沒有待批准卡片。
    """
    n_ok = sum(1 for c in load_candidates(day) if c.get("verdict") != "blocked")
    return f"""<style>{FRAG_CSS}</style><div class='ag' data-day='{day}'>
<div class='hd' style='margin-bottom:6px'>
 <span class='nm'>🤖 操盤手代理人 · 自動送單
  <span class='tag' style='background:#1f6feb'>{n_ok}</span></span>
 <span class='t'>{day}　{_today_summary(day, len(load_candidates(day)))}　<a href='/agent?day={day}' style='color:#79c0ff'>完整紀錄與規格 →</a></span></div>
{render_order_strip(day)}{render_ai_view(day)}
<div class='sub' style='margin:0 0 6px'>規則通過的卡片由獨立下單層在 {_order_window()} 自動送出，不需批准；能擋單的只有「關閉自動送單」與 AI 監督員撤單。
此頁不會送出委託單（<code>src/clone_agent/</code> 不 import 富邦 SDK）,按鈕只寫開關檔。</div>
</div>"""


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


def _ledger_days() -> list[str]:
    from clone_agent.order_switch import ledger_path
    d = ledger_path("2000-01-01").parent
    return sorted(p.stem.rsplit("_", 1)[-1] for p in d.glob("clone_agent_ledger_*.json"))


def _today_summary(day: str, n_cards: int) -> str:
    """① 摘要:以下單層帳本為準(卡片層的 verdict 幾乎都是 ok,真正擋單的是下單層閘門)。"""
    from collections import Counter
    ents = list((read_ledger_display(day).get("entries") or {}).values())
    if not ents:
        return f"出卡 {n_cards} 張；此日沒有下單層帳本"
    sent = [e for e in ents if e.get("status") not in ("skipped",)]
    why = Counter((e.get("reason") or "?").split(":")[0] for e in ents if e.get("status") == "skipped")
    names = {"stars_below_4": "★ 不足 4", "outside_window": "不在送單窗", "same_sid_active": "同標的已有單",
             "broker_position_same_root": "券商已有同商品持倉", "account_cooldown": "額度冷卻中",
             "broker_margin_short": "券商資力不足", "margin_cap": "超過 147 萬上限"}
    why_t = "、".join(f"{names.get(k, k)} {v}" for k, v in why.most_common())
    return (f"出卡 {n_cards} 張 → 下單層送出 {len(sent)} 張、略過 {len(ents) - len(sent)} 張"
            + (f"（{why_t}）" if why_t else ""))


def _capital_live(day: str) -> str:
    hb, led = read_heartbeat(), read_ledger_display(day)
    ents = list((led.get("entries") or {}).values())
    cap = float(hb.get("margin_cap") or 1_470_000)
    held = sum(float(e.get("margin") or 0) for e in ents if e.get("status") in ("working", "partial", "unknown_submit", "filled", "dry_run"))
    bk = led.get("broker") or {}
    av = bk.get("available_margin")
    oo = bk.get("open_orders") or []
    other = [o for o in oo if not o.get("ours")]
    other_t = (f"　<span style='color:#e3b341'>另有 {len(other)} 筆非代理人的未了結委託（手動單），其保證金<b>不在</b>上面的代理人佔用內，"
               f"但會吃掉券商可用保證金</span>") if other else ""
    return (f"<div class='sub' style='margin-bottom:6px;border:1px solid #30363d;border-radius:5px;padding:6px 8px'>"
            f"<b>即時（{_esc(day)}）</b>　代理人佔用 <b>{held/1e4:,.0f} 萬</b>／上限 {cap/1e4:,.0f} 萬（{100*held/cap:.1f}%）"
            + (f"　券商可用保證金 <b>{float(av)/1e4:,.0f} 萬</b>（{_esc(str(bk.get('at') or '')[11:19])} 核對）" if av is not None else "")
            + f"{other_t}</div>")


def _live_trades() -> str:
    """③ 代理人實單紀錄:彙整所有下單層帳本中真的送過單的項目(去重,以最新一天的狀態為準)。"""
    from clone_agent.order_switch import read_ledger_display as rl
    seen: dict[str, tuple[str, dict]] = {}
    for d in _ledger_days():
        for e in (rl(d).get("entries") or {}).values():
            if e.get("status") != "skipped":
                seen[e["intent_id"]] = (e.get("carried_from") or d, e)
    st = {"filled": "持倉中", "closed": "已平倉", "failed": "送單失敗", "cancelled": "已撤單", "working": "掛單中",
          "partial": "部分成交", "unknown_submit": "狀態不明", "dry_run": "演練"}
    rows = sorted(seen.values(), key=lambda x: (x[0], x[1].get("card_time") or ""))
    if not rows:
        return "<div class='sub'>下單層尚未送過單。</div>"
    from datetime import datetime

    def hm(ts):
        return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "—"
    body = "".join(
        f"<tr><td>{_esc(e.get('uname'))} <span class='dim'>{_esc(e.get('contract'))}</span></td>"
        f"<td class='dim'>{_esc(d[5:])} {_esc(e.get('card_time'))}</td><td>{_n(e.get('price'), 1)}</td>"
        f"<td>{e.get('filled_lot', 0)}/{e.get('lot')}</td><td>{hm(e.get('held_since'))}</td>"
        f"<td>{st.get(e.get('status'), e.get('status'))}"
        + (f" <span class='dim'>{_esc(e.get('reason'))}</span>" if e.get("reason") else "") + "</td>"
        f"<td>{_n(e.get('margin'))}</td></tr>"
        for d, e in rows)
    ok = sum(1 for _, e in rows if e.get("filled_lot"))
    return (f"<div class='sub'>共送單 {len(rows)} 筆、其中成交 {ok} 筆（資料來源：下單層帳本，非研究資料）。"
            f"損益請看 :8780 財務會計（券商成交明細先進先出）；此帳本沒有出場價。</div>"
            f"<table><tr><th>標的</th><th>卡片時間</th><th>限價</th><th>成交/口</th><th>成交時間</th><th>狀態</th><th>保證金</th></tr>{body}</table>")


# ------------------------------------------------------------ 主頁

def render_agent_page(day: str) -> str:
    cs = load_candidates(day)
    okc = [c for c in cs if c.get("verdict") != "blocked"]
    days = sorted({p.name[11:21] for p in _data_dir().glob("candidates_*.jsonl")} | set(_ledger_days()) | {day})
    sel = "".join(f"<option{' selected' if d == day else ''}>{d}</option>" for d in days)

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

    return f"""<style>{CSS}</style><div class='ag' data-day='{day}'>
<div class='hd'><h2 style='border:0;margin:4px 0'>🤖 操盤手代理人 · 自動送單中（出場手動）</h2>
<span class='t'>資料日
<select onchange="location.href='/agent?day='+this.value">{sel}</select></span></div>

<div class='warn'>這個頁面<b>不會送出任何委託單</b>。<code>src/clone_agent/</code> 沒有 import 富邦 SDK，
構造上無法下單（有測試掃描釘住）。<b>實單由獨立下單層（<code>src/order/clone_agent_*</code>）自動送出</b>：
規則通過的新倉買進卡，在 {_order_window()} 內直接用卡片價與口數送單，<b>不需要你批准</b>，本頁也沒有批准／略過按鈕。
要停止送單請用下方「關閉自動送單」。AI 監督員另可依 C1–C3 觸發條件請求撤單（每日 ≤5 次，下單層再驗證）。</div>
{render_order_strip(day)}{render_ai_view(day)}

<h2>① 今日出卡 · {day}　<span class='t'>{_today_summary(day, len(cs))}（卡片層被 blocked {len(cs)-len(okc)} 張）</span></h2>

<h2>② 資金水位</h2>
{_capital_live(day)}
<details><summary>歷史三腿消長（研究期 09-29～10-02，已停止更新）</summary>{_capital()}</details>

<h2>③ 代理人實單紀錄</h2>
{_live_trades()}
<details><summary>研究期人類成交（09-29～10-02 共 {len(f)} 趟，與代理人實單無關，已停止更新）</summary>
<div class='kpi'><div><b class='{_p(pnl)}'>{_n(pnl,0,True)}</b><span>淨損益</span></div>
<div><b>{100*wn/len(f) if f else 0:.1f}%</b><span>勝率 {wn}/{len(f)}</span></div>
<div><b>{_n(mgs)}</b><span>累計動用保證金</span></div>
<div><b class='{_p(pnl)}'>{100*pnl/mgs:+.2f}%</b><span>保證金報酬率</span></div></div>
<div class='sub'>「報酬÷保證金」才是真正的資金效率——每口保證金從 3 萬到 24 萬差 8 倍，
用報酬率看會誤判哪一趟做得好。</div>
<table><tr><th>標的</th><th>方向</th><th>口</th><th>進場</th><th>進場價</th><th>出場</th><th>出場價</th>
<th>保證金率</th><th>保證金</th><th>淨損益</th><th>÷保證金%</th><th>分鐘</th></tr>{fb}</table></details>

<div class='sub'>機器人規則、回測研究與「最常見故事」→ <a href='/agentrules'>代理人規則頁</a>（下方 ④ 是依人類下單習慣整理的舊規格）</div>
<details><summary>④ 量化規格（點開）</summary>{_spec()}</details>
<script>{JS}</script></div>"""


def _spec() -> str:
    s = _j("spec.json") or {}
    g = s.get
    T = lambda rows: "".join(f"<tr><td style='text-align:left'>{a}</td>"  # noqa: E731
                            f"<td style='text-align:left'>{b}</td></tr>" for a, b in rows)
    mrs = g("保證金率分級") or {}
    return f"""<div class='sub'>研究窗 <b>{g('研究窗')}</b>。數字全來自四天逐單重放。
完整規格與否證清單在 <code>docs/trader-clone-agent-prd.md</code>。</div>
<h2>進場</h2><table>{T([
 ("H1 時間窗", f"研究口徑 09:00–13:40（四天 100% 落在此窗）；<b>實單視窗為 {_order_window()}</b>"),
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
 ("出場護欄", "不能用距離（任何距離門檻都讓總帳變差）；用方向反轉放棄排隊 ＋ 13:25 強制平倉（<b>以上為回測假設；實單層沒有強制平倉，出場手動、AI 出場僅影子</b>）"),
])}</table>
<h2>風控 —— 用保證金不用檔數</h2><table>{T([
 ("保證金率", " / ".join(f"{k} {v}%" for k, v in mrs.items() if k != "其他") + f"；另有 {mrs.get('其他')}"),
 ("每口範圍", f"{(g('每口保證金範圍') or {}).get('最低')} ～ {(g('每口保證金範圍') or {}).get('最高')} → 差 8 倍，所以檔數是無效的風控單位"),
 ("三分法", "持倉目標 35–50%（下限 20%）／掛單上限 35%／現金硬下限 20%"),
 ("硬閘", f"持倉+掛單 ≤ {_n(g('風控上限'))}（研究口徑：權益的 84.1%）—— 四天有 4 筆委託踩線被靜默退單。<b>實單現行：固定 147 萬上限＋全帳戶資力證明閘（券商持倉含手動倉×原始保證金率＋掛單＋新單 ≤ 147 萬）</b>"),
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
<h2>階段 A → C 升級門檻（原設計；<b>實際系統已越過階段 A：下單層已自動送單</b>，以下僅作同意率驗收參考）</h2><ol>
<li>連續 <b>20 個交易日</b>，代理人建議與你裁決的同意率 <b>≥80%</b></li>
<li>不同意的逐筆覆盤完畢，原因回寫規則或標記「人類判斷優先」</li>
<li>影子帳淨值差距 ≤0.5%，無單筆 &gt;−3% 的失控單</li>
<li>下單層通過 PRD §8 八項 fail-closed 測試</li></ol>"""


# ------------------------------------------------------------ 紙上模擬

_LVL = {"致命": "#ff7b72", "高": "#e3b341", "中": "#79c0ff", "低": "#8b949e"}


def _learn_dir():
    return _data_dir() / "learn"


def _champion_stats(L, m: str = "ex60") -> dict:
    """冠軍帳(樣本內/樣本外)的 n、均值、t、拿掉前3大後的均值。"""
    import math
    x = L[m].dropna()
    if len(x) < 2:
        return {"n": len(x), "mean": None, "t": None, "ex3": None, "hit": None}
    sd = x.std(ddof=1)
    return {"n": len(x), "mean": float(x.mean()),
            "t": float(x.mean() / (sd / math.sqrt(len(x)))) if sd > 0 else None,
            "ex3": float(x.sort_values().iloc[:-3].mean()) if len(x) > 3 else None,
            "hit": float((x > 0).mean())}


def render_paper_page(day: str = "") -> str:
    """唯一的紙上軌:複製人 CA-R1 凍結規則的每日重放(clone_agent_learn.py 的產物)。

    樣本外從 learn 設定的 oos_from 起算;異常區放最前面,先看這裡再看績效。
    """
    import pandas as pd
    from clone_agent.rules_version import label as rules_label

    ld = _learn_dir()
    lp = ld / "ledger.jsonl"
    cfg_p = Path(__file__).resolve().parents[2] / "config" / "clone_agent_learn.json"
    if not lp.exists() or not cfg_p.exists():
        return ("<style>" + CSS + "</style><div class='ag'><h2>紙上模擬</h2>"
                "<div class='sub'>還沒有學習迴圈帳本。每日收盤後由 "
                "<code>clone_agent_learn.py</code> 產生。</div></div>")
    cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
    oos_from = cfg["oos_from"]
    L = pd.DataFrame([json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines() if x.strip()])
    champ = L.query(cfg["champion"]["filter"])
    champ = champ[champ["ex60"].notna()]
    ins, oos = champ[champ.day < oos_from], champ[champ.day >= oos_from]
    S_in, S_oos, S_all = _champion_stats(ins), _champion_stats(oos), _champion_stats(champ)
    n_days_oos = int(oos.day.nunique())

    anomalies = []
    if S_oos["n"] < 30:
        anomalies.append(("高", f"樣本外只有 {S_oos['n']} 筆 / {n_days_oos} 個交易日",
                          "n<30 時均值的信賴區間寬到任何數字都不該當結論;影子挑戰者需要的日數見下表"))
    if S_oos["ex3"] is not None and S_oos["mean"] is not None and S_oos["mean"] > 0 >= S_oos["ex3"]:
        anomalies.append(("高", "樣本外拿掉前 3 大獲利後轉負", "少數幾筆撐住全部,在統計上與運氣分不開"))
    if S_in["mean"] is not None and S_oos["mean"] is not None and S_in["mean"] * S_oos["mean"] < 0:
        anomalies.append(("高", "樣本內與樣本外符號相反", "規則在樣本外沒有重現,先查是不是樣本內過擬合"))
    anom = "".join(
        f"<div class='card' style='border-left:3px solid {_LVL.get(lv, '#8b949e')};margin-bottom:6px'>"
        f"<div><span class='tag' style='background:{_LVL.get(lv, '#484f58')};color:#0d1117'>{_esc(lv)}</span> "
        f"<b>{_esc(item)}</b></div><div class='sub' style='margin:4px 0 0'>→ {_esc(why)}</div></div>"
        for lv, item, why in anomalies)

    def kpi_row(title: str, S: dict) -> str:
        cls = _p(S["mean"])
        return (f"<tr><td>{_esc(title)}</td><td>{_n(S['n'])}</td>"
                f"<td class='{cls}'>{_n(S['mean'], 1, True)}</td><td>{_n(S['t'], 2, True)}</td>"
                f"<td>{_n(S['hit'] * 100 if S['hit'] is not None else None, 0)}%</td>"
                f"<td class='{_p(S['ex3'])}'>{_n(S['ex3'], 1, True)}</td></tr>")

    daily = "".join(
        f"<tr><td>{_esc(d)}</td><td>{int((champ.day == d).sum())}</td>"
        f"<td class='{_p(g['ex60'].mean())}'>{_n(g['ex60'].mean(), 1, True)}</td>"
        f"<td>{'樣本外' if d >= oos_from else '樣本內'}</td></tr>"
        for d, g in sorted(champ.groupby("day"), key=lambda kv: kv[0], reverse=True)[:15])

    reviews = sorted(ld.glob("review_*.md"))
    review = _esc(reviews[-1].read_text(encoding="utf-8")) if reviews else "尚無檢討檔"
    hb = read_heartbeat() or {}
    sw = read_switch()
    mode = {"dry": "紙上(不送券商)", "live": "實彈"}.get(hb.get("mode"), "未知")

    return f"""<style>{CSS}</style><div class='ag' data-day='{day}'>
<h2>🧪 紙上模擬 · 單一軌:{_esc(rules_label())}</h2>
<div class='sub'>每日收盤後以<b>凍結規則</b>重放當天全部訊號(含排隊模型),扣同名單等權基準與成本,
指標為 ex60(成本後 60 分超額,bps)。樣本外從 <b>{_esc(oos_from)}</b> 起算。
操盤手下單層目前:<b>{_esc(mode)}</b> · 開關 {'開' if sw.get('enabled') else '關'}。
舊的 9/29–10/2 靜態模擬與 biglot 分數紙上帳已退役。</div>

<h2 style='color:#ff7b72'>⚠ 異常 {len(anomalies)} 項 —— 請先看這裡</h2>
{anom or "<div class='sub'>沒有偵測到異常。</div>"}

<h2>冠軍帳(ex60,bps)</h2>
<table><tr><th>區間</th><th>筆數</th><th>均值</th><th>t</th><th>勝率</th><th>拿掉前3大後均值</th></tr>
{kpi_row("樣本內(oos_from 之前)", S_in)}{kpi_row("樣本外", S_oos)}{kpi_row("全部", S_all)}</table>
<div class='sub'>冠軍篩選:<code>{_esc(cfg['champion']['filter'])}</code></div>

<h2>逐日(最近 15 日)</h2>
<table><tr><th>日期</th><th>筆數</th><th>均 ex60</th><th>區間</th></tr>{daily}</table>

<h2>最新每日檢討(含影子挑戰者)</h2>
<pre style='white-space:pre-wrap;font-size:12px'>{review}</pre>
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
