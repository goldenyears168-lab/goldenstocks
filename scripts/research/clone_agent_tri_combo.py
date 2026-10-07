#!/usr/bin/env python3
"""研究用:★4 與 H0 的「優勢交集」——交集/聯集/互斥子集/H0 去 5 分尺度,現貨訊號層級(卡片價假設成交,出場=最後成交)的 60/90 分報酬(bps)。
a=★4 條件=現行寬門檻∧stars==4; b=H0=三尺度∧距高≤-0.5∧額pct≥30; b2=H0 去 5 分尺度(僅 30 分+全日)。各變體各自套 20 分冷卻與價格帶。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_tri_combo.py IS|HOLD
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import clone_agent_tri_holdout as th  # noqa: E402
import clone_agent_wide5m_backtest as m  # noqa: E402
import trader_signal_watch as w  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

HOLDS, LAG, FEE = (60, 90), 5, 176.0


def tri2(c):
    mm = th.DAYRE.search(" ".join(c["hits"]))
    fl = c["flow"]
    return fl["big30"][0] >= 0 and fl["ret30"][0] < 0 and int(mm.group(1)) >= 0 and int(mm.group(2)) < 0


def A(c):
    return th.old_ok(c) and th.g0(c) and c["stars"] == 4


def B(c):
    return th.tri(c) and th.g0(c)


def B2(c):
    return tri2(c) and th.g0(c)


PRED = {
    "★4": A,
    "H0": B,
    "H0無5分": B2,
    "交集 ★4∧H0": lambda c: A(c) and B(c),
    "交集 ★4∧H0無5分": lambda c: A(c) and B2(c),
    "聯集 ★4∨H0": lambda c: A(c) or B(c),
    "聯集 ★4∨H0無5分": lambda c: A(c) or B2(c),
    "★4但非H0無5分": lambda c: A(c) and not B2(c),
    "H0無5分但非★4": lambda c: B2(c) and not A(c),
    "H0∧★3以上": lambda c: B(c) and c["stars"] >= 3,
    "H0無5分∧★3以上": lambda c: B2(c) and c["stars"] >= 3,
}


def ep(day, hms):
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


def main():
    R = load_rules()
    members, band = universe_sids(R), tuple(R["universe"]["price_band"])
    rows, venn = [], []
    for d in th.DAYS:
        cards = [json.loads(x) for x in (th.SRC / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        inu = [c for c in cards if str(c["sid"]) in members and band[0] <= c["price"] <= band[1]]
        venn.append(dict(day=d, 卡=len(inu), a=sum(map(A, inu)), b=sum(map(B, inu)), b2=sum(map(B2, inu)), a_b=sum(A(c) and B(c) for c in inu), a_b2=sum(A(c) and B2(c) for c in inu)))
        tape = w.Tape(d)
        tape.poll()
        close = ep(d, "13:30:00")
        for v, pr in PRED.items():
            m.old_ok = lambda c: True
            m.new_ok = lambda c, tag, pr=pr: pr(c)
            for c in m.replay(cards, ("X",), band, members):
                sid, t = str(c["sid"]), ep(d, c["time"])
                t0, L = t + LAG, c["price"]
                oth = [s for s in members if s != sid]

                def bret(a_t, b_t):
                    xs = []
                    for s in oth:
                        a, _ = tape.px_at(s, a_t)
                        b, _ = tape.px_at(s, b_t)
                        if a and b:
                            xs.append(1e4 * (b / a - 1))
                    return float(np.mean(xs)) if xs else np.nan

                rec = dict(var=v, day=d, time=c["time"], sid=sid, name=c["uname"], stars=c["stars"], pre_bench30=bret(t - 1800, t))
                for h in HOLDS:
                    t1 = min(t0 + h * 60, close)
                    px1, _ = tape.px_at(sid, t1)
                    ok = px1 is not None and t0 < close and (t0 + h * 60) <= close
                    g = 1e4 * (px1 / L - 1) if ok else np.nan
                    n = L * c["shares"]
                    rec[f"g{h}"] = g
                    rec[f"net{h}"] = g - (2 * FEE + 2e-5 * 2 * n) / n * 1e4 if ok else np.nan
                    rec[f"b{h}"] = bret(t0, t1) if ok else np.nan
                rows.append(rec)
        print("done", d, flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(th.OUT / "combo_trades.csv", index=False)
    pd.DataFrame(venn).to_csv(th.OUT / "combo_venn.csv", index=False)
    print(th.MODE, "寫出", len(df), "列;卡片層級重疊:", pd.DataFrame(venn).drop(columns="day").sum().to_dict())


if __name__ == "__main__":
    main()
