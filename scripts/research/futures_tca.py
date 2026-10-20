#!/usr/bin/env python
"""個股期貨真實滑價記錄（TCA）。唯讀：只讀成交事件與買賣簿，不碰 DB／下單。

資料：富邦成交推播 cache/tmf_channel/tick_seconds/futopt_fill_events_{日}.jsonl
      （futopt_order status=10＝委託進場；futopt_filled＝成交，以 order_no 串接）
      買賣簿 cache/{root}_books、現股／台指逐筆 → 重播 fairvalue_kalman.Filter 取得當秒校正合理價。
每筆『有成交的個股期貨委託』輸出一列帳本（~/goldenstocks-data/data/order/futures_tca.jsonl，依 order_no 去重）：
  - 進場當下：買一/賣一/中價、合理價(K2)、台指是否觸發、委託限價是否可立即成交(跨價差)
  - 成本：成交價相對進場中價／合理價的偏差，單位 tick 與 NT$/口（正＝多付／少賣）
  - 逆選擇：成交後 10s/60s 中價相對成交價（負＝成交後行情往不利方向走）
只涵蓋日盤（09:00:15–13:24）；夜盤與盤前只記限價與成交價。
用法： PYTHONPATH=src .venv/bin/python scripts/research/futures_tca.py [--since 2026-10-01]
樣本很小時只做描述，不下結論（見 docs/analysis-claim-discipline.md）。
"""
from __future__ import annotations

import argparse
import bisect
import glob
import json
import math
import os
from datetime import datetime

import numpy as np

import fairvalue_kalman as fk

CACHE = fk.CACHE
LEDGER = os.path.join(os.path.expanduser("~/goldenstocks-data"), "data", "order", "futures_tca.jsonl")


def tick_size(p):  # 與 biglot/fairvalue_live.tick_size 同表（2026-10-20 以買賣簿實測）
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 2500 else 5.0


def _epoch(ts):
    return datetime.fromisoformat(ts).timestamp()


def load_events(since):
    orders, fills = {}, []
    for f in sorted(glob.glob(f"{CACHE}/tmf_channel/tick_seconds/futopt_fill_events_*.jsonl")):
        if f[-16:-6] < since:
            continue
        for line in open(f):
            r = json.loads(line); p = r.get("payload")
            if not isinstance(p, dict):
                continue
            if r["kind"] == "futopt_order[1]" and p.get("status") == 10:
                orders[p["order_no"]] = (r["ts"], p)
            elif r["kind"] == "futopt_filled[1]":
                fills.append((r["ts"], p))
    return orders, fills


def contracts():
    calib = json.load(open(os.path.join(CACHE, "..", "data", "cache", "pit_universe_tick", "_live_calib.json")))["universe"]
    uni = fk.universe()
    return {r["fut_code"]: dict(sid=r["sid"], root=uni.get(r["sid"]), size=float(r.get("contract_size") or 2000))
            for r in calib if r.get("fut_code") and uni.get(r["sid"])}


def books(root, day):
    t, b, a = [], [], []
    for line in open(f"{CACHE}/{root}_books/{root}_books_{day}.jsonl"):
        if '"day"' not in line or '"stale": true' in line:
            continue
        r = json.loads(line)
        bb, aa = r.get("bids"), r.get("asks")
        if r.get("session") != "day" or r.get("stale") or not bb or not aa or bb[0]["price"] >= aa[0]["price"]:
            continue
        t.append(r["book_time"] / 1e6); b.append(bb[0]["price"]); a.append(aa[0]["price"])
    return np.array(t), np.array(b), np.array(a)


