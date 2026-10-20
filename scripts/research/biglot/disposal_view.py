"""處置監測（/disposal）：三個分頁，資料全部來自收盤後落地的 JSON/CSV，本模組只讀取與呈現，不連外、不寫檔。

  warn   進處置預警 —— ① 已被列注意、差幾次進處置  ② 全市場尚未被列、逼近第 1/3 款門檻（同一張表、同一個列渲染）
  jail   處置中    —— 坐牢中與 7 日內出關（含今天出關）
  stats  歷史統計  —— 全部處置事件的九時點紅黑/開收

門檻一律讀 disposal_gap.json / disposal_early.json（由 scripts/research/disposal_gap.py、disposal_early.py 產生），
這裡不重算價格門檻。盤中即時價來自 collect_biglot_futprice.py 的 disposal_live_{date}.json。
與 `xq_style.py` 同一個 stale-reference 理由：只 `import biglot_dashboard`，函式體內用屬性存取。
"""
from __future__ import annotations

import csv
import datetime as dt
import html as html_mod
import json
import sqlite3
import time
from pathlib import Path

import biglot_dashboard
from stock_db import DATA_DIR, DEFAULT_DB_PATH

OUT_DIR = DATA_DIR / "disposal"
LIVE_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch"
FUT_MAP = Path(__file__).resolve().parents[3] / "config" / "taifex_stock_futures_map.json"
NEW_RULE = "2026-08-10"
TABS = (("warn", "進處置預警"), ("jail", "處置中"), ("stats", "歷史統計"))
TAB_ALIAS = {"all": "warn", "radar": "warn", "early": "warn", "daily": "warn", "clause1": "warn", "release": "jail"}
ICON = {3: "🔴", 2: "🟠", 1: "🟡"}
LVL_CLS = {3: "r3", 2: "r2", 1: "r1"}
CLAUSE_NAME = {"1": "價格", "2": "長期漲幅", "3": "價+量", "4": "價+週轉", "5": "", "6": "", "7": "", "8": "",
               "9": "本益比", "10": "累積週轉", "11": "價差+收最低", "12": "", "13": "當沖比"}
STATS_JSON = OUT_DIR / "disposal_stats.json"
_TTL = 120.0
_D: dict = {"t": 0.0}
_LIVE: dict = {"t": 0.0, "day": "", "data": {}}
esc = html_mod.escape


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _fut_map() -> dict[str, tuple[str, int]]:
    """代號→(期貨代碼, 每口股數)；標準 2000 股優先於小型 100 股。"""
    m: dict[str, tuple[str, int]] = {}
    for r in _json(FUT_MAP, {}).get("rows", []):
        if not r.get("is_future"):
            continue
        std = str(r.get("contract_size", "")).replace(",", "") == "2000"
        cur = m.get(r["stock_id"])
        if cur is None or (std and cur[1] != 2000):
            m[r["stock_id"]] = (r["futures_id"], 2000 if std else 100)
    return m


def _repeat_keys(win) -> set[tuple[str, str]]:
    """累犯 =（stock_id,start）集合：前次處置結束後 60 日內再被處置，或原因欄明寫第二次以上（櫃買公告沒有該字樣，故兩者並用）。"""
    out: set[tuple[str, str]] = set()
    prev_end: dict[str, str] = {}
    for w in sorted(win, key=lambda w: (w["stock_id"], str(w["start"]))):
        sid, st = w["stock_id"], str(w["start"])
        pe = prev_end.get(sid)
        gap = (dt.date.fromisoformat(st) - dt.date.fromisoformat(pe)).days if pe else None
        if any(k in str(w["measure"]) for k in ("第二次", "第三次", "第四次", "連續三次")) or (gap is not None and gap <= 60):
            out.add((sid, st))
        prev_end[sid] = str(w["end"])
    return out


def _bar_day() -> str:
    try:
        con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        return con.execute("select max(trade_date) from stock_daily_bars where trade_date>=date('now','-15 day')").fetchone()[0] or ""
    except Exception:  # noqa: BLE001
        return ""


