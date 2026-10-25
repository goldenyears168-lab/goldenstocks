"""期貨內外盤儀表板（/futflow）：每天一頁，該日日盤＋夜盤、所有品牌（TXF/MXF/TMF/CDF/QFF）的圖一次排出來，只放圖。純展示，不進分數、不是訊號。

資料：`<狀態根>/cache/{mxf,tmf}_trades/{root}_trades_YYYY-MM-DD.jsonl`（富邦 ws 逐筆成交，兩條獨立流 FUTURE／FUTURE_AH）。
口徑：
- 一筆「單」＝同毫秒、同方向的主動成交爆發量（用 Δcum_volume 合併，不用 size 欄；富邦 ws size 會低估）。
- 分組依品牌各自定義，SSOT 在 `futures_flow`（_SPEC）：小台/微台＝連續單口／零星單口／中單；大台＝大單≥4000萬／連續單口／零星單口／中單；台積電期貨＝純名目大單／中單／散戶。
- 內外盤：成交價 ≥ 賣一＝外盤（主動買）、≤ 買一＝內盤（主動賣）；其餘不計方向。圖上畫的是累計「外盤−內盤」名目（億元）。
- 夜盤跨午夜：15:00～24:00 在 T 檔、00:00～05:00 在 T+1 檔，累計量跨檔延續；日盤只取 08:45～13:45（濾掉殭屍重送）。
⚠ 期貨沒有身分：這裡的分組是依單的大小或下單結構，不是辨認人。
只讀檔案、不連外、不寫檔；增量讀取（記住檔案位移），啟動時在背景載入最近幾個檔。
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import html as html_mod
import json
import os
import re
import threading
import time

import futures_flow as FF
from stock_db import DATA_DIR

CACHE = DATA_DIR.parent / "cache"
ROOTS = ("MXF", "TMF", "TXF", "CDF", "QFF")
CORE_ROOTS = ("MXF", "TMF")                    # /health 的整體判斷只看這兩個(台積電期貨夜盤每分鐘只有幾口,90 秒沒成交是常態,不是故障)
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

STREAK_BOOKS = ("TXF", "CDF", "QFF")      # 有連次標注的品牌：大台（預先登記）＋台積電期貨標準／小型（2026-10-21 起前向記錄＋顯示，未預先登記）；小台微台探索期無效不顯示
CKPT_SCHEMA = 3                                  # 2：加入『補 tick rule』影子連次與無法判定口數統計（3：台積電期貨 CDF／QFF 也跑連次；舊檢查點作廢、完整重建一次）
CKPT_DIR = CACHE / "futflow_view"            # 檢查點目錄(派生資料,可隨時刪除;刪了只是重啟時完整重建)
CKPT_EVERY = 30.0                            # 秒
V2_ROOT = FF.STREAK_ROOT                     # 竭盡 v2（連次≥10∧轉色∧RVOL≥2×；2026-10-25）目前只跑大台
V2_SCHEMA = 4                                # TXF 的檢查點多了 v2 狀態：舊檢查點作廢、只重建 TXF 一次（其他品牌不受影響）
REF30_PATH = lambda root: CACHE / f"futflow_ref30_{root}.json"   # noqa: E731  日盤可判定口數日合計（scripts/research/futures_ref30_build.py 產生；頁面隨收盤補寫）
V2_LEDGER_PATH = DATA_DIR / "biglot_forward" / "futures_exhaust_v2_events.jsonl"
_V2_SEEN: set = set()


def _ckpt_schema(root: str) -> int:
    return V2_SCHEMA if root == V2_ROOT else CKPT_SCHEMA


def _ref30_totals(root: str) -> dict:
    try:
        return {k: float(v) for k, v in json.loads(REF30_PATH(root).read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}

_LOCK = threading.RLock()
_BOOKS: dict = {}
_LOADING = {"started": False, "done": False, "err": ""}


group_of = FF.group_of            # 規則在共用模組;此處保留名稱供頁面與測試使用


def _new_bar(px):
    z = lambda: {g: 0 for g in GROUPS}  # noqa: E731
    return {"o": px, "h": px, "l": px, "c": px, "vol": 0, "bl": z(), "sl": z(), "bn": z(), "sn": z(), "t": 0, "amt": 0.0}


LEDGER_PATH = DATA_DIR / "biglot_forward" / "futures_streak_events.jsonl"   # 連次事件前向帳本（大台＋台積電期貨）
_LEDGER_SEEN: set = set()
_LEDGER_LOCK = threading.Lock()


def _ledger_load_seen():
    try:
        for ln in open(LEDGER_PATH, encoding="utf-8"):
            r = json.loads(ln)
            _LEDGER_SEEN.add((r["root"], r["version"], r["kind"], r["stype"], r["T"], r["end"]))
    except (OSError, ValueError, KeyError):
        pass


_ledger_load_seen()


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
        # 連次/連量(5 秒窗,小哥口徑):目前只對大台(小台/微台每天亮 12~17 次且無訊號,見 futures_flow.WindowStreak)
        self.wstreak = FF.WindowStreak() if root in STREAK_BOOKS else None
        # 影子版（2026-10-21）：凍結版（預先登記，定義不得改）把『買賣價之間、無法判定方向』的成交丟掉（大台約 15~17% 口數）；
        # 影子版用與股票相同的 tick rule（比前一筆價高低，同價承襲）補判後，餵給**同一個** WindowStreak 類別。
        # 兩版亮燈事件一起顯示、互相比對；影子版不屬於預先登記檢定。
        self.wstreak_fill = FF.WindowStreak() if root in STREAK_BOOKS else None
        self.merger_fill = FF.BurstMerger(self.pv, self._on_burst_fill, streaming=True) if self.wstreak_fill is not None else None
        self._fill_state: dict = {}             # quote_type → [上一筆價, 上一筆方向]（tick rule 用）
        # 竭盡 v2（大台；jack 2026-10-25）：另跑一份，不碰凍結的 wstreak。RVOL 基準＝過去 ≤10 日盤『每 30 秒平均口數』
        self._ref_totals: dict = _ref30_totals(root) if root == V2_ROOT else {}
        self._ref_saved = len(self._ref_totals)
        self.wstreak_v2 = FF.WindowStreakV2(self._ref30) if root == V2_ROOT else None
        self.unk: dict = {}                     # (stype, T) → [無法判定口數, 總口數]
        self.last_ts = ""

    def _ref30(self, T):
        tot = dict(self._ref_totals)
        if self.wstreak_v2 is not None:
            for d, v in self.wstreak_v2.day_tot.items():
                if d < T and d not in tot:
                    tot[d] = v                   # 檔案還沒補到的前幾個日盤，用頁面自己累積的
        return FF.ref30_from_totals(tot, T)

    def _maybe_save_ref30(self):
        """已收盤（比最新日盤更早）的日盤合計寫回基準檔；只在新增時寫。"""
        if self.wstreak_v2 is None or not self.wstreak_v2.day_tot:
            return
        latest = max(self.wstreak_v2.day_tot)
        new = {d: round(v, 1) for d, v in self.wstreak_v2.day_tot.items() if d < latest and d not in self._ref_totals}
        if not new:
            return
        self._ref_totals.update(new)
        try:
            out = REF30_PATH(self.root); tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(sorted(self._ref_totals.items())), ensure_ascii=False), encoding="utf-8"); tmp.replace(out)
        except OSError:
            pass

    def _on_burst_fill(self, b):
        self.wstreak_fill.add(b)

    def _on_burst(self, b):
        if self.wstreak is not None:
            self.wstreak.add(b)
        if self.wstreak_v2 is not None:
            self.wstreak_v2.add(b)
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
        if self.wstreak is not None:
            self.wstreak.flush(self.merger.last_tt)
        if self.wstreak_v2 is not None:
            self.wstreak_v2.flush(self.merger.last_tt)
            self._maybe_save_ref30()
            self._record_v2_events()
        if self.merger_fill is not None:
            self.merger_fill.flush_old()
            self.wstreak_fill.flush(self.merger_fill.last_tt)
            self._record_streak_events()

    def _record_streak_events(self):
        """連次亮燈／轉色事件前向帳本（大台＋台積電期貨；凍結版 A 與補 tick rule 影子版 B 都記）。同事件不重複；
        距現在 ≤180 秒的記 live、其餘（重建／重放）記 replay。例外自吞。價＝該分鐘收盤。"""
        try:
            new = []
            for ver, w in (("A", self.wstreak), ("B", self.wstreak_fill)):
                for (stype, T), st in w.snapshot().items():
                    bars = self.sessions.get((stype, T)) or {}
                    for kind, rows in (("lit", st["lits"]), ("turn", st["turns"])):
                        for end, d, n in rows:
                            key = (self.root, ver, kind, stype, T, round(end, 1))
                            if key in _LEDGER_SEEN:
                                continue
                            tm = dt.datetime.fromtimestamp(end, dt.timezone(dt.timedelta(hours=8)))
                            mk = tm.hour * 60 + tm.minute + (1440 if (stype == "night" and tm.hour * 60 + tm.minute < 480) else 0)
                            bar = bars.get(mk)
                            new.append((key, {"root": self.root, "version": ver, "kind": kind, "stype": stype, "T": T, "end": round(end, 1),
                                              "time": tm.strftime("%Y-%m-%d %H:%M:%S"), "dir": d, "n": n, "px": (bar["c"] if bar else None),
                                              "source": "live" if abs(time.time() - end) <= 180 else "replay", "logged_at": round(time.time(), 1)}))
            if new:
                with _LEDGER_LOCK:
                    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
                    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
                        for key, rec in new:
                            _LEDGER_SEEN.add(key)
                            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 -- 帳本失敗不得影響頁面
            pass

    def _record_v2_events(self):
        """竭盡 v2 前向帳本（大台）：只記 RVOL≥V2_RVOL 的轉色（連次≥V2_MIN_N）。同事件不重複；距現在 ≤180 秒的記 live、其餘記 replay。例外自吞。"""
        try:
            new = []
            for (stype, T), v in self.wstreak_v2.snapshot2().items():
                bars = self.sessions.get((stype, T)) or {}
                for end, d, n, rv in v["sigs"]:
                    key = (self.root, stype, T, round(end, 1))
                    if key in _V2_SEEN:
                        continue
                    tm = dt.datetime.fromtimestamp(end, dt.timezone(dt.timedelta(hours=8)))
                    bar = bars.get(tm.hour * 60 + tm.minute + (1440 if (stype == "night" and tm.hour * 60 + tm.minute < 480) else 0))
                    new.append((key, {"root": self.root, "rule": "fut-exh-v2-2026-10-25", "stype": stype, "T": T, "end": round(end, 1),
                                      "time": tm.strftime("%Y-%m-%d %H:%M:%S"), "dir": d, "n": n, "rvol30": round(rv, 2),
                                      "px": (bar["c"] if bar else None), "source": "live" if abs(time.time() - end) <= 180 else "replay",
                                      "logged_at": round(time.time(), 1)}))
            if new:
                with _LEDGER_LOCK:
                    V2_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
                    with open(V2_LEDGER_PATH, "a", encoding="utf-8") as fh:
                        for key, rec in new:
                            _V2_SEEN.add(key)
                            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 -- 帳本失敗不得影響頁面
            pass

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
        u = self.unk.setdefault((t.stype, t.T), [0, 0])
        u[1] += t.dv
        if t.side == 0:
            u[0] += t.dv
        self.merger.add(t)
        if self.merger_fill is not None:
            fs = self._fill_state.setdefault(t.qt, [None, 0])
            s_ = t.side
            if s_ == 0 and fs[0] is not None:
                s_ = 1 if t.px > fs[0] else (-1 if t.px < fs[0] else fs[1])      # tick rule：同價承襲前一方向
            fs[0] = t.px
            if s_ != 0:
                fs[1] = s_
            self.merger_fill.add(t._replace(side=s_) if s_ != t.side else t)

    # ---- 檢查點(checkpoint):位移與彙整結果一起原子寫入,重啟後只讀新增的位元組 ----
    def to_state(self, keep_sessions: int = 24) -> dict:
        keys = sorted(self.sessions, key=lambda k: (k[1], 0 if k[0] == "day" else 1))[-keep_sessions:]
        return {"schema": _ckpt_schema(self.root), "rules": FF.RULES_VERSION, "root": self.root, "offsets": self.offsets, "prev": self.stream.prev,
                "last_ts": self.last_ts, "last_tt": self.merger.last_tt,
                "open": {f"{k[0]}|{k[1]}": v for k, v in self.merger.open.items()},
                "streak": self.tagger.to_state() if self.tagger is not None else {},
                "wstreak": self.wstreak.to_state() if self.wstreak is not None else {},
                "wstreak_v2": self.wstreak_v2.to_state() if self.wstreak_v2 is not None else {},
                "fill": ({"last_tt": self.merger_fill.last_tt,
                          "open": {f"{k[0]}|{k[1]}": v for k, v in self.merger_fill.open.items()},
                          "wstreak": self.wstreak_fill.to_state(), "state": self._fill_state}
                         if self.merger_fill is not None else {}),
                "unk": {f"{k[0]}|{k[1]}": v for k, v in self.unk.items()},
                "sessions": {f"{k[0]}|{k[1]}": {str(m): bar for m, bar in self.sessions[k].items()} for k in keys}}

    @classmethod
    def from_state(cls, root: str, st: dict):
        """檢查點不合(版本、品牌、結構不符)回傳 None,呼叫端改為完整重建。"""
        try:
            if st.get("schema") != _ckpt_schema(root) or st.get("rules") != FF.RULES_VERSION or st.get("root") != root:
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
            if b.wstreak is not None:
                b.wstreak.load_state(st.get("wstreak"))
            if b.wstreak_v2 is not None:
                b.wstreak_v2.load_state(st.get("wstreak_v2"))
            fl = st.get("fill") or {}
            if b.merger_fill is not None and fl:
                b.merger_fill.last_tt = dict(fl.get("last_tt") or {})
                for k, v in (fl.get("open") or {}).items():
                    qt, side = k.split("|")
                    b.merger_fill.open[(qt, int(side))] = v
                b.wstreak_fill.load_state(fl.get("wstreak"))
                b._fill_state = {k: list(v) for k, v in (fl.get("state") or {}).items()}
            for k, v in (st.get("unk") or {}).items():
                stype, T = k.split("|")
                b.unk[(stype, T)] = [int(v[0]), int(v[1])]
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

YI = 1e8                                        # 圖上金額一律以「億元」顯示
DISPLAY = ("TXF", "MXF", "TMF", "CDF", "QFF")   # 每天一頁上的品牌順序(大到小)


def _tkey(mk):
    return "%02d:%02d" % (mk // 60 % 24, mk % 60)


def _yi(v, sign=True):
    """億元,小於 10 億留兩位小數(微台、台積電小型期貨的量很小)。"""
    d = 2 if abs(v) < 10 else 1
    return f"{v:+,.{d}f}億" if sign else f"{v:,.{d}f}億"


def summarize(bars: dict, root: str = "MXF"):
    """一個盤別的彙整:價格序列與各組累計內外盤差(外盤−內盤,億元)序列。"""
    ks = sorted(bars)
    cum = {g: [] for g in GROUPS}
    run = {g: 0.0 for g in GROUPS}
    price = []
    for mk in ks:
        b = bars[mk]
        price.append((mk, b["c"]))
        for g in GROUPS:
            run[g] += (b["bn"][g] - b["sn"][g]) / YI
            cum[g].append((mk, run[g]))
    return {"root": root, "ks": ks, "cum": cum, "price": price}


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


def _nice_step(span):
    for st in (5, 10, 15, 30, 60, 120, 180):
        if span / st <= 7:
            return st
    return 240



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


def _overlay_svg(S, title, w=640, h=230, adj=None, marks=None):
    """價格線 + 各組累計內外盤差疊在同一張圖,各線以自己的 ±最大值正規化、0 線置中。
    adj 給定時改畫『扣掉價格影響後』的累計殘差(無單位,只看形狀)。
    hover 資料放 data-pts:[x, 時間, 價, 各線值…],名稱/顏色放 data-g、單位放 data-unit。"""
    head = f"<div class='ct'>{html_mod.escape(title)}</div>"
    ks = S["ks"]
    if len(ks) < 2:
        return head + "<div class='meta'>尚無資料</div>"
    root = S["root"]
    gs = groups(root)
    cum = S["cum"] if adj is None else {g: _adj_fill(adj[g], ks) for g in gs}
    unit = "億" if adj is None else ""
    pad_l, pad_r, pad_t, pad_b = 46, 124, 8, 20
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
    o = [f"<svg viewBox='0 0 {w} {h}' width='100%' style='display:block;background:#0d1117;border:1px solid #30363d;border-radius:6px' class='ffsvg'"]
    pts = []
    pmap = dict(S["price"])
    for i, mk in enumerate(ks):
        pts.append([round(sx(mk), 1), _tkey(mk), pmap[mk]] + [round(cum[g][i][1], 2) for g in gs])
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
    ends = []
    for g in [g for g in ("M", "R", "C", "B") if g in gs]:     # 第一線(B)最後畫,疊在最上層
        v = [x for _, x in cum[g]]
        mx = max(abs(x) for x in v) or 1.0
        o.append("<polyline fill='none' stroke='%s' stroke-width='1.2' opacity='0.85' points='%s'/>"
                 % (gcolor(root, g), " ".join(f"{sx(m):.1f},{mid - (x / mx) * ((bot - top) / 2 - 6):.1f}" for m, x in zip(ks, v))))
        val = _yi(v[-1]) if adj is None else ""
        ends.append([mid - (v[-1] / mx) * ((bot - top) / 2 - 6), gcolor(root, g), f"{gname(root, g)} {val}".strip()])
    o.append(f"<polyline fill='none' stroke='{pcol}' stroke-width='1.4' points='{' '.join(f'{sx(m):.1f},{py(p):.1f}' for m, p in S['price'])}'/>")
    o.append(f"<circle cx='{sx(ks[-1]):.1f}' cy='{py(prices[-1]):.1f}' r='2.5' fill='{pcol}'/>")
    for kind, mk_, d_, n_, tstr, ver in (marks or []):        # 連次亮燈(◯)／轉色(▽△)標注：落在該分鐘的價格上；A=凍結版(實線)、B=補 tick rule 影子版(虛線、略大)
        if mk_ not in pmap:
            continue
        mx_, my_ = sx(mk_), py(pmap[mk_])
        vtag = "凍結版" if ver == "A" else ("竭盡v2" if ver == "V" else "補tick rule版")
        dash = "" if ver == "A" else " stroke-dasharray='2,2'"
        if kind == "sig":                                  # 竭盡 v2：連次≥10∧轉色∧RVOL≥2×（描述／探索，不是進出場訊號）；轉綠＝紅連攻被打斷＝買盤竭盡
            col_ = "#f0883e" if d_ < 0 else "#56d4dd"
            side_ = "買盤竭盡(紅連" if d_ < 0 else "賣盤竭盡(綠連"
            o.append(f"<text x='{mx_:.1f}' y='{(my_ - 18) if d_ < 0 else (my_ + 24):.1f}' font-size='14' text-anchor='middle' fill='{col_}' stroke='#0d1117' stroke-width='0.6'>"
                     f"★<title>[竭盡v2·期貨探索] {tstr} {side_}{n_}→轉色) 連次≥10∧RVOL≥2×；大台37日含零毛 3/5/15分 +1.4/+1.6/+3.9bps(t 1.1~1.4)·未達顯著·非進出場訊號</title></text>")
            continue
        if kind == "lit":
            r_ = 3.2 if ver == "A" else 5.0
            o.append(f"<circle cx='{mx_:.1f}' cy='{my_:.1f}' r='{r_}' fill='none' stroke='{'#ff7b72' if d_ > 0 else '#3fb950'}' stroke-width='1.6'{dash}><title>[{vtag}] {tstr} 亮燈 {'紅' if d_ > 0 else '綠'}連{n_}</title></circle>")
        elif d_ == 0:
            continue      # 平手中斷不畫標記
        else:
            off = 12 if ver == "A" else 15
            col = "#f0883e" if d_ < 0 else "#56d4dd"          # 轉綠＝紅連攻被打斷；轉紅＝綠連攻被打斷
            if d_ < 0:
                pts_ = f"{mx_ - 4:.1f},{my_ - off:.1f} {mx_ + 4:.1f},{my_ - off:.1f} {mx_:.1f},{my_ - off + 8:.1f}"
            else:
                pts_ = f"{mx_ - 4:.1f},{my_ + off:.1f} {mx_ + 4:.1f},{my_ + off:.1f} {mx_:.1f},{my_ + off - 8:.1f}"
            o.append(f"<polygon points='{pts_}' fill='none' stroke='{col}' stroke-width='1.3'{dash}><title>[{vtag}] {tstr} 轉{'綠' if d_ < 0 else '紅'}(前段{n_}窗)·僅轉色，連量大小與漲不動未驗證</title></polygon>")
    ends.append([py(prices[-1]), pcol, f"價 {prices[-1]:,.0f}"])
    ends.sort(key=lambda e: e[0])
    for i in range(1, len(ends)):
        if ends[i][0] - ends[i - 1][0] < 13:
            ends[i][0] = ends[i - 1][0] + 13
    for y, c, txt in ends:
        o.append(f"<text x='{w - pad_r + 4}' y='{y + 4:.1f}' fill='{c}' font-size='11'>{html_mod.escape(txt)}</text>")
    o.append("</svg>")
    return head + "".join(o)


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
.sh{margin:16px 0 6px;font-size:14px;color:#e6edf3;font-weight:600}
.ct{margin:0 0 2px;font-size:12px;color:#c9d1d9}
.cgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px}
.cgrid .full{grid-column:1/-1}
@media (max-width:900px){.cgrid{grid-template-columns:1fr}}
.chip{display:inline-block;font-size:11px;padding:0 6px;border-radius:3px;margin-left:6px;vertical-align:1px}
.chip.on{background:#1a4d2e;color:#b3ffcc}.chip.off{background:#30363d;color:#c9d1d9}
"""


