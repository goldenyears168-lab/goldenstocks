#!/usr/bin/env python3
"""複製人代理人出場研究:出場也拆成「送出委託 → 排隊 → 成交」,並測持倉期間的提早平倉條件與單位(金額 vs %)。

進場沿用 CA-R1 凍結規則(名單 ∩ 價格帶、qpro 排隊、保證金 147 萬 fcfs、手續費 176、期交稅 0.002%),
出場另外模擬(賣單):
  taker   = 送出時賣在當下買一(主動成交,保證成交,付半個價差)
  passive = 掛在賣一(或賣一再上N檔)排隊,逾時未成交就撤單改打買一;排隊用與進場同款 qpro(撤單依前後比例)。
  last    = 舊口徑(假裝以 60 分鐘當下最後成交價成交),僅供對照。
所有決策到送單固定延遲 LAG 秒。強制出清時間 FLAT。資料:期貨逐筆/五檔(cache)與 exit_flowgrid.pkl(clone_agent_exit_flowgrid.py)。
"""
from __future__ import annotations

import bisect
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_agent_day_backtest as db  # noqa: E402
import clone_agent_margin_backtest as mb  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
OUT = Path.home() / "goldenstocks-data/data/clone_agent"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"]
OOS_DAYS = {"2026-10-05"}
LAG = 5
FLAT_HMS = "13:25:00"
TAX = 0.00002
BITS = {"A": 1, "B": 2, "C": 4, "D": 8}


def tick_size(px):
    for lim, t in ((10, .01), (50, .05), (100, .1), (500, .5)):
        if px < lim:
            return t
    return 1.      # 個股期貨實測:500 以上最小跳動 1 元(現貨 1000 以上為 5 元,期貨不是)


# ───────────── 資料 ─────────────
def load_books2(root, day):
    p = db.CACHE / f"{root.lower()}_books" / f"{root.lower()}_books_{day}.jsonl"
    rows = []
    if not p.exists():
        return rows
    for line in p.open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("stale") or r.get("session") != "day" or r["ts"][:10] != day:
            continue
        t = r["book_time"] / 1e6
        if db.epoch(day, "08:45:00") <= t <= db.epoch(day, "13:45:05"):
            bids = {float(x["price"]): float(x["size"]) for x in r.get("bids") or [] if float(x["price"]) > 0}
            asks = {float(x["price"]): float(x["size"]) for x in r.get("asks") or [] if float(x["price"]) > 0}
            rows.append((t, bids, asks))
    rows.sort(key=lambda x: x[0])
    return rows


class Mkt:
    def __init__(self, root, day):
        tr = db.load_trades(root, day) or []
        self.ts = [x[0] for x in tr]
        self.px = np.array([x[1] for x in tr])
        self.bidtick = [x[2] for x in tr]
        self.size = [x[4] for x in tr]
        self.bk = load_books2(root, day)
        self.bt = [b[0] for b in self.bk]

    def book(self, t):
        i = bisect.bisect_right(self.bt, t) - 1
        return self.bk[i] if i >= 0 else None

    def best_bid(self, t):
        b = self.book(t)
        if b and b[1]:
            return max(b[1])
        k = bisect.bisect_right(self.ts, t) - 1
        while k >= 0:
            if self.bidtick[k]:
                return float(self.bidtick[k])
            k -= 1
        return float(self.px[max(bisect.bisect_right(self.ts, t) - 1, 0)])

    def best_ask(self, t):
        b = self.book(t)
        return min(b[2]) if b and b[2] else None

    def last(self, t):
        k = max(bisect.bisect_right(self.ts, t) - 1, 0)
        return float(self.px[k])