def _data() -> dict:
    """檔案級快取：儀表板是常駐 server，每次請求重讀 CSV/JSON 會拖慢。"""
    if time.time() - _D["t"] < _TTL and "risk" in _D:
        return _D
    risk = _json(OUT_DIR / "disposal_risk.json", {"asof": "", "stocks": {}})
    win: list[dict] = []
    try:
        with (OUT_DIR / "disposal_windows.csv").open(encoding="utf-8") as fh:
            win = list(csv.DictReader(fh))
    except OSError:
        pass
    notices: list[dict] = []
    try:
        with (OUT_DIR / "notice_history.csv").open(encoding="utf-8") as fh:
            notices = list(csv.DictReader(fh))
    except OSError:
        pass
    # 代號→名稱備援（儀表板 NAMES 只涵蓋它自己的監測宇宙，處置股多半在宇宙外）；先到先得
    names = {s: v["name"] for s, v in risk["stocks"].items() if v.get("name")}
    for r in notices:
        if r.get("name"):
            names.setdefault(r["stock_id"], r["name"])
    for r in _json(OUT_DIR / "twse_punish_raw.json", []):
        if len(r) > 3 and r[3]:
            names.setdefault(str(r[2]), str(r[3]))
    # 近 14 天被公布、但不計次的款別（第 9~13 款）
    last = max((r["date"] for r in notices), default="")
    cut = (dt.date.fromisoformat(last) - dt.timedelta(days=14)).isoformat() if last else ""
    unc: dict[str, set[str]] = {}
    for r in notices:
        if r["date"] >= cut:
            for c in str(r.get("clauses") or "").split("|"):
                if c.isdigit() and int(c) >= 9:
                    unc.setdefault(r["stock_id"], set()).add(c)
    wpath = OUT_DIR / "disposal_windows.csv"
    _D.update(t=time.time(), risk=risk, win=win, names=names, unc=unc, repeat=_repeat_keys(win), px={}, bar_day=_bar_day(),
              gap=_json(OUT_DIR / "disposal_gap.json", {"stocks": {}, "watch": []}),
              early=_json(OUT_DIR / "disposal_early.json", {"stocks": {}, "order": []}),
              spread=_json(OUT_DIR / "disposal_spread.json", {}),
              win_day=dt.datetime.fromtimestamp(wpath.stat().st_mtime).strftime("%Y-%m-%d") if wpath.exists() else "",
              notice_day=last, fut=_fut_map())
    return _D


def _live(today: str) -> dict:
    """蒐集器每 ~15 秒覆寫的現貨即時價；回傳 {sid: {px,pc,vol,..}, "__mtime": 檔案時間}，不是今天的檔就視為沒有。"""
    if time.time() - _LIVE["t"] < 5 and _LIVE["day"] == today:
        return _LIVE["data"]
    f = LIVE_DIR / f"disposal_live_{today}.json"
    data = _json(f, {})
    if data:
        data["__mtime"] = f.stat().st_mtime
    _LIVE.update(t=time.time(), day=today, data=data)
    return data


def _closes(sids: list[str], n: int = 20) -> dict[str, list[tuple[str, float]]]:
    """各檔最近 n 根日線收盤（升冪），只抓 45 天（抓 90 天會讓冷啟動第一次請求超過瀏覽器 timeout）。"""
    px = _D["px"]
    need = [s for s in sids if s not in px]
    if need:
        got: dict[str, list] = {s: [] for s in need}
        try:
            con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
            rows = con.execute(
                f"select stock_id,trade_date,close from stock_daily_bars where stock_id in ({','.join('?' * len(need))}) "
                f"and trade_date>=date('now','-45 day') order by trade_date", need).fetchall()
            for sid, d, c in rows:
                if c and c > 0 and (not got[sid] or got[sid][-1][0] != d):
                    got[sid].append((d, float(c)))
        except Exception:  # noqa: BLE001
            pass
        for sid, arr in got.items():
            px[sid] = arr[-n:]
    return {s: px.get(s, []) for s in sids}


def _now_hm() -> str:
    return biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%H:%M")


