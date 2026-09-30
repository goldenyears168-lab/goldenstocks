"""處置分頁（/disposal）：注意累積計數、第一款價格門檻、坐牢中、今天出關、歷史統計。

資料全部來自收盤後落地的檔案（`fetch_disposal_list.py` + `disposal_risk_watch.py`），
本模組只做讀取與呈現，不連外、不寫檔。與 `xq_style.py` 同一個 stale-reference 理由：
只 `import biglot_dashboard`，函式體內用屬性存取。

五個分頁對照市面上的處置 APP，但每個數字都是我們自己算的：
  daily   處置日報 —— 還差幾次被關（計數，不是預測）
  clause1 第一款預測 —— 反解「明天/後天收盤到多少會觸發」
  jail    坐牢中 —— 累幅/剩天，附雙刀回測的進場判準
  release 今天出關 —— 多刀的出場日
  stats   歷史統計 —— 1,176 筆處置事件的九時點紅黑/開收
"""
from __future__ import annotations

import csv
import datetime as dt
import html as html_mod
import json
import sqlite3
import time

import biglot_dashboard
from stock_db import DATA_DIR, DEFAULT_DB_PATH

OUT_DIR = DATA_DIR / "disposal"
RISK_JSON = OUT_DIR / "disposal_risk.json"
WINDOWS_CSV = OUT_DIR / "disposal_windows.csv"

TABS = (("daily", "處置日報"), ("clause1", "第一款預測"), ("jail", "處置坐牢中"),
        ("release", "今天出關"), ("stats", "歷史統計"))
ICON = {3: "🔴", 2: "🟠", 1: "🟡", 0: "", -1: "⬛"}
_CACHE: dict = {"t": 0.0, "risk": None, "win": None, "px": {}}
_TTL = 120.0


def _load():
    """檔案級快取：儀表板是常駐 server，每次請求重讀 CSV/DB 會拖慢。"""
    now = time.time()
    if now - _CACHE["t"] < _TTL and _CACHE["risk"] is not None:
        return _CACHE["risk"], _CACHE["win"]
    risk = json.loads(RISK_JSON.read_text(encoding="utf-8")) if RISK_JSON.exists() else {"asof": "", "stocks": {}}
    win = []
    if WINDOWS_CSV.exists():
        with WINDOWS_CSV.open(encoding="utf-8") as fh:
            win = list(csv.DictReader(fh))
    _CACHE.update({"t": now, "risk": risk, "win": win, "px": {}})
    return risk, win


def _closes(sids: list[str], n: int = 12) -> dict[str, list[tuple[str, float]]]:
    """取各檔最近 n 根日線收盤（升冪）。結果併入快取，同一輪多個分頁共用。"""
    need = [s for s in sids if s not in _CACHE["px"]]
    if need:
        try:
            con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
            qs = ",".join("?" * len(need))
            rows = con.execute(
                f"select stock_id,trade_date,close from stock_daily_bars "
                f"where stock_id in ({qs}) and trade_date>=date('now','-90 day') order by trade_date", need).fetchall()
            got: dict[str, list] = {s: [] for s in need}
            for sid, d, c in rows:
                if c and c > 0 and (not got[sid] or got[sid][-1][0] != d):
                    got[sid].append((d, float(c)))
            for sid, arr in got.items():
                _CACHE["px"][sid] = arr[-n:]
        except Exception:  # noqa: BLE001
            for s in need:
                _CACHE["px"].setdefault(s, [])
    return {s: _CACHE["px"].get(s, []) for s in sids}


def clause1_thresholds(closes: list[tuple[str, float]]) -> dict:
    """反解第一款門檻：h 日後的『六個營業日累積漲跌』達標所需收盤價。

    法規：近 6 營業日累積漲跌 >32%（或 >25% 且起迄價差 ≥50 元）。
    基期 = 今天往前數第 (6−h) 根的收盤；h=1 即「明天」。
    2026-09-30 對帳市售 APP：睿生 6861 2 日門檻 341.2 vs APP 340（25% 款，基期 273、價差 68 元 ≥50 成立）。
    ⚠ 全友 2305 當時看似也對上（75.0 vs 74.8），但那檔 APP 標的是**第二款**、且 60→75 只差 15 元
      不滿足 25% 款的 50 元價差門檻 —— 是巧合而非驗證，正確的第一款門檻是 32% 款的 79.2。
    """
    out = {}
    if len(closes) < 6:
        return out
    cur = closes[-1][1]
    for h in (1, 2):
        base = closes[-(6 - h)][1] if len(closes) >= 6 - h else None
        if not base:
            continue
        up32, up25 = base * 1.32, base * 1.25
        dn32, dn25 = base * 0.68, base * 0.75
        # 25% 款需起迄價差 ≥50 元（高價股另有 300/450… 元級距，這裡只擋基本門檻）
        up = up25 if (up25 - base) >= 50 else up32
        dn = dn25 if (base - dn25) >= 50 else dn32
        out[h] = {"up": up, "dn": dn, "up_pct": (up / cur - 1) * 100, "dn_pct": (dn / cur - 1) * 100, "base": base}
    return out


