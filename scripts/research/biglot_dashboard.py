#!/usr/bin/env python3
"""大戶-散戶即時儀表板 · 唯讀 · 自帶HTTP(:8771) · 5秒增量更新。

  PYTHONPATH=src .venv/bin/python scripts/research/biglot_dashboard.py
  瀏覽 http://100.81.2.33:8771 （Tailscale 內網）

資料源：biglot-live-watch raw jsonl（逐筆）＋ watchlist_books jsonl（五檔）。
方法論同 scripts/research/biglot_5m_monitor.py（互斥三桶/競價與跳量排除/
獨漲勿追旗標/散戶參與度），詳見該檔 docstring。
不動 :8770 live UI（tmf-sim-server），完全獨立、無下單路徑。
"""
from __future__ import annotations

import html as html_mod
import json
import sqlite3
import sys
import threading
import time
import urllib.parse as urllib_parse
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "src")
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

TZ = timezone(timedelta(hours=8))
PORT = 8771
BIG_AMT = 10_000_000     # 2026-09-12 使用者定案:大戶=真大戶(>=1000萬)
RETAIL_CAP = 5_000_000   # 散戶定義不動:1張且<500萬(高價股1張大單歸中實不歸散戶)
GAP_JUMP_AMT = 500_000_000
GAP_SECONDS = 90
REFRESH_SEC = 1   # 背景 ingest(~2ms)+render(~23ms) 每秒約 2.5% 單核,client 抓 ~66KB/s,mini 綽綽有餘

CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
_cal = json.load(open(CALIB))
NAMES = {r["sid"]: r["name"] for r in _cal["universe"]}
CATS = {r["sid"]: r["cat"][:4] for r in _cal["universe"]}
# 細分產業(45檔手動策展,較 cat 粗類細一層,方便看族群輪動);查無則退回 CATS
SUBCAT = {
    "2492": "被動-MLCC", "6173": "被動-陶瓷", "2327": "被動-MLCC", "3042": "石英元件",
    "6182": "矽晶圓", "6488": "矽晶圓", "3532": "矽晶圓", "5483": "矽晶圓",
    "2303": "晶圓代工", "6770": "晶圓代工",
    "2449": "封測", "6147": "封測-驅動IC", "3374": "封測-晶圓級",
    "2408": "DRAM記憶體", "2344": "記憶體-利基", "2337": "記憶體-NOR", "3006": "記憶體IC設計",
    "3443": "IC設計-ASIC",
    "6213": "CCL銅箔基板", "6274": "CCL銅箔基板", "2383": "CCL銅箔基板", "1303": "塑化-CCL",
    "8358": "PCB-銅箔", "8039": "PCB-軟板材料",
    "8046": "ABF載板", "3189": "ABF載板", "3037": "ABF載板",
    "2368": "PCB-伺服器板", "4958": "PCB-軟板",
    "3105": "砷化鎵-PA", "2455": "砷化鎵-磊晶", "3081": "光通訊",
    "3406": "光學鏡頭", "3008": "光學鏡頭",
    "3324": "散熱", "3653": "散熱-均熱片", "3017": "散熱",
    "2059": "機殼滑軌", "6669": "伺服器代工", "2357": "品牌NB",
    "2481": "二極體-功率", "6223": "半導體設備-探針卡", "2301": "光電-電源",
    "2615": "貨櫃航運", "2609": "貨櫃航運",
    # 2026-09-22 換池納入(有個股期貨·高波動)
    "8150": "封測-記憶體", "6239": "封測-記憶體", "6271": "封測-CIS", "3711": "封測-龍頭",
    "2313": "PCB-HDI", "2308": "電源供應", "1802": "玻璃基板", "3231": "伺服器代工", "3481": "面板",
}
RET_UNM = {r["sid"] for r in _cal["universe"] if r.get("px", 0) * 1000 >= RETAIL_CAP}
FUT_MINI = {r["sid"] for r in _cal["universe"] if r.get("is_mini")}   # 期貨為小型契約(100 股):期貨買/賣欄標「小」、1 口名目=價×100
# 公司描述/看盤標籤(純靜態;主表名稱 hover 提示 + 詳情頁區塊)。查無則不顯示。
try:
    from biglot_stock_info import INFO as STOCK_INFO, BLOCKS as STOCK_BLOCKS, tooltip as _stock_tip
except Exception:  # noqa: BLE001 -- 資料檔缺失不影響儀表板
    STOCK_INFO, STOCK_BLOCKS = {}, {}
    def _stock_tip(_sid):
        return ""
# 高波動分數 = 20日日均振幅%((高−低)/收) ,來自 calib;越高越適合本系統的日內波段
AMP20 = {r["sid"]: r.get("amp20") for r in _cal["universe"]}
PREOPEN: dict = {}   # 盤前試撮快照 sid->{px,bid,ask,size,t}(collector preopen_*.json,08:30~09:00)
FUT_PX: dict = {}    # 個股期貨即時 sid->{px,sym,t,bid,bidsz,ask,asksz,bt}(futprice_*.json;bid/ask 來自 ws books)
WRT: dict = {}       # 權證多空 sid->{call_30,put_30,call_day,put_day,...}(warrantflow_*.json;MIS 輪詢,描述性未回測)
TX_SER: dict = {"day": None, "t": [], "px": [], "off": 0}
TX_LAST: dict = {"z": None}   # 台指 1 分 z 最近值(_tx_panel 每秒更新,淨分欄用)
WRT_MIN: dict = {"day": None, "off": 0, "data": {}}   # 權證逐筆 → sid -> {HH:MM: 簽號淨額(元,購+/售−×主動方)},供各圖紫線
AGG: dict = {}                                        # 36 檔分鐘加總累計(大戶/散戶/權證),供台指面板;render_grid_frag 每 5 秒更新


def _wrt_cum(sid, order):
    """依分鐘鍵 order 回傳權證簽號淨額的累計序列(元);無資料回 None。"""
    d = WRT_MIN["data"].get(sid)
    if not d:
        return None
    keys = sorted(d)
    out, c, j = [], 0.0, 0
    for k in order:
        while j < len(keys) and keys[j] <= k:
            c += d[keys[j]]; j += 1
        out.append(c)
    return out   # 台指近月 10 秒樣本(txf_10s_*.jsonl 增量),頂部校準圖用
#: 頁首可編輯筆記(2026-09-24):存在資料目錄,不進 git;沒有檔案時顯示預設紀律條
NOTES_PATH = DATA_DIR.parent / "cache" / "biglot_live_watch" / "dashboard_notes.html"
DEFAULT_NOTES = "（自由書寫的筆記區：點這裡開始輸入；Enter 換行，停止輸入 1.5 秒自動儲存，Ctrl/Cmd+S 立即儲存。紀律條全文見 📖 欄位說明。）"


def _load_notes():
    try:
        s = NOTES_PATH.read_text(encoding="utf-8")
        return s if s.strip() else DEFAULT_NOTES
    except Exception:  # noqa: BLE001
        return DEFAULT_NOTES