def _live_state(today: str) -> tuple[dict, bool, bool]:
    """(即時報價, 是否新鮮, 是否盤中)。新鮮＝檔案 <90 秒；盤中沒有新鮮報價要在橫幅標紅。"""
    live = _live(today)
    mt = live.get("__mtime")
    # 收盤後蒐集器停寫：當天 13:30 後最後一次寫入就是收盤價，不能因超過 90 秒就退回前一日基準收盤
    final = bool(mt) and _now_hm() >= "13:35" and dt.datetime.fromtimestamp(mt, biglot_dashboard.TZ).strftime("%H:%M") >= "13:30"
    fresh = bool(mt) and (time.time() - mt < 90 or final)
    return live, fresh, "09:00" <= _now_hm() <= "13:35" and dt.date.fromisoformat(today).weekday() < 5

NEW_RULE_BANNER = (
    "<div class='note' style='border-left-color:#ffa657'><b>⚠ 2026-08-10 新制</b>：處置期 10→5 個營業日、"
    "撮合統一約 2 分鐘（舊制初犯 5 分／累犯 20–25 分）。本頁所有回測數字（多刀、空刀、九時點）"
    "<b>幾乎都來自舊制</b>；新制僅約百筆事件、尚不足以判斷是否沿用，<b>不要把舊制數字當成新制進場依據</b>。"
    "<br>2026-10-08 研究：多刀的報酬主要是持有處置窗的整體漂移（舊制任一天進場約 +3.5%），"
    "『第 3 天』『累幅&gt;−10%』『法人』『量縮』都無可辨識的增量；以收盤已達第一款價格門檻做的『快進處置放空』"
    "為負期望（新制後 n=28 −3.6%）；出關日開盤追強勢股（站上 5MA 且法人買）持有 3 日 −2.7%。</div>")


def _name(sid: str) -> str:
    return esc(str(biglot_dashboard.NAMES.get(sid) or _D["names"].get(sid, "")))


def _clause_html(clauses: str | None, sid: str = "") -> str:
    """款別欄：數字改成短名（滑鼠停留看說明），第 9~13 款灰字標「不計次」。"""
    cs = [c for c in str(clauses or "").split("|") if c]
    parts = [f"<span title='第{c}款'>{c}{CLAUSE_NAME.get(c, '')}</span>" for c in cs]
    unc = sorted(_D["unc"].get(sid, ()), key=int)
    if unc:
        parts.append("<span class='dim' title='第9~13款只公布、不計入處置次數'>+"
                     + "·".join(f"{c}{CLAUSE_NAME.get(c, '')}" for c in unc) + "(不計次)</span>")
    return " | ".join(parts) or "—"


def _freshness(today: str, live: dict, fresh: bool, in_mkt: bool, missing: int) -> str:
    """頁首資料新鮮度橫幅：各資料源的資料日，落後最新日線者與盤中報價中斷會標紅並寫出原因。"""
    d = _data()
    day, sp = d["bar_day"], d["spread"]
    gap_day, early_day = d["gap"].get("price_date") or d["gap"].get("asof", ""), d["early"].get("asof", "")
    now = dt.datetime.now(biglot_dashboard.TZ)
    items: list[tuple[str, str, str]] = []                      # (標籤, 資料日, 問題；空字串＝正常)

    def chk(label: str, val: str, why: str, bad: bool) -> None:
        items.append((label, val, why if bad else ""))

    late = now.weekday() < 5 and now.strftime("%H:%M") >= "19:00" and day < today
    chk("日線", day, "今日收盤資料尚未入庫（今天休市可忽略）", late or not day or (now.date() - dt.date.fromisoformat(day)).days > 4)
    chk("公告計數", d["risk"].get("asof", ""), f"尚未納入 {day} 的公告（收盤後約 18:30 更新）", d["risk"].get("asof", "") < day)
    chk("處置名單", d["win_day"], f"處置名單檔早於 {day}", d["win_day"] < day)
    chk("門檻", gap_day, f"disposal_gap.json 基準收盤 {gap_day} 早於日線 {day}", gap_day < day)
    chk("全市場預警", early_day, f"disposal_early.json 基準收盤 {early_day} 早於日線 {day}", early_day < day)
    chk("差幅地板", sp.get("asof", ""), f"disposal_spread.json {sp.get('asof', '')} 早於日線 {day}", sp.get("asof", "") < day)
    chk("本益比", sp.get("pe_date", ""), "本益比資料缺或過期，本益比除外情形未套用" if not sp.get("pe_date") else
        f"本益比 {sp['pe_date']} 早於日線 {day}", sp.get("pe_date", "") < day)
    if in_mkt:
        ts = dt.datetime.fromtimestamp(live["__mtime"], biglot_dashboard.TZ).strftime("%H:%M:%S") if live.get("__mtime") else "無"
        why = "盤中沒有新鮮即時報價（蒐集器未啟動或已停），現價與距離以基準收盤計算" if not fresh else f"{missing} 檔沒有即時報價（不在蒐集清單）"
        chk("即時報價", ts, why, not fresh or missing > 0)
    else:
        items.append(("即時報價", "非盤中", ""))
    bad = [f"<b>{lb}</b>：{why}" for lb, _, why in items if why]
    chips = " ｜ ".join(f"<span class='{'r3' if why else 'up'}'>{lb} {esc(v[5:] if len(v) == 10 else v) or '—'}</span>" for lb, v, why in items)
    head = "<b class='r3'>⚠ 資料不新鮮</b>：" + "；".join(bad) + "<br>" if bad else ""
    return f"<div class='fresh{' bad' if bad else ''}'>{head}{chips}</div>"


