#!/usr/bin/env python3
"""45檔大戶-散戶即時儀表板 · 唯讀 · 自帶HTTP(:8771) · 30秒增量更新。

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
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "src")
from stock_db import DATA_DIR  # noqa: E402

TZ = timezone(timedelta(hours=8))
PORT = 8771
BIG_AMT = 5_000_000
GAP_JUMP_AMT = 500_000_000
GAP_SECONDS = 90
REFRESH_SEC = 30

CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
_cal = json.load(open(CALIB))
NAMES = {r["sid"]: r["name"] for r in _cal["universe"]}
CATS = {r["sid"]: r["cat"][:4] for r in _cal["universe"]}
RET_UNM = {r["sid"] for r in _cal["universe"] if r.get("px", 0) * 1000 >= BIG_AMT}

PAGE = {"html": "<html><body>初始化中…</body></html>"}


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
                                        "tot": 0., "big_pm": 0., "px0": None})
        self.book = {}                          # sid -> latest snapshot


ST = S()


def bucket_key(dt):
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def ingest():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if ST.date != today:
        ST.__init__()
        ST.date = today
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
    if ds["px0"] is None:
        ds["px0"] = px
    if gap and amt >= GAP_JUMP_AMT:
        return
    row["tot"] += amt
    row["vol"] += dv
    row["pxvol"] += px * dv
    ds["tot"] += amt
    sgn = 1 if side > 0 else (-1 if side < 0 else 0)
    if amt >= BIG_AMT:
        row["big"] += sgn * amt
        ds["big"] += sgn * amt
        if t.hour >= 12:
            ds["big_pm"] += sgn * amt
    elif dv == 1:
        row["retn"] += sgn * amt
        row["ret2"] += amt
        ds["ret"] += sgn * amt
        ds["ret2"] += amt
    else:
        row["midn"] += sgn * amt
        ds["mid"] += sgn * amt


def _fmt(v, unit=1e4, dec=0, plus=True):
    if v is None:
        return "—"
    s = f"{v/unit:+.{dec}f}" if plus else f"{v/unit:.{dec}f}"
    return s


def render():
    now = datetime.now(TZ)
    all_bks = sorted({bk for m in ST.buckets.values() for bk in m})
    done = [bk for bk in all_bks if bk + timedelta(minutes=5, seconds=10) <= now]
    cur = done[-1] if done else None
    prev = done[-2] if len(done) >= 2 else None
    win6 = done[-6:] if done else []
    win6p = done[-12:-6] if len(done) >= 7 else []
    base_bk = max((bk for bk in all_bks if bk < win6[0]), default=None) if win6 else None

    rows, rets5, rets30 = [], [], []
    for sid in NAMES:
        m = ST.buckets.get(sid, {})
        ds = ST.day.get(sid)
        a = m.get(cur) if cur else None
        p = m.get(prev) if prev else None
        r = {"sid": sid, "name": NAMES[sid], "cat": CATS.get(sid, ""),
             "unm": sid in RET_UNM}
        # 5分窗
        r["w_ret"] = (a["px"] / p["px"] - 1) * 10000 if (a and p and a["px"] and p["px"]) else None
        if r["w_ret"] is not None:
            rets5.append(r["w_ret"])
        r["big5"] = a["big"] if a else None
        r["big5p"] = p["big"] if p else None
        r["tot5"] = a["tot"] if a else None
        r["tot5p"] = p["tot"] if p else None
        r["retn5"] = a["retn"] if a else None
        share = a["ret2"] / a["tot"] * 100 if (a and a["tot"]) else None
        share_p = p["ret2"] / p["tot"] * 100 if (p and p["tot"]) else None
        r["share5"] = share
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
            r["share30"] = (sum(m[bk]["ret2"] for bk in win6 if bk in m) / t30 * 100
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
            r["tot30"] = r["big30p"] = r["dsh30"] = None
        # 全日
        last_price = ST.last_px.get(sid)
        r["px"] = last_price
        r["day_ret"] = ((last_price / ds["px0"] - 1) * 100
                        if (ds and ds["px0"] and last_price) else None)
        r["bigday"] = ds["big"] if ds else None
        r["retday"] = ds["ret"] if ds else None
        r["bigpm"] = ds["big_pm"] if ds else None
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
        rows.append(r)

    mkt5 = sum(rets5) / len(rets5) if rets5 else 0.0
    mkt30 = sum(rets30) / len(rets30) if rets30 else 0.0

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

    rows.sort(key=lambda r: -(r["big30"] or 0))
    win_lbl = (f"{cur.strftime('%H:%M')}–{(cur+timedelta(minutes=5)).strftime('%H:%M')}"
               if cur else "—")
    w30_lbl = (f"{win6[0].strftime('%H:%M')}–{(win6[-1]+timedelta(minutes=5)).strftime('%H:%M')}"
               if win6 else "—")

    # 旗標速覽 + 資料延遲警示
    fl_chase = " ".join(f"{r['sid']}{r['name']}" for r in rows if r["flag"] == "⚠勿追30")
    fl_catch = " ".join(f"{r['sid']}{r['name']}" for r in rows
                        if r["flag"] == "🟢跌深大戶接")
    flag_bar = ""
    if fl_chase:
        flag_bar += f"<span class='warnv'>⚠勿追30(漲窗×參與跳升/大戶賣):</span> {fl_chase} "
    if fl_catch:
        flag_bar += f"<span style='color:#3fb950'>🟢跌深大戶接(唯一正EV格):</span> {fl_catch}"
    if not flag_bar:
        flag_bar = "<span class='dim'>本窗無旗標</span>"
    raw_path = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{ST.date}.jsonl"
    in_mkt = now.weekday() < 5 and "09:00" <= now.strftime("%H:%M") <= "13:32"
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

    trs = []
    for r in rows:
        name = html_mod.escape(f"{r['sid']} {r['name']}")
        trs.append(
            "<tr>"
            f"<td class='nm'>{name}<span class='cat'>{r['cat']}</span></td>"
            + rk_td(r["r30r"], r["d30"]) + rk_td(r["rdr"])
            + rk_td(r["r5"], r["d5"]) + rk_td(r["rh"], r["dh"])
            + f"<td>{r['px'] if r['px'] else '—'}</td>"
            + td(r["day_ret"], "pct2")
            + td(r["r30"], "bps") + td(r["big30"], "wan") + td(r["share30"], "pct", False, r["unm"])
            + td(r["dsh30"], "bps", True, r["unm"]).replace("bps", "")
            + td(r["w_ret"], "bps") + td(r["big5"], "wan") + td(r["retn5"], "wan", unm=r["unm"])
            + td(r["share5"], "pct", False, r["unm"])
            + td(r["streak"] if r["streak"] else None, "int")
            + td(r["bigday"], "yi") + td(r["bigpm"], "yi") + td(r["retday"], "yi", unm=r["unm"])
            + td(r["vwap_gap"], "bps")
            + td(r["lu_dist"], "pct2", False)
            + td(r["bid_min"], "min", False) + td(r["ask_min"], "min", False)
            + f"<td class='flag'>{r['flag']}</td>"
            "</tr>")

    PAGE["html"] = f"""<!DOCTYPE html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=0.6">
