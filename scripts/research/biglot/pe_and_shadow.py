"""biglot dashboard 重構：`_pe_peer_block` 讀 `PE_TABLE`/`PE_GEN`/`SUBCAT`，
`_shadow_triple` 讀 `SHADOW`/`WRT`——`PE_TABLE`/`PE_GEN`/`WRT` 都是
docs/biglot-refactor-roadmap.md 列出的「每日整包重新賦值」全域（`ingest()`/`_load_pe_peer()`
過日重載會用 `global WRT; WRT = {...}` 這種寫法整包換掉物件）。`SUBCAT` 是靜態 dict 不會被
重新賦值，`SHADOW` 是模組層級 dict 只被原地 mutate（`SHADOW["date"] = ...`），但為了跟
危險全域的存取方式保持一致、降低未來誤判風險，一律用同一套屬性存取寫法。

如果這裡跟 Phase 1 的 biglot/utils.py 一樣用 `from biglot_dashboard import PE_TABLE`，
搬過來的名字只會抓到「當下那一刻」的參照，過日後 `biglot_dashboard.py` 自己那份
換了新物件，這裡卻還指著昨天的舊物件——這正是路線圖裡標注過的 stale-reference 風險。

正確做法（比照 biglot/xq_style.py）：只 `import biglot_dashboard`（模組本身，不指名字），
函式本體內用 `biglot_dashboard.PE_TABLE` 等屬性存取——這是屬性查找，每次呼叫都會拿到當下
biglot_dashboard 模組裡最新的值，不管 ingest() 換過幾次日都不會過期。
"""
from __future__ import annotations

import html as html_mod
import json

import biglot_dashboard
from biglot.utils import bucket_key
from stock_db import DATA_DIR


def _shadow_triple(rows, now):
    """權證三條件影子帳(不顯示、不進訊號、不進OOS記分)。
    對照組=已驗證的『主力點火5分』兩腳:5分大戶淨買≥3千萬 ∧ 散買%<5%(非高價股不可測)。
    每檔每個5分窗第一次成立時記一筆,附當下權證欄位(活動量+簽號),之後自動補 5分/30分/收盤價;
    隔夜由離線分析從 stock_daily_bars 補。這樣任何權證門檻都能離線測,且能算對『兩腳單獨』的增量。
    檔案:cache/biglot_live_watch/warrant_triple_shadow_{date}.json(整檔覆寫,重啟時讀回)。"""
    d = now.strftime("%Y-%m-%d")
    f = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"warrant_triple_shadow_{d}.json"
    if biglot_dashboard.SHADOW["date"] != d:
        biglot_dashboard.SHADOW["date"], biglot_dashboard.SHADOW["events"] = d, []
        try:
            if f.exists():
                biglot_dashboard.SHADOW["events"] = json.loads(f.read_text())
        except Exception:
            biglot_dashboard.SHADOW["events"] = []
    hm = now.strftime("%H:%M")
    bk = bucket_key(now).strftime("%H:%M")
    seen = {(e["sid"], e["bk"]) for e in biglot_dashboard.SHADOW["events"]}
    changed = False
    for r in rows:
        if (r.get("unm") or r.get("big5") is None or r.get("rbuy5") is None
                or not r.get("px") or hm >= "13:25" or (r["sid"], bk) in seen):
            continue
        if r["big5"] >= 3e7 and r["rbuy5"] < 5:
            w = biglot_dashboard.WRT.get(r["sid"]) if isinstance(biglot_dashboard.WRT.get(r["sid"]), dict) else {}
            biglot_dashboard.SHADOW["events"].append({
                "sid": r["sid"], "bk": bk, "t": now.strftime("%H:%M:%S"), "ts": now.timestamp(),
                "px0": r["px"], "big5": r["big5"], "rbuy5": r["rbuy5"], "big30": r.get("big30"),
                "r5": r.get("w_ret"), "r30": r.get("r30"), "rvol5": r.get("rvol5"),
                "call_5": w.get("call_5"), "put_5": w.get("put_5"),
                "bull_5": w.get("bull_5"), "bear_5": w.get("bear_5"),
                "bull_30": w.get("bull_30"), "bear_30": w.get("bear_30"),
                "n_call": w.get("n_call"), "px5": None, "px30": None, "pxc": None})
            changed = True
    pxnow = {r["sid"]: r["px"] for r in rows if r.get("px")}
    tnow = now.timestamp()
    for e in biglot_dashboard.SHADOW["events"]:
        p = pxnow.get(e["sid"])
        if not p:
            continue
        if e["px5"] is None and tnow >= e["ts"] + 300:
            e["px5"] = p; changed = True
        if e["px30"] is None and tnow >= e["ts"] + 1800:
            e["px30"] = p; changed = True
        if e["pxc"] is None and hm >= "13:30":
            e["pxc"] = p; changed = True
    if changed:
        try:
            f.write_text(json.dumps(biglot_dashboard.SHADOW["events"], ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def _pe_peer_block(sid):
    """本益比同族群成員清單(2026-09-25 jack 交辦:『同族群的名單請你放進HTML的個別頁面上面,
    讓我們知道你研究認為跟誰比較』)。見 _load_pe_peer / _score_rows 的 pe_live 計算說明。"""
    grp = biglot_dashboard.SUBCAT.get(sid)
    rows = biglot_dashboard.PE_TABLE.get(grp, [])
    if not rows:
        return ""
    trs = []
    for r in rows:
        is_self = r["sid"] == sid
        pe_disp = f"{r['pe']:.1f}" if r.get("pe") is not None else "虧損/無EPS"
        rank_disp = f"第{r['rank']}/{r['n_valid']}低" if r.get("rank") is not None else "—"
        row_cls = " class='self'" if is_self else ""
        trs.append(f"<tr{row_cls}><td>{r['sid']}</td><td>{html_mod.escape(r.get('name') or r['sid'])}</td>"
                    f"<td>{pe_disp}</td><td>{rank_disp}</td></tr>")
    return (f"<div class='pepeer'><div class='pehead'>本益比同族群「{html_mod.escape(grp or '')}」"
            f"(快照{html_mod.escape(biglot_dashboard.PE_GEN or '—')},本股本益比於主表即時重算·此表為快照收盤價)</div>"
            "<table class='petbl'><thead><tr><th>代號</th><th>名稱</th><th>本益比</th><th>族群排名</th></tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table>"
            "<div class='penote'>依楊育華分析師《御錢術》節目邏輯人工擴充同業清單(不跨族群比較,如IC設計不跟記憶體比)。"
            "⚠ EPS 為 TTM(近四季已公布),非分析師預估EPS(她的原方法),為與原方法唯一的實質差異,已誠實揭露。"
            "多數細分族群天生只有 3~8 檔真實同業,未硬湊到她說的 20~30 檔。</div></div>"
            "<style>.pepeer{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 10px;"
            "margin-bottom:8px;font-size:12px}.pepeer .pehead{color:#d2a8ff;font-weight:700;margin-bottom:4px}"
            ".petbl{border-collapse:collapse;width:100%}.petbl td,.petbl th{padding:2px 8px;text-align:right;"
            "border-bottom:1px solid #21262d}.petbl th:nth-child(2),.petbl td:nth-child(2){text-align:left}"
            ".petbl tr.self{background:rgba(31,111,235,0.25);font-weight:700}"
            ".pepeer .penote{color:#8b949e;font-size:11px;margin-top:4px}</style>")