def _head(extra_head=""):
    try:                                          # 與主儀表板共用同一份表格樣式(單一來源)
        import biglot_dashboard
        base = biglot_dashboard.TABLE_CSS
    except Exception:  # noqa: BLE001
        base = "body{background:#0d1117;color:#c9d1d9;font:12px/1.5 -apple-system,'PingFang TC',monospace;margin:8px}.meta{color:#8b949e;font-size:11px}table{border-collapse:collapse;width:100%;white-space:nowrap}th,td{padding:2px 7px;text-align:right;border-bottom:1px solid #21262d}th{background:#161b22;color:#8b949e}th .sub{display:block;font-size:9px;font-weight:400}.up{color:#ff7b72}.dn{color:#3fb950}.dim{color:#484f58}.warnv{color:#e3b341}a{color:#79c0ff;text-decoration:none}h3{margin:4px 0;font-size:14px}"
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=0.6'>"
            f"<title>期貨內外盤</title><style>{base}{_CSS_EXTRA}</style>{extra_head}</head><body>")



def _lines_svg(series, x0, x1, title, w=640, h=230, unit="", dec=1, pad_r=190):
    """多條折線同一張圖。series=[(標籤, 顏色, [(分鐘鍵, 值)])];共用 y 軸(含 0 線),不正規化,所以不同商品的幅度可直接比。"""
    head = f"<div class='ct'>{html_mod.escape(title)}</div>"
    pts = [v for _, _, ss in series for _, v in ss]
    if not pts or x1 <= x0:
        return head + "<div class='meta'>尚無資料</div>"
    pad_l, pad_t, pad_b = 46, 8, 20
    lo, hi = min(min(pts), 0), max(max(pts), 0)
    if hi - lo < 1e-9:
        hi = lo + 1
    top, bot = pad_t, h - pad_b
    sx = lambda m: pad_l + (m - x0) / (x1 - x0) * (w - pad_l - pad_r)  # noqa: E731
    sy = lambda v: top + (hi - v) / (hi - lo) * (bot - top)  # noqa: E731
    o = [f"<svg viewBox='0 0 {w} {h}' width='100%' style='display:block;background:#0d1117;border:1px solid #30363d;border-radius:6px'>"]
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
    return head + "".join(o)


