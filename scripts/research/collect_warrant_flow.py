#!/usr/bin/env python
"""盤中權證多空成交額 → warrantflow_{date}.json(v2:富邦 REST 活躍子集輪詢;不再使用 TWSE MIS)。唯讀。

v1(MIS 全掃 7.5k 檔)於 2026-09-23 造成 TWSE MIS IP 封鎖並打掛生產收集器(見記憶 twse-mis-rate-ban-incident)。
v2 設計(同日實測):
- **名單**:啟動時抓**前一交易日** TWSE `MI_INDEX?type=0999/0999P`(每列含標的代號、成交金額;自動往回找最近交易日,
  快取到 scratch/warrant_daily/twse/{date}.json 可重用)+ TPEx `dailyQuotes?type=EW`(7 開頭權證,名稱前綴對映)。
  只留對到 36 檔標的、前一日成交額 >0 的權證,依成交額取前 TOPN(預設 1000)。已到期的當日 404 即標死。
- **輪詢**:富邦 `stock.intraday.quote`(實測 15 req/s 無節流;上線用 RATE=8 req/s,1000 檔 ≈2 分/輪),與 MIS 不同配額。
  quote 含 `total.tradeValue/tradeVolume`(累計 → Δ 即成交增量)與 `lastTrade{price,bid,ask}`(簽號:價≥賣一買、≤買一賣、
  中間 tick rule、無法判定對半)。零量權證 09:30 後每 4 輪一次。429/連續錯誤 → 退讓 60s。
- **指標/輸出**與 v1 相同:call_*/put_*(活動量)、bull_*/bear_*(簽號:買call+賣put=多方)各 5分/30分/全日 + n_call/n_put/t。
用法:PYTHONPATH=src .venv-fubon/bin/python scripts/research/collect_warrant_flow.py [--smoke K]
"""
import json
import os
import ssl
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2] / "src"))
from stock_db import DATA_DIR  # noqa: E402
from order.fubon_session import connect_fubon, safe_logout  # noqa: E402

TZ = ZoneInfo("Asia/Taipei")
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
OUT_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch"
WD = DATA_DIR.parent / "scratch" / "warrant_daily"
END_HHMM = "13:35"
TOPN = int(os.environ.get("WARRANT_TOPN", "1000"))
RATE = float(os.environ.get("WARRANT_RATE", "8"))        # req/s
DEAD_EVERY = 4
ALIAS = {"2449": ["京元電"]}
BULL = {"購", "牛"}
_SSL = ssl.create_default_context()
_SSL.verify_flags &= ~ssl.VERIFY_X509_STRICT
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _now():
    return datetime.now(TZ)


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _get_json(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
        return json.loads(r.read())


def _twse_day(d):
    """前一交易日 TWSE 權證行情(0999 認購 / 0999P 認售),快取。回 None 表非交易日/失敗。"""
    f = WD / "twse" / f"{d}.json"
    if f.exists():
        return json.loads(f.read_text())
    rec = {}
    for typ in ("0999", "0999P"):
        p = _get_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d.replace('-', '')}&type={typ}&response=json")
        if p.get("stat") != "OK":
            return None
        rows = []
        for t in p.get("tables") or []:
            fl = [str(x).strip() for x in (t.get("fields") or [])]
            if "證券代號" in fl and "標的代號" in fl:
                ix = {n: i for i, n in enumerate(fl)}
                for it in t.get("data") or []:
                    rows.append({"code": it[ix["證券代號"]], "name": it[ix["證券名稱"]], "amt": it[ix["成交金額"]], "und": it[ix["標的代號"]]})
        rec[typ] = rows
        time.sleep(1.0)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rec, ensure_ascii=False))
    return rec