def sim_sell(M, S_px, t0, t_end, mode="qpro"):
    """賣單排隊(進場買單的鏡像):掛在 S_px,t0 送出,t_end 前沒成交回 None。回傳 (成交時間, 成交價, 前方量)。"""
    b0 = M.book(t0)
    if b0 is None:
        return None
    bids, asks = b0[1], b0[2]
    if bids and S_px <= max(bids):
        return t0, max(bids), 0.0
    s_last = asks.get(S_px, 0.0)
    ahead, vL = s_last, 0.0
    i_b = bisect.bisect_right(M.bt, t0)
    i_t = bisect.bisect_right(M.ts, t0)
    ev = [(M.ts[k], 0, M.px[k], M.size[k]) for k in range(i_t, len(M.ts)) if M.ts[k] <= t_end]
    ev += [(M.bk[k][0], 1, M.bk[k][2], 0) for k in range(i_b, len(M.bk)) if M.bk[k][0] <= t_end]
    ev.sort(key=lambda x: (x[0], x[1]))
    for t, kind, p, extra in ev:
        if kind == 0:
            if p > S_px:
                return t, S_px, ahead
            if p == S_px:
                vL += extra
                ahead -= extra
                if ahead <= -1 + 1e-9:
                    return t, S_px, ahead
        else:
            asks_new = p
            if S_px in asks_new:
                s_new = asks_new[S_px]
            elif asks_new and S_px <= max(asks_new):
                s_new = 0.0
            else:
                continue          # 看不到該價位(超出五檔):不更新,不把「看不到」當撤單
            canc = max(0.0, (s_last - s_new) - vL)
            base = s_last - vL
            if canc > 0 and ahead > 0:
                if mode == "qopt":
                    ahead = max(0.0, ahead - canc)
                elif mode == "qpro" and base > 0:
                    ahead = max(0.0, ahead - canc * min(1.0, ahead / base))
            s_last, vL = s_new, 0.0
    return None


def exec_sell(M, t_dec, route, e, flat):
    """決策時間 t_dec 之後的賣出執行。route: last / taker / pK_T{分}(K=相對賣一的檔數,T=逾時分鐘)。回傳 (t, px, 說明)。"""
    if route == "last":
        return t_dec, M.last(t_dec), "last"
    t_s = t_dec + LAG
    if route == "taker" or t_s >= flat:
        return t_s, M.best_bid(t_s), "taker"
    if route.startswith("d"):      # 對照:不掛單、延後 W 分鐘才主動賣(量出「多持有 W 分鐘」本身的漂移)
        t_d = min(t_s + float(route[1:]) * 60, flat)
        return t_d, M.best_bid(t_d), "delayed"
    k_s, T_s = route[1:].split("_T")
    ask = M.best_ask(t_s)
    if ask is None:
        return t_s, M.best_bid(t_s), "taker"
    S_px = ask + int(k_s) * tick_size(ask)
    t_end = min(t_s + float(T_s) * 60, flat)
    r = sim_sell(M, S_px, t_s, t_end)
    if r is not None:
        return r[0], r[1], "passive"
    t_c = t_end + 1
    return t_c, M.best_bid(t_c), "fallback"


# ───────────── 卡片 / 規則引擎 ─────────────
def load_cards():
    R = load_rules()
    mem = universe_sids(R)
    lo, hi = R["universe"]["price_band"]
    d = pd.concat([pd.read_csv(S / f"cab_{x}.csv", keep_default_na=False, na_values=[""]) for x in DAYS], ignore_index=True)
    d["sid"] = d.sid.astype(str)
    d = d[d.sid.isin(mem) & d.limit.between(lo, hi)].copy().reset_index(drop=True)
    d["cid"] = d.index
    d["lag"] = LAG
    return d, R


def flow_flags(G, day, sid, unit):
    g = G[day]
    return g["t"], g[f"flags_{unit}"][:, g["sids"].index(sid)] if sid in g["sids"] else None