def _v2_marks(v2, cur):
    """竭盡 v2（大台）→ 圖上 ★ 標注 [('sig', 分鐘鍵, 新顏色方向, 被打斷連次, 'HH:MM:SS RVOL x.x×', 'V')]。"""
    st = (v2 or {}).get(cur)
    if not st:
        return []
    out = []
    tz = dt.timezone(dt.timedelta(hours=8))
    for t_, d_, n_, rv_ in st["sigs"]:
        tm = dt.datetime.fromtimestamp(t_, tz)
        mk = tm.hour * 60 + tm.minute
        out.append(("sig", mk, d_, n_, f"{tm.strftime('%H:%M:%S')} RVOL {rv_:.1f}×", "V"))
    return out


def _streak_marks(ws, cur, ver="A"):
    """把 WindowStreak 的 lits/turns（epoch 秒）轉成圖上標注 [(kind, 分鐘鍵, 方向, 連次, 'HH:MM:SS')]。夜盤午夜後分鐘鍵 +1440（與 Trade.mk 一致）。"""
    st = (ws or {}).get(cur)
    if not st:
        return []
    out = []
    tz = dt.timezone(dt.timedelta(hours=8))
    for kind, rows in (("lit", st["lits"]), ("turn", st["turns"])):
        for t_, d_, n_ in rows:
            tm = dt.datetime.fromtimestamp(t_, tz)
            mk = tm.hour * 60 + tm.minute
            if cur[0] == "night" and mk < 480:
                mk += 1440
            out.append((kind, mk, d_, n_, tm.strftime("%H:%M:%S"), ver))
    return out


