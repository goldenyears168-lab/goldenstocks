#!/usr/bin/env python3
"""研究用:三尺度(5分∧30分∧全日 大戶淨買≥0∧散戶淨賣<0)組合的再篩選分析。
母體=拿掉『距高/成交額』寬門檻的全分鐘卡 → T 成立 → 20分冷卻 → 名單/價格帶。逐卡排隊回測後,
比較各篩選器『同母體保留 vs 剔除』;並用置換檢定回答「事後挑最佳篩選器」的偶然機率。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_tri_filter_analysis.py [--skip-queue]
"""
from __future__ import annotations

import json
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
A = S / "clone_agent_w5_nogate"
OUT = S / "w5t"
CANDS = OUT / "cands"
OUT.mkdir(exist_ok=True)
CANDS.mkdir(exist_ok=True)
DAYRE = re.compile(r"全日 大戶([+-]?\d+)萬／散戶([+-]?\d+)萬")


def tri(c):
    mm = DAYRE.search(" ".join(c["hits"]))
    f, fl = c["f5"], c["flow"]
    return (f["big5_net"] >= 0 and f["ret5_net"] < 0 and fl["big30"][0] >= 0 and fl["ret30"][0] < 0
            and int(mm.group(1)) >= 0 and int(mm.group(2)) < 0)


def main():
    R = load_rules()
    members = universe_sids(R)
    band = tuple(R["universe"]["price_band"])
    mode, fee, pol = R["order"]["queue_model"], R["cost"]["fee_per_lot_per_side"], R["order"]["policy"]
    cards = {d: [json.loads(x) for x in (A / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] for d in m.DAYS}
    key = lambda c: (c["day"], c["time"], str(c["sid"]))  # noqa: E731

    # 母體:T 成立(不看距高/成交額)→冷卻→名單/價格帶;另外保留所有 T 卡供穩健性
    m.old_ok = lambda c: True
    m.new_ok = lambda c, tag: tri(c)
    pop = {d: m.replay(cards[d], ("T",), band, members) for d in m.DAYS}
    allT = {d: [c for c in cards[d] if tri(c) and str(c["sid"]) in members and band[0] <= c["price"] <= band[1]] for d in m.DAYS}
    popk = {key(c) for d in m.DAYS for c in pop[d]}
    for d in m.DAYS:
        with open(CANDS / f"candidates_{d}.jsonl", "w", encoding="utf-8") as fh:
            for c in allT[d]:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    print("T 卡(未冷卻)", {d: len(allT[d]) for d in m.DAYS}, "冷卻後母體", len(popk))
    if "--skip-queue" not in sys.argv:
        for d in m.DAYS:
            subprocess.run([sys.executable, "scripts/research/clone_agent_day_backtest.py", "--day", d, "--cand-dir", str(CANDS),
                            "--out", str(OUT / f"q_{d}.csv")], check=True, env={**__import__("os").environ, "PYTHONPATH": "src"})
    q = pd.concat([pd.read_csv(OUT / f"q_{d}.csv") for d in m.DAYS], ignore_index=True)
    q["sid"] = q.sid.astype(str)
    fp = {d: json.loads((rb.db.CACHE / f"biglot_live_watch/futprice_{d}.json").read_text(encoding="utf-8")) for d in m.DAYS}
    trs = []
    for d, g in q.groupby("day"):
        _, _, tr = rb.day_stats(g, mode, 1e12, fee, policy=pol, one=False)
        trs.append(tr)
    tr = pd.concat(trs, ignore_index=True)
    tr["bench_bps"] = rb.benchmark(tr, members, fp)
    tr["excess_bps"] = tr.gross_bps - tr.bench_bps
    tr["time"] = tr["signal"]

    # 特徵
    allc = {key(c): c for d in m.DAYS for c in cards[d]}
    grp = {str(r.sid): r.group for r in q.drop_duplicates("sid").itertuples()}
    by_min: dict = {}
    for c in allc.values():
        by_min.setdefault((c["day"], c["time"]), []).append(c)
    rows = []
    for r in tr.itertuples():
        c = allc[(r.day, r.time, str(r.sid))]
        mm = DAYRE.search(" ".join(c["hits"]))
        f, fl = c["f5"], c["flow"]
        g = grp.get(str(r.sid))
        mates = [x for x in by_min[(r.day, r.time)] if grp.get(str(x["sid"])) == g and str(x["sid"]) != str(r.sid)]
        mate_T = sum(1 for x in mates if tri(x))
        mate_big = [x["flow"]["big30"][0] for x in mates]
        rows.append(dict(bs5=f["big5_share"], rs5=f["ret5_share"], bs30=fl["big30"][0], rs30=fl["ret30"][0], bsd=fl["big_day"][0],
                         r_big5=f["big5_rank"], r_ret5=f["ret5_rank"], r_big30=fl["big30"][1], r_ret30=fl["ret30"][1], r_bigd=fl["big_day"][1],
                         r_diff30=fl["diff30"][1], dist=c["dist_hi"], amtp=c["amt_pct"], chg=c["chg_cm"], big5_net=f["big5_net"],
                         hh=m.hms(c["time"]) / 3600, mate_T=mate_T, mates=len(mates),
                         g_rank_big30=(sum(1 for v in mate_big if v > fl["big30"][0]) / len(mates)) if mates else np.nan))
    X = pd.concat([tr.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    X["k"] = list(zip(X.day, X.time, X.sid.astype(str)))
    X["inpop"] = X.k.isin(popk)
    X["old_gate"] = (X.dist <= -0.5) & (X.amtp >= 30)
    X.to_pickle(OUT / "X.pkl")
    print("成交", len(X), "母體成交", int(X.inpop.sum()), "母體∧舊寬門檻", int((X.inpop & X.old_gate).sum()))


if __name__ == "__main__":
    main()