<meta http-equiv="refresh" content="{REFRESH_SEC}">
<title>大戶45檔儀表板</title><style>
body{{background:#0d1117;color:#c9d1d9;font:12px/1.5 -apple-system,'PingFang TC',monospace;margin:8px}}
h3{{margin:4px 0;font-size:14px}}
.meta{{color:#8b949e;font-size:11px;margin-bottom:6px}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}
th,td{{padding:2px 7px;text-align:right;border-bottom:1px solid #21262d}}
th{{position:sticky;top:0;background:#161b22;color:#8b949e;font-weight:600;cursor:default}}
th.g5{{color:#e3b341}} th.g30{{color:#79c0ff}} th.gd{{color:#d2a8ff}}
td.nm{{text-align:left;font-weight:600;color:#e6edf3}}
.cat{{color:#8b949e;font-weight:400;font-size:10px;margin-left:4px}}
.up{{color:#ff7b72}} .dn{{color:#3fb950}} .dim{{color:#484f58}}
.warnv{{color:#e3b341}} .wall{{color:#d2a8ff;font-weight:700}}
.flag{{color:#e3b341;text-align:left}}
.rk1{{color:#ffd700;font-weight:700}} .rkN{{color:#3fb950;font-weight:700}}
.flagbar{{padding:3px 8px;font-size:12px;background:#161b22;margin-bottom:4px}}
</style></head><body>
<h3>大戶-散戶 45檔即時儀表板</h3>
{stale_bar}
<div class="meta">更新 {now.strftime('%H:%M:%S')} · 5分窗 {win_lbl} · 30分窗 {w30_lbl} ·
市場代理 5分 <b>{mkt5:+.1f}bps</b> / 30分 <b>{mkt30:+.1f}bps</b> ·
紅=正/買 綠=負/賣 · 淨流單位:5分=萬、全日=億 · 簿深≥10分=牆(紫) <3分=真空(灰) ·
散戶參與≥35%標黃 · 排名=注意力分流非訊號 · <b>主尺度=30分</b>(旗標依127日驗證:
勿追30超額−5bps/跌深大戶接+9bps) · 5分組=執行細節 · 每{REFRESH_SEC}s自動更新</div>
<div class="flagbar">{flag_bar}</div>
<table><thead><tr>
<th>股票</th>
<th title="30分大戶淨流排名(主尺度)">R30</th><th title="全日大戶淨流排名">R日</th>
<th title="5分大戶淨流排名">R5</th><th title="5分成交金額排名">R熱</th>
<th>價</th><th>日內%</th>
<th class="g30">30分bps</th><th class="g30">30分大戶</th><th class="g30">參與%</th>
<th class="g30">Δ參與30</th>
<th class="g5">5分bps</th><th class="g5">5分大戶</th><th class="g5">5分散戶淨</th>
<th class="g5">參與%</th><th class="g5">連續窗</th>
<th class="gd">全日大戶</th><th class="gd">午後大戶</th><th class="gd">全日散戶</th>
<th>VWAP差</th><th>距漲停</th><th>買簿</th><th>賣簿</th><th>旗標</th>
</tr></thead><tbody>{''.join(trs)}</tbody></table>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE["html"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def loop():
    while True:
        try:
            ingest()
            render()
        except Exception as e:
            PAGE["html"] = f"<html><body>render error: {html_mod.escape(str(e))}</body></html>"
        time.sleep(REFRESH_SEC)


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    print(f"biglot dashboard on :{PORT}")
    ThreadingHTTPServer(("", PORT), H).serve_forever()