def _rules_html() -> str:
    return (
        "<details class='note'><summary>規則與欄位說明（點開）</summary>"
        "<p><b>進處置條件</b>：連續 3 日第 1 款／連續 5 日第 1~8 款任一／10 日內 6 日／30 日內 12 日（2026-08-10 起處置期 5 個營業日、"
        "撮合約 2 分鐘）。只有第 1~8 款計次，第 9~13 款（本益比、累積週轉、當沖比…）只公布不計次，名稱下以灰字標出；計數在上次處置期滿後重新起算。</p>"
        "<p><b>差</b>＝再被列注意幾次達標，取四個條件中最近的一個（差 0＝已達標，收盤後就公告）。"
        "下方灰字＝連續第 1 款天數／近 10 營業日被列天數（滿 6）／近 30 營業日被列天數（滿 12）。</p>"
        "<p><b>觸發價</b>＝<b>下一個收盤</b>要到多少，才會讓近 6 日累積漲跌（<b>日漲跌幅相加</b>，不是收盤價比率）達第 1 款："
        "&gt;32%，或 &gt;25% 且起迄價差 ≥50 元；另需比大盤平均與同類股平均各多 ≥20 個百分點，已換算成價格地板"
        "（以今日平均為基準、假設下一交易日他股不動）。<b>本益比為負或 ≥60 倍者類股條件不適用</b>，只看大盤（名稱旁標「本益比除外」）。"
        "自動取漲幅側或跌幅側中較近者。<b>已在觸發區</b>＝現價已越過，收盤守住即再中一次；"
        "<b>漲跌停內不可能</b>＝所需變動超過 ±10%。口徑以華新科 2492 公告對帳（10/7 36.39%、10/8 35.35%）。</p>"
        "<p><b>量能款</b>（②）：價格離第 1 款還 &gt;10%，但 25% 款＋成交量 ≥5 倍 60 日均量有機會；<b>第 3 款</b>＝所需下一交易日成交量、"
        "<b>第 4 款</b>＝週轉率 10% 的張數，「現」＝今日累計量佔比。</p>"
        "<p><b>款別</b>：<b>[事實]</b>第 5/6/8 款是狀態型條件（灰字「含5/6/8款」），今天因它被列者與價格無關，是否下一交易日再被列無法由日線預測。盤中只更新現價與距離。</p>"
        "<p><b>限制</b>：第 2 款（30/60/90 日翻倍）與第 5/6/8 款無法由日線預測；"
        "上櫃沿用上市校準，且上櫃公告歷史短，計數要累積滿 30 個營業日才完整；②近 5 日有單日 &gt;10.6% 者視為除權息/減資假象而排除；"
        "「可交易」＝前 60 日均成交值 ≥1 億或有個股期貨（暫定值）。</p>"
        "<p><b>個股期貨</b>：放空來回成本約 0.05–0.1%，融券約 0.5–0.7%（本系統估計，非券商報價；處置期間融券是否暫停未查證）；期交所對處置股期貨保證金會加成。</p>"
        "</details>")


