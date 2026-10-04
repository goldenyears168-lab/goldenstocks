"""複製人代理人 —— 待批准區的純邏輯層（帳本讀寫 ＋ 卡片渲染）。

設計原則（刻意的硬邊界，對應 docs/trader-clone-agent-prd.md §1 階段 A）：

  1. **本模組不 import 任何富邦 SDK，也不 import `src/order/`。**
     構造上就不可能送出委託單。它只做一件事：把「代理人認為符合條件的候選」
     渲染成人看得懂的卡片，並把人的裁決寫進 append-only 帳本。
  2. 帳本是 A→C 升級的唯一資料來源（PRD §1 的同意率 ≥80% 就靠它算）。
     所以「略過」必須記原因，而且原因是選項化的、不是自由文字。
  3. `intent_id` 是 (日期, 時間, 標的, 方向, 掛價) 的雜湊，重複裁決只會覆寫同一筆，
     不會重複計數。

帳本位置：`${GOLDENSTOCKS_DATA_DIR}/data/clone_agent/`
  · `candidates_YYYY-MM-DD.jsonl` —— 代理人產生的候選（唯讀，由產生器寫）
  · `decisions_YYYY-MM-DD.jsonl`  —— 人的裁決（append-only）
"""
from __future__ import annotations

import hashlib
import html
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

SKIP_REASONS = [
    "不同意條件判斷（族群看錯）",
    "不同意條件判斷（大戶數字看錯）",
    "掛價太遠／掛不到",
    "掛價太近／不划算",
    "保證金不夠／已滿檔",
    "這檔我不碰",
    "時機不對（想再等）",
    "其他",
]


def _data_dir() -> Path:
    root = os.environ.get("GOLDENSTOCKS_DATA_DIR")
    base = Path(root) if root else Path(__file__).resolve().parents[2]
    d = base / "data" / "clone_agent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def intent_id(c: dict[str, Any]) -> str:
    key = f"{c.get('day')}|{c.get('time')}|{c.get('symbol')}|{c.get('side')}|{c.get('price')}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def load_candidates(day: str) -> list[dict[str, Any]]:
    p = _data_dir() / f"candidates_{day}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        c["intent_id"] = c.get("intent_id") or intent_id(c)
        out.append(c)
    return out


def load_decisions(day: str) -> dict[str, dict[str, Any]]:
    """後寫的覆蓋先寫的 —— 同一個 intent_id 只算最後一次裁決。"""
    p = _data_dir() / f"decisions_{day}.jsonl"
    if not p.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("intent_id"):
            out[d["intent_id"]] = d
    return out


