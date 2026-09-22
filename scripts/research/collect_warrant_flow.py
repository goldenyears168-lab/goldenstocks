#!/usr/bin/env python
"""盤中權證多空成交額 → warrantflow_{date}.json,供 biglot dashboard「權證多空」欄。唯讀。

資料路線(2026-09-22 探測定案):
- 權證清單:富邦 SDK ``stock.intraday.tickers(type="WARRANT")``(上市+上櫃 ≈4.5 萬檔),
  欄位只有 symbol/name、**沒有標的欄位** → 用 36 檔標的簡稱做**名稱前綴對映**
  (台股權證命名固定=標的簡稱+發行商+流水+購/售;長名優先避免「南亞」吃掉「南亞科」;
  別名:去『*』、去『-KY』、京元電子→京元電)。實測對到 ≈6.4k 檔/33+ 標的。
  SDK 只在啟動時用一次就 logout,**不佔盤中富邦連線額度**。
- 盤中報價:Fugle snapshot 不含權證(type 僅 COMMONSTOCK)、逐檔 REST 太慢、ws 訂閱撞額度 →
  用 **TWSE MIS 批次**(``collect_limitup_books.fetch``,45 檔/請求,內建節流),上市 ``tse_``/上櫃 ``otc_``
  皆回 累計量 v / 價 z / 五檔。≈145 請求一輪 ≈2.5 分鐘;零量權證(多數)兩輪後降頻(每 4 輪一次)。
- 指標:每輪 Δ累計量 × 價 × 1000 = 該權證成交額增量。兩套並存:
  (a) **未簽號**:按商品多空(購+牛=call、售+熊=put)累進 → call_*/put_*(活動量)。
  (b) **主動方簽號**:z ≥ 賣一 → 主動買、z ≤ 買一 → 主動賣、夾在中間用 tick rule(對上輪 z),
      仍無法判定則對半;多方 = 主動買 call + 主動賣 put,空方 = 主動賣 call + 主動買 put → bull_*/bear_*。
      **「權證做多」必須看 (b)**;(a) 只是活動量,散戶倒認購也算 call 成交。
  皆為滾動 5 分 / 30 分 + 全日。快照粒度=一輪(暖機後 <1 分),同一輪內多筆成交共用一個簽號,屬近似。
  **描述性資料、尚未回測**,不當訊號。

用法:PYTHONPATH=src .venv-fubon/bin/python scripts/research/collect_warrant_flow.py [--smoke K]
      --smoke K:只跑前 K 批、一輪即退(測管線用)。13:35 自動退出。
"""
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2] / "src"))
sys.path.insert(0, str(_HERE.parent))
from stock_db import DATA_DIR  # noqa: E402
from collect_limitup_books import fetch  # noqa: E402  (MIS 批次,含節流/重試)

TZ = ZoneInfo("Asia/Taipei")
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
OUT_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch"
END_HHMM = "13:35"
BATCH = 45
DEAD_EVERY = 4           # 零量權證每幾輪才輪詢一次
WARMUP_SWEEPS = 2        # 前幾輪全掃,之後才分死活
ALIAS = {"2449": ["京元電"]}   # 權證簡稱與宇宙名不同者

BULL = {"購", "牛"}
BEAR = {"售", "熊"}


def _now():
    return datetime.now(TZ)


def _side(sym, nm):
    if "售" in nm or sym.endswith("P"):
        return "售"
    if "熊" in nm or sym.endswith("B"):
        return "熊"
    if "牛" in nm or sym.endswith("C"):
        return "牛"
    return "購"


