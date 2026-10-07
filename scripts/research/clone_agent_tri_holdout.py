#!/usr/bin/env python3
"""研究用:預註冊變體 H0–H6(三尺度組合)在 holdout 日(未參與挑選)的重放回測,對照現行寬門檻 V0 與 V0★4。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_tri_holdout.py [--days IS|HOLD] [--skip-queue]
HOLD=9/3–9/28(18日,holdout);IS=9/29–10/6(挑選用的樣本內,供對照)。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import clone_agent_rules_backtest as rb  # noqa: E402
import clone_agent_wide5m_backtest as m  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
HOLD_DAYS = ["2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15",
             "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]  # 9/25、9/28 raw tape 僅 174KB(無資料)已剔除
MODE = "IS" if "IS" in sys.argv else "HOLD"
DAYS = m.DAYS if MODE == "IS" else HOLD_DAYS
SRC = S / ("clone_agent_w5_nogate" if MODE == "IS" else "clone_agent_w5_hold")
OUT = S / ("w5h_is" if MODE == "IS" else "w5h")
CANDS = OUT / "cands"
OUT.mkdir(exist_ok=True)
CANDS.mkdir(exist_ok=True)
DAYRE = re.compile(r"全日 大戶([+-]?\d+)萬／散戶([+-]?\d+)萬")
KEEP = 0.70 + 1e-12


def tri(c):
    mm = DAYRE.search(" ".join(c["hits"]))
    f, fl = c["f5"], c["flow"]
    return (f["big5_net"] >= 0 and f["ret5_net"] < 0 and fl["big30"][0] >= 0 and fl["ret30"][0] < 0
            and int(mm.group(1)) >= 0 and int(mm.group(2)) < 0)


def g0(c):
    return c["dist_hi"] <= -0.5 and c["amt_pct"] is not None and c["amt_pct"] >= 30


def old_ok(c):
    fr = c["flow_ranks"]
    return all(fr[k] is not None and fr[k] <= KEEP for k in ("big_day", "big30", "ret30"))


def hh(c):
    return m.hms(c["time"]) / 3600


PRED = {
    "V0 現行寬門檻": lambda c: old_ok(c) and g0(c),
    "H0 三尺度∧距高∧額≥30": lambda c: tri(c) and g0(c),
    "H1 H0∧大戶5分>0": lambda c: tri(c) and g0(c) and c["f5"]["big5_net"] > 0,
    "H2 H1∧成交額≥70": lambda c: tri(c) and g0(c) and c["f5"]["big5_net"] > 0 and c["amt_pct"] >= 70,
    "H3 H2∧11:15前": lambda c: tri(c) and g0(c) and c["f5"]["big5_net"] > 0 and c["amt_pct"] >= 70 and hh(c) < 11.25,
    "H4 H1∧11:15前": lambda c: tri(c) and g0(c) and c["f5"]["big5_net"] > 0 and hh(c) < 11.25,
    "H5 對照:三尺度∧距高>-0.5∧額≥30": lambda c: tri(c) and c["dist_hi"] > -0.5 and (c["amt_pct"] or 0) >= 30,
    "H6 H1∧距高≤-1∧族群≤50": lambda c: tri(c) and g0(c) and c["f5"]["big5_net"] > 0 and c["dist_hi"] <= -1 and c["chg_cm"] is not None and c["chg_cm"] <= 50,
}


def main():
    R = load_rules()
    members = universe_sids(R)
    band = tuple(R["universe"]["price_band"])
    mode, fee, pol = R["order"]["queue_model"], R["cost"]["fee_per_lot_per_side"], R["order"]["policy"]
    cards = {d: [json.loads(x) for x in (SRC / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] for d in DAYS}
    key = lambda c: (c["day"], c["time"], str(c["sid"]))  # noqa: E731
    fired = {}
    for v, p in PRED.items():
        m.old_ok = lambda c: True
        m.new_ok = lambda c, tag, p=p: p(c)
        fired[v] = {d: m.replay(cards[d], ("X",), band, members) for d in DAYS}
    union = {d: {key(c): c for v in fired for c in fired[v][d]} for d in DAYS}
    for d in DAYS:
        with open(CANDS / f"candidates_{d}.jsonl", "w", encoding="utf-8") as fh:
            for c in union[d].values():
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    print("聯集候選", sum(len(union[d]) for d in DAYS), {v: sum(len(fired[v][d]) for d in DAYS) for v in fired})
    if "--skip-queue" not in sys.argv:
        for d in DAYS:
            subprocess.run([sys.executable, "scripts/research/clone_agent_day_backtest_fpfallback.py", "--day", d, "--cand-dir", str(CANDS),
                            "--out", str(OUT / f"q_{d}.csv")], check=True, env={**os.environ, "PYTHONPATH": "src"})
    q = pd.concat([pd.read_csv(OUT / f"q_{d}.csv") for d in DAYS], ignore_index=True)
    q["sid"] = q.sid.astype(str)
    q["k"] = list(zip(q.day, q.time, q.sid))
    fp = {}
    for d in DAYS:
        p = rb.db.CACHE / f"biglot_live_watch/futprice_{d}.json"
        if p.exists():
            fp[d] = json.loads(p.read_text(encoding="utf-8"))
    rng = np.random.default_rng(11)

    def ci(d, col="gross_bps"):
        cl = d.day + "_" + d.sid.astype(str)
        g = pd.DataFrame({"v": d[col].values, "c": cl.values}).groupby("c").v.agg(["sum", "count"])
        s, c = g["sum"].values, g["count"].values
        i = rng.integers(0, len(g), (2000, len(g)))
        mu = s[i].sum(1) / c[i].sum(1)
        return np.percentile(mu, [5, 95])

    ledgers = {}
    for v in PRED:
        ks = {key(c) for d in DAYS for c in fired[v][d]}
        sub = q[q.k.isin(ks)]
        trs = []
        for d, g in sub.groupby("day"):
            _, _, tr = rb.day_stats(g, mode, 1e12, fee, policy=pol, one=False)
            trs.append(tr)
        tr = pd.concat(trs, ignore_index=True) if trs else pd.DataFrame()
        if len(tr):
            have = tr[tr.day.isin(fp)].copy()
            ex = pd.Series(np.nan, index=tr.index)
            if len(have):
                have["bench_bps"] = rb.benchmark(have, members, fp)
                ex.loc[have.index] = have.gross_bps - have.bench_bps
            tr["excess_bps"] = ex
        ledgers[v] = (tr, len(sub))
        tr.to_csv(OUT / f"ledger_{v.split(' ')[0]}.csv", index=False)

    def row(name, tr, nsig=None):
        if tr is None or not len(tr):
            return dict(變體=name, 訊號=nsig, 成交=0)
        lo, hi = ci(tr)
        return dict(變體=name, 訊號=nsig, 成交=len(tr), 天=tr.day.nunique(), 勝率=round(100 * (tr.net > 0).mean()), 毛bps=round(tr.gross_bps.mean(), 1),
                    每筆淨=round(tr.net.mean()), 總淨=int(tr.net.sum()), 毛90CI=f"{lo:.0f}~{hi:.0f}",
                    超額bps=None if tr.excess_bps.notna().sum() == 0 else round(tr.excess_bps.mean(), 1), 超額n=int(tr.excess_bps.notna().sum()))

    pd.set_option("display.width", 250, "display.max_columns", 40)
    print(f"\n===== {MODE} 各變體(不設保證金上限;冷卻後) =====")
    rows = [row(v, t, n) for v, (t, n) in ledgers.items()]
    v0 = ledgers["V0 現行寬門檻"][0]
    rows.insert(1, row("V0★4(現行實單口徑)", v0[v0.stars == 4]))
    rows.insert(2, row("V0★3+", v0[v0.stars >= 3]))
    print(pd.DataFrame(rows).to_string(index=False))

    print(f"\n===== {MODE} 同母體保留 vs 剔除(以 H0 成交為母體) =====")
    h0 = ledgers["H0 三尺度∧距高∧額≥30"][0].copy()
    allc = {key(c): c for d in DAYS for c in cards[d]}
    h0["k"] = list(zip(h0.day, h0.signal, h0.sid.astype(str)))
    feat = {"大戶5分淨額>0": lambda c: c["f5"]["big5_net"] > 0, "成交額≥70": lambda c: c["amt_pct"] >= 70, "11:15前": lambda c: hh(c) < 11.25,
            "距高≤-1": lambda c: c["dist_hi"] <= -1, "族群≤50(嚴④)": lambda c: c["chg_cm"] is not None and c["chg_cm"] <= 50}
    rr = []
    for nm, f in feat.items():
        msk = h0.k.map(lambda k: bool(f(allc[k])))
        K, Dr = h0[msk], h0[~msk]
        if len(K) and len(Dr):
            rr.append(dict(篩選器=nm, 保留n=len(K), 保留毛=round(K.gross_bps.mean(), 1), 保留勝率=round(100 * (K.net > 0).mean()), 剔除n=len(Dr),
                           剔除毛=round(Dr.gross_bps.mean(), 1), 剔除勝率=round(100 * (Dr.net > 0).mean()), 差=round(K.gross_bps.mean() - Dr.gross_bps.mean(), 1)))
    print(pd.DataFrame(rr).to_string(index=False))


if __name__ == "__main__":
    main()