def _tabs_html(active: str) -> str:
    items = []
    for key, label in TABS:
        cls = "tabon" if key == active else "tab"
        items.append(f"<a class='{cls}' href='/disposal?tab={key}'>{label}</a>")
    return "<div class='tabs'>" + " ".join(items) + "</div>"


def _shell(active: str, asof: str, body: str) -> str:
    css = """<style>
    .tabs{margin:8px 0 12px}.tab,.tabon{display:inline-block;padding:5px 12px;margin-right:6px;border-radius:6px;
    text-decoration:none;font-size:13px;border:1px solid #333}.tab{color:#9aa;background:#181818}
    .tabon{color:#111;background:#d2a8ff;border-color:#d2a8ff;font-weight:600}
    table.dz{border-collapse:collapse;font-size:12px;width:100%}
    table.dz th{position:sticky;top:0;background:#1a1a1a;color:#8b949e;padding:5px 7px;text-align:right;white-space:nowrap}
    table.dz td{padding:4px 7px;border-bottom:1px solid #222;text-align:right;white-space:nowrap}
    table.dz td.l,table.dz th.l{text-align:left}
    .r3{color:#ff6b6b;font-weight:700}.r2{color:#ffa657}.r1{color:#d2cf6b}.rj{color:#8b949e}
    .note{color:#8b949e;font-size:11px;line-height:1.7;margin:10px 0;padding:8px 10px;background:#141414;border-left:3px solid #333}
    </style>"""
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>處置監測</title>"
            f"{biglot_dashboard.ARC_CSS}{css}</head><body>"
            f"<div class='meta'><a href='/'>← 大戶儀表板</a> ｜ <b>處置監測</b> ｜ 資料日 {html_mod.escape(asof or '—')}</div>"
            f"{_tabs_html(active)}{body}</body></html>")


def _name(sid: str) -> str:
    return html_mod.escape(str(biglot_dashboard.NAMES.get(sid, "")))


def _tab_daily(risk) -> str:
    rows = sorted(((k, v) for k, v in risk["stocks"].items() if v["level"] >= 1),
                  key=lambda kv: (-kv[1]["level"], kv[1]["need"], -kv[1]["in30"]))
    trs = []
    for sid, v in rows[:80]:
        cls = {3: "r3", 2: "r2", 1: "r1", -1: "rj"}.get(v["level"], "")
        trs.append(
            f"<tr><td class='l {cls}'>{ICON.get(v['level'], '')} {html_mod.escape(sid)} {_name(sid)}</td>"
            f"<td class='l'>{html_mod.escape(v['market'])}</td><td>{v['streak']}</td><td>{v['in10']}</td>"
            f"<td>{v['in30']}</td><td class='{cls}'>{v['need']}</td>"
            f"<td class='l'>{html_mod.escape(v['clauses'] or '—')}</td>"
            f"<td class='l'>{html_mod.escape(v['last_date'])}</td>"
            f"<td class='l dim'>{html_mod.escape(v['label'])}</td></tr>")
    note = ("<div class='note'>處置觸發＝<b>連續 3 日達第一款</b>／<b>連續 5 日達第一~第八款</b>／"
            "<b>10 日內 6 日</b>／<b>30 日內 12 日</b>。「差」＝再被列注意幾次就達標；<b>差 0</b> 表示已達標、"
            "當天收盤後就會公告。<br>只有第一~第八款會累積；第 9~13 款（當沖比、本益比）被公布注意但不累積。"
            "計數在上次處置期滿後<b>重新起算</b>。<br>⚠ 上櫃只有當日快照可抓（無歷史 API），"
            "上櫃個股的計數要累積滿 30 個營業日才完整。</div>")
    return (note + "<table class='dz'><tr><th class='l'>代號</th><th class='l'>市場</th><th>連續</th>"
            "<th>10中</th><th>30中</th><th>差</th><th class='l'>款別</th><th class='l'>最後注意</th>"
            "<th class='l'>狀態</th></tr>" + "".join(trs) + "</table>")


