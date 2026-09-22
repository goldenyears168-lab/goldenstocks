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
TX_Z_CUT = -1.0
START_HM, END_ENTRY_HM, END_HM = "09:15", "13:00", "13:35"
LOOP_SEC = float(os.environ.get("ZSHADOW_LOOP", "2"))


def _now():
    return datetime.now(TZ)


class Stock:
    def __init__(self, sid):
        self.sid = sid
        self.t, self.px = [], []
        self.grid_next = None          # 下一個要算的 3 分報酬格點時間
        self.r3 = []                   # 30s 抽樣 3 分報酬歷史(供 σ)
        self.last_ev = -1e9
        self.scan = 0                  # 已檢查到的 tick index

    def add(self, ts, px):
        self.t.append(ts)
        self.px.append(px)

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
                    st.add(datetime.fromisoformat(rec["ts"]).timestamp(), float(dd["price"]))
                    new[sid] = True
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
                if st.px[i] > min(w):
                    continue
                mv = st.px[i] / max(w) - 1
                if mv > -MINABS:
                    continue
                sig = st.sigma(T)
                if sig is None or abs(mv) < Z * sig:
                    continue
                st.last_ev = T
                ev = {"sid": sid, "name": names[sid], "T": datetime.fromtimestamp(T, TZ).strftime("%H:%M:%S"), "T_ts": T,
                      "trig_px": st.px[i], "move_bps": round(mv * 1e4, 1), "sigma_bps": round(sig * 1e4, 1), "z": round(abs(mv) / sig, 2),
                      "entry_t": None, "entry_px": None, "exit_t": None, "exit_px": None, "reason": None, "ret_bps": None,
                      "hold_sec": None, "tx_z_exit": None, "scan_from": i + 1}
                events.append(ev)
                opened.append(ev)
                changed = True
                print(f"{_now():%H:%M:%S} 觸發 {sid}{names[sid]} {ev['T']} 幅{ev['move_bps']} z{ev['z']}", flush=True)
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
                el = tk - te
                sp = ev["sigma_bps"] / 1e4 * pe
                reason = None
                if tx_z is not None and tx_z <= TX_Z_CUT:
                    reason = "台指逆勢"
                elif el >= MAXH:
                    reason = "到期"
                elif el >= MINH:
                    w0 = bisect.bisect_left(st.t, tk - WIN)
                    seg = st.px[w0:k + 1]
                    if max(seg) - min(seg) <= FRAC * sp and p > pe:
                        reason = "盤整"
                if reason:
                    ev.update({"exit_t": datetime.fromtimestamp(tk, TZ).strftime("%H:%M:%S"), "exit_px": p, "reason": reason,
                               "ret_bps": round((p / pe - 1) * 1e4, 1), "hold_sec": int(el), "tx_z_exit": None if tx_z is None else round(tx_z, 2)})
                    ev.pop("scan_from", None)
                    opened.remove(ev)
                    changed = True
                    print(f"{_now():%H:%M:%S} 出場 {ev['sid']} {reason} {ev['ret_bps']:+.1f}bps 持{el:.0f}s", flush=True)
                    break
            else:
                ev["scan_from"] = len(st.t)
        # 收盤強制結清
        if hm >= "13:30":
            for ev in list(opened):
                st = stocks[ev["sid"]]
                if ev["entry_px"] and st.px:
                    p = st.px[-1]
                    ev.update({"exit_t": hm, "exit_px": p, "reason": "收盤", "ret_bps": round((p / ev["entry_px"] - 1) * 1e4, 1),
                               "hold_sec": int(st.t[-1] - ev["entry_ts"])})
                    opened.remove(ev)
                    changed = True
        if changed or time.time() - lastwrite > 60:
            write()
            lastwrite = time.time()
        time.sleep(LOOP_SEC)
    write()
    n = [e for e in events if e.get("ret_bps") is not None]
    if n:
        print(f"{_now():%H:%M:%S} 今日 {len(n)} 筆,均 {sum(e['ret_bps'] for e in n)/len(n):+.1f}bps", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
