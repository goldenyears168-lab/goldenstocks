"""期貨大戶／散戶儀表板（/futflow）：小型台指 MXF、微型台指 TMF，日盤＋夜盤。純展示，不進分數、不是訊號。

資料：`<狀態根>/cache/{mxf,tmf}_trades/{root}_trades_YYYY-MM-DD.jsonl`（富邦 ws 逐筆成交，兩條獨立流 FUTURE／FUTURE_AH）。
口徑（與既有儀表板期貨欄一致：散戶＝1 口、大戶＝名目 ≥ 1000 萬）：
- 一筆「單」＝同毫秒、同方向的主動成交爆發量（用 Δcum_volume 合併，不用 size 欄；富邦 ws size 會低估）。
- 散戶＝爆發量 1 口；大戶＝爆發量名目 ≥ BIG_AMT；其餘＝中單。名目＝口數 × 點值（MXF 50、TMF 10）× 成交價。
- 主動方向：成交價 ≥ 賣一＝主動買、≤ 買一＝主動賣；其餘不計方向。
- 夜盤跨午夜：15:00～24:00 在 T 檔、00:00～05:00 在 T+1 檔，累計量跨檔延續；日盤只取 08:45～13:45（濾掉殭屍重送）。
⚠ 期貨沒有身分：這裡的「大戶／散戶」是依單的大小分組，不是辨認人。1 口也可能是演算法切單。
只讀檔案、不連外、不寫檔；增量讀取（記住檔案位移），啟動時在背景載入最近幾個檔。
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import html as html_mod
import json
import os
import threading
import time

from stock_db import DATA_DIR

CACHE = DATA_DIR.parent / "cache"
ROOTS = ("MXF", "TMF")
PV = {"MXF": 50, "TMF": 10}
BIG_AMT = 10_000_000
GROUPS = ("B", "M", "R")                       # 大戶／中單／散戶
G_NAME = {"B": "大戶", "M": "中單", "R": "散戶"}
G_COLOR = {"B": "#ff7b72", "M": "#8b949e", "R": "#58a6ff"}   # 與主儀表板台指面板一致:大戶紅、散戶藍;中單用灰(紫=權證、黃=警示已被佔用)
DAY0, DAY1 = 8 * 3600 + 45 * 60, 13 * 3600 + 45 * 60
LOAD_FILES = 14                                # 啟動時載入最近幾個日曆檔
MIN_REFRESH = 1.0                              # 秒

_LOCK = threading.RLock()
_BOOKS: dict = {}
_LOADING = {"started": False, "done": False, "err": ""}


def group_of(lots: int, notional: float) -> str:
    if lots == 1:
        return "R"
    return "B" if notional >= BIG_AMT else "M"


def _new_bar(px):
    z = lambda: {g: 0 for g in GROUPS}  # noqa: E731
    return {"o": px, "h": px, "l": px, "c": px, "vol": 0, "bl": z(), "sl": z(), "bn": z(), "sn": z(), "t": 0, "amt": 0.0}


class Book:
    """單一品牌的增量讀取狀態。"""

    def __init__(self, root: str):
        self.root = root
        self.pv = PV[root]
        self.sessions: dict = {}                # (stype, T) → {minute_key: bar}
        self.prev: dict = {}                    # quote_type → 上一筆累計量
        self.offsets: dict = {}                 # 檔 → 位移
        self.seen: dict = {}                    # 檔 → set((serial,cum))
        self.open: dict = {}                    # (qt, dir) → [trade_time, lots, notional, stype, T, mkey]
        self.last_tt: dict = {}                 # qt → 最後 trade_time
        self.last_ts = ""

    @staticmethod
    def _session(qt, file_date, sec):
        if qt == "FUTURE":
            return "day", file_date
        if sec >= 12 * 3600:
            return "night", file_date
        return "night", (dt.date.fromisoformat(file_date) - dt.timedelta(days=1)).isoformat()

    @staticmethod
    def mkey(stype, sec):
        m = int(sec // 60)
        return m if (stype == "day" or m >= 720) else m + 1440

    def _flush(self, key):
        e = self.open.pop(key, None)
        if not e:
            return
        _tt, lots, notional, stype, T, mk = e
        bar = self.sessions[(stype, T)][mk]
        g = group_of(lots, notional)
        side = "bl" if key[1] > 0 else "sl"
        nside = "bn" if key[1] > 0 else "sn"
        bar[side][g] += lots
        bar[nside][g] += notional

    def feed_line(self, line, file_date, seen):
        try:
            r = json.loads(line)
        except ValueError:
            return
        if r.get("stale"):
            return
        qt, cv, px = r.get("quote_type"), r.get("cum_volume"), r.get("price")
        if qt not in ("FUTURE", "FUTURE_AH") or cv is None or px is None:
            return
        k = (r.get("serial"), cv)
        if (qt,) + k in seen:
            return
        seen.add((qt,) + k)
        ts = r["ts"]
        sec = int(ts[11:13]) * 3600 + int(ts[14:16]) * 60 + int(ts[17:19]) + int(ts[20:23]) / 1000
        if qt == "FUTURE" and not (DAY0 <= sec < DAY1):
            return
        if qt == "FUTURE_AH" and 5 * 3600 <= sec < 15 * 3600:
            return
        stype, T = self._session(qt, file_date, sec)
        mk = self.mkey(stype, sec)
        bars = self.sessions.setdefault((stype, T), {})
        p = self.prev.get(qt)
        self.prev[qt] = cv
        bar = bars.get(mk)
        if bar is None:
            bar = bars[mk] = _new_bar(px)
        bar["h"], bar["l"], bar["c"] = max(bar["h"], px), min(bar["l"], px), px
        self.last_ts = ts
        if p is None or cv <= p:
            return                                # session 第一筆／重置／重送：只當基準
        dv = cv - p
        bar["vol"] += dv
        bar["amt"] += dv * self.pv * px
        bar["t"] += 1
        b, a = r.get("bid"), r.get("ask")
        d = 1 if (a is not None and px >= a) else (-1 if (b is not None and px <= b) else 0)
        if d == 0:
            return
        tt = r.get("trade_time")
        key = (qt, d)
        e = self.open.get(key)
        if e is not None and e[0] != tt:
            self._flush(key)
            e = None
        if e is None:
            self.open[key] = [tt, dv, dv * self.pv * px, stype, T, mk]
        else:
            e[1] += dv
            e[2] += dv * self.pv * px
        self.last_tt[qt] = tt or self.last_tt.get(qt)

    def _flush_old(self):
        for key in list(self.open):
            tt = self.open[key][0]
            lt = self.last_tt.get(key[0])
            if tt is not None and lt is not None and lt - tt > 2_000_000:   # trade_time 為微秒
                self._flush(key)

    def read(self, files):
        for f in files:
            file_date = os.path.basename(f).split("_")[-1][:10]
            try:
                size = os.path.getsize(f)
            except OSError:
                continue
            off = self.offsets.get(f, 0)
            if size <= off:
                continue
            seen = self.seen.setdefault(f, set())
            with open(f, "rb") as fh:
                fh.seek(off)
                chunk = fh.read(size - off)
            nl = chunk.rfind(b"\n")
            if nl < 0:
                continue
            for line in chunk[: nl + 1].decode("utf-8", "replace").splitlines():
                self.feed_line(line, file_date, seen)
            self.offsets[f] = off + nl + 1
        self._flush_old()


def _files(root):
    fs = sorted(glob.glob(str(CACHE / f"{root.lower()}_trades" / f"{root.lower()}_trades_*.jsonl")))
    return fs


def _initial_load():
    try:
        for root in ROOTS:
            b = Book(root)
            fs = _files(root)[-LOAD_FILES:]
            with _LOCK:
                _BOOKS[root] = b
            b.read(fs)
            # 啟動時只留最後幾個檔的位移以外的 seen 集合：舊檔不會再增長，釋放記憶體
            for f in fs[:-1]:
                b.seen.pop(f, None)
        _LOADING["done"] = True
    except Exception as exc:  # noqa: BLE001
        _LOADING["err"] = repr(exc)


def _refresh():
    if not _LOADING["started"]:
        _LOADING["started"] = True
        threading.Thread(target=_initial_load, daemon=True).start()
        return
    if not _LOADING["done"]:
        return
    now = time.time()
    with _LOCK:
        if now - _LOADING.get("t", 0) < MIN_REFRESH:
            return
        _LOADING["t"] = now
        for root, b in _BOOKS.items():
            b.read(_files(root)[-2:])


# ---------------------------------------------------------------- 彙整

WAN = 1e4                                       # 金額一律以「萬元」顯示,與主儀表板一致(淨額·萬)


def _tkey(mk):
    return "%02d:%02d" % (mk // 60 % 24, mk % 60)


def _window(bars, ks, last, minutes, hi=None):
    """最後 `minutes` 分鐘(含最新一根)的各組買賣量與成交額;口徑同主頁的 5分/30分滾動。"""
    lo = last - minutes + 1
    tot = {g: {"bl": 0, "sl": 0, "bn": 0.0, "sn": 0.0} for g in GROUPS}
    amt = vol = 0
    for mk in ks:
        if mk < lo or (hi is not None and mk > hi):
            continue
        b = bars[mk]
        vol += b["vol"]; amt += b["amt"]
        for g in GROUPS:
            tot[g]["bl"] += b["bl"][g]; tot[g]["sl"] += b["sl"][g]
            tot[g]["bn"] += b["bn"][g]; tot[g]["sn"] += b["sn"][g]
    return {"tot": tot, "vol": vol, "amt": amt}


def summarize(bars: dict):
    """一個盤別的彙整:本盤累計、5分／30分滾動、累計淨額序列、價格序列。"""
    ks = sorted(bars)
    cum = {g: [] for g in GROUPS}
    run = {g: 0.0 for g in GROUPS}
    price = []
    for mk in ks:
        b = bars[mk]
        price.append((mk, b["c"]))
        for g in GROUPS:
            run[g] += (b["bn"][g] - b["sn"][g]) / WAN
            cum[g].append((mk, run[g]))
    last = ks[-1] if ks else 0
    full = _window(bars, ks, last, 10 ** 6)
    return {"ks": ks, "full": full, "w5": _window(bars, ks, last, 5), "w30": _window(bars, ks, last, 30), "cum": cum, "price": price,
            "open": bars[ks[0]]["o"] if ks else None, "close": bars[ks[-1]]["c"] if ks else None,
            "hi": max((bars[k]["h"] for k in ks), default=None), "lo": min((bars[k]["l"] for k in ks), default=None),
            "vol": full["vol"], "amt": full["amt"], "minutes": (ks[-1] - ks[0] + 1) if ks else 0}


def share_diff(w, g1="B", g2="R"):
    """主頁「大戶佔比」口徑:(大戶淨額 − 散戶淨額) ÷ 成交額,%。"""
    if not w["amt"]:
        return None
    n = lambda g: w["tot"][g]["bn"] - w["tot"][g]["sn"]  # noqa: E731
    return (n(g1) - n(g2)) / w["amt"] * 100


def market_status(now=None):
    """依時鐘判斷盤別狀態(日盤 08:45~13:45、夜盤 15:00~次日 05:00,週一至週五;不含國定假日)。"""
    now = now or dt.datetime.now()
    m = now.hour * 60 + now.minute
    wd = now.weekday()
    if wd < 5 and 8 * 60 + 45 <= m < 13 * 60 + 45:
        return "日盤進行中", True
    if (wd < 5 and m >= 15 * 60) or (wd in (1, 2, 3, 4, 5) and m < 5 * 60):
        return "夜盤進行中", True
    return "盤後定格(非交易時段)", False


def _is_active(key, now=None):
    """盤別 key=(stype, T) 此刻是否正在交易(日盤:當天 08:45~13:45;夜盤:開始日 15:00 起到次日 05:00)。"""
    now = now or dt.datetime.now()
    status, ok = market_status(now)
    if not ok:
        return False
    stype, T = key
    if stype == "day":
        return status == "日盤進行中" and T == now.strftime("%Y-%m-%d")
    start = dt.date.fromisoformat(T)
    return status == "夜盤進行中" and now.date() in (start, start + dt.timedelta(days=1)) and (now.date() == start) == (now.hour >= 12)


def _cls(n):
    return "up" if n > 0 else ("dn" if n < 0 else "dim")


def _wan(v, sign=True):
    return f"{v / WAN:+,.0f}" if sign else f"{v / WAN:,.0f}"


def _nice_step(span):
    for st in (5, 10, 15, 30, 60, 120, 180):
        if span / st <= 7:
            return st
    return 240


N_PRIOR = 10
MIN_PRIOR = 5


def _pct_rank(cur, vals):
    """cur 在 vals 中的百分位(中位秩):低於者 + 相等者的一半。"""
    if not vals:
        return None
    lo = sum(v < cur for v in vals); eq = sum(v == cur for v in vals)
    return (lo + 0.5 * eq) / len(vals) * 100


def percentiles(cur_bars, cur_key, sessions, order):
    """目前盤別的本盤累計淨額,相對『最近 N 個同類(日對日、夜對夜)、同一個開盤後經過時間』的百分位。
    只比較比目前更早的盤別;歷史盤別不足 MIN_PRIOR 個或時間不夠長者不計。回傳 {g: pct, 'D': pct, 'n': n} 或 None。"""
    ks = sorted(cur_bars)
    if not ks:
        return None
    elapsed = ks[-1] - ks[0]
    cur = _window(cur_bars, ks, ks[-1], 10 ** 6)
    cur_net = {g: (cur["tot"][g]["bn"] - cur["tot"][g]["sn"]) / WAN for g in GROUPS}
    cur_d = share_diff(cur)
    hist = {g: [] for g in GROUPS}; hist_d = []
    prior = [k for k in order if k[0] == cur_key[0] and k[1] < cur_key[1]][-N_PRIOR:]
    n = 0
    for k in prior:
        b = sessions[k]; kk = sorted(b)
        if not kk or kk[-1] - kk[0] < elapsed:          # 不完整的歷史盤別不拿來比
            continue
        lim = kk[0] + elapsed
        w = _window(b, kk, lim, 10 ** 6, hi=lim)
        n += 1
        for g in GROUPS:
            hist[g].append((w["tot"][g]["bn"] - w["tot"][g]["sn"]) / WAN)
        d = share_diff(w)
        if d is not None:
            hist_d.append(d)
    if n < MIN_PRIOR:
        return {"n": n}
    out = {g: _pct_rank(cur_net[g], hist[g]) for g in GROUPS}
    out["D"] = _pct_rank(cur_d, hist_d) if cur_d is not None else None
    out["n"] = n
    return out


def _overlay_svg(S, w=860, h=250):
    """與主頁台指面板同型:價格線 + 大戶/中單/散戶 累計淨額疊在同一張圖,各組以自己的 ±最大值正規化、0 線置中。
    hover 資料放 data-pts:[x, 時間, 價, 大戶萬, 中單萬, 散戶萬]。"""
    ks = S["ks"]
    if len(ks) < 2:
        return "<div class='meta'>尚無資料</div>"
    pad_l, pad_r, pad_t, pad_b = 52, 92, 8, 20
    x0, x1 = ks[0], ks[-1]
    sx = lambda m: pad_l + (m - x0) / (x1 - x0) * (w - pad_l - pad_r)  # noqa: E731
    prices = [p for _, p in S["price"]]
    lo, hi = min(prices), max(prices)
    if hi - lo < 1e-9:
        hi = lo + 1
    top, bot = pad_t, h - pad_b
    py = lambda v: top + (hi - v) / (hi - lo) * (bot - top)  # noqa: E731
    mid = (top + bot) / 2
    pcol = "#e6edf3"      # 價格線用中性白:大戶線是紅色(與漲同色),價格線再依漲跌上紅綠會與大戶線混淆
    o = [f"<svg viewBox='0 0 {w} {h}' width='100%' style='max-width:{w}px;display:block;background:#0d1117;border:1px solid #30363d;border-radius:6px' class='ffsvg'"]
    pts = []
    cum = S["cum"]
    pmap = dict(S["price"])
    for i, mk in enumerate(ks):
        pts.append([round(sx(mk), 1), _tkey(mk), pmap[mk], round(cum["B"][i][1]), round(cum["M"][i][1]), round(cum["R"][i][1])])
    o.append(f" data-pts='{json.dumps(pts)}'>")
    o.append(f"<line x1='{pad_l}' x2='{w - pad_r}' y1='{mid:.0f}' y2='{mid:.0f}' stroke='#30363d' stroke-dasharray='2,3'/>")
    step = _nice_step(x1 - x0)
    t = (int(x0) // step + 1) * step
    while t < x1:
        o.append(f"<text x='{sx(t):.1f}' y='{h - 5}' fill='#6e7681' font-size='10' text-anchor='middle'>{_tkey(t)}</text>")
        t += step
    o.append(f"<text x='{pad_l - 4}' y='{top + 8}' font-size='9' fill='#8b949e' text-anchor='end'>{hi:,.0f}</text>"
             f"<text x='{pad_l - 4}' y='{bot}' font-size='9' fill='#8b949e' text-anchor='end'>{lo:,.0f}</text>")
    legend, ends = [], []
    for g in ("M", "R", "B"):                              # 大戶最後畫,疊在最上層
        v = [x for _, x in cum[g]]
        mx = max(abs(x) for x in v) or 1.0
        o.append("<polyline fill='none' stroke='%s' stroke-width='1.2' opacity='0.85' points='%s'/>"
                 % (G_COLOR[g], " ".join(f"{sx(m):.1f},{mid - (x / mx) * ((bot - top) / 2 - 6):.1f}" for m, x in zip(ks, v))))
        ends.append([mid - (v[-1] / mx) * ((bot - top) / 2 - 6), G_COLOR[g], f"{G_NAME[g]} {v[-1]:+,.0f}萬"])
        legend.append(f"<span style='color:{G_COLOR[g]}'>{G_NAME[g]} {v[-1]:+,.0f}萬(尺±{mx:,.0f})</span>")
    o.append(f"<polyline fill='none' stroke='{pcol}' stroke-width='1.4' points='{' '.join(f'{sx(m):.1f},{py(p):.1f}' for m, p in S['price'])}'/>")
    o.append(f"<circle cx='{sx(ks[-1]):.1f}' cy='{py(prices[-1]):.1f}' r='2.5' fill='{pcol}'/>")
    ends.append([py(prices[-1]), pcol, f"價 {prices[-1]:,.0f}"])
    ends.sort(key=lambda e: e[0])
    for i in range(1, len(ends)):
        if ends[i][0] - ends[i - 1][0] < 13:
            ends[i][0] = ends[i - 1][0] + 13
    for y, c, txt in ends:
        o.append(f"<text x='{w - pad_r + 4}' y='{y + 4:.1f}' fill='{c}' font-size='11'>{html_mod.escape(txt)}</text>")
    o.append("</svg>")
    return ("".join(o) + "<div class='meta' style='margin-top:2px'>累計淨額(買−賣)疊在價格上;各組以自己的 ±最大值正規化、0 線置中(尺=該組最大累計淨額)。"
            + " · ".join(legend) + "</div>")


def _sess_label(stype, T):
    d = dt.date.fromisoformat(T)
    if stype == "day":
        return f"{d:%m/%d} 日盤"
    return f"{d:%m/%d} 夜盤"            # 夜盤 15:00 起、跨午夜到次日 05:00,以開始日為名


def _sess_title(stype, T):
    d = dt.date.fromisoformat(T)
    if stype == "day":
        return f"{d:%m/%d} 日盤 08:45–13:45"
    return f"{d:%m/%d} 15:00 → {d + dt.timedelta(days=1):%m/%d} 05:00 夜盤"


FUT_NAME = {"MXF": "小型台指 MXF", "TMF": "微型台指 TMF"}

_CSS_EXTRA = """
.tab{display:inline-block;padding:2px 9px;border:1px solid #30363d;border-radius:5px;margin:0 4px 4px 0;color:#c9d1d9}
.tab.on{background:#1f6feb;border-color:#1f6feb;color:#fff}
.sect{margin:12px 0 4px;font-size:13px;color:#e6edf3;font-weight:600}
.disc{font-size:11px;line-height:1.7;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 10px;margin:10px 0 6px}
.disc b{color:#e6edf3}
.wrap{overflow-x:auto}
.chip{display:inline-block;font-size:11px;padding:0 6px;border-radius:3px;margin-left:6px;vertical-align:1px}
.chip.on{background:#1a4d2e;color:#b3ffcc}.chip.off{background:#30363d;color:#c9d1d9}
th{cursor:default}
th:first-child{text-align:left}
td.g{text-align:left;font-weight:600;color:#e6edf3}
"""


def _head(extra_head=""):
    try:                                          # 與主儀表板共用同一份表格樣式(單一來源)
        import biglot_dashboard
        base = biglot_dashboard.TABLE_CSS
    except Exception:  # noqa: BLE001
        base = "body{background:#0d1117;color:#c9d1d9;font:12px/1.5 -apple-system,'PingFang TC',monospace;margin:8px}.meta{color:#8b949e;font-size:11px}table{border-collapse:collapse;width:100%;white-space:nowrap}th,td{padding:2px 7px;text-align:right;border-bottom:1px solid #21262d}th{background:#161b22;color:#8b949e}th .sub{display:block;font-size:9px;font-weight:400}.up{color:#ff7b72}.dn{color:#3fb950}.dim{color:#484f58}.warnv{color:#e3b341}a{color:#79c0ff;text-decoration:none}h3{margin:4px 0;font-size:14px}"
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=0.6'>"
            f"<title>期貨大戶−散戶</title><style>{base}{_CSS_EXTRA}</style>{extra_head}</head><body>")


def _th(label, sub="", tip=""):
    t = f" title=\"{html_mod.escape(tip, quote=True)}\"" if tip else ""
    s = f"<span class='sub'>{html_mod.escape(sub)}</span>" if sub else ""
    return f"<th{t}>{html_mod.escape(label)}{s}</th>"


def _pct_cell(v, n):
    if v is None:
        return "<td class='dim'>—</td>"
    c = "up" if v >= 80 else ("dn" if v <= 20 else "dim")
    b = "font-weight:700" if (v >= 80 or v <= 20) else ""
    return f"<td class='{c}' style='{b}' title='本盤累計淨額,在近 {n} 個同類盤別「同一個開盤後經過時間」的累計淨額中,目前高於約 {v:.0f}% 的歷史盤別。80 以上=買超罕見偏多、20 以下=罕見偏空。'>P{v:.0f}</td>"


def _summary_table(S, pct=None):
    wins = (("本盤", S["full"], "本盤累計(日盤自 08:45、夜盤自 15:00 起)"), ("5分", S["w5"], "近 5 分鐘滾動(以最新一根分鐘為終點)"), ("30分", S["w30"], "近 30 分鐘滾動"))
    o = ["<div class='wrap'><table><thead><tr>", _th("組別", "", "大戶=單筆爆發名目≥1000萬;散戶=1口;中單=其餘。「單」=同毫秒同方向主動成交爆發量,不是母單、不辨認身分")]
    for nm, _w, tip in wins:
        o.append(_th(f"{nm}淨額", "買−賣·萬", f"{tip}的主動買賣淨名目(萬元)。紅=買超、綠=賣超"))
        o.append(_th(f"{nm}淨口", "買−賣·口", f"{tip}的主動買賣淨口數"))
    o.append(_th("本盤買", "口"))
    o.append(_th("本盤賣", "口"))
    o.append(_th("成交占比", "買+賣÷總量", "本盤該組主動買賣口數 ÷ 本盤成交量。注意:不是主頁的「大戶佔比」(那是淨額差÷成交額)"))
    npr = (pct or {}).get("n", 0)
    o.append(_th("本盤百分位", f"近{npr}盤·同時點" if npr >= MIN_PRIOR else "歷史不足", "本盤累計淨額相對『最近 N 個同類盤別、同一個開盤後經過時間』的百分位(日對日、夜對夜)。歷史盤別少於 5 個時不計算"))
    o.append("</tr></thead><tbody>")
    for g in GROUPS:
        o.append(f"<tr><td class='g' style='color:{G_COLOR[g]}'>{G_NAME[g]}</td>")
        for _nm, w, _t in wins:
            t = w["tot"][g]
            n = t["bn"] - t["sn"]; nl = t["bl"] - t["sl"]
            o.append(f"<td class='{_cls(n)}'>{_wan(n)}</td><td class='{_cls(nl)}'>{nl:+,}</td>")
        t = S["full"]["tot"][g]
        share = (t["bl"] + t["sl"]) / S["vol"] * 100 if S["vol"] else 0
        o.append(f"<td class='up'>{t['bl']:,}</td><td class='dn'>{t['sl']:,}</td><td>{share:.0f}%</td>"
                 + _pct_cell((pct or {}).get(g) if npr >= MIN_PRIOR else None, npr) + "</tr>")
    o.append("<tr><td class='g'>大戶−散戶</td>")
    for _nm, w, _t in wins:
        v = share_diff(w)
        o.append(f"<td class='{_cls(v or 0)}' colspan='2' title='(大戶淨額−散戶淨額)÷成交額,與主頁「大戶−散戶」同口徑'>{'—' if v is None else f'{v:+.1f}%'}</td>")
    o.append("<td colspan='3' class='dim'>÷成交額</td>" + _pct_cell((pct or {}).get("D") if npr >= MIN_PRIOR else None, npr) + "</tr></tbody></table></div>")
    return "".join(o)


def _block_table(bars, S):
    blocks = collections.OrderedDict()
    for mk in S["ks"]:
        key = mk // 5 * 5
        b = blocks.setdefault(key, {"c": None, "vol": 0, "amt": 0.0, **{g: 0.0 for g in GROUPS}})
        b["c"] = bars[mk]["c"]; b["vol"] += bars[mk]["vol"]; b["amt"] += bars[mk]["amt"]
        for g in GROUPS:
            b[g] += bars[mk]["bn"][g] - bars[mk]["sn"][g]
    o = ["<div class='wrap'><table><thead><tr>", _th("時段", "5分"), _th("收"), _th("成交量", "口")]
    for g in GROUPS:
        o.append(_th(f"{G_NAME[g]}淨額", "買−賣·萬"))
    o.append(_th("大戶−散戶", "÷成交額%"))
    o.append("</tr></thead><tbody>")
    for key, b in reversed(list(blocks.items())):
        d = (b["B"] - b["R"]) / b["amt"] * 100 if b["amt"] else None
        cells = "".join(f"<td class='{_cls(b[g])}'>{_wan(b[g])}</td>" for g in GROUPS)
        o.append(f"<tr><td style='text-align:left'>{_tkey(key)}</td><td>{b['c']:,.0f}</td><td>{b['vol']:,}</td>{cells}"
                 f"<td class='{_cls(d or 0)}'>{'—' if d is None else f'{d:+.1f}%'}</td></tr>")
    o.append("</tbody></table></div>")
    return "".join(o)


def _history_table(sessions, order, root):
    o = ["<div class='wrap'><table><thead><tr>", _th("盤別"), _th("漲跌", "%"), _th("成交量", "口")]
    for g in GROUPS:
        o.append(_th(f"{G_NAME[g]}淨額", "買−賣·萬"))
    o.append(_th("大戶−散戶", "÷成交額%"))
    o.append("</tr></thead><tbody>")
    for k in reversed(order[-10:]):
        s = summarize(sessions[k]); c = (s["close"] / s["open"] - 1) * 100 if s["open"] else 0
        cells = "".join(f"<td class='{_cls(s['full']['tot'][g]['bn'] - s['full']['tot'][g]['sn'])}'>{_wan(s['full']['tot'][g]['bn'] - s['full']['tot'][g]['sn'])}</td>" for g in GROUPS)
        d = share_diff(s["full"])
        o.append(f"<tr><td style='text-align:left'><a href='/futflow?root={root}&s={k[0]}|{k[1]}' title='{html_mod.escape(_sess_title(*k))}'>{html_mod.escape(_sess_label(*k))}</a></td>"
                 f"<td class='{_cls(c)}'>{c:+.2f}%</td><td>{s['vol']:,}</td>{cells}<td class='{_cls(d or 0)}'>{'—' if d is None else f'{d:+.1f}%'}</td></tr>")
    o.append("</tbody></table></div>")
    return "".join(o)


def _min_vol(root):
    return 3000 if root == "TMF" else 1500


def _body(root, sel):
    """回傳 (html 片段, 是否即時更新)。片段不含 <html><head>,供整頁與每秒更新共用。"""
    root = root.upper() if root.upper() in ROOTS else "MXF"
    with _LOCK:
        book = _BOOKS.get(root)
        sessions = {k: dict(v) for k, v in book.sessions.items() if v} if book else {}
        last_ts = book.last_ts if book else ""
    keys = sorted(sessions, key=lambda k: (k[1], 0 if k[0] == "day" else 1))
    # 殘留(<20 根分鐘,例如週末檔的零星重送)不列;但最新的盤別即使剛開盤也要列,才能在開盤後立刻切過去
    order = [k for k in keys if len(sessions[k]) >= 20 or (k == keys[-1] and len(sessions[k]) >= 2 and _is_active(k))]
    if not order:
        return "<h3>期貨 大戶−散戶</h3><div class='meta'>沒有足夠資料的盤別</div>", False
    want = None
    if "|" in sel:
        s_, _, t_ = sel.partition("|")
        want = (s_, t_) if (s_, t_) in order else None
    cur = want or order[-1]
    S = summarize(sessions[cur])
    pct = percentiles(sessions[cur], cur, sessions, order)
    status, in_session = market_status()
    is_latest = cur == order[-1]
    now = dt.datetime.now()
    live = in_session and is_latest            # 最新盤別、且正在交易時段:不論是預設頁或固定網址都每秒更新
    o = []
    o.append(f"<h3>期貨 大戶−散戶 · {FUT_NAME[root]} <span class='meta'>{now:%H:%M:%S}</span>"
             f"<span class='chip {'on' if in_session else 'off'}'>{html_mod.escape(status)}</span></h3>")
    o.append("<div>" + "".join(f"<a class='tab{' on' if r == root else ''}' href='/futflow?root={r}'>{FUT_NAME[r]}</a>" for r in ROOTS) + "</div>")
    o.append("<div>" + "".join(f"<a class='tab{' on' if k == cur else ''}' href='/futflow?root={root}&s={k[0]}|{k[1]}' title='{html_mod.escape(_sess_title(*k))}'>{html_mod.escape(_sess_label(*k))}</a>"
                               for k in reversed(order[-8:])) + "</div>")
    chg = (S["close"] / S["open"] - 1) * 100 if S["open"] else 0
    flags = []
    if is_latest and in_session and last_ts:
        try:
            age = (now - dt.datetime.fromisoformat(last_ts[:19])).total_seconds()
            if age > 90:
                flags.append(f"<span class='warnv'>⚠ 最新成交已 {age:.0f} 秒前,資料可能中斷</span>")
        except ValueError:
            pass
    if S["vol"] < _min_vol(root) or S["minutes"] < 15:
        flags.append("<span class='warnv'>⚠ 樣本不足(本盤未滿 15 分鐘或成交量太少),占比會劇烈跳動</span>")
    o.append(f"<div class='meta'>{html_mod.escape(_sess_title(*cur))} · 開 {S['open']:,.0f} 高 {S['hi']:,.0f} 低 {S['lo']:,.0f} 收／現 {S['close']:,.0f} "
             f"(<span class='{_cls(chg)}'>{chg:+.2f}%</span>) · 成交 {S['vol']:,} 口 · 成交額 {S['amt'] / 1e8:,.1f} 億 {' '.join(flags)}</div>")
    o.append("<div class='sect'>本盤／5分／30分 主動買賣</div>")
    o.append(_summary_table(S, pct))
    o.append("<div class='sect'>價格與累計淨額(疊圖)</div>")
    o.append(_overlay_svg(S))
    o.append("<div class='sect'>逐 5 分鐘(新→舊)</div>")
    o.append(_block_table(sessions[cur], S))
    o.append("<div class='sect'>近期各盤別</div>")
    o.append(_history_table(sessions, order, root))
    big_lots = int(BIG_AMT / (PV[root] * (S["close"] or 47000))) + 1
    o.append("<div class='disc'><b>口徑與限制</b><br>"
             f"· 散戶＝爆發量 1 口;大戶＝名目 ≥ {BIG_AMT / 1e6:.0f} 百萬元(目前約 {FUT_NAME[root].split()[0]} {big_lots} 口以上);其餘為中單。名目＝口數 × 點值 × 成交價。<br>"
             "· 「單」＝同毫秒、同方向的主動成交爆發量,不是母單;期貨沒有分點或身分資料,這是依單量分組,<b>不是辨認人</b>,1 口也可能是演算法切單。<br>"
             "· 微型台指 1 口名目約為小型台指的 1/5,所以同一門檻下 TMF 的大戶占比偏低、中單偏高;兩個品牌的占比不可直接比較。<br>"
             "· 「大戶−散戶」＝(大戶淨額 − 散戶淨額) ÷ 成交額,與主頁同口徑;「成交占比」是買賣口數占比,兩者不同。<br>"
             "· 「百分位」＝本盤累計淨額在最近 10 個同類盤別、同一個開盤後經過時間的位置(日對日、夜對夜);歷史少於 5 個盤別時不計算。它描述『相對平常多極端』,不是方向預測。<br>"
             "· 方向以成交價對買一／賣一判定,無法判定者不計方向。日盤與夜盤是兩條獨立資料流。圖表以分鐘為單位,最新一分鐘每秒更新。<br>"
             "· <span class='warnv'>⚠</span> 純觀察展示,不進任何分數、不是證實的訊號;先前回測顯示這些分組的秒級報酬差異遠小於交易成本。</div>")
    return "".join(o), live


_JS = """<div id='fftip' style='position:fixed;display:none;z-index:9;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:3px 7px;font-size:11px;pointer-events:none'></div>
<script>(function(){
var el=document.getElementById('ffapp');
function bind(){document.querySelectorAll('svg.ffsvg').forEach(function(s){
 var rows=JSON.parse(s.getAttribute('data-pts')||'[]');
 s.onmousemove=function(e){var r=s.getBoundingClientRect(),vb=s.viewBox.baseVal,x=(e.clientX-r.left)/r.width*vb.width,b=null,d=1e9;
  rows.forEach(function(q){var k=Math.abs(q[0]-x);if(k<d){d=k;b=q}});
  var t=document.getElementById('fftip');if(!b){t.style.display='none';return}
  t.innerHTML=b[1]+' 價 '+b[2].toLocaleString()+'<br><span style="color:#ff7b72">大戶 '+b[3].toLocaleString()+'萬</span> <span style="color:#8b949e">中單 '+b[4].toLocaleString()+'萬</span> <span style="color:#58a6ff">散戶 '+b[5].toLocaleString()+'萬</span>';
  t.style.left=(e.clientX+12)+'px';t.style.top=(e.clientY+12)+'px';t.style.display='block'};
 s.onmouseleave=function(){document.getElementById('fftip').style.display='none'}})}
bind();
if(el.dataset.live!=='1')return;
async function tick(){try{var sc=[].map.call(el.querySelectorAll('.wrap'),function(w){return w.scrollLeft});
 var r=await fetch(el.dataset.u+'&_='+Date.now());if(!r.ok)return;var h=await r.text();
 el.innerHTML=h;el.querySelectorAll('.wrap').forEach(function(w,i){if(sc[i])w.scrollLeft=sc[i]});bind()}catch(e){}}
setInterval(tick,1000)})();</script>"""


def render_futflow_frag(root="MXF", sel=""):
    _refresh()
    if not _LOADING["done"]:
        return "<div class='meta'>載入中…</div>"
    return _body(root, sel)[0]


def render_futflow(root: str = "MXF", sel: str = "") -> str:
    _refresh()
    root = root.upper() if root.upper() in ROOTS else "MXF"
    if not _LOADING["done"]:
        msg = _LOADING["err"] or "載入最近幾個交易日的逐筆成交中(首次約 1 分鐘),請稍候…"
        return _head("<meta http-equiv='refresh' content='8'>") + f"<h3>期貨 大戶−散戶</h3><div class='meta'>{html_mod.escape(msg)}</div></body></html>"
    body, live = _body(root, sel)
    u = f"/futflowfrag?root={root}&s={html_mod.escape(sel, quote=True)}"
    return _head() + f"<div id='ffapp' data-live='{1 if live else 0}' data-u='{u}'>{body}</div>" + _JS + "</body></html>"
