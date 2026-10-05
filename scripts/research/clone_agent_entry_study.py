#!/usr/bin/env python3
"""進場掛價 × 未成交撤單 × 下一個訊號邏輯 × 回檔參考高點 的研究(CA-R1 → CA-R1.1)。

輸入:`scan_v2/hw{0,30,60}_dense/`(逐分鐘全記的候選卡,冷卻留給本程式事後重放)。
每張卡的掛價:t1=買一再下1檔(正確期貨 tick) / b1=買一 / b2=買二 / b3=買三(取卡片當下的買方五檔)。
成交:db.simulate_queue 的排隊模擬(送出延遲 5 秒);TTL 內沒成交就撤單 → 同一檔等下一個訊號。
出場:CA-R1.1 出場(90 分後賣一、每 5 分鐘改價、不提早、不強制平倉,可留倉過夜)。
保證金:送出即占用,直到成交後平倉 / 撤單 / 收盤;跨日連續;上限 147 萬、先到先得。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_agent_day_backtest as db  # noqa: E402
import clone_agent_exit_backtest as X  # noqa: E402
import clone_agent_exit_v2 as V  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

S = X.S
OUT = X.OUT
DAYS = X.DAYS
OOS = X.OOS_DAYS
CAP, FEE = 1_470_000, 176
COOL = 20 * 60
CLOSE = "13:45:00"
LEVELS = ("t1", "b1", "b2", "b3")
EXIT_P = {"mode": "ask", "groups": ()}
TZ = db.TZ


def tick(px):
    for lim, t in ((10, .01), (50, .05), (100, .1), (500, .5)):
        if px < lim:
            return t
    return 1.


def load_dense(variant, members, lo, hi):
    rows = []
    for d in DAYS:
        p = S / "scan_v2" / variant / f"candidates_{d}.jsonl"
        if not p.exists():
            continue
        fp = json.loads((db.CACHE / f"biglot_live_watch/futprice_{d}.json").read_text(encoding="utf-8"))
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            if c["sid"] not in members:
                continue
            root = db.resolve_root((fp.get(c["sid"]) or {}).get("sym", ""), d)
            lad = [x[0] for x in c.get("bid_ladder") or []]
            lv = {"t1": c["price"] if c.get("bid_ladder") else None,
                  "b1": lad[0] if len(lad) > 0 else None, "b2": lad[1] if len(lad) > 1 else None,
                  "b3": lad[2] if len(lad) > 2 else None}
            rows.append({**c, "root": root, "lv": lv, "now": db.epoch(d, c["time"])})
    rows.sort(key=lambda r: (r["now"], r["sid"]))
    return rows


class Sim:
    def __init__(self):
        self.tr, self.bk, self.fill_memo, self.exit_memo = {}, {}, {}, {}
        cards, self.R, self.mk, self.G = V.load_all2()

    def data(self, root, day):
        k = (root, day)
        if k not in self.tr:
            self.tr[k] = db.load_trades(root, day)
            self.bk[k] = db.load_books(root, day)
        return self.tr[k], self.bk[k]

    def fill(self, root, day, t0, L, qmode):
        k = (root, day, t0, L, qmode)
        if k not in self.fill_memo:
            tr, bk = self.data(root, day)
            if not tr or not bk:
                self.fill_memo[k] = (None, None)
            else:
                tf, px, *_ = db.simulate_queue(tr, bk, L, t0, qmode, db.epoch(day, CLOSE))
                self.fill_memo[k] = (tf, px)
        return self.fill_memo[k]

    def exit(self, c, tf, entry, margin):
        k = (c["day"], c["root"], c["sid"], tf, entry)
        if k not in self.exit_memo:
            hms = datetime.fromtimestamp(tf, db.pd.Timestamp(0, tz=TZ).tz).strftime("%H:%M:%S")
            row = pd.DataFrame([{"cid": 0, "day": c["day"], "time": c["time"], "sid": c["sid"], "name": c["uname"], "root": c["root"],
                                 "shares": c["shares"], "margin": margin, "qpro_fill": hms, "qpro_entry": entry}])
            for key in ((c["day"], c["root"]),):
                if key not in self.mk:
                    self.mk[key] = V.Mkt2(c["root"], c["day"])
            t = V.run_all(row, self.mk, self.G, EXIT_P)
            self.exit_memo[k] = t.iloc[0].to_dict() if len(t) else None
        return self.exit_memo[k]


def run_cfg(sim, cards, level, ttl, one, rearm, qmode="qpro", lo=350, hi=2000, cap=CAP):
    items, last_sig, trades, orders = [], {}, [], []
    stat = {"signals": 0, "no_level": 0, "band": 0, "one": 0, "margin": 0}
    for c in cards:
        now, sid = c["now"], c["sid"]
        ls = last_sig.get(sid)
        if ls and now - ls[0] < COOL and not (rearm and ls[1] is not None and now >= ls[1]):
            continue
        last_sig[sid] = (now, None)
        stat["signals"] += 1
        L = c["lv"][level]
        if L is None:
            stat["no_level"] += 1
            continue
        if level == "t1":
            L = round(c["lv"]["b1"] - tick(c["lv"]["b1"]), 2) if c["lv"]["b1"] else None
            if L is None:
                stat["no_level"] += 1
                continue
        if not (lo <= L <= hi):
            stat["band"] += 1
            continue
        t0 = now + 5
        mg = L * c["shares"] * c["rate"]
        act = [k for k in items if k["t0"] <= t0 < k["end"]]
        if one and any(k["sid"] == sid for k in act):
            stat["one"] += 1
            continue
        if sum(k["m"] for k in act) + mg > cap:
            stat["margin"] += 1
            continue
        tf, px = sim.fill(c["root"], c["day"], t0, L, qmode)
        dl = None if ttl is None else t0 + ttl * 60
        if tf is not None and dl is not None and tf > dl:
            tf = None
        close_t = db.epoch(c["day"], CLOSE)
        o = {"day": c["day"], "sid": sid, "name": c["uname"], "time": c["time"], "L": L, "stars": c["stars"],
             "d30": (c.get("dist_refs") or {}).get("30"), "filled": tf is not None}
        if tf is None:
            end = min(dl, close_t) if dl is not None else close_t
            items.append({"t0": t0, "end": end, "m": mg, "sid": sid})
            if rearm and ttl is not None:
                last_sig[sid] = (now, end)
            orders.append(o)
            continue
        r = sim.exit(c, tf, px, mg)
        end = r["exit_t"] if r and r.get("closed") else float("inf")
        items.append({"t0": t0, "end": end, "m": mg, "sid": sid})
        o.update(wait=(tf - t0) / 60, entry=px, net=r["net"], closed=bool(r["closed"]), overnight=bool(r.get("overnight")),
                 bps=r["bps"], gross=r["gross"], cost=r["cost"], exit_day=r.get("exit_day"))
        orders.append(o)
        trades.append(o)
    return orders, trades, stat


def summ(label, orders, trades, stat):
    n, f = len(orders), len(trades)
    net = np.array([t["net"] for t in trades]) if trades else np.array([])
    cl = [t for t in trades if t["closed"]]
    by = {}
    for t in trades:
        by[t["day"]] = by.get(t["day"], 0) + t["net"]
    oos = sum(v for k, v in by.items() if k in OOS)
    return {"設定": label, "送出": n, "成交": f, "成交率": round(f / n, 2) if n else 0,
            "平均等待分": round(float(np.mean([t["wait"] for t in trades])), 1) if trades else 0,
            "淨損益": int(net.sum()) if f else 0, "每筆": int(net.mean()) if f else 0,
            "勝率": round(float((net > 0).mean()), 2) if f else 0,
            "樣本內": int(net.sum() - oos) if f else 0, "樣本外1005": int(oos),
            "已平倉": len(cl), "留倉過夜": sum(t["overnight"] for t in cl),
            "未平倉": f - len(cl), "逐日": {k: int(v) for k, v in sorted(by.items())}, **{f"擋_{k}": v for k, v in stat.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "entry_study_CA-R1.1.json"))
    a = ap.parse_args()
    R = load_rules()
    members = universe_sids(R)
    lo, hi = R["universe"]["price_band"]
    sim = Sim()
    res = {}
    cards0 = load_dense("hw0_dense", members, lo, hi)
    print("hw0 dense 成員卡", len(cards0), flush=True)
    pd.set_option("display.width", 250, "display.max_columns", 40)
    # ① 進場掛價 × TTL × 同檔政策 × 冷卻重置
    tab = []
    for level in LEVELS:
        for ttl in (None, 5, 10, 20):
            for one in (False, True):
                for rearm in ((False, True) if ttl else (False,)):
                    lab = f"{level}|ttl={ttl or '收盤'}|{'單檔' if one else '可疊'}|{'撤單即重武裝' if rearm else '冷卻20分'}"
                    o, t, st = run_cfg(sim, cards0, level, ttl, one, rearm)
                    r = summ(lab, o, t, st)
                    r.update(level=level, ttl=ttl, one=one, rearm=rearm)
                    tab.append(r)
                    print(f"{lab:42s} 送{r['送出']:>3} 成{r['成交']:>3} {r['成交率']:.2f} 淨{r['淨損益']:>8} 每筆{r['每筆']:>6} 勝{r['勝率']:.2f} 內{r['樣本內']:>8} 外{r['樣本外1005']:>7}", flush=True)
    res["entry_grid"] = tab
    # ② 排隊假設敏感度(主要候選另算 qcons / qopt)
    sens = []
    for level in LEVELS:
        for qm in ("qcons", "qpro", "qopt"):
            o, t, st = run_cfg(sim, cards0, level, 10, False, True, qmode=qm)
            r = summ(f"{level}|ttl=10|{qm}", o, t, st)
            r.update(level=level, qmode=qm)
            sens.append(r)
            print(f"[敏感度] {r['設定']:30s} 送{r['送出']:>3} 成{r['成交']:>3} {r['成交率']:.2f} 淨{r['淨損益']:>8}", flush=True)
    res["queue_sens"] = sens
    # ③ 回檔參考高點(以現行進場 t1、收盤前一直掛、可疊 為基準)
    refs = []
    for var, lab in (("hw0_dense", "當日高(現行)"), ("hw60_dense", "近60分高"), ("hw30_dense", "近30分高")):
        cs = load_dense(var, members, lo, hi)
        for level in ("t1", "b1"):
            o, t, st = run_cfg(sim, cs, level, None, False, False)
            r = summ(f"{lab}|{level}", o, t, st)
            r.update(ref=lab, level=level, 卡片數=st["signals"])
            refs.append(r)
            print(f"[高點] {lab:10s} {level} 卡{st['signals']:>3} 送{r['送出']:>3} 成{r['成交']:>3} 淨{r['淨損益']:>8} 每筆{r['每筆']:>6} 內{r['樣本內']:>8} 外{r['樣本外1005']:>7}", flush=True)
    res["ref_high"] = refs
    # ④ 現行規則(當日高)的訊號,事後依「近30分高回檔」切新鮮/陳舊
    o, t, st = run_cfg(sim, cards0, "t1", None, False, False)
    split = {}
    for nm, fn in (("近30分高回檔≥0.5%(新鮮)", lambda x: x["d30"] is not None and x["d30"] <= -0.5),
                   ("近30分高回檔<0.5%(高點已舊)", lambda x: x["d30"] is None or x["d30"] > -0.5)):
        tt = [x for x in t if fn(x)]
        oo = [x for x in o if fn(x)]
        split[nm] = summ(nm, oo, tt, {})
        s_ = split[nm]
        print(f"[新鮮度] {nm:24s} 送{s_['送出']:>3} 成{s_['成交']:>3} 淨{s_['淨損益']:>8} 每筆{s_['每筆']:>6}")
    res["freshness_split"] = split
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