def replay(day, sid, root, params):
    """回傳每秒 (fair價, 報價年齡秒, 台指自上次報價累計bps)，索引 i＝秒數−09:00:15。"""
    g = fk.load_day(day, {sid: root}).get(sid)
    if not g:
        return None
    tx, mid, sp = g
    f = fk.Filter([params[sid][k] for k in fk.PARAM_NAMES]); n = len(tx)
    fair = np.full(n, np.nan); age = np.full(n, np.nan); mv = np.full(n, np.nan)
    for i in range(1, n):
        f.step(i, (tx[i] - tx[i - 1]) * 1e4, tx[i], mid[i], sp[i])
        if f.x is not None:
            fair[i] = math.exp(f.x / 1e4); age[i] = i - f.last; mv[i] = (tx[i] - f.tx_at) * 1e4
    return fair, age, mv


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--since", default="2026-09-03"); a = ap.parse_args()
    orders, fills = load_events(a.since); cons = contracts()
    pj = sorted(glob.glob(f"{CACHE}/fairvalue/params_*.json"))
    params = json.load(open(pj[-1]))["params"] if pj else None
    done = set()
    if os.path.exists(LEDGER):
        done = {json.loads(l)["order_no"] for l in open(LEDGER)}
    byday = {}
    for ts, p in fills:
        c = cons.get(p["symbol"][2:] if p["symbol"].startswith("FI") else p["symbol"])
        if c and p["order_no"] not in done and p["order_no"] in orders:
            byday.setdefault(ts[:10], []).append((ts, p, c))
    rows = []
    for day, items in sorted(byday.items()):
        cache_bk, cache_rp = {}, {}
        t0 = datetime.fromisoformat(day).replace(tzinfo=fk.TZ, hour=9, minute=0, second=15).timestamp()
        for fts, fp, c in items:
            ots, op = orders[fp["order_no"]]
            sign = 1 if "Buy" in fp["buy_sell"] else -1
            to, tf = _epoch(ots), _epoch(fts); lim, fill = op["price"], fp["filled_price"]
            row = dict(order_no=fp["order_no"], day=day, sid=c["sid"], side="買" if sign > 0 else "賣", limit=lim, fill=fill,
                       order_ts=ots, fill_ts=fts, wait_s=round(tf - to, 2), size=c["size"], session="day")
            if not (0 <= to - t0 < fk.N_SEC):
                row["session"] = "off"; rows.append(row); continue
            if c["root"] not in cache_bk:
                cache_bk[c["root"]] = books(c["root"], day)
            bt, bb, ba = cache_bk[c["root"]]
            k = bisect.bisect_right(bt, to) - 1
            if k < 0:
                row["session"] = "no_book"; rows.append(row); continue
            bid, ask = float(bb[k]), float(ba[k]); mid = (bid + ask) / 2; tk = tick_size(mid)
            row.update(bid=bid, ask=ask, mid=mid, tick=tk, book_age_s=round(to - bt[k], 1),
                       marketable=bool(lim >= ask if sign > 0 else lim <= bid))
            cost = lambda ref: sign * (fill - ref)                       # 正＝多付／少賣
            row["slip_mid_t"] = round(cost(mid) / tk, 2); row["slip_mid_nt"] = round(cost(mid) * c["size"])
            for h in (10, 60):
                j = bisect.bisect_right(bt, tf + h) - 1
                if j >= 0 and bt[j] > tf:
                    row[f"markout{h}_t"] = round(sign * ((bb[j] + ba[j]) / 2 - fill) / tk, 2)   # 負＝成交後往不利方向走
            if params and c["sid"] in params:
                key = (day, c["sid"])
                if key not in cache_rp:
                    cache_rp[key] = replay(day, c["sid"], c["root"], params)
                rp = cache_rp[key]
                i = int(to - t0)
                if rp is not None and 1 <= i < len(rp[0]) and rp[0][i] == rp[0][i]:
                    fair = float(rp[0][i]); trig = bool(rp[1][i] >= fk.MIN_GAP and abs(rp[2][i]) >= fk.TRIGGER_BPS)
                    row.update(fair=round(fair, 3), fair_vs_mid_t=round((fair - mid) / tk, 2), trigger=trig,
                               slip_fair_t=round(cost(fair) / tk, 2), limit_vs_fair_t=round(sign * (lim - fair) / tk, 2))
            rows.append(row)
    if rows:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    allrows = [json.loads(l) for l in open(LEDGER)] if os.path.exists(LEDGER) else []
    print(f"新增 {len(rows)} 筆；帳本共 {len(allrows)} 筆（個股期貨有成交的委託）→ {LEDGER}")
    d = [r for r in allrows if r.get("slip_mid_t") is not None]
    if not d:
        print("日盤且有買賣簿的樣本 0 筆"); return
    def line(name, xs):
        if xs:
            s = [r["slip_mid_t"] for r in xs]; m = [r["slip_mid_nt"] for r in xs]
            print(f"{name:14s} n={len(xs):3d}  進場中價滑價 平均 {np.mean(s):+.2f} tick（NT${np.mean(m):+,.0f}/口） 中位 {np.median(s):+.2f}")
    print("正＝比進場當下中價多付／少賣；0＝以中價成交；負＝比中價好")
    line("全部", d); line("跨價差(馬上成)", [r for r in d if r["marketable"]]); line("掛在價差內/外", [r for r in d if not r["marketable"]])
    t = [r for r in d if r.get("trigger")]
    if t:
        line("台指觸發時下單", t)
    for h in (10, 60):
        mo = [r[f"markout{h}_t"] for r in d if f"markout{h}_t" in r]
        if mo:
            print(f"成交後{h}秒 中價走勢 平均 {np.mean(mo):+.2f} tick（負＝逆向選擇）n={len(mo)}")
    if len(d) < 30:
        print("⚠ 樣本不足 30 筆，只做描述，不得下結論。")


if __name__ == "__main__":
    main()
