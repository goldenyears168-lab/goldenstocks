#!/usr/bin/env python
"""z 急殺回彈 影子帳(不顯示、不送單、不進 OOS 記分;只累 20 日 OOS 供事後判定)。

規格(2026-09-23 預先登記,對應記憶 zscore-normalized-spike-reversion;之後不改):
- 事件:3 分報酬 ≤ −3σ 且創 3 分新低、絕對幅度 ≥0.8%;σ = 當日至今 3 分報酬(30s 抽樣)滾動標準差、≥10 樣本;
  每檔 5 分冷卻;只在 09:15~13:00 觸發(開盤 15 分 = 價格發現,兩側皆續行,排除)。
- 進場參考:事件後 ≥5 秒的第一筆成交價(影子=市價假設;被動掛單版待現股 ws 五檔累積後另測)。
- 出場:近 60 秒高低區間 ≤ 0.5σ(換成價格)且持倉獲利 → 出;持有 ≥60 秒才判、最長 10 分到期市價。
- 風控:台指期近月 1 分報酬 ≤ −1σ_TX(σ_TX = 當日 1 分報酬 30s 抽樣滾動 std)→ 立即出。
  台指價來自 futprice_{date}.json 的 "TXF" 鍵(collect_biglot_futprice 每 10s 更新);缺則風控停用並記錄。
- 不用移動停利、不用價格硬停損(回測證明對回彈路徑是毒)。

資料:增量讀 raw_{date}.jsonl(bytes offset,與詳情頁同法),逐筆精度,與回測同一套判定。
輸出:cache/biglot_live_watch/zcrash_shadow_{date}.json = {"_meta":..., "events":[...]},有變動即整檔覆寫。
用法:PYTHONPATH=src .venv/bin/python scripts/research/zcrash_shadow.py   (08:55~13:35 自退)

2026-09-23 追加(純記錄,不改任何進出場規則):**賣盤竭盡點 五檔指標**。每筆事件自觸發起每 30 秒取樣到出場,
讀 cache/stock_books_ws/stock_books_{date}.jsonl(增量、只解析有持倉的檔),記進 ev["book"]:
  sell_pct  近 30 秒主動賣金額占比(逐筆簽號:價≥賣一=買、≤買一=賣、否則 tick rule)
  bid_min / ask_min  五檔買/賣掛單金額 ÷ 近 5 分每分鐘成交額(= 幾分鐘量;<3 真空、>10 牆)
  bid_refill / ask_refill  30 秒內(掛單量變化 + 被吃量)÷ 被吃量(≥1 = 補得比吃得快;被吃量 0 則 None)
  bid_cancel / ask_cancel  30 秒內 max(0, 掛單減少 − 被吃量) ÷ 期初掛單量(= 撤單、非成交消失的比例)
  bge  買深 ≥ 賣深 旗標
另記 trough_t/trough_px(觸發後最低價時點)與 exh_t(首次 sell_pct≤0.40 ∧ bid_refill≥1 連兩樣本 ∧ bid_min≥5)。
候選定義來自 09-23 n=20 案例解剖,≥20 日後才檢定;--smoke SID HH:MM:SS [short] 可離線重放單一事件驗算。

2026-09-24 追加 **鏡像:急拉做空**(預先登記,回測 OOS +11/t2.5,對應同一記憶;與急殺做多各自獨立記分):
- 事件:3 分報酬 ≥ +3σ 且創 3 分新高、絕對幅度 ≥0.8%;其餘(σ、冷卻、時窗)同急殺。ev["side"] = +1(做空)/ −1(做多)。
- 出場:盤整 且持倉獲利(做空 = 價 < 進場價);到期 10 分;風控鏡像 = 台指 1 分報酬 ≥ +1σ_TX → 出。
- 報酬 = −side × (出場/進場 − 1)。
- 五檔指標鏡像:crest_t/crest_px(觸發後最高價)、exh_t = 首次 sell_pct ≥ 0.60(主動買 ≤40%)∧ ask_refill ≥1 連兩樣本 ∧ ask_min ≥5。
"""
import bisect
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DATA_DIR  # noqa: E402

