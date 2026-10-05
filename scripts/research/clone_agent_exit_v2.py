#!/usr/bin/env python3
"""複製人 CA-R1.1(候選)完整出場模擬:90 分後送平倉單、內外盤決定掛價、5 分鐘改價、四個流量條件(%)提早平倉、收盤不強制平倉(留倉過夜)。

進場沿用 CA-R1 凍結規則的成交(cab_*.csv 的 qpro 排隊成交)。出場:
  · 計畫出場 = 成交後 hold 分鐘;提早出場 = 持有期間 30 分鐘組(C∧D)或日級組(A∨B)連續 persist 分鐘成立。
  · 送平倉單:近 30 分鐘內外盤(張數)外盤比 r:r>=hi 掛賣一、r<=lo 掛買一(=吃單)、其間掛中價;每 reprice 分鐘撤單重掛、重新判斷。
  · 日盤 13:45 收盤時未成交的單作廢;持倉與保證金續留到次一交易日 09:00 再送(不強制平倉)。
  · 保證金:全部日子連續計(留倉部位持續占額度),先到先得。
"""
from __future__ import annotations

import bisect
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_agent_day_backtest as db  # noqa: E402
import clone_agent_exit_backtest as X  # noqa: E402

S = X.S
CLOSE_HMS, NEXT_OPEN_HMS = "13:45:00", "09:00:00"
LAG, TAX = 5, 0.00002
C_, D_, A_, B_ = X.BITS["C"], X.BITS["D"], X.BITS["A"], X.BITS["B"]


class Mkt2(X.Mkt):
    """Mkt + 成交內外盤分類(Lee-Ready:價>=賣一外盤、<=買一內盤、其間比中價、等於中價用 tick rule)。"""

    def __init__(self, root, day):
        tr = db.load_trades(root, day) or []
        self.ts = [x[0] for x in tr]
        self.px = np.array([x[1] for x in tr])
        self.bidtick = [x[2] for x in tr]
        self.size = [x[4] for x in tr]
        self.bk = X.load_books2(root, day)
        self.bt = [b[0] for b in self.bk]
        sg, prev_p, prev_s = [], None, 0
        for (_, p, b, a, _) in tr:
            s = 0
            if b and a and a > b:
                s = 1 if p >= a else -1 if p <= b else (1 if p > (a + b) / 2 else -1 if p < (a + b) / 2 else 0)
            if s == 0 and prev_p is not None:
                s = 1 if p > prev_p else -1 if p < prev_p else prev_s
            sg.append(s)
            prev_p, prev_s = p, s
        sz = np.array(self.size)
        sg = np.array(sg)
        self.cs_out = np.concatenate([[0.0], np.cumsum(sz * (sg > 0))])
        self.cs_in = np.concatenate([[0.0], np.cumsum(sz * (sg < 0))])

    def out_ratio(self, t, win=1800):
        i1 = bisect.bisect_right(self.ts, t)
        i0 = bisect.bisect_left(self.ts, t - win)
        o, n = self.cs_out[i1] - self.cs_out[i0], self.cs_in[i1] - self.cs_in[i0]
        return (o / (o + n)) if o + n > 0 else None


def pick_price(M, t, mode, hi, lo):
    bid, ask = M.best_bid(t), M.best_ask(t)
    if ask is None or mode == "bid":
        return bid, "買一", None
    if mode == "ask":
        return ask, "賣一", None
    r = M.out_ratio(t)
    if r is None:
        return bid + math.ceil((ask - bid) / 2 / X.tick_size(ask) - 1e-9) * X.tick_size(ask), "中價(無成交)", None
    if r >= hi:
        return ask, "賣一", r
    if r <= lo:
        return bid, "買一", r
    tk = X.tick_size(ask)
    return min(ask, bid + math.ceil((ask - bid) / 2 / tk - 1e-9) * tk), "中價", r