def build_map(names):
    """SDK 列權證 → 名稱前綴對映到宇宙 sid。回 {sid: [{sym, ex, name, side}]}。"""
    from order.fubon_session import connect_fubon, safe_logout  # 只在此用一次
    cands = []
    for sid, nm in names.items():
        alts = {nm, nm.rstrip("*"), nm.replace("-KY", "")} | set(ALIAS.get(sid, []))
        for a in alts:
            if a:
                cands.append((a, sid))
    cands.sort(key=lambda x: -len(x[0]))           # 長名優先
    sess = connect_fubon(realtime=True)
    stk = sess.sdk.marketdata.rest_client.stock
    out = defaultdict(list)
    try:
        for ex, pre in (("TWSE", "tse"), ("TPEx", "otc")):
            d = stk.intraday.tickers(type="WARRANT", exchange=ex)
            rows = d.get("data") if isinstance(d, dict) else d
            for r in rows or []:
                sym, nm = str(r.get("symbol") or ""), str(r.get("name") or "")
                for a, sid in cands:
                    if nm.startswith(a):
                        out[sid].append({"sym": sym, "ex": pre, "name": nm, "side": _side(sym, nm)})
                        break
    finally:
        safe_logout(sess)
    return dict(out)


def _px(row):
    for k in ("z",):
        try:
            v = float(row.get(k))
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    try:
        a = float((row.get("a") or "").split("_")[0])
        b = float((row.get("b") or "").split("_")[0])
        if a > 0 and b > 0:
            return (a + b) / 2
    except (TypeError, ValueError):
        pass
    try:
        return float(row.get("y")) or None
    except (TypeError, ValueError):
        return None