# ---------------------------------------------------------------- 每天一頁:該日日盤＋夜盤,所有品牌的圖一次排出來

_WD = "一二三四五六日"
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _lit_overlap(la, lb, tol=15.0):
    """兩版亮燈事件重疊：同方向且相差 ≤tol 秒視為同一個。回傳 (A 有對應 B 的數, B 有對應 A 的數)。"""
    ma = sum(1 for x in la if any(y[1] == x[1] and abs(y[0] - x[0]) <= tol for y in lb))
    mb = sum(1 for y in lb if any(x[1] == y[1] and abs(y[0] - x[0]) <= tol for x in la))
    return ma, mb


_STREAK_NOTE = {
    "TXF": "大台：預先登記確認期（docs/futures-streak-prereg-20261019.md），確認期天數不足、尚無結論",
    "CDF": "台積電期貨：前向記錄中，未預先登記；逐筆自 2026-10-16 起，樣本不足、不能下結論",
    "QFF": "台積電小型期貨：前向記錄中，未預先登記；逐筆自 2026-10-16 起，樣本不足、不能下結論",
}


def _streak_legend(root, cur, wsr):
    """連次標注說明列：符號、版本（凍結／補 tick rule）、兩版亮燈比對、被排除的無法判定口數比例、品牌狀態。"""
    a, b, unk = wsr[:3]
    sa, sb = (a or {}).get(cur), (b or {}).get(cur)
    o = ("<div class='meta'>◯紅/◯綠＝連次達 10 亮燈 · ▽△＝連次被反向窗打斷 · 實線＝凍結版、虛線＝補 tick rule 版(描述用,不是進出場訊號)")
    if sa and sb:
        na, nb = len(sa["lits"]), len(sb["lits"])
        ma, mb = _lit_overlap(sa["lits"], sb["lits"])
        o += f"<br>本盤亮燈：凍結版 {na} 次、補版 {nb} 次；同方向且相差 ≤15 秒視為同一個——凍結版 {ma}/{na}、補版 {mb}/{nb} 重疊"
    u = (unk or {}).get(cur)
    if u and u[1]:
        o += f"<br>⚠ 凍結版排除買賣價之間、無法判定方向的成交——本盤約 {u[0] / u[1] * 100:.1f}% 口數；補版納入"
    if len(wsr) > 3 and wsr[3] is not None:
        v2 = (wsr[3] or {}).get(cur) or {"turns": [], "sigs": []}
        o += (f"<br>★＝竭盡 v2（連次≥10 ∧ 轉色 ∧ 近30秒 RVOL≥2×；jack 2026-10-25）本盤 {len(v2['sigs'])} 次（連次≥10 的轉色共 {len(v2['turns'])} 次）。"
              f"RVOL＝最近6個5秒窗口數 ÷ 過去≤10日盤每30秒平均口數；探索性、不屬凍結的 S1 登記；事件寫入 biglot_forward/futures_exhaust_v2_events.jsonl")
    o += f"<br>{_STREAK_NOTE.get(root, '')}；事件寫入 biglot_forward/futures_streak_events.jsonl</div>"
    return o