def _warn_rows(d: dict, show_all: bool, today: str = "") -> list[dict]:
    """把 disposal_gap.json（①已被列）與 disposal_early.json（②全市場）整理成同一種列結構。"""
    gs, es, risk = d["gap"]["stocks"], d["early"]["stocks"], d["risk"]["stocks"]
    # risk 以資料日判斷處置中；週末/盤前公告、今天才生效的處置會被漏掉而顯示「已達標・今晚公告」
    jailed = {w["stock_id"] for w in d["win"] if today and str(w["end"]) >= today}
    a = sorted((s for s in (gs if show_all else d["gap"].get("watch", [])) if s in gs and s not in jailed),
               key=lambda s: (min(gs[s]["need"], 3), gs[s]["need_pct"], s))
    rows = [{**gs[s], "sid": s, "grp": 1, "route": "price", "tp": gs[s]["trig_px"], "pct": gs[s]["need_pct"],
             "cnt": risk.get(s, {})} for s in a]
    order = [s for s in d["early"].get("order", []) if s in es]
    for s in (order if show_all else [s for s in order if es[s].get("tradable") and es[s]["p"] >= 0.10]):
        if s in jailed:
            continue
        g, vol = es[s], es[s]["route"] == "vol"
        rows.append({**g, "sid": s, "grp": 2, "tp": g["vol_route_px"] if vol else g["trig_px"],
                     "pct": g["vol_route_pct"] if vol else g["need_pct"], "cnt": {}})
    return rows


def _warn_tr(r: dict, d: dict, live: dict, fresh: bool) -> tuple[str, bool]:
    sid, g1 = r["sid"], r["grp"] == 1
    q = live.get(sid) if fresh else None
    px = q["px"] if q else r["close"]
    chg = f"{(px / q['pc'] - 1) * 100:+.2f}%" if q and q.get("pc") else ("即時" if q else "基準收盤")
    up, vol_route = r["side"] == "up", r.get("route") == "vol"
    arrow, tp = ("≥" if up else "≤"), r["tp"]
    crossed = px >= tp if up else px <= tp
    cnt, need = r["cnt"], r.get("need", 9)
    if g1 and need <= 0:
        dist = "<td class='dim'>—</td>"
    elif g1 and not r["feasible"]:
        dist = "<td class='dim'>漲跌停內不可能</td>"
    elif crossed:
        dist = ("<td class='r1'><b>價已到</b><br><span class='dim'>還需爆量</span></td>" if vol_route else
                "<td class='r3'><b>已在觸發區</b><br><span class='dim'>收盤守住即列注意</span></td>")
    else:
        dist = f"<td class='r2'>{'再漲' if up else '再跌'} {abs(tp / px - 1) * 100:.2f}%</td>"
    if g1:
        count = ("<td class='r3'><b>已達標</b><br><span class='dim'>今晚公告</span></td>" if need <= 0 else
                 f"<td class='{LVL_CLS.get(r['level'], '')}'><b>差 {need}</b><br><span class='dim'>連{cnt.get('streak', 0)}·10中{cnt.get('in10', 0)}"
                 f"·30中{cnt.get('in30', 0)}</span></td>")
        clauses = _clause_html(cnt.get("clauses"), sid) + ("" if r["state"] == "未列" else f"<br><span class='dim'>{esc(r['state'])}</span>")
    else:
        count, clauses = "<td class='dim'>未列</td>", _clause_html("", sid)
    v_now = int(q["vol"]) if q and q.get("vol") else None
    vt = []
    for lab, lots in (("第3款", r.get("vol_route_lots")), ("第4款", r.get("to10_lots"))):
        if lots:
            vt.append(f"{lab} {lots:,} 張" + (f"（現 {v_now / lots * 100:.0f}%）" if v_now else ""))
    avg = f"60日均 {r['avg60_lots']:,} 張" + ("" if g1 else f" · 昨量 {r['vr']:.1f}×") if r.get("avg60_lots") else ""
    vcell = f"<td>{'<br>'.join(vt) or '—'}" + (f"<br><span class='dim'>{avg}</span>" if avg else "") + "</td>"
    fc = d["fut"].get(sid)
    futc = f"<td class='up'>✓ {esc(fc[0])}<br><span class='dim'>{fc[1]} 股/口</span></td>" if fc else "<td class='dim'>無</td>"
    sub = ["上市" if r["market"] == "TWSE" else "上櫃"]
    if not g1 and r.get("adv_yi") is not None:
        sub.append(f"均值 {r['adv_yi']:g} 億")
    if sid in d["spread"].get("pe_ex", ()):
        sub.append("<span title='本益比為負或≥60倍：類股差幅條件不適用'>本益比除外</span>")
    name = esc(r.get("name") or "") or _name(sid)
    trig = (f"<td>{arrow}{tp:g}<br><span class='dim'>{r['pct']:+.2f}% · 基準 {r['close']:g}"
            + ("" if g1 else f" · {'量能款' if vol_route else '價格款'}") + "</span></td>")
    tr = (f"<tr><td class='l {LVL_CLS.get(r.get('level', 0), '')}'>{ICON.get(r.get('level', 0), '')} {esc(sid)} {name}"
          f"<br><span class='dim'>{' · '.join(sub)}</span></td>"
          f"<td>{px:g}<br><span class='dim'>{chg}</span></td>{count}<td class='l'>{clauses}</td>{trig}{dist}"
          f"{vcell}{futc}</tr>")
    return tr, q is None


