"""biglot dashboard 重構：使用者手動輸入的持久化狀態——頁首自由筆記(`_load_notes`)、
個股筆記(`_stock_note_td`/`_save_stock_note`)、持倉監控事件日誌(`_hold_log`/`_hold_save`)、
持倉監控按鈕/自動出場判斷(`_hold_toggle`/`_hold_update`)、OOS 帳本讀取與彙總/收盤結算
(`_oos_load`/`_oos_summary`/`_oos_update_at_close`)。

依 docs/biglot-refactor-roadmap.md 訂下的規則搬移：只 `import biglot_dashboard`
（模組本身，不指名字），函式本體內對 `biglot_dashboard.py` 自己定義的全域一律用
`biglot_dashboard.NAME` 屬性存取（`NOTES_PATH`/`DEFAULT_NOTES`/`STOCK_NOTES_PATH`/
`STOCK_NOTES`/`HOLDS_PATH`/`HOLDS`/`HOLD_BAD`/`OOS_FILE`/`TZ`/`ST`/`NAMES`/
`PREV_CLOSE`/`RET_UNM`/`DAILY_TREND`/`UNI5`）——不只是「危險全域」（會被
`ingest()` 整包重新賦值的那 18 個，`DAILY_TREND`/`UNI5` 就在其中），是全部，
因為屬性存取本來就不比 bare name 貴，一律用同一個安全模式比逐一分類「這個要
不要小心」更不容易出錯。`DATA_DIR` 例外：它來自 `stock_db`、不是
`biglot_dashboard.py` 自己定義的全域，直接 `from stock_db import DATA_DIR` 匯入。

參見 scripts/research/biglot/xq_style.py 開頭的完整說明（同一套規則第一次
被端到端驗證的地方）。
"""
from __future__ import annotations

import html as html_mod
import json
import time

from stock_db import DATA_DIR

import biglot_dashboard


def _load_notes():
    try:
        s = biglot_dashboard.NOTES_PATH.read_text(encoding="utf-8")
        return s if s.strip() else biglot_dashboard.DEFAULT_NOTES
    except Exception:  # noqa: BLE001
        return biglot_dashboard.DEFAULT_NOTES


def _stock_note_td(sid):
    n = biglot_dashboard.STOCK_NOTES.get(sid) or {}
    txt = html_mod.escape(n.get("txt") or "")
    when = (n.get("t") or "")
    if n.get("d") and n["d"] != biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d"):
        when = f"{n['d'][5:]} {when}"
    return (f"<td class='snote'><span class='ne' contenteditable='true' spellcheck='false' data-sid='{sid}'>{txt}</span>"
            f"<span class='nt dim'>{when}</span></td>")


def _hold_save():
    biglot_dashboard.HOLDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    biglot_dashboard.HOLDS_PATH.write_text(json.dumps(biglot_dashboard.HOLDS, ensure_ascii=False, indent=0), encoding="utf-8")


def _hold_log(rec: dict):
    try:
        f = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"hold_events_{biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime('%Y-%m-%d')}.jsonl"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%H:%M:%S"), **rec}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _save_stock_note(sid, txt):
    now = biglot_dashboard.datetime.now(biglot_dashboard.TZ)
    biglot_dashboard.STOCK_NOTES[sid] = {"txt": txt[:2000], "t": now.strftime("%H:%M:%S"), "d": now.strftime("%Y-%m-%d")}
    biglot_dashboard.STOCK_NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    biglot_dashboard.STOCK_NOTES_PATH.write_text(json.dumps(biglot_dashboard.STOCK_NOTES, ensure_ascii=False, indent=0), encoding="utf-8")
    return biglot_dashboard.STOCK_NOTES[sid]["t"]


def _oos_load():
    try:
        return json.load(open(biglot_dashboard.OOS_FILE))
    except Exception:
        return {"intraday": [], "overnight": [], "overnight_pending": [],
            "overnight_short": [], "overnight_short_pending": []}