def run_trade(c, M, G, rule):
    day = c.day
    tf = db.epoch(day, c.qpro_fill)
    e = float(c.qpro_entry)
    sh = float(c.shares)
    flat = db.epoch(day, FLAT_HMS)
    fl = rule.get("flow")
    hold = rule.get("hold", 60)
    plan_end = min(tf + (rule.get("max_hold", 1e4) if (fl and rule.get("extend")) else hold) * 60, flat)
    ts = M.ts
    i0, i1 = bisect.bisect_right(ts, tf), bisect.bisect_right(ts, plan_end)
    cand = []           # (觸發時間, 原因)
    if i1 > i0:
        p = M.px[i0:i1]
        t_arr = np.array(ts[i0:i1])
        st, tr_ = rule.get("stop"), rule.get("trail")
        if st:
            x = st["x"] * e / 1e4 if st["unit"] == "bps" else st["x"] / sh
            hit = np.where(p <= e - x)[0]
            if len(hit):
                cand.append((float(t_arr[hit[0]]), "stop"))
        if tr_:
            gv = (lambda v: v * e / 1e4) if tr_["unit"] == "bps" else (lambda v: v / sh)
            act, give = gv(tr_["act"]), gv(tr_["give"])
            peak = np.maximum.accumulate(np.maximum(p, e))
            hit = np.where((peak - e >= act) & (p <= peak - give))[0]
            if len(hit):
                cand.append((float(t_arr[hit[0]]), "trail"))
    if fl:
        gt, arr = flow_flags(G, day, c.sid, fl["unit"])
        if arr is not None:
            mask = sum(BITS[k] for k in fl["set"])
            g0 = int(np.searchsorted(gt, tf, "right")) - 1
            base = int(arr[max(g0, 0)])
            ga = int(np.searchsorted(gt, tf + fl.get("grace", 2) * 60, "left"))
            gb = int(np.searchsorted(gt, plan_end, "right"))
            seg = arr[ga:gb].astype(int)
            hitv = ((seg & ~base) & mask) != 0 if fl["trig"] == "N" else (seg & mask) != 0
            w = np.where(hitv)[0]
            if len(w):
                cand.append((float(gt[ga + w[0]]), "flow"))
    if cand:
        t_dec, why = min(cand)
        route = rule.get("adv_route", "taker")
    else:
        t_dec, why, route = plan_end, ("flat" if plan_end >= flat - 1e-6 and plan_end < tf + hold * 60 - 1 else "time"), rule.get("plan_route", "taker")
    tx, xp, how = exec_sell(M, t_dec, route, e, flat)
    ba, bb = M.best_ask(t_dec + LAG), M.best_bid(t_dec + LAG)
    mid = (ba + bb) / 2 if ba else bb
    gross = (xp - e) * sh
    cost = 2 * rule.get("fee", 176) + TAX * (e + xp) * sh
    return {"cid": c.cid, "day": day, "sid": c.sid, "name": c.name, "entry": e, "fill": tf, "exit_t": tx, "exit_px": xp,
            "why": why, "how": how, "held_min": (tx - tf) / 60, "gross": gross, "cost": cost, "net": gross - cost,
            "bps": 1e4 * (xp / e - 1), "mid_dec": mid, "t_dec": t_dec, "shortfall": (mid - xp) * sh, "margin": float(c.margin), "notional": e * sh}


def evaluate(cards, mk, G, rule):
    rows = []
    for c in cards.itertuples():
        if not isinstance(c.qpro_fill, str) or not isinstance(c.root, str):
            continue
        rows.append(run_trade(c, mk[(c.day, c.root)], G, rule))
    return pd.DataFrame(rows)


def capped(cards, tr, cap):
    """依出場實際結束時間重排保證金(fcfs),回傳被接受且成交的交易表。"""
    held = dict(zip(tr.cid, tr.held_min))
    d = cards.copy()
    d["qpro_held_min"] = d.cid.map(held)
    d["margin"] = d.margin.astype(float)
    keep = []
    for day, g in d.groupby("day"):
        items, _ = mb.run(g, "qpro", cap, None, "fcfs", False)
        keep += [k["r"].cid for k in items if k["tf"] is not None]
    return tr[tr.cid.isin(keep)]


def summ(label, cards, tr, cap):
    t2 = capped(cards, tr, cap)
    oos = t2[t2.day.isin(OOS_DAYS)]
    return {"規則": label, "全部成交n": len(tr), "不限保證金淨": int(tr.net.sum()), "每筆": int(tr.net.mean()),
            "限147萬n": len(t2), "限147萬淨": int(t2.net.sum()), "樣本外10-05": int(oos.net.sum()),
            "勝率": round(float((t2.net > 0).mean()) if len(t2) else 0, 2),
            "平均持有分": round(float(t2.held_min.mean()), 1) if len(t2) else 0}


def load_all():
    cards, R = load_cards()
    cp = S / "exit_mkt.pkl"
    if cp.exists():
        mk = pickle.load(open(cp, "rb"))
    else:
        mk = {(day, root): Mkt(root, day) for (day, root), _ in cards.groupby(["day", "root"])}
        pickle.dump(mk, open(cp, "wb"))
    G = pickle.load(open(S / "exit_flowgrid.pkl", "rb"))
    return cards, R, mk, G


if __name__ == "__main__":
    cards, R, mk, G = load_all()
    cap = R["capital"]["margin_cap"]
    base = evaluate(cards, mk, G, {"plan_route": "last"})
    print(len(cards), "卡", base.shape[0], "成交"); print(summ("基準 last", cards, base, cap))