def record_decision(day: str, iid: str, decision: str, skip_reason: str = "") -> dict[str, Any]:
    """寫入一筆裁決。**這個函式不會送出任何委託單**，只寫帳本。

    `iid` 必須存在於當天的候選檔，否則拒絕。沒有這道檢查的話，任何字串都會被寫進
    帳本，而 `agreement_stats()` 的分母是候選數、分子是比對得上的裁決數 —— 垃圾列
    會讓同意率看起來比實際低，而 A→C 升級門檻（≥80%）就是靠這個數字。
    """
    if decision not in ("approve", "skip"):
        raise ValueError(f"bad decision {decision!r}")
    if iid not in {c["intent_id"] for c in load_candidates(day)}:
        raise ValueError(f"unknown intent_id {iid!r} for {day}")
    rec = {"intent_id": iid, "decision": decision,
            "skip_reason": skip_reason if decision == "skip" else "",
            "decided_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    p = _data_dir() / f"decisions_{day}.jsonl"
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def agreement_stats(days: list[str]) -> dict[str, Any]:
    """PRD §1 的升級門檻：同意率 ≥80% 連續 20 個交易日。"""
    n = ok = 0
    per_day = []
    for d in days:
        cs = load_candidates(d)
        ds = load_decisions(d)
        dec = [ds[c["intent_id"]] for c in cs if c["intent_id"] in ds]
        a = sum(1 for x in dec if x["decision"] == "approve")
        n += len(dec); ok += a
        per_day.append({"day": d, "候選": len(cs), "已裁決": len(dec), "同意": a,
                        "同意率": round(100 * a / len(dec), 1) if dec else None})
    return {"總裁決": n, "總同意": ok,
            "同意率": round(100 * ok / n, 1) if n else None, "分日": per_day}


# ---------------------------------------------------------------- 渲染

_CSS = """
body{background:#11151a;color:#dfe6ee;font:13px/1.5 -apple-system,"PingFang TC",sans-serif;margin:0;padding:12px}
h1{font-size:16px;margin:0 0 4px}
.sub{color:#7f8c99;font-size:11px;margin-bottom:12px}
.warn{background:#3a2a12;border:1px solid #7a5a20;color:#f0c674;padding:8px 10px;border-radius:6px;
      font-size:12px;margin-bottom:14px}
.card{background:#1a2027;border:1px solid #2b343d;border-radius:8px;padding:10px 12px;margin-bottom:10px;
      max-width:620px}
.card.done{opacity:.45}
.hd{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.nm{font-size:15px;font-weight:600}
.t{color:#7f8c99;font-size:11px}
.px{margin:6px 0;font-size:12px;color:#b8c4d0}
.px b{color:#dfe6ee}
ul{margin:6px 0 8px;padding-left:18px}
li{margin:1px 0}
li.hit::marker{content:"✓ "}li.hit{color:#8fd19e}
li.bonus::marker{content:"+ "}li.bonus{color:#8ab4f8}
li.risk::marker{content:"⚠ "}li.risk{color:#f0c674}
.btns{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}
button{background:#2b6cb0;color:#fff;border:0;border-radius:5px;padding:6px 14px;font-size:13px;cursor:pointer}
button.sk{background:#3a434d}
select{background:#222a32;color:#dfe6ee;border:1px solid #39434d;border-radius:5px;padding:5px;font-size:12px}
.vd{font-size:12px;font-weight:600}
.vd.a{color:#8fd19e}.vd.s{color:#e08a7a}
.st{color:#7f8c99;font-size:11px;margin:14px 0 6px;border-top:1px solid #2b343d;padding-top:8px}
"""

_JS = """
async function dec(iid,d,el){
  const sel=el.closest('.card').querySelector('select');
  const r=await fetch('/decide',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({day:DAY,intent_id:iid,decision:d,skip_reason:d==='skip'&&sel?sel.value:''})});
  if(r.ok) location.reload(); else alert('寫入失敗 '+r.status);
}
"""


def _li(kind: str, text: str) -> str:
    return f"<li class='{kind}'>{html.escape(text)}</li>"


def render_card(c: dict[str, Any], dec: dict[str, Any] | None) -> str:
    iid = c["intent_id"]
    done = " done" if dec else ""
    hits = "".join(_li("hit", x) for x in c.get("hits", []))
    bonus = "".join(_li("bonus", x) for x in c.get("bonus", []))
    risks = "".join(_li("risk", x) for x in c.get("risks", []))
    if dec:
        lab = "已同意" if dec["decision"] == "approve" else f"已略過（{dec.get('skip_reason') or '未填'}）"
        cls = "a" if dec["decision"] == "approve" else "s"
        btns = f"<div class='btns'><span class='vd {cls}'>{html.escape(lab)}</span>" \
               f"<button class='sk' onclick=\"dec('{iid}','approve',this)\">改為同意</button></div>"
    else:
        opts = "".join(f"<option>{html.escape(r)}</option>" for r in SKIP_REASONS)
        btns = (f"<div class='btns'><button onclick=\"dec('{iid}','approve',this)\">確認（記錄同意）</button>"
                f"<select>{opts}</select>"
                f"<button class='sk' onclick=\"dec('{iid}','skip',this)\">略過</button></div>")
    return f"""<div class='card{done}'>
 <div class='hd'><span class='nm'>{html.escape(str(c.get('uname','')))}
   <span class='t'>{html.escape(str(c.get('symbol','')))}</span></span>
  <span class='t'>{html.escape(str(c.get('time','')))} · {html.escape(str(c.get('action','新倉買進')))}</span></div>
 <div class='px'>掛價 <b>{c.get('price')}</b> · {html.escape(str(c.get('price_note','')))}
   · 口數 <b>{c.get('lot',1)}</b> · 每口保證金 <b>{c.get('margin',0):,.0f}</b>
   · 送出後持倉腿 {html.escape(str(c.get('leg_note','—')))}</div>
 <ul>{hits}{bonus}{risks}</ul>
 {btns}
</div>"""


def render_page(day: str) -> str:
    cs = load_candidates(day)
    ds = load_decisions(day)
    cards = "".join(render_card(c, ds.get(c["intent_id"])) for c in cs) or \
        "<div class='sub'>這一天沒有候選。用 scripts/research/trader_build_approval_candidates.py 產生。</div>"
    st = agreement_stats([day])
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>待批准區 {day}</title><style>{_CSS}</style>
<h1>待批准區 · {day}</h1>
<div class='sub'>複製人代理人 階段 A ——「條件成立 → 跳確認鍵」。候選 {len(cs)} 筆。</div>
<div class='warn'>這個頁面<b>不會送出任何委託單</b>。本服務沒有 import 富邦 SDK，構造上無法下單。
按「確認」只是把你的同意寫進帳本，供 A→C 升級的同意率統計使用。真正的送單要等
PRD §8 的下單層建好、四層鎖打開。</div>
{cards}
<div class='st'>已裁決 {st['總裁決']}/{len(cs)} · 同意 {st['總同意']} ·
 同意率 {st['同意率'] if st['同意率'] is not None else '—'}%（升級門檻：連續 20 日 ≥80%）</div>
<script>const DAY={json.dumps(day)};{_JS}</script>"""