#: 每檔筆記(2026-09-24):sid -> {"txt": 純文字, "t": "HH:MM:SS", "d": "YYYY-MM-DD"};存資料目錄,不進 git
STOCK_NOTES_PATH = DATA_DIR.parent / "cache" / "biglot_live_watch" / "stock_notes.json"
STOCK_NOTES: dict = {}
try:
    STOCK_NOTES = json.loads(STOCK_NOTES_PATH.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    STOCK_NOTES = {}


def _stock_note_td(sid):
    n = STOCK_NOTES.get(sid) or {}
    txt = html_mod.escape(n.get("txt") or "")
    when = (n.get("t") or "")
    if n.get("d") and n["d"] != datetime.now(TZ).strftime("%Y-%m-%d"):
        when = f"{n['d'][5:]} {when}"
    return (f"<td class='snote'><span class='ne' contenteditable='true' spellcheck='false' data-sid='{sid}'>{txt}</span>"
            f"<span class='nt dim'>{when}</span></td>")


# ---- 持倉監控(jack 2026-09-25):手動標記持倉,持有中每輪重算 V2.5 當「持倉分」,出場提示依 127 日面板對照
#      (scratch/exit_rules_2026-09-25.txt):分數≤0 出 +23.1/+23.2(t5.2/6.2,均持 11 分,SD 94)、壞標籤出 +24.5/+26.0、
#      到期 60 分;移動停利/硬停損/破昨低出場皆較差,不做。純提示,不送單。
HOLDS_PATH = DATA_DIR.parent / "cache" / "biglot_live_watch" / "holds.json"
try:
    HOLDS: dict = json.loads(HOLDS_PATH.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    HOLDS = {}
HOLD_BAD = ("散戶虛拉", "噴後過熱", "急跌·竭盡∧散戶接")


def _hold_save():
    HOLDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOLDS_PATH.write_text(json.dumps(HOLDS, ensure_ascii=False, indent=0), encoding="utf-8")


def _hold_log(rec: dict):
    try:
        f = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"hold_events_{datetime.now(TZ).strftime('%Y-%m-%d')}.jsonl"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(TZ).strftime("%H:%M:%S"), **rec}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _hold_toggle(sid: str, action: str, px):
    now = time.time()
    if action == "open" and sid not in HOLDS:
        HOLDS[sid] = {"t0": now, "hm": datetime.now(TZ).strftime("%H:%M:%S"), "px0": px, "low_since": None, "fired": []}
        _hold_log({"ev": "open", "sid": sid, "px0": px})
    elif action == "close" and sid in HOLDS:
        h = HOLDS.pop(sid); pnl = ((px / h["px0"] - 1) * 1e4) if (px and h.get("px0")) else None
        _hold_log({"ev": "close", "sid": sid, "px0": h.get("px0"), "px": px, "pnl_bps": pnl, "hold_min": (now - h["t0"]) / 60, "reason": "manual", "fired": h.get("fired", [])})
    _hold_save()


def _hold_update(rows):
    """每輪:持倉分=當下 V2.5;分數≤0 連續 30 秒 / 壞標籤 / 60 分到期 → 出場旗標;獲利≥50 提示。首次觸發落地。"""
    now = time.time()
    for r in rows:
        h = HOLDS.get(r["sid"])
        if not h:
            r["hold"] = None; continue
        px = r.get("px"); sc = r.get("sc_v2"); items = [k for k, _ in (r.get("sc_v2_items") or [])]
        pnl = ((px / h["px0"] - 1) * 1e4) if (px and h.get("px0")) else None
        hold_min = (now - h["t0"]) / 60
        if sc is not None and sc <= 0:
            if h.get("low_since") is None: h["low_since"] = now
        else:
            h["low_since"] = None
        flags = []
        if h.get("low_since") is not None and now - h["low_since"] >= 30: flags.append("分數≤0·30秒")
        bad = [k for k in items if any(k.startswith(b) for b in HOLD_BAD)]
        if bad: flags.append("壞標籤:" + "/".join(x.split("(")[0] for x in bad))
        if hold_min >= 60: flags.append("到期60分")
        hint = "停利+50" if (pnl is not None and pnl >= 50) else ""
        for fl in flags:
            key = fl.split(":")[0]
            if key not in h.setdefault("fired", []):
                h["fired"].append(key); _hold_log({"ev": "flag", "sid": r["sid"], "flag": fl, "pnl_bps": pnl, "hold_min": hold_min, "score": sc})
        r["hold"] = {"hm": h["hm"], "px0": h.get("px0"), "pnl": pnl, "min": hold_min, "score": sc, "flags": flags, "hint": hint,
                     "low_s": (now - h["low_since"]) if h.get("low_since") is not None else 0}


def _save_stock_note(sid, txt):
    now = datetime.now(TZ)
    STOCK_NOTES[sid] = {"txt": txt[:2000], "t": now.strftime("%H:%M:%S"), "d": now.strftime("%Y-%m-%d")}
    STOCK_NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    STOCK_NOTES_PATH.write_text(json.dumps(STOCK_NOTES, ensure_ascii=False, indent=0), encoding="utf-8")
    return STOCK_NOTES[sid]["t"]


def _agg_lines(t0, t1, W, H, L, R):
    """台指面板疊圖:36 檔累計大戶(紅)/散戶(藍)/權證簽號(紫)加總,各自以 ±最大值正規化到同一畫面(0 線置中)。"""
    if not AGG.get("mins"):
        return ""
    mins = AGG["mins"]; mid = H / 2
    def X(hm):
        h, m = hm.split(":"); ts = t0 + ((int(h) - 8) * 60 + int(m) - 45) * 60
        return L + max(0.0, min(1.0, (ts - t0) / (t1 - t0))) * (W - L - R)
    out = [f"<line x1='{L}' y1='{mid:.0f}' x2='{W-R}' y2='{mid:.0f}' stroke='#30363d' stroke-dasharray='2,3'/>"]
    lab = []
    for key, col, nm in (("big", "#ff7b72", "大戶"), ("ret", "#58a6ff", "散戶"), ("wrt", "#d2a8ff", "權證")):
        v = AGG.get(key) or []
        if not v:
            continue
        mx = max(abs(x) for x in v) or 1.0
        pts = " ".join(f"{X(k):.0f},{mid - (x / mx) * (H / 2 - 8):.0f}" for k, x in zip(mins, v))
        out.append(f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='1.2' opacity='0.85'/>")
        lab.append(f"<tspan fill='{col}'>{nm} {v[-1]/1e4:+,.0f}萬(尺±{mx/1e4:,.0f})</tspan>")
    out.append(f"<text x='{W-R}' y='{H-2}' font-size='9' text-anchor='end'>36檔累計 " + " ".join(lab) + "</text>")
    return "".join(out)


def _tx_panel(now):
    """頂部右側:台指近月即時價 + 對昨結 + 5分/30分 bps + 1分 z(影子帳砍尾同口徑)+ 10 秒線 SVG。無資料回空字串。"""
    t, px = TX_SER["t"], TX_SER["px"]
    tx = FUT_PX.get("TXF") if isinstance(FUT_PX.get("TXF"), dict) else None
    if not t or not tx or not tx.get("px"):
        return ""
    last = float(tx["px"]); fpc = tx.get("fpc"); nts = t[-1]
    import bisect as _bs
    def _at(sec):
        i = _bs.bisect_right(t, nts - sec) - 1
        return px[i] if i >= 0 else None
    p5, p30 = _at(300), _at(1800)
    b5 = (last / p5 - 1) * 1e4 if p5 else None
    b30 = (last / p30 - 1) * 1e4 if p30 else None
    # 1 分 z:30 秒格點的 1 分報酬歷史 σ(與 zcrash_shadow 相同)
    z = None
    if len(t) > 12:
        r1 = []
        g = t[0] + 60
        while g <= nts:
            i = _bs.bisect_right(t, g) - 1; j = _bs.bisect_right(t, g - 60) - 1
            if i >= 0 and j >= 0 and px[j]:
                r1.append(px[i] / px[j] - 1)
            g += 30
        if len(r1) >= 10:
            m = sum(r1) / len(r1); sd = (sum((x - m) ** 2 for x in r1) / len(r1)) ** 0.5
            p60 = _at(60)
            if sd > 0 and p60:
                z = (last / p60 - 1) / sd
    chg = (last / fpc - 1) * 100 if fpc else None
    TX_LAST["z"] = z
    cls = "up" if (chg or 0) > 0 else ("dn" if (chg or 0) < 0 else "")
    zcls = " style='background:#6e1a1a;color:#ffb3b3;padding:0 4px'" if (z is not None and z <= -1) else (
        " style='background:#1a4d2e;color:#b3ffcc;padding:0 4px'" if (z is not None and z >= 1) else "")
    # SVG:固定 08:45→13:45 時間軸,y 含昨結
    W, H, L, R = 470, 250, 4, 4          # 頂部說明文字隱藏後,圖高拉到 250
    t0 = datetime.fromisoformat(f"{TX_SER['day']}T08:45:00+08:00").timestamp(); t1 = t0 + 5 * 3600
    ys = px + ([fpc] if fpc else [])
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-9:
        hi = lo + 1
    def X(ts): return L + (ts - t0) / (t1 - t0) * (W - L - R)
    def Y(v): return 6 + (hi - v) / (hi - lo) * (H - 12)
    step = max(1, len(t) // 600)
    samp = list(zip(t, px))[::step]
    pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in samp)
    # hover 用:每個取樣點的 (x 像素, y 像素, 時間, 價),前端找最近 x 顯示
    hov = json.dumps([[round(X(a), 1), round(Y(b), 1), datetime.fromtimestamp(a, TZ).strftime("%H:%M:%S"), b] for a, b in samp])
    col = "#ff7b72" if (chg or 0) > 0 else "#3fb950"
    svg = (f"<svg width='{W}' height='{H}' style='display:block' data-pts='{hov}'>"
           + (f"<line x1='{L}' y1='{Y(fpc):.1f}' x2='{W-R}' y2='{Y(fpc):.1f}' stroke='#8b949e' stroke-dasharray='3,3'/>" if fpc else "")
           + f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='1.2'/>"
           + f"<circle cx='{X(nts):.1f}' cy='{Y(last):.1f}' r='2.5' fill='{col}'/>"
           + _agg_lines(t0, t1, W, H, L, R)
           + f"<text x='{L}' y='10' font-size='9' fill='#8b949e'>{hi:,.0f}</text>"
           + f"<text x='{L}' y='{H-1}' font-size='9' fill='#8b949e'>{lo:,.0f}</text></svg>")
    f = lambda v: f"{v:+.0f}" if v is not None else "—"  # noqa: E731
    fp = lambda v: f"{v/100:+.2f}%" if v is not None else "—"  # noqa: E731  # 統一用 %
    # 左文右圖:文字欄固定 200px 直排,圖吃剩餘寬度、高度拉滿
    left = (f"<div><b>台指近月</b> <span class='dim'>{tx.get('t', '')}</span></div>"
            f"<div><span class='{cls}' style='font-size:24px;font-weight:700'>{last:,.0f}</span></div>"
            + (f"<div class='{cls}'>{last - fpc:+,.0f} ({chg:+.2f}%) <span class='dim'>對昨結 {fpc:,.0f}</span></div>" if fpc else ""))
    if z is not None:
        left += (f"<div>5分 <b>{fp(b5)}</b> · 30分 <b>{fp(b30)}</b></div>"
                 f"<div>1分z <b{zcls}>{z:+.1f}</b>"
                 + (f" · 買{tx.get('bid')}/賣{tx.get('ask')}" if tx.get("bid") else "") + "</div>"
                 "<div class='dim' style='font-size:10px;line-height:1.3'>校準:z≤−1 紅=急殺做多砍尾中<br>z≥+1 綠=急拉做空砍尾中</div>")
    return (f"<div id='txsrc' hidden><div style='display:flex;gap:10px;align-items:stretch'>"
            f"<div style='flex:0 0 200px'>{left}</div><div style='flex:1 1 auto'>{svg}</div></div></div>")
# 固定產業鏈排序(避免5秒隨大戶流跳位):相近產業相鄰,半導體上游→下游→非半導體。
# 產業交界畫粗線(band)。查無的股票排最後。
_CLUSTERS = [
    ("矽晶圓", ["6182", "6488", "5483", "3532"]),
    ("晶圓代工", ["2303", "6770"]),
    ("記憶體", ["2344", "2408", "2337", "3006"]),
    ("封測", ["2449", "6147", "3374"]),
    ("化合物半導體", ["3105", "2455"]),
    ("被動元件", ["2327", "2492", "6173", "3042"]),
    ("CCL銅箔基板", ["6213", "6274"]),
    ("ABF載板", ["8046", "3189", "3037"]),
    ("PCB/載板", ["2368", "4958", "8039", "8358"]),
    ("散熱", ["3324"]),
    ("光學", ["3406"]),
    ("功率二極體", ["2481"]),
    ("電源光電", ["2301"]),
    ("系統品牌", ["2357"]),
    ("塑化", ["1303"]),
    ("航運", ["2615", "2609"]),
]
SORT_INDEX = {sid: i for i, sid in enumerate(s for _n, sids in _CLUSTERS for s in sids)}
CLUSTER_OF = {sid: name for name, sids in _CLUSTERS for sid in sids}
try:
    _rb = json.load(open(DATA_DIR.parent / "cache" / "biglot_live_watch" / "_rvol_base.json"))
    RVOL_BASE = _rb.get("base", {})
except Exception:
    RVOL_BASE = {}

PAGE = {"frag": "<div class='meta'>初始化中…</div>"}
SNAP_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch" / "eod_snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)

def _load_hist():
    """近5個快照:每檔前幾日大戶淨流/收盤、宇宙5日累積、昨日午後低。"""
    # 排除「當日」快照:對昨收/前n日大戶都該用 ≤昨日 的收盤;否則盤後重啟會抓到今收→漲跌恆0
    _today = datetime.now(TZ).strftime("%Y-%m-%d")
    files = [f for f in sorted(SNAP_DIR.glob("eod_*.json")) if _today not in f.name][-5:]
    snaps = []
    for f in files:
        try:
            snaps.append(json.load(open(f)))
        except Exception:
            pass
    hist_big = {}      # sid -> [前n日big,...最舊在前]
    prev_close = {}
    y_pmlow = {}
    for s in snaps:
        for r in s["rows"]:
            hist_big.setdefault(r["sid"], []).append(r.get("big", 0))
            prev_close[r["sid"]] = r.get("close")
            if "pm_low" in r:
                y_pmlow[r["sid"]] = r["pm_low"]
    # 宇宙近5日累積(等權,快照收盤鏈)
    u5 = None
    if len(snaps) >= 2:
        rets = []
        for i in range(1, len(snaps)):
            a = {r["sid"]: r["close"] for r in snaps[i-1]["rows"]}
            b = {r["sid"]: r["close"] for r in snaps[i]["rows"]}
            vs = [(b[k]/a[k]-1) for k in b if k in a and a[k]]
            if vs:
                rets.append(sum(vs)/len(vs))
        if rets:
            u5 = sum(rets) * 100
    return hist_big, prev_close, y_pmlow, u5

HIST_BIG, PREV_CLOSE, Y_PMLOW, UNI5 = _load_hist()


def _load_prev_close_db():
    """昨收改抓官方 stock_daily_bars 的收盤競價價(權威),取代 EOD 快照的『最後一筆 tick』。
    快照 tick 收盤與官方收盤常差 1~2 檔,會讓漲跌%失真——南電 2026-09-21 快照 1055 vs
    官方 1060,今日漲停 1165 就被算成 +10.4%(超過±10%上限,不可能)。用『<今日的最近交易日』
    避免抓到今日盤中殘影;雙來源同價,取一筆即可。"""
    out = {}
    try:
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        for sid in NAMES:
            row = conn.execute(
                "SELECT close FROM stock_daily_bars WHERE stock_id=? AND trade_date<? "
                "AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 1",
                (sid, today)).fetchone()
            if row and row[0]:
                out[sid] = float(row[0])
        conn.close()
    except Exception:
        pass
    return out


PREV_CLOSE.update(_load_prev_close_db())   # 官方收盤優先,快照昨收僅作 fallback


def _load_daily_trend():
    """每檔日線趨勢(截至最近日收盤):站上5日均線? 5日動能%。
    回測(127日隔夜候選池):壓縮∧站上5日線 +93.8bps/t5.10 vs 跌破 +30/t1.65,
    差+63.5bps;純脈絡欄+影子帳分層,不改選股規則。日線雙來源(finmind/tpex/twse)
    同價,按 trade_date 去重取一筆。"""
    out = {}
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        for sid in NAMES:
            rows = conn.execute(
                "SELECT trade_date, MAX(close) FROM stock_daily_bars "
                "WHERE stock_id=? GROUP BY trade_date ORDER BY trade_date DESC LIMIT 11",
                (sid,)).fetchall()
            closes = [c for _, c in rows if c]
            if len(closes) < 4:
                continue
            last = closes[0]
            ma5 = sum(closes[:5]) / len(closes[:5])
            ma10 = sum(closes[:10]) / len(closes[:10]) if len(closes) >= 6 else None
            ret5 = (last / closes[5] - 1) * 100 if len(closes) >= 6 and closes[5] else None
            out[sid] = {"above_ma5": last > ma5,
                        "above_ma10": (last > ma10) if ma10 else None,
                        "ret5d": ret5, "last": last, "asof": rows[0][0]}
        conn.close()
    except Exception as e:
        print(f"[daily_trend] load failed: {e}", file=sys.stderr)
    return out

DAILY_TREND = _load_daily_trend()

# ---- 融資/借券變化幅度 → 波動風險分數（非方向訊號，只預測盤中振幅，多空都適用）------
# 方法論：scripts/research/margin_lending_spike_next_day_amplitude.py（45檔高波動宇宙
# 2025-01~2026-09 回測）。演進紀錄（後面取代前面）：
#   1) 一開始只看「同時大增」(AND, 有方向)：q95 事件少(35~42次)但效果最大(+1.9~2.3pp)。
#   2) 放寬成「任一邊大增即可」(OR)：事件變多但控制當日振幅(vol clustering)後大多不顯著
#      (q90 t1.90 p0.057、q95 t1.53 p0.127)——原始t值好看是自相關撐出來的假象。
#   3) 改良為「不分方向的變化幅度」(用 |日增幅| 而非「大增」)：兩邊各自的絕對值分位數
#      取平均當 Score(0~100)，關係轉為單調遞增(十等分乾淨遞增4.9%→6.2%)，控制當日振幅後
#      仍顯著(t=3.40 p=0.0007，比原始AND設計更穩健)——這是目前採用的最終版本。
# 已驗證的邊界：只影響隔日「盤中振幅(H-L)/前收」，對隔日|收對收報酬|(t1.57 p0.117)、
# 跳空幅度(t0.01 p0.99)、隔日量能(係數反而顯著為負,t-4.33)都測不出增量——不是方向或
# 跳空訊號,量能還偏低(流動性變薄格)。換Parkinson(log range)結果不變(t3.43),但換成
# True Range(含跳空)訊號整個消失(t-0.07)——訊號本質是「盤中來回」不是「跳空」。
# ⚠ margin_balance 單位是「張」(1張=1000股)、lending_balance 單位是「股」，
# 不可共用同一個門檻——5000張門檻會系統性排掉大立光/玉晶光/華碩/緯穎等高價股
# (股價高→可融資張數天生就少，不是資料不足)。
MIN_PREV_MARGIN_LOTS = 100        # 張，只擋真正近零/停融資的退化列
MIN_PREV_LENDING_SHARES = 5_000   # 股，同一用意
VOLRISK_MIN_OBS = 60              # 兩邊都要至少60個交易日紀錄才計分位，避免新股/資料不足誤判
VOLRISK_STALE_DAYS = 7            # 融資/借券最新一筆超過這麼多天沒更新(處置股常停融資)就不計分數
# Score 分級門檻(對應回測的q90/95/98,見上方演進紀錄第3版十等分結果)
VOLRISK_TIERS = ((92, "🌊🌊"), (86, "🌊"), (80, ""))  # 第三級只上色不加圖示,由極端到寬鬆


def _pctile_rank(values):
    """回傳每個元素在序列中的百分位排名(0~1,含自己;越大越極端)。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for pos, i in enumerate(order):
        ranks[i] = (pos + 1) / len(values)
    return ranks


def _load_vol_risk_flags():
    """算出每檔股票「最新一筆」融資/借券變化幅度(不分方向)的歷史分位平均分數。"""
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    sids = list(NAMES)
    ph = ",".join("?" * len(sids))
    mg_by_sid = defaultdict(list)
    for sid, td, bal in conn.execute(
            f"""SELECT stock_id, trade_date, margin_balance FROM (
                    SELECT stock_id, trade_date, margin_balance,
                           ROW_NUMBER() OVER (
                               PARTITION BY stock_id, trade_date
                               ORDER BY CASE source WHEN 'twse_mi_margn' THEN 0 ELSE 1 END
                           ) AS rn
                      FROM stock_margin_daily WHERE stock_id IN ({ph})
                ) WHERE rn=1 ORDER BY stock_id, trade_date""", sids):
        mg_by_sid[sid].append((td, bal))
    ln_by_sid = defaultdict(list)
    for sid, td, prev_bal, bal in conn.execute(
            f"""SELECT stock_id, trade_date, prev_balance, lending_balance
                  FROM stock_lending_balance_daily WHERE stock_id IN ({ph})
                  ORDER BY stock_id, trade_date""", sids):
        ln_by_sid[sid].append((td, prev_bal, bal))
    conn.close()

    out = {}
    for sid in sids:
        m, l = mg_by_sid.get(sid, []), ln_by_sid.get(sid, [])
        if len(m) < VOLRISK_MIN_OBS + 1 or len(l) < VOLRISK_MIN_OBS:
            continue
        m_dates, m_pct = [], []
        for i in range(1, len(m)):
            prev, cur = m[i - 1][1], m[i][1]
            if prev and prev >= MIN_PREV_MARGIN_LOTS and cur is not None:
                m_dates.append(m[i][0])
                m_pct.append((cur - prev) / prev)
        l_dates, l_pct = [], []
        for td, prev, bal in l:
            if prev and prev >= MIN_PREV_LENDING_SHARES and bal is not None:
                l_dates.append(td)
                l_pct.append((bal - prev) / prev)
        if len(m_pct) < VOLRISK_MIN_OBS or len(l_pct) < VOLRISK_MIN_OBS:
            continue
        days_stale = max(
            (datetime.now(TZ).date() - datetime.strptime(d, "%Y-%m-%d").date()).days
            for d in (m_dates[-1], l_dates[-1])
        )
        if days_stale > VOLRISK_STALE_DAYS:
            out[sid] = {
                "score": None, "tier": None, "stale": True, "days_stale": days_stale,
                "margin_asof": m_dates[-1], "lending_asof": l_dates[-1],
                "margin_pct": m_pct[-1], "lending_pct": l_pct[-1],
                "margin_abs_pctile": None, "lending_abs_pctile": None,
            }
            continue
        m_last = _pctile_rank([abs(x) for x in m_pct])[-1]
        l_last = _pctile_rank([abs(x) for x in l_pct])[-1]
        score = (m_last + l_last) / 2 * 100
        tier = next((badge for thr, badge in VOLRISK_TIERS if score >= thr), None)
        out[sid] = {
            "score": score, "tier": tier, "stale": False, "days_stale": days_stale,
            "margin_asof": m_dates[-1], "lending_asof": l_dates[-1],
            "margin_pct": m_pct[-1], "lending_pct": l_pct[-1],
            "margin_abs_pctile": m_last, "lending_abs_pctile": l_last,
        }
    return out


VOLRISK, VOLRISK_DATE = {}, None


def _refresh_vol_risk_if_needed() -> bool:
    """依實際日曆日期(非 ST.date)刷新——T-1 籌碼資料跟有沒有開盤無關，不该被
    ingest()/render() 只在盤中才跑的邏輯卡住,否則開盤前使用者看到的都是前一個
    交易日收盤時算出的舊分數(2026-09-21 發現:盤前完全看不到當天該有的分數)。
    回傳是否真的重算了,讓呼叫端決定要不要順便重繪一次盤後定格頁面。
    """
    global VOLRISK, VOLRISK_DATE
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if VOLRISK_DATE == today:
        return False
    try:
        VOLRISK = _load_vol_risk_flags()
    except Exception:
        VOLRISK = {}
    VOLRISK_DATE = today
    return True

OOS_FILE = DATA_DIR.parent / "cache" / "biglot_live_watch" / "oos_scoreboard.json"

def _oos_load():
    try:
        return json.load(open(OOS_FILE))
    except Exception:
        return {"intraday": [], "overnight": [], "overnight_pending": [],
            "overnight_short": [], "overnight_short_pending": []}

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
    today = ST.date
    if any(x.get("date") == today for x in o["intraday"]) or \
       any(x.get("date") == today for x in o["overnight_pending"]):
        return
    # a) 結算pending(用今日首價)
    still = []
    for p in o["overnight_pending"]:
        px0 = ST.day.get(p["sid"], {}).get("px0")
        if px0 and p.get("close"):
            o["overnight"].append({**p, "resolve_date": today,
                                   "ret": (px0 / p["close"] - 1) * 1e4})
        else:
            still.append(p)
    o["overnight_pending"] = still
    # 做空腿結算:做空報酬=-(次開/今收-1)
    still_s = []
    for p in o.get("overnight_short_pending", []):
        px0 = ST.day.get(p["sid"], {}).get("px0")
        if px0 and p.get("close"):
            o.setdefault("overnight_short", []).append(
                {**p, "resolve_date": today, "ret": -(px0 / p["close"] - 1) * 1e4})
        else:
            still_s.append(p)
    o["overnight_short_pending"] = still_s
    # b) 今日盤中終版訊號實績(三窗<5%∧pb5<0∧pb30<=-3千萬∧買>=3千萬>10%,45分)
    for sid, m in ST.buckets.items():
        if sid in RET_UNM:
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
                                  "gate": bool(UNI5 is not None and UNI5 < -5)})
    # c) 今日隔夜候選:做多3檔(佔比前10∧壓縮深)+ 做空3檔(佔比最負前10∧彈開最多∧日線空)
    cand = []
    for sid in NAMES:
        ds = ST.day.get(sid)
        px = ST.last_px.get(sid)
        pc = PREV_CLOSE.get(sid)
        if not ds or not px or not ds["tot"]:
            continue
        m = ST.buckets.get(sid, {})
        bks = sorted(m)
        last12 = [m[b]["px"] for b in bks[-12:] if m[b]["px"]]
        if len(last12) < 8:
            continue
        cand.append({"sid": sid, "big": ds["big"], "close": px,
                     "cmp": px / (sum(last12) / len(last12)) - 1,
                     "locked": bool(pc and px / pc - 1 >= 0.09),
                     "above_ma5": DAILY_TREND.get(sid, {}).get("above_ma5")})
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
    json.dump(o, open(OOS_FILE, "w"))

SHELL = f"""<!DOCTYPE html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=0.6">
<title>大戶{len(NAMES)}檔儀表板</title><style>
body{{background:#0d1117;color:#c9d1d9;font:12px/1.5 -apple-system,'PingFang TC',monospace;margin:8px}}
h3{{margin:4px 0;font-size:14px}}
.meta{{color:#8b949e;font-size:11px;margin-bottom:6px}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}
th,td{{padding:2px 7px;text-align:right;border-bottom:1px solid #21262d}}
th{{position:sticky;top:0;z-index:2;background:#161b22;color:#8b949e;font-weight:600;cursor:default}}
th.g5{{color:#e3b341}} th.g30{{color:#79c0ff}} th.gd{{color:#d2a8ff}}
td.nm{{position:sticky;left:0;background:#0d1117;z-index:1;text-align:left;font-weight:600;color:#e6edf3}}
th.stk{{position:sticky;left:0;z-index:3}}
tbody tr td{{border-bottom:1px solid #1c2128}}
tbody tr.band td{{border-bottom:2px solid #454d57}}
tbody tr:hover{{background:#1c2635 !important}}
tbody tr:hover td.nm{{background:#1c2635 !important}}
.cat{{color:#8b949e;font-weight:400;font-size:10px;margin-left:4px}}
th .sub{{display:block;font-size:9px;font-weight:400;color:#8b949e;margin-top:1px}}
.up{{color:#ff7b72}} .dn{{color:#3fb950}} .dim{{color:#484f58}}
.lup{{background:#d1242f;color:#fff;font-weight:700}}  /* 漲停:紅底白字(台股慣例) */
.ldn{{background:#1a7f37;color:#fff;font-weight:700}}  /* 跌停:綠底白字 */
.nlup{{color:#ff7b72;font-weight:700}} .nldn{{color:#3fb950;font-weight:700}}  /* 接近漲/跌停:粗紅/綠 */
.hit{{text-decoration:underline;text-decoration-color:#f2cc60;text-decoration-thickness:2px;text-underline-offset:3px}}  /* 期貨成交價落在的那一邊:黃底線 */
.warnv{{color:#e3b341}} .wall{{color:#d2a8ff;font-weight:700}}
.vr0{{color:#58a6ff}} .vr1{{color:#e3b341;font-weight:700}} .vr2{{color:#f0883e;font-weight:700}}
.flag{{color:#e3b341;text-align:left}}
.sigdn{{color:#3fb950}} .sigup{{color:#ff7b72}}
.rk1{{color:#ffd700;font-weight:700}} .rkN{{color:#3fb950;font-weight:700}}
.flagbar{{padding:3px 8px;font-size:12px;background:#161b22;margin-bottom:4px}}
.disc{{font-size:11px;line-height:1.7;background:#161b22;border:1px solid #30363d;
border-radius:6px;padding:6px 10px;margin-bottom:6px}}
.disc b{{color:#e6edf3}} .disc .ok{{color:#3fb950}} .disc .no{{color:#ff7b72}}
.disc summary{{cursor:pointer;color:#8b949e;font-weight:600}}
.txp{{flex:0 0 700px;width:700px;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 10px;margin-bottom:6px;font-size:12px;line-height:1.6;position:relative}}
#txtip{{position:absolute;display:none;background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:1px 6px;font-size:11px;color:#e6edf3;pointer-events:none;z-index:5;white-space:nowrap}}
#txline{{position:absolute;display:none;width:1px;background:#8b949e;pointer-events:none;z-index:4}}
#notes{{outline:none;min-height:60px;padding:2px 4px;border-radius:4px}} #notes:focus{{background:#0d1117;box-shadow:0 0 0 1px #388bfd}}
#nstat{{color:#8b949e;font-size:10px;text-align:right}}
td.snote{{text-align:left;min-width:170px;white-space:nowrap;font-weight:400}}
.ne{{display:inline-block;min-width:130px;max-width:320px;overflow:hidden;text-overflow:ellipsis;vertical-align:bottom;white-space:nowrap;outline:none;padding:0 3px;border-radius:3px;color:#e6edf3}} .ne:empty::before{{content:'…';color:#484f58}}
.ne:focus{{max-width:none;white-space:normal;overflow:visible}}
.ne:focus{{background:#0d1117;box-shadow:0 0 0 1px #388bfd}} .nt{{margin-left:4px;font-size:9px;white-space:nowrap}}
</style></head><body>
<h3>大戶-散戶 {len(NAMES)}檔即時儀表板
<span id="clk" style="font-size:14px;color:#e3b341;margin-left:10px;font-variant-numeric:tabular-nums">--:--:--</span>
<a href="/history" style="font-size:11px;margin-left:8px;color:#79c0ff">歷史分頁</a>
<a href="/help" style="font-size:11px;margin-left:8px;color:#79c0ff">📖 欄位說明</a>
<a href="/grid" target="_blank" style="font-size:11px;margin-left:8px;color:#79c0ff">▦ 36檔圖形總覽</a></h3>
<div style="display:flex;gap:14px;align-items:stretch">
<details class="disc" open style="flex:1 1 auto;margin-bottom:6px"><summary>📝 筆記（自由書寫 · 自動儲存）</summary>
<div id="notes" contenteditable="true" spellcheck="false">{{NOTES}}</div>
<div id="nstat">未編輯</div></details>
<div id="txp" class="txp"><div id="txbody"><span class="dim">台指近月 載入中…</span></div><div id="txtip"></div><div id="txline"></div></div>
</div>
<div id="app"><div class="meta">載入中…</div></div>
<script>
const R={REFRESH_SEC}000;
function clk(){{const d=new Date();const p=n=>String(n).padStart(2,'0');
  document.getElementById('clk').textContent=p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());}}
clk(); setInterval(clk,1000);   // 每秒跳動的即時時鐘(到秒)
async function tick(){{
  try{{
    const r=await fetch('/frag?_='+Date.now());
    const t=await r.text();
    const ae=document.activeElement;
    if(window.__noteEditing || (ae && ae.classList && ae.classList.contains('ne'))){{setTimeout(tick,R);return;}}   // 正在編輯個股筆記:暫停換表,離開格子後恢復
    document.getElementById('app').innerHTML=t;   // 只換內容,不重載整頁,不閃爍
    const s=document.getElementById('txsrc'); if(s){{document.getElementById('txbody').innerHTML=s.innerHTML;}}   // 台指面板搬到右上(tip/line 元素保留)
    const c=document.getElementById('closed');
    if(c && c.dataset.closed==='1'){{setTimeout(tick,30000);return;}}   // 非交易時段改 30s 慢輪詢,08:30 自動恢復(不必重載頁面)
  }}catch(e){{}}
  setTimeout(tick,R);
}}
tick();
// 頁首筆記:contenteditable,停止輸入 1.5 秒或 Ctrl/Cmd+S 自動 POST /notes 存檔
(function(){{
  const n=document.getElementById('notes'), st=document.getElementById('nstat'); if(!n) return; let tm=null;
  const save=async()=>{{try{{const r=await fetch('/notes',{{method:'POST',body:n.innerHTML}});
    st.textContent=(r.ok?'已儲存 ':'儲存失敗 ')+new Date().toTimeString().slice(0,8);}}catch(e){{st.textContent='儲存失敗';}}}};
  n.addEventListener('input',()=>{{st.textContent='編輯中…';clearTimeout(tm);tm=setTimeout(save,1500);}});
  n.addEventListener('keydown',e=>{{if((e.metaKey||e.ctrlKey)&&e.key==='s'){{e.preventDefault();clearTimeout(tm);save();}}}});
}})();
// 個股筆記:事件委派到 #app(表格每秒重繪);input 去抖 1.5s / blur / Ctrl+S → POST /stocknote,回傳最後編輯時間寫進同格小字
(function(){{
  const app=document.getElementById('app'); const tm={{}}; window.__noteEditing=false;
  // 按下格子的瞬間就鎖住重繪(避免 fetch 回應剛好在 mousedown 與 focus 之間把格子換掉);focusout 解鎖
  app.addEventListener('mousedown',e=>{{if(e.target.closest&&e.target.closest('td.snote')){{window.__noteEditing=true;}}}});
  app.addEventListener('focusin',e=>{{const el=e.target.closest&&e.target.closest('.ne'); if(el){{window.__noteEditing=true; if(el.dataset.orig===undefined){{el.dataset.orig=el.innerText;}}}}}});
  app.addEventListener('click',e=>{{const td=e.target.closest&&e.target.closest('td.snote'); if(!td) return;
    const el=td.querySelector('.ne'); if(el && document.activeElement!==el){{el.focus();}}}});   // 點到格子空白處也進入編輯
  const save=async (el,force)=>{{const sid=el.dataset.sid; const nt=el.nextElementSibling;
    if(!force && el.innerText===(el.dataset.orig||'')){{return;}}   // 沒改動就不存(避免只是點進去也蓋掉編輯時間)
    el.dataset.orig=el.innerText;
    try{{const r=await fetch('/stocknote',{{method:'POST',body:JSON.stringify({{sid:sid,txt:el.innerText}})}});
      const j=await r.json(); if(nt){{nt.textContent=j.t;}}}}catch(e){{if(nt){{nt.textContent='儲存失敗';}}}}}};
  app.addEventListener('input',e=>{{const el=e.target.closest('.ne'); if(!el) return; const sid=el.dataset.sid;
    const nt=el.nextElementSibling; if(nt){{nt.textContent='編輯中…';}} clearTimeout(tm[sid]); tm[sid]=setTimeout(()=>save(el,true),1500);}});
  app.addEventListener('focusout',e=>{{const el=e.target.closest&&e.target.closest('.ne'); if(!el) return; clearTimeout(tm[el.dataset.sid]); save(el); setTimeout(()=>{{window.__noteEditing=false;}},200);}});
  app.addEventListener('keydown',e=>{{const el=e.target.closest&&e.target.closest('.ne'); if(!el) return;
    if((e.metaKey||e.ctrlKey)&&e.key==='s'){{e.preventDefault();clearTimeout(tm[el.dataset.sid]);save(el,true);}}
    if(e.key==='Escape'){{el.blur();}}}});
}})();
// 持倉按鈕:持/出 → POST /hold(純提示,不送單)
(function(){{const app=document.getElementById('app');
  app.addEventListener('click',async e=>{{const b=e.target.closest&&e.target.closest('.hbtn'); if(!b) return; e.preventDefault(); e.stopPropagation();
    try{{await fetch('/hold',{{method:'POST',body:JSON.stringify({{sid:b.dataset.sid,action:b.dataset.action}})}});}}catch(_){{}}}});
}})();
// 台指圖 hover:找最近取樣點,顯示 時間/價 + 垂直線(事件掛在容器上,svg 每秒被換掉也不用重綁)
(function(){{
  const box=document.getElementById('txp'), tip=document.getElementById('txtip'), ln=document.getElementById('txline');
  box.addEventListener('mousemove',e=>{{
    const svg=box.querySelector('svg'); if(!svg){{tip.style.display='none';ln.style.display='none';return;}}
    if(!svg._pts){{try{{svg._pts=JSON.parse(svg.dataset.pts);}}catch(_){{return;}}}}
    const r=svg.getBoundingClientRect(), b=box.getBoundingClientRect(), x=e.clientX-r.left;
    if(x<0||x>r.width||e.clientY<r.top||e.clientY>r.bottom){{tip.style.display='none';ln.style.display='none';return;}}
    let best=null,bd=1e9; for(const p of svg._pts){{const d=Math.abs(p[0]-x); if(d<bd){{bd=d;best=p;}}}}
    if(!best||bd>12){{tip.style.display='none';ln.style.display='none';return;}}
    tip.textContent=best[2]+'  '+best[3].toLocaleString(); tip.style.display='block';
    const lx=r.left-b.left+best[0]; ln.style.left=lx+'px'; ln.style.top=(r.top-b.top)+'px'; ln.style.height=r.height+'px'; ln.style.display='block';
    tip.style.left=Math.min(lx+8,b.width-110)+'px'; tip.style.top=(r.top-b.top+best[1]-22)+'px';
  }});
  box.addEventListener('mouseleave',()=>{{tip.style.display='none';ln.style.display='none';}});
}})();
</script>
</body></html>"""


class S:
    """解析狀態（當日）。"""
    def __init__(self):
        self.date = None
        self.raw_off = 0
        self.book_off = 0
        self.lastvol = defaultdict(float)
        self.last_px = {}
        self.last_seen = {}
        self.first_done = set()
        self.buckets = {}                       # sid -> {bk: {...}}
        self.day = defaultdict(lambda: {"big": 0., "ret": 0., "ret2": 0., "mid": 0.,
                                        "tot": 0., "big_pm": 0., "px0": None, "hi": None, "lo": None})
        self.book = {}                          # sid -> latest snapshot
        # 每檔最近 ~60 分鐘逐筆 (ts, px, amt, sgn, is_big, is_retail):供 5分/30分 欄位**每秒滾動窗**
        # (2026-09-23 jack 要求)。標籤/旗標仍依完成的 5 分桶判定(=127 日回測定義),不走這裡。
        self.recent = defaultdict(deque)


ST = S()


def bucket_key(dt):
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def ingest():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if ST.date != today:
        ST.__init__()
        ST.date = today
        _refresh_vol_risk_if_needed()
        global DAILY_TREND
        DAILY_TREND = _load_daily_trend()
        PREV_CLOSE.update(_load_prev_close_db())   # 換日refresh官方昨收
    raw = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{today}.jsonl"
    if raw.exists():
        with open(raw) as f:
            f.seek(ST.raw_off)
            while True:
                line = f.readline()
                if not line or not line.endswith("\n"):
                    break                     # 尾行未寫完,下輪再讀
                ST.raw_off = f.tell()
                _ingest_trade(line)
    bookf = DATA_DIR / "cache" / "watchlist_books" / f"watchlist_books_{today}.jsonl"
    if bookf.exists():
        with open(bookf) as f:
            f.seek(ST.book_off)
            while True:
                line = f.readline()
                if not line or not line.endswith("\n"):
                    break
                ST.book_off = f.tell()
                try:
                    r = json.loads(line)
                    ST.book[r["sym"]] = r
                except Exception:
                    pass
    # 盤前試撮快照 + 個股期貨即時價(小檔,每輪重讀)
    global PREOPEN, FUT_PX
    _bd = DATA_DIR.parent / "cache" / "biglot_live_watch"
    try:
        pf = _bd / f"preopen_{today}.json"
        PREOPEN = json.loads(pf.read_text()).get("trial", {}) if pf.exists() else {}
    except Exception:
        PREOPEN = {}
    try:
        ff = _bd / f"futprice_{today}.json"
        FUT_PX = json.loads(ff.read_text()) if ff.exists() else {}
    except Exception:
        FUT_PX = {}
    global WRT
    try:
        wf = _bd / f"warrantflow_{today}.json"
        WRT = json.loads(wf.read_text()) if wf.exists() else {}
    except Exception:
        WRT = {}
    # 台指近月 10 秒樣本(collect_biglot_futprice 落地),增量讀,供頂部台指校準圖
    try:
        if TX_SER["day"] != today:
            TX_SER.update({"day": today, "t": [], "px": [], "off": 0})
        tf = _bd / f"txf_10s_{today}.jsonl"
        if tf.exists():
            with open(tf, "rb") as f:
                f.seek(TX_SER["off"])
                chunk = f.read()
            nl = chunk.rfind(b"\n")
            if nl != -1:
                TX_SER["off"] += nl + 1
                for line in chunk[:nl].split(b"\n"):
                    try:
                        o = json.loads(line)
                        ts = datetime.fromisoformat(f"{today}T{o['t']}+08:00").timestamp()
                        if o.get("px") and (not TX_SER["t"] or ts > TX_SER["t"][-1]):
                            TX_SER["t"].append(ts)
                            TX_SER["px"].append(float(o["px"]))
                    except Exception:  # noqa: BLE001
                        continue
    except Exception:  # noqa: BLE001
        pass
    # 權證逐筆(collect_warrant_ws 落地)增量聚合成每分鐘簽號淨額:購 +dirn、售 −dirn
    try:
        if WRT_MIN["day"] != today:
            WRT_MIN.update({"day": today, "off": 0, "data": {}})
        wtf = _bd.parent / "warrant_trades_ws" / f"warrant_trades_{today}.jsonl"
        if wtf.exists():
            with open(wtf, "rb") as f:
                f.seek(WRT_MIN["off"])
                chunk = f.read()
            nl = chunk.rfind(b"\n")
            if nl != -1:
                WRT_MIN["off"] += nl + 1
                for line in chunk[:nl].split(b"\n"):
                    try:
                        o = json.loads(line)
                        sgn = (o.get("dirn") or 0) * (1 if o.get("side") == "購" else -1)
                        if not sgn:
                            continue
                        hm = o["ts"][11:16]
                        dd = WRT_MIN["data"].setdefault(str(o["sid"]), {})
                        dd[hm] = dd.get(hm, 0.0) + sgn * float(o["price"]) * float(o["size"]) * 1000
                    except Exception:  # noqa: BLE001
                        continue
    except Exception:  # noqa: BLE001
        pass
    _ingest_mini_fut(today)                        # 期散:小型契約 1 口成交(FUT_MINI 檔)


def _ingest_trade(line):
    try:
        rec = json.loads(line)
    except Exception:
        return
    if rec.get("kind") != "message":
        return
    msg = rec["payload"]
    if msg.get("channel") != "trades" or msg.get("event") not in (None, "data"):
        return
    d = msg.get("data") or {}
    sid, px, vol = str(d.get("symbol", "")), d.get("price"), d.get("volume")
    if not sid or px is None or vol is None or d.get("isTrial"):
        return
    px, vol = float(px), float(vol)
    dv = vol - ST.lastvol[sid]
    ST.lastvol[sid] = vol
    if dv <= 0 or d.get("isOpen") or d.get("isClose"):
        return
    if sid not in ST.first_done:                # 開盤競價（常無 isOpen 旗標）
        ST.first_done.add(sid)
        ST.day[sid]["px0"] = px
        return
    b, a = d.get("bid"), d.get("ask")
    side = 1 if (a is not None and px >= float(a)) else (
        -1 if (b is not None and px <= float(b)) else 0)
    if side == 0:
        p = ST.last_px.get(sid)
        side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
    ST.last_px[sid] = px
    ts = rec.get("ts")
    if not ts:
        return
    t = datetime.fromisoformat(ts)
    gap = sid in ST.last_seen and (t - ST.last_seen[sid]).total_seconds() > GAP_SECONDS
    ST.last_seen[sid] = t
    amt = px * dv * 1000
    bk = bucket_key(t)
    row = ST.buckets.setdefault(sid, {}).setdefault(
        bk, {"big": 0., "retn": 0., "ret2": 0., "midn": 0., "tot": 0.,
             "px": None, "vol": 0., "pxvol": 0.})
    row["px"] = px
    ds = ST.day[sid]
    ds["hi"] = px if ds.get("hi") is None else max(ds["hi"], px)   # 今日高低(振幅倍數用)
    ds["lo"] = px if ds.get("lo") is None else min(ds["lo"], px)
    if ds["px0"] is None:
        ds["px0"] = px
    if gap and amt >= GAP_JUMP_AMT:
        return
    row["tot"] += amt
    _q = ST.recent[sid]
    _sgn = 1 if side > 0 else (-1 if side < 0 else 0)
    _q.append((t.timestamp(), px, amt, _sgn, amt >= BIG_AMT, (dv == 1 and amt < RETAIL_CAP)))
    _cut = t.timestamp() - 3700
    while _q and _q[0][0] < _cut:
        _q.popleft()
    row["vol"] += dv
    row["pxvol"] += px * dv
    ds["tot"] += amt
    sgn = 1 if side > 0 else (-1 if side < 0 else 0)
    if amt >= BIG_AMT:
        row["big"] += sgn * amt
        ds["big"] += sgn * amt
        if t.hour >= 12:
            ds["big_pm"] += sgn * amt
    elif dv == 1 and amt < RETAIL_CAP:
        row["retn"] += sgn * amt
        row["ret2"] += amt
        ds["ret"] += sgn * amt
        ds["ret2"] += amt
    else:
        row["midn"] += sgn * amt
        ds["mid"] += sgn * amt


def _rolling(sid, nts):
    """每秒滾動窗:5分=(now−300s, now]、30分=(now−1800s, now]、參與Δ=本30分 − 前30分。
    只供欄位顯示;標籤照舊用完成的 5 分桶。無逐筆時回 None。"""
    out = {k: None for k in ("big5_r", "big30_r", "retn5_r", "rbuy5_r", "rsell5_r",
                             "rbuy30_r", "rsell30_r", "dsh30_r", "w_ret_r", "r30_r",
                             "tot5_r", "tot30_r", "big5p_r", "bigp30_r", "share5_r", "dshare5_r", "sell30s_r", "r30s_r")}
    q = ST.recent.get(sid)
    if not q:
        return out
    t5, t30, t60 = nts - 300, nts - 1800, nts - 3600
    t10, t35 = nts - 600, nts - 2100          # 前一個 5 分窗 / 「本 5 分之前的 30 分」(純機構的逆大戶條件)
    big5 = big30 = tot5 = tot30 = retn5 = ret2_5 = retn30 = ret2_30 = 0.0
    tot_p = ret2_p = 0.0
    big5p = tot5p = ret2_5p = bigp30 = 0.0
    t30s = nts - 30
    sbuy = ssell = 0.0                        # 近 30 秒主動買/賣金額(竭盡狀態格用)
    px5 = px30 = px30s = None                 # px30s = 30 秒前成交價(近30秒報酬,V2.5 真空/已止跌判定)
    for ts, px, amt, sgn, isbig, isret in q:
        if ts <= t60:
            continue
        if t35 < ts <= t5 and isbig:
            bigp30 += sgn * amt
        if t10 < ts <= t5:
            tot5p += amt
            if isbig:
                big5p += sgn * amt
            if isret:
                ret2_5p += amt
        if ts <= t30:
            px30 = px
            tot_p += amt
            if isret:
                ret2_p += amt
            continue
        tot30 += amt
        if isbig:
            big30 += sgn * amt
        if isret:
            ret2_30 += amt
            retn30 += sgn * amt
        if ts > t30s:
            if sgn > 0:
                sbuy += amt
            elif sgn < 0:
                ssell += amt
        else:
            px30s = px
        if ts <= t5:
            px5 = px
        else:
            tot5 += amt
            if isbig:
                big5 += sgn * amt
            if isret:
                ret2_5 += amt
                retn5 += sgn * amt
    pxnow = q[-1][1]
    if px5 is None:
        px5 = px30
    out["big5_r"], out["big30_r"], out["retn5_r"] = big5, big30, retn5
    out["tot5_r"], out["tot30_r"], out["big5p_r"], out["bigp30_r"] = tot5, tot30, big5p, bigp30
    if sbuy + ssell > 0:
        out["sell30s_r"] = ssell / (sbuy + ssell)
    if px30s and pxnow:
        out["r30s_r"] = (pxnow / px30s - 1) * 10000
    if tot5 > 0:
        out["rbuy5_r"] = (ret2_5 + retn5) / 2 / tot5 * 100
        out["rsell5_r"] = (ret2_5 - retn5) / 2 / tot5 * 100
        out["share5_r"] = ret2_5 / tot5 * 100                      # 5 分散戶參與(買+賣)
        if tot5p > 0:
            out["dshare5_r"] = out["share5_r"] - ret2_5p / tot5p * 100   # 參與 Δ(本 5 分 − 前 5 分,pp)
    if tot30 > 0:
        out["rbuy30_r"] = (ret2_30 + retn30) / 2 / tot30 * 100
        out["rsell30_r"] = (ret2_30 - retn30) / 2 / tot30 * 100
        if tot_p > 0:
            out["dsh30_r"] = ret2_30 / tot30 * 100 - ret2_p / tot_p * 100
    if px5:
        out["w_ret_r"] = (pxnow / px5 - 1) * 10000
    if px30:
        out["r30_r"] = (pxnow / px30 - 1) * 10000
    return out


def _fmt(v, unit=1e4, dec=0, plus=True):
    if v is None:
        return "—"
    s = f"{v/unit:+.{dec}f}" if plus else f"{v/unit:.{dec}f}"
    return s


def _tick_sz(p):
    return (0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100
            else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0)


def _limits(pc):
    """台股漲跌停價(±10%,對齊 tick):漲停=不超過+10%的最大tick、跌停=不低於−10%的最小tick。"""
    import math
    up, dn = pc * 1.1, pc * 0.9
    return math.floor(up / _tick_sz(up)) * _tick_sz(up), math.ceil(dn / _tick_sz(dn)) * _tick_sz(dn)


SHADOW = {"date": None, "events": []}


def _shadow_triple(rows, now):
    """權證三條件影子帳(不顯示、不進訊號、不進OOS記分)。
    對照組=已驗證的『主力點火5分』兩腳:5分大戶淨買≥3千萬 ∧ 散買%<5%(非高價股不可測)。
    每檔每個5分窗第一次成立時記一筆,附當下權證欄位(活動量+簽號),之後自動補 5分/30分/收盤價;
    隔夜由離線分析從 stock_daily_bars 補。這樣任何權證門檻都能離線測,且能算對『兩腳單獨』的增量。
    檔案:cache/biglot_live_watch/warrant_triple_shadow_{date}.json(整檔覆寫,重啟時讀回)。"""
    d = now.strftime("%Y-%m-%d")
    f = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"warrant_triple_shadow_{d}.json"
    if SHADOW["date"] != d:
        SHADOW["date"], SHADOW["events"] = d, []
        try:
            if f.exists():
                SHADOW["events"] = json.loads(f.read_text())
        except Exception:
            SHADOW["events"] = []
    hm = now.strftime("%H:%M")
    bk = bucket_key(now).strftime("%H:%M")
    seen = {(e["sid"], e["bk"]) for e in SHADOW["events"]}
    changed = False
    for r in rows:
        if (r.get("unm") or r.get("big5") is None or r.get("rbuy5") is None
                or not r.get("px") or hm >= "13:25" or (r["sid"], bk) in seen):
            continue
        if r["big5"] >= 3e7 and r["rbuy5"] < 5:
            w = WRT.get(r["sid"]) if isinstance(WRT.get(r["sid"]), dict) else {}
            SHADOW["events"].append({
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
    for e in SHADOW["events"]:
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
            f.write_text(json.dumps(SHADOW["events"], ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def _wrt_td(c, p, bull, bear, tip):
    """權證多空一格:購額/售額(未簽號活動量,萬)+ 小字=簽號後多方占比(主動買call+主動賣put ÷ 全部主動額);
    顏色依簽號淨額:紅=多方>空方、綠=空方>多方、灰=皆0。簽號欄缺時退回未簽號(舊檔相容)。"""
    if bull is None or bear is None:
        bull, bear = c, p
    tot = bull + bear
    # 權證單價低(幾毛~幾塊),一筆 1 張只有幾百元;<10 萬顯示一位小數,免得「0/0 100%」。
    def _w(v):
        return f"{v/1e4:,.1f}" if v < 1e5 else f"{v/1e4:,.0f}"
    MIN_JUDGE = 1e5                                  # 主動額 <10 萬不下判斷(幾百元的成交不算方向)
    if tot >= MIN_JUDGE:
        pct = bull / tot * 100
        shr = f"{pct:.0f}%"
        # 判斷帶:≥60% 偏多(紅)、≤40% 偏空(綠)、其間中性(灰)——顏色與文字同一規則
        cls, lab = ("up", "偏多") if pct >= 60 else (("dn", "偏空") if pct <= 40 else ("dim", "中性"))
    elif tot > 0:
        shr, cls, lab = f"{bull / tot * 100:.0f}%", "dim", "量小"
    else:
        shr, cls, lab = "—", "dim", ""
    return (f"<td class='{cls}' style='font-size:11px' title='{tip} · 活動量 購{_w(c)}萬/售{_w(p)}萬 · "
            f"簽號 多方{_w(bull)}萬/空方{_w(bear)}萬 · 多方占比{shr} {lab}(主動額<10萬不判斷)'>"
            f"{_w(c)}/{_w(p)}<span class='dim' style='font-size:9px'> {shr}</span>"
            f"{(' <b>' + lab + '</b>') if lab else ''}</td>")


def _px_class(px, pc, chg):
    """價格著色類別:漲停紅底白字/跌停綠底白字/接近漲跌停粗字/一般漲跌。"""
    if px and pc:
        up, dn = _limits(pc)
        if px >= up - 1e-6:
            return "lup"
        if px <= dn + 1e-6:
            return "ldn"
    if chg is None:
        return ""
    if chg >= 9.0:
        return "nlup"
    if chg <= -9.0:
        return "nldn"
    return "up" if chg > 0 else ("dn" if chg < 0 else "")


#: 即時標籤引擎(2026-09-24):盤中 7 格改吃每秒滾動窗、條件連續成立 TAG_HOLD_SEC 秒才「首次觸發」,
#: 觸發後顯示經過分鐘、到基準率時距自動熄(30 分格 1800s / 5 分格 300s),滾動數反向加 ✗,13:00 後觸發加「尾」。
#: 每次觸發落地 tag_events_{date}.jsonl 供事後對照 127 日(完成桶版)基準率。日級格(蓄勢隔夜/同賣/連3買/破昨/弱開)不變。
TAG_HOLD_SEC = 10
TAG_STATE: dict = {}          # (sid, tag) -> {"pend": ts|None, "t0": ts|None, "hz": sec, "dead": bool, "tail": bool, "txt": str}
TAG_LOG_DAY = {"d": None}
#: (tag, 方向, 時距秒, 條件(r)->bool|None, 反向失效(r)->bool, 顯示名(r)->str)
def _par30(r):
    return None if (r.get("rbuy30_r") is None or r["unm"]) else (r["rbuy30_r"] + r["rsell30_r"])
def _b30n(r):
    return (r["big30_r"] / r["tot30_r"] * 100) if (r.get("big30_r") is not None and r.get("tot30_r")) else None
def _b5n(r):
    return (r["big5_r"] / r["tot5_r"] * 100) if (r.get("big5_r") is not None and r.get("tot5_r")) else None
TAG_DEFS = [
    ("主力點火", "bull", 1800,
     lambda r: (r.get("big30_r") or 0) >= 3e7 and (_par30(r) is None or _par30(r) < 45),
     lambda r: (r.get("big30_r") or 0) < 0, lambda r: "主力點火"),
    ("機構暗退", "bear", 1800,
     lambda r: (r.get("big30_r") or 0) <= -3e7 and (_par30(r) is None or _par30(r) < 15),
     lambda r: (r.get("big30_r") or 0) > 0, lambda r: "機構暗退"),
    ("噴後過熱", "bear", 1800,
     lambda r: (r.get("r30_r") or 0) >= 150,
     lambda r: (r.get("r30_r") or 0) < 50, lambda r: "噴後過熱"),
    ("勿追30", "bear", 1800,
     lambda r: (r.get("r30_r") or 0) > 30 and (((r.get("dsh30_r") or 0) > 10 and not r["unm"]) or (_b30n(r) is not None and _b30n(r) < -5)),
     lambda r: (r.get("r30_r") or 0) < 0, lambda r: "勿追"),
    ("勿追5m", "bear", 300,
     lambda r: (r.get("w_ret_r") or 0) > 20 and (((r.get("dshare5_r") or 0) > 5 and not r["unm"]) or (_b5n(r) is not None and _b5n(r) < -5)),
     lambda r: (r.get("w_ret_r") or 0) < 0,
     lambda r: "勿追5m(枯量)" if (r.get("rvol5_r") is not None and r["rvol5_r"] < 1.0) else "勿追5m"),
    ("深接30", "bull", 1800,
     lambda r: (r.get("r30_r") or 0) < -30 and _b30n(r) is not None and _b30n(r) > 5,
     lambda r: (r.get("big30_r") or 0) < 0, lambda r: "深接"),
    ("深接5m", "bull", 300,
     lambda r: (r.get("w_ret_r") or 0) < -20 and _b5n(r) is not None and _b5n(r) > 5 and (r.get("rvol5_r") or 0) >= 0.5,
     lambda r: (r.get("big5_r") or 0) < 0, lambda r: "深接5m"),
    ("虛胖接刀", "ex", 300,
     lambda r: (r.get("w_ret_r") or 0) < -20 and _b5n(r) is not None and _b5n(r) > 5 and r.get("rvol5_r") is not None and r["rvol5_r"] < 0.5,
     lambda r: False, lambda r: "▫虛胖接刀(枯量)"),
    ("散戶虛拉", "bear", 300,
     lambda r: (r.get("w_ret_r") or 0) > 20 and (r.get("rbuy5_r") or 0) >= 5 and not r["unm"],
     lambda r: (r.get("w_ret_r") or 0) < 0, lambda r: "散戶虛拉"),
    ("純機構", "bull", 1800,
     lambda r: (_b5n(r) or 0) > 10 and (r.get("tot5_r") or 0) > 0 and r.get("share5_r") is not None and r["share5_r"] < 5 and not r["unm"]
     and (r.get("bigp30_r") if r.get("bigp30_r") is not None else 0) < 0 and (r.get("big5p_r") if r.get("big5p_r") is not None else 0) < 0,
     lambda r: (r.get("big5_r") or 0) < 0,
     lambda r: "巨資機構" if (r.get("big5_r") or 0) >= 3e7 else "純機構"),
]


def _tag_engine(rows, nts, now):
    """回傳 sid -> {"bull":[txt...], "bear":[...], "ex":[...]};同時維護 TAG_STATE 並落地觸發事件。"""
    day = now.strftime("%Y-%m-%d")
    statep = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"tag_state_{day}.json"
    if TAG_LOG_DAY["d"] != day:
        TAG_STATE.clear()
        TAG_LOG_DAY["d"] = day
        try:                                   # 同日重啟:接續既有觸發時刻(不然全部歸零變 0′)
            for k, v in json.loads(statep.read_text()).items():
                sid, tag = k.split("|", 1)
                TAG_STATE[(sid, tag)] = {"pend": None, "t0": v["t0"], "hz": v["hz"], "dead": v["dead"], "tail": v["tail"], "txt": v["txt"]}
        except Exception:  # noqa: BLE001
            pass
    logp = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"tag_events_{day}.jsonl"
    fired = False
    hm = now.strftime("%H:%M")
    out = {}
    for r in rows:
        sid = r["sid"]
        res = {"bull": [], "bear": [], "ex": []}
        for tag, kind, hz, cond, dead, name in TAG_DEFS:
            st = TAG_STATE.setdefault((sid, tag), {"pend": None, "t0": None, "hz": hz, "dead": False, "tail": False, "txt": ""})
            try:
                ok = bool(cond(r))
            except Exception:  # noqa: BLE001
                ok = False
            active = st["t0"] is not None and nts - st["t0"] < hz
            if ok:
                if st["pend"] is None:
                    st["pend"] = nts
                if not active and nts - st["pend"] >= TAG_HOLD_SEC:
                    st.update({"t0": nts, "dead": False, "tail": hm >= "13:00" and hz >= 1800, "txt": name(r)})
                    active = True
                    fired = True
                    try:
                        with logp.open("a", encoding="utf-8") as f:
                            f.write(json.dumps({"t": now.strftime("%H:%M:%S"), "sid": sid, "name": r["name"], "tag": tag, "txt": st["txt"],
                                                "px": r.get("px"), "big30_r": r.get("big30_r"), "big5_r": r.get("big5_r"),
                                                "par30": _par30(r), "r30_r": r.get("r30_r"), "w_ret_r": r.get("w_ret_r"),
                                                "rvol5_r": r.get("rvol5_r")}, ensure_ascii=False) + "\n")
                    except Exception:  # noqa: BLE001
                        pass
            else:
                st["pend"] = None
            if active:
                try:
                    if dead(r):
                        st["dead"] = True
                except Exception:  # noqa: BLE001
                    pass
                age = int((nts - st["t0"]) // 60)
                txt = f"{st['txt']}{age}′" + ("✗" if st["dead"] else "") + ("尾" if st["tail"] else "")
                if age < 5 and not st["dead"]:
                    txt = f"<b>{txt}</b>"                      # ≤5 分 = 最佳狀態(粗體)
                res[kind].append(txt)
        out[sid] = res
    if fired:
        try:
            statep.write_text(json.dumps({f"{s}|{t}": {"t0": v["t0"], "hz": v["hz"], "dead": v["dead"], "tail": v["tail"], "txt": v["txt"]}
                                          for (s, t), v in TAG_STATE.items() if v["t0"] is not None}, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
    return out


#: 各訊號的時間價值曲線 (平台秒數, 歸零秒數):經過 < 平台 → 1.0;之後線性降到 歸零秒 = 0。依各格驗證時距設定(2026-09-24):
#:   主力點火/深接/勿追/噴後過熱:基準率=首次觸發後 30 分 → 0 平台、30 分歸零
#:   純機構:30 分 +24~29、45 分 +36 仍成長 → 5 分平台、45 分歸零
#:   機構暗退:流量領先價 ~2h → 60 分平台、120 分歸零(標籤顯示仍 30 分熄,計分延續)
#:   散戶虛拉:留不到收盤 → 10 分平台、60 分歸零
TAG_DECAY = {"主力點火": (0, 1800), "純機構": (300, 2700), "深接30": (0, 1800), "深接5m": (0, 1800),
             "機構暗退": (3600, 7200), "噴後過熱": (0, 1800), "散戶虛拉": (600, 3600),
             "勿追30": (0, 1800), "勿追5m": (0, 1800), "虛胖接刀": (0, 300)}


def _active_tags(sid, nts):
    """標籤 → 時間價值權重(依 TAG_DECAY 各格不同曲線);未 ✗ 且尚未歸零者才算。"""
    out = {}
    for (s_, tag), st in TAG_STATE.items():
        if s_ != sid or st["t0"] is None or st["dead"]:
            continue
        flat, zero = TAG_DECAY.get(tag, (0, st["hz"]))
        age = nts - st["t0"]
        if age >= zero:
            continue
        out[tag] = 1.0 if age < flat else max(0.0, 1.0 - (age - flat) / max(1, zero - flat))
    return out


def _score_rows(rows, mkt30, nts, mkt30_r=None):
    """隔夜分 / 盤中分:各項權重只用 0/±1/±2,依 127 日基準率;權證依 jack 要求納入盤中分(±1,未驗證)。
    寫入 r["sc_ov"], r["sc_in"], r["sc_ov_items"], r["sc_in_items"], r["sc_in_nowrt"]。"""
    for r in rows:
        sid = r["sid"]
        ds = ST.day.get(sid) or {}
        tot = ds.get("tot") or 0
        bs = r.get("bigsh_d")                      # 全日大戶佔比 %
        rp = (r["retday"] / tot * 100) if (tot and r.get("retday") is not None and not r["unm"]) else None
        cmp_ = r.get("cmp1h")
        ov, ovi = 0, []
        if bs is not None and bs >= 10:
            ov += 2; ovi.append(("大戶佔比≥+10%", +2))
            if rp is not None and rp >= 5:
                ov -= 1; ovi.append(("散戶佔比≥5%(B格)", -1))
            if cmp_ is not None and cmp_ > 1.0:
                ov -= 1; ovi.append(("壓縮>+1% 彈開", -1))
            if r.get("rvol_day") is not None and r["rvol_day"] >= 1.5:
                ov += 1; ovi.append(("全日量能≥1.5x", +1))
        elif bs is not None and bs <= -10:
            ov -= 2; ovi.append(("大戶佔比≤−10%", -2))
            if cmp_ is not None and cmp_ > 0.3:
                ov -= 1; ovi.append(("大戶賣∧壓縮>+0.3%", -1))
        if "同賣" in (r.get("stamp") or ""):
            ov -= 1; ovi.append(("同賣", -1))
        dt = r.get("dtrend") or {}
        if dt.get("above_ma5"):
            ov += 1; ovi.append(("日線↑多", +1))
        elif r.get("rs_live") is not None and r["rs_live"] > 1 and dt:
            ov -= 1; ovi.append(("相對強弱>+1∧日線↓空", -1))
        # ---- 盤中分 ----
        act = _active_tags(sid, nts)
        sc, sci = 0, []
        # 同源不累加(2026-09-24 jack 定案):同一筆大戶買會同時點亮 主力點火/純機構/深接30/深接5m → 取最大值一次;
        # 散戶側 散戶虛拉/勿追 同源 → 取一次 −1;機構暗退、噴後過熱、破昨防線 各自獨立來源。
        # 時間衰減(2026-09-24 jack 要求):標籤價值 = 權重 × (1 − 經過/時距),基準率是「首次觸發起未來30分」,越晚看剩越少;小數一位
        bull_src = [(t, p * act[t]) for t, p in (("主力點火", 2), ("純機構", 2), ("深接30", 1), ("深接5m", 1)) if t in act]
        if bull_src:
            best = max(bull_src, key=lambda x: x[1])
            sc += best[1]; sci.append(("大戶買[" + "·".join(f"{t}×{act[t]:.2f}" for t, _ in bull_src) + "]取最大", round(best[1], 1)))
        for tag, pts in (("機構暗退", -2), ("噴後過熱", -2)):
            if tag in act:
                sc += pts * act[tag]; sci.append((f"{tag}×{act[tag]:.2f}", round(pts * act[tag], 1)))
        ret_src = [t for t in ("散戶虛拉", "勿追30", "勿追5m") if t in act]
        if ret_src:
            if ("散戶虛拉" not in ret_src) and mkt30 > 0:
                sci.append(("勿追(市場30分>0,記0)", 0))
            else:
                w_ = max(act[t] for t in ret_src)
                sc -= w_; sci.append(("散戶側[" + "·".join(ret_src) + f"]×{w_:.2f}", round(-w_, 1)))
        if r.get("pmlow_warn"):
            sc -= 1; sci.append(("破昨防線", -1))
        sc_nowrt = sc
        # 權證(jack 要求納入;未驗證,±1):30 分認購+認售 ≥100 萬才判
        w = WRT.get(sid) if isinstance(WRT.get(sid), dict) else None
        if w:
            wt = (w.get("call_30") or 0) + (w.get("put_30") or 0)
            b_, s_ = (w.get("bull_30") or 0), (w.get("bear_30") or 0)
            sh = b_ / (b_ + s_) if (b_ + s_) > 0 else None
            b30 = r.get("big30_r") or 0
            if wt >= 1e6 and sh is not None:
                if sh >= 0.6 and (r.get("r30_r") or 0) > 0:
                    sc -= 1; sci.append(("權證偏多∧價漲(槓桿散戶追價)", -1))
                elif sh >= 0.6 and b30 <= -3e7:
                    sc -= 1; sci.append(("權證偏多∧大戶賣(散戶接貨)", -1))
                elif sh <= 0.4 and b30 >= 3e7:
                    sc += 1; sci.append(("權證偏空∧大戶買(散戶倒·大戶接)", +1))
        # 委託簿竭盡候選(尚非策略,jack 要求先計分;定義來自 09-23/24 tick 案例,未驗證):
        #   急殺中(近5分 ≤−0.20%)∧ 近30秒主動賣 ≤40% ∧ 買深 ≥3 分 → +1;急拉中 ∧ 主動買 ≤40%(sell≥60%)∧ 賣深 ≥3 分 → −1
        sp = r.get("sell30s_r"); w5 = r.get("w_ret_r") or 0
        bm, am = r.get("bid_min"), r.get("ask_min")
        if sp is not None:
            if w5 <= -20 and sp <= 0.40 and (bm or 0) >= 3:
                sc += 1; sci.append((f"賣盤竭盡候選(30s主動賣{sp*100:.0f}%·買深{bm:.1f}分,未驗證)", +1))
            elif w5 >= 20 and sp >= 0.60 and (am or 0) >= 3:
                sc -= 1; sci.append((f"買盤竭盡候選(30s主動買{(1-sp)*100:.0f}%·賣深{am:.1f}分,未驗證)", -1))
        r["sc_ov"], r["sc_in"], r["sc_in_nowrt"] = ov, sc, sc_nowrt
        try:
            _hm = datetime.now(TZ).strftime("%H:%M")
            r["sc_v2"], r["sc_v2_items"] = _score_v2(r, mkt30 if mkt30_r is None else mkt30_r, _hm)
            r["sc_v22"] = _score_v22_legacy(r, mkt30, _hm)
        except Exception as _e:  # noqa: BLE001
            r["sc_v2"], r["sc_v2_items"], r["sc_v22"] = None, [(f"計分失敗:{type(_e).__name__}", 0)], None
        r["sc_ov_items"], r["sc_in_items"] = ovi, sci


SC_HIST: dict = {}   # sid -> deque[(ts, score)] 近 60 分盤中分歷史(V2.2「近60分極端分」用)


#: 盤中分 V2.3 權重(2026-09-24;scripts/research/biglot_score_v23_fit.py):pit100×127 日 5 分桶面板,只用 IS(≤06-30)做
#: 「區間指標」聯合 OLS(y=未來 60 分對宇宙等權超額 bps,按日聚類 SE),權重 = 0.7×係數、|cl-t|<2 歸零、四捨五入到 0.5;
#: 過熱 400–600 與逆弱 50–100 依單調先驗沿用前一格(擬合值 t−2.0/+1.9 剛好落門檻外)。OOS(07-01~)不參與擬合。
#: 歸零項(聯合後無增量):過熱 150–200、急跌 200–600、順漲/順跌/逆強、對開盤 ±3/±5%(被過熱/散戶側吸收)、主力點火/深接/暗退/破昨。
V23_W = {   # 名稱沿用;內容為 V2.5(2026-09-24 晚)
    "散戶虛拉": -4.5, "勿追5m": -3.0,
    "過熱200-300": -5.0, "過熱300-400": -8.5, "過熱400-600": -8.5,
    "急跌≤−600": +40.0,                       # 擬合 +45~49,受 |分|≤40 上限
    "逆弱20-50": +1.5, "逆弱50-100": +1.5, "逆弱≤−100": +5.5,
    "純機構": +9.0,
    "蓄勢": +4.5, "蓄勢深": +5.0, "倒貨": -2.5,  # V2.4 分級蓄勢
    # V2.5 竭盡狀態格(biglot_score_v25_fit.py;近5分 ≤−0.2% 為「急跌」、≥+0.2% 為「急拉」;30秒主動賣占比 ≤40%=竭盡、≥60%=未竭):
    #   單獨的「賣盤竭盡」是負的(−5.5/−5.1):賣壓退了=反彈已在桶內發生;反轉指紋是「賣壓還在」或「沒人賣價卻掉(真空)」
    "急跌·賣壓未竭": +3.0, "急跌·真空": +12.5, "急跌·竭盡散戶接": -5.5, "急跌·大戶接∧未竭": +6.0, "急跌·末30秒續跌": +2.0,
    "急拉·買壓竭盡": +4.5, "急拉·末30秒續漲": -3.5,
    "權證": 3.0,                               # tick 項,面板測不到,沿用暫定 ±3(散戶接跌 +2.5 已被狀態格吸收 → 0)
}


def _score_v22_legacy(r, mkt30, hm):
    """V2.2 加總(bps;僅供並列落地/tooltip 對照,不顯示主值)。與 f63aa01 版同邏輯。"""
    if hm < "09:30":
        return 0.0
    fac = 0.5 if hm < "10:00" else 1.0; sc = 0.0
    w5 = r.get("w_ret_r"); r30 = r.get("r30_r"); rb5 = r.get("rbuy5_r"); unm = r["unm"]; b5n = _b5n(r)
    ret = []
    if w5 is not None and w5 > 20 and rb5 is not None and rb5 >= 5 and not unm: ret.append(-10 if w5 > 50 else -8)
    if w5 is not None and w5 > 20 and (((r.get("dshare5_r") or 0) > 5 and not unm) or (b5n is not None and b5n < -5)): ret.append(-8)
    if ret: sc += min(ret)
    if w5 is not None and w5 < -20 and rb5 is not None and rb5 >= 5 and not unm: sc += 4
    if r30 is not None:
        if r30 >= 600: pass
        elif r30 >= 400: sc += -21 * (600 - r30) / 200
        elif r30 >= 300: sc += -18
        elif r30 >= 200: sc += -13
        elif r30 >= 150: sc += -10
        if r30 <= -600: sc += 23
        elif r30 <= -400: sc += 22
        elif r30 <= -300: sc += 14
        elif r30 <= -200: sc += 6
        if mkt30 >= 5 and r30 >= 20 and "10:00" <= hm < "12:00": sc += -3
        if mkt30 <= -5 and r30 <= -20: sc += 3
        if mkt30 <= -5 and r30 >= 20: sc += -7
        if mkt30 >= 5:
            if r30 <= -100: sc += 11
            elif r30 <= -50: sc += 8
            elif r30 <= -20: sc += 6
    dr = r.get("day_ret")
    if dr is not None:
        if dr > 5: sc += -15
        elif dr > 3: sc += -9
        elif dr < -5: sc += 12
        elif dr < -3: sc += 8
    if (hm >= "10:00" and b5n is not None and b5n > 10 and (r.get("tot5_r") or 0) > 0 and r.get("share5_r") is not None and r["share5_r"] < 5 and not unm
            and (r.get("bigp30_r") if r.get("bigp30_r") is not None else 0) < 0 and (r.get("big5p_r") if r.get("big5p_r") is not None else 0) < 0):
        sc += 12
    sc = round(sc * fac, 1)
    return max(-40.0, min(40.0, sc))


def _score_v2(r, mkt30, hm=None):
    """盤中分 V2.3(bps;2026-09-24):權重表 V23_W(IS 聯合 OLS×0.7 收縮,t<2 歸零),各項**可加**、無同源取一次
    (聯合係數已是條件增量)、無時段係數(池化擬合;09:30 前仍不計)、|分| 上限 40。
    mkt30 應傳滾動版(與個股 r30_r 同鐘)。IS/OOS 對照見 scratch/v23_fit_2026-09-24.txt。"""
    hm = hm or datetime.now(TZ).strftime("%H:%M")
    if hm < "09:30":
        return 0.0, [("09:30 前不計分", 0)]
    sc, it = 0.0, []
    def add(k, name=None, sign=1):
        nonlocal sc
        v = round(sign * V23_W[k], 1); sc += v; it.append((name or k, v))
    w5 = r.get("w_ret_r"); r30 = r.get("r30_r"); rb5 = r.get("rbuy5_r"); unm = r["unm"]
    b5n = _b5n(r)
    if w5 is not None and w5 > 20 and rb5 is not None and rb5 >= 5 and not unm:
        add("散戶虛拉")
    if w5 is not None and w5 > 20 and (((r.get("dshare5_r") or 0) > 5 and not unm) or (b5n is not None and b5n < -5)):
        add("勿追5m")
    if r30 is not None:
        if r30 >= 600:
            it.append(("噴後過熱≥600 近漲停,不計", 0))
        elif r30 >= 400: add("過熱400-600", f"噴後過熱≥400({r30:.0f})")
        elif r30 >= 300: add("過熱300-400", "噴後過熱≥300")
        elif r30 >= 200: add("過熱200-300", "噴後過熱≥200")
        if r30 <= -600: add("急跌≤−600", f"急跌≤−600({r30:.0f})")
        if mkt30 >= 5:
            if r30 <= -100: add("逆弱≤−100")
            elif r30 <= -50: add("逆弱50-100")
            elif r30 <= -20: add("逆弱20-50")
    if (hm >= "10:00" and b5n is not None and b5n > 10 and (r.get("tot5_r") or 0) > 0 and r.get("share5_r") is not None and r["share5_r"] < 5 and not unm
            and (r.get("bigp30_r") if r.get("bigp30_r") is not None else 0) < 0 and (r.get("big5p_r") if r.get("big5p_r") is not None else 0) < 0):
        add("純機構", "巨資機構" if (r.get("big5_r") or 0) >= 3e7 else "純機構")
    # 壓縮(現價 ÷ 近12桶均價 −1,需≥8桶)× 近30分大戶佔比:大戶買而價被壓著 = 蓄勢;大戶倒而價仍在均價上 = 倒貨。
    # 單獨的大戶30分佔比(主力點火)在60分尺度為 0,增量只在與壓縮的交互。
    cmp_ = r.get("cmp1h"); b30n = _b30n(r)
    if cmp_ is not None and b30n is not None:
        if 5 <= b30n < 40 and cmp_ < -0.5: add("蓄勢深", f"蓄勢深(壓縮{cmp_:+.1f}%∧大戶30分{b30n:+.0f}%)")
        elif 5 <= b30n < 40 and cmp_ < 0: add("蓄勢", f"蓄勢(壓縮{cmp_:+.1f}%∧大戶30分{b30n:+.0f}%)")
        elif b30n >= 40 and cmp_ < 0: it.append((f"鉅額吸(大戶30分{b30n:+.0f}%,≥40% 不計)", 0))
        elif 0 < cmp_ <= 0.5 and b30n <= -10: add("倒貨", f"倒貨(壓縮{cmp_:+.1f}%∧大戶30分{b30n:+.0f}%)")
    w = WRT.get(r["sid"]) if isinstance(WRT.get(r["sid"]), dict) else None
    if w:
        wt = (w.get("call_30") or 0) + (w.get("put_30") or 0); b_, s_ = (w.get("bull_30") or 0), (w.get("bear_30") or 0)
        sh = b_ / (b_ + s_) if (b_ + s_) > 0 else None; b30 = r.get("big30_r") or 0
        if wt >= 1e6 and sh is not None:
            if sh >= 0.6 and (r30 or 0) > 0: add("權證", "權證偏多∧價漲(暫)", -1)
            elif sh >= 0.6 and b30 <= -3e7: add("權證", "權證偏多∧大戶賣(暫)", -1)
            elif sh <= 0.4 and b30 >= 3e7: add("權證", "權證偏空∧大戶買(暫)", +1)
    # 竭盡狀態格(V2.5):賣壓還在→買;賣壓退了∧散戶在接→晚了;沒人主動賣價卻掉→真空(最強);急拉對稱
    sp = r.get("sell30s_r"); r30s = r.get("r30s_r")
    if sp is not None and w5 is not None:
        exh, notexh = sp <= 0.40, sp >= 0.60
        if w5 <= -20:
            if exh and r30s is not None and r30s <= -10: add("急跌·真空", f"急跌·真空(30s主動賣{sp*100:.0f}%∧末30秒{r30s:+.0f}bps)")
            if notexh: add("急跌·賣壓未竭", f"急跌·賣壓未竭(30s主動賣{sp*100:.0f}%)")
            if exh and rb5 is not None and rb5 >= 5 and not unm: add("急跌·竭盡散戶接", f"急跌·竭盡∧散戶接(散買{rb5:.0f}%)")
            if b5n is not None and b5n > 5 and not exh: add("急跌·大戶接∧未竭", f"急跌·大戶接∧未竭(大戶5分{b5n:+.0f}%)")
            if r30s is not None and r30s <= -10 and not exh: add("急跌·末30秒續跌", f"急跌·末30秒續跌({r30s:+.0f}bps)")
            if exh and r30s is not None and r30s > 0: it.append((f"急跌·竭盡已止跌(30s主動賣{sp*100:.0f}%,反彈已發生,0)", 0))
        elif w5 >= 20:
            if notexh and r30s is not None and r30s < 0: add("急拉·買壓竭盡", f"急拉·買壓竭盡(30s主動賣{sp*100:.0f}%∧末30秒{r30s:+.0f})")
            if r30s is not None and r30s >= 10: add("急拉·末30秒續漲", f"急拉·末30秒續漲({r30s:+.0f}bps)")
    if abs(sc) > 40:
        it.append((f"上限 ±40(原 {sc:+.0f})", 0)); sc = 40.0 if sc > 0 else -40.0
    # 近 60 分極端分(⚠ 每 5 秒取樣的極值統計量,系統性大於桶級分數;只當「剛剛出現過」提示)
    from collections import deque as _dq
    hq = SC_HIST.setdefault(r["sid"], _dq()); nts = time.time(); hq.append((nts, round(sc, 1)))
    while hq and hq[0][0] < nts - 3600:
        hq.popleft()
    pk = max(hq, key=lambda x: abs(x[1])) if hq else (nts, sc)
    r["sc_v2_peak"] = (pk[1], datetime.fromtimestamp(pk[0], TZ).strftime("%H:%M"))
    return round(sc, 1), it


SC_LOGGED: set = set()   # (日, 5分桶, sid) 已落地


def _log_scores(rows, day):
    """每個 5 分桶第一次 render 時把各檔 V2.3/V2.2/隔夜分 落到 score_v2_{日}.jsonl(供累 20 日算 IC / 對照面板)。"""
    now = datetime.now(TZ); bk = f"{now.hour:02d}:{now.minute - now.minute % 5:02d}"
    if not ("09:30" <= bk <= "13:25"):
        return
    logp = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"score_v2_{day}.jsonl"
    out = []
    for r in rows:
        key = (day, bk, r["sid"])
        if key in SC_LOGGED or r.get("sc_v2") is None:
            continue
        SC_LOGGED.add(key)
        out.append(json.dumps({"date": day, "bucket": bk, "ts": now.strftime("%H:%M:%S"), "sid": r["sid"], "px": r.get("px"),
                               "sc_v23": r["sc_v2"], "sc_v22": r.get("sc_v22"), "sc_ov": r.get("sc_ov"), "sc_v1": r.get("sc_in"),
                               "items": r.get("sc_v2_items") or [], "cause": [t for t, _ in (r.get("cause") or [])],
                               "mini": ({k: (v if k != "n30" and k != "n5" else list(v)) for k, v in r["mini"].items()} if r.get("mini") else None),
                               "hold": r.get("hold")}, ensure_ascii=False))
    if out:
        try:
            logp.parent.mkdir(parents=True, exist_ok=True)
            with logp.open("a", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
        except Exception:  # noqa: BLE001
            pass



# ---- 成因標籤(jack 2026-09-24:極值分數進場前要知道「為什麼」——處置/跌停/族群/MOPS,每項標來源與時間)----
_DISP_CACHE: dict = {"date": None, "sids": {}, "mtime": None}


def _disposal_today(today: str) -> dict:
    """處置窗內的 sid → (measure, end)。來源 ${DATA_DIR}/disposal/disposal_windows.csv(fetch_disposal_list.py,
    limitup-fade-nightly 每晚更新;TWSE 可回溯、TPEx 只有當日快照靠每日累積)。"""
    f = DATA_DIR / "disposal" / "disposal_windows.csv"
    try:
        mt = f.stat().st_mtime
    except OSError:
        return {}
    if _DISP_CACHE["date"] == today and _DISP_CACHE["mtime"] == mt:
        return _DISP_CACHE["sids"]
    out = {}
    try:
        import csv
        with f.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("start", "") <= today <= row.get("end", ""):
                    out[str(row.get("stock_id", ""))] = (row.get("measure", "")[:6], row.get("end", ""))
    except Exception:  # noqa: BLE001
        pass
    _DISP_CACHE.update(date=today, sids=out, mtime=mt)
    return out


def _stock_tick(p: float) -> float:
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0


def _limit_down(y: float) -> float:
    """跌停價 = 前收 ×0.9 無條件進位到升降單位(TWSE 規則)。"""
    import math
    raw = y * 0.9; t = _stock_tick(raw)
    return round(math.ceil(raw / t - 1e-9) * t, 2)


def _mops_load(day: str) -> dict:
    """MOPS 重大訊息 sid → [(hh:mm, 主旨)]。來源 ${DATA_DIR}/cache/mops_today_{發言日}.json(fetch_mops_today.py,
    TWSE/TPEx OpenAPI 每日快照:發言日=前一營業日;盤中即時 MOPS 尚無來源)。無檔回 None(=未抓,非無訊息)。"""
    f = DATA_DIR / "cache" / f"mops_today_{day}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _prev_mops_day(today: str) -> tuple[str, dict] | tuple[None, None]:
    """往回找最近一個有檔的發言日(≤7 天)。"""
    from datetime import date as _d, timedelta as _td
    d0 = _d.fromisoformat(today)
    for k in range(1, 8):
        day = (d0 - _td(days=k)).isoformat(); m = _mops_load(day)
        if m is not None:
            return day, m
    return None, None


def _cause_tags(rows, today: str):
    """每檔寫入 r["cause"] = [(標籤, tooltip 說明含來源/時間)];只在 |盤中分|≥10 或急跌/過熱時才算族群,其餘也照標處置/跌停。"""
    disp = _disposal_today(today); mops = _mops_load(today); pday, pmops = _prev_mops_day(today)
    grp = {}
    for r in rows:
        grp.setdefault(SUBCAT.get(r["sid"]) or CATS.get(r["sid"]), []).append(r)
    # 大盤狀態(進場過濾器,不計分;jack 2026-09-24):36 檔等權 近5分 / 近30秒
    _w5s = [q["w_ret_r"] for q in rows if q.get("w_ret_r") is not None]; _r30ss = [q["r30s_r"] for q in rows if q.get("r30s_r") is not None]
    mkt5 = sum(_w5s) / len(_w5s) if len(_w5s) >= 10 else None; mkt30s = sum(_r30ss) / len(_r30ss) if len(_r30ss) >= 10 else None
    for r in rows:
        tags = []; sid = r["sid"]
        w5_, r30s_ = r.get("w_ret_r"), r.get("r30s_r")
        if w5_ is not None and w5_ <= -20 and mkt5 is not None:
            if mkt5 <= -20 and (w5_ - mkt5) > -10:
                tags.append(("跟盤殺", f"個股5分 {w5_:+.0f} vs 大盤5分 {mkt5:+.0f}bps,相對<10bps=只是跟著大盤;127日:聯合 −2.9/−3.7(t−2.3)、原始後60分 −20/−24 → 不做多(大盤不反轉)"))
            elif mkt30s is not None and r30s_ is not None and r30s_ <= -10 and mkt30s >= 0:
                tags.append(("自己殺", f"個股末30秒 {r30s_:+.0f} 而大盤末30秒 {mkt30s:+.0f}bps=大盤止跌它還在殺;127日:聯合 +3.4/+5.6(t2.1)、原始 +6/+7 → 要的格"))
            elif mkt30s is not None and mkt30s < -5:
                tags.append(("大盤仍跌", f"大盤末30秒 {mkt30s:+.0f}bps 仍在跌;127日:個股續跌∧大盤續跌 聯合 −3.4/−5.6、原始 −5/−18 → 等大盤止住"))
        if sid in disp:
            m, end = disp[sid]; tags.append((f"處置{m}", f"處置窗至 {end}(disposal_windows.csv,分盤撮合、當沖停)"))
        bk = ST.book.get(sid) or {}
        try:
            y = float(bk.get("y")) if bk.get("y") not in (None, "-", "") else None
            z = float(bk.get("z")) if bk.get("z") not in (None, "-", "") else None
            lo = float(bk.get("l")) if bk.get("l") not in (None, "-", "") else None
            if y:
                ld = _limit_down(y); bq0 = (bk.get("bq") or [0])[0] or 0
                if z is not None and z <= ld + 1e-9 and not bq0:
                    tags.append(("跌停鎖", f"現價 {z} ≤ 跌停 {ld} 且買一為空(MIS 五檔 {bk.get('t')});鎖死中不可買→非流動性反轉樣本"))
                elif lo is not None and lo <= ld + 1e-9:
                    tags.append(("觸跌停", f"今低 {lo} ≤ 跌停 {ld},現 {z}(MIS {bk.get('t')});打開後反彈屬跌停機制,勿與一般急跌混看"))
        except Exception:  # noqa: BLE001
            pass
        r30 = r.get("r30_r")
        if r30 is not None and abs(r30) >= 200:
            peers = [q for q in grp.get(SUBCAT.get(sid) or CATS.get(sid), []) if q["sid"] != sid and q.get("r30_r") is not None]
            same = [q for q in peers if (q["r30_r"] <= -200 if r30 < 0 else q["r30_r"] >= 200)]
            if peers:
                k = len(same)
                tags.append((f"族群{k}/{len(peers)}", f"同細分產業 {SUBCAT.get(sid) or CATS.get(sid)} 其餘 {len(peers)} 檔中 {k} 檔近30分同向≥2%:" +
                             ("、".join(q['name'] for q in same) if same else "無") + ";≥半數=族群連鎖(超額已扣籃子但仍屬共同衝擊,反轉率不同)"))
        if mops and sid in mops:
            for hm, subj in mops[sid][:2]:
                tags.append((f"MOPS{hm}", f"今日重大訊息 {hm}:{subj[:60]}(mops_today 快取);有訊息的急跌傾向延續非反轉"))
        elif mops is None:
            tags.append(("MOPS?", "今日盤中 MOPS 無即時來源(OpenAPI 只有 T-1 快照)——不是「無訊息」,下單前自行查 mops.twse.com.tw"))
        if pmops and sid in pmops:
            for hm, subj in pmops[sid][:2]:
                tags.append((f"昨MOPS{hm}", f"{pday} 發言 {hm}:{subj[:60]}(TWSE/TPEx OpenAPI 快照,fetch_mops_today.py);盤後訊息會反映在今日跳空與盤中"))
        r["cause"] = tags



# ---- 期散(小型契約 1 口成交 = 期貨市場散戶代理;jack 2026-09-25):只對 FUT_MINI 檔顯示,描述性、不進分數 ----
from collections import deque as _deque_mini
MINI_ROOT = {r["sid"]: (r["fut_code"][:-1] if len(r.get("fut_code", "")) == 3 and r["fut_code"].endswith("F") else r.get("fut_code", "")).lower()
             for r in _cal["universe"] if r.get("is_mini")}
MINI_ST: dict = {"day": None, "off": {}, "q": {}}       # off: root -> 檔案讀取位移;q: sid -> deque[(ts, px, size, sgn, amt)]


def _ingest_mini_fut(today: str) -> None:
    """增量讀 {root}_trades_{today}.jsonl(期貨簿收集器落地,交易所 µs 時戳),只留 FUT_MINI 檔;主動方向以該筆 bid/ask 判。"""
    if MINI_ST["day"] != today:
        MINI_ST.update({"day": today, "off": {}, "q": {}})
    for sid, root in MINI_ROOT.items():
        f = DATA_DIR.parent / "cache" / f"{root}_trades" / f"{root}_trades_{today}.jsonl"
        if not f.exists():
            continue
        try:
            with open(f, "rb") as fh:
                fh.seek(MINI_ST["off"].get(root, 0)); chunk = fh.read()
            nl = chunk.rfind(b"\n")
            if nl == -1:
                continue
            MINI_ST["off"][root] = MINI_ST["off"].get(root, 0) + nl + 1
            q = MINI_ST["q"].setdefault(sid, _deque_mini())
            for line in chunk[:nl].split(b"\n"):
                try:
                    o = json.loads(line)
                    if o.get("stale") or o.get("quote_type") not in (None, "FUTURE"):
                        continue
                    px, sz, b, a = float(o["price"]), float(o["size"]), o.get("bid"), o.get("ask")
                    sgn = 1 if (a is not None and px >= float(a)) else (-1 if (b is not None and px <= float(b)) else 0)
                    ts = float(o["trade_time"]) / 1e6
                    q.append((ts, px, sz, sgn, px * sz * 100))          # 小型契約 1 口 = 100 股
                except Exception:  # noqa: BLE001
                    continue
            cut = time.time() - 7200
            while q and q[0][0] < cut:
                q.popleft()
        except Exception:  # noqa: BLE001
            continue


def _mini_stats(sid: str, nts: float) -> dict | None:
    q = MINI_ST["q"].get(sid)
    if not q:
        return None
    out = {}
    for lab, win in (("5", 300), ("30", 1800)):
        t0 = nts - win; n1 = a1 = tot = 0.0; nb = ns = 0
        for ts, px, sz, sgn, amt in q:
            if ts <= t0: continue
            tot += amt
            if sz == 1 and sgn:
                n1 += sgn * amt; a1 += amt; nb += (sgn > 0); ns += (sgn < 0)
        out[f"net{lab}"] = n1; out[f"share{lab}"] = (a1 / tot * 100) if tot > 0 else None; out[f"n{lab}"] = (nb, ns); out[f"tot{lab}"] = tot
    return out


def _mini_td(r) -> str:
    if r["sid"] not in FUT_MINI:
        return "<td class='dim'>—</td>"
    m = r.get("mini")
    if not m or m.get("tot30", 0) <= 0:
        return "<td class='dim' title='期散:小型契約 1 口成交(期貨散戶代理);今日尚無小型成交'>無</td>"
    net, sh, (nb, ns) = m["net30"], m["share30"], m["n30"]
    cls = "up" if net > 0 else ("dn" if net < 0 else "dim")
    tip = (f"期散(描述性,不進分數):小型契約單筆 1 口(1 口=100 股≈{r.get('px') or 0:.0f}×100 元)的主動買−主動賣淨額,期貨散戶代理。"
           f"近30分 淨 {net/1e4:+,.0f} 萬(主動買 {nb} 筆/主動賣 {ns} 筆)·1 口成交占全部小型成交 {sh:.0f}%;近5分 淨 {m['net5']/1e4:+,.0f} 萬。"
           "⚠ 與現股散戶(1 張<500 萬)是不同母體;小型契約有造市商對敲,主動簽號只能濾掉一部分;累 20 日後與可測檔對照再決定用途")
    return (f"<td class='{cls}' title='{html_mod.escape(tip, quote=True)}'>{net/1e4:+,.0f}"
            f"<span class='dim' style='font-size:9px'> {sh:.0f}%·{nb}/{ns}</span></td>")


def _score_td(r):
    ov, sc = r.get("sc_ov"), r.get("sc_in")
    if ov is None or sc is None:
        return "<td class='dim'>—</td>"

    def _c(v):
        return "up" if v > 0 else ("dn" if v < 0 else "dim")
    v2 = r.get("sc_v2"); v2i = r.get("sc_v2_items") or []
    tip = ("隔夜分:" + (" · ".join(f"{k} {v:+d}" for k, v in r["sc_ov_items"]) or "無") +
           " ‖ 盤中分V2.5(bps,60分,IS聯合OLS×0.7,上限±40):" + (" · ".join(f"{k} {v:+.1f}" for k, v in v2i) or "無") +
           " ‖ 盤中分V1(0/±1/±2,標籤×衰減,並列20日):" + (" · ".join(f"{k} {v:+.1f}" for k, v in r["sc_in_items"]) or "無") +
           f" = {sc:+.1f}" +
           (f" ‖ V2.2 加總並列 {r['sc_v22']:+.1f}" if r.get("sc_v22") is not None else "") +
           (f" ‖ 高波動日 ×(今日振幅 {r['amp_ratio']:.1f}x 20日均:同分對應更大 bps,分數不變)" if (r.get("amp_ratio") or 0) >= 1.5 else "") +
           " ‖ V2.3 IS/OOS 見 scratch/v23_fit_2026-09-24.txt;每桶落地 score_v2_{日}.jsonl 供累 20 日算 IC")
    z = TX_LAST.get("z")
    bg = " background:#21262d;" if (z is not None and abs(z) >= 1) else ""
    big_ov = " style='font-size:13px'" if abs(ov) >= 3 else ""
    v2s = v2 if v2 is not None else 0.0
    big_sc = " style='font-size:13px'" if abs(v2s) >= 15 else ""
    pk = r.get("sc_v2_peak")
    pk_html = (f" <span class='dim' style='font-size:9px'>峰<span class='{_c(pk[0])}'>{pk[0]:+.0f}</span>@{pk[1]}</span>"
               if (pk and abs(pk[0]) >= 15 and abs(pk[0]) > abs(v2s)) else "")
    cause = r.get("cause") or []
    if cause:
        tip += " ‖ 成因:" + " · ".join(f"[{t}] {d}" for t, d in cause)
    h = r.get("hold"); hold_html = ""
    if h:
        pn = f"{h['pnl']:+.0f}" if h["pnl"] is not None else "—"
        fl = " ".join(f"<b style='color:#f85149'>{html_mod.escape(f)}</b>" for f in h["flags"])
        hint = f" <span style='color:#3fb950'>{h['hint']}</span>" if h.get("hint") else ""
        hold_html = (f"<br><span style='font-size:10px;color:#79c0ff'>持 {h['hm'][:5]} 損益 {pn} · {h['min']:.0f}分 · 分 {h['score'] if h['score'] is not None else '—'}"
                     f"{(' 低'+str(int(h['low_s']))+'s') if h['low_s'] else ''}</span> {fl}{hint}")
        tip += f" ‖ 持倉:進 {h['hm']} @ {h['px0']} · 出場規則=分數≤0 連續 30 秒 / 壞標籤(虛拉·過熱·竭盡∧散戶接) / 60 分到期;獲利≥50 可停利;不設移動停利/硬停損/破昨低(面板對照較差)"
    _col = {"處置": "#f0883e", "跌停鎖": "#f85149", "觸跌停": "#f85149", "族群": "#d29922", "MOPS?": "#8b949e", "MOPS": "#a371f7", "昨MOPS": "#7d5bbe", "跟盤殺": "#f85149", "自己殺": "#3fb950", "大盤仍跌": "#d29922"}
    def _cc(t):
        return next((v for k, v in _col.items() if t.startswith(k)), "#8b949e")
    cause_html = ("<br><span style='font-size:9px'>" + " ".join(f"<span style='color:{_cc(t)}'>{html_mod.escape(t)}</span>" for t, _ in cause) + "</span>") if cause else ""
    return (f"<td style='text-align:left;white-space:nowrap;{bg}' title='{html_mod.escape(tip, quote=True)}'>"
            f"<span class='dim'>隔</span><b class='{_c(ov)}'{big_ov}>{ov:+d}</b> "
            f"<span class='dim'>盤</span><b class='{_c(v2s)}'{big_sc}>{v2s:+.0f}</b><span class='dim' style='font-size:9px'>bps</span>{pk_html}{cause_html}{hold_html}</td>")


def render():
    now = datetime.now(TZ)
    all_bks = sorted({bk for m in ST.buckets.values() for bk in m})
    done = [bk for bk in all_bks if bk + timedelta(minutes=5, seconds=10) <= now]
    cur = done[-1] if done else None
    prev = done[-2] if len(done) >= 2 else None
    win6 = done[-6:] if done else []
    win6p = done[-12:-6] if len(done) >= 7 else []
    base_bk = max((bk for bk in all_bks if bk < win6[0]), default=None) if win6 else None

    prior6 = done[-7:-1] if len(done) >= 7 else []

    rows, rets5, rets30 = [], [], []
    _dayrets = []
    for sid in NAMES:
        m = ST.buckets.get(sid, {})
        ds = ST.day.get(sid)
        a = m.get(cur) if cur else None
        p = m.get(prev) if prev else None
        r = {"sid": sid, "name": NAMES[sid], "cat": SUBCAT.get(sid, CATS.get(sid, "")),
             "amp20": AMP20.get(sid),
             "unm": sid in RET_UNM}
        # 5分窗
        r["w_ret"] = (a["px"] / p["px"] - 1) * 10000 if (a and p and a["px"] and p["px"]) else None
        if r["w_ret"] is not None:
            rets5.append(r["w_ret"])
        r["big5"] = a["big"] if a else None
        r["big_prior6"] = (sum(m[bk]["big"] for bk in prior6 if bk in m)
                           if prior6 else None)
        r["big5p"] = p["big"] if p else None
        r["tot5"] = a["tot"] if a else None
        r["tot5p"] = p["tot"] if p else None
        r["retn5"] = a["retn"] if a else None
        rb = RVOL_BASE.get(sid, {})
        bk_lbl = cur.strftime("%H:%M") if cur else None
        r["rvol5"] = (a["tot"] / rb[bk_lbl] if (a and a["tot"] and bk_lbl in rb and rb[bk_lbl] > 0)
                      else None)
        if win6:
            b30 = sum(rb.get(bk.strftime("%H:%M"), 0) for bk in win6)
            t30v = sum(m[bk]["tot"] for bk in win6 if bk in m)
            r["rvol30"] = t30v / b30 if b30 > 0 else None
        else:
            r["rvol30"] = None
        # 全日量能 = 今日累計成交額 ÷ 同時段基準累計(近5日同時段中位加總),供淨分 +1(127日:成交÷20日均額 控佔比後 +13.8/t2.64)
        _exp = sum(rb.get(bk.strftime("%H:%M"), 0) for bk in done)
        r["rvol_day"] = (ds["tot"] / _exp) if (ds and ds.get("tot") and _exp > 0) else None
        share = a["ret2"] / a["tot"] * 100 if (a and a["tot"]) else None
        share_p = p["ret2"] / p["tot"] * 100 if (p and p["tot"]) else None
        r["share5"] = share
        if a and a["tot"]:
            r["rbuy5"] = (a["ret2"] + a["retn"]) / 2 / a["tot"] * 100
            r["rsell5"] = (a["ret2"] - a["retn"]) / 2 / a["tot"] * 100
        else:
            r["rbuy5"] = r["rsell5"] = None
        r["dshare"] = share - share_p if (share is not None and share_p is not None) else None
        # 大戶連續同向窗數
        streak = 0
        for bk in reversed(done):
            v = m.get(bk, {}).get("big", 0)
            if streak == 0:
                sgn0 = 1 if v > 0 else (-1 if v < 0 else 0)
                if sgn0 == 0:
                    break
                streak = sgn0
            else:
                if (v > 0) == (streak > 0) and v != 0:
                    streak += 1 if streak > 0 else -1
                else:
                    break
        r["streak"] = streak
        # 30分窗
        if win6:
            r["big30"] = sum(m[bk]["big"] for bk in win6 if bk in m)
            t30 = sum(m[bk]["tot"] for bk in win6 if bk in m)
            r["tot30"] = t30
            _r2 = sum(m[bk]["ret2"] for bk in win6 if bk in m)
            _rn = sum(m[bk]["retn"] for bk in win6 if bk in m)
            r["rbuy30"] = (_r2 + _rn) / 2 / t30 * 100 if t30 else None
            r["rsell30"] = (_r2 - _rn) / 2 / t30 * 100 if t30 else None
            r["share30"] = (_r2 / t30 * 100
                            if t30 else None)
            r["big30p"] = sum(m[bk]["big"] for bk in win6p if bk in m) if win6p else None
            t30p = sum(m[bk]["tot"] for bk in win6p if bk in m) if win6p else 0
            sh30p = (sum(m[bk]["ret2"] for bk in win6p if bk in m) / t30p * 100
                     if t30p else None)
            r["dsh30"] = (r["share30"] - sh30p
                          if (r["share30"] is not None and sh30p is not None) else None)
            px_end = next((m[bk]["px"] for bk in reversed(win6) if bk in m and m[bk]["px"]), None)
            px_base = (m[base_bk]["px"] if base_bk and base_bk in m and m[base_bk].get("px")
                       else next((m[bk]["px"] for bk in win6 if bk in m and m[bk]["px"]), None))
            r["r30"] = (px_end / px_base - 1) * 10000 if (px_end and px_base) else None
            if r["r30"] is not None:
                rets30.append(r["r30"])
        else:
            r["big30"] = r["share30"] = r["r30"] = None
            r["rbuy30"] = r["rsell30"] = None
            r["tot30"] = r["big30p"] = r["dsh30"] = None
        # 全日
        last_price = ST.last_px.get(sid)
        r.update(_rolling(sid, time.time()))            # 5分/30分 欄位每秒滾動(顯示用 + 即時標籤引擎)
        r["mini"] = _mini_stats(sid, time.time()) if sid in FUT_MINI else None   # 期散(小型契約 1 口)
        r["rvol5_r"] = ((r["tot5_r"] / rb[bk_lbl]) if (r.get("tot5_r") and bk_lbl in rb and rb[bk_lbl] > 0)
                        else None)                        # 滾動 5 分量能倍數(對同時段基準)
        r["px"] = last_price
        _pc = PREV_CLOSE.get(sid)                       # 前一交易日收盤(專業看盤主報價基準)
        r["chg_amt"] = (last_price - _pc) if (_pc and last_price) else None
        r["chg_pct"] = ((last_price / _pc - 1) * 100) if (_pc and last_price) else None
        r["day_ret"] = ((last_price / ds["px0"] - 1) * 100
                        if (ds and ds["px0"] and last_price) else None)
        r["bigday"] = ds["big"] if ds else None
        r["retday"] = ds["ret"] if ds else None
        r["bigpm"] = ds["big_pm"] if ds else None
        # 融資/借券變化幅度波動風險分數（T-1資料，只預測盤中振幅，非方向/跳空訊號）
        vr = VOLRISK.get(sid)
        r["volrisk_score"] = vr["score"] if vr else None
        r["volrisk_tier"] = vr["tier"] if vr else None
        if not vr:
            r["volrisk_title"] = "融資/借券歷史資料不足60個交易日，無法計算分數"
        elif vr.get("stale"):
            r["volrisk_title"] = (
                f"融資或借券最新資料已 {vr['days_stale']} 天未更新(>{VOLRISK_STALE_DAYS}天門檻)"
                f"(融資asof {vr['margin_asof']}／借券asof {vr['lending_asof']})，"
                f"可能是處置股停融資或資料延遲，不計分數")
        else:
            stale_note = f"（資料{vr['days_stale']}天前,較舊）" if vr["days_stale"] > 2 else ""
            r["volrisk_title"] = (
                f"波動風險分數 {vr['score']:.0f}/100{stale_note} · "
                f"T-1 融資({vr['margin_asof']})日變動{vr['margin_pct']*100:+.1f}%"
                f"(變動幅度歷史分位{vr['margin_abs_pctile']*100:.0f}%) · "
                f"借券({vr['lending_asof']})日變動{vr['lending_pct']*100:+.1f}%"
                f"(變動幅度歷史分位{vr['lending_abs_pctile']*100:.0f}%) · "
                f"宇宙回測:分數每+1分,控制當日振幅後隔日振幅仍+0.006pp(t3.40 p0.0007)。"
                f"只預測盤中來回幅度,對隔日淨報酬/跳空/量能皆無解釋力,非方向訊號")
        # 隔夜策略因子
        r["bigsh_d"] = (ds["big"] / ds["tot"] * 100) if (ds and ds["tot"]) else None
        last12 = [m[bk]["px"] for bk in done[-12:] if bk in m and m[bk]["px"]]
        r["cmp1h"] = ((last_price / (sum(last12) / len(last12)) - 1) * 100
                      if (len(last12) >= 8 and last_price) else None)
        hb = HIST_BIG.get(sid, [])
        streak_ok = (ds and ds["big"] > 0 and len(hb) >= 2 and hb[-1] > 0 and hb[-2] > 0)
        tongmai = (ds and ds["big"] < 0 and ds["ret"] < 0)
        weak_open = tongmai and (r["cmp1h"] is not None and r["cmp1h"] > 0)
        r["stamp"] = ("連3買" if streak_ok else "") + ("⚠同賣" if tongmai else "") + ("↓弱開" if weak_open else "")
        ypl = Y_PMLOW.get(sid)
        r["pmlow"] = ypl                             # 昨日午後低=防線價(供破昨防線標註)
        r["pmlow_warn"] = (ypl is not None and last_price is not None and last_price <= ypl * 1.002)
        # VWAP 與委託簿
        tv = sum(v["vol"] for v in m.values())
        tpv = sum(v["pxvol"] for v in m.values())
        vwap = tpv / tv if tv else None
        r["vwap_gap"] = (last_price / vwap - 1) * 10000 if (vwap and last_price) else None
        bkk = ST.book.get(sid)
        if bkk:
            try:
                v = int(bkk.get("v") or 0)
                tstr = bkk.get("t", "09:00:00")
                mins = max((int(tstr[:2]) - 9) * 60 + int(tstr[3:5]), 1)
                pm_rate = v / mins
                bq, aq = sum(bkk.get("bq", [])), sum(bkk.get("aq", []))
                r["bid_min"] = bq / pm_rate if pm_rate else None
                r["ask_min"] = aq / pm_rate if pm_rate else None
                u, px_now = bkk.get("u"), last_price or bkk.get("z")
                r["lu_dist"] = ((float(u) / px_now - 1) * 100
                                if (u and px_now) else None)
            except Exception:
                r["bid_min"] = r["ask_min"] = r["lu_dist"] = None
        else:
            r["bid_min"] = r["ask_min"] = r["lu_dist"] = None
        if r.get("day_ret") is not None:
            _dayrets.append(r["day_ret"])
        rows.append(r)

    mkt5 = sum(rets5) / len(rets5) if rets5 else 0.0
    mkt30 = sum(rets30) / len(rets30) if rets30 else 0.0
    _r30r = [r["r30_r"] for r in rows if r.get("r30_r") is not None]
    mkt30_r = sum(_r30r) / len(_r30r) if _r30r else mkt30      # 滾動版市場 30 分(V2.3 逆弱用,與個股 r30_r 同鐘)

    # 排名(1=最大;注意力分流用,非訊號——個股排名持續性已檢定為不可持續)
    def _rank(key):
        order = sorted((r for r in rows if r.get(key) is not None),
                       key=lambda x: -x[key])
        return {r["sid"]: i + 1 for i, r in enumerate(order)}
    rk5, rk5p = _rank("big5"), _rank("big5p")
    rkh, rkhp = _rank("tot5"), _rank("tot5p")
    rk30, rk30p, rkd = _rank("big30"), _rank("big30p"), _rank("bigday")
    for r in rows:
        r["r5"] = rk5.get(r["sid"])
        r["d5"] = (rk5p[r["sid"]] - r["r5"]) if (r["r5"] and r["sid"] in rk5p) else None
        r["rh"] = rkh.get(r["sid"])
        r["dh"] = (rkhp[r["sid"]] - r["rh"]) if (r["rh"] and r["sid"] in rkhp) else None
        r["r30r"] = rk30.get(r["sid"])
        r["d30"] = ((rk30p[r["sid"]] - r["r30r"])
                    if (r["r30r"] and r["sid"] in rk30p) else None)
        r["rdr"] = rkd.get(r["sid"])
    # 旗標(30分尺度,127日面板驗證:勿追超額-4.9~-5.6bps cl-t≈-3;跌深大戶接+8.9bps cl-t+2.3)
    for r in rows:
        r["flag"] = ""
        big30n = (r["big30"] / r["tot30"] * 100
                  if (r.get("big30") is not None and r.get("tot30")) else None)
        if r["r30"] is not None and r["r30"] > 30:
            if ((r["dsh30"] is not None and r["dsh30"] > 10 and not r["unm"])
                    or (big30n is not None and big30n < -5)):
                r["flag"] = "⚠勿追30"
        elif (r["r30"] is not None and r["r30"] < -30
              and big30n is not None and big30n > 5):
            r["flag"] = "🟢跌深大戶接"
        # 5分早期旗標(127日驗證預測未來30分:F8超額-6.1/cl-t-4.9、F9 -9.1/-5.0;
        #  F7 +10.8/+2.8僅觀察)——與30分旗標並列顯示
        big5n = (r["big5"] / r["tot5"] * 100
                 if (r.get("big5") is not None and r.get("tot5")) else None)
        early = ""
        tot5v = r.get("tot5") or 0
        if (big5n is not None and big5n > 10 and tot5v > 0
                and r["share5"] is not None and r["share5"] < 5 and not r["unm"]
                and r["big_prior6"] is not None and r["big_prior6"] < 0
                and r["big5p"] is not None and r["big5p"] < 0):
            # 127日兩兩交互測試定案:核心=逆大戶(雙尺度)∧散戶<5%,+23.8bps/t5.18 n=1801
            # 市場方向條件是死重(只+1.3bps卻砍40%樣本),已移除;7/7月為正
            # 劑量≥3000萬:+28.7/t4.09、45分+36.1/t3.84
            early = ("💎💎巨資機構(≥3千萬)" if r["big5"] >= 3e7
                     else "💎純機構")
        elif r["w_ret"] is not None and r["w_ret"] > 20:
            if ((r["dshare"] is not None and r["dshare"] > 5 and not r["unm"])
                    or (big5n is not None and big5n < -5)):
                early = "⚠勿追5m"
                if r["rvol5"] is not None and r["rvol5"] < 1.0:
                    early = "⚠勿追5m(枯量,最強帶)"   # 127日:<1x 帶超額-7~-9bps t≈-3.3
        elif (r["w_ret"] is not None and r["w_ret"] < -20
              and big5n is not None and big5n > 5):
            if r["rvol5"] is not None and r["rvol5"] >= 0.5:
                early = "🟢跌深大戶接"               # RVOL>=0.5 條件版:127日+11~14bps cl-t 3.4-3.9
            else:
                early = "▫虛胖接刀(枯量,超額≈0)"     # <0.5x 帶127日超額≈0(名字直接標無效,別和「深接」搞混)
        if early:
            r["flag"] = (r["flag"] + " " + early).strip()

    # 漲訊/跌訊 逐條件計分(只收錄已驗證格;每格獨立打勾,分數=命中數)
    for r in rows:
        par30 = ((r["rbuy30"] or 0) + (r["rsell30"] or 0)
                 if (r.get("rbuy30") is not None and not r["unm"]) else None)
        bear = []
        if r.get("r30") is not None and r["r30"] >= 150:
            bear.append("噴後過熱")        # 波段峰後30分均-32/中位-45
        if "⚠勿追" in r["flag"]:
            bear.append("勿追")        # 漲×參與跳升或大戶賣 -5~-9.6bps(趨勢日-32)
        if (r.get("big30") is not None and r["big30"] <= -3e7
                and (par30 is None or par30 < 15)):
            bear.append("機構暗退")      # 30分大戶賣≥3千萬∧散戶缺席=機構主動調節(金居格)
        if (r.get("w_ret") is not None and r["w_ret"] > 20
                and r.get("rbuy5") is not None and r["rbuy5"] >= 5 and not r["unm"]):
            bear.append("散戶虛拉")      # 散戶買方推的急拉留不到收盤 -6~-9bps/t-5
        if "⚠同賣" in r["stamp"]:
            bear.append("同賣")        # 大戶賣∧散戶賣 隔夜-28bps/t-6
        if r.get("pmlow_warn"):
            bear.append(f"破昨防線@{r['pmlow']:g}" if r.get("pmlow") else "破昨防線")  # 觸昨日午後低=主力尾盤防守位 -125bps/73%貫穿
        bull = []
        if (r.get("big30") is not None and r["big30"] >= 3e7
                and (par30 is None or par30 < 45)):
            bull.append("主力點火")     # 大戶買≥3千萬∧散戶<45%=健康首發(唯一正格;台股「抬轎」易誤解為散戶)
        if "💎" in r["flag"]:
            bull.append("巨資機構" if "💎💎" in r["flag"] else "純機構")  # 逆勢純機構 +24~29/t5.2(文字區分,不靠鑽石數量)
        if "🟢" in r["flag"]:
            bull.append("深接")        # 跌深大戶接(RVOL≥0.5,正EV) +11~14bps/t3.4+
        if (r.get("bigsh_d") is not None and r["bigsh_d"] >= 10
                and r.get("cmp1h") is not None and r["cmp1h"] < 0):
            bull.append("蓄勢隔夜")     # 佔比≥10%∧收盤前壓著=隔夜雙鍵(IC t7.1)
        if "連3買" in r["stamp"]:
            bull.append("連3買")       # 持續章(確認格)
        r["bear_n"], r["bear_txt"] = len(bear), "·".join(bear)
        r["bull_n"], r["bull_txt"] = len(bull), "·".join(bull)
    # ---- 即時標籤引擎覆寫盤中 7 格(完成桶版仍算,供 flagbar/舊欄);日級格照舊接在後面 ----
    try:
        _tg = _tag_engine(rows, time.time(), now)
        for r in rows:
            g = _tg.get(r["sid"], {"bull": [], "bear": [], "ex": []})
            bull = g["bull"] + [t for t in r["bull_txt"].split("·") if t in ("蓄勢隔夜", "連3買")]
            bear = g["bear"] + [t for t in r["bear_txt"].split("·") if t == "同賣" or t.startswith("破昨防線")]
            r["bull_n"], r["bull_txt"] = len(bull), "·".join(bull)
            r["bear_n"], r["bear_txt"] = len(bear), "·".join(bear)
            r["ex_txt"] = " ".join(g["ex"])
    except Exception as _e:  # noqa: BLE001 -- 引擎失敗退回完成桶版標籤
        print(f"[tag_engine] {_e!r}", file=sys.stderr)
    # ---- 淨分(2026-09-24 設計,加總分未驗證;各項權重依 127 日基準率 0/±1/±2)----
    try:
        _score_rows(rows, mkt30, time.time(), mkt30_r)
        _cause_tags(rows, ST.date)
        _hold_update(rows)
        _log_scores(rows, ST.date)
    except Exception as _e:  # noqa: BLE001
        print(f"[score] {_e!r}", file=sys.stderr)

    # 固定產業鏈排序(不隨大戶流跳位);查無者(理論上不會有)排最後、依big30
    rows.sort(key=lambda r: (SORT_INDEX.get(r["sid"], 999), -(r["big30"] or 0)))
    # 產業交界的最後一列→畫粗線
    grpend = set()
    for i, r in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if nxt is None or CLUSTER_OF.get(r["sid"]) != CLUSTER_OF.get(nxt["sid"]):
            grpend.add(r["sid"])
    win_lbl = (f"{cur.strftime('%H:%M')}–{(cur+timedelta(minutes=5)).strftime('%H:%M')}"
               if cur else "—")
    w30_lbl = (f"{win6[0].strftime('%H:%M')}–{(win6[-1]+timedelta(minutes=5)).strftime('%H:%M')}"
               if win6 else "—")

    # 旗標速覽 + 資料延遲警示
    fl_chase = " ".join(f"{r['sid']}{r['name']}" for r in rows if r["flag"] == "⚠勿追30")
    fl_catch = " ".join(f"{r['sid']}{r['name']}" for r in rows
                        if r["flag"] == "🟢跌深大戶接")
    flag_bar = ""
    gate_txt = ""
    if UNI5 is not None:
        gate_on = UNI5 < -5
        gate_txt = (f"<span style='color:{'#ff7b72' if gate_on else '#8b949e'}'>閘門(近5日{UNI5:+.1f}%):"
                    f"{'🔴啟動-僅記帳' if gate_on else '🟢關'}</span> · ")
    on_pool = [r for r in rows if r.get("bigday") and r["bigday"] > 0 and not r["unm"]]
    cand_txt = ""
    if now.strftime("%H:%M") >= "13:00" and len(on_pool) >= 5:
        pool = sorted(on_pool, key=lambda r: -r["bigday"])[:10]
        pool = [r for r in pool if r.get("cmp1h") is not None]
        picks = sorted(pool, key=lambda r: r["cmp1h"])[:3]
        cand_txt = ("<span style='color:#ffd700'>隔夜候選(前10∧壓縮深3,13:25定案):</span> "
                    + " ".join(f"{r['sid']}{r['name']}({r['cmp1h']:+.1f}%/佔{r['bigsh_d']:.0f}%)"
                               for r in picks) + " · ")
    if fl_chase:
        flag_bar += f"<span class='warnv'>⚠勿追30(漲窗×參與跳升/大戶賣):</span> {fl_chase} "
    if fl_catch:
        flag_bar += f"<span style='color:#3fb950'>🟢跌深大戶接(唯一正EV格):</span> {fl_catch} "
    fl_e1 = " ".join(f"{r['sid']}{r['name']}" for r in rows if "⚠勿追5m" in r["flag"])
    fl_e2 = " ".join(f"{r['sid']}{r['name']}" for r in rows if "🟡接刀觀察" in r["flag"])
    fl_dia = " ".join(f"{r['sid']}{r['name']}" for r in rows if "💎" in r["flag"])
    if fl_dia:
        flag_bar = (f"<span style='color:#79c0ff'>💎純機構買單(127日+16bps/t4.4,全系統最強格):</span> "
                    f"{fl_dia} ") + flag_bar
    if fl_e1:
        flag_bar += f"<span class='warnv'>⚠勿追5m(早期):</span> {fl_e1} "
    if fl_e2:
        flag_bar += f"<span style='color:#d29922'>🟡接刀觀察(5m早期,未達門檻):</span> {fl_e2}"
    if not flag_bar:
        flag_bar = "<span class='dim'>本窗無旗標</span>"
    raw_path = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{ST.date}.jsonl"
    in_mkt = now.weekday() < 5 and "08:30" <= now.strftime("%H:%M") <= "13:32"   # 含 08:30 起盤前試撮
    stale_bar = ""
    if in_mkt:
        try:
            age = time.time() - raw_path.stat().st_mtime
            if age > 90:
                stale_bar = (f"<div style='background:#6e1a1a;color:#ffb3b3;padding:4px 8px;"
                             f"font-weight:700'>⚠ 資料延遲 {age:.0f} 秒——collector 可能斷線,"
                             f"表格為舊資料</div>")
        except FileNotFoundError:
            stale_bar = ("<div style='background:#6e1a1a;color:#ffb3b3;padding:4px 8px;"
                         "font-weight:700'>⚠ 今日 raw 檔不存在——collector 未啟動</div>")
    else:
        stale_bar = "<div style='color:#8b949e;padding:2px 8px'>盤後定格(非交易時段)</div>"

    def td(v, fmt="wan", cls_by_sign=True, unm=False):
        if unm:
            return "<td class='dim'>不可測</td>"
        if v is None:
            return "<td class='dim'>—</td>"
        cls = ""
        if cls_by_sign:
            cls = "up" if v > 0 else ("dn" if v < 0 else "")
        if fmt == "wan":
            txt = f"{v/1e4:+,.0f}"
        elif fmt == "yi":
            txt = f"{v/1e8:+.2f}"
        elif fmt == "bps":
            txt = f"{v:+.0f}"
        elif fmt == "pct":
            txt = f"{v:.0f}%"
            cls = "warnv" if v >= 35 else ""
        elif fmt == "pct2":
            txt = f"{v:+.2f}%"
        elif fmt == "min":
            txt = f"{v:.1f}分"
            cls = "wall" if v >= 10 else ("dim" if v < 3 else "")
        elif fmt == "int":
            txt = f"{v:+d}"
        else:
            txt = str(v)
        return f"<td class='{cls}'>{txt}</td>"

    def rk_td(n, d=None):
        if n is None:
            return "<td class='dim'>—</td>"
        arrow = ""
        if d is not None and d != 0:
            arrow = (f"<span class='up'>▲{d}</span>" if d > 0
                     else f"<span class='dn'>▼{-d}</span>")
        cls = "rk1" if n <= 3 else ("rkN" if n >= 43 else "")
        return f"<td class='{cls}'>{n}{arrow}</td>"

    VR_CLS = {"🌊🌊": "vr2", "🌊": "vr1", "": "vr0"}   # 對應 VOLRISK_TIERS 的分級,單一事實來源

    def vr_td(r):
        title = html_mod.escape(r.get("volrisk_title") or "")
        score = r.get("volrisk_score")
        if score is None:
            return f"<td class='dim' title=\"{title}\">—</td>"
        tier = r.get("volrisk_tier")
        cls = VR_CLS.get(tier, "") if tier is not None else ""
        icon = tier if tier else ""
        return f"<td class='{cls}' title=\"{title}\">{icon}{score:.0f}</td>"

    _mktday = sum(_dayrets) / len(_dayrets) if _dayrets else None
    for r in rows:
        r["rs_live"] = (r["day_ret"] - _mktday
                        if (r.get("day_ret") is not None and _mktday is not None) else None)
        # 順/逆市:個股30分方向 vs 市場30分方向(描述性脈絡,非訊號——市場是最強控制變數)
        if r.get("r30") is not None and abs(r["r30"]) >= 20 and abs(mkt30) >= 5:
            same = (r["r30"] > 0) == (mkt30 > 0)
            r["mkt_ctx"] = ("順漲" if (same and mkt30 > 0)
                            else "順跌" if same
                            else "逆強" if r["r30"] > 0
                            else "逆弱")
        else:
            r["mkt_ctx"] = None
        dt = DAILY_TREND.get(r["sid"])
        r["dtrend"] = dt
    trs = []
    for r in rows:
        name = html_mod.escape(f"{r['sid']} {r['name']}")
        _hh = r.get("hold"); _hcls = ""
        if _hh:
            _hcls = "hexp" if any(f.startswith("到期") for f in _hh["flags"]) else ("hbad" if any(f.startswith("壞") for f in _hh["flags"]) else ("hexit" if _hh["flags"] else "hold"))
        _band = " class='" + " ".join(x for x in (("band" if r["sid"] in grpend else ""), _hcls) if x) + "'" if (r["sid"] in grpend or _hcls) else ""     # 產業交界粗線 + 持倉底色
        _cc = r.get("chg_amt")                          # 對昨收漲跌:紅漲綠跌(台股慣例)
        _qcls = _px_class(r.get("px"), PREV_CLOSE.get(r["sid"]), r.get("chg_pct"))
        # 對昨收:只用一般紅漲綠跌字色(不要紅底白字——紅底只留給「價」欄)
        _ccls = "up" if (_cc is not None and _cc > 0) else ("dn" if (_cc is not None and _cc < 0) else "")
        if _cc is not None:
            _arrow = "▲" if _cc > 0 else ("▼" if _cc < 0 else "")
            _chgtd = f"<td class='{_ccls}'>{_arrow}{abs(_cc):g} {r['chg_pct']:+.2f}%</td>"
        else:
            _chgtd = "<td class='dim'>—</td>"
        # 個股期貨買一/賣一 拆兩欄:委託價+委託量(小字)。title 附成交價與基差%(期貨/現股−1)
        _fp = FUT_PX.get(r["sid"]) if isinstance(FUT_PX.get(r["sid"]), dict) else None
        _futpx = _fp.get("px") if _fp else None
        _bastxt = ""
        if _futpx and r.get("px"):
            _bastxt = f" · 期貨成交{_futpx:g} 基差{(_futpx / r['px'] - 1) * 100:+.1f}%"
        _fbid = _fp.get("bid") if _fp else None
        _fask = _fp.get("ask") if _fp else None
        _ffpc = _fp.get("fpc") if _fp else None         # 期貨自身昨結:算期貨漲跌停(紅底/綠底)
        # 著色:有期貨昨結→用 _px_class(漲停紅底白字/跌停綠底白字/一般紅漲綠跌);
        # 無昨結時退回買紅賣綠(維持買賣側可辨)。
        _fbcls = (_px_class(_fbid, _ffpc, (_fbid / _ffpc - 1) * 100) if (_fbid and _ffpc)
                  else ("up" if _fbid is not None else ""))
        _facls = (_px_class(_fask, _ffpc, (_fask / _ffpc - 1) * 100) if (_fask and _ffpc)
                  else ("dn" if _fask is not None else ""))
        # 成交價落在買一或賣一的那一邊:該側報價加黃底線(=主動方 price==ask主動買 / price==bid主動賣)
        _hitb = _futpx is not None and _fbid is not None and abs(_futpx - _fbid) < 1e-6
        _hita = _futpx is not None and _fask is not None and abs(_futpx - _fask) < 1e-6
        if _fbid is not None:
            _bp = f"<span class='hit'>{_fbid:g}</span>" if _hitb else f"{_fbid:g}"
            _fbtd = (f"<td class='{_fbcls}' title='期貨買一{_bastxt}'>{_bp}"
                     f"<span class='dim' style='font-size:9px'>×{_fp.get('bidsz') or 0}{'小' if r['sid'] in FUT_MINI else ''}</span></td>")
        else:
            _fbtd = "<td class='dim'>—</td>"
        if _fask is not None:
            _ap = f"<span class='hit'>{_fask:g}</span>" if _hita else f"{_fask:g}"
            _fatd = (f"<td class='{_facls}' title='期貨賣一{_bastxt}'>{_ap}"
                     f"<span class='dim' style='font-size:9px'>×{_fp.get('asksz') or 0}{'小' if r['sid'] in FUT_MINI else ''}</span></td>")
        else:
            _fatd = "<td class='dim'>—</td>"
        # 盤前試撮:08:30~09:00 無成交價時,試撮直接塞進現有欄位共用(價/對昨收/買簿/賣簿),不另立欄
        _tr = PREOPEN.get(r["sid"]); _tpc = PREV_CLOSE.get(r["sid"])
        if r["px"]:                                     # 已有成交價:正常顯示
            _pxtd = f"<td class='{_qcls}'>{r['px']}</td>"
            _bidtd, _asktd = td(r["bid_min"], "min", False), td(r["ask_min"], "min", False)
        elif _tr and _tr.get("px") is not None:         # 盤前:借 價/對昨收/買賣簿 顯示試撮(標「試」上標)
            _tpx = _tr["px"]; _tg = ((_tpx / _tpc - 1) * 100) if _tpc else None
            _sup = "<sup style='font-size:8px;color:#8b949e'>試</sup>"
            _pxtd = f"<td class='{_px_class(_tpx, _tpc, _tg)}' title='盤前試撮價'>{_tpx:g}{_sup}</td>"
            if _tg is not None:
                _chgtd = (f"<td class='{'up' if _tg > 0 else ('dn' if _tg < 0 else '')}' title='盤前試撮跳空%'>"
                          f"{_tg:+.2f}%{_sup}</td>")
            _bidtd = f"<td title='試撮買一'>{_tr.get('bid')}{_sup}</td>"
            _asktd = f"<td title='試撮賣一(撮合{_tr.get('size') or 0}張)'>{_tr.get('ask')}{_sup}</td>"
        else:                                           # 開盤前空窗/無試撮
            _pxtd = "<td class='dim'>—</td>"
            _bidtd, _asktd = td(r["bid_min"], "min", False), td(r["ask_min"], "min", False)
        # 訊號合併欄:漲訊(紅)+跌訊(綠)+注記(黃)整成一格,不同顏色分辨方向。
        # 原本『章/跌訊/漲訊/旗標』四欄內容高度重複(連3買/同賣/破昨防線/勿追/深接/純機構
        # 都跨欄出現),bull/bear_txt 已把 stamp+flag 去重整合;只需補兩個未涵蓋的注記:
        # 章的『↓弱開』、旗標的『虛胖接刀(無效帶)』。
        _sig = []
        if r["bull_n"]:
            _sig.append(f"<span class='sigup'>{r['bull_txt']}</span>")
        if r["bear_n"]:
            _sig.append(f"<span class='sigdn'>{r['bear_txt']}</span>")
        _ex = []
        if "↓弱開" in r["stamp"]:
            _ex.append("↓弱開")
        if r.get("ex_txt"):
            _ex.append(r["ex_txt"])
        elif "ex_txt" not in r and "虛胖接刀" in r["flag"]:
            _ex.append("▫虛胖接刀(枯量,超額≈0)")
        if _ex:
            _sig.append(f"<span class='warnv'>{' '.join(_ex)}</span>")
        _signum = r["bull_n"] + r["bear_n"]
        _sigtd = (f"<td style='text-align:left{';font-weight:700' if _signum >= 3 else ''}'>"
                  f"{' · '.join(_sig)}</td>" if _sig else "<td class='dim'>—</td>")
        # 權證多空 5分/30分兩欄:購/售 成交額(萬)+ 多方占比;紅=購>售 綠=售>購。描述性、未回測。
        _w = WRT.get(r["sid"]) if isinstance(WRT.get(r["sid"]), dict) else None
        if _w and (_w.get("n_call") or _w.get("n_put")):
            _wtip = (f"全日 購{(_w.get('call_day') or 0)/1e4:,.0f}萬/售{(_w.get('put_day') or 0)/1e4:,.0f}萬 · "
                     f"對映 購{_w.get('n_call', 0)}檔/售{_w.get('n_put', 0)}檔 · 更新{_w.get('t', '')}")
            _wrt5td = _wrt_td(_w.get("call_5") or 0.0, _w.get("put_5") or 0.0,
                              _w.get("bull_5"), _w.get("bear_5"), "權證5分 " + _wtip)
            _wrt30td = _wrt_td(_w.get("call_30") or 0.0, _w.get("put_30") or 0.0,
                               _w.get("bull_30"), _w.get("bear_30"), "權證30分 " + _wtip)
        else:
            _wrt5td = _wrt30td = "<td class='dim'>—</td>"
        # ---- 各欄先算成具名字串,再依「同尺度 大戶→散戶→權證」順序組列(2026-09-24 重排) ----
        c_nm = (f"<td class='nm'><a href='/stock?sid={r['sid']}' target='_blank' "
                f"title=\"{html_mod.escape(_stock_tip(r['sid']), quote=True).replace(chr(10), '&#10;')}\" "
                f"style='color:inherit;text-decoration:none'>{name}</a>"
                f"<span class='cat'>{r['cat']}</span>"
                + ("<span class='dim' style='font-size:9px' title='散戶不可測:1 張≥500 萬,散戶欄=不可測、散戶側訊號(虛拉/勿追/竭盡∧散戶接)關閉;大戶/真空/急跌/隔夜大戶佔比照常'> ✗散</span>" if r.get("unm") else "")
                + ("<span class='dim' style='font-size:9px' title='個股期貨為小型契約(100 股):期貨買/賣欄的量以小型口數計,1 口名目=價×100'> 小</span>" if r["sid"] in FUT_MINI else "")
                + (f" <span class='hbtn' data-sid='{r['sid']}' data-action='close' title='點一下=平倉(記錄損益與原因 manual)' style='cursor:pointer;color:#f85149;font-size:9px;border:1px solid #f85149;padding:0 3px'>出</span>" if r.get("hold")
                   else f" <span class='hbtn' data-sid='{r['sid']}' data-action='open' title='點一下=標記持倉(以現價為進場價,啟動持倉分監控;純提示不送單)' style='cursor:pointer;color:#58a6ff;font-size:9px;border:1px solid #30363d;padding:0 3px'>持</span>")
                + "</td>")
        c_vr = vr_td(r)
        c_amp = (f"<td class='{'warnv' if r['amp20'] >= 7 else ('dim' if r['amp20'] < 5 else '')}'>"
                 f"{r['amp20']:.1f}%</td>" if r.get("amp20") is not None else "<td class='dim'>—</td>")
        c_open = td(r["day_ret"], "pct2")
        c_r30 = td(r["r30_r"] / 100 if r["r30_r"] is not None else None, "pct2")   # 統一用 %(2026-09-24)
        c_ctx = (f"<td class='{'up' if '逆強' in r['mkt_ctx'] or '順漲' in r['mkt_ctx'] else 'dn'}' "
                 f"style='font-size:11px'>{r['mkt_ctx']}</td>" if r.get("mkt_ctx") else "<td class='dim'>—</td>")
        c_big5, c_big30, c_bigday = td(r["big5_r"], "wan"), td(r["big30_r"], "wan"), td(r["bigday"], "wan")
        c_rb30 = (f"<td class='{'warnv' if (r['rbuy30_r'] or 0) >= 5 else ''}'>{r['rbuy30_r']:.1f}%</td>"
                  if (r.get("rbuy30_r") is not None and not r["unm"]) else "<td class='dim'>—</td>")
        c_rs30 = (f"<td>{r['rsell30_r']:.1f}%</td>"
                  if (r.get("rsell30_r") is not None and not r["unm"]) else "<td class='dim'>—</td>")
        c_dsh = td(r["dsh30_r"], "bps", True, r["unm"]).replace("bps", "")
        c_w5 = td(r["w_ret_r"] / 100 if r["w_ret_r"] is not None else None, "pct2")
        c_ret5 = td(r["retn5_r"], "wan", unm=r["unm"])
        c_rb5 = (f"<td class='{'warnv' if (r['rbuy5_r'] or 0) >= 5 else ''}'>"
                 f"{r['rbuy5_r']:.1f}%</td>" if (r["rbuy5_r"] is not None and not r["unm"]) else "<td class='dim'>—</td>")
        c_rs5 = (f"<td>{r['rsell5_r']:.1f}%</td>" if (r["rsell5_r"] is not None and not r["unm"]) else "<td class='dim'>—</td>")
        c_retday = td(r["retday"], "wan", unm=r["unm"])
        # 新欄:全日大戶 − 全日散戶(萬);|差| ≥ 全日成交 5% 粗體
        # 大戶−散戶 改為 ÷ 全日成交 的 %(= 大戶佔比 − 散戶佔比);tooltip 給 A/B/C/D 判讀(2026-09-24)
        _tot = (ST.day.get(r["sid"]) or {}).get("tot") or 0
        if r.get("bigday") is not None and r.get("retday") is not None and not r["unm"] and _tot > 0:
            _bp = r["bigday"] / _tot * 100; _rp = r["retday"] / _tot * 100; _dp = _bp - _rp
            if _bp >= 10 and _rp < 5:
                _lab, _cls = "A 大戶買·散戶未主導(127日隔夜 +124,散0~5%最佳 +185/t4.5)", "up"
            elif _bp >= 10 and _rp >= 5:
                _lab, _cls = "B 大戶帶散戶(散戶≥5%,127日 +75/t1.2 n15,打折)", "warnv"
            elif _bp <= -10 and _rp >= 5:
                _lab, _cls = "C 大戶倒·散戶接刀(127日隔夜 −11,低於基準 +73 約 85bps)", "dn"
            elif _bp <= -10:
                _lab, _cls = "D 大戶賣·散戶不接(127日隔夜 −13,低於基準約 85bps)", "dn"
            else:
                _lab, _cls = "佔比未過 ±10% 門檻,不判", ""
            _bold = "font-weight:700" if abs(_dp) >= 15 else ""   # ±15% ≈ 127日 p10/p90
            c_diff = (f"<td class='{_cls or ('up' if _dp > 0 else ('dn' if _dp < 0 else ''))}' style='{_bold}' "
                      f"title='大戶佔比 {_bp:+.1f}% − 散戶佔比 {_rp:+.1f}% = {_dp:+.1f}% → {_lab}'>{_dp:+.1f}%</td>")
        else:
            c_diff = "<td class='dim'>—</td>"
        c_bigsh = (f"<td class='{'up' if r['bigsh_d'] > 0 else 'dn'}'>{r['bigsh_d']:+.1f}%</td>"
                   if r["bigsh_d"] is not None else "<td class='dim'>—</td>")
        # 壓縮顯示規則(2026-09-24 jack 定案,多空對稱、不標字):
        #   多:全日大戶佔比 ≥+10% ∧ 壓縮 <0(價壓在近1h均價下)→ 黃粗體
        #   空:全日大戶佔比 ≤−10% ∧ 壓縮 >0(大戶倒完價仍在均價上)→ 黃粗體
        #   其餘淡化。127日參考:多格 +139/t2.9;空格 −28~−80(勿抱,非放空);基準 +73。
        if r["cmp1h"] is None:
            c_cmp = "<td class='dim'>—</td>"
        else:
            _cv = r["cmp1h"]; _bs = r.get("bigsh_d")
            if _bs is not None and _bs >= 10 and _cv < 0:
                c_cmp = (f"<td class='warnv' style='font-weight:700' title='多:全日大戶佔比 {_bs:+.1f}% ≥+10 ∧ 壓縮 <0(價壓在近1h均價下)。"
                         f"127日隔夜 +139/t2.9(基準 +73)'>{_cv:+.2f}%</td>")
            elif _bs is not None and _bs <= -10 and _cv > 0:
                c_cmp = (f"<td class='warnv' style='font-weight:700' title='空:全日大戶佔比 {_bs:+.1f}% ≤−10 ∧ 壓縮 >0(大戶倒完價仍在均價上)。"
                         f"127日隔夜 −28~−80(基準 +73);勿抱,非放空訊號'>{_cv:+.2f}%</td>")
            else:
                c_cmp = f"<td class='dim' title='無方向:|大戶佔比|<10%,或方向與壓縮不對稱'>{_cv:+.2f}%</td>"
        c_dtr = (("<td class='up' style='font-size:11px'>↑多"
                  + (f" {r['dtrend']['ret5d']:+.1f}%" if r['dtrend'].get('ret5d') is not None else "")
                  + "</td>" if r['dtrend']['above_ma5']
                  else "<td class='dn' style='font-size:11px'>↓空"
                  + (f" {r['dtrend']['ret5d']:+.1f}%" if r['dtrend'].get('ret5d') is not None else "")
                  + "</td>")
                 if r.get("dtrend") else "<td class='dim'>—</td>")
        c_rs = (f"<td class='{'dn' if r['rs_live'] < 0 else ('warnv' if r['rs_live'] > 1 else '')}'>"
                f"{r['rs_live']:+.1f}</td>" if r.get("rs_live") is not None else "<td class='dim'>—</td>")
        c_rvol = (f"<td class='{'wall' if (r['rvol5'] or 0) >= 2 else ('dim' if (r['rvol5'] or 0) < 0.5 else '')}'>"
                  f"{r['rvol5']:.1f}x</td>" if r["rvol5"] is not None else "<td class='dim'>—</td>")
        c_rvd = (f"<td class='{'wall' if r['rvol_day'] >= 1.5 else ('dim' if r['rvol_day'] < 0.7 else '')}'>{r['rvol_day']:.2f}x</td>"
                 if r.get("rvol_day") is not None else "<td class='dim'>—</td>")
        # 今日振幅倍數 = (今高−今低)/昨收% ÷ 20日均振幅%;波動聚集只預測振幅不預測方向 → 不進淨分,當倍率/風控提示
        _pc = PREV_CLOSE.get(r["sid"]); _ds = ST.day.get(r["sid"]) or {}
        if _pc and _ds.get("hi") and _ds.get("lo") and r.get("amp20"):
            _a20 = r["amp20"]
            _ampt = (_ds["hi"] - _ds["lo"]) / _pc * 100; _ar = _ampt / _a20 if _a20 > 0 else None
            r["amp_ratio"] = _ar
            if _ar is None:
                c_ampr = "<td class='dim'>—</td>"
            else:
                _acls = 'warnv' if _ar >= 1.5 else ('dim' if _ar < 0.7 else '')
                _abold = 'font-weight:700' if _ar >= 1.5 else ''
                c_ampr = (f"<td class='{_acls}' style='{_abold}' title='今日振幅 {_ampt:.2f}% ÷ 20日均振幅 {_a20:.2f}% = {_ar:.2f}x。"
                          f"波動聚集:只預測明日振幅(真),方向 IC≈0 → 不進淨分;≥1.5x = 高波動日,淨分同分對應更大 bps、砍尾閾值可放寬'>{_ar:.2f}x</td>")
        else:
            r["amp_ratio"] = None
            c_ampr = "<td class='dim'>—</td>"
        trs.append(
            f"<tr{_band}>" + c_nm
            + _pxtd + _fbtd + _fatd + _chgtd + c_open + c_w5 + c_r30 + c_ctx   # ① 價(期貨買賣緊接現價)
            + c_big5 + c_ret5 + c_rb5 + c_rs5 + _wrt5td                # ② 5分:大戶→散戶→權證
            + c_big30 + c_rb30 + c_rs30 + c_dsh + _wrt30td + _mini_td(r)   # ③ 30分(+期散)
            + c_bigday + c_retday + c_diff + c_bigsh                   # ④ 全日
            + c_cmp + c_dtr + c_rs + c_rvol + c_rvd + c_vr + c_amp + c_ampr   # ⑤ 結構/隔夜(+全日量能、今日振幅倍數)
            + _sigtd + _score_td(r) + _stock_note_td(r["sid"])         # ⑥ 訊號·淨分·筆記(最末)
            + "</tr>")

    try:
        _shadow_triple(rows, now)                    # 權證三條件影子帳(失敗不影響畫面)
    except Exception as _e:
        print(f"[shadow] {_e!r}", file=sys.stderr)
    upd_note = (f"每{REFRESH_SEC}s自動更新(不重載)" if in_mkt
                else "盤後定格,已停止更新")
    try:
        _txp = _tx_panel(now)
    except Exception as _e:  # noqa: BLE001
        _txp = ""
        print(f"[tx_panel] {_e!r}", file=sys.stderr)
    PAGE["rows"] = rows
    PAGE["in_mkt"] = in_mkt
    PAGE["frag"] = f"""<div id="closed" data-closed="{0 if in_mkt else 1}" hidden></div>
{_txp}{stale_bar}
<div class="meta" hidden>更新 {now.strftime('%H:%M:%S')} · 5分窗 {win_lbl} · 30分窗 {w30_lbl} ·
市場代理 5分 <b>{mkt5:+.1f}bps</b> / 30分 <b>{mkt30:+.1f}bps</b> ·
紅=正/買 綠=負/賣 · <b>淨額單位一律=萬</b>(5分/30分/全日/權證) · <b>5分/30分欄=每秒滾動窗</b>(往回300s/1800s);訊號欄標籤仍依完成的5分桶判定(=回測定義) ·簿深≥10分=牆(紫) <3分=真空(灰) ·
散戶參與≥35%標黃 · <b>大戶=≥1000萬</b>(127日:隔夜IC+0.13/接刀+12.7/勿追賣−9.6皆過檢) · <b>主尺度=30分</b>(旗標依127日驗證:
勿追30超額−5bps/跌深大戶接+9bps/💎純機構=千萬淨買&gt;10%窗量∧前5分+前30分大戶皆淨賣∧散戶&lt;5%→+24bps cl-t5.2(兩兩交互測試定案:市場方向係死重已移除);💎💎=淨買≥3千萬→30分+29/45分+36bps;效應前5分吃69%、45分後歸零) · 5分組=執行細節 · {upd_note}</div>
<div class="flagbar" hidden>{gate_txt}<span style='color:#a5d6ff'>OOS: {_oos_summary()}</span> · {cand_txt}{flag_bar}</div>
<table><thead><tr>
<th class="stk">股票<span class="sub">點名稱看詳情</span></th>
<th title="現價,顏色為對前一交易日收盤:紅漲綠跌(台股慣例)。盤前08:30~09:00 無成交時,此欄顯示『試撮價』(帶『試』上標),09:00開盤後轉為成交價">現價</th>
<th title="個股期貨買一:委託價×委託量(小字)。紅=買方掛價側。滑鼠移上看期貨成交價與基差%。資料源:個股期貨ws books channel(斷線逾30s此欄剔除不顯示凍結價)">期貨買<span class="sub">買一價×量</span></th>
<th title="個股期貨賣一:委託價×委託量(小字)。綠=賣方掛價側。買賣一價差=期貨即時流動性;量=該價位掛單張數。資料源:個股期貨ws books channel">期貨賣<span class="sub">賣一價×量</span></th>
<th title="對前一交易日收盤的漲跌金額與%(專業看盤主報價)。盤前08:30~09:00 無成交時,此欄顯示『試撮跳空%』(帶『試』上標)">漲跌<span class="sub">對昨收</span></th>
<th title="現價/今日開盤−1(盤中相對開盤走勢,與對昨收互補)">對開盤%</th>
<th class="g5" title="近5分鐘價格報酬,單位bps。最短尺度、雜訊最大。">近5分漲跌<span class="sub">%</span></th>
<th class="g30" title="近30分鐘價格報酬,單位bps(1bps=0.01%)。主尺度。每秒滾動(現價 vs 1800秒前成交價);訊號標籤用完成5分桶版">近30分漲跌<span class="sub">%·滾動</span></th>
<th class="g30" title="個股30分方向vs市場30分方向(描述性脈絡,非訊號):順漲/順跌=同向,逆強=市場跌它漲,逆弱=市場漲它跌。市場是個股報酬最強控制變數,讀任何訊號前先看這格。門檻:個股|30分|≥20bps∧市場≥5bps才標。">順逆大盤</th>
<th class="gd" title="近5分大戶淨額(萬),每秒滾動(往回300秒)。大戶=單筆成交≥1000萬,按主動方向計正負。訊號標籤用完成5分桶版。">5分大戶<span class="sub">淨額·萬·滾動</span></th>
<th class="g5" title="5分窗散戶淨額(萬)。散戶=1張且<500萬。">5分散戶<span class="sub">淨額·萬</span></th>
<th class="g5" title="散戶買方參與(毒藥側:只買不賣格-11bps/t-4.9,>=5%標黃)">5分散買<span class="sub">參與%</span></th>
<th class="g5" title="散戶賣方參與(投降側:無資訊,less bad)">5分散賣<span class="sub">參與%</span></th>
<th class="g5" title="權證5分:該標的底下全部權證近5分。數字=認購/認售 成交額(活動量,萬);小字=簽號後『多方占比』=(主動買認購+主動賣認售)÷全部主動額;判斷與顏色同一規則:占比≥60%=偏多(紅)、≤40%=偏空(綠)、其間=中性(灰)。主動方以每筆成交價對當下買一/賣一判定(富邦 ws 逐筆)。名單=前一日成交額前600檔活躍權證。⚠描述性、尚未回測">權證5分<span class="sub">購/售·萬 (多方%) 判斷</span></th>
<th class="g30" title="近30分大戶淨額(萬),每秒滾動(往回1800秒)。大戶=單筆≥1000萬。主尺度;訊號標籤用完成5分桶版。">30分大戶<span class="sub">淨額·萬·滾動</span></th>
<th class="g30" title="30分散戶買方參與(毒藥側,≥5%標黃)。散戶=1張且<500萬。">30分散買<span class="sub">參與%</span></th>
<th class="g30" title="30分散戶賣方參與(投降側,無資訊)">30分散賣<span class="sub">參與%</span></th>
<th class="g30" title="30分散戶買方參與 − 前一段參與%,即散戶參與度的變化(跳升=散戶湧入)">散戶參與Δ<span class="sub">30分</span></th>
<th class="g30" title="權證30分:近30分 認購/認售 成交額(萬),小字=簽號後多方占比,判斷:≥60%偏多(紅)/≤40%偏空(綠)/其間中性(灰)。主尺度。資料源富邦 ws 逐筆(2連線×300檔)。⚠描述性、尚未回測,不是訊號;『權證做多』看占比與判斷,不看購/售活動量">權證30分<span class="sub">購/售·萬 (多方%) 判斷</span></th>
<th class="g30" title="期散30分:個股期貨『小型契約』(100 股)單筆 1 口成交的主動買−主動賣淨額(萬),小字=1 口成交占全部小型成交%·主動買筆數/主動賣筆數。只對期貨為小型契約的高價檔顯示(大立光/健策/旺矽/台光電;現股 1 張≥500 萬散戶不可測)。⚠ 期貨市場散戶代理,與現股散戶(1 張<500 萬)是不同母體;含造市商對敲;描述性、不進分數,累 20 日後與可測檔對照">期散30分<span class="sub">小型1口淨·萬 (占比·買/賣筆)</span></th>
<th class="gd" title="全日累計大戶淨額(萬)=盤中一路累加,收盤即全日淨額;最重要,÷成交=佔比%(隔夜排序主鍵IC+0.097/t7.1)。三尺度並排看背離:短窗買∧全日仍賣=誘多">全日大戶<span class="sub">淨額·萬</span></th>
<th class="gd" title="全日累計散戶淨額(萬)。散戶=1張且<500萬。">全日散戶<span class="sub">淨額·萬</span></th>
<th class="gd" title="(全日大戶淨 − 全日散戶淨)÷ 全日成交額 = 大戶佔比 − 散戶佔比(%)。搭配大戶佔比讀:A 佔比≥+10%∧散戶<5% = 大戶買散戶未主導(紅;127日隔夜+124,散戶0~5%最佳+185/t4.5) · B 佔比≥+10%∧散戶≥5% = 大戶帶散戶(黃,+75/t1.2) · C 佔比≤−10%∧散戶≥5% = 大戶倒散戶接刀(綠,−11) · D 佔比≤−10% = 大戶賣散戶不接(綠,−13;全體基準+73)。門檻:佔比±10%≈p12/p85,差±15%≈p10/p90(粗體)。127日檢定:此欄控大戶佔比後無獨立方向資訊,排序仍用大戶佔比。">大戶−散戶<span class="sub">÷成交% · A/B/C/D</span></th>
<th class="gd" title="當日大戶淨流÷成交金額=隔夜排序主鍵(IC+0.097/t7.1)">大戶佔比<span class="sub">÷成交%</span></th>
<th title="壓縮 =(現價 ÷ 近12個5分桶均價 − 1)%,需≥8桶;與全日大戶佔比聯合、多空對稱:黃粗體(多)= 大戶佔比≥+10% ∧ 壓縮<0(價壓著,127日隔夜 +139/t2.9);黃粗體(空)= 大戶佔比≤−10% ∧ 壓縮>0(大戶倒完價仍在均價上,−28~−80,勿抱非放空);其餘淡化。基準 +73。">壓縮<span class="sub">對1h均% × 大戶佔比</span></th>
<th class="gd" title="日線趨勢(截至最近日收盤):↑多=最新收盤站上5日均線,↓空=跌破;附5日動能%。回測:壓縮∧站上5日線隔夜+93.8bps/t5.10 vs 跌破+30/t1.65(差+63.5)——壓縮回檔在日線多頭股才是買點、空頭股是接刀。短線(壓縮/即時RS)×日線(此欄)分層,並行OOS影子帳驗證中,暫不改選股規則">日線趨勢</th>
<th title="個股日內% − 宇宙日內%(百分點):負(綠)=相對大盤壓著(彈簧),>+1(黃)=已彈開;軟否決件:日線弱∧已彈=毒格−31bps">相對強弱<span class="sub">對大盤</span></th>
<th title="5分窗成交金額 ÷ 近5日同時段中位(rvol)。≥5=爆量。">量能倍數<span class="sub">x</span></th>
<th title="全日量能 = 今日累計成交額 ÷ 同時段基準累計(近5日同時段中位加總)。127日:成交÷20日均額 控大戶佔比後隔夜 +13.8/t2.64;≥1.5x 且大戶買時淨分 +1。">全日量能<span class="sub">x</span></th>
<th title="波動風險分數(0-100)＝融資日變動幅度歷史分位 與 借券日變動幅度歷史分位 的平均(不分方向,大增大減都算)。宇宙回測:分數與隔日盤中振幅單調正相關,控制當日振幅(排除純波動群聚)後仍顯著(t3.40 p0.0007)。只預測盤中來回幅度——對隔日淨報酬/跳空/量能皆無解釋力,非方向訊號,量能反而偏低(流動性變薄)。🌊🌊=≥92分 🌊=≥86分 藍字=≥80分">波動分<span class="sub">隔日振幅預測</span></th>
<th title="高波動分數=20日日均振幅%((高−低)/收盤)。這是選股進本系統的門檻指標:宇宙中位約6.5%,越高日內波段越大、越適合大戶/散戶流策略。金字=≥7%(高波動)、灰=＜5%(偏低)。與左側『波動分數』不同:那是融資/借券變動的T-1振幅預測,這是實際已實現振幅。">振幅%<span class="sub">20日已實現</span></th>
<th title="今日振幅倍數 = (今高−今低)/昨收% ÷ 20日均振幅%。波動聚集:預測明日振幅為真、方向 IC≈0(tick排列/籌碼分數兩線驗過)→ 不投票、不進淨分;≥1.5x 黃粗=高波動日:同樣淨分對應更大 bps、急殺z 砍尾閾值可放寬、部位縮小。">今日振幅<span class="sub">÷20日均 x</span></th>
<th title="訊號合併欄(原章/跌訊/漲訊/旗標四欄整合,去重):【紅=看多】主力點火=30分大戶買≥3千萬∧散戶<45%(唯一正格) · 純機構/巨資機構=逆勢純機構買(+24~29/t5.2) · 深接=跌深大戶接RVOL≥0.5(+11~14/t3.4) · 蓄勢隔夜=全日佔比≥10%∧壓縮<0(隔夜IC t7.1) · 連3買=持續。【綠=看空】噴後過熱=30分漲≥150bps · 勿追=漲×參與跳升或大戶賣(−5~−9.6,趨勢日−32) · 機構暗退=30分大戶賣≥3千萬∧散戶<15% · 散戶虛拉=5分漲>20∧散買≥5% · 同賣=大戶賣∧散戶賣(隔夜−28/t−6) · 破昨防線@價=觸昨日午後低(−125bps/73%貫穿)。【黃=注記】↓弱開=明日弱開候選 · 虛胖接刀=枯量RVOL<0.5超額≈0(無效帶,別和深接混淆)。命中≥3整格粗體。【2026-09-24 即時制】盤中格改吃每秒滾動窗,條件連續 10 秒成立才觸發;名稱後數字=觸發後經過分鐘(粗體=≤5分最佳狀態);30分格 30 分後自動熄、5分格 5 分;✗=滾動數已反向(格失效);尾=13:00 後觸發無時距可兌現。127日基準率為完成桶版,滾動版待 15 日回放驗證">訊號<br><span style='font-size:9px;font-weight:400'>紅多綠空黃注記 · 名稱+經過分′</span></th>
<th title="淨分 = 隔夜分(收盤→明開,0/±1/±2)與 盤中分V2.5(未來60分,bps 制,|分|≤40)分開計、不相加。隔夜:大戶佔比≥+10% +2/≤−10% −2 · 大戶買∧散戶佔比≥5% −1 · 大戶買∧壓縮>+1% −1 · 大戶賣∧壓縮>+0.3% −1 · 同賣 −1 · 日線↑多 +1 · 相對強弱>+1∧日線↓空 −1 · 全日量能≥1.5x(大戶買)+1。盤中V2.3(2026-09-24,pit100×127日 IS 聯合OLS×0.7、按日聚類 t<2 歸零、OOS 未參與擬合;各項可加):散戶虛拉 −4.5 · 勿追5m −3 · 噴後過熱 ≥200/≥300/≥400 −5/−8.5/−8.5、≥600 不計 · 急跌≤−600 +40(多方唯一存活項) · 逆弱(市場30分≥+5) ≤−20/−50/−100 +1.5/+1.5/+5.5 · 純機構 +9(10:00後) · 蓄勢 −0.5~0% +4.5 / 蓄勢深 <−0.5% +5(大戶30分 5~40%;≥40% 鉅額不計) / 倒貨(0<壓縮≤0.5%∧≤−10%) −2.5 · 竭盡狀態格(近5分≤−0.2%=急跌;30秒主動賣≤40%=竭盡/≥60%=未竭):真空(竭盡∧末30秒仍跌≥10bps) +12.5 · 大戶接∧未竭 +6 · 賣壓未竭 +3 · 末30秒續跌 +2 · 竭盡∧散戶接 −5.5 · 急拉:買壓竭盡 +4.5 / 末30秒續漲 −3.5 · 權證 ±3(暫)。⚠ 單獨的「賣盤竭盡」是負的:賣壓退=反彈已發生。歸零:過熱150–200、急跌200–600、順漲/順跌/逆強、對開盤±3/±5%、散戶接跌(被狀態格吸收)、主力點火/深接/暗退/破昨。09:30 前不計、無時段係數。「峰」= 近60分最極端分與時刻(每5秒取樣的極值,偏大,只當提示)。第二行=成因標籤(下單前必看):處置(disposal_windows.csv)/跌停鎖·觸跌停(MIS 五檔 y·l·z 算跌停價)/族群k/m(同細分產業近30分同向≥2%)/MOPS hh:mm(今日,尚無即時源→顯示 MOPS?)/昨MOPS hh:mm(T-1 重大訊息,TWSE/TPEx OpenAPI 快照,每晚 fetch_mops_today.py);跟盤殺(紅=不做)/自己殺(綠=大盤止跌它還在殺,要的格)/大盤仍跌(黃=等):大盤條件是進場過濾器不計分,因為分數預測超額、你吃原始。hover 看各標籤來源與時間。OOS(07-01~08,V2.5):IC +0.072(V2.4 +0.058);|分|≥15 多 n=427 超額+38/t4.8(延遲1桶 +20/t2.7 首次顯著)、≥20 多 n=108 +78/t5.0;空 ≥15 n=792 +25/t2.5;校準斜率 1.19。覆蓋比 V2.2 少約 40 倍,多數時間為 0 = 無證據不是中性。">淨分<span class="sub">隔夜 · 盤中V2.5 bps · 峰 · 成因</span></th>
<th title="每檔自由筆記:點格子輸入,停止輸入 1.5 秒自動儲存(Ctrl/Cmd+S 立即);小字=最後編輯時間。存在資料目錄 stock_notes.json,不進 git。編輯中表格暫停更新,離開格子後恢復。">筆記<br><span style='font-size:9px;font-weight:400'>自動儲存 · 最後編輯</span></th>
</tr></thead><tbody>{''.join(trs)}</tbody></table>"""


ARC_CSS = """<style>body{background:#0d1117;color:#c9d1d9;font:13px/1.6 -apple-system,'PingFang TC',monospace;margin:10px}
tr.hold td{box-shadow:inset 3px 0 #58a6ff}tr.hexit td{background:#3a3300 !important}tr.hbad td{background:#4a1a1a !important}tr.hexp td{background:#2a2a2a !important}
table{border-collapse:collapse;white-space:nowrap}th,td{padding:2px 9px;text-align:right;border-bottom:1px solid #21262d}
th{background:#161b22;color:#8b949e;position:sticky;top:0;z-index:2;cursor:pointer;user-select:none}
td.nm{position:sticky;left:0;background:#0d1117;text-align:left;font-weight:600;color:#e6edf3;z-index:1}
th.stk{position:sticky;left:0;top:0;z-index:3}
.top5{color:#ffd700;font-weight:700}.bot5{color:#3fb950;font-weight:700}
.up{color:#ff7b72}.dn{color:#3fb950}.dim{color:#484f58}.nx{background:#161b22}
a{color:#79c0ff;text-decoration:none}h3{margin:4px 0}.meta{color:#8b949e;font-size:11px}</style>"""


SORT_JS = """<script>
(function(){
  const tb = document.querySelector('table'); if(!tb) return;
  const ths = tb.querySelectorAll('thead th');
  let cur = -1, asc = false;
  function val(td){
    const t = td.textContent.trim();
    if(t==='—'||t==='') return null;
    const n = parseFloat(t.replace(/[+%,]/g,'').replace('不可測',''));
    return isNaN(n) ? t : n;
  }
  ths.forEach((th,i)=>{ th.addEventListener('click',()=>{
    if(cur===i){ asc=!asc } else { cur=i; asc=false }
    ths.forEach(h=>h.textContent=h.textContent.replace(/[▲▼]$/,''));
    th.textContent += asc?'▲':'▼';
    const rows=[...tb.querySelectorAll('tbody tr')];
    rows.sort((a,b)=>{
      const x=val(a.children[i]), y=val(b.children[i]);
      if(x===null) return 1; if(y===null) return -1;
      if(typeof x==='string'||typeof y==='string')
        return asc ? String(x).localeCompare(String(y)) : String(y).localeCompare(String(x));
      return asc ? x-y : y-x;
    });
    const body=tb.querySelector('tbody');
    rows.forEach(r=>body.appendChild(r));
  })});
})();
</script>"""


def _day_sig_counts():
    """以儀表板旗標同款條件回放今日:每檔 💎 / 💎💎 觸發數。"""
    out = {}
    for sid, m in ST.buckets.items():
        if sid in RET_UNM:
            continue
        bks = sorted(m)
        c1 = c2 = 0
        for i in range(7, len(bks)):
            cur, prev, prior6 = bks[i], bks[i - 1], bks[i - 6:i]
            a, p = m[cur], m[prev]
            if not a["tot"] or a["big"] <= 0.10 * a["tot"]:
                continue
            if a["ret2"] / a["tot"] * 100 >= 5:
                continue
            if sum(m[b]["big"] for b in prior6) >= 0 or p["big"] >= 0:
                continue
            c1 += 1
            if a["big"] >= 3e7:
                c2 += 1
        out[sid] = (c1, c2)
    return out


def snapshot_day():
    """收盤後存當日 EOD 快照(冪等)。"""
    today = ST.date
    if not today:
        return
    f = SNAP_DIR / f"eod_{today}.json"
    if f.exists() or not ST.day:
        return
    sig = _day_sig_counts()
    rows = []
    for sid in NAMES:
        ds = ST.day.get(sid)
        px = ST.last_px.get(sid)
        if not ds or px is None:
            continue
        s1, s2 = sig.get(sid, (0, 0))
        m = ST.buckets.get(sid, {})
        pm_px = [m[bk]["px"] for bk in m if bk.hour >= 12 and m[bk]["px"]]
        rows.append({"sid": sid, "name": NAMES[sid], "close": px, "px0": ds["px0"],
                     "pm_low": min(pm_px) if len(pm_px) >= 3 else None,
                     "big": ds["big"], "ret2": ds["ret2"], "retn": ds["ret"], "tot": ds["tot"],
                     "sig1": s1, "sig2": s2})
    if len(rows) >= 20:                      # 資料太少不存(避免半天斷線垃圾)
        json.dump({"date": today, "rows": rows}, open(f, "w"))


def _snap_dates():
    return sorted(p.name[4:14] for p in SNAP_DIR.glob("eod_*.json"))


def _load_snap(d):
    try:
        return json.load(open(SNAP_DIR / f"eod_{d}.json"))
    except Exception:
        return None


def render_history():
    ds = _snap_dates()
    lis = "".join(f"<li><a href='/day?d={d}'>{d}</a></li>" for d in reversed(ds))
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=0.8'>"
            f"<title>歷史分頁</title>{ARC_CSS}</head><body>"
            f"<h3>每日收盤快照 <a href='/'>←即時</a></h3><ul>{lis or '<li>尚無</li>'}</ul>"
            f"<div class='meta'>每頁最後兩欄=次日漲跌/次日排名(次日收盤後自動補上)</div></body></html>")


def render_day(d):
    snap = _load_snap(d)
    if not snap:
        return f"<!DOCTYPE html><html><head>{ARC_CSS}</head><body>無 {html_mod.escape(d)} 快照 <a href='/history'>返回</a></body></html>"
    ds_all = _snap_dates()
    i = ds_all.index(d) if d in ds_all else -1
    prev = _load_snap(ds_all[i - 1]) if i > 0 else None
    nxt = _load_snap(ds_all[i + 1]) if 0 <= i < len(ds_all) - 1 else None
    pc = {r["sid"]: r["close"] for r in prev["rows"]} if prev else {}
    nc = {r["sid"]: r["close"] for r in nxt["rows"]} if nxt else {}
    no = {r["sid"]: r.get("px0") for r in nxt["rows"]} if nxt else {}
    rows = []
    for r in snap["rows"]:
        base = pc.get(r["sid"]) or r["px0"]
        r["dret"] = (r["close"] / base - 1) * 100 if base else None
        n = nc.get(r["sid"])
        r["nret"] = (n / r["close"] - 1) * 100 if (n and r["close"]) else None
        op = no.get(r["sid"])
        r["ngap"] = (op / r["close"] - 1) * 100 if (op and r["close"]) else None
        rows.append(r)
    rows.sort(key=lambda r: -(r["dret"] if r["dret"] is not None else -99))
    nrank = {r["sid"]: k + 1 for k, r in enumerate(
        sorted([r for r in rows if r["nret"] is not None], key=lambda r: -r["nret"]))}
    grank = {r["sid"]: k + 1 for k, r in enumerate(
        sorted([r for r in rows if r["ngap"] is not None], key=lambda r: -r["ngap"]))}
    trs = []
    for k, r in enumerate(rows, 1):
        def pct(v):
            if v is None:
                return "<td class='dim'>—</td>"
            return f"<td class='{'up' if v > 0 else 'dn' if v < 0 else ''}'>{v:+.2f}%</td>"
        unm = r["sid"] in RET_UNM
        share = (r["ret2"] / r["tot"] * 100) if (r["tot"] and not unm) else None
        trs.append(
            f"<tr><td>{k}</td><td class='nm'>{r['sid']} {r['name']}</td>"
            f"<td>{r['close']:g}</td>" + pct(r["dret"])
            + f"<td class='{'up' if r['big'] > 0 else 'dn' if r['big'] < 0 else ''}'>{r['big'] / 1e8:+.2f}</td>"
            + (f"<td class='{'up' if r['big'] > 0 else 'dn'}'>{r['big'] / r['tot'] * 100:+.1f}%</td>"
               if r['tot'] else "<td class='dim'>—</td>")
            + (f"<td>{share:.1f}%</td>" if share is not None else "<td class='dim'>不可測</td>")
            + f"<td>{r['tot'] / 1e8:.1f}</td>"
            + f"<td>{r['sig1'] or ''}</td>"
            + f"<td>{r['sig2'] or ''}</td>"
        )
        # 次日兩欄
        if r["ngap"] is None:
            trs[-1] += "<td class='nx dim'>—</td><td class='nx dim'>—</td>"
        else:
            gcl = 'up' if r['ngap'] > 0 else 'dn' if r['ngap'] < 0 else ''
            gk = grank.get(r['sid'])
            gk_cl = ('top5' if gk and gk <= 5 else
                     'bot5' if gk and gk > len(grank) - 5 else '')
            trs[-1] += (f"<td class='nx {gcl}'>{r['ngap']:+.2f}%</td>"
                        f"<td class='nx {gk_cl}'>{gk or '—'}</td>")
        if r["nret"] is None:
            trs[-1] += "<td class='nx dim'>—</td><td class='nx dim'>—</td></tr>"
        else:
            cl = 'up' if r['nret'] > 0 else 'dn' if r['nret'] < 0 else ''
            rk = nrank.get(r['sid'])
            rk_cl = ('top5' if rk and rk <= 5 else
                     'bot5' if rk and rk > len(nrank) - 5 else '')
            trs[-1] += (f"<td class='nx {cl}'>{r['nret']:+.2f}%</td>"
                        f"<td class='nx {rk_cl}'>{rk or '—'}</td></tr>")
    nav_p = f"<a href='/day?d={ds_all[i-1]}'>←{ds_all[i-1]}</a>" if i > 0 else ""
    nav_n = f"<a href='/day?d={ds_all[i+1]}'>{ds_all[i+1]}→</a>" if 0 <= i < len(ds_all) - 1 else ""
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=0.7'>"
            f"<title>{d} 收盤</title>{ARC_CSS}</head><body>"
            f"<h3>{d} 收盤快照 &nbsp;{nav_p} <a href='/history'>索引</a> {nav_n}</h3>"
            f"<div class='meta'>點欄位標題可排序(再點反向) · 預設=當日漲跌 · 漲跌基準=前一快照收盤(缺則用當日首價) · "
            f"💎欄=當日核心/強訊號觸發數 · 深底色兩欄=<b>次日</b>漲跌與排名(次日收盤自動補)</div>"
            f"<table><thead><tr><th>#</th><th class='stk'>股票</th><th>收盤</th><th>當日%</th>"
            f"<th>全日大戶(億)</th><th title='大戶淨流÷成交,127日驗證次日排名IC+0.043/t3.2=最佳排序鍵'>大戶佔比</th><th>散戶參與</th><th>成交(億)</th><th>💎</th><th>💎💎</th>"
            f"<th class='nx' title='次日開盤vs今收(隔夜跳空);127日:大戶佔比→開盤IC+0.097/t7.1=最可預測段'>次日開%</th><th class='nx'>開名</th><th class='nx' title='次日收對收;=開盤慣性−日內回吐的殘影'>次日%</th><th class='nx'>收名</th></tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table>"
            + SORT_JS + "</body></html>")


_HELP_GROUPS = [
    ("識別", [
        ("股票", "中文名＋細分產業標籤(灰字)。細分產業為宇宙標的手動策展,比大類細一層(如半導體再分晶圓代工/封測/DRAM/矽晶圓;PCB再分CCL/ABF載板/軟板)。",
         "同族群連動看輪動:CCL(聯茂/台燿)整片被買、ABF(南電/景碩/欣興)整片被賣一目了然。目前宇宙全為散戶可測標的(股價<2000),無隱藏。"),
    ]),
    ("價格", [
        ("價", "最新成交價。顏色＝對前一交易日收盤:紅漲綠跌(台股慣例,與美股相反)。", "一眼看今日相對昨收是紅是綠。"),
        ("對昨收", "現價−昨收 的金額與%,即專業看盤軟體的主報價。▲紅=漲、▼綠=跌。", "這才是一般人講的『今天漲跌多少』。金額看跳動幅度、%看比例。"),
        ("期貨買 / 期貨賣", "個股期貨近月買一/賣一,拆成兩欄:委託價＋委託量(小字×N張)。著色比照『價』欄以期貨自身昨結為基準:紅漲綠跌,期貨漲停=紅底白字、跌停=綠底白字(漲停時賣方常空→期貨賣顯示—、買一鎖在漲停價;跌停反之)。**黃底線**標在成交價落在的那一邊(成交價==賣一→主動買、==買一→主動賣),一眼看主動方在哪側。滑鼠移上 tooltip 顯示期貨成交價與基差%(期貨/現股−1,正=溢價)。資料源:個股期貨 ws books channel(五檔即時推播取第一檔);整條 ws 斷線逾30秒才剔除,鎖死檔簿不動仍保留(不再閃爍消失)。", "買一/賣一價差=期貨即時流動性(價差窄=好成交);委託量=該價位掛單張數(對照『幾分鐘成交量』判牆/真空,勿看買賣比)。黃底線那側=最近成交的主動方向。期貨先漲停/跌停常領先現股,是搶帽方向的即時線索;基差看盤前期現貨背離與盤中溢價/逆價差。"),
        ("日內%", "現價/今日開盤−1。盤中相對『開盤』的走勢,與對昨收互補。", "跳空開高後拉回:對昨收仍紅、日內%卻綠=開高走低。兩欄一起讀分辨跳空 vs 盤中動能。"),
        ("盤前試撮(共用欄)", "不另立欄:08:30~09:00 無成交時,試撮價塞進『價』欄、試撮跳空%塞『對昨收』,皆帶『試』上標;09:00開盤後自動轉為成交資料。(試撮買一/賣一改到個股詳情頁的五檔看)", "開盤前看試撮預判開盤;試撮價會被大單掛撤誘導,非確定開盤價。"),
        ("30分bps", "近30分窗價格報酬(1bps=0.01%)。主尺度。", "驗證格(噴後過熱/跌深接/勿追)判斷的價格軸。"),
        ("順逆市", "個股30分方向 vs 市場方向(=全宇宙36檔等權30分報酬,即頂端『市場代理30分』)。順漲/順跌=同向;逆強=大盤跌它漲;逆弱=大盤漲它跌。門檻:個股|30分|≥20bps ∧ 市場|30分|≥5bps 兩邊都夠力才標,否則顯示—。", "市場是個股報酬最強控制變數——先看這格:順漲/順跌多是隨大盤,逆強/逆弱才有個股獨立alpha。"),
        ("5分bps", "近5分窗價格報酬。最短尺度、雜訊最大。", "只作即時異動參考,別單獨下判斷。"),
        ("即時RS", "個股日內% − 宇宙日內%(百分點)。負(綠)=相對壓著(彈簧);＞+1(黃)=已彈開。", "隔夜挑股:壓著的彈簧優先。軟否決:日線弱∧已彈=毒格−31bps。"),
    ]),
    ("大戶三尺度(單筆≥1000萬;全系統核心)", [
        ("大戶淨額三尺度", "大戶=單筆成交≥1000萬;每筆按主動方向(價≥賣一→買、≤買一→賣)計正負,累加成淨額。單位一律=萬(5分/30分/全日)。5分/30分為每秒滾動窗;訊號標籤用完成5分桶版。", "三尺度=不同記憶長度的同一件事。"),
        ("5分大戶", "最新完成5分窗的大戶淨額(單位萬)。最短窗、最即時。", "看『現在』誰在進出;易反覆,配30分看。"),
        ("30分大戶", "近30分滾動窗大戶淨額(單位萬)。主尺度。", "驗證格的大戶軸。與價格軸(30分bps)交叉:跌×大戶買=跌深接。"),
        ("全日大戶", "開盤一路累加至今的大戶淨額,收盤即全日最終值。單位=萬元(NT$),與 5分/30分 同尺。最重要。", "紅=整天淨買、綠=淨賣。÷成交金額=佔比%(隔夜排序主鍵)。三尺度並排看背離:短窗買∧全日仍賣=誘多/出貨。"),
        ("權證5分 / 權證30分", "該標的底下的**活躍權證**(前一交易日成交額前 600 檔、對映到 36 檔標的;認購為主、認售稀少)近5分 / 近30分滾動窗。數字=認購/認售**成交額(活動量)**,單位萬——只代表多方/空方商品有多熱,**散戶倒認購也算認購成交**,不帶方向。小字=**簽號後多方占比**:每筆成交用當下買一/賣一判主動方(≥賣一=主動買、≤買一=主動賣、中間用tick rule),多方=主動買認購+主動賣認售、空方=主動賣認購+主動買認售,占比=多方÷(多方+空方)。**判斷字與顏色同一規則:≥60% 偏多(紅)、≤40% 偏空(綠)、40~60% 中性(灰);主動額 <10 萬標『量小』不判斷**(權證單價低,一筆 1 張常只有幾百元,<10 萬顯示一位小數)。資料源:富邦 ws 逐筆(2 連線×300 檔,單線上限 300),不用 MIS、不吃 REST 配額。", "⚠純描述性、**尚未回測**,不進訊號欄。**『權證做多』看小字占比與顏色,不看購/售活動量。**機制假說:散戶主動買認購→造市商賣認購並買現股避險(機械性買盤),可能與中實桶有交集;反面它也可能只是散戶追價放大鏡——方向待影子帳驗(『大戶5分≥3千萬∧散買%<5%』對照組每窗自動記錄權證欄位與5/30分/收盤結果)。"),
        ("佔比%", "全日大戶淨額 ÷ 成交金額。", "隔夜今收→明開跳空最強預測(IC+0.097/t7.1)。中市值重殺看這欄不看絕對金額(旺矽絕對−5.8億進不了榜、佔比−11%才顯眼)。"),
        ("全日散戶", "全日散戶(1張∧＜500萬)淨額(億)。", "散戶大買常是出貨對手方;參與過高(黃)=毒藥側。"),
    ]),
    ("散戶(1張∧＜500萬;買賣拆兩側)", [
        ("散買30 / 散買%", "30分 / 5分窗散戶『買方』參與(散戶買額÷窗總額)。毒藥側。", "只買不賣格−11bps/t−4.9;≥5%標黃=散戶在追,留不到收盤。"),
        ("散賣30 / 散賣%", "散戶『賣方』參與。投降側,無資訊(less bad)。", "散戶賣≠訊號;真正毒的是散戶買。"),
        ("Δ參與30", "30分散戶總參與較前一窗的變化。", "跳升=散戶剛湧入(常見於急拉尾聲)。"),
        ("5分散戶淨", "5分窗散戶淨額(萬)。", "最短窗散戶方向,雜訊大。"),
    ]),
    ("隔夜挑股因子(收盤導向,13:20後看)", [
        ("壓縮1h", "(現價 ÷ 最近12個5分桶均價 − 1)×100%,即現價相對『近1小時均價』的位置。需≥8桶才算,13:20後最有意義。負(綠)=壓在均價下=彈簧;正=已彈高。", "隔夜挑股鍵。壓縮∧站上5日線隔夜+93.8/t5.10;噴高(正值大)=甜蜜點流失。是『蓄勢隔夜』紅籤條件之一。"),
        ("日線", "取最近11個交易日收盤:↑多=最新收盤>5日均線、↓空=跌破;後面%=5日動能(最新收盤÷5交易日前−1)。截至最近日收盤,盤中不變。", "壓縮回檔在日線多頭股(↑)才是買點、空頭股(↓)是接刀。做空池要求↓;上週五三檔↑做空腿全被軋。"),
    ]),
    ("五檔委託簿(移到個股詳情頁)", [
        ("買簿 / 賣簿", "已從總表移除,改到個股詳情頁看完整五檔。判讀原則不變:委買/委賣量要換算成『幾分鐘的成交量』——＜3分=真空、＞10分=牆,絕對張數/金額當牆是錯的(欣興950誤判教訓),要除以流速,不看買賣比。", "總表擠,五檔屬於描述性、可被掛撤誘導,放詳情頁逐檔細看更合適。"),
    ]),
    ("風險/量能", [
        ("波動分數", "0–100:融資日變動幅度歷史分位＋借券日變動幅度歷史分位的平均(不分方向)。🌊🌊≥92 🌊≥86 藍字≥80。", "只預測隔日盤中『振幅』(t3.40),對淨報酬/跳空/量能無解釋力,非方向訊號。"),
        ("振幅%", "高波動分數=20日日均振幅%((高−低)/收盤),來自 calib 選股校準。金字≥7%(高波動)、灰＜5%(偏低)。與『波動分數』不同:那是融資/借券的T-1預測,這是實際已實現振幅。", "這是股票進本系統的門檻指標——宇宙中位約6.5%,越高日內波段越大、越適合大戶/散戶流策略。用來檢視/汰換成員。"),
        ("量能x", "5分窗成交金額 ÷ 近5日同時段中位(rvol)。", "≥5=爆量。三合一吸貨窗(大戶≥3千萬∧rvol≥5∧散＜25%)的量能條件。"),
    ]),
    ("訊號(合併欄:原章/跌訊/漲訊/旗標整成一格,去重,顏色分方向)", [
        ("紅=看多", "主力點火(30分大戶買≥3千萬∧散＜45%,健康首發、唯一正格;台股『抬轎』易誤解為散戶故改名)·純機構/巨資機構(逆勢純機構買單+24~29/t5.2,巨資=≥3千萬)·深接(跌深大戶接RVOL≥0.5,正EV,+11~14/t3.4)·蓄勢隔夜(全日佔比≥10%∧壓縮＜0,隔夜雙鍵IC t7.1)·連3買(持續)。", "多方計分;蓄勢隔夜是收盤導向、盤中格是條件式基準率。"),
        ("綠=看空", "噴後過熱(30分漲≥150,峰後均−32)·勿追(漲×參與跳升或大戶賣,−5~−9.6,趨勢日−32)·機構暗退(30分大戶賣≥3千萬∧散＜15%,流量領先價~2h)·散戶虛拉(5分漲＞20∧散買≥5%,留不到收盤)·同賣(大戶賣∧散戶賣,隔夜−28/t−6)·破昨防線@價(觸昨日午後低,−125bps/73%貫穿)。", "空方計分;每格都附127日基準率。"),
        ("黃=注記", "↓弱開=明日弱開候選 · ▫虛胖接刀=枯量RVOL＜0.5、超額≈0的無效帶。", "⚠『深接』(正EV,紅)與『虛胖接刀』(無效,黃)語意近但一好一壞,別搞混。"),
        ("整格粗體", "同一列命中的多方+空方訊號合計 ≥3 個時整格加粗。", "多格共振,最該優先看的列。原本四欄拆開看容易漏掉『同一檔同時有多空』的背離,合併後一眼看到。"),
    ]),
]


def render_help():
    css = ("body{background:#0d1117;color:#c9d1d9;font:13px/1.7 -apple-system,'PingFang TC',"
           "sans-serif;margin:0;padding:14px 16px 40px}"
           "h2{font-size:17px;margin:2px 0 4px}h3{font-size:14px;color:#79c0ff;margin:18px 0 4px;"
           "border-bottom:1px solid #30363d;padding-bottom:3px}"
           "a{color:#79c0ff}.meta{color:#8b949e;font-size:12px;margin-bottom:10px}"
           "table{border-collapse:collapse;width:100%;max-width:1000px;margin:2px 0}"
           "td{border-bottom:1px solid #21262d;padding:5px 8px;vertical-align:top}"
           "td.c{color:#e6edf3;font-weight:600;white-space:nowrap;width:96px}"
           "td.d{color:#adbac7;width:44%}td.u{color:#8b949e}"
           ".up{color:#ff7b72}.dn{color:#3fb950}.leg{background:#161b22;border:1px solid #30363d;"
           "border-radius:6px;padding:8px 12px;margin:10px 0;font-size:12px;max-width:1000px}")
    parts = [f"<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>欄位說明</title><style>{css}</style></head><body>"
             f"<h2>📖 大戶儀表板 · 欄位說明</h2>"
             f"<div class='meta'><a href='/'>← 回儀表板</a>　每欄:定義(怎麼算) / 怎麼讀(用途)。"
             f"顏色慣例:<span class='up'>紅=漲/正值</span>、<span class='dn'>綠=跌/負值</span>(台股慣例)、灰=無資料或不可測。</div>"
             f"<div class='leg'><b>表頭顏色</b>=尺度分層:<span style='color:#e3b341'>黃=5分窗</span>·"
             f"<span style='color:#79c0ff'>藍=30分窗(主尺度)</span>·<span style='color:#d2a8ff'>紫=全日/隔夜</span>。"
             f"<b>列的每5列一條粗分隔線</b>,方便橫向對到同一檔;滑鼠移到列上會整列highlight。</div>"]
    for title, rows in _HELP_GROUPS:
        parts.append(f"<h3>{html_mod.escape(title)}</h3><table>")
        for col, dfn, howto in rows:
            parts.append(f"<tr><td class='c'>{html_mod.escape(col)}</td>"
                         f"<td class='d'>{html_mod.escape(dfn)}</td>"
                         f"<td class='u'>{html_mod.escape(howto)}</td></tr>")
        parts.append("</table>")
    parts.append("<div class='leg' style='margin-top:18px'><b>一句紀律</b>:盤中只在已驗證格觸發時喊方向、"
                 "且附數字＋基準率＋t;無觸發＝棄權。方向重倉判斷留到收盤(隔夜候選才進OOS記分)。"
                 "詳見 docs/biglot-broadcast-protocol.md。</div>")
    parts.append("</body></html>")
    return "".join(parts)


# ============================ 個股詳情頁（點名稱進入） ============================
# 逐筆分時圖 + 累計大戶/散戶淨流 + 五檔委託簿。沿用主表同一套判定
# (BIG_AMT=1000萬 / 散戶=1張<500萬 / side=主動買賣 / 空窗跳量剔除),
# 資料源=raw_{日}.jsonl(逐筆) + watchlist_books_{日}.jsonl(五檔),兩者皆有歷史,
# 故同一頁 today=即時、d=過去日=回放,完全共用。per-(sid,日) 增量快取,重繪只讀新增 bytes。
DETAIL: dict = {}
DETAIL_LOCK = threading.Lock()   # _stock_series 增量讀非執行緒安全:loop(總覽/AGG)與 HTTP(詳情頁)同時呼叫會把同一段 bytes 解析兩次(2026-09-24 大戶累計 2 倍事故)


def _stock_series(sid, day):
    """回傳 {mins:{"HH:MM":{px,vol,big,ret,tot}}, px0, last_px, big_day, ret_day, tot_day}。
    增量:今天的檔會一路長,只解析新增行;過去日解析一次後快取到 EOF。整段加鎖(見 DETAIL_LOCK)。"""
    with DETAIL_LOCK:
        return _stock_series_locked(sid, day)


def _stock_series_locked(sid, day):
    key = (sid, day)
    st = DETAIL.get(key)
    if st is None:
        st = {"off": 0, "lastvol": 0.0, "last_px": None, "last_seen": None,
              "first_done": False, "px0": None, "mins": {}}
        DETAIL[key] = st
    raw = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{day}.jsonl"
    if raw.exists():
        # bytes 大塊讀:seek 到上次 offset,一次讀進新增區段,只保留到最後一個換行(尾行
        # 未寫完下輪再讀)。bytes 子字串預篩在 C 層跑,冷啟整天(95MB)<1s,遠快於 readline。
        sidb = sid.encode()
        with open(raw, "rb") as f:
            f.seek(st["off"])
            chunk = f.read()
        nl = chunk.rfind(b"\n")
        if nl != -1:
            st["off"] += nl + 1
            for line in chunk[:nl].split(b"\n"):
                if not line or sidb not in line:          # 便宜的子字串預篩(1/36 命中)
                    continue
                _stk_trade(sid, line.decode("utf-8", "replace"), st)
    return st


def _stk_trade(sid, line, st):
    try:
        rec = json.loads(line)
    except Exception:
        return
    if rec.get("kind") != "message":
        return
    msg = rec["payload"]
    if msg.get("channel") != "trades" or msg.get("event") not in (None, "data"):
        return
    d = msg.get("data") or {}
    if str(d.get("symbol", "")) != sid:
        return
    px, vol = d.get("price"), d.get("volume")
    if px is None or vol is None or d.get("isTrial"):
        return
    px, vol = float(px), float(vol)
    dv = vol - st["lastvol"]
    st["lastvol"] = vol
    if dv <= 0 or d.get("isOpen") or d.get("isClose"):
        return
    if not st["first_done"]:
        st["first_done"] = True
        st["px0"] = px
        return
    b, a = d.get("bid"), d.get("ask")
    side = 1 if (a is not None and px >= float(a)) else (
        -1 if (b is not None and px <= float(b)) else 0)
    if side == 0:
        p = st["last_px"]
        side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
    st["last_px"] = px
    ts = rec.get("ts")
    if not ts:
        return
    t = datetime.fromisoformat(ts)
    gap = st["last_seen"] is not None and (t - st["last_seen"]).total_seconds() > GAP_SECONDS
    st["last_seen"] = t
    amt = px * dv * 1000
    m = st["mins"].setdefault(t.strftime("%H:%M"),
                              {"px": None, "vol": 0.0, "big": 0.0, "ret": 0.0, "tot": 0.0})
    m["px"] = px
    if st["px0"] is None:
        st["px0"] = px
    if gap and amt >= GAP_JUMP_AMT:                       # 空窗跳量:更新價、不計流量
        return
    m["tot"] += amt
    m["vol"] += dv
    sgn = 1 if side > 0 else (-1 if side < 0 else 0)
    if amt >= BIG_AMT:
        m["big"] += sgn * amt
    elif dv == 1 and amt < RETAIL_CAP:
        m["ret"] += sgn * amt


def _book_of(sid, day):
    """該日 sid 最後一筆五檔快照(今天用記憶體 ST.book,過去日讀檔尾)。"""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if day == today and sid in ST.book:
        return ST.book[sid]
    bf = DATA_DIR / "cache" / "watchlist_books" / f"watchlist_books_{day}.jsonl"
    if not bf.exists():
        return None
    last = None
    with open(bf) as f:
        for line in f:
            if sid not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if str(r.get("sym")) == sid:
                last = r
    return last


def _svg_detail(sid, day, st, pc):
    """單圖疊合(2026-09-24):分時價(左軸)+ 成交量(底部淡色柱)+ 累計大戶/散戶淨(右軸,0 線置中)。x 依實際時鐘 09:00–13:30。"""
    mins = st["mins"]
    if not mins:
        return "<div class='meta'>今日尚無成交(或該檔今日無資料)</div>"
    order = sorted(mins.keys())

    def _mod(hm):
        h, mm = hm.split(":")
        return int(h) * 60 + int(mm)
    x0, x1 = 540, 810                          # 09:00–13:30
    W, H, PADL, PADR, PADT, PADB = 940, 380, 52, 64, 10, 22
    plotW, plotH = W - PADL - PADR, H - PADT - PADB

    def X(hm):
        return PADL + max(0.0, min(1.0, (_mod(hm) - x0) / (x1 - x0))) * plotW
    pxs = [mins[k]["px"] for k in order if mins[k]["px"]]
    lo, hi = min(pxs), max(pxs)
    refs = [pc] if pc else []
    up = dn = None
    if pc:
        up, dn = _limits(pc)
        if up <= hi * 1.02:
            refs.append(up)
        if dn >= lo * 0.98:
            refs.append(dn)
    ylo, yhi = min([lo] + refs), max([hi] + refs)
    if yhi - ylo < 1e-9:
        yhi = ylo + 1
    pad = (yhi - ylo) * 0.06
    ylo -= pad
    yhi += pad

    def Y(v):                                  # 價格:左軸,佔整個繪圖區
        return PADT + (yhi - v) / (yhi - ylo) * plotH
    parts = [f"<svg viewBox='0 0 {W} {H}' style='width:100%;max-width:{W}px;height:auto;background:#0d1117' data-vw='{W}' data-pts='__PTS__'>"]
    # 成交量:底部 22% 高度的淡色柱(先畫,壓在最底層)
    VH = plotH * 0.22
    vmax = max((mins[k]["vol"] for k in order), default=1) or 1
    for k in order:
        v = mins[k]["vol"]
        if v <= 0:
            continue
        h = v / vmax * VH
        parts.append(f"<rect x='{X(k)-1:.1f}' y='{PADT+plotH-h:.1f}' width='2' height='{h:.1f}' fill='#3b5170' opacity='0.55'/>")
    # 累計大戶/散戶淨:右軸,0 線置中,單位萬
    cb = cr = 0.0
    cum = []
    for k in order:
        cb += mins[k]["big"]
        cr += mins[k]["ret"]
        cum.append((k, cb / 1e4, cr / 1e4))
    amax = max((max(abs(b), abs(r)) for _, b, r in cum), default=1) or 1
    fmid = PADT + plotH / 2

    def FY(wan):
        return fmid - (wan / amax) * (plotH / 2 - 8)
    parts.append(f"<line x1='{PADL}' y1='{fmid:.1f}' x2='{W-PADR}' y2='{fmid:.1f}' stroke='#30363d' stroke-width='1'/>")
    bpts = " ".join(f"{X(k):.1f},{FY(b):.1f}" for k, b, _ in cum)
    rpts = " ".join(f"{X(k):.1f},{FY(r):.1f}" for k, _, r in cum)
    parts.append(f"<polyline points='{bpts}' fill='none' stroke='#ff7b72' stroke-width='1.8' opacity='0.85'/>")
    parts.append(f"<polyline points='{rpts}' fill='none' stroke='#58a6ff' stroke-width='1.3' opacity='0.85'/>")
    wc = _wrt_cum(sid, order); wmax = 0.0
    if wc and any(abs(v) > 0 for v in wc):
        wmax = max(abs(v) for v in wc) or 1.0
        wpts = " ".join(f"{X(k):.1f},{fmid - (v / wmax) * (plotH / 2 - 8):.1f}" for k, v in zip(order, wc))
        parts.append(f"<polyline points='{wpts}' fill='none' stroke='#d2a8ff' stroke-width='1.2' opacity='0.9'/>")
        parts.append(f"<text x='{W-PADR+4}' y='{PADT+plotH-2:.1f}' fill='#d2a8ff' font-size='9'>權±{wmax/1e4:,.0f}萬</text>")
    for wan, y in ((amax, FY(amax)), (0, fmid), (-amax, FY(-amax))):
        parts.append(f"<text x='{W-PADR+4}' y='{y+3:.1f}' fill='#8b949e' font-size='10'>{wan:+,.0f}萬</text>")
    # 參考線:昨收(灰)/漲停(紅)/跌停(綠)
    for val, col, lab in [(pc, "#8b949e", "昨收" if day == datetime.now(TZ).strftime('%Y-%m-%d') else "基準"),
                          (up, "#d1242f", "漲停"), (dn, "#1a7f37", "跌停")]:
        if val and ylo <= val <= yhi:
            y = Y(val)
            parts.append(f"<line x1='{PADL}' y1='{y:.1f}' x2='{W-PADR}' y2='{y:.1f}' stroke='{col}' "
                         f"stroke-dasharray='4 3' stroke-width='1' opacity='0.7'/>")
            parts.append(f"<text x='{W-PADR-2}' y='{y-2:.1f}' fill='{col}' font-size='10' text-anchor='end'>{lab} {val:g}</text>")
    # 分時價格線(最上層)
    pts = " ".join(f"{X(k):.1f},{Y(mins[k]['px']):.1f}" for k in order if mins[k]["px"])
    parts.append(f"<polyline points='{pts}' fill='none' stroke='#e3b341' stroke-width='1.6'/>")
    for v in (yhi, (yhi + ylo) / 2, ylo):
        parts.append(f"<text x='2' y='{Y(v)+3:.1f}' fill='#e3b341' font-size='10'>{v:.1f}</text>")
    # 圖例
    parts.append(f"<text x='{PADL+4}' y='{PADT+11}' fill='#e3b341' font-size='10'>— 價(左軸)</text>")
    parts.append(f"<text x='{PADL+80}' y='{PADT+11}' fill='#ff7b72' font-size='10'>— 累計大戶淨(右軸·萬)</text>")
    parts.append(f"<text x='{PADL+210}' y='{PADT+11}' fill='#58a6ff' font-size='10'>— 累計散戶淨</text>")
    parts.append(f"<text x='{PADL+300}' y='{PADT+11}' fill='#3b5170' font-size='10'>▮ 量(底部)</text>")
    parts.append(f"<text x='{PADL+370}' y='{PADT+11}' fill='#d2a8ff' font-size='10'>— 累計權證簽號淨(自訂尺)</text>")
    for hm in ("09:00", "10:00", "11:00", "12:00", "13:00", "13:30"):
        parts.append(f"<text x='{X(hm):.1f}' y='{H-6}' fill='#8b949e' font-size='10' text-anchor='middle'>{hm}</text>")
    parts.append("</svg>")
    # hover 資料:每分鐘 [x, y(價), 時間, 價, 累計大戶萬, 累計散戶萬, 量張]
    cumd = {k: (b, r) for k, b, r in cum}
    wcd = dict(zip(order, wc)) if wc else {}
    hov = json.dumps([[round(X(k), 1), round(Y(mins[k]["px"]), 1), k, mins[k]["px"], round(cumd[k][0]), round(cumd[k][1]), int(mins[k]["vol"]), round(wcd.get(k, 0) / 1e4)]
                      for k in order if mins[k]["px"]])
    return "".join(parts).replace("__PTS__", html_mod.escape(hov, quote=True), 1)


def _book_table(bk):
    if not bk:
        return "<div class='meta'>無五檔資料</div>"
    bp, bq = bk.get("bp") or [], bk.get("bq") or []
    ap, aq = bk.get("ap") or [], bk.get("aq") or []
    rows = []
    for i in range(5):                                   # 賣五~賣一 由上而下
        j = 4 - i
        ax = f"{ap[j]:g}" if j < len(ap) and ap[j] else "—"
        aqx = f"{aq[j]:g}" if j < len(aq) and aq[j] else ""
        rows.append(f"<tr><td class='dim'>賣{j+1}</td><td class='dn'>{ax}</td><td>{aqx}</td></tr>")
    for i in range(5):                                   # 買一~買五
        bx = f"{bp[i]:g}" if i < len(bp) and bp[i] else "—"
        bqx = f"{bq[i]:g}" if i < len(bq) and bq[i] else ""
        rows.append(f"<tr><td class='dim'>買{i+1}</td><td class='up'>{bx}</td><td>{bqx}</td></tr>")
    tsline = f"<div class='meta'>五檔快照 {bk.get('t','')} · 委買/委賣量單位:張</div>"
    return (tsline + "<table class='book'><thead><tr><th></th><th>價</th><th>量(張)</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>")


def render_stock_frag(sid, day):
    st = _stock_series(sid, day)
    pc = PREV_CLOSE.get(sid) if day == datetime.now(TZ).strftime("%Y-%m-%d") else st.get("px0")
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


def _svg_mini(st, pc):
    """6×6 總覽用迷你疊圖:價(黃)+昨收虛線 + 累計大戶(紅)/散戶(藍)右軸 + 量(底部面積)。無文字、座標取整、preserveAspectRatio=none 拉滿格子。"""
    mins = st["mins"]
    if not mins:
        return "<svg viewBox='0 0 320 170' preserveAspectRatio='none' style='width:100%;height:100%'></svg>", 1, 0
    order = sorted(mins.keys())
    W, H = 320, 170

    def _mod(hm):
        h, mm = hm.split(":")
        return int(h) * 60 + int(mm)

    def X(hm):
        return max(0.0, min(1.0, (_mod(hm) - 540) / 270)) * W
    pxs = [mins[k]["px"] for k in order if mins[k]["px"]]
    lo, hi = min(pxs), max(pxs)
    if pc:
        lo, hi = min(lo, pc), max(hi, pc)
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = (hi - lo) * 0.06
    lo -= pad; hi += pad

    def Y(v):
        return 4 + (hi - v) / (hi - lo) * (H - 8)
    hov = json.dumps([[_mod(k) - 540, mins[k]["px"]] for k in order if mins[k]["px"]], separators=(",", ":"))
    parts = [f"<svg viewBox='0 0 {W} {H}' preserveAspectRatio='none' style='width:100%;height:100%;display:block' data-pts='{hov}'>"]
    vmax = max((mins[k]["vol"] for k in order), default=1) or 1
    VH = H * 0.22
    area = " ".join(f"{X(k):.0f},{H - mins[k]['vol'] / vmax * VH:.0f}" for k in order)
    if area:
        parts.append(f"<polygon points='{X(order[0]):.0f},{H} {area} {X(order[-1]):.0f},{H}' fill='#3b5170' opacity='0.45'/>")
    cb = cr = 0.0; cum = []
    for k in order:
        cb += mins[k]["big"]; cr += mins[k]["ret"]; cum.append((k, cb, cr))
    amax = max((max(abs(b), abs(r)) for _, b, r in cum), default=1) or 1
    mid = H / 2
    FY = lambda v: mid - (v / amax) * (H / 2 - 6)  # noqa: E731
    parts.append(f"<line x1='0' y1='{mid:.0f}' x2='{W}' y2='{mid:.0f}' stroke='#30363d' stroke-width='1'/>")
    parts.append("<polyline points='" + " ".join(f"{X(k):.0f},{FY(b):.0f}" for k, b, _ in cum) + "' fill='none' stroke='#ff7b72' stroke-width='1.4' opacity='0.85'/>")
    parts.append("<polyline points='" + " ".join(f"{X(k):.0f},{FY(r):.0f}" for k, _, r in cum) + "' fill='none' stroke='#58a6ff' stroke-width='1.1' opacity='0.85'/>")
    wc = _wrt_cum(st.get("sid") or "", order) if st.get("sid") else None
    wmax = 0.0
    if wc and any(abs(v) > 0 for v in wc):
        wmax = max(abs(v) for v in wc) or 1.0
        parts.append("<polyline points='" + " ".join(f"{X(k):.0f},{mid - (v / wmax) * (H / 2 - 6):.0f}" for k, v in zip(order, wc)) + "' fill='none' stroke='#d2a8ff' stroke-width='1.1' opacity='0.9'/>")
    if pc:
        parts.append(f"<line x1='0' y1='{Y(pc):.0f}' x2='{W}' y2='{Y(pc):.0f}' stroke='#8b949e' stroke-dasharray='3 3' opacity='0.7'/>")
    parts.append("<polyline points='" + " ".join(f"{X(k):.0f},{Y(mins[k]['px']):.0f}" for k in order if mins[k]["px"]) + "' fill='none' stroke='#e3b341' stroke-width='1.5'/>")
    parts.append("</svg>")
    return "".join(parts), amax, wmax


def render_grid_frag(sort="ind"):
    """36 檔 6×6 迷你圖(每 5 秒由 loop 重建快取)。sort: ind=產業鏈固定 / big=全日大戶淨 / chg=漲跌%。"""
    rows = PAGE.get("rows") or []
    if not rows:
        return "<div class='meta'>初始化中…</div>"
    day = ST.date
    key = {"big": lambda r: -(r.get("bigday") or 0), "chg": lambda r: -(r.get("chg_pct") or 0)}.get(sort, lambda r: SORT_INDEX.get(r["sid"], 999))
    cells = []
    for r in sorted(rows, key=key):
        sid = r["sid"]
        st = _stock_series(sid, day)
        pc = PREV_CLOSE.get(sid)
        px = r.get("px"); chg = r.get("chg_pct")
        cls = "up" if (chg or 0) > 0 else ("dn" if (chg or 0) < 0 else "")
        pxs = f"<span class='{cls}' style='font-weight:700'>{px:g}</span> <span class='{cls}'>{chg:+.2f}%</span>" if (px and chg is not None) else "<span class='dim'>—</span>"
        bd = r.get("bigday") or 0; rd = r.get("retday") if r.get("retday") is not None else (st.get("ret_day") or 0)
        tags = (r.get("bull_txt") or "").split("·")[:1] + (r.get("bear_txt") or "").split("·")[:1]
        tagh = "".join(f"<span class='{'sigup' if i == 0 else 'sigdn'}'>{t}</span>" for i, t in enumerate(tags) if t)
        st["sid"] = sid
        svg, amax, wmax = _svg_mini(st, pc)
        cells.append(f"<a class='cell' href='/stock?sid={sid}' target='_blank'>"
                     f"<div class='ch'><b>{sid} {NAMES.get(sid, '')}</b> {pxs} · 大戶 <span class='{'up' if bd > 0 else 'dn'}'>{bd/1e4:+,.0f}</span>"
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
            wd = WRT_MIN["data"].get(r["sid"]) or {}
            for k, v in wd.items():
                if k in wrt:
                    wrt[k] += v
        cb = cr = cw = 0.0; B = []; Rr = []; Wv = []
        for k in allk:
            cb += big[k]; cr += ret[k]; cw += wrt[k]; B.append(cb); Rr.append(cr); Wv.append(cw)
        AGG.update({"mins": allk, "big": B, "ret": Rr, "wrt": Wv, "t": datetime.now(TZ).strftime("%H:%M:%S")})
    except Exception as _e:  # noqa: BLE001
        print(f"[agg] {_e!r}", file=sys.stderr)
    tx_row = ""
    try:
        _tp = _tx_panel(datetime.now(TZ))
        if _tp:
            tx_row = "<div class='txrow'>" + _tp.replace("<div id='txsrc' hidden>", "<div>", 1) + "</div>"
    except Exception as _e:  # noqa: BLE001
        print(f"[grid-tx] {_e!r}", file=sys.stderr)
    return tx_row + f"<div class='meta' style='margin:0 0 2px'>更新 {datetime.now(TZ).strftime('%H:%M:%S')} · 黃=價 · 紅=累計大戶淨 · 藍=累計散戶淨 · 紫=累計權證簽號淨 · 底=量 · 點格子開詳情</div>" + "".join(cells)


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
    name = NAMES.get(sid, sid)
    cat = SUBCAT.get(sid) or CATS.get(sid, "")
    amp = AMP20.get(sid)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
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
            f"<title>{sid} {name}</title>{ARC_CSS}"
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
            f"{_stock_info_block(sid)}"
            f"<div id='sd'>{frag}</div>{js}</body></html>")


def _stock_info_block(sid):
    """詳情頁:公司描述 + 看盤標籤 + 族群看盤核心(靜態,不含訊號)。"""
    rec = STOCK_INFO.get(sid)
    if not rec:
        return ""
    block, desc, tags = rec
    tag_html = "".join(f"<span class='tag'>{html_mod.escape(t)}</span>" for t in tags)
    return ("<div class='sinfo'>"
            f"<span class='blk'>{html_mod.escape(block)}</span>{tag_html}"
            f"<div class='desc'>{html_mod.escape(desc)}</div>"
            f"<div class='core'>族群看盤核心:{html_mod.escape(STOCK_BLOCKS.get(block, ''))}</div></div>"
            "<style>.sinfo{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 10px;"
            "margin-bottom:8px;font-size:12px;line-height:1.6}"
            ".sinfo .blk{color:#d2a8ff;font-weight:700;margin-right:8px}"
            ".sinfo .tag{display:inline-block;background:#21262d;color:#79c0ff;border-radius:4px;padding:0 6px;margin-right:4px;font-size:11px}"
            ".sinfo .desc{color:#e6edf3;margin-top:2px}.sinfo .core{color:#8b949e;font-size:11px}</style>")


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path == "/frag":
            body = PAGE["frag"].encode("utf-8")
        elif path == "/history":
            body = render_history().encode("utf-8")
        elif path == "/help":
            body = render_help().encode("utf-8")
        elif path == "/grid":
            body = GRID_SHELL.encode("utf-8")
        elif path == "/gridfrag":
            q = {k: v[0] for k, v in urllib_parse.parse_qs(qs).items()}
            body = (PAGE.get("grid") or render_grid_frag(str(q.get("sort", "ind"))[:4])).encode("utf-8") if str(q.get("sort", "ind")) == "ind" else render_grid_frag(str(q.get("sort", "ind"))[:4]).encode("utf-8")
        elif path == "/day":
            d = qs.split("d=")[-1][:10] if "d=" in qs else ""
            body = render_day(d).encode("utf-8")
        elif path in ("/stock", "/stockfrag"):
            q = {k: v[0] for k, v in urllib_parse.parse_qs(qs).items()}
            sid = str(q.get("sid", ""))[:8]
            day = str(q.get("d", ""))[:10] or datetime.now(TZ).strftime("%Y-%m-%d")
            if sid not in NAMES:
                body = "<div class='meta'>未知代碼</div>".encode("utf-8")
            elif path == "/stockfrag":
                body = render_stock_frag(sid, day).encode("utf-8")
            else:
                body = render_stock(sid, day).encode("utf-8")
        else:
            body = SHELL.replace("{NOTES}", _load_notes()).encode("utf-8")   # SHELL 是 f-string,{{NOTES}} 已成 {NOTES}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 手機瀏覽器(尤其 Safari over Tailscale)會積極快取整份 HTML,
        # 導致看到舊紀律條+卡在「載入中…」。強制不快取。
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """/notes:儲存頁首可編輯筆記(本機/Tailscale 私網用,內容原樣存 HTML,不做權限控制)。"""
        path, _, _ = self.path.partition("?")
        if path == "/stocknote":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                q = json.loads(self.rfile.read(n).decode("utf-8", "replace")[:10_000])
                sid = str(q.get("sid", ""))[:8]
                if sid not in NAMES:
                    raise ValueError("unknown sid")
                t = _save_stock_note(sid, str(q.get("txt", "")))
                body, code = json.dumps({"ok": True, "t": t}).encode(), 200
            except Exception as exc:  # noqa: BLE001
                body, code = f"err {exc!r}".encode(), 500
        elif path == "/hold":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                q = json.loads(self.rfile.read(n).decode("utf-8", "replace")[:1000])
                sid = str(q.get("sid", ""))[:8]; act = str(q.get("action", ""))
                if sid not in NAMES or act not in ("open", "close"):
                    raise ValueError("bad sid/action")
                _hold_toggle(sid, act, ST.last_px.get(sid))
                body, code = json.dumps({"ok": True, "holds": list(HOLDS)}).encode(), 200
            except Exception as exc:  # noqa: BLE001
                body, code = f"err {exc!r}".encode(), 500
        elif path == "/notes":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8", "replace")[:200_000]
                NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
                NOTES_PATH.write_text(raw, encoding="utf-8")
                body = b"ok"
                code = 200
            except Exception as exc:  # noqa: BLE001
                body, code = f"err {exc!r}".encode(), 500
        else:
            body, code = b"not found", 404
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _in_market():
    n = datetime.now(TZ)
    # 08:30 起 = 期貨/現貨盤前試撮(2026-09-24:launchd 也提前到 08:30),試撮價要即時跳動
    return n.weekday() < 5 and "08:30" <= n.strftime("%H:%M") <= "13:35"


def loop():
    done_close = False
    while True:
        try:
            if _in_market():
                ingest()
                render()
                done_close = False
                if time.time() - PAGE.get("grid_t", 0) >= 5:
                    try:
                        PAGE["grid"] = render_grid_frag("ind")
                    except Exception as _ge:  # noqa: BLE001
                        print(f"[grid] {_ge!r}", file=sys.stderr)
                    PAGE["grid_t"] = time.time()
            elif not done_close:
                ingest()          # 收盤後補跑一次定格,之後停工
                render()
                try:              # 盤後定格也要有 36 檔加總(台指面板紅/藍/紫線)與總覽快取:先建 AGG 再重繪一次
                    PAGE["grid"] = render_grid_frag("ind")
                    PAGE["grid_t"] = time.time()
                    render()
                except Exception as _ge:  # noqa: BLE001
                    print(f"[grid-close] {_ge!r}", file=sys.stderr)
                snapshot_day()
                try:
                    _oos_update_at_close()
                except Exception:
                    pass
                done_close = True
            elif _refresh_vol_risk_if_needed():
                # 盤前/盤後定格期間:tick 沒得更新,但 T-1 籌碼分數只要 DB 有新資料
                # 就該顯示,不用等開盤——重繪一次讓「盤後定格」頁面秀出當天分數
                render()
        except Exception as e:
            PAGE["frag"] = f"<div class='meta'>render error: {html_mod.escape(str(e))}</div>"
        time.sleep(REFRESH_SEC if _in_market() else 300)


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    print(f"biglot dashboard on :{PORT}")
    ThreadingHTTPServer(("", PORT), H).serve_forever()