def _tpex_day(d):
    f = WD / "tpex" / f"{d}.json"
    if f.exists():
        return json.loads(f.read_text())
    try:
        p = _get_json(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={d.replace('-', '/')}&type=EW&response=json")
    except Exception:  # noqa: BLE001
        return None
    rows = []
    for t in p.get("tables") or []:
        fl = [str(x).strip() for x in (t.get("fields") or [])]
        ci = next((i for i, n in enumerate(fl) if "代號" in n), None)
        ni = next((i for i, n in enumerate(fl) if "名稱" in n), None)
        ai = next((i for i, n in enumerate(fl) if "成交金額" in n), None)
        if ci is None or ai is None:
            continue
        for it in t.get("data") or []:
            code = str(it[ci]).strip()
            if len(code) == 6 and code[0] == "7":
                rows.append({"code": code, "name": it[ni] if ni is not None else "", "amt": it[ai]})
    if not rows:
        return None
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rows, ensure_ascii=False))
    return rows


def _side(code, nm, typ=None):
    if typ == "0999P" or "售" in nm or code.endswith("P") or "熊" in nm or code.endswith("B"):
        return "售" if ("售" in nm or typ == "0999P" or code.endswith("P")) else "熊"
    return "牛" if ("牛" in nm or code.endswith("C")) else "購"


def build_universe(names):
    """回 (wmap {sid:[{sym,ex,name,side,amt}]}, ref_date)。名單=前一交易日成交額>0、依成交額取前 TOPN。"""
    cands = []
    for sid, nm in names.items():
        for a in {nm, nm.rstrip("*"), nm.replace("-KY", "")} | set(ALIAS.get(sid, [])):
            if a:
                cands.append((a, sid))
    cands.sort(key=lambda x: -len(x[0]))
    d = _now().date()
    for back in range(1, 8):
        ref = (d - timedelta(days=back)).isoformat()
        tw = _twse_day(ref)
        if tw is None:
            continue
        tp = _tpex_day(ref) or []
        rows = []
        for typ, lst in tw.items():
            for r in lst:
                if r.get("und") in names and _num(r.get("amt")) > 0:
                    rows.append((r["code"], "tse", r["name"], _side(r["code"], r["name"], typ), _num(r["amt"]), r["und"]))
        for r in tp:
            if _num(r.get("amt")) <= 0:
                continue
            for a, sid in cands:
                if str(r.get("name", "")).startswith(a):
                    rows.append((r["code"], "otc", r["name"], _side(r["code"], r["name"]), _num(r["amt"]), sid))
                    break
        rows.sort(key=lambda x: -x[4])
        wmap = defaultdict(list)
        for code, ex, nm, side, amt, sid in rows[:TOPN]:
            wmap[sid].append({"sym": code, "ex": ex, "name": nm, "side": side, "amt": amt})
        return dict(wmap), ref
    raise RuntimeError("找不到最近 7 天的 TWSE 權證行情")


def _aggr(lt, last_px):
    """主動方:+1 買 / −1 賣 / 0 無法判定。lt = quote['lastTrade']。"""
    if not isinstance(lt, dict):
        return 0
    p, a, b = lt.get("price"), lt.get("ask"), lt.get("bid")
    if not p:
        return 0
    if a and p >= a:
        return 1
    if b and p <= b:
        return -1
    if last_px:
        return 1 if p > last_px else (-1 if p < last_px else 0)
    return 0


