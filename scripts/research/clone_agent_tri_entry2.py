#!/usr/bin/env python3
"""研究用:H0 與現行 ★4 的逐筆紀錄,比較兩種進場(買一賣一價差≤1檔才下單,不套保證金/價格帶):
E1=一律吃賣一立即成交;E2=限價掛「當下成交價」(夾在買一~賣一之間):外盤=吃賣一立即成交、內盤=在買一排隊(qpro/qcons/qopt),收盤13:30前一直掛。
報酬率=持有60/90分(自成交起,上限13:30,未滿者剔除)的買一/最後成交出場。成本=手續費2×176+期貨稅2e-5/邊。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_tri_entry2.py IS|HOLD
"""
from __future__ import annotations

import bisect
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import clone_agent_day_backtest as bt  # noqa: E402
import clone_agent_tri_holdout as th  # noqa: E402
import clone_agent_wide5m_backtest as m  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

FEE, TAX, LAG, CLOSE = 176.0, 2e-5, 5, "13:30:00"
HOLDS = (60, 90)
QM = ("qpro", "qcons", "qopt")


def tick(p):
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0


def main():
    R = load_rules()
    members, band = universe_sids(R), tuple(R["universe"]["price_band"])
    rows = []
    for d in th.DAYS:
        cards = [json.loads(x) for x in (th.SRC / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        p = bt.CACHE / f"biglot_live_watch/futprice_{d}.json"
        fp = json.loads((p if p.exists() else bt.CACHE / "biglot_live_watch/futprice_2026-09-22.json").read_text(encoding="utf-8"))
        fired = {}
        for v in ("V0 現行寬門檻", "H0 三尺度∧距高∧額≥30"):
            pr = th.PRED[v]
            m.old_ok = lambda c: True
            m.new_ok = lambda c, tag, pr=pr: pr(c)
            fired[v] = m.replay(cards, ("X",), band, members)
        h0 = {(c["time"], c["sid"]): c for c in fired["H0 三尺度∧距高∧額≥30"]}
        s4 = {(c["time"], c["sid"]): c for c in fired["V0 現行寬門檻"] if c["stars"] == 4}
        tc, bc = {}, {}
        for key in sorted(set(h0) | set(s4)):
            c = h0.get(key) or s4.get(key)
            sid = c["sid"]
            root = bt.resolve_root((fp.get(sid) or {}).get("sym", ""), d)
            rec = dict(day=d, time=c["time"], sid=sid, name=c["uname"], stars=c["stars"], H0=key in h0, S4=key in s4, card_px=c["price"], shares=c["shares"])
            rows.append(rec)
            tr = tc.setdefault(root, bt.load_trades(root, d)) if root else None
            if not tr:
                rec["status"] = "無期貨逐筆"
                continue
            bk = bc.setdefault(root, bt.load_books(root, d))
            bts = [b[0] for b in bk]
            ts = [x[0] for x in tr]
            t0 = bt.epoch(d, c["time"]) + LAG
            i = bisect.bisect_right(bts, t0) - 1
            if i < 0:
                rec["status"] = "無委託簿"
                continue
            bids, ask = bk[i][1], bk[i][3]
            if not bids or not np.isfinite(ask):
                rec["status"] = "單邊簿"
                continue
            bid = max(bids)
            rec["bid0"], rec["ask0"] = bid, ask
            rec["spread_ticks"] = round((ask - bid) / tick(ask), 1)
            if ask - bid > tick(ask) + 1e-9:
                rec["status"] = "價差>1檔"
                continue
            ki = bisect.bisect_right(ts, t0) - 1
            last0 = tr[ki][1] if ki >= 0 else bid
            L = min(max(last0, bid), ask)
            rec["limit"], rec["side"] = L, "外盤吃賣一" if L >= ask else "內盤排買一"
            close_t = bt.epoch(d, CLOSE)
            fills = {"E1": (t0, ask)}
            for mode in QM:
                tf, px, *_ = bt.simulate_queue(tr, bk, L, t0, mode, close_t)
                fills[f"E2{mode}"] = (tf, px)
            rec["status"] = "ok"
            for nm, (tf, px0) in fills.items():
                if tf is None:
                    rec[f"{nm}_fill"] = None
                    continue
                rec[f"{nm}_fill"] = pd.Timestamp(tf, unit="s", tz="UTC").tz_convert(bt.TZ).strftime("%H:%M:%S")
                rec[f"{nm}_wait"] = round((tf - t0) / 60, 1)
                rec[f"{nm}_entry"] = px0
                j = max(bisect.bisect_left(ts, tf), 0)
                for h in HOLDS:
                    t1 = tf + h * 60
                    rec[f"{nm}_trunc{h}"] = t1 > close_t
                    k = max(bisect.bisect_right(ts, min(t1, close_t)) - 1, j)
                    last = tr[k][1]
                    bb = [x[2] for x in tr[j:k + 1] if x[2]]
                    bidx = bb[-1] if bb else last
                    for kind, px in (("last", last), ("bid", bidx)):
                        gross = (px - px0) * c["shares"]
                        cost = 2 * FEE + TAX * (px0 + px) * c["shares"]
                        rec[f"{nm}_exit{h}_{kind}"] = px
                        rec[f"{nm}_bps{h}_{kind}"] = 1e4 * (px / px0 - 1)
                        rec[f"{nm}_netbps{h}_{kind}"] = 1e4 * (gross - cost) / (px0 * c["shares"])
    df = pd.DataFrame(rows)
    df.to_csv(th.OUT / "entry2_trades.csv", index=False)
    print(f"{th.MODE} 寫出 {th.OUT / 'entry2_trades.csv'} 共{len(df)}列")


if __name__ == "__main__":
    main()