def _tab_warn(today: str, show_all: bool) -> tuple[str, int]:
    d, (live, fresh, _) = _data(), _live_state(today)
    rows = _warn_rows(d, show_all, today)
    head = {1: "① 已被列注意：差幾次進處置、下一交易日收盤要到多少才再被列", 2: "② 全市場尚未被列：逼近第 1/3 款門檻"}
    n_early = len(d["early"]["stocks"])
    toggle = ("　<a href='/disposal?tab=warn'>只看重點</a>" if show_all else
              f"　<a href='/disposal?tab=warn&all=1'>顯示全部（①{len(d['gap']['stocks'])} 檔、②{n_early} 檔）</a>")
    trs, grp, missing = [], 0, 0
    for r in rows:
        if r["grp"] != grp:
            grp = r["grp"]
            n = sum(1 for x in rows if x["grp"] == grp)
            scope = "" if show_all else "（差 ≤2 次或下一交易日可能觸發）" if grp == 1 else "（可交易、逼近門檻）"
            trs.append(f"<tr><td class='l grp' colspan='8'>{head[grp]} — {n} 檔{scope}{toggle if grp == 1 else ''}</td></tr>")
        tr, nq = _warn_tr(r, d, live, fresh)
        trs.append(tr)
        missing += nq
    cols = ["~代號 名稱", "現價", "差 / 計數", "~款別", "觸發價", "距離", "量能門檻", "個股期貨"]
    table = ("<table class='dz'><tr>" + "".join(f"<th class='l'>{c[1:]}</th>" if c[0] == "~" else f"<th>{c}</th>" for c in cols)
             + "</tr>" + "".join(trs) + "</table>")
    if not rows:
        table += "<div class='note'>目前沒有候選（disposal_gap.json / disposal_early.json 尚未產生，或沒有股票逼近條件）</div>"
    return table + _rules_html(), missing


