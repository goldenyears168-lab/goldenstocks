#!/usr/bin/env python3
"""研究用:訊號層級(不模擬成交/不需期貨逐筆檔)——用現貨 raw tape 算 H0–H6/V0 觸發後 60、90 分鐘報酬。
進場價=卡片限價(假設成交),出場=現貨最後成交價(截到 13:30);成本用期貨口徑(手續費176×2+期交稅2e-5×2邊)。
超額=同窗口名單其他標的等權現貨報酬。解決回測只覆蓋 13 檔中 6 檔有期貨逐筆檔的盲點。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_tri_spot_signal.py [IS]
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import clone_agent_tri_holdout as H  # noqa: E402
import trader_signal_watch as w  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

m = H.m
HOLDS = (60, 90)
LAG = 5


def ep(day, hms):
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


def main():
    R = load_rules()
    members = universe_sids(R)
    band = tuple(R["universe"]["price_band"])
    rng = np.random.default_rng(5)
    rows = []
    for d in H.DAYS:
        cards = [json.loads(x) for x in (H.SRC / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        tape = w.Tape(d)
        tape.poll()
        close = ep(d, "13:30:00")
        for v, p in H.PRED.items():
            m.old_ok = lambda c: True
            m.new_ok = lambda c, tag, p=p: p(c)
            fired = m.replay(cards, ("X",), band, members)
            for tag, sel in ((v, fired), (v + "★4" if v.startswith("V0") else None, [c for c in fired if c["stars"] == 4])):
                if tag is None:
                    continue
                for c in sel:
                    t0 = ep(d, c["time"]) + LAG
                    L = c["price"]
                    for hold in HOLDS:
                        t1 = min(t0 + hold * 60, close)
                        px1, _ = tape.px_at(str(c["sid"]), t1)
                        if px1 is None or t0 >= close:
                            continue
                        gross = 1e4 * (px1 / L - 1)
                        notion = L * c["shares"]
                        cost = (2 * 176 + 2e-5 * 2 * notion) / notion * 1e4
                        bench = []
                        for s in members:
                            if s == str(c["sid"]):
                                continue
                            a, _ = tape.px_at(s, t0)
                            b, _ = tape.px_at(s, t1)
                            if a and b:
                                bench.append(1e4 * (b / a - 1))
                        rows.append(dict(var=tag, day=d, sid=str(c["sid"]), time=c["time"], hold=hold, trunc=(t0 + hold * 60) > close,
                                         gross=gross, net=gross - cost, excess=gross - np.mean(bench) if bench else np.nan))
        print("done", d, flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(H.OUT / "spot_signal.csv", index=False)

    def ci(g, col):
        cl = (g.day + "_" + g.sid).values
        t = pd.DataFrame({"v": g[col].values, "c": cl}).groupby("c").v.agg(["sum", "count"])
        i = rng.integers(0, len(t), (2000, len(t)))
        mu = t["sum"].values[i].sum(1) / t["count"].values[i].sum(1)
        return np.percentile(mu, [5, 95])

    pd.set_option("display.width", 250, "display.max_columns", 40)
    for hold in HOLDS:
        out = []
        for v in dict.fromkeys(df["var"]):
            g = df[(df["var"] == v) & (df.hold == hold)]
            if not len(g):
                continue
            lo, hi = ci(g, "gross")
            elo, ehi = ci(g.dropna(subset=["excess"]), "excess")
            out.append(dict(變體=v, n=len(g), 天=g.day.nunique(), 簇=(g.day + g.sid).nunique(), 截斷=int(g.trunc.sum()), 勝率=round(100 * (g.net > 0).mean()),
                            毛bps=round(g.gross.mean(), 1), 毛CI=f"{lo:.0f}~{hi:.0f}", 淨bps=round(g.net.mean(), 1),
                            超額bps=round(g.excess.mean(), 1), 超額CI=f"{elo:.0f}~{ehi:.0f}"))
        print(f"\n===== {H.MODE} 訊號層級 持有{hold}分(現貨,假設以限價成交) =====")
        print(pd.DataFrame(out).to_string(index=False))


if __name__ == "__main__":
    main()