def _tab_clause1(risk) -> str:
    cand = [k for k, v in risk["stocks"].items() if v["level"] >= 1 and not v["in_disposal"]][:60]
    px = _closes(cand)
    trs = []
    for sid in sorted(cand, key=lambda s: -risk["stocks"][s]["level"]):
        arr = px.get(sid) or []
        th = clause1_thresholds(arr)
        if not th:
            continue
        cur = arr[-1][1]
        v = risk["stocks"][sid]
        cells = []
        for h in (1, 2):
            t = th.get(h)
            if not t:
                cells.append("<td class='dim'>—</td>"); continue
            hit = "up" if t["up_pct"] <= 0 else ""
            cells.append(f"<td class='{hit}'>≥{t['up']:.1f}<br><span class='dim'>{t['up_pct']:+.2f}%</span></td>")
        trs.append(f"<tr><td class='l'>{ICON.get(v['level'], '')} {html_mod.escape(sid)} {_name(sid)}</td>"
                   f"<td>{cur:g}</td>" + "".join(cells) +
                   f"<td class='l dim'>{html_mod.escape(v['clauses'] or '—')}</td></tr>")
    note = ("<div class='note'>第一款＝近 6 個營業日（含當日）累積收盤漲跌 <b>&gt;32%</b>，"
            "或 <b>&gt;25% 且起迄價差 ≥50 元</b>（另需與全體及同類平均差幅 ≥20%，<b>本欄尚未檢查該條件</b>，"
            "故為寬鬆上界）。<br>門檻＝該日收盤要到多少才達標；括號是相對現價的漲幅，<b>負值表示現價已越過門檻</b>。"
            "<br>2026-09-30 對帳市售 APP：睿生 6861 2 日門檻 341.2 vs 340（25% 款成立）。"
            "本欄自動在 32%／25% 兩款中取較易觸發者，25% 款會先檢查 50 元價差條件。</div>")
    return (note + "<table class='dz'><tr><th class='l'>代號</th><th>現價</th><th>1日門檻</th>"
            "<th>2日門檻</th><th class='l'>目前款別</th></tr>" + "".join(trs) + "</table>")


def _tab_jail(risk, win, today: str) -> str:
    live = [w for w in win if str(w["start"]) <= today <= str(w["end"])]
    sids = [w["stock_id"] for w in live]
    px = _closes(sids, n=20)
    trs = []
    for w in sorted(live, key=lambda w: w["end"]):
        sid = w["stock_id"]; arr = px.get(sid) or []
        start = str(w["start"])
        inside = [(d, c) for d, c in arr if d >= start]
        cum = ((inside[-1][1] / inside[0][1] - 1) * 100) if len(inside) >= 2 else None
        nth = len(inside)                       # 處置期第幾個交易日
        repeat = "累犯" if ("第二次" in str(w["measure"]) or "第三次" in str(w["measure"])) else "初犯"
        sig = ""
        if repeat == "累犯" and nth >= 3 and cum is not None:
            sig = "🟢 多刀可進" if cum > -10 else "🔴 跌幅≥10%，訊號消失"
        cc = "up" if (cum or 0) > 0 else "dn" if cum is not None else ""
        trs.append(f"<tr><td class='l'>{html_mod.escape(sid)} {_name(sid)}</td>"
                   f"<td class='l'>{html_mod.escape(start)}~{html_mod.escape(str(w['end']))}</td>"
                   f"<td class='l'>{repeat}</td><td>{nth}</td>"
                   f"<td class='{cc}'>{f'{cum:+.2f}%' if cum is not None else '—'}</td>"
                   f"<td class='l'>{html_mod.escape(str(w['measure'])[:18])}</td>"
                   f"<td class='l'>{sig}</td></tr>")
    note = ("<div class='note'><b>多刀</b>（自有回測 1,478 筆事件，超額扣 0.44% 成本）：<b>累犯 ∧ 處置第 3 天 ∧ "
            "累幅 &gt; −10%</b> 買進、<b>出關日開盤</b>賣出 → 中位 +3.77%、正 62%、t+4.77；"
            "IS +5.78% / OOS +4.37% 皆顯著。累幅 ≤ −10% 那組只有 +0.18%（t+0.07），訊號消失。"
            "<br>⚠ 處置期為分盤撮合且須預收款券，<b>排隊成交率完全未驗證</b>；p5 −20.95%、單筆最差 −53%，尾部很重。</div>")
    return (note + "<table class='dz'><tr><th class='l'>代號</th><th class='l'>處置期間</th><th class='l'>犯次</th>"
            "<th>第N天</th><th>累幅</th><th class='l'>原因</th><th class='l'>訊號</th></tr>"
            + "".join(trs) + "</table>")