def _f(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _aggr(row, last_z):
    """主動方:+1 買 / −1 賣 / 0 無法判定(對半)。"""
    z = _f(row.get("z"))
    if z is None:
        return 0
    a1 = _f((row.get("a") or "").split("_")[0])
    b1 = _f((row.get("b") or "").split("_")[0])
    if a1 and z >= a1:
        return 1
    if b1 and z <= b1:
        return -1
    if last_z:
        return 1 if z > last_z else (-1 if z < last_z else 0)
    return 0


def main():
    smoke = 0
    if "--smoke" in sys.argv:
        smoke = int(sys.argv[sys.argv.index("--smoke") + 1])
    cal = json.loads(CALIB.read_text())
    names = {r["sid"]: r["name"] for r in cal["universe"]}
    today = _now().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mapf = OUT_DIR / f"warrant_map_{today}.json"
    if mapf.exists():
        wmap = json.loads(mapf.read_text())
    else:
        wmap = build_map(names)
        mapf.write_text(json.dumps(wmap, ensure_ascii=False), encoding="utf-8")
    sym_info = {}                                   # sym -> (sid, side, ex)
    for sid, lst in wmap.items():
        for w in lst:
            sym_info[w["sym"]] = (sid, w["side"], w["ex"])
    n_map = len(sym_info)
    print(f"{_now():%H:%M:%S} 權證對映 {n_map} 檔 / {len(wmap)} 標的", flush=True)

    last_v = {}                                     # sym -> 上輪累計量(張)
    last_z = {}                                     # sym -> 上輪成交價(tick rule 用)
    dead = set()
    events = []                                     # (epoch秒, sid, inst多/空, dirn+1/-1/0, amt) 只留最近30分
    dayamt = defaultdict(float)                     # (sid,inst) -> 未簽號成交額
    daysig = defaultdict(float)                     # (sid,'多'/'空') -> 簽號後 多方/空方 主動額
    out = OUT_DIR / f"warrantflow_{today}.json"
    start = _now().strftime("%H:%M:%S")
    sweep = 0
    while smoke or _now().strftime("%H:%M") < END_HHMM:   # smoke 模式不受時窗限制(一輪即退)
        sweep += 1
        t0 = time.monotonic()
        if sweep <= WARMUP_SWEEPS:
            syms = list(sym_info)
        else:
            syms = [s for s in sym_info if s not in dead or sweep % DEAD_EVERY == 0]
        lastamt = defaultdict(float)
        now = _now()
        tnow = now.timestamp()
        batches = [syms[i:i + BATCH] for i in range(0, len(syms), BATCH)]
        if smoke:
            batches = batches[:smoke]
        for chs in batches:
            rows = fetch([f"{sym_info[s][2]}_{s}.tw" for s in chs])
            for r in rows:
                sym = str(r.get("c") or "")
                if sym not in sym_info:
                    continue
                try:
                    v = float(r.get("v") or 0)
                except (TypeError, ValueError):
                    v = 0.0
                if v <= 0:
                    dead.add(sym)
                else:
                    dead.discard(sym)
                prev = last_v.get(sym)
                last_v[sym] = v
                if prev is None:
                    continue                        # 首次觀測只建基準,不把整天灌進一桶
                dv = v - prev
                if dv <= 0:
                    continue
                px = _px(r)
                if not px:
                    continue
                dirn = _aggr(r, last_z.get(sym))
                zz = _f(r.get("z"))
                if zz:
                    last_z[sym] = zz
                sid, side, _ = sym_info[sym]
                amt = dv * px * 1000
                inst = "多" if side in BULL else "空"
                events.append((tnow, sid, inst, dirn, amt))
                dayamt[(sid, inst)] += amt
                lastamt[(sid, inst)] += amt
                # 簽號:買call/賣put=多方;賣call/買put=空方;無法判定對半
                bullness = dirn * (1 if inst == "多" else -1)
                if bullness > 0:
                    daysig[(sid, "多")] += amt
                elif bullness < 0:
                    daysig[(sid, "空")] += amt
                else:
                    daysig[(sid, "多")] += amt / 2
                    daysig[(sid, "空")] += amt / 2
        # 滾動窗:5分 / 30分(以每輪起始時戳計;只保留最近30分的事件)
        events = [e for e in events if e[0] > tnow - 1800]
        w5, w30 = defaultdict(float), defaultdict(float)       # 未簽號 (sid,inst)
        s5, s30 = defaultdict(float), defaultdict(float)       # 簽號   (sid,'多'/'空')
        for ts, sid_, inst, dirn, a in events:
            in5 = ts > tnow - 300
            w30[(sid_, inst)] += a
            if in5:
                w5[(sid_, inst)] += a
            bullness = dirn * (1 if inst == "多" else -1)
            parts = ((("多", a),) if bullness > 0 else (("空", a),) if bullness < 0
                     else (("多", a / 2), ("空", a / 2)))
            for k, v in parts:
                s30[(sid_, k)] += v
                if in5:
                    s5[(sid_, k)] += v
        snap = {"_meta": {"start": start, "sweep": sweep, "t": now.strftime("%H:%M:%S"),
                          "polled": len(syms), "mapped": n_map, "sec": round(time.monotonic() - t0, 1)}}
        for sid in wmap:
            nc = sum(1 for w in wmap[sid] if w["side"] in BULL)
            np_ = sum(1 for w in wmap[sid] if w["side"] in BEAR)
            snap[sid] = {"call_day": dayamt[(sid, "多")], "put_day": dayamt[(sid, "空")],
                         "call_5": w5[(sid, "多")], "put_5": w5[(sid, "空")],
                         "call_30": w30[(sid, "多")], "put_30": w30[(sid, "空")],
                         "bull_5": s5[(sid, "多")], "bear_5": s5[(sid, "空")],
                         "bull_30": s30[(sid, "多")], "bear_30": s30[(sid, "空")],
                         "bull_day": daysig[(sid, "多")], "bear_day": daysig[(sid, "空")],
                         "call_last": lastamt[(sid, "多")], "put_last": lastamt[(sid, "空")],
                         "n_call": nc, "n_put": np_, "t": now.strftime("%H:%M:%S")}
        try:
            out.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"寫檔失敗: {exc}", flush=True)
        top = sorted(((daysig[(s, "多")] - daysig[(s, "空")], s) for s in wmap), reverse=True)[:3]
        print(f"{_now():%H:%M:%S} 第{sweep}輪 掃{len(syms)}檔 {time.monotonic()-t0:.0f}s · "
              f"死{len(dead)} · 全日簽號淨多前3: " + " ".join(f"{names.get(s, s)}{a/1e4:+,.0f}萬" for a, s in top),
              flush=True)
        if smoke:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