def _hold_toggle(sid: str, action: str, px):
    now = time.time()
    if action == "open" and sid not in biglot_dashboard.HOLDS:
        biglot_dashboard.HOLDS[sid] = {"t0": now, "hm": biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%H:%M:%S"), "px0": px, "low_since": None, "fired": []}
        _hold_log({"ev": "open", "sid": sid, "px0": px})
    elif action == "close" and sid in biglot_dashboard.HOLDS:
        h = biglot_dashboard.HOLDS.pop(sid); pnl = ((px / h["px0"] - 1) * 1e4) if (px and h.get("px0")) else None
        _hold_log({"ev": "close", "sid": sid, "px0": h.get("px0"), "px": px, "pnl_bps": pnl, "hold_min": (now - h["t0"]) / 60, "reason": "manual", "fired": h.get("fired", [])})
    _hold_save()


def _hold_update(rows):
    """每輪:持倉分=當下 V2.5;分數≤PAPER_EXIT_TH 連續 30 秒 / 壞標籤 / 60 分到期 → 出場旗標;獲利≥50 提示。首次觸發落地。

    門檻與紙上交易共用 `biglot_dashboard.PAPER_EXIT_TH`(2026-09-30 起為 −5),避免顯示給人看的
    提示與紙上帳跑兩套規則。
    """
    now = time.time()
    for r in rows:
        h = biglot_dashboard.HOLDS.get(r["sid"])
        if not h:
            r["hold"] = None; continue
        px = r.get("px"); sc = r.get("sc_v2"); items = [k for k, _ in (r.get("sc_v2_items") or [])]
        pnl = ((px / h["px0"] - 1) * 1e4) if (px and h.get("px0")) else None
        hold_min = (now - h["t0"]) / 60
        if sc is not None and sc <= biglot_dashboard.PAPER_EXIT_TH:
            if h.get("low_since") is None: h["low_since"] = now
        else:
            h["low_since"] = None
        flags = []
        if h.get("low_since") is not None and now - h["low_since"] >= 30:
            flags.append(f"分數≤{biglot_dashboard.PAPER_EXIT_TH:g}·30秒")
        bad = [k for k in items if any(k.startswith(b) for b in biglot_dashboard.HOLD_BAD)]
        if bad: flags.append("壞標籤:" + "/".join(x.split("(")[0] for x in bad))
        if hold_min >= 60: flags.append("到期60分")
        hint = "停利+50" if (pnl is not None and pnl >= 50) else ""
        for fl in flags:
            key = fl.split(":")[0]
            if key not in h.setdefault("fired", []):
                h["fired"].append(key); _hold_log({"ev": "flag", "sid": r["sid"], "flag": fl, "pnl_bps": pnl, "hold_min": hold_min, "score": sc})
        r["hold"] = {"hm": h["hm"], "px0": h.get("px0"), "pnl": pnl, "min": hold_min, "score": sc, "flags": flags, "hint": hint,
                     "low_s": (now - h["low_since"]) if h.get("low_since") is not None else 0}


def _oos_summary():
    o = _oos_load()
    parts = []
    it = o.get("intraday", [])
    if it:
        rets = [x["ret"] for x in it]
        parts.append(f"💎終版 {len(it)}筆 均{sum(rets)/len(rets):+.0f}bps "
                     f"勝{sum(1 for x in rets if x > 0)}/{len(rets)}")
    ov = o.get("overnight", [])
    if ov:
        rets = [x["ret"] for x in ov]
        parts.append(f"隔夜 {len(ov)}筆 均{sum(rets)/len(rets):+.0f}bps "
                     f"勝{sum(1 for x in rets if x > 0)}/{len(rets)}")
        # 影子分層:只留日線站上5日線(回測+63.5bps)——並行OOS,不改選股
        ma = [x["ret"] for x in ov if x.get("above_ma5") is True]
        if ma:
            parts.append(f"↳日線多{len(ma)}筆 均{sum(ma)/len(ma):+.0f}bps "
                         f"勝{sum(1 for x in ma if x > 0)}/{len(ma)}")
    sh = o.get("overnight_short", [])
    if sh:
        rets = [x["ret"] for x in sh]
        parts.append(f"隔夜空 {len(sh)}筆 均{sum(rets)/len(rets):+.0f}bps "
                     f"勝{sum(1 for x in rets if x > 0)}/{len(rets)}")
        ms = [x["ret"] for x in sh if x.get("above_ma5") is False]
        if ms:
            parts.append(f"↳日線空{len(ms)}筆 均{sum(ms)/len(ms):+.0f}bps "
                         f"勝{sum(1 for x in ms if x > 0)}/{len(ms)}")
    pend = len(o.get("overnight_pending", []))
    if pend:
        parts.append(f"待結算{pend}")
    return " | ".join(parts) if parts else "OOS帳本累積中"


def _oos_update_at_close():
    """收盤後:結算昨日隔夜腿、記今日盤中終版訊號、掛今日隔夜候選。冪等(按日期)。"""
    o = _oos_load()
    today = biglot_dashboard.ST.date
    if any(x.get("date") == today for x in o["intraday"]) or \
       any(x.get("date") == today for x in o["overnight_pending"]):
        return
    # a) 結算pending(用今日首價)
    still = []
    for p in o["overnight_pending"]:
        px0 = biglot_dashboard.ST.day.get(p["sid"], {}).get("px0")
        if px0 and p.get("close"):
            o["overnight"].append({**p, "resolve_date": today,
                                   "ret": (px0 / p["close"] - 1) * 1e4})
        else:
            still.append(p)
    o["overnight_pending"] = still
    # 做空腿結算:做空報酬=-(次開/今收-1)
    still_s = []
    for p in o.get("overnight_short_pending", []):
        px0 = biglot_dashboard.ST.day.get(p["sid"], {}).get("px0")
        if px0 and p.get("close"):
            o.setdefault("overnight_short", []).append(
                {**p, "resolve_date": today, "ret": -(px0 / p["close"] - 1) * 1e4})
        else:
            still_s.append(p)
    o["overnight_short_pending"] = still_s
    # b) 今日盤中終版訊號實績(三窗<5%∧pb5<0∧pb30<=-3千萬∧買>=3千萬>10%,45分)
    for sid, m in biglot_dashboard.ST.buckets.items():
        if sid in biglot_dashboard.RET_UNM:
            continue
        bks = sorted(m)
        for i in range(7, len(bks)):
            a = m[bks[i]]
            if not a["tot"] or a["big"] < 3e7 or a["big"] <= 0.10 * a["tot"]:
                continue
            shs = [m[bks[j]]["ret2"] / m[bks[j]]["tot"] * 100 if m[bks[j]]["tot"] else 99
                   for j in (i, i - 1, i - 2)]
            if max(shs) >= 5:
                continue
            if m[bks[i - 1]]["big"] >= 0 or sum(m[b]["big"] for b in bks[i - 6:i]) > -3e7:
                continue
            if i + 9 >= len(bks) or not a["px"] or not m[bks[i + 9]]["px"]:
                continue
            o["intraday"].append({"date": today, "sid": sid,
                                  "bucket": bks[i].strftime("%H:%M"),
                                  "ret": (m[bks[i + 9]]["px"] / a["px"] - 1) * 1e4,
                                  "gate": bool(biglot_dashboard.UNI5 is not None and biglot_dashboard.UNI5 < -5)})
    # c) 今日隔夜候選:做多3檔(佔比前10∧壓縮深)+ 做空3檔(佔比最負前10∧彈開最多∧日線空)
    cand = []
    for sid in biglot_dashboard.NAMES:
        ds = biglot_dashboard.ST.day.get(sid)
        px = biglot_dashboard.ST.last_px.get(sid)
        pc = biglot_dashboard.PREV_CLOSE.get(sid)
        if not ds or not px or not ds["tot"]:
            continue
        m = biglot_dashboard.ST.buckets.get(sid, {})
        bks = sorted(m)
        last12 = [m[b]["px"] for b in bks[-12:] if m[b]["px"]]
        if len(last12) < 8:
            continue
        cand.append({"sid": sid, "big": ds["big"], "close": px,
                     "cmp": px / (sum(last12) / len(last12)) - 1,
                     "locked": bool(pc and px / pc - 1 >= 0.09),
                     "above_ma5": biglot_dashboard.DAILY_TREND.get(sid, {}).get("above_ma5")})
    # 做多:大戶淨買>0∧未鎖漲停,佔比前10取壓縮最深3
    lp = [c for c in cand if c["big"] > 0 and not c["locked"]]
    lp = sorted(lp, key=lambda r: -r["big"])[:10]
    for p in sorted(lp, key=lambda r: r["cmp"])[:3]:
        o["overnight_pending"].append({
            "date": today, "sid": p["sid"], "close": p["close"],
            "above_ma5": p["above_ma5"]})
    # 做空:大戶淨賣<0,淨賣量最大前10取彈開最多3,再要日線↓空(回測+74.5/t3.82)
    sp = [c for c in cand if c["big"] < 0]
    sp = sorted(sp, key=lambda r: r["big"])[:10]
    for p in sorted(sp, key=lambda r: -r["cmp"])[:3]:
        o.setdefault("overnight_short_pending", []).append({
            "date": today, "sid": p["sid"], "close": p["close"],
            "above_ma5": p["above_ma5"]})
    json.dump(o, open(biglot_dashboard.OOS_FILE, "w"))