def _tab_release(win, today: str) -> str:
    out = []
    for w in win:
        end = str(w["end"])
        if today <= end <= (dt.date.fromisoformat(today) + dt.timedelta(days=7)).isoformat():
            out.append(w)
    trs = []
    for w in sorted(out, key=lambda w: w["end"]):
        sid = w["stock_id"]
        tag = "★ 今天出關" if str(w["end"]) == today else ""
        trs.append(f"<tr><td class='l'>{html_mod.escape(sid)} {_name(sid)}</td>"
                   f"<td class='l'>{html_mod.escape(str(w['start']))}~{html_mod.escape(str(w['end']))}</td>"
                   f"<td class='l'>{html_mod.escape(str(w['measure'])[:18])}</td>"
                   f"<td class='l up'>{tag}</td></tr>")
    note = ("<div class='note'>出關日是<b>多刀的出場點</b>：報酬拆解顯示（累犯 ∧ 累幅&gt;−10%）"
            "處置期內 +3.40%、<b>出關跳空 +2.12%</b>、出關日內 −0.41%、出關後 1→3 日 +0.43% —— "
            "報酬全在處置期與跳空，<b>出關後就沒有了</b>，所以賣在<b>出關日開盤</b>而不是收盤或之後。</div>")
    return (note + "<table class='dz'><tr><th class='l'>代號</th><th class='l'>處置期間</th>"
            "<th class='l'>原因</th><th class='l'></th></tr>" + "".join(trs) + "</table>")


def _tab_stats() -> str:
    rows = [("處前2", "+3.69", "51.5%", "35.1%", "+1.03", "+3.39"),
            ("處前1（公告日）", "+1.97", "43.9%", "45.7%", "+0.05", "+0.17"),
            ("處置1（首日）", "−3.15", "29.6%", "58.5%", "−1.53", "−5.68"),
            ("處置2", "−0.63", "40.9%", "46.6%", "−0.51", "−1.79"),
            ("處置3", "−0.12", "43.6%", "45.4%", "−0.18", "−0.70"),
            ("出前3", "+0.64", "48.2%", "45.1%", "−0.01", "−0.02"),
            ("出前1", "+1.84", "55.2%", "40.2%", "+1.28", "+4.35"),
            ("出關1", "+1.47", "44.3%", "46.8%", "−0.25", "−0.66"),
            ("出關2", "+0.36", "38.5%", "54.5%", "−0.75", "−2.33")]
    trs = "".join(
        f"<tr><td class='l'>{a}</td><td>{b}%</td><td>{c}</td><td>{d}</td><td>{e}%</td><td>{f}</td></tr>"
        for a, b, c, d, e, f in rows)
    note = ("<div class='note'>樣本＝自有 <b>1,176 筆處置事件（2019-12 ~ 2026-09、479 檔）</b>，"
            "此表為<b>累犯</b>（TWSE 明寫第二次以上，n=328）。紅K/黑K＝收 vs 開（K 棒顏色）；"
            "平均漲跌＝收 vs 昨收。<br><b>處前1 黑K 率只有 45.7%</b> —— 「處置前一天是黑K」不成立；"
            "真正的特徵是<b>振幅 7.58%（一般日的 1.9 倍）</b>、買在當日最高價收盤平均 −3.47%。"
            "<br>真正的利空在<b>處置首日</b>（累犯 −3.15%、黑K 58.5%、t−5.68），初犯只有 −0.51%。"
            "<br>重算：<code>PYTHONPATH=src .venv/bin/python scripts/research/disposal_window_stats.py</code></div>")
    return (note + "<table class='dz'><tr><th class='l'>時點</th><th>平均漲跌</th><th>紅K</th><th>黑K</th>"
            "<th>開收幅</th><th>t</th></tr>" + trs + "</table>")


def render_disposal(tab: str = "daily") -> str:
    risk, win = _load()
    today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
    tab = tab if tab in dict(TABS) else "daily"
    try:
        if tab == "daily":
            body = _tab_daily(risk)
        elif tab == "clause1":
            body = _tab_clause1(risk)
        elif tab == "jail":
            body = _tab_jail(risk, win, today)
        elif tab == "release":
            body = _tab_release(win, today)
        else:
            body = _tab_stats()
    except Exception as exc:  # noqa: BLE001
        body = f"<div class='note'>渲染失敗：{html_mod.escape(str(exc))}</div>"
    return _shell(tab, risk.get("asof", ""), body)