def _tab_jail(today: str) -> tuple[str, int]:
    d, (live, fresh, _) = _data(), _live_state(today)
    ws = sorted((w for w in d["win"] if str(w["end"]) >= today), key=lambda w: (str(w["end"]), w["stock_id"]))
    px = _closes([w["stock_id"] for w in ws])
    trs, missing = [], 0
    for w in ws:
        sid, start, end = w["stock_id"], str(w["start"]), str(w["end"])
        q = live.get(sid) if fresh else None
        missing += q is None
        arr = list(px.get(sid) or [])
        if q and (not arr or arr[-1][0] < today):
            arr.append((today, float(q["px"])))
        inside = [c for dd, c in arr if dd >= start]
        cum = (inside[-1] / inside[0] - 1) * 100 if len(inside) >= 2 else None
        cur = arr[-1][1] if arr else None
        left = (dt.date.fromisoformat(end) - dt.date.fromisoformat(today)).days
        out = "<b class='r2'>★ 今天出關</b>" if left == 0 else f"{end[5:]}（{left} 天後）"
        cc = "up" if (cum or 0) > 0 else "dn" if cum is not None else ""
        new = start >= NEW_RULE
        trs.append(
            f"<tr><td class='l'>{esc(sid)} {_name(sid)}</td>"
            f"<td>{f'{cur:g}' if cur else '—'}</td>"
            f"<td class='l'>{esc(start[5:])}~{esc(end[5:])}</td><td class='l'>{out}</td>"
            f"<td class='l'>{'累犯' if (sid, start) in d['repeat'] else '初犯'}</td>"
            f"<td>{'未開始' if start > today else len(inside)}</td>"
            f"<td class='{cc}'>{f'{cum:+.2f}%' if cum is not None else '—'}</td>"
            f"<td class='l dim'>{'新制·回測不適用' if new else '舊制'}</td>"
            f"<td class='l'>{esc(str(w['measure'])[:18])}</td></tr>")
    cols = ["~代號 名稱", "現價", "~處置期間", "~出關", "~犯次", "第N天", "累幅", "~制度", "~原因"]
    table = ("<table class='dz'><tr>" + "".join(f"<th class='l'>{c[1:]}</th>" if c[0] == "~" else f"<th>{c}</th>" for c in cols)
             + "</tr>" + "".join(trs) + "</table>")
    note = (
        "<details class='note'><summary>規則與欄位說明（點開）</summary>"
        "<p><b>累幅</b>＝處置首日收盤至今（盤中用即時價）。<b>多刀</b>（舊制回測，超額 0050 扣 0.44%）：處置期內任一天收盤買、<b>出關日開盤</b>賣，"
        "平均約 +3.5%（累犯約 +5%）；<b>第幾天、距前高跌多少、法人、量縮都沒有可辨識的增量</b>，新制是否延續未知。</p>"
        "<p>出關日是多刀的出場點：報酬在處置期與出關跳空，出關後就沒有了，所以賣在<b>出關日開盤</b>；"
        "<b>不要在出關日開盤追進</b>（開盤進場到當日收 −1.0%、持有 3 日 −1.4%，t−3.4；出關前站上 5MA 且法人買超的反而最差，3 日 −2.7%）。</p>"
        "<p>⚠ 處置期為分盤撮合且須預收款券，<b>排隊成交率完全未驗證</b>；p5 −20.95%、單筆最差 −53%，尾部很重。</p></details>")
    return NEW_RULE_BANNER + table + note, missing


def _tab_stats() -> str:
    if not STATS_JSON.exists():
        return ("<div class='note'>尚無 disposal_stats.json。產生：<code>PYTHONPATH=src .venv/bin/python "
                "scripts/research/disposal_window_stats.py --json</code>（收盤後 daily_sync 會自動重算）。</div>")
    st = json.loads(STATS_JSON.read_text(encoding="utf-8"))

    def f(x, nd=2, sign=True):
        return "—" if x is None else (f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}")

    blocks = []
    for key in ("old_repeat", "old_first", "new"):
        g = st["groups"].get(key)
        if not g:
            continue
        trs = ""
        for r in g["rows"]:
            if "chg" not in r:
                trs += f"<tr><td class='l'>{r['point']}</td><td colspan='5' class='dim'>n={r['n']} 太少</td></tr>"
                continue
            trs += (f"<tr><td class='l'>{r['point']}</td><td>{r['n']}</td><td>{f(r['chg'])}%</td>"
                    f"<td>{r['red']:.1f}%</td><td>{r['black']:.1f}%</td><td>{f(r['oc'])}%</td><td>{f(r['t'])}</td></tr>")
        blocks.append(f"<h3>{esc(g['label'])}（事件 n={g['n']}）</h3><table class='dz'><tr><th class='l'>時點</th>"
                      "<th>n</th><th>平均漲跌</th><th>紅K</th><th>黑K</th><th>開收幅</th><th>t</th></tr>" + trs + "</table>")
    note = (f"<div class='note'>樣本＝自有處置事件 <b>{st['n_events']}</b> 筆（{esc(st['first'])} ~ "
            f"{esc(st['last'])}），重算於 {esc(st['generated'])}。<b>累犯</b>＝前次處置結束後 60 日內再被處置"
            "（上市櫃同口徑）。紅K/黑K＝收 vs 開（K 棒顏色）；平均漲跌＝收 vs 昨收；t 為開收幅對 0 的檢定（未做日期聚類，僅供參考）。"
            "<br>處置首日是舊制累犯的主要利空；<b>新制組樣本很小，數字不可當結論</b>。"
            "<br>重算：<code>PYTHONPATH=src .venv/bin/python scripts/research/disposal_window_stats.py --json</code></div>")
    return note + "".join(blocks)


