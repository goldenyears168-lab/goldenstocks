"""/guard 撤單神器頁面：說明、開關按鈕、即時監控狀態。資料全部來自 data/order/cancel_guard_*.json（worker 每秒寫）。

開關寫入 data/order/cancel_guard_switch.json（與 scripts/order/cancel_guard_switch.py 同一份）。
無驗證，綁所有介面（比照 /agentorderswitch，使用者 2026-10-16 已接受此風險）。
"""
from __future__ import annotations

import html as H
import json
from datetime import datetime

from order.cancel_guard import ORDER_DIR, TZ, load_switch, save_switch

MODES = {"off": dict(enabled=False, auto_cancel=False), "alert": dict(enabled=True, auto_cancel=False), "on": dict(enabled=True, auto_cancel=True)}


def _j(name: str, default):
    try:
        return json.loads((ORDER_DIR / name).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def post_switch(q: dict) -> dict:
    """POST /guardswitch。mode=off|alert|on；另可帶 apply_to(list)、against_ticks(float)、max_cancels_per_day(int)。"""
    upd: dict = {}
    if "mode" in q:
        if q["mode"] not in MODES:
            raise ValueError("mode must be off/alert/on")
        upd.update(MODES[q["mode"]])
    if "apply_to" in q:
        at = q["apply_to"]
        if not isinstance(at, list) or not at or any(x not in ("New", "Close") for x in at):
            raise ValueError("apply_to must be a non-empty list of New/Close")
        upd["apply_to"] = at
    if "against_ticks" in q:
        v = float(q["against_ticks"])
        if not 0.5 <= v <= 5:
            raise ValueError("against_ticks must be within 0.5..5")
        upd["against_ticks"] = v
    if "max_cancels_per_day" in q:
        v = int(q["max_cancels_per_day"])
        if not 1 <= v <= 200:
            raise ValueError("max_cancels_per_day must be within 1..200")
        upd["max_cancels_per_day"] = v
    return save_switch(upd, by="dashboard")


def _mode_label(st: dict, sw: dict) -> tuple[str, str]:
    if not sw["enabled"]:
        return "關閉", "#6e7681"
    if not sw["auto_cancel"]:
        return "只通知（不撤單）", "#d29922"
    if st.get("dry_run_env", True):
        return "自動撤單・演練中（.env 仍為 DRY_RUN，不會真的撤）", "#d29922"
    return "自動撤單・實彈", "#da3633"


def render_frag() -> str:
    sw = load_switch(); st = _j("cancel_guard_status.json", {})
    now = datetime.now(TZ)
    age = None
    try:
        age = (now - datetime.fromisoformat(st["ts"])).total_seconds()
    except Exception:  # noqa: BLE001
        pass
    alive = age is not None and age < 15
    label, color = _mode_label(st, sw)
    worker = ("<span style='color:#3fb950'>● 運作中</span>" + ("（非交易時段待命）" if st.get("idle") else "")) if alive else "<span style='color:#da3633'>● 未運作（尚未安裝或已停止）</span>"
    locks = (f"<tr><td>開關檔</td><td>{'開' if sw['enabled'] else '關'} ／ 自動撤單 {'開' if sw['auto_cancel'] else '關'}</td></tr>"
             f"<tr><td>.env DRY_RUN</td><td>{'1（演練，不撤）' if st.get('dry_run_env', True) else '0（可實撤）'}</td></tr>"
             f"<tr><td>ORDER_MASTER_ENABLED</td><td>{H.escape(str(st.get('order_master_enabled', '?')))}</td></tr>"
             f"<tr><td>監控類型 ／ 門檻 ／ 每日上限</td><td>{'＋'.join('新倉' if x == 'New' else '平倉' for x in sw['apply_to'])} ／ 往不利 ≥{sw['against_ticks']:g} tick ／ {sw['max_cancels_per_day']} 次（今日已撤 {st.get('cancels_today', 0)}）</td></tr>")
    rows = ""
    for o in st.get("orders", []):
        ag = o.get("against_ticks")
        agtxt = "—" if ag is None else f"{ag:+.1f}"
        hot = ag is not None and ag >= sw["against_ticks"] * 0.7
        rows += (f"<tr><td>{H.escape(o['code'])}</td><td>{o['side']}{'(平倉)' if o['type'] == 'Close' else ''}</td><td>{o['price']:g}</td>"
                 f"<td>{'—' if o.get('ref_mid') is None else format(o['ref_mid'], '.2f')}</td><td>{'—' if o.get('mid') is None else format(o['mid'], '.2f')}</td>"
                 f"<td style='{'color:#f0883e;font-weight:600' if hot else ''}'>{agtxt}</td><td>{o['state']}</td><td>{o['age_s']:.0f}s</td></tr>")
    orders = (f"<table class=gt><tr><th>商品</th><th>方向</th><th>掛價</th><th>掛單時中價</th><th>現在中價</th><th>往不利(tick)</th><th>狀態</th><th>已掛</th></tr>{rows}</table>"
              if rows else "<div class=dim>目前沒有被監控的委託（只監控個股期貨的被動限價單；你掛單後約 1 秒內會出現在這裡）。</div>")
    alerts = _j("cancel_guard_alerts.json", [])[-15:][::-1]
    arows = "".join(f"<tr><td>{H.escape(a.get('ts', '')[11:19])}</td><td>{H.escape(a.get('code', ''))}</td><td>{a.get('side', '')}{'(平倉)' if a.get('type') == 'Close' else ''}</td><td>{a.get('price', 0):g}</td>"
                    f"<td>{a.get('against_ticks')}</td><td>{H.escape(str(a.get('action')))}</td><td>{a.get('cancel_roundtrip_s', '')}</td></tr>" for a in alerts)
    trig = (f"<table class=gt><tr><th>時間</th><th>商品</th><th>方向</th><th>掛價</th><th>往不利(tick)</th><th>動作</th><th>撤單來回(秒)</th></tr>{arows}</table>"
            if arows else "<div class=dim>尚無觸發紀錄。</div>")
    cov = st.get("coverage", [])
    covtxt = "、".join(f"{c['name']}({c['code']}{'·小' if c['shares'] == 100 else ''})" for c in cov) or "（尚未取得）"
    return (f"<div class=card><div class=big style='color:{color}'>目前模式：{label}</div><div>撤單神器程式：{worker}</div></div>"
            f"<div class=card><b>鎖與設定</b><table class=gt>{locks}</table></div>"
            f"<div class=card><b>監控中的委託</b>{orders}</div>"
            f"<div class=card><b>最近觸發</b>{trig}</div>"
            f"<div class=card><b>目前涵蓋的個股期貨（{len(cov)} 檔，買賣簿仍在更新）</b><div class=dim style='line-height:1.8'>{H.escape(covtxt)}</div>"
            f"<div class=dim>不在這裡的商品：你的單會被略過、不會誤撤；要新增請告訴 Claude 加入買賣簿收集器。</div></div>")


def render_page() -> str:
    sw = load_switch()
    types = sw["apply_to"]
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=0.8">
<title>撤單神器</title><style>
body{{background:#0d1117;color:#c9d1d9;font:13px/1.6 -apple-system,'PingFang TC',monospace;margin:8px 12px;max-width:1100px}}
h2{{margin:6px 0}} h3{{margin:14px 0 4px;color:#e3b341}} .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 12px;margin:8px 0}}
.big{{font-size:18px;font-weight:700;margin-bottom:4px}} .dim{{color:#8b949e}} table.gt{{border-collapse:collapse;margin-top:6px;width:100%}}
table.gt td,table.gt th{{border-bottom:1px solid #21262d;padding:3px 8px;text-align:left}} th{{color:#8b949e;font-weight:500}}
.btns button{{font-size:14px;padding:8px 18px;margin-right:8px;border-radius:6px;border:1px solid #30363d;background:#21262d;color:#c9d1d9;cursor:pointer}}
.btns button.sel{{background:#1f6feb;color:#fff;border-color:#1f6feb}} .btns button.danger.sel{{background:#da3633;border-color:#da3633}}
.warn{{color:#f0883e}} details{{margin:6px 0}} summary{{cursor:pointer;color:#79c0ff}} li{{margin:2px 0}}
input,select{{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:2px 6px}}
</style></head><body>
<h2>🛡 撤單神器</h2>
<div class="card">
<div class="btns"><b>開關：</b>
<button id="b-off" onclick="setMode('off')">關閉</button>
<button id="b-alert" onclick="setMode('alert')">只通知</button>
<button id="b-on" class="danger" onclick="setMode('on')">自動撤單</button>
<span id="msg" class="dim"></span></div>
<div style="margin-top:8px">監控：<label><input type="checkbox" id="t-new" {'checked' if 'New' in types else ''}> 新倉單</label>
<label style="margin-left:10px"><input type="checkbox" id="t-close" {'checked' if 'Close' in types else ''}> 平倉單</label>
<span class="dim">（撤掉平倉單後，該筆部位暫時沒有出場單）</span>
　門檻：中價往不利 ≥ <select id="agt">{''.join(f'<option value="{v}" {"selected" if abs(sw["against_ticks"]-v)<1e-9 else ""}>{v:g}</option>' for v in (0.5,1,1.5,2,3))}</select> tick
　每日撤單上限：<input id="cap" type="number" min="1" max="200" value="{sw['max_cancels_per_day']}" style="width:60px">
<button onclick="saveCfg()" style="padding:2px 10px">儲存設定</button></div>
</div>
<div id="live">{render_frag()}</div>

<h3>這是什麼</h3>
<div class="card">
<p>你掛了一張<b>被動限價單</b>（買在買一、賣在賣一附近等人來成交）之後，如果<b>買賣簿的中價往對你不利的方向走了 ≥1 tick</b>——買單遇到中價下跌、賣單遇到中價上漲——代表成交很可能是在行情對你不利的時候發生。這時神器會<b>通知你，或直接幫你把這張單撤掉</b>。</p>
<ul>
<li>它<b>只能撤單</b>，程式裡沒有任何下單、改價的功能。</li>
<li>你手動在富邦 App 下的單它也看得到（讀的是帳戶層級的委託回報），不用事先告訴它。</li>
<li>範圍：期交所個股期貨清單內、而且我們有收買賣簿的商品（上面「目前涵蓋」那一排）。指數期貨（大台、小台、微台）不在範圍。</li>
<li>跨價差、馬上就會成交的單不算被動單，不處理。</li>
</ul>
<h3>三個模式</h3>
<ul>
<li><b>關閉</b>：完全不動作；仍會讀資料讓上面的「監控中的委託」可以顯示。</li>
<li><b>只通知</b>：符合條件寄 email、記錄在「最近觸發」，由你自己撤單。</li>
<li><b>自動撤單</b>：符合條件直接送出撤單。需要 .env 的 <code>ORDER_CANCEL_GUARD_DRY_RUN=0</code> 與 <code>ORDER_MASTER_ENABLED=1</code> 才會真的撤；否則只記錄「會撤」。</li>
</ul>
<h3>為什麼這樣做（證據）</h3>
<div class="card">
<p>用歷史逐筆資料模擬 23 個日盤、約 33 萬張被動單（掛在買一／賣一，等 300 秒）：</p>
<table class="gt"><tr><th></th><th>成交率</th><th>每筆成交後 60 秒走勢</th></tr>
<tr><td>不撤單</td><td>45%</td><td>−0.58 tick（平均吃虧）</td></tr>
<tr><td>撤單規則・延遲 2 秒</td><td>23%</td><td>−0.28 tick</td></tr>
<tr><td>撤單規則・延遲 0 秒</td><td>12%</td><td>+0.18 tick</td></tr></table>
<p class="dim">走勢＝成交後 60 秒的中價相對成交價（負＝成交後行情往不利方向走）。撤得越快越有用；成交率下降是代價。台指校正價、OFI（委託流失衡）都沒有比「中價往不利 ≥1 tick」更好，所以用最簡單的規則。完整紀錄：docs/ofi-cancel-prereg-20261021.md。</p></div>
<h3>風險與限制</h3>
<ul>
<li class="warn">撤單來回需要多久還沒量過（需要實際撤一張單才知道）；實際延遲越長，效果越接近上表的 2 秒或更差。</li>
<li class="warn">這是歷史模擬，不保證未來；它降低「被逆向成交」，也會讓你少成交。</li>
<li class="warn">複製人代理人若實彈運作，它可能在神器撤單後重掛，造成來回；目前代理人是關閉的。</li>
<li>每張單最多撤一次；收集器 30 秒沒寫任何買賣簿檔時不會撤單（只通知）；結算日換月後不同合約的價格不會互比。</li>
<li>無登入驗證（比照 /agentorderswitch），請只在內網使用。</li>
</ul>
</div>
<script>
const sel=m=>['off','alert','on'].forEach(k=>document.getElementById('b-'+k).classList.toggle('sel',k===m));
function curMode(){{return {json.dumps('off' if not sw['enabled'] else ('on' if sw['auto_cancel'] else 'alert'))}}}
sel(curMode());
async function post(obj){{const r=await fetch('/guardswitch',{{method:'POST',body:JSON.stringify(obj)}});if(!r.ok)throw new Error(await r.text());return r.json();}}
async function setMode(m){{
  if(m==='on'&&!confirm('開啟「自動撤單」：符合條件時會直接撤掉你的委託單。確定嗎？'))return;
  const msg=document.getElementById('msg');
  try{{const j=await post({{mode:m}});sel(m);msg.textContent='已設定：'+m+'（'+(j.updated_at||'')+'）';refresh();}}catch(e){{msg.textContent='失敗：'+e.message;}}
}}
async function saveCfg(){{
  const at=[];if(document.getElementById('t-new').checked)at.push('New');if(document.getElementById('t-close').checked)at.push('Close');
  const msg=document.getElementById('msg');
  if(!at.length){{msg.textContent='至少選一種委託類型（或把開關設為關閉）';return;}}
  try{{await post({{apply_to:at,against_ticks:parseFloat(document.getElementById('agt').value),max_cancels_per_day:parseInt(document.getElementById('cap').value)}});msg.textContent='設定已儲存';refresh();}}catch(e){{msg.textContent='失敗：'+e.message;}}
}}
async function refresh(){{try{{const r=await fetch('/guardfrag');if(r.ok)document.getElementById('live').innerHTML=await r.text();}}catch(e){{}}}}
setInterval(refresh,2000);
</script></body></html>"""