TZ = ZoneInfo("Asia/Taipei")
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
BD = DATA_DIR.parent / "cache" / "biglot_live_watch"
Z, MINABS, LOOK, SAMP, COOL, DELAY = 3.0, 0.008, 180, 30, 300, 5
FRAC, WIN, MINH, MAXH = 0.5, 60, 60, 600
TX_Z_CUT = -1.0            # 急殺做多:台指 1 分 z ≤ −1 → 砍;急拉做空鏡像 = z ≥ +1
START_HM, END_ENTRY_HM, END_HM = "09:15", "13:00", "13:35"
LOOP_SEC = float(os.environ.get("ZSHADOW_LOOP", "2"))
BOOKS_DIR = DATA_DIR.parent / "cache" / "stock_books_ws"
BK_SAMP, BK_FLOW_WIN, BK_HIST = 30, 300, 900      # 取樣間隔 / 流速窗 / 簿歷史保留秒數
EXH_SELL, EXH_REFILL, EXH_MIN = 0.40, 1.0, 5.0    # 竭盡候選門檻(記錄用)


def _now():
    return datetime.now(TZ)


class Stock:
    def __init__(self, sid):
        self.sid = sid
        self.t, self.px = [], []
        self.amt, self.sgn, self.dv = [], [], []   # 逐筆金額 / 主動方(+1買 −1賣 0) / 張數(五檔指標用,不影響規則)
        self.last_px = None
        self.grid_next = None          # 下一個要算的 3 分報酬格點時間
        self.r3 = []                   # 30s 抽樣 3 分報酬歷史(供 σ)
        self.last_ev = -1e9
        self.scan = 0                  # 已檢查到的 tick index
        self.bk_t, self.bk = [], []    # 五檔歷史 (ts) / (bid_sz, ask_sz, bid_notional, ask_notional)

    def add(self, ts, px, dv=0.0, sgn=0):
        self.t.append(ts)
        self.px.append(px)
        self.amt.append(px * dv * 1000)
        self.sgn.append(sgn)
        self.dv.append(dv)

    def add_book(self, ts, bids, asks):
        bsz = sum(float(b.get("size") or 0) for b in bids)
        asz = sum(float(a.get("size") or 0) for a in asks)
        bno = sum(float(b.get("size") or 0) * float(b.get("price") or 0) * 1000 for b in bids)
        ano = sum(float(a.get("size") or 0) * float(a.get("price") or 0) * 1000 for a in asks)
        self.bk_t.append(ts)
        self.bk.append((bsz, asz, bno, ano))
        cut = ts - BK_HIST
        while self.bk_t and self.bk_t[0] < cut:
            self.bk_t.pop(0)
            self.bk.pop(0)

    def book_at(self, T):
        i = bisect.bisect_right(self.bk_t, T) - 1
        return self.bk[i] if i >= 0 else None


    def px_at(self, T):
        i = bisect.bisect_right(self.t, T) - 1
        return self.px[i] if i >= 0 else None

    def update_grid(self, open_t, now_ts):
        if self.grid_next is None:
            self.grid_next = open_t + LOOK
        while self.grid_next <= now_ts - 1:
            a, b = self.px_at(self.grid_next), self.px_at(self.grid_next - LOOK)
            if a and b:
                self.r3.append(a / b - 1)
            self.grid_next += SAMP

    def sigma(self, T):
        # 用 T−LOOK 之前的格點(與回測一致:不含當前窗)
        n = int((T - LOOK - (self.grid_next - SAMP * len(self.r3))) // SAMP) + 1 if self.r3 else 0
        h = self.r3[:max(0, min(n, len(self.r3)))]
        if len(h) < 10:
            return None
        m = sum(h) / len(h)
        return math.sqrt(sum((x - m) ** 2 for x in h) / len(h)) or None


def _side(px, bid, ask, last_px):
    """與儀表板同法:價≥賣一=主動買、≤買一=主動賣、否則 tick rule。"""
    if ask is not None and px >= float(ask):
        return 1
    if bid is not None and px <= float(bid):
        return -1
    if last_px is None:
        return 0
    return 1 if px > last_px else (-1 if px < last_px else 0)


def book_sample(st, S):
    """S 時點的五檔指標(見檔頭);資料不足的欄位為 None。"""
    i1 = bisect.bisect_right(st.t, S)
    i0 = bisect.bisect_left(st.t, S - BK_SAMP)
    buy = sum(st.amt[k] for k in range(i0, i1) if st.sgn[k] > 0)
    sell = sum(st.amt[k] for k in range(i0, i1) if st.sgn[k] < 0)
    cons_bid = sum(st.dv[k] for k in range(i0, i1) if st.sgn[k] < 0)   # 打到買方的張數
    cons_ask = sum(st.dv[k] for k in range(i0, i1) if st.sgn[k] > 0)
    sell_pct = round(sell / (buy + sell), 3) if (buy + sell) > 0 else None
    f0 = bisect.bisect_left(st.t, S - BK_FLOW_WIN)
    flow_pm = sum(st.amt[f0:i1]) / (BK_FLOW_WIN / 60)                   # 近 5 分每分鐘成交額
    now, prev = st.book_at(S), st.book_at(S - BK_SAMP)
    out = {"t": datetime.fromtimestamp(S, TZ).strftime("%H:%M:%S"), "px": st.px_at(S), "sell_pct": sell_pct,
           "bid_min": None, "ask_min": None, "bid_refill": None, "ask_refill": None,
           "bid_cancel": None, "ask_cancel": None, "bge": None}
    if now:
        bsz, asz, bno, ano = now
        if flow_pm > 0:
            out["bid_min"], out["ask_min"] = round(bno / flow_pm, 1), round(ano / flow_pm, 1)
            out["bge"] = bno >= ano
        if prev:
            pb, pa = prev[0], prev[1]
            if cons_bid > 0:
                out["bid_refill"] = round((bsz - pb + cons_bid) / cons_bid, 2)
            if cons_ask > 0:
                out["ask_refill"] = round((asz - pa + cons_ask) / cons_ask, 2)
            if pb > 0:
                out["bid_cancel"] = round(max(0.0, pb - bsz - cons_bid) / pb, 2)
            if pa > 0:
                out["ask_cancel"] = round(max(0.0, pa - asz - cons_ask) / pa, 2)
    return out


def book_update_event(ev, st, now_ts):
    """自觸發起每 30 秒補樣本到 now_ts−2(留資料到達餘裕);更新最低價與竭盡候選時點。"""
    changed = False
    ev.setdefault("book", [])
    nxt = ev.get("bk_next") or (ev["T_ts"] + BK_SAMP)
    end = ev.get("exit_ts") or now_ts - 2
    side = ev.get("side", -1)                      # −1 急殺做多(找賣方竭盡) / +1 急拉做空(找買方竭盡)
    ext_px, ext_t = ("trough_px", "trough_t") if side < 0 else ("crest_px", "crest_t")
    rk, mk = ("bid_refill", "bid_min") if side < 0 else ("ask_refill", "ask_min")
    while nxt <= min(end, now_ts - 2):
        s = book_sample(st, nxt)
        ev["book"].append(s)
        # 觸發後極值(做多記最低價、做空記最高價)
        i0 = bisect.bisect_left(st.t, ev["T_ts"])
        i1 = bisect.bisect_right(st.t, nxt)
        if i1 > i0:
            k = (min if side < 0 else max)(range(i0, i1), key=lambda j: st.px[j])
            if ev.get(ext_px) is None or (st.px[k] < ev[ext_px] if side < 0 else st.px[k] > ev[ext_px]):
                ev[ext_px], ev[ext_t] = st.px[k], datetime.fromtimestamp(st.t[k], TZ).strftime("%H:%M:%S")
        # 竭盡候選:攻方占比≤0.40 ∧ 守方補單率≥1 連兩樣本 ∧ 守方深度≥5 分
        # (做多:攻方=主動賣、守方=買簿;做空:攻方=主動買 ⇔ sell_pct≥0.60、守方=賣簿)
        # 補單率 None = 30 秒內沒人打到守方(攻方停手),視為成立;sell_pct None(無成交)同理
        if ev.get("exh_t") is None and len(ev["book"]) >= 2:
            a, b = ev["book"][-1], ev["book"][-2]
            _ref = lambda s: s[rk] is None or s[rk] >= EXH_REFILL  # noqa: E731
            atk_ok = (a["sell_pct"] is None or
                      (a["sell_pct"] <= EXH_SELL if side < 0 else a["sell_pct"] >= 1 - EXH_SELL))
            if atk_ok and _ref(a) and _ref(b) and (a[mk] or 0) >= EXH_MIN:
                ev["exh_t"] = a["t"]
        nxt += BK_SAMP
        changed = True
    ev["bk_next"] = nxt
    return changed


def main():
    cal = json.loads(CALIB.read_text())
    names = {r["sid"]: r["name"] for r in cal["universe"]}
    stocks = {sid: Stock(sid) for sid in names}
    today = _now().strftime("%Y-%m-%d")
    raw = BD / f"raw_{today}.jsonl"
    out = BD / f"zcrash_shadow_{today}.json"
    open_t = datetime.fromisoformat(f"{today}T09:00:00+08:00").timestamp()
    events = []
    if out.exists():                                   # 重啟續接
        try:
            events = json.loads(out.read_text()).get("events", [])
        except Exception:  # noqa: BLE001
            events = []
    off = 0
    books = BOOKS_DIR / f"stock_books_{today}.jsonl"
    boff = None
    opened = [e for e in events if e.get("exit_t") is None]
    tx_samples = []                                    # (epoch, px) 台指
    tx_r1, tx_grid_next = [], None
    tx_warned = False
    lastwrite = 0.0
    print(f"{_now():%H:%M:%S} zcrash shadow 啟動,{len(stocks)} 檔;既有事件 {len(events)}", flush=True)

    def write():
        meta = {"t": _now().strftime("%H:%M:%S"), "n_events": len(events), "open": sum(1 for e in events if e.get("exit_t") is None),
                "spec": {"Z": Z, "minabs": MINABS, "look": LOOK, "frac": FRAC, "win": WIN, "minh": MINH, "maxh": MAXH, "tx_cut": TX_Z_CUT}}
        try:
            out.write_text(json.dumps({"_meta": meta, "events": events}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"寫檔失敗 {exc}", flush=True)

    while _now().strftime("%H:%M") < END_HM:
        now_ts = time.time()
        # ---- 增量讀逐筆 ----
        new = {}
        if raw.exists():
            with open(raw, "rb") as f:
                f.seek(off)
                chunk = f.read()
            nl = chunk.rfind(b"\n")
            if nl != -1:
                off += nl + 1
                for line in chunk[:nl].split(b"\n"):
                    if b'"price"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    dd = (rec.get("payload") or {}).get("data") or {}
                    sid = str(dd.get("symbol", ""))
                    if sid not in stocks or dd.get("isTrial") or dd.get("price") is None or dd.get("volume") is None:
                        continue
                    st = stocks[sid]
                    v = float(dd["volume"])
                    lastv = getattr(st, "lastv", 0.0)
                    if v <= lastv:
                        st.lastv = max(lastv, v)
                        continue
                    st.lastv = v
                    px = float(dd["price"])
                    sgn = _side(px, dd.get("bid"), dd.get("ask"), st.last_px)
                    st.last_px = px
                    st.add(datetime.fromisoformat(rec["ts"]).timestamp(), px, v - lastv, sgn)
                    new[sid] = True
        # ---- 增量讀五檔(只解析有持倉的檔;啟動時從檔尾開始,不回讀 GB 級歷史) ----
        if opened and books.exists():
            if boff is None:
                boff = books.stat().st_size
            watch = {ev["sid"] for ev in opened}
            keys = {sid: f'"symbol": "{sid}"'.encode() for sid in watch}
            with open(books, "rb") as f:
                f.seek(boff)
                chunk = f.read()
            nl = chunk.rfind(b"\n")
            if nl != -1:
                boff += nl + 1
                for line in chunk[:nl].split(b"\n"):
                    hit = next((sid for sid, k in keys.items() if k in line), None)
                    if hit is None:
                        continue
                    try:
                        b = json.loads(line)
                        stocks[hit].add_book(datetime.fromisoformat(b["ts"]).timestamp(), b.get("bids") or [], b.get("asks") or [])
                    except Exception:  # noqa: BLE001
                        continue
        elif boff is None and books.exists():
            boff = books.stat().st_size
        # ---- 台指 ----
        tx_z = None
        try:
            fp = json.loads((BD / f"futprice_{today}.json").read_text())
            tx = fp.get("TXF")
            if tx and tx.get("px"):
                tts = datetime.fromisoformat(f"{today}T{tx['t']}+08:00").timestamp()
                if not tx_samples or tts > tx_samples[-1][0]:
                    tx_samples.append((tts, float(tx["px"])))
        except Exception:  # noqa: BLE001
            pass
        if tx_samples:
            tt = [s[0] for s in tx_samples]
            if tx_grid_next is None:
                tx_grid_next = tx_samples[0][0] + 60
            while tx_grid_next <= tx_samples[-1][0]:
                i = bisect.bisect_right(tt, tx_grid_next) - 1
                j = bisect.bisect_right(tt, tx_grid_next - 60) - 1
                if i >= 0 and j >= 0:
                    tx_r1.append(tx_samples[i][1] / tx_samples[j][1] - 1)
                tx_grid_next += SAMP
            if len(tx_r1) >= 10:
                m = sum(tx_r1) / len(tx_r1)
                sd = math.sqrt(sum((x - m) ** 2 for x in tx_r1) / len(tx_r1))
                j = bisect.bisect_right(tt, tx_samples[-1][0] - 60) - 1
                if sd > 0 and j >= 0:
                    tx_z = (tx_samples[-1][1] / tx_samples[j][1] - 1) / sd
        elif not tx_warned and _now().strftime("%H:%M") > "09:05":
            print(f"{_now():%H:%M:%S} ⚠ futprice 無 TXF 鍵,台指風控停用", flush=True)
            tx_warned = True
        hm = _now().strftime("%H:%M")
        changed = False
        # ---- 觸發偵測(逐筆) ----
        for sid in new:
            st = stocks[sid]
            st.update_grid(open_t, now_ts)
            i0 = st.scan
            for i in range(i0, len(st.t)):
                T = st.t[i]
                hmT = datetime.fromtimestamp(T, TZ).strftime("%H:%M")
                if hmT < START_HM or hmT > END_ENTRY_HM or T - st.last_ev < COOL:
                    continue
                lo = bisect.bisect_left(st.t, T - LOOK)
                w = st.px[lo:i + 1]
                # 急殺(創 3 分新低,做多 side=−1)/ 急拉(創 3 分新高,做空 side=+1)鏡像
                if st.px[i] <= min(w):
                    side, mv = -1, st.px[i] / max(w) - 1
                elif st.px[i] >= max(w):
                    side, mv = +1, st.px[i] / min(w) - 1
                else:
                    continue
                if abs(mv) < MINABS:
                    continue
                sig = st.sigma(T)
                if sig is None or abs(mv) < Z * sig:
                    continue
                st.last_ev = T
                ev = {"sid": sid, "name": names[sid], "side": side, "T": datetime.fromtimestamp(T, TZ).strftime("%H:%M:%S"), "T_ts": T,
                      "trig_px": st.px[i], "move_bps": round(mv * 1e4, 1), "sigma_bps": round(sig * 1e4, 1), "z": round(abs(mv) / sig, 2),
                      "entry_t": None, "entry_px": None, "exit_t": None, "exit_px": None, "reason": None, "ret_bps": None,
                      "hold_sec": None, "tx_z_exit": None, "scan_from": i + 1}
                events.append(ev)
                opened.append(ev)
                changed = True
                print(f"{_now():%H:%M:%S} 觸發 {sid}{names[sid]} {'急拉做空' if side > 0 else '急殺做多'} {ev['T']} 幅{ev['move_bps']} z{ev['z']}", flush=True)
            st.scan = len(st.t)
        # ---- 持倉管理 ----
        for ev in list(opened):
            st = stocks[ev["sid"]]
            k0 = ev.get("scan_from", 0)
            for k in range(k0, len(st.t)):
                tk, p = st.t[k], st.px[k]
                if ev["entry_px"] is None:
                    if tk >= ev["T_ts"] + DELAY:
                        ev["entry_px"], ev["entry_t"], ev["entry_ts"] = p, datetime.fromtimestamp(tk, TZ).strftime("%H:%M:%S"), tk
                        changed = True
                    continue
                pe, te = ev["entry_px"], ev["entry_ts"]
                side = ev.get("side", -1)
                el = tk - te
                sp = ev["sigma_bps"] / 1e4 * pe
                reason = None
                # 與回測一致:台指逆勢風控自進場 30 秒後才開始檢查(2026-09-23 首日 9/10 筆在 0~4 秒被砍=實作偏差)
                # 做多:台指 z ≤ −1 逆勢;做空鏡像:台指 z ≥ +1
                tx_adverse = tx_z is not None and ((tx_z <= TX_Z_CUT) if side < 0 else (tx_z >= -TX_Z_CUT))
                if tx_adverse and el >= 30:
                    reason = "台指逆勢"
                elif el >= MAXH:
                    reason = "到期"
                elif el >= MINH:
                    w0 = bisect.bisect_left(st.t, tk - WIN)
                    seg = st.px[w0:k + 1]
                    if max(seg) - min(seg) <= FRAC * sp and (p > pe if side < 0 else p < pe):
                        reason = "盤整"
                if reason:
                    ev.update({"exit_t": datetime.fromtimestamp(tk, TZ).strftime("%H:%M:%S"), "exit_px": p, "reason": reason,
                               "ret_bps": round(-side * (p / pe - 1) * 1e4, 1), "hold_sec": int(el), "tx_z_exit": None if tx_z is None else round(tx_z, 2),
                               "exit_ts": tk})
                    ev.pop("scan_from", None)
                    # 出場前最後補齊五檔樣本(只記錄,不影響出場判定);exit_ts 保留供 book_update_event 截止
                    try:
                        book_update_event(ev, st, tk + BK_SAMP)
                    except Exception as exc:  # noqa: BLE001
                        print(f"book metrics 失敗 {ev['sid']}: {exc!r}", flush=True)
                    ev.pop("bk_next", None)
                    opened.remove(ev)
                    changed = True
                    print(f"{_now():%H:%M:%S} 出場 {ev['sid']} {reason} {ev['ret_bps']:+.1f}bps 持{el:.0f}s", flush=True)
                    break
            else:
                ev["scan_from"] = len(st.t)
        # ---- 五檔指標取樣(持倉中,每 30 秒;純記錄) ----
        for ev in opened:
            try:
                if book_update_event(ev, stocks[ev["sid"]], now_ts):
                    changed = True
            except Exception as exc:  # noqa: BLE001
                print(f"book metrics 失敗 {ev['sid']}: {exc!r}", flush=True)
        # 收盤強制結清
        if hm >= "13:30":
            for ev in list(opened):
                st = stocks[ev["sid"]]
                if ev["entry_px"] and st.px:
                    p = st.px[-1]
                    ev.update({"exit_t": hm, "exit_px": p, "reason": "收盤",
                               "ret_bps": round(-ev.get("side", -1) * (p / ev["entry_px"] - 1) * 1e4, 1),
                               "hold_sec": int(st.t[-1] - ev["entry_ts"]), "exit_ts": st.t[-1]})
                    ev.pop("bk_next", None)
                    opened.remove(ev)
                    changed = True
        if changed or time.time() - lastwrite > 60:
            write()
            lastwrite = time.time()
        time.sleep(LOOP_SEC)
    write()
    for side, lab in ((-1, "急殺做多"), (1, "急拉做空")):
        n = [e for e in events if e.get("ret_bps") is not None and e.get("side", -1) == side]
        if n:
            print(f"{_now():%H:%M:%S} 今日 {lab} {len(n)} 筆,均 {sum(e['ret_bps'] for e in n)/len(n):+.1f}bps", flush=True)
    return 0


def smoke(sid, hms, day=None):
    """離線重放:讀當日 raw 逐筆 + 五檔,對 sid 在 hms 觸發的事件印出每 30 秒五檔指標(驗算用)。"""
    day = day or _now().strftime("%Y-%m-%d")
    T = datetime.fromisoformat(f"{day}T{hms}+08:00").timestamp()
    st = Stock(sid)
    key = f'"symbol": "{sid}"'.encode()
    with open(BD / f"raw_{day}.jsonl", "rb") as f:
        for line in f:
            if key not in line or b'"price"' not in line:
                continue
            rec = json.loads(line)
            dd = (rec.get("payload") or {}).get("data") or {}
            if dd.get("isTrial") or dd.get("price") is None or dd.get("volume") is None:
                continue
            v = float(dd["volume"]); lastv = getattr(st, "lastv", 0.0)
            if v <= lastv:
                st.lastv = max(lastv, v); continue
            st.lastv = v
            px = float(dd["price"]); sgn = _side(px, dd.get("bid"), dd.get("ask"), st.last_px); st.last_px = px
            st.add(datetime.fromisoformat(rec["ts"]).timestamp(), px, v - lastv, sgn)
    with open(BOOKS_DIR / f"stock_books_{day}.jsonl", "rb") as f:
        for line in f:
            if key not in line:
                continue
            b = json.loads(line)
            ts = datetime.fromisoformat(b["ts"]).timestamp()
            if T - BK_SAMP - 5 <= ts <= T + MAXH + BK_SAMP:
                st.add_book(ts, b.get("bids") or [], b.get("asks") or [])
    side = 1 if "short" in sys.argv else -1
    ev = {"sid": sid, "side": side, "T_ts": T, "exit_ts": T + MAXH}
    book_update_event(ev, st, T + MAXH + BK_SAMP)
    ext = f"最低 {ev.get('trough_px')}@{ev.get('trough_t')}" if side < 0 else f"最高 {ev.get('crest_px')}@{ev.get('crest_t')}"
    print(f"{sid} {'急拉做空' if side > 0 else '急殺做多'} 觸發 {hms} 觸發價 {st.px_at(T)} {ext} 竭盡候選 {ev.get('exh_t')}")
    print(f"{'t':9s}{'px':>8s}{'sell%':>7s}{'bidMin':>8s}{'askMin':>8s}{'bRef':>7s}{'aRef':>7s}{'bCan':>6s}{'aCan':>6s} bge")
    for s in ev["book"]:
        f = lambda x, w: (f"{x:{w}}" if x is not None else f"{'—':>{w}}")
        print(f"{s['t']:9s}{f(s['px'],8)}{f(s['sell_pct'],7)}{f(s['bid_min'],8)}{f(s['ask_min'],8)}{f(s['bid_refill'],7)}{f(s['ask_refill'],7)}{f(s['bid_cancel'],6)}{f(s['ask_cancel'],6)} {s['bge']}")


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        i = sys.argv.index("--smoke")
        _day = next((a for a in sys.argv[i + 3:] if len(a) == 10 and a[4] == "-"), None)
        sys.exit(smoke(sys.argv[i + 1], sys.argv[i + 2], _day))
    sys.exit(main())