def _snapshot():
    """在鎖內複製各品牌的盤別資料(淺複製分鐘列表),之後的繪圖不佔鎖。"""
    with _LOCK:
        per = {r: {k: dict(v) for k, v in _BOOKS[r].sessions.items() if v} for r in ROOTS if r in _BOOKS}
        last_ts = {r: _BOOKS[r].last_ts for r in ROOTS if r in _BOOKS}
        ws = {}
        for r in STREAK_BOOKS:
            b = _BOOKS.get(r)
            if b is not None and b.wstreak is not None:
                ws[r] = (b.wstreak.snapshot(), b.wstreak_fill.snapshot() if b.wstreak_fill is not None else None, dict(b.unk),
                         b.wstreak_v2.snapshot2() if b.wstreak_v2 is not None else None)
    return per, last_ts, ws


def _order_keys(per):
    """盤別清單以 MXF 為準(最完整);沒有 MXF 資料就退回任何有資料的品牌。
    殘留(<20 根分鐘,例如週末檔的零星重送)不列;最新的盤別即使剛開盤也要列。"""
    base = per.get("MXF") or next((v for v in per.values() if v), {})
    keys = sorted(base, key=lambda k: (k[1], 0 if k[0] == "day" else 1))
    return [k for k in keys if len(base[k]) >= 20 or (k == keys[-1] and len(base[k]) >= 2 and _is_active(k))]


