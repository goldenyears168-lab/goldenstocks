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

import futures_flow as FF
from biglot import mtx_retail_proxy as MP
from stock_db import DATA_DIR

CACHE = DATA_DIR.parent / "cache"
ROOTS = ("MXF", "TMF", "TXF", "CDF", "QFF")
CORE_ROOTS = ("MXF", "TMF")                    # /health 的整體判斷只看這兩個(台積電期貨夜盤每分鐘只有幾口,90 秒沒成交是常態,不是故障)
LINK = "ALL"                                    # 聯動分頁的 root 參數
TSMC = ("CDF", "QFF")
PV = FF.PV
BIG_AMT = FF.BIG_AMT
GROUPS = FF.SLOTS                              # ("B","C","M","R"):bar 內固定四個槽位;各品牌只顯示自己有定義的槽位(見 futures_flow.root_slots)
# 2026-10-19:分組改為『每品牌各自』——MXF/TMF:B=1口連發、R=孤立單口、M=其餘;TXF:B=大單≥4000萬、C=1口連發、R=孤立單口、M=其餘;
# 台積電期貨維持 B=大單/M=中單/R=散戶(純名目)。名稱與顏色一律走 gname()/gcolor(),不要再寫死「大單/散戶」。


def groups(root):
    return FF.root_slots(root)


def gname(root, g):
    return FF.slot_name(root, g)


def gcolor(root, g):
    return FF.slot_color(root, g)
LOAD_FILES = 14                                # 啟動時載入最近幾個日曆檔
MIN_REFRESH = 1.0                              # 秒

CKPT_SCHEMA = 1
CKPT_DIR = CACHE / "futflow_view"            # 檢查點目錄(派生資料,可隨時刪除;刪了只是重啟時完整重建)
CKPT_EVERY = 30.0                            # 秒

_LOCK = threading.RLock()
_BOOKS: dict = {}
_LOADING = {"started": False, "done": False, "err": ""}


group_of = FF.group_of            # 規則在共用模組;此處保留名稱供頁面與測試使用


def _new_bar(px):
    z = lambda: {g: 0 for g in GROUPS}  # noqa: E731
    return {"o": px, "h": px, "l": px, "c": px, "vol": 0, "bl": z(), "sl": z(), "bn": z(), "sn": z(), "t": 0, "amt": 0.0}


class Book:
    """單一品牌的增量讀取狀態。解析與分組規則一律來自 `futures_flow`(共用模組),這裡只負責彙整成分鐘列。"""

    def __init__(self, root: str):
        self.root = root
        self.pv = PV[root]
        self.sessions: dict = {}                # (stype, T) → {minute_key: bar}
        self.offsets: dict = {}                 # 檔 → 位移
        self.stream = FF.TradeStream()
        self.merger = FF.BurstMerger(self.pv, self._on_burst, streaming=True)
        # 指數期貨需要『連發』標記(延遲確認 3~8 秒);台積電期貨純名目分組,不需要
        self.tagger = FF.StreakTagger(self._on_tagged) if root in FF.STREAK_ROOTS else None
        self.last_ts = ""

    def _on_burst(self, b):
        if self.tagger is not None:
            self.tagger.add(b)                 # 類別確定後才呼叫 _on_tagged
        else:
            self._on_tagged(b, False)

    def _on_tagged(self, b, streak):
        bar = self.sessions[(b.stype, b.T)][b.mk]
        g = FF.group_for(self.root, b.lots, b.notional_sum, streak)
        side, nside = ("bl", "bn") if b.side > 0 else ("sl", "sn")
        bar[side][g] += b.lots
        bar[nside][g] += b.notional_sum

    def flush(self):
        """把已超時的爆發與連發串結算(每輪讀檔後呼叫)。"""
        self.merger.flush_old()
        if self.tagger is not None:
            self.tagger.flush_old(self.merger.last_tt)

    def feed_line(self, line, file_date, file_key=None):
        t = self.stream.feed(line, file_date, file_key)
        if t is None:
            return
        bars = self.sessions.setdefault((t.stype, t.T), {})
        bar = bars.get(t.mk)
        if bar is None:
            bar = bars[t.mk] = _new_bar(t.px)
        bar["h"], bar["l"], bar["c"] = max(bar["h"], t.px), min(bar["l"], t.px), t.px
        self.last_ts = t.ts
        if t.dv <= 0:
            return                                # 基準列／重置／重送:只當基準
        bar["vol"] += t.dv
        bar["amt"] += t.dv * self.pv * t.px
        bar["t"] += 1
        self.merger.add(t)

    # ---- 檢查點(checkpoint):位移與彙整結果一起原子寫入,重啟後只讀新增的位元組 ----
    def to_state(self, keep_sessions: int = 24) -> dict:
        keys = sorted(self.sessions, key=lambda k: (k[1], 0 if k[0] == "day" else 1))[-keep_sessions:]
        return {"schema": CKPT_SCHEMA, "rules": FF.RULES_VERSION, "root": self.root, "offsets": self.offsets, "prev": self.stream.prev,
                "last_ts": self.last_ts, "last_tt": self.merger.last_tt,
                "open": {f"{k[0]}|{k[1]}": v for k, v in self.merger.open.items()},
                "streak": self.tagger.to_state() if self.tagger is not None else {},
                "sessions": {f"{k[0]}|{k[1]}": {str(m): bar for m, bar in self.sessions[k].items()} for k in keys}}

    @classmethod
    def from_state(cls, root: str, st: dict):
        """檢查點不合(版本、品牌、結構不符)回傳 None,呼叫端改為完整重建。"""
        try:
            if st.get("schema") != CKPT_SCHEMA or st.get("rules") != FF.RULES_VERSION or st.get("root") != root:
                return None
            b = cls(root)
            b.offsets = {k: int(v) for k, v in st["offsets"].items()}
            b.stream.prev = dict(st["prev"])
            b.last_ts = st.get("last_ts", "")
            b.merger.last_tt = dict(st.get("last_tt") or {})
            for k, v in (st.get("open") or {}).items():
                qt, side = k.split("|")
                b.merger.open[(qt, int(side))] = v
            if b.tagger is not None:
                b.tagger.load_state(st.get("streak"))
            for k, bars in st["sessions"].items():
                stype, T = k.split("|")
                b.sessions[(stype, T)] = {int(m): bar for m, bar in bars.items()}
            return b
        except (KeyError, TypeError, ValueError, AttributeError):
            return None

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
            with open(f, "rb") as fh:
                fh.seek(off)
                chunk = fh.read(size - off)
            nl = chunk.rfind(b"\n")
            if nl < 0:
                continue
            for line in chunk[: nl + 1].decode("utf-8", "replace").splitlines():
                self.feed_line(line, file_date, f)
            self.offsets[f] = off + nl + 1
        for f in files[:-1]:                    # 舊檔不會再增長,釋放去重集合(每檔約 10 萬筆)
            self.stream.drop_file(f)
        self.flush()


