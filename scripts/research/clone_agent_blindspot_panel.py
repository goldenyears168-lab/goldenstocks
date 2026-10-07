#!/usr/bin/env python3
"""研究用:盲點掃描面板。把「所有」名單內價格帶內卡片(不套任何進場閘門)配上現貨前瞻報酬(30/60/90分/到收盤)、同窗其餘12檔等權基準、進場前30分個股/籃子報酬。
卡片價假設成交(+5秒),出場=現貨最後成交。輸出 blind_panel.csv(IS+HOLD 合併,set 欄區分)。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_blindspot_panel.py
"""
from __future__ import annotations

import bisect
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import clone_agent_wide5m_backtest as m  # noqa: E402
import trader_signal_watch as w  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
HOLD_DAYS = ["2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15",
             "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]
LAG, HOLDS = 5, (30, 60, 90)


def ep(day, hms):
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


def main():
    R = load_rules()
    members, band = universe_sids(R), tuple(R["universe"]["price_band"])
    rows = []
    for setname, days, src in (("IS", m.DAYS, S / "clone_agent_w5_nogate"), ("HOLD", HOLD_DAYS, S / "clone_agent_w5_hold")):
        for d in days:
            cards = [json.loads(x) for x in (src / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            cards = [c for c in cards if str(c["sid"]) in members and band[0] <= c["price"] <= band[1]]
            tape = w.Tape(d)
            tape.poll()
            ser = {s: ([t for t, _ in tape._pxev.get(s, ())], [p for _, p in tape._pxev.get(s, ())]) for s in members}
            close = ep(d, "13:30:00")

            def px(s, t):
                ts, ps = ser[s]
                i = bisect.bisect_right(ts, t) - 1
                return ps[i] if i >= 0 else None

            def bret(sid, a_t, b_t):
                xs = []
                for s in members:
                    if s == sid:
                        continue
                    a, b = px(s, a_t), px(s, b_t)
                    if a and b:
                        xs.append(1e4 * (b / a - 1))
                return float(np.mean(xs)) if xs else np.nan

            for c in cards:
                sid, t = str(c["sid"]), ep(d, c["time"])
                t0, L = t + LAG, c["price"]
                a, b = px(sid, t - 1800), px(sid, t)
                fr = c["flow_ranks"]
                rec = dict(set=setname, day=d, time=c["time"], hh=int(c["time"][:2]) + int(c["time"][3:5]) / 60, sid=sid, stars=c["stars"], dist_hi=c["dist_hi"],
                           amt_pct=c["amt_pct"], chg_cm=c["chg_cm"], r_bigday=fr["big_day"], r_big30=fr["big30"], r_ret30=fr["ret30"], r_diff30=fr["diff30"], r_mid30=fr["mid30"],
                           big5=c["f5"]["big5_net"], ret5=c["f5"]["ret5_net"], diff5_rank=c["f5"]["diff5_rank"], big30=c["flow"]["big30"][0], ret30=c["flow"]["ret30"][0],
                           s1=c["strict"][0], s2=c["strict"][1], s3=c["strict"][2], s4=c["strict"][3],
                           pre_stock30=1e4 * (b / a - 1) if a and b else np.nan, pre_bench30=bret(sid, t - 1800, t))
                for h in HOLDS + ("close",):
                    t1 = close if h == "close" else t0 + h * 60
                    ok = t0 < close and t1 <= close and (h == "close" or True)
                    p1 = px(sid, min(t1, close))
                    full = (h == "close") or (t0 + h * 60 <= close)
                    rec[f"g{h}"] = 1e4 * (p1 / L - 1) if p1 and ok and full else np.nan
                    rec[f"b{h}"] = bret(sid, t0, min(t1, close)) if p1 and ok and full else np.nan
                rows.append(rec)
            print("done", d, len(cards), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(S / "blind_panel.csv", index=False)
    print("寫出", len(df))


if __name__ == "__main__":
    main()