def _linked_cells(bars_by_root):
    """聯動:各品牌相對開盤報酬(%)、第一線累計內外盤差(億元),同一條時間軸。回傳兩張圖的 html 清單。"""
    ret, net, spans = [], [], []
    for r in DISPLAY:
        b = bars_by_root.get(r)
        if not b or len(b) < 2:
            continue
        kk = sorted(b)
        spans.append((kk[0], kk[-1]))
        o0 = b[kk[0]]["o"] or 1
        ret.append((r, R_COLOR[r], [(m, (b[m]["c"] / o0 - 1) * 100) for m in kk]))
        run, ser = 0.0, []
        for m in kk:
            run += (b[m]["bn"]["B"] - b[m]["sn"]["B"]) / YI
            ser.append((m, run))
        net.append((f"{r} {gname(r, 'B')}", R_COLOR[r], ser))
    if not spans:
        return []
    x0, x1 = min(s[0] for s in spans), max(s[1] for s in spans)
    return [f"<div>{_lines_svg(ret, x0, x1, '各品牌 相對開盤漲跌(%)', unit='%', dec=2)}</div>",
            f"<div>{_lines_svg(net, x0, x1, '各品牌 第一線累計內外盤差(億元;各品牌第一線定義不同,幅度不可直接比)', unit='億', dec=2)}</div>"]


