#!/usr/bin/env python3
"""研究變體「立刻掛、退兩檔、撤單即重新武裝」(預先登記的單一規格,不掃參數)。事後描述性,不是樣本外。

訊號:同 CA-R1 v1.2.0(scan_v3/dense2 逐分鐘全記 → ①③④排名濾網 → 20 分冷卻,撤單後重置)。
掛價:卡片當下買一(bid_ladder[0])再下 N 檔(期貨 tick 表),掛出後不改價。
撤單:(a) 掛出後 TTL 分鐘仍未成交;(b) 之後任一分鐘 ①③④ 排名不再成立(scan_v3/trace 逐分鐘流量排名,不看②⑤)。
      撤單動作在該分鐘 + 送單延遲秒生效;撤單釋放保證金,且該檔可在撤單後立即重新發卡(不受 20 分冷卻)。
成交:db.simulate_queue(qpro),結束時間 = 撤單時刻。出場:成交後 60 分最後成交價(截 13:45)。保證金 147 萬先到先得。
BASE 組態(1 檔、不撤單、不重武裝)=現行 1.2.0,同一引擎、同一組同分鐘排序種子,做配對比較。
注意:watcher 的 core 是 set,同分鐘發卡順序每次啟動都不同,所以「掃描序」只是其中一次抽籤,以隨機排序平均為準。
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_agent_day_backtest as db  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
OUT = Path.home() / "goldenstocks-data/data/clone_agent"
V3 = S / "scan_v3"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"]
KEEP = 0.70 + 1e-12
COOL = 20 * 60
R = load_rules()
MEMBERS = universe_sids(R)
LO, HI = R["universe"]["price_band"]
CAP, FEE, TAX = R["capital"]["margin_cap"], R["cost"]["fee_per_lot_per_side"], 0.00002
CLOSE_HMS = "13:45:00"
FLOWS = ("big_day", "big30", "ret30")


def tick_below(p):
    q = p - 1e-9
    for lim, t in ((10, .01), (50, .05), (100, .1), (500, .5)):
        if q < lim:
            return t
    return 1.


def down(p, n):
    for _ in range(n):
        p = round(p - tick_below(p), 2)
    return p


def flow_ok(c):
    fl = c.get("flow") or {}
    return all(fl.get(k) and fl[k][1] is not None and fl[k][1] <= KEEP for k in FLOWS)


def load_cards(day):
    cards, trace = [], {}
    for ln in (V3 / "dense2" / f"candidates_{day}.jsonl").read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        c = json.loads(ln)
        c["sid"] = str(c["sid"])
        if c["sid"] in MEMBERS and LO <= c["price"] <= HI and c.get("bid_ladder"):
            cards.append(c)
    p = V3 / "trace" / f"candidates_{day}.jsonl"
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            c = json.loads(ln)
            trace[(c["time"], str(c["sid"]))] = flow_ok(c)
    return cards, trace


class Day:
    def __init__(self, day):
        self.day = day
        self.cards, self.trace = load_cards(day)
        self.fp = json.loads((db.CACHE / f"biglot_live_watch/futprice_{day}.json").read_text(encoding="utf-8"))
        self.close_t = db.epoch(day, CLOSE_HMS)
        self.data, self.memo = {}, {}
        for c in self.cards:
            c["root"] = db.resolve_root((self.fp.get(c["sid"]) or {}).get("sym", ""), day)
            c["now"] = db.epoch(day, c["time"])
        self.minutes = sorted({c["time"] for c in self.cards} | {t for t, _ in self.trace})

    def tb(self, root):
        if root not in self.data:
            tr = db.load_trades(root, self.day) or []
            bk = db.load_books(root, self.day) or []
            self.data[root] = (tr, [x[0] for x in tr], bk, [x[0] for x in bk])
        return self.data[root]

    def fill(self, root, t0, L, end_t):
        k = (root, t0, L, end_t)
        if k not in self.memo:
            tr, tts, bk, bts = self.tb(root)
            if not tr or not bk:
                self.memo[k] = None
            else:
                i = bisect.bisect_right(bts, t0) - 1
                j = bisect.bisect_right(bts, end_t)
                if i < 0:
                    self.memo[k] = None
                else:
                    a, b = bisect.bisect_right(tts, t0), bisect.bisect_right(tts, end_t)
                    tf, px, *_ = db.simulate_queue(tr[a:b], bk[i:j], L, t0, "qpro", end_t)
                    self.memo[k] = None if tf is None else (tf, px)
        return self.memo[k]

    def exit(self, root, tf, shares, entry, hold_min=60):
        tr, tts, *_ = self.tb(root)
        t1 = min(tf + hold_min * 60, self.close_t)
        j = max(bisect.bisect_left(tts, tf), 0)
        k = max(bisect.bisect_right(tts, t1) - 1, j)
        x = tr[k][1]
        gross = (x - entry) * shares
        return t1, x, gross - (2 * FEE + TAX * (entry + x) * shares)

    def ext_share(self, root, now, win=300):
        """決策時刻前 win 秒的期貨成交量中,外盤(成交價>=當時賣一)的比例;沒資料回 None。"""
        k = ("ext", root, now)
        if k not in self.memo:
            tr, tts, *_ = self.tb(root)
            a, b = bisect.bisect_left(tts, now - win), bisect.bisect_right(tts, now)
            tot = ext = 0.0
            for t, px, bid, ask, sz in tr[a:b]:
                if bid and ask:
                    tot += sz
                    if px >= ask:
                        ext += sz
            self.memo[k] = ext / tot if tot > 0 else None
        return self.memo[k]

    def next_fail(self, sid, T, hold_to):
        """卡片分鐘 T 之後、hold_to(epoch)之前,第一個 ①③④ 不成立的分鐘(epoch);沒有回 None。"""
        for m in self.minutes:
            e = db.epoch(self.day, m)
            if e <= db.epoch(self.day, T):
                continue
            if e > hold_to:
                return None
            if not self.trace.get((m, sid), False):
                return e
        return None


def run_day(D, cfg, order_seed):
    """回傳 (orders, trades)。cfg: ticks, ttl(分|None), flow_cancel, rearm, lag。"""
    lag = cfg["lag"]
    by_min = {}
    for c in D.cards:
        by_min.setdefault(c["time"], []).append(c)
    rng = np.random.default_rng(order_seed) if order_seed is not None else None
    items, last_card, rearm_at, orders = [], {}, {}, []
    for m in sorted(by_min):
        group = by_min[m]
        if rng is not None:
            group = [group[i] for i in rng.permutation(len(group))]
        for c in group:
            if not flow_ok(c):
                continue
            sid, now = c["sid"], c["now"]
            ls = last_card.get(sid)
            if ls is not None and now - ls < COOL and not (cfg["rearm"] and sid in rearm_at and now + lag >= rearm_at[sid]):
                continue
            last_card[sid] = now
            rearm_at.pop(sid, None)
            if not c["root"]:
                continue
            b1 = c["bid_ladder"][0][0]
            ask = float(re.search(r"賣一 ([\d.]+)", c["price_note"]).group(1))
            spread_ticks = round((ask - b1) / tick_below(ask), 3)
            ext = D.ext_share(c["root"], now)
            mode = cfg.get("px", "ticks")
            ask_trig = spread_ticks <= 1.0001 and ext is not None and ext >= cfg.get("thr", 0.9)
            if mode == "ticks":
                L = down(b1, cfg["ticks"])
            elif mode == "ask":
                L = ask
            else:                                   # 條件式外盤:近5分鐘外盤>=九成且價差僅一檔 → 掛賣一,否則退回 fb 檔
                L = ask if ask_trig else down(b1, cfg["fb"])
            t0 = now + lag
            mg = L * c["shares"] * c["rate"]
            cap = cfg.get("cap", CAP)
            if sum(k["m"] for k in items if k["t0"] <= t0 < k["end"]) + mg > cap:
                orders.append({"day": D.day, "sid": sid, "name": c["uname"], "time": m, "status": "margin_block", "ask_trig": bool(ask_trig)})
                continue
            end_t = D.close_t
            reason = "close"
            if cfg["ttl"] is not None:
                end_t, reason = min(end_t, t0 + cfg["ttl"] * 60), "ttl"
            if cfg["flow_cancel"]:
                nf = D.next_fail(sid, m, end_t)
                if nf is not None and nf + lag < end_t:
                    end_t, reason = nf + lag, "flow"
            f = D.fill(c["root"], t0, L, end_t)
            o = {"day": D.day, "sid": sid, "name": c["uname"], "time": m, "L": L, "t0": t0, "stars": c["stars"],
                 "spread_bps": 1e4 * (ask - b1) / b1, "ext": ext, "ask_trig": bool(ask_trig),
                 "dist30": (c.get("dist_refs") or {}).get("30"), "shares": c["shares"], "root": c["root"]}
            if f is None:
                items.append({"t0": t0, "end": end_t, "m": mg})
                if cfg["rearm"] and reason in ("ttl", "flow"):
                    rearm_at[sid] = end_t
                orders.append({**o, "status": "cancel_" + reason if reason != "close" else "unfilled_close", "cancel_t": end_t})
                continue
            tf, px = f
            t1, x, net = D.exit(c["root"], tf, c["shares"], px)
            items.append({"t0": t0, "end": t1, "m": mg})
            orders.append({**o, "status": "filled", "tf": tf, "entry": px, "exit": x, "exit_t": t1, "net": net,
                           "bps": 1e4 * (x / px - 1), "wait_min": (tf - t0) / 60})
    return orders


def run_all(days, cfg, order_seed):
    out = []
    for D in days:
        out += run_day(D, cfg, None if order_seed is None else order_seed * 100 + DAYS.index(D.day))
    return pd.DataFrame(out)


def digest(o):
    f = o[o.status == "filled"]
    sent = o[o.status != "margin_block"]
    r = {"送出": len(sent), "成交": len(f), "成交率": round(len(f) / len(sent), 3) if len(sent) else 0,
         "撤單_ttl": int((o.status == "cancel_ttl").sum()), "撤單_流量": int((o.status == "cancel_flow").sum()),
         "保證金擋": int((o.status == "margin_block").sum()),
         "淨": int(f.net.sum()) if len(f) else 0, "每筆": int(f.net.mean()) if len(f) else 0,
         "勝率": round(float((f.net > 0).mean()), 2) if len(f) else None,
         "bps均": round(float(f.bps.mean()), 1) if len(f) else None,
         "平均等待分": round(float(f.wait_min.mean()), 2) if len(f) else None,
         "逐日": {d: int(g.net.sum()) for d, g in f.groupby("day")}}
    return r


def evaluate(days, cfg, ties):
    scan = run_all(days, cfg, None)
    r = {"掃描序": digest(scan)}
    if ties:
        rs = [digest(run_all(days, cfg, k + 1)) for k in range(ties)]
        net = np.array([x["淨"] for x in rs])
        r["同分鐘隨機排序"] = {"K": ties, "淨_平均": int(net.mean()), "淨_標準差": int(net.std()),
                         "淨_p5": int(np.percentile(net, 5)), "淨_p95": int(np.percentile(net, 95)),
                         "成交_平均": round(float(np.mean([x["成交"] for x in rs])), 1),
                         "送出_平均": round(float(np.mean([x["送出"] for x in rs])), 1),
                         "成交率_平均": round(float(np.mean([x["成交率"] for x in rs])), 3),
                         "撤單ttl_平均": round(float(np.mean([x["撤單_ttl"] for x in rs])), 1),
                         "撤單流量_平均": round(float(np.mean([x["撤單_流量"] for x in rs])), 1),
                         "每筆_平均": int(np.mean([x["每筆"] for x in rs])),
                         "勝率_平均": round(float(np.mean([x["勝率"] or 0 for x in rs])), 2),
                         "逐日_平均": {d: int(np.mean([x["逐日"].get(d, 0) for x in rs])) for d in DAYS},
                         "逐日_標準差": {d: int(np.std([x["逐日"].get(d, 0) for x in rs])) for d in DAYS},
                         "每次淨損益": [int(v) for v in net]}
    return r, scan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ties", type=int, default=40)
    ap.add_argument("--days", default=",".join(DAYS))
    a = ap.parse_args()
    days = [Day(d) for d in a.days.split(",")]
    print("卡片(名單內+價格帶+有買方五檔)", {D.day: len(D.cards) for D in days}, flush=True)
    miss = sum(1 for D in days for c in D.cards if (c["time"], c["sid"]) not in D.trace)
    chk = sum(1 for D in days for c in D.cards if (c["time"], c["sid"]) in D.trace and D.trace[(c["time"], c["sid"])] != flow_ok(c))
    print(f"對照:dense2 卡片在 trace 缺列 {miss}、①③④判定與 trace 不一致 {chk}", flush=True)
    specs = {
        "BASE 1.2.0 現行(買一下1檔、不撤單、冷卻20分)": dict(ticks=1, ttl=None, flow_cancel=False, rearm=False, lag=5),
        "MAIN 退2檔+5分TTL+流量撤單+重武裝(送單5秒)": dict(ticks=2, ttl=5, flow_cancel=True, rearm=True, lag=5),
        "SENS 同MAIN 送單0秒": dict(ticks=2, ttl=5, flow_cancel=True, rearm=True, lag=0),
        "SENS 同MAIN 送單30秒": dict(ticks=2, ttl=5, flow_cancel=True, rearm=True, lag=30),
        "DIAG-1 只退2檔(不撤單、冷卻20分)": dict(ticks=2, ttl=None, flow_cancel=False, rearm=False, lag=5),
        "DIAG-2 退1檔+5分TTL+流量撤單+重武裝": dict(ticks=1, ttl=5, flow_cancel=True, rearm=True, lag=5),
        "DIAG-3 退2檔+只5分TTL+重武裝(不看流量)": dict(ticks=2, ttl=5, flow_cancel=False, rearm=True, lag=5),
        "DIAG-4 退2檔+只流量撤單+重武裝(無TTL)": dict(ticks=2, ttl=None, flow_cancel=True, rearm=True, lag=5),
    }
    res, led = {}, None
    for name, cfg in specs.items():
        r, scan = evaluate(days, cfg, a.ties)
        res[name] = {"cfg": cfg, **r}
        s, t = r["掃描序"], r.get("同分鐘隨機排序", {})
        print(f"{name}\n  掃描序 送{s['送出']} 成{s['成交']} 率{s['成交率']} ttl撤{s['撤單_ttl']} 流量撤{s['撤單_流量']} 擋{s['保證金擋']} "
              f"淨{s['淨']} 每筆{s['每筆']} 勝{s['勝率']} bps{s['bps均']} 等{s['平均等待分']}分 逐日{s['逐日']}\n  隨機排序 淨均{t.get('淨_平均')} sd{t.get('淨_標準差')} "
              f"p5/p95 {t.get('淨_p5')}/{t.get('淨_p95')} 送{t.get('送出_平均')} 成{t.get('成交_平均')} 率{t.get('成交率_平均')} "
              f"ttl撤{t.get('撤單ttl_平均')} 流量撤{t.get('撤單流量_平均')} 逐日{t.get('逐日_平均')}", flush=True)
        if name.startswith("MAIN"):
            led = scan
    base = np.array(res[next(iter(res))]["同分鐘隨機排序"]["每次淨損益"])
    for name, r in res.items():
        d = np.array(r["同分鐘隨機排序"]["每次淨損益"]) - base
        r["對BASE配對差"] = {"平均": int(d.mean()), "標準誤": int(d.std(ddof=1) / np.sqrt(len(d))), "sd": int(d.std(ddof=1)),
                          "優於BASE比例": round(float((d > 0).mean()), 2)}
        print(f"配對差 {name}: 平均{d.mean():+.0f} se{d.std(ddof=1) / np.sqrt(len(d)):.0f} sd{d.std(ddof=1):.0f} 優於BASE {(d > 0).mean():.0%}")
    (OUT / "entry2_study_CA-R1_v1.2.0.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if led is not None:
        led.to_csv(S / "entry2_main_orders_scanorder.csv", index=False)


if __name__ == "__main__":
    main()