def work_exit(mk, root, days, d0, t_sub, P):
    """從第 d0 個交易日的 t_sub 開始送平倉單。回傳 dict 或 None(資料結束仍未成交)。"""
    orders, log = 0, []
    d = d0
    while d < len(days):
        M = mk.get((days[d], root))
        close = db.epoch(days[d], CLOSE_HMS)
        t = t_sub if (d == d0 and t_sub is not None) else db.epoch(days[d], NEXT_OPEN_HMS)
        if M is None or not M.ts:
            d += 1
            continue
        while t < close:
            S_px, how, r = pick_price(M, t, P["mode"], P["hi"], P["lo"])
            t_end = min(t + P["reprice"] * 60, close)
            orders += 1
            res = X.sim_sell(M, S_px, t, t_end)
            if len(log) < 12:
                log.append((days[d], round(t - db.epoch(days[d], "00:00:00")), how, S_px, None if r is None else round(r, 2)))
            if res is not None:
                return {"t": res[0], "px": res[1], "day_i": d, "orders": orders, "log": log, "route": how}
            t = t_end
        d += 1
    return {"t": None, "px": None, "day_i": None, "orders": orders, "log": log, "route": None}


def early_trigger(G, day, sid, tf, t_limit, groups, persist_pts):
    g = G[day]
    if sid not in g["sids"]:
        return None, None
    arr = g["flags_pct"][:, g["sids"].index(sid)].astype(int)
    gt = g["t"]
    c30 = ((arr & C_) != 0) & ((arr & D_) != 0)
    cday = (arr & (A_ | B_)) != 0
    out = {}
    for name, m in (("g30", c30), ("gday", cday)):
        if name not in groups:
            continue
        run = 0
        for k in range(len(gt)):
            if gt[k] <= tf:
                run = 0
                continue
            run = run + 1 if m[k] else 0
            if run >= persist_pts:
                if gt[k] < t_limit:
                    out[name] = float(gt[k])
                break
    if not out:
        return None, None
    why = min(out, key=out.get)
    return out[why], why


DEFAULT = {"hold": 90, "groups": ("g30", "gday"), "persist_pts": 11, "mode": "ratio", "hi": 0.55, "lo": 0.45, "reprice": 5,
           "force_close": None, "fee": 176}


def run_all(cards, mk, G, P=None):
    P = {**DEFAULT, **(P or {})}
    days = X.DAYS
    rows = []
    for c in cards.itertuples():
        if not isinstance(c.qpro_fill, str) or not isinstance(c.root, str):
            continue
        day, di = c.day, days.index(c.day)
        tf = db.epoch(day, c.qpro_fill)
        e, sh = float(c.qpro_entry), float(c.shares)
        close = db.epoch(day, CLOSE_HMS)
        t90 = tf + P["hold"] * 60
        why = "time"
        t_dec = t90
        if P["force_close"]:
            fc = db.epoch(day, P["force_close"])
            if t90 > fc:
                t_dec, why = fc, "flat"
        t_trig, g = early_trigger(G, day, c.sid, tf, min(t_dec, close), P["groups"], P["persist_pts"]) if P["groups"] else (None, None)
        if t_trig is not None:
            t_dec, why = t_trig, "early_" + g
        PP = P
        if why == "flat":
            PP = {**P, "mode": "bid"}
        if t_dec >= close:
            why = "carry"
            res = work_exit(mk, c.root, days, di + 1, None, PP) if di + 1 < len(days) else {"t": None, "px": None, "orders": 0, "log": [], "route": None, "day_i": None}
        else:
            res = work_exit(mk, c.root, days, di, t_dec + LAG, PP)
        M0 = mk[(day, c.root)]
        mid = None
        ba, bb = M0.best_ask(min(t_dec + LAG, close - 1)), M0.best_bid(min(t_dec + LAG, close - 1))
        if ba:
            mid = (ba + bb) / 2
        r = {"cid": c.cid, "day": day, "sid": c.sid, "name": c.name, "entry": e, "fill": tf, "why": why, "orders": res["orders"], "route": res["route"],
             "margin": float(c.margin), "notional": e * sh, "t_dec": t_dec, "log": res["log"]}
        if res["t"] is not None:
            xp = res["px"]
            gross = (xp - e) * sh
            r.update(exit_t=res["t"], exit_day=days[res["day_i"]], exit_px=xp, gross=gross, cost=2 * P["fee"] + TAX * (e + xp) * sh, closed=True,
                     overnight=days[res["day_i"]] != day, held_min=(res["t"] - tf) / 60 - (0 if days[res["day_i"]] == day else 0))
            r["net"] = r["gross"] - r["cost"]
            r["bps"] = 1e4 * (xp / e - 1)
            r["shortfall"] = ((mid - xp) * sh) if (mid and days[res["day_i"]] == day) else np.nan
        else:
            lastday = days[-1] if di == len(days) - 1 else days[-1]
            Ml = mk[(lastday, c.root)]
            xp = float(Ml.px[-1])
            r.update(exit_t=None, exit_day=None, exit_px=xp, closed=False, overnight=False,
                     gross=(xp - e) * sh, cost=2 * P["fee"] + TAX * (e + xp) * sh, held_min=np.nan, shortfall=np.nan)
            r["net"] = r["gross"] - r["cost"]
            r["bps"] = 1e4 * (xp / e - 1)
        rows.append(r)
    return pd.DataFrame(rows)