def _section(cur, per, last_ts, ws, now):
    """一個盤別(日盤或夜盤)的全部圖:聯動 2 張 + 每個品牌 2 張(原始、扣價格)。"""
    live = _is_active(cur, now)
    flags = ""
    if live:
        for r in CORE_ROOTS:
            fr, age = freshness(last_ts.get(r, ""), True, now)
            if fr == "stale":
                flags += f" <span class='warnv'>⚠ {r} 資料過舊({age:.0f} 秒前)</span>"
            elif fr == "missing":
                flags += f" <span class='warnv'>⚠ {r} 交易時段內沒有資料</span>"
    chip = "<span class='chip on'>進行中</span>" if live else ""
    o = [f"<div class='sh'>{html_mod.escape(_sess_title(*cur))}{chip}{flags}</div><div class='cgrid'>"]
    bars = {r: per.get(r, {}).get(cur) for r in DISPLAY}
    o += _linked_cells(bars)
    for r in DISPLAY:
        b = bars[r]
        if not b or len(b) < 2:
            o.append(f"<div class='meta full'>{html_mod.escape(FUT_NAME[r])}:本盤沒有成交資料</div>")
            continue
        S = summarize(b, r)
        sess_r = per[r]
        order_r = [k for k in sorted(sess_r, key=lambda k: (k[1], 0 if k[0] == "day" else 1)) if len(sess_r[k]) >= 20 or k == cur]
        wsr = ws.get(r) if r in STREAK_BOOKS else None
        marks = (_streak_marks(wsr[0], cur, "A") + _streak_marks(wsr[1], cur, "B") + (_v2_marks(wsr[3], cur) if len(wsr) > 3 else [])) if wsr else []
        raw = _overlay_svg(S, f"{FUT_NAME[r]} · 累計內外盤差(外盤−內盤,億元)疊在價格上", marks=marks)
        if wsr:
            raw += _streak_legend(r, cur, wsr)
        adj = _overlay_svg(S, f"{FUT_NAME[r]} · 扣掉價格影響後的累計(只看形狀,看兩條線在價格之外有沒有分開)",
                           adj=_adj_series(sess_r, order_r, cur, r), marks=marks)
        o.append(f"<div>{raw}</div><div>{adj}</div>")
    o.append("</div>")
    return "".join(o)


