#!/usr/bin/env python3
"""研究用:H0 拿掉「距近30分高≤-0.5%」(唯一的價格下跌條件)後,與 H0、★4 逐筆比較報酬率(bps),並拆內盤/外盤。
變體: H0=三尺度∧距高≤-0.5∧額pct≥30; H0去距高=三尺度∧額pct≥30; 距高反面=三尺度∧距高>-0.5∧額pct≥30; V0★4=現行寬門檻stars==4。
期貨口徑: 買賣價差≤1檔才下單;限價=當下成交價(夾在買一~賣一);外盤=吃賣一立即成交,內盤=買一排隊(qpro/qcons,最多TTL=300秒,逾時撤單);出場=持有60/90分後買一。不套保證金。
現貨口徑: 進場=卡片價+5秒假設成交,出場=現貨最後成交,bench=其餘12檔等權同窗報酬;另記進場前30分個股/籃子報酬。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_tri_h0_nodist.py IS|HOLD
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
import trader_signal_watch as w  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

FEE, TAX, LAG, CLOSE, TTL = 176.0, 2e-5, 5, "13:30:00", 300
HOLDS = (60, 90)
PRED = {
    "H0": th.PRED["H0 三尺度∧距高∧額≥30"],
    "H0去距高": lambda c: th.tri(c) and c["amt_pct"] is not None and c["amt_pct"] >= 30,
    "距高反面": lambda c: th.tri(c) and c["dist_hi"] > -0.5 and c["amt_pct"] is not None and c["amt_pct"] >= 30,
    "V0現行": th.PRED["V0 現行寬門檻"],
}


def tick(p):
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0


def ep(day, hms):
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


def fut_sim(c, d, fp, tc, bc):
    sid = c["sid"]
    root = bt.resolve_root((fp.get(sid) or {}).get("sym", ""), d)
    rec = {}
    tr = tc.setdefault(root, bt.load_trades(root, d)) if root else None
    if not tr:
        return dict(status="無期貨逐筆")
    bk = bc.setdefault(root, bt.load_books(root, d))
    bts, ts = [b[0] for b in bk], [x[0] for x in tr]
    t0 = bt.epoch(d, c["time"]) + LAG
    i = bisect.bisect_right(bts, t0) - 1
    if i < 0:
        return dict(status="無委託簿")
    bids, ask = bk[i][1], bk[i][3]
    if not bids or not np.isfinite(ask):
        return dict(status="單邊簿")
    bid = max(bids)
    if ask - bid > tick(ask) + 1e-9:
        return dict(status="價差>1檔")
    ki = bisect.bisect_right(ts, t0) - 1
    L = min(max(tr[ki][1] if ki >= 0 else bid, bid), ask)
    rec.update(status="ok", side="外盤" if L >= ask else "內盤", ask0=ask, bid0=bid)
    close_t = bt.epoch(d, CLOSE)
    fills = {"E1": (t0, ask)}
    for mode in ("qpro", "qcons"):
        tf, px, *_ = bt.simulate_queue(tr, bk, L, t0, mode, min(close_t, t0 + TTL))
        fills[mode] = (tf, px)
    for nm, (tf, px0) in fills.items():
        rec[f"{nm}_fill"] = tf is not None
        if tf is None:
            continue
        rec[f"{nm}_entry"] = px0
        j = max(bisect.bisect_left(ts, tf), 0)
        for h in HOLDS:
            t1 = tf + h * 60
            rec[f"{nm}_trunc{h}"] = t1 > close_t
            k = min(max(bisect.bisect_right(ts, min(t1, close_t)) - 1, j), len(tr) - 1)
            bb = [x[2] for x in tr[j:k + 1] if x[2]]
            px = bb[-1] if bb else tr[k][1]
            rec[f"{nm}_bps{h}"] = 1e4 * (px / px0 - 1)
            rec[f"{nm}_net{h}"] = 1e4 * ((px - px0) * c["shares"] - (2 * FEE + TAX * (px0 + px) * c["shares"])) / (px0 * c["shares"])
    return rec


def main():
    R = load_rules()
    members, band = universe_sids(R), tuple(R["universe"]["price_band"])
    rows = []
    for d in th.DAYS:
        cards = [json.loads(x) for x in (th.SRC / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        p = bt.CACHE / f"biglot_live_watch/futprice_{d}.json"
        fp = json.loads((p if p.exists() else bt.CACHE / "biglot_live_watch/futprice_2026-09-22.json").read_text(encoding="utf-8"))
        tape = w.Tape(d)
        tape.poll()
        close = ep(d, CLOSE)
        tc, bc, cache = {}, {}, {}
        for v, pr in PRED.items():
            m.old_ok = lambda c: True
            m.new_ok = lambda c, tag, pr=pr: pr(c)
            fired = m.replay(cards, ("X",), band, members)
            sel = [(v, c) for c in fired] + ([("V0★4", c) for c in fired if c["stars"] == 4] if v == "V0現行" else [])
            for tag, c in sel:
                key = (c["time"], str(c["sid"]))
                sid = str(c["sid"])
                if key not in cache:
                    r = fut_sim(c, d, fp, tc, bc)
                    t = ep(d, c["time"])
                    t0 = t + LAG
                    L = c["price"]
                    oth = [s for s in members if s != sid]
                    def bret(a_t, b_t):
                        xs = []
                        for s in oth:
                            a, _ = tape.px_at(s, a_t)
                            b, _ = tape.px_at(s, b_t)
                            if a and b:
                                xs.append(1e4 * (b / a - 1))
                        return float(np.mean(xs)) if xs else np.nan
                    sa, _ = tape.px_at(sid, t - 1800)
                    sb, _ = tape.px_at(sid, t)
                    r["pre_stock30"] = 1e4 * (sb / sa - 1) if sa and sb else np.nan
                    r["pre_bench30"] = bret(t - 1800, t)
                    for h in HOLDS:
                        t1 = min(t0 + h * 60, close)
                        px1, _ = tape.px_at(sid, t1)
                        ok = px1 is not None and t0 < close
                        r[f"s_trunc{h}"] = (t0 + h * 60) > close if ok else True
                        r[f"s_gross{h}"] = 1e4 * (px1 / L - 1) if ok else np.nan
                        r[f"s_bench{h}"] = bret(t0, t1) if ok else np.nan
                    cache[key] = r
                rows.append(dict(var=tag, day=d, time=c["time"], sid=sid, name=c["uname"], stars=c["stars"], card_px=c["price"], shares=c["shares"],
                                 dist_hi=c["dist_hi"], amt_pct=c["amt_pct"], **cache[key]))
        print("done", d, flush=True)
    df = pd.DataFrame(rows)
    out = th.OUT / "h0_nodist_trades.csv"
    df.to_csv(out, index=False)
    print(f"{th.MODE} 寫出 {out} 共{len(df)}列")


if __name__ == "__main__":
    main()
