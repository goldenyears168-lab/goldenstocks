#!/usr/bin/env python3
"""盤中5分鐘/30分鐘大戶-散戶流向監控 · 讀 biglot-live-watch 的 raw jsonl · 唯讀。

用法（PYTHONPATH=src）:
  python scripts/research/biglot_5m_monitor.py            # 報最新完成的5分窗+滾動30分窗
  python scripts/research/biglot_5m_monitor.py --summary  # 收盤全日總結+隔日觀察名單

方法論（2026-09-09/10 盤中實戰定版，見 memory quiet-accumulation-retail-arrival-verdict）:
· 互斥三桶：大戶=單筆金額>=500萬；散戶=1張且<500萬；中實戶=其餘。
  1張>=500萬的高價股散戶結構性不可測（標示、不給假數字）。
· Δvolume 為單筆量；isTrial/isOpen/isClose 排除；**每檔當日第一筆 dv>0 = 開盤競價
  彙總量且常不帶 isOpen 旗標，跳過方向歸屬**（2026-09-10 玉晶光 417 張實測，
  不跳過會把整包競價量誤判成單邊大戶、正負號可翻面）。
· 斷線重連跳量（>90s 無 tick 且單筆 >=5億）排除方向歸屬。
· 市場代理 = 宇宙等權窗報酬。
· 「獨漲+散戶湧入」旗標：窗報酬>0 ∧ 市場<+5bps ∧ 超額>20bps ∧ 散戶參與度較前窗
  跳升 —— 歷史（95檔×127日）下一窗平均回吐 -16bps（平均值非必然，當否決條件用；
  2026-09-10 實戰 3勝2敗）。
· 散戶參與度 = 散戶雙邊成交金額/總金額；報告欄位顯示，健康買盤實測 5~25%、
  擁擠弱勢 35%+。
· 「回檔且大戶在賣」= 窗報酬<-20bps ∧ 大戶淨賣>=3千萬 —— 盤中回檔唯一有方向的
  分界（大戶沒賣的回檔 +0.21% vs 有賣 -0.15%，t≈1.4 未顯著，觀察用）。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")
from stock_db import DATA_DIR  # noqa: E402

TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(TZ).strftime("%Y-%m-%d")
RAW = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{TODAY}.jsonl"
STATE = DATA_DIR.parent / "cache" / "biglot_live_watch" / "_5m_monitor_state.json"
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
BIG_AMT = 10_000_000     # 2026-09-12 使用者定案:大戶=真大戶(>=1000萬)
RETAIL_CAP = 5_000_000   # 散戶定義不動:1張且<500萬(高價股1張大單歸中實不歸散戶)
GAP_JUMP_AMT = 500_000_000
GAP_SECONDS = 90

try:
    _cal = json.load(open(CALIB))
    NAMES = {r["sid"]: r["name"] for r in _cal["universe"]}
    RET_UNMEASURABLE = {r["sid"] for r in _cal["universe"]
                        if r.get("px", 0) * 1000 >= RETAIL_CAP}
except Exception:
    NAMES, RET_UNMEASURABLE = {}, set()


def nm(s):
    return NAMES.get(s, s)


def bucket_key(dt):
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def load_buckets():
    """回傳 (buckets, day_stat)。buckets[sid][bk] = 各桶淨流/雙邊/末價。"""
    lastvol = defaultdict(float)
    last_px = {}
    last_seen = {}
    first_done = set()
    buckets = {}
    day = defaultdict(lambda: {"big": 0., "mid": 0., "ret": 0., "ret2": 0., "tot": 0.,
                               "big_pm": 0., "px0": None, "px": None})
    with open(RAW) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("kind") != "message":
                continue
            ts = rec.get("ts")
            msg = rec["payload"]
            if msg.get("channel") != "trades" or msg.get("event") not in (None, "data"):
                continue
            d = msg.get("data") or {}
            sid, px, vol = str(d.get("symbol", "")), d.get("price"), d.get("volume")
            if not sid or px is None or vol is None or d.get("isTrial"):
                continue
            px, vol = float(px), float(vol)
            dv = vol - lastvol[sid]
            lastvol[sid] = vol
            if dv <= 0:
                continue
            if d.get("isClose"):
                day[sid]["px"] = px
                continue
            if d.get("isOpen"):
                continue
            if sid not in first_done:      # 開盤競價（常無 isOpen 旗標），不計方向
                first_done.add(sid)
                day[sid]["px0"] = px
                continue
            b, a = d.get("bid"), d.get("ask")
            side = 1 if (a is not None and px >= float(a)) else (
                -1 if (b is not None and px <= float(b)) else 0)
            if side == 0:
                p = last_px.get(sid)
                side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
            last_px[sid] = px
            if not ts:
                continue
            t = datetime.fromisoformat(ts)
            gap = sid in last_seen and (t - last_seen[sid]).total_seconds() > GAP_SECONDS
            last_seen[sid] = t
            amt = px * dv * 1000
            bk = bucket_key(t)
            row = buckets.setdefault(sid, {}).setdefault(
                bk, {"big": 0., "retn": 0., "ret2": 0., "midn": 0., "tot": 0., "px": None})
            row["px"] = px
            ds = day[sid]
            if ds["px0"] is None:
                ds["px0"] = px
            ds["px"] = px
            if gap and amt >= GAP_JUMP_AMT:
                continue
            row["tot"] += amt
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
    return buckets, day


def report_window():
    now = datetime.now(TZ)
    buckets, _ = load_buckets()
    all_bks = sorted({bk for m in buckets.values() for bk in m})
    done = [bk for bk in all_bks if bk + timedelta(minutes=5, seconds=15) <= now]
    if not done:
        print("尚無已完成的5分鐘窗。")
        return
    cur = done[-1]
    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE))
        except Exception:
            pass
    if state.get("last") == cur.isoformat():
        print(f"NOOP:{cur.strftime('%H:%M')} 窗已報告過。")
        return
    prev = done[-2] if len(done) >= 2 else None

    rets, rows = [], []
    for sid, m in buckets.items():
        a = m.get(cur)
        p = m.get(prev) if prev else None
        if not a or a["px"] is None:
            continue
        w_ret = (a["px"] / p["px"] - 1) * 10000 if (p and p["px"]) else None
        if w_ret is not None:
            rets.append(w_ret)
        share = a["ret2"] / a["tot"] * 100 if a["tot"] else 0
        share_prev = (p["ret2"] / p["tot"] * 100) if (p and p["tot"]) else None
        rows.append({"sid": sid, "w_ret": w_ret, "big": a["big"], "retn": a["retn"],
                     "share": share,
                     "d_share": share - share_prev if share_prev is not None else None})
    mkt = sum(rets) / len(rets) if rets else 0.0

    def wr(r):
        return f"{r['w_ret']:+.0f}bps" if r["w_ret"] is not None else "—(首窗)"

    def sh(r):
        return "散戶不可測" if r["sid"] in RET_UNMEASURABLE else f"散戶參與{r['share']:.0f}%"

    print(f"===== {cur.strftime('%H:%M')}–{(cur+timedelta(minutes=5)).strftime('%H:%M')} 窗 =====")
    print(f"市場代理: {mkt:+.1f} bps · 有效檔數 {len(rets)}")
    print("\n【大戶淨買 Top5】")
    for r in sorted(rows, key=lambda x: -x["big"])[:5]:
        if r["big"] <= 0:
            break
        print(f"  {r['sid']} {nm(r['sid']):<5} 淨買 {r['big']/1e4:>8.0f}萬  {wr(r)}  {sh(r)}")
    print("【大戶淨賣 Top5】")
    for r in sorted(rows, key=lambda x: x["big"])[:5]:
        if r["big"] >= 0:
            break
        print(f"  {r['sid']} {nm(r['sid']):<5} 淨賣 {r['big']/1e4:>8.0f}萬  {wr(r)}  {sh(r)}")
    print("\n【散戶參與度跳升 Top5】")
    cand = [r for r in rows if r["d_share"] is not None and r["sid"] not in RET_UNMEASURABLE]
    for r in sorted(cand, key=lambda x: -x["d_share"])[:5]:
        if r["d_share"] < 3:
            break
        flag = ""
        if (r["w_ret"] is not None and r["w_ret"] > 20 and r["d_share"] > 5):
            flag = "  ⚠勿追5m(127日:未來30分超額-6bps,cl-t-4.9)"
        print(f"  {r['sid']} {nm(r['sid']):<5} Δ{r['d_share']:+.1f}pp  散戶淨 "
              f"{r['retn']/1e4:+.0f}萬  {wr(r)}{flag}")
    catch = [r for r in rows if r["w_ret"] is not None and r["w_ret"] < -20
             and r["big"] > 3e7]
    if catch:
        print("\n【🟢跌深大戶接（127日+10.8bps/cl-t+2.8,觀察級未達門檻）】")
        for r in sorted(catch, key=lambda x: -x["big"])[:5]:
            print(f"  {r['sid']} {nm(r['sid']):<5} {wr(r)}  大戶淨買 {r['big']/1e4:.0f}萬")

    # 30分鐘窗
    win6 = done[-6:]
    prior = [bk for bk in all_bks if bk < win6[0]]
    base_bk = max(prior) if prior else None
    rows30, rets30 = [], []
    for sid, m in buckets.items():
        big30 = sum(m[bk]["big"] for bk in win6 if bk in m)
        ret2_30 = sum(m[bk]["ret2"] for bk in win6 if bk in m)
        tot30 = sum(m[bk]["tot"] for bk in win6 if bk in m)
        px_end = next((m[bk]["px"] for bk in reversed(win6)
                       if bk in m and m[bk]["px"]), None)
        px_base = (m[base_bk]["px"] if base_bk and base_bk in m and m[base_bk]["px"]
                   else next((m[bk]["px"] for bk in win6 if bk in m and m[bk]["px"]), None))
        r30 = (px_end / px_base - 1) * 10000 if (px_end and px_base) else None
        if r30 is not None:
            rets30.append(r30)
        rows30.append({"sid": sid, "big30": big30, "r30": r30,
                       "share30": ret2_30 / tot30 * 100 if tot30 else None})
    mkt30 = sum(rets30) / len(rets30) if rets30 else 0.0
    span = f"{win6[0].strftime('%H:%M')}–{(win6[-1]+timedelta(minutes=5)).strftime('%H:%M')}"
    print(f"\n===== 30分鐘窗 {span} =====")
    print(f"市場代理(30分): {mkt30:+.1f} bps")

    def sh30(r):
        if r["sid"] in RET_UNMEASURABLE:
            return "散戶不可測"
        return f"散戶參與{r['share30']:.0f}%" if r["share30"] is not None else ""

    def wr30(r):
        return f"{r['r30']:+.0f}bps" if r["r30"] is not None else "—"

    print("【30分 大戶淨買 Top5】")
    for r in sorted(rows30, key=lambda x: -x["big30"])[:5]:
        if r["big30"] <= 0:
            break
        print(f"  {r['sid']} {nm(r['sid']):<5} 淨買 {r['big30']/1e4:>8.0f}萬  {wr30(r)}  {sh30(r)}")
    print("【30分 大戶淨賣 Top5】")
    for r in sorted(rows30, key=lambda x: x["big30"])[:5]:
        if r["big30"] >= 0:
            break
        print(f"  {r['sid']} {nm(r['sid']):<5} 淨賣 {r['big30']/1e4:>8.0f}萬  {wr30(r)}  {sh30(r)}")

    json.dump({"last": cur.isoformat()}, open(STATE, "w"))


def report_summary():
    _, day = load_buckets()
    rows = []
    for sid, s in day.items():
        if not (s["px0"] and s["px"]):
            continue
        rows.append({"sid": sid, "ret_day": (s["px"] / s["px0"] - 1) * 100,
                     "big": s["big"], "big_pm": s["big_pm"], "retn": s["ret"],
                     "share": s["ret2"] / s["tot"] * 100 if s["tot"] else 0})
    tb = sum(r["big"] for r in rows)
    tr = sum(r["retn"] for r in rows)
    tp = sum(r["big_pm"] for r in rows)
    print(f"全日: 大戶 {tb/1e8:+.1f}億  散戶 {tr/1e8:+.1f}億  12:00後大戶 {tp/1e8:+.1f}億")

    def line(r):
        return (f"{r['sid']} {nm(r['sid']):<6} 大戶{r['big']/1e8:+7.2f}億 "
                f"(午後{r['big_pm']/1e8:+6.2f}) 散戶{r['retn']/1e8:+6.2f}億 "
                f"參與{r['share']:.0f}% 日內{r['ret_day']:+.2f}%")

    print("\n=== 全日大戶淨買 Top8 ===")
    for r in sorted(rows, key=lambda x: -x["big"])[:8]:
        print(line(r))
    print("\n=== 全日大戶淨賣 Top8 ===")
    for r in sorted(rows, key=lambda x: x["big"])[:8]:
        print(line(r))
    print("\n=== 隔日偏多候選（午後大戶淨買強，IC≈+0.15 統計傾向非訊號）===")
    for r in sorted(rows, key=lambda x: -x["big_pm"])[:6]:
        print(line(r))
    print("\n=== 隔日危險背離（散戶大買∧大戶大賣，歷史最差配置）===")
    for r in sorted(rows, key=lambda x: x["retn"] - x["big"], reverse=True)[:8]:
        if r["retn"] > 3e7 and r["big"] < -3e7:
            print(line(r))


if __name__ == "__main__":
    if not RAW.exists():
        print(f"STOP:{RAW} 不存在（collector 未啟動或休市）。")
        sys.exit(1)
    if "--summary" in sys.argv:
        report_summary()
    else:
        report_window()