def _pick_date(sel, dates):
    m = _DATE_RE.search(sel or "")
    return m.group(0) if m and m.group(0) in dates else dates[-1]


def _date_label(d):
    x = dt.date.fromisoformat(d)
    return f"{x:%m/%d}({_WD[x.weekday()]})"


_JS = """<div id='fftip' style='position:fixed;display:none;z-index:9;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:3px 7px;font-size:11px;pointer-events:none'></div>
<script src='/assets/live.js?v=1'></script>
<script>(function(){
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
document.querySelectorAll('.sess[data-live="1"]').forEach(function(el){LiveFrag({el:el,url:el.dataset.u,interval:15000,onSwap:bind})});
})();</script>"""


def render_futflow_frag(root="MXF", sel=""):
    """即時更新用:只回傳單一盤別('day|2026-10-20' 或 'night|2026-10-20')那一段的圖。"""
    _refresh()
    if not _LOADING["done"]:
        return "<div class='meta'>載入中…</div>"
    per, last_ts, ws = _snapshot()
    stype, _, d = (sel or "").partition("|")
    cur = (stype, d)
    if cur not in _order_keys(per):
        return ""
    return _section(cur, per, last_ts, ws, _now())


def render_futflow(root: str = "MXF", sel: str = "") -> str:
    """每天一頁:/futflow(最新一天)或 /futflow?s=2026-10-20。舊連結的 root=… 與 s=day|… 一律導向該日。"""
    _refresh()
    if not _LOADING["done"]:
        msg = _LOADING["err"] or "載入最近幾個交易日的逐筆成交中(首次約 1 分鐘),請稍候…"
        return _head("<meta http-equiv='refresh' content='8'>") + f"<h3>期貨內外盤</h3><div class='meta'>{html_mod.escape(msg)}</div></body></html>"
    per, last_ts, ws = _snapshot()
    order = _order_keys(per)
    if not order:
        return _head() + "<h3>期貨內外盤</h3><div class='meta'>沒有足夠資料的盤別</div></body></html>"
    dates = sorted({k[1] for k in order})
    d = _pick_date(sel, dates)
    i = dates.index(d)
    now = _now()
    status, in_session = market_status(now)
    o = [f"<h3>期貨內外盤 · {_date_label(d)} <span class='meta ffclk'>{now:%H:%M:%S}</span><span class='chip {'on' if in_session else 'off'}'>{html_mod.escape(status)}</span></h3>", "<div>"]
    if i > 0:
        o.append(f"<a class='tab' href='/futflow?s={dates[i - 1]}'>◀ {_date_label(dates[i - 1])}</a>")
    for x in reversed(dates[-8:]):
        o.append(f"<a class='tab{' on' if x == d else ''}' href='/futflow?s={x}'>{_date_label(x)}</a>")
    if i < len(dates) - 1:
        o.append(f"<a class='tab' href='/futflow?s={dates[i + 1]}'>{_date_label(dates[i + 1])} ▶</a>")
    o.append("</div>")
    for k in [k for k in order if k[1] == d]:
        live = _is_active(k, now)
        o.append(f"<div class='sess' data-live='{1 if live else 0}' data-u='/futflowfrag?s={k[0]}|{k[1]}'>{_section(k, per, last_ts, ws, now)}</div>")
    return _head() + "".join(o) + _JS + "</body></html>"