def _files(root):
    fs = sorted(glob.glob(str(CACHE / f"{root.lower()}_trades" / f"{root.lower()}_trades_*.jsonl")))
    return fs


def _ckpt_path(root):
    return CKPT_DIR / f"{root.lower()}.json"


def _load_ckpt(root, files):
    """讀檢查點;位移超過檔案大小(檔被截斷或換檔)就整份作廢。"""
    try:
        st = json.loads(_ckpt_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    b = Book.from_state(root, st)
    if b is None:
        return None
    for f, off in b.offsets.items():
        try:
            if os.path.exists(f) and os.path.getsize(f) < off:
                return None
        except OSError:
            return None
    return b


def _save_ckpt(book):
    try:
        CKPT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _ckpt_path(book.root).with_suffix(".tmp")
        tmp.write_text(json.dumps(book.to_state(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, _ckpt_path(book.root))
    except OSError:
        pass                                      # 存檔失敗不影響頁面


def _initial_load():
    try:
        for root in ROOTS:
            fs = _files(root)[-LOAD_FILES:]
            b = _load_ckpt(root, fs) or Book(root)
            with _LOCK:
                _BOOKS[root] = b
            b.read(fs)
            _save_ckpt(b)
        _LOADING["done"] = True
        _LOADING["ckpt_t"] = time.time()
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
        if now - _LOADING.get("ckpt_t", 0) >= CKPT_EVERY:
            _LOADING["ckpt_t"] = now
            for b in _BOOKS.values():
                _save_ckpt(b)


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


def summarize(bars: dict, root: str = "MXF"):
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
    return {"root": root, "ks": ks, "full": full, "w5": _window(bars, ks, last, 5), "w30": _window(bars, ks, last, 30), "cum": cum, "price": price,
            "open": bars[ks[0]]["o"] if ks else None, "close": bars[ks[-1]]["c"] if ks else None,
            "hi": max((bars[k]["h"] for k in ks), default=None), "lo": min((bars[k]["l"] for k in ks), default=None),
            "vol": full["vol"], "amt": full["amt"], "minutes": (ks[-1] - ks[0] + 1) if ks else 0}


def share_diff(w, g1="B", g2="R"):
    """主頁「大戶佔比」口徑:(大戶淨額 − 散戶淨額) ÷ 成交額,%。"""
    if not w["amt"]:
        return None
    n = lambda g: w["tot"][g]["bn"] - w["tot"][g]["sn"]  # noqa: E731
    return (n(g1) - n(g2)) / w["amt"] * 100


def _now():
    return dt.datetime.now()


STALE_SEC = 90


def freshness(last_ts, in_session, now=None):
    """資料新鮮度(最新資料的年齡,與管線是否成功分開):
    ('idle'|'missing'|'fresh'|'stale', 年齡秒)。非交易時段一律 idle;交易時段沒有資料=missing;超過 STALE_SEC=stale。"""
    now = now or _now()
    if not in_session:
        return "idle", None
    if not last_ts:
        return "missing", None
    try:
        age = (now - dt.datetime.fromisoformat(last_ts[:19])).total_seconds()
    except ValueError:
        return "missing", None
    return ("stale" if age > STALE_SEC else "fresh"), age


def market_status(now=None):
    """依時鐘判斷盤別狀態(日盤 08:45~13:45、夜盤 15:00~次日 05:00,週一至週五;不含國定假日)。"""
    now = now or _now()
    m = now.hour * 60 + now.minute
    wd = now.weekday()
    if wd < 5 and 8 * 60 + 45 <= m < 13 * 60 + 45:
        return "日盤進行中", True
    if (wd < 5 and m >= 15 * 60) or (wd in (1, 2, 3, 4, 5) and m < 5 * 60):
        return "夜盤進行中", True
    return "盤後定格(非交易時段)", False


def _is_active(key, now=None):
    """盤別 key=(stype, T) 此刻是否正在交易(日盤:當天 08:45~13:45;夜盤:開始日 15:00 起到次日 05:00)。"""
    now = now or _now()
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


def _adj_series(sessions, order, cur, root):
    """『扣價格』版累計線:每 5 分鐘各線的正規化淨額 (買−賣)/(買+賣) 減去 β×同期報酬(bps),再逐 5 分鐘累計。
    β 以最近 10 個同類盤別(含目前)合併估計(中心化的最小平方斜率)。回傳 {g: [(分鐘鍵, 累計殘差)]},單位無意義,只看形狀。"""
    gs = groups(root)

    def rows_of(bars):
        ks = sorted(bars)
        if not ks:
            return []
        prev = bars[ks[0]]["o"]
        by = collections.OrderedDict()
        for mk in ks:
            by.setdefault(mk // 5 * 5, []).append(mk)
        out = []
        for k, mks in by.items():
            c = bars[mks[-1]]["c"]
            ret = (c / prev - 1) * 1e4 if prev else 0.0
            imb = {}
            for g in gs:
                bn = sum(bars[m]["bn"][g] for m in mks); sn = sum(bars[m]["sn"][g] for m in mks)
                imb[g] = (bn - sn) / (bn + sn) if (bn + sn) > 0 else 0.0
            out.append((k, ret, imb, mks[-1]))
            prev = c
        return out

    pool = [k for k in order if k[0] == cur[0] and k[1] <= cur[1]][-10:]
    allr = [r for k in pool for r in rows_of(sessions[k])]
    beta = {}
    for g in gs:
        n = len(allr)
        if n < 10:
            beta[g] = 0.0
            continue
        mr = sum(r[1] for r in allr) / n; mx = sum(r[2][g] for r in allr) / n
        var = sum((r[1] - mr) ** 2 for r in allr)
        beta[g] = sum((r[1] - mr) * (r[2][g] - mx) for r in allr) / var if var > 0 else 0.0
    res = {g: [] for g in gs}
    run = {g: 0.0 for g in gs}
    for k, ret, imb, last_mk in rows_of(sessions[cur]):
        for g in gs:
            run[g] += imb[g] - beta[g] * ret
            res[g].append((last_mk, run[g]))
    return res


def _adj_fill(ser, ks):
    """把每 5 分鐘一點的累計殘差,向前填到每個分鐘(圖上每分鐘一個座標)。"""
    out, j, cur = [], 0, 0.0
    for mk in ks:
        while j < len(ser) and ser[j][0] <= mk:
            cur = ser[j][1]; j += 1
        out.append((mk, cur))
    return out


def _overlay_svg(S, w=860, h=250, adj=None):
    """價格線 + 各線累計淨額疊在同一張圖,各線以自己的 ±最大值正規化、0 線置中。
    adj 給定時改畫『扣價格』的累計殘差(單位無意義,只看形狀)。
    hover 資料放 data-pts:[x, 時間, 價, 各線值…],名稱/顏色放 data-g、單位放 data-unit。"""
    ks = S["ks"]
    if len(ks) < 2:
        return "<div class='meta'>尚無資料</div>"
    root = S["root"]
    gs = groups(root)
    cum = S["cum"] if adj is None else {g: _adj_fill(adj[g], ks) for g in gs}
    unit = "萬" if adj is None else ""
    pad_l, pad_r, pad_t, pad_b = 52, 112, 8, 20
    x0, x1 = ks[0], ks[-1]
    sx = lambda m: pad_l + (m - x0) / (x1 - x0) * (w - pad_l - pad_r)  # noqa: E731
    prices = [p for _, p in S["price"]]
    lo, hi = min(prices), max(prices)
    if hi - lo < 1e-9:
        hi = lo + 1
    top, bot = pad_t, h - pad_b
    py = lambda v: top + (hi - v) / (hi - lo) * (bot - top)  # noqa: E731
    mid = (top + bot) / 2
    pcol = "#e6edf3"      # 價格線用中性白,避免與紅色線混淆
    o = [f"<svg viewBox='0 0 {w} {h}' width='100%' style='max-width:{w}px;display:block;background:#0d1117;border:1px solid #30363d;border-radius:6px' class='ffsvg'"]
    pts = []
    pmap = dict(S["price"])
    for i, mk in enumerate(ks):
        pts.append([round(sx(mk), 1), _tkey(mk), pmap[mk]] + [round(cum[g][i][1], 2 if adj is not None else 0) for g in gs])
    names = [[gname(root, g), gcolor(root, g)] for g in gs]
    o.append(f" data-pts='{json.dumps(pts)}' data-g='{html_mod.escape(json.dumps(names, ensure_ascii=False), quote=True)}' data-unit='{unit}'>")
    o.append(f"<line x1='{pad_l}' x2='{w - pad_r}' y1='{mid:.0f}' y2='{mid:.0f}' stroke='#30363d' stroke-dasharray='2,3'/>")
    step = _nice_step(x1 - x0)
    t = (int(x0) // step + 1) * step
    while t < x1:
        o.append(f"<text x='{sx(t):.1f}' y='{h - 5}' fill='#6e7681' font-size='10' text-anchor='middle'>{_tkey(t)}</text>")
        t += step
    o.append(f"<text x='{pad_l - 4}' y='{top + 8}' font-size='9' fill='#8b949e' text-anchor='end'>{hi:,.0f}</text>"
             f"<text x='{pad_l - 4}' y='{bot}' font-size='9' fill='#8b949e' text-anchor='end'>{lo:,.0f}</text>")
    legend, ends = [], []
    for g in [g for g in ("M", "R", "C", "B") if g in gs]:     # 第一線(B)最後畫,疊在最上層
        v = [x for _, x in cum[g]]
        mx = max(abs(x) for x in v) or 1.0
        o.append("<polyline fill='none' stroke='%s' stroke-width='1.2' opacity='0.85' points='%s'/>"
                 % (gcolor(root, g), " ".join(f"{sx(m):.1f},{mid - (x / mx) * ((bot - top) / 2 - 6):.1f}" for m, x in zip(ks, v))))
        val = f"{v[-1]:+,.0f}萬" if adj is None else ""
        ends.append([mid - (v[-1] / mx) * ((bot - top) / 2 - 6), gcolor(root, g), f"{gname(root, g)} {val}".strip()])
        legend.append(f"<span style='color:{gcolor(root, g)}'>{gname(root, g)} {val}{'(尺±%s)' % format(mx, ',.0f') if adj is None else ''}</span>")
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
    cap = ("累計淨額(買−賣)疊在價格上;各線以自己的 ±最大值正規化、0 線置中(尺=該線最大累計淨額)。" if adj is None else
           "『扣價格』累計殘差:每 5 分鐘各線的正規化淨額先扣掉同期價格變動的影響(β 以最近 10 個同類盤別估計)再累計;單位無意義,只看形狀與是否互相背離。")
    return "".join(o) + f"<div class='meta' style='margin-top:2px'>{cap} " + " · ".join(legend) + "</div>"



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


FUT_NAME = {"MXF": "小型台指 MXF", "TMF": "微型台指 TMF", "TXF": "大型台指 TXF", "CDF": "台積電期貨 CDF(2000股)", "QFF": "台積電小型期貨 QFF(100股)"}
R_COLOR = {"TXF": "#e6edf3", "MXF": "#ff7b72", "TMF": "#58a6ff", "CDF": "#d2a8ff", "QFF": "#e3b341"}

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
            f"<title>期貨大單−散戶</title><style>{base}{_CSS_EXTRA}</style>{extra_head}</head><body>")


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


def _def_tip(root):
    if root in FF.NOTIONAL_ROOTS:
        return f"大單=單筆爆發名目≥{BIG_AMT / 1e6:.0f}百萬;散戶=名目<{FF.RETAIL_AMT / 1e4:.0f}萬;中單=其餘。「單」=同毫秒同方向主動成交爆發量,不是母單、不辨認身分"
    if root == "TXF":
        return ("大單≥4000萬=單筆爆發名目≥4000萬;1口連發=同方向同口數連續(1口間隔≤1秒連≥5筆、≥2口間隔≤5秒連≥4筆),確認後回頭歸類;"
                "孤立單口=1口且不在連發串;其餘=以上皆非。不是母單、不辨認身分")
    return ("1口連發=同方向同口數連續(1口間隔≤1秒連≥5筆、≥2口間隔≤5秒連≥4筆),確認後回頭歸類;孤立單口=1口且不在連發串;其餘=以上皆非"
            "(含舊稱「大單」的名目≥1000萬單)。不是母單、不辨認身分")


def _summary_table(S, pct=None):
    root = S["root"]
    gs = groups(root)
    wins = (("本盤", S["full"], "本盤累計(日盤自 08:45、夜盤自 15:00 起)"), ("5分", S["w5"], "近 5 分鐘滾動(以最新一根分鐘為終點)"), ("30分", S["w30"], "近 30 分鐘滾動"))
    o = ["<div class='wrap'><table><thead><tr>", _th("組別", "", _def_tip(root))]
    for nm, _w, tip in wins:
        o.append(_th(f"{nm}淨額", "買−賣·萬", f"{tip}的主動買賣淨名目(萬元)。紅=買超、綠=賣超"))
        o.append(_th(f"{nm}淨口", "買−賣·口", f"{tip}的主動買賣淨口數"))
    o.append(_th("本盤買", "口"))
    o.append(_th("本盤賣", "口"))
    o.append(_th("成交占比", "買+賣÷總量", "本盤該組主動買賣口數 ÷ 本盤成交量。注意:不是主頁的「大戶佔比」(那是淨額差÷成交額)"))
    npr = (pct or {}).get("n", 0)
    o.append(_th("本盤百分位", f"近{npr}盤·同時點" if npr >= MIN_PRIOR else "歷史不足", "本盤累計淨額相對『最近 N 個同類盤別、同一個開盤後經過時間』的百分位(日對日、夜對夜)。歷史盤別少於 5 個時不計算"))
    o.append("</tr></thead><tbody>")
    for g in gs:
        o.append(f"<tr><td class='g' style='color:{gcolor(root, g)}'>{gname(root, g)}</td>")
        for _nm, w, _t in wins:
            t = w["tot"][g]
            n = t["bn"] - t["sn"]; nl = t["bl"] - t["sl"]
            o.append(f"<td class='{_cls(n)}'>{_wan(n)}</td><td class='{_cls(nl)}'>{nl:+,}</td>")
        t = S["full"]["tot"][g]
        share = (t["bl"] + t["sl"]) / S["vol"] * 100 if S["vol"] else 0
        o.append(f"<td class='up'>{t['bl']:,}</td><td class='dn'>{t['sl']:,}</td><td>{share:.0f}%</td>"
                 + _pct_cell((pct or {}).get(g) if npr >= MIN_PRIOR else None, npr) + "</tr>")
    dl = f"{gname(root, 'B')}−{gname(root, 'R')}"
    o.append(f"<tr><td class='g'>{dl}</td>")
    for _nm, w, _t in wins:
        v = share_diff(w)
        o.append(f"<td class='{_cls(v or 0)}' colspan='2' title='({gname(root, 'B')}淨額−{gname(root, 'R')}淨額)÷成交額,與主頁「大戶−散戶」同算法'>{'—' if v is None else f'{v:+.1f}%'}</td>")
    o.append("<td colspan='3' class='dim'>÷成交額</td>" + _pct_cell((pct or {}).get("D") if npr >= MIN_PRIOR else None, npr) + "</tr></tbody></table></div>")
    return "".join(o)



def _block_table(bars, S):
    root = S["root"]
    gs = groups(root)
    blocks = collections.OrderedDict()
    for mk in S["ks"]:
        key = mk // 5 * 5
        b = blocks.setdefault(key, {"c": None, "vol": 0, "amt": 0.0, **{g: 0.0 for g in GROUPS}})
        b["c"] = bars[mk]["c"]; b["vol"] += bars[mk]["vol"]; b["amt"] += bars[mk]["amt"]
        for g in GROUPS:
            b[g] += bars[mk]["bn"][g] - bars[mk]["sn"][g]
    o = ["<div class='wrap'><table><thead><tr>", _th("時段", "5分"), _th("收"), _th("成交量", "口")]
    for g in gs:
        o.append(_th(f"{gname(root, g)}淨額", "買−賣·萬"))
    o.append(_th(f"{gname(root, 'B')}−{gname(root, 'R')}", "÷成交額%"))
    o.append("</tr></thead><tbody>")
    for key, b in reversed(list(blocks.items())):
        d = (b["B"] - b["R"]) / b["amt"] * 100 if b["amt"] else None
        cells = "".join(f"<td class='{_cls(b[g])}'>{_wan(b[g])}</td>" for g in gs)
        o.append(f"<tr><td style='text-align:left'>{_tkey(key)}</td><td>{b['c']:,.0f}</td><td>{b['vol']:,}</td>{cells}"
                 f"<td class='{_cls(d or 0)}'>{'—' if d is None else f'{d:+.1f}%'}</td></tr>")
    o.append("</tbody></table></div>")
    return "".join(o)



def _history_table(sessions, order, root):
    gs = groups(root)
    o = ["<div class='wrap'><table><thead><tr>", _th("盤別"), _th("漲跌", "%"), _th("成交量", "口")]
    for g in gs:
        o.append(_th(f"{gname(root, g)}淨額", "買−賣·萬"))
    o.append(_th(f"{gname(root, 'B')}−{gname(root, 'R')}", "÷成交額%"))
    o.append("</tr></thead><tbody>")
    for k in reversed(order[-10:]):
        s = summarize(sessions[k], root); c = (s["close"] / s["open"] - 1) * 100 if s["open"] else 0
        cells = "".join(f"<td class='{_cls(s['full']['tot'][g]['bn'] - s['full']['tot'][g]['sn'])}'>{_wan(s['full']['tot'][g]['bn'] - s['full']['tot'][g]['sn'])}</td>" for g in gs)
        d = share_diff(s["full"])
        o.append(f"<tr><td style='text-align:left'><a href='/futflow?root={root}&s={k[0]}|{k[1]}' title='{html_mod.escape(_sess_title(*k))}'>{html_mod.escape(_sess_label(*k))}</a></td>"
                 f"<td class='{_cls(c)}'>{c:+.2f}%</td><td>{s['vol']:,}</td>{cells}<td class='{_cls(d or 0)}'>{'—' if d is None else f'{d:+.1f}%'}</td></tr>")
    o.append("</tbody></table></div>")
    return "".join(o)



def _root_tabs(cur):
    tabs = [(LINK, "聯動(指數×台積電)")] + [(r, FUT_NAME[r]) for r in ROOTS]
    return "".join(f"<a class='tab{' on' if r == cur else ''}' href='/futflow?root={r}'>{html_mod.escape(n)}</a>" for r, n in tabs)


# ---------------------------------------------------------------- 聯動分頁(指數期貨 × 台積電期貨,同一個盤別、同一條時間軸)

def _lines_svg(series, x0, x1, w=860, h=200, unit="", dec=1):
    """多條折線同一張圖。series=[(標籤, 顏色, [(分鐘鍵, 值)])];共用 y 軸(含 0 線),不正規化,所以不同商品的幅度可直接比。"""
    pts = [v for _, _, ss in series for _, v in ss]
    if not pts or x1 <= x0:
        return "<div class='meta'>尚無資料</div>"
    pad_l, pad_r, pad_t, pad_b = 52, 150, 8, 20
    lo, hi = min(min(pts), 0), max(max(pts), 0)
    if hi - lo < 1e-9:
        hi = lo + 1
    top, bot = pad_t, h - pad_b
    sx = lambda m: pad_l + (m - x0) / (x1 - x0) * (w - pad_l - pad_r)  # noqa: E731
    sy = lambda v: top + (hi - v) / (hi - lo) * (bot - top)  # noqa: E731
    o = [f"<svg viewBox='0 0 {w} {h}' width='100%' style='max-width:{w}px;display:block;background:#0d1117;border:1px solid #30363d;border-radius:6px'>"]
    o.append(f"<line x1='{pad_l}' x2='{w - pad_r}' y1='{sy(0):.0f}' y2='{sy(0):.0f}' stroke='#30363d' stroke-dasharray='2,3'/>")
    step = _nice_step(x1 - x0)
    t = (int(x0) // step + 1) * step
    while t < x1:
        o.append(f"<text x='{sx(t):.1f}' y='{h - 5}' fill='#6e7681' font-size='10' text-anchor='middle'>{_tkey(t)}</text>")
        t += step
    o.append(f"<text x='{pad_l - 4}' y='{top + 8}' font-size='9' fill='#8b949e' text-anchor='end'>{hi:,.{dec}f}</text>"
             f"<text x='{pad_l - 4}' y='{bot}' font-size='9' fill='#8b949e' text-anchor='end'>{lo:,.{dec}f}</text>")
    ends = []
    for label, color, ss in series:
        if not ss:
            continue
        o.append(f"<polyline fill='none' stroke='{color}' stroke-width='1.3' opacity='0.9' points='{' '.join(f'{sx(m):.1f},{sy(v):.1f}' for m, v in ss)}'/>")
        ends.append([sy(ss[-1][1]), color, f"{label} {ss[-1][1]:+,.{dec}f}{unit}"])
    ends.sort(key=lambda e: e[0])
    for i in range(1, len(ends)):
        if ends[i][0] - ends[i - 1][0] < 13:
            ends[i][0] = ends[i - 1][0] + 13
    for y, c, txt in ends:
        o.append(f"<text x='{w - pad_r + 4}' y='{y + 4:.1f}' fill='{c}' font-size='11'>{html_mod.escape(txt)}</text>")
    o.append("</svg>")
    return "".join(o)


def _linked_body(sel):
    """聯動頁:同一個盤別下,TXF/MXF/TMF/CDF/QFF 並排。回傳 (html, live)。"""
    with _LOCK:
        per = {r: {k: dict(v) for k, v in _BOOKS[r].sessions.items() if v} for r in ROOTS if r in _BOOKS}
        last_ts = {r: _BOOKS[r].last_ts for r in ROOTS if r in _BOOKS}
    # 盤別清單以 MXF 為準(最完整);沒有 MXF 資料就退回任何有資料的品牌
    base = per.get("MXF") or next((v for v in per.values() if v), {})
    keys = sorted(base, key=lambda k: (k[1], 0 if k[0] == "day" else 1))
    order = [k for k in keys if len(base[k]) >= 20 or (k == keys[-1] and len(base[k]) >= 2 and _is_active(k))]
    if not order:
        return "<h3>期貨聯動</h3><div class='meta'>沒有足夠資料的盤別</div>", False
    want = None
    if "|" in sel:
        s_, _, t_ = sel.partition("|")
        want = (s_, t_) if (s_, t_) in order else None
    cur = want or order[-1]
    now = _now()
    status, in_session = market_status(now)
    live = in_session and cur == order[-1]
    bars = {r: per.get(r, {}).get(cur) for r in ROOTS}
    have = [r for r in ROOTS if bars[r]]
    lastmk = max(max(bars[r]) for r in have)
    firstmk = min(min(bars[r]) for r in have)
    o = [f"<h3>期貨聯動 · 指數 × 台積電 <span class='meta ffclk'>{now:%H:%M:%S}</span><span class='chip {'on' if in_session else 'off'}'>{html_mod.escape(status)}</span></h3>",
         "<div>" + _root_tabs(LINK) + "</div>",
         "<div>" + "".join(f"<a class='tab{' on' if k == cur else ''}' href='/futflow?root={LINK}&s={k[0]}|{k[1]}'>{html_mod.escape(_sess_label(*k))}</a>"
                           for k in reversed(order[-8:])) + "</div>",
         f"<div class='meta'>{html_mod.escape(_sess_title(*cur))} · 所有視窗(5分／30分)都以全品牌最新一分鐘 {_tkey(lastmk)} 為終點,薄的品牌最近沒成交就是 0,不是資料缺漏</div>"]
    # ---- 表:每品牌一列
    wins = (("本盤", 10 ** 6), ("5分", 5), ("30分", 30))
    o.append("<div class='sect'>各品牌 第一線／第二線 淨額(買−賣·萬元)· 各品牌定義不同,見「定義」欄</div><div class='wrap'><table><thead><tr>")
    o.append(_th("品牌", "", "各品牌的分組口徑不同(2026-10-19 起):指數期貨依下單結構分,台積電期貨依名目分。不同品牌的同名欄位不可直接比較或相加"))
    o += [_th("現價"), _th("漲跌", "%"), _th("成交額", "億"), _th("末筆", "時間"), _th("定義", "第一線／第二線")]
    for nm, _m in wins:
        o.append(_th(f"{nm}第一線", "萬", "紅/青:該品牌的第一條線(見定義欄)"))
        o.append(_th(f"{nm}第二線", "萬", "藍:該品牌的第二條線(見定義欄)"))
    o.append("</tr></thead><tbody>")

    def _row(label, color, bs, name_tip="", rk=None):
        ks = [sorted(b) for b in bs]
        pair = ("B", "R")
        tot_win = {nm: {g: 0.0 for g in pair} for nm, _m in wins}
        amt = 0.0
        for b, kk in zip(bs, ks):
            for nm, m in wins:
                w = _window(b, kk, lastmk, m)
                for g in pair:
                    tot_win[nm][g] += w["tot"][g]["bn"] - w["tot"][g]["sn"]
            amt += sum(b[k]["amt"] for k in kk)
        main = bs[0]; mk = sorted(main)
        px = main[mk[-1]]["c"]; chg = (px / main[mk[0]]["o"] - 1) * 100 if main[mk[0]]["o"] else 0
        rk = rk or label
        dfn = (f"<span style='color:{gcolor(rk, 'B')}'>{html_mod.escape(gname(rk, 'B'))}</span> / "
               f"<span style='color:{gcolor(rk, 'R')}'>{html_mod.escape(gname(rk, 'R'))}</span>")
        cells = "".join(f"<td class='{_cls(tot_win[nm][g])}'>{_wan(tot_win[nm][g])}</td>" for nm, _m in wins for g in pair)
        return (f"<tr><td class='g' style='color:{color}' title='{html_mod.escape(name_tip, quote=True)}'>{html_mod.escape(label)}</td><td>{px:,.0f}</td>"
                f"<td class='{_cls(chg)}'>{chg:+.2f}%</td><td>{amt / 1e8:,.1f}</td><td class='dim'>{_tkey(mk[-1])}</td><td style='text-align:left;font-size:11px'>{dfn}</td>{cells}</tr>")

    for r in ("TXF", "MXF", "TMF"):
        if bars[r]:
            o.append(_row(r, R_COLOR[r], [bars[r]], FUT_NAME[r]))
    for r in TSMC:
        if bars[r]:
            o.append(_row(r, R_COLOR[r], [bars[r]], FUT_NAME[r]))
    if bars["CDF"] and bars["QFF"]:                 # 兩者同為名目分級,淨額可直接相加
        o.append(_row("台積電合計", "#e6edf3", [bars["QFF"], bars["CDF"]], "CDF+QFF;現價/漲跌取 QFF", rk="QFF"))
    o.append("</tbody></table></div>")
    # ---- 圖
    ret, net = [], []
    for r in ROOTS:
        b = bars[r]
        if not b:
            continue
        kk = sorted(b)
        o0 = b[kk[0]]["o"] or 1
        ret.append((r, R_COLOR[r], [(m, (b[m]["c"] / o0 - 1) * 1e4) for m in kk]))
        run, ser = 0.0, []
        for m in kk:
            run += (b[m]["bn"]["B"] - b[m]["sn"]["B"]) / WAN
            ser.append((m, run))
        net.append((r, R_COLOR[r], ser))
    o.append("<div class='sect'>相對開盤報酬(bps)· 同一條時間軸</div>")
    o.append(_lines_svg(ret, firstmk, lastmk, unit="bp", dec=1))
    o.append("<div class='sect'>第一線累計淨額(萬元)· 各品牌第一線定義不同(見上表),幅度不可直接比</div>")
    o.append(_lines_svg(net, firstmk, lastmk, unit="萬", dec=0))
    o.append("<div class='disc'><b>口徑與限制</b><br>"
             "· 純觀察:不進分數、不是訊號。分組依單量或下單結構,<b>不辨認身分</b>,也不等於大戶或法人。<br>"
             "· 2026-10-19 起各品牌分組不同:小台/微台 第一線=1口連發、第二線=孤立單口;大台 第一線=大單≥4000萬、第二線=孤立單口(另有1口連發);台積電期貨 第一線=大單≥1000萬、第二線=散戶(名目 &lt; 50 萬)。同名欄位在不同品牌不可直接相加或比較。<br>"
             "· 台積電期貨夜盤每分鐘常只有個位數口,折線稀疏;日盤才有統計力。逐筆資料自 2026-10-16 21:43 起。<br>"
             "· 圖是分鐘級;要看毫秒先後需另做事件時間軸(尚未做,見規劃 P3)。</div>")
    for r in ROOTS:
        fr, age = freshness(last_ts.get(r, ""), in_session, now)
        if live and r in CORE_ROOTS and fr in ("stale", "missing"):
            o.insert(3, f"<div class='meta'><span class='warnv'>⚠ {r} 資料{'過舊' if fr == 'stale' else '缺漏'}</span></div>")
    return "".join(o), live


def _min_vol(root):
    return {"TMF": 3000, "MXF": 1500, "TXF": 600, "CDF": 200, "QFF": 1000}.get(root, 1500)


def _parse_sel(sel):
    """sel 形如 '' | 'day|2026-10-16' | 'day|2026-10-16|adj' | 'adj'。回傳 (盤別選擇字串, 是否扣價格版)。"""
    if sel == "adj":
        return "", True
    if sel.endswith("|adj"):
        return sel[:-4], True
    return sel, False


def _disc(root, S):
    """依品牌產生『口徑與限制』說明。所有數字都來自 2026-10-19 的探索分析(記憶 futflow-tier-redundancy-20261019)。"""
    big_lots = int(BIG_AMT / (PV[root] * (S["close"] or 47000))) + 1
    common = ("· 方向以成交價對買一／賣一判定,無法判定者不計方向。日盤與夜盤是兩條獨立資料流。圖表以分鐘為單位,最新一分鐘每秒更新。<br>"
              "· 「百分位」＝本盤累計淨額在最近 10 個同類盤別、同一個開盤後經過時間的位置(日對日、夜對夜);歷史少於 5 個盤別時不計算。它描述『相對平常多極端』,不是方向預測。<br>"
              "· <span class='warnv'>⚠</span> 純觀察展示,不進任何分數、不是證實的訊號。<b>兩條線不相像不等於有預測力</b>:這些分組對下一個 5/30 分鐘報酬都沒有顯著預測力(約 36 個日盤,|t|&lt;2.1);"
              "先前回測也顯示秒級報酬差異遠小於交易成本。</div>")
    if root in FF.NOTIONAL_ROOTS:
        return ("<div class='disc'><b>台積電期貨的分組口徑</b><br>"
                f"· 1 口名目差異大(1 口 QFF 約 24 萬、1 口 CDF 約 485 萬),所以不用「1 口=散戶」,改用純名目:散戶＜{FF.RETAIL_AMT / 1e4:.0f} 萬、大單≥{BIG_AMT / 1e6:.0f} 百萬元(目前約 {big_lots} 口以上)、其餘中單。<br>"
                "· <b>樣本不足</b>:逐筆資料自 2026-10-16 21:43 起才有,目前只有夜盤個位數口、沒有可評估的日盤區間;占比、百分位與兩線相關都沒有意義,等累積約 10 個日盤(約 11/02)再評估是否套用下單結構分組。<br>"
                "· 「單」＝同毫秒、同方向的主動成交爆發量,不是母單;這是依單量分組,<b>不是辨認人</b>。<br>" + common)
    if root == "TXF":
        who = ("· <b>分組(2026-10-19 起)</b>:<span style='color:#ff7b72'>大單≥4000萬</span>＝單筆爆發名目 ≥ 4000 萬(約 4 口以上);"
               "<span style='color:#39c5cf'>1口連發</span>＝同方向、同口數連續出現的單(見下);<span style='color:#58a6ff'>孤立單口</span>＝1 口且不在連發串;其餘=中間口數。優先序:大單 &gt; 連發 &gt; 孤立單口 &gt; 其餘。<br>"
               "· 為什麼:大台 1 口名目已近 970 萬,舊的『1 口=散戶、≥1000 萬=大單』等於 1 口 vs 2 口以上,中間沒有東西。新分法(前半段挑、後半段約 18 個日盤驗證)兩線 5 分鐘淨額相關約 0.36(舊 0.44)。<br>"
               "· 探索(未預註冊、36 個日盤):大台『孤立單口』日淨額與投信期貨淨成交 r=+0.59(扣當日報酬後仍 +0.59),外資/自營商無穩定關係;這是相關不是因果,先當假設。<br>")
    else:
        who = ("· <b>分組(2026-10-19 起)</b>:<span style='color:#39c5cf'>1口連發</span>＝同方向、同口數連續出現的單(見下);<span style='color:#58a6ff'>孤立單口</span>＝1 口且不在連發串;其餘=中間口數(含舊稱『大單』的名目≥1000 萬單)。<br>"
               f"· 為什麼:只按大小分(舊:1 口=散戶、≥1000 萬=大單),各線的主動買賣淨額都跟價格同向(各與價格相關 0.5~0.7),換門檻救不了;"
               + ("小台舊兩線 5 分鐘淨額相關 0.63,新分法 0.26(後半段約 18 個日盤)。" if root == "MXF" else
                  "微台舊的『大單』只占成交額 9%、只有 54% 的 5 分鐘有單(近乎空線),新分法兩條線都有量。") + "<br>")
    streak = ("· <b>1口連發</b>:1 口且相鄰爆發間隔 ≤1 秒連續 ≥5 筆,或 ≥2 口且間隔 ≤5 秒連續 ≥4 筆(同方向、同口數)。達標後,串內較早的幾筆回頭一併歸類,"
              "所以最新幾秒的歸類會晚 3~8 秒才補上(實測一串從第 1 筆到第 4 筆中位數不到 2 秒)。<br>"
              "· <b>它不是辨認人</b>:連發約 96% 是 1 口高頻連發,是行為描述,不是證實的程式單(5 分鐘自相關僅約 0.1);它與價格較不同步(相關約 0.2~0.3)是觀察,不是結論。<br>"
              "· 「單」＝同毫秒、同方向的主動成交爆發量,不是母單;期貨沒有分點或身分資料。<br>")
    return ("<div class='disc'><b>口徑與限制</b><br>" + who + streak
            + "· 「第一線−第二線」＝(第一線淨額 − 第二線淨額) ÷ 成交額;「成交占比」是買賣口數占比,兩者不同。<br>"
            + "· 「扣價格殘差」＝各線每 5 分鐘的正規化淨額先扣掉同期價格變動的影響再累計(β 以最近 10 個同類盤別估計);用來看兩條線在價格之外是否真的不同。<br>" + common)


def _body(root, sel):
    """回傳 (html 片段, 是否即時更新)。片段不含 <html><head>,供整頁與每秒更新共用。"""
    root = root.upper() if root.upper() in ROOTS + (LINK,) else "MXF"
    sel, adj_on = _parse_sel(sel)
    if root == LINK:
        return _linked_body(sel)
    with _LOCK:
        book = _BOOKS.get(root)
        sessions = {k: dict(v) for k, v in book.sessions.items() if v} if book else {}
        last_ts = book.last_ts if book else ""
    keys = sorted(sessions, key=lambda k: (k[1], 0 if k[0] == "day" else 1))
    # 殘留(<20 根分鐘,例如週末檔的零星重送)不列;但最新的盤別即使剛開盤也要列,才能在開盤後立刻切過去
    order = [k for k in keys if len(sessions[k]) >= 20 or (k == keys[-1] and len(sessions[k]) >= 2 and _is_active(k))]
    if not order:
        return "<h3>期貨 大單−散戶</h3><div class='meta'>沒有足夠資料的盤別</div>", False
    want = None
    if "|" in sel:
        s_, _, t_ = sel.partition("|")
        want = (s_, t_) if (s_, t_) in order else None
    cur = want or order[-1]
    S = summarize(sessions[cur], root)
    pct = percentiles(sessions[cur], cur, sessions, order)
    now = _now()
    status, in_session = market_status(now)
    is_latest = cur == order[-1]
    live = in_session and is_latest            # 最新盤別、且正在交易時段:不論是預設頁或固定網址都每秒更新
    o = []
    o.append(f"<h3>期貨 大單−散戶 · {FUT_NAME[root]} <span class='meta ffclk'>{now:%H:%M:%S}</span>"
             f"<span class='chip {'on' if in_session else 'off'}'>{html_mod.escape(status)}</span></h3>")
    o.append("<div>" + _root_tabs(root) + "</div>")
    o.append("<div>" + "".join(f"<a class='tab{' on' if k == cur else ''}' href='/futflow?root={root}&s={k[0]}|{k[1]}' title='{html_mod.escape(_sess_title(*k))}'>{html_mod.escape(_sess_label(*k))}</a>"
                               for k in reversed(order[-8:])) + "</div>")
    chg = (S["close"] / S["open"] - 1) * 100 if S["open"] else 0
    flags = []
    if is_latest:
        fr, age = freshness(last_ts, in_session, now)
        if fr == "stale":
            flags.append(f"<span class='warnv'>⚠ 資料過舊:最新成交已 {age:.0f} 秒前,可能中斷</span>")
        elif fr == "missing":
            flags.append("<span class='warnv'>⚠ 交易時段內沒有資料</span>")
    if S["vol"] < _min_vol(root) or S["minutes"] < 15:
        flags.append("<span class='warnv'>⚠ 樣本不足(本盤未滿 15 分鐘或成交量太少),占比會劇烈跳動</span>")
    o.append(f"<div class='meta'>{html_mod.escape(_sess_title(*cur))} · 開 {S['open']:,.0f} 高 {S['hi']:,.0f} 低 {S['lo']:,.0f} 收／現 {S['close']:,.0f} "
             f"(<span class='{_cls(chg)}'>{chg:+.2f}%</span>) · 成交 {S['vol']:,} 口 · 成交額 {S['amt'] / 1e8:,.1f} 億 {' '.join(flags)}</div>")
    o.append("<div class='sect'>本盤／5分／30分 主動買賣</div>")
    o.append(_summary_table(S, pct))
    if root == "MXF":                              # 小台持倉估計:只對應期交所 MTX,微台沒有校準
        o.append(MP.render_block(MP.estimate(sessions), html_mod.escape))
    o.append("<div class='sect'>價格與累計淨額(疊圖)</div>")
    base_u = f"/futflow?root={root}&s={cur[0]}|{cur[1]}"
    o.append("<div class='meta'>線型: "
             f"<a class='tab{'' if adj_on else ' on'}' href='{base_u}'>原始淨額</a>"
             f"<a class='tab{' on' if adj_on else ''}' href='{base_u}|adj' title='把各線先扣掉同期價格變動的影響再累計;用來看兩條線在『價格之外』是否真的不同'>扣價格殘差</a></div>")
    o.append(_overlay_svg(S, adj=_adj_series(sessions, order, cur, root) if adj_on else None))
    o.append("<div class='sect'>逐 5 分鐘(新→舊)</div>")
    o.append(_block_table(sessions[cur], S))
    o.append("<div class='sect'>近期各盤別</div>")
    o.append(_history_table(sessions, order, root))
    o.append(_disc(root, S))
    return "".join(o), live


_JS = """<div id='fftip' style='position:fixed;display:none;z-index:9;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:3px 7px;font-size:11px;pointer-events:none'></div>
<script src='/assets/live.js?v=1'></script>
<script>(function(){
var el=document.getElementById('ffapp');
function bind(){document.querySelectorAll('svg.ffsvg').forEach(function(s){
 var rows=JSON.parse(s.getAttribute('data-pts')||'[]');
 s.onmousemove=function(e){var r=s.getBoundingClientRect(),vb=s.viewBox.baseVal,x=(e.clientX-r.left)/r.width*vb.width,b=null,d=1e9;
  rows.forEach(function(q){var k=Math.abs(q[0]-x);if(k<d){d=k;b=q}});
  var t=document.getElementById('fftip');if(!b){t.style.display='none';return}
  var g=JSON.parse(s.getAttribute('data-g')||'[]'),u=s.getAttribute('data-unit')||'',h=b[1]+' 價 '+b[2].toLocaleString()+'<br>';
  g.forEach(function(x,i){h+='<span style="color:'+x[1]+'">'+x[0]+' '+b[3+i].toLocaleString()+u+'</span> '});
  t.innerHTML=h;
  t.style.left=(e.clientX+12)+'px';t.style.top=(e.clientY+12)+'px';t.style.display='block'};
 s.onmouseleave=function(){document.getElementById('fftip').style.display='none'}})}
bind();LiveClock('.ffclk');
if(el.dataset.live==='1')LiveFrag({el:el,url:el.dataset.u,interval:1000,onSwap:bind});
})();</script>"""


def render_futflow_frag(root="MXF", sel=""):
    _refresh()
    if not _LOADING["done"]:
        return "<div class='meta'>載入中…</div>"
    return _body(root, sel)[0]


def render_futflow(root: str = "MXF", sel: str = "") -> str:
    _refresh()
    root = root.upper() if root.upper() in ROOTS + (LINK,) else "MXF"
    if not _LOADING["done"]:
        msg = _LOADING["err"] or "載入最近幾個交易日的逐筆成交中(首次約 1 分鐘),請稍候…"
        return _head("<meta http-equiv='refresh' content='8'>") + f"<h3>期貨 大單−散戶</h3><div class='meta'>{html_mod.escape(msg)}</div></body></html>"
    body, live = _body(root, sel)
    u = f"/futflowfrag?root={root}&s={html_mod.escape(sel, quote=True)}"
    return _head() + f"<div id='ffapp' data-live='{1 if live else 0}' data-u='{u}'>{body}</div>" + _JS + "</body></html>"