def _shell(active: str, banner: str, body: str, refresh: int = 0) -> str:
    css = """<style>
    .tabs{margin:8px 0 12px}.tab,.tabon{display:inline-block;padding:5px 12px;margin-right:6px;border-radius:6px;
    text-decoration:none;font-size:13px;border:1px solid #333}.tab{color:#9aa;background:#181818}
    .tabon{color:#111;background:#d2a8ff;border-color:#d2a8ff;font-weight:600}
    table.dz{border-collapse:collapse;font-size:12px;width:100%}
    table.dz th{position:sticky;top:0;background:#1a1a1a;color:#8b949e;padding:5px 7px;text-align:right;white-space:nowrap}
    table.dz td{padding:4px 7px;border-bottom:1px solid #222;text-align:right;white-space:nowrap}
    table.dz td.l,table.dz th.l{text-align:left}table.dz td.grp{background:#1d1d24;color:#d2a8ff;font-weight:600;padding:7px}
    .r3{color:var(--status-error,#f85149);font-weight:700}.r2{color:#ffa657}.r1{color:#d2cf6b}
    .note{color:#8b949e;font-size:11px;line-height:1.7;margin:10px 0;padding:8px 10px;background:#141414;border-left:3px solid #333}
    .note summary{cursor:pointer;color:#c9d1d9}.note p{margin:6px 0}
    .fresh{font-size:11px;color:#8b949e;margin:6px 0 10px;padding:6px 10px;background:#141414;border-left:3px solid #3fb950}
    .fresh.bad{border-left-color:var(--status-error,#f85149);line-height:1.8}
    </style>"""
    tabs = " ".join(f"<a class='{'tabon' if k == active else 'tab'}' href='/disposal?tab={k}'>{label}</a>" for k, label in TABS)
    rf = f"<meta http-equiv='refresh' content='{refresh}'>" if refresh else ""
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>{rf}<title>處置監測</title>"
            f"{biglot_dashboard.ARC_CSS}{css}</head><body>"
            f"<div class='meta'><a href='/'>← 大戶儀表板</a> ｜ <b>處置監測</b></div>"
            f"<div class='tabs'>{tabs}</div>{banner}{body}</body></html>")


def render_disposal(tab: str = "warn", show_all: bool = False) -> str:
    _data()
    today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
    tab = TAB_ALIAS.get(tab, tab)
    tab = tab if tab in dict(TABS) else "warn"
    live, fresh, in_mkt = _live_state(today)
    missing = 0
    try:
        if tab == "warn":
            body, missing = _tab_warn(today, show_all)
        elif tab == "jail":
            body, missing = _tab_jail(today)
        else:
            body = NEW_RULE_BANNER + _tab_stats()
    except Exception as exc:  # noqa: BLE001
        body = f"<div class='note'>渲染失敗：{esc(str(exc))}</div>"
    refresh = 20 if (fresh and in_mkt and tab != "stats") else 0
    return _shell(tab, _freshness(today, live, fresh, in_mkt, missing), body, refresh)