def main():
    smoke = 0
    if "--smoke" in sys.argv:
        smoke = int(sys.argv[sys.argv.index("--smoke") + 1])
    cal = json.loads(CALIB.read_text())
    names = {r["sid"]: r["name"] for r in cal["universe"]}
    today = _now().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wmap, ref = build_universe(names)
    (OUT_DIR / f"warrant_map_{today}.json").write_text(json.dumps(wmap, ensure_ascii=False), encoding="utf-8")
    info = {}
    for sid, lst in wmap.items():
        for w in lst:
            info[w["sym"]] = (sid, w["side"])
    syms_all = list(info)
    if smoke:
        syms_all = syms_all[:smoke]
    print(f"{_now():%H:%M:%S} 名單(依 {ref} 成交額前 {TOPN}):{len(syms_all)} 檔 / {len(wmap)} 標的;RATE={RATE} req/s", flush=True)
    sess = connect_fubon(realtime=True)
    stk = sess.sdk.marketdata.rest_client.stock
    last_val, last_px, dead, zero = {}, {}, set(), set()
    events = []
    dayamt, daysig = defaultdict(float), defaultdict(float)
    out = OUT_DIR / f"warrantflow_{today}.json"
    start = _now().strftime("%H:%M:%S")
    sweep = 0
    consec_err = 0
    try:
        while smoke or _now().strftime("%H:%M") < END_HHMM:
            sweep += 1
            t0 = time.monotonic()
            hm = _now().strftime("%H:%M")
            syms = [s for s in syms_all if s not in dead and (s not in zero or hm < "09:30" or sweep % DEAD_EVERY == 0)]
            now = _now()
            tnow = now.timestamp()
            lastamt = defaultdict(float)
            n_err = 0
            nxt = time.monotonic()
            for s in syms:
                nxt += 1.0 / RATE
                d = nxt - time.monotonic()
                if d > 0:
                    time.sleep(d)
                try:
                    q = stk.intraday.quote(symbol=s)
                    consec_err = 0
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc)
                    if "404" in msg or "Not Found" in msg:
                        dead.add(s)
                        continue
                    n_err += 1
                    consec_err += 1
                    if "429" in msg or consec_err >= 10:
                        print(f"{_now():%H:%M:%S} REST 節流/連錯 {consec_err}({msg[:60]}),退讓 60s", flush=True)
                        time.sleep(60)
                        consec_err = 0
                        nxt = time.monotonic()
                    continue
                tot = q.get("total") or {}
                val = float(tot.get("tradeValue") or 0)
                vol = float(tot.get("tradeVolume") or 0)
                if vol <= 0:
                    zero.add(s)
                else:
                    zero.discard(s)
                prev = last_val.get(s)
                last_val[s] = val
                lt = q.get("lastTrade") or {}
                dirn = _aggr(lt, last_px.get(s))
                if isinstance(lt, dict) and lt.get("price"):
                    last_px[s] = lt["price"]
                if prev is None or val <= prev:
                    continue
                amt = val - prev
                sid, side = info[s]
                inst = "多" if side in BULL else "空"
                events.append((tnow, sid, inst, dirn, amt))
                dayamt[(sid, inst)] += amt
                lastamt[(sid, inst)] += amt
                bl = dirn * (1 if inst == "多" else -1)
                if bl > 0:
                    daysig[(sid, "多")] += amt
                elif bl < 0:
                    daysig[(sid, "空")] += amt
                else:
                    daysig[(sid, "多")] += amt / 2
                    daysig[(sid, "空")] += amt / 2
            # 滾動窗
            events = [e for e in events if e[0] > tnow - 1800]
            w5, w30 = defaultdict(float), defaultdict(float)
            s5, s30 = defaultdict(float), defaultdict(float)
            for ts, sid_, inst, dirn, a in events:
                in5 = ts > tnow - 300
                w30[(sid_, inst)] += a
                if in5:
                    w5[(sid_, inst)] += a
                bl = dirn * (1 if inst == "多" else -1)
                parts = ((("多", a),) if bl > 0 else (("空", a),) if bl < 0 else (("多", a / 2), ("空", a / 2)))
                for k, v in parts:
                    s30[(sid_, k)] += v
                    if in5:
                        s5[(sid_, k)] += v
            snap = {"_meta": {"start": start, "sweep": sweep, "t": now.strftime("%H:%M:%S"), "polled": len(syms),
                              "mapped": len(syms_all), "dead": len(dead), "zero": len(zero), "err": n_err,
                              "sec": round(time.monotonic() - t0, 1), "ref": ref, "src": "fubon_rest"}}
            for sid in wmap:
                nc = sum(1 for w in wmap[sid] if w["side"] in BULL)
                np_ = len(wmap[sid]) - nc
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
            top = sorted(((daysig[(s_, "多")] - daysig[(s_, "空")], s_) for s_ in wmap), reverse=True)[:3]
            print(f"{_now():%H:%M:%S} 第{sweep}輪 掃{len(syms)}檔 {time.monotonic()-t0:.0f}s · 死{len(dead)} 零{len(zero)} 錯{n_err} · "
                  f"全日簽號淨多前3: " + " ".join(f"{names.get(s_, s_)}{a/1e4:+,.0f}萬" for a, s_ in top), flush=True)
            if smoke and sweep >= 2:
                break
    finally:
        safe_logout(sess)
    return 0


if __name__ == "__main__":
    sys.exit(main())