def margin_fcfs(cards, tr, cap):
    """跨日連續保證金先到先得:委託送出起占用,直到平倉成交(留倉過夜持續占);未成交進場單占到當日收盤。"""
    info = {r.cid: r for r in tr.itertuples()}
    items, keep = [], []
    for c in cards.assign(tt0=[db.epoch(d, t) + LAG for d, t in zip(cards.day, cards.time)]).sort_values("tt0").itertuples():
        r = info.get(c.cid)
        if r is not None:
            end = r.exit_t if r.closed else float("inf")
        else:
            end = db.epoch(c.day, CLOSE_HMS)
        act = [k for k in items if k[0] <= c.tt0 < k[1]]
        used = sum(k[2] for k in act)
        if used + float(c.margin) <= cap:
            items.append((c.tt0, end, float(c.margin)))
            if r is not None:
                keep.append(c.cid)
    return tr[tr.cid.isin(keep)]


def summarize(label, cards, tr, cap):
    t2 = margin_fcfs(cards, tr, cap)
    cl, op = t2[t2.closed], t2[~t2.closed]
    d = cl.groupby("exit_day").net.sum()
    return {"規則": label, "接受成交": len(t2), "已平倉": len(cl), "未平倉": len(op), "已實現淨": int(cl.net.sum()),
            "未平估值": int(op.net.sum()) if len(op) else 0, "含估值合計": int(t2.net.sum()), "勝率": round(float((cl.net > 0).mean()), 2) if len(cl) else 0,
            "平均持有分": round(float(cl.held_min.mean()), 0) if len(cl) else 0, "留倉過夜": int(cl.overnight.sum()),
            "提早出場": int(cl.why.str.startswith("early").sum()), "平均掛單次數": round(float(cl.orders.mean()), 2) if len(cl) else 0,
            "逐日已實現": {k: int(v) for k, v in d.items()}}, t2


def load_all2():
    cards, R = X.load_cards()
    cp = S / "exit_mkt2.pkl"
    if cp.exists():
        mk = pickle.load(open(cp, "rb"))
    else:
        roots = sorted(set(cards.root.dropna()))
        mk = {(d, r): Mkt2(r, d) for d in X.DAYS for r in roots}
        pickle.dump(mk, open(cp, "wb"))
    G = pickle.load(open(S / "exit_flowgrid.pkl", "rb"))
    return cards, R, mk, G
