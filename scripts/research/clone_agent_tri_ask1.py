#!/usr/bin/env python3
"""研究用:V0 現行寬門檻 / V0★4 / H0 改「賣一掛單」(買進以賣一立即成交,不排隊)的每筆報酬率。
進場=卡片時間+5秒當下期貨簿賣一;出場=持有 60/90 分後(上限 13:30)的最後成交價或買一。成本=手續費2×176+期貨稅2e-5/邊。
不套保證金、價格帶(題設);冷卻沿用各變體 replay。用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_tri_ask1.py IS|HOLD
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
import clone_agent_day_backtest as bt  # noqa: E402
import clone_agent_tri_holdout as th  # noqa: E402
import clone_agent_wide5m_backtest as m  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

FEE, TAX, LAG, CLOSE = 176.0, 2e-5, 5, "13:30:00"
HOLDS = (60, 90)
VARS = {k: th.PRED[k] for k in ("V0 現行寬門檻", "H0 三尺度∧距高∧額≥30")}


def main():
    R = load_rules()
    members, band = universe_sids(R), tuple(R["universe"]["price_band"])
    rows = []
    for d in th.DAYS:
        cards = [json.loads(x) for x in (th.SRC / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        p = bt.CACHE / f"biglot_live_watch/futprice_{d}.json"
        fp = json.loads((p if p.exists() else bt.CACHE / "biglot_live_watch/futprice_2026-09-22.json").read_text(encoding="utf-8"))
        fired = {}
        for v, pr in VARS.items():
            m.old_ok = lambda c: True
            m.new_ok = lambda c, tag, pr=pr: pr(c)
            fired[v] = m.replay(cards, ("X",), band, members)
        fired["V0★4(現行實單)"] = [c for c in fired["V0 現行寬門檻"] if c["stars"] == 4]
        tc, bc = {}, {}
        for v, cs in fired.items():
            for c in cs:
                sid = c["sid"]
                root = bt.resolve_root((fp.get(sid) or {}).get("sym", ""), d)
                rec = dict(day=d, var=v, sid=sid, time=c["time"], stars=c["stars"], card_px=c["price"], shares=c["shares"], root=root)
                tr = tc.setdefault(root, bt.load_trades(root, d)) if root else None
                if not tr:
                    rec["note"] = "no_fut_tape"
                    rows.append(rec)
                    continue
                bk = bc.setdefault(root, bt.load_books(root, d))
                bt_ = [b[0] for b in bk]
                t0 = bt.epoch(d, c["time"]) + LAG
                i = bisect.bisect_right(bt_, t0) - 1
                if i < 0 or not np.isfinite(bk[i][3]):
                    rec["note"] = "no_ask"
                    rows.append(rec)
                    continue
                ask, tcl = bk[i][3], bt.epoch(d, CLOSE)
                rec["ask"] = ask
                ts = [x[0] for x in tr]
                for h in HOLDS:
                    t1 = t0 + h * 60
                    rec[f"trunc{h}"] = t1 > tcl
                    k = max(bisect.bisect_right(ts, min(t1, tcl)) - 1, 0)
                    j = max(bisect.bisect_left(ts, t0), 0)
                    last = tr[k][1]
                    bids = [x[2] for x in tr[j:k + 1] if x[2]]
                    bid = bids[-1] if bids else last
                    for kind, px in (("last", last), ("bid", bid)):
                        gross = (px - ask) * c["shares"]
                        cost = 2 * FEE + TAX * (ask + px) * c["shares"]
                        rec[f"bps{h}_{kind}"] = 1e4 * (px / ask - 1)
                        rec[f"net{h}_{kind}"] = gross - cost
                        rec[f"netbps{h}_{kind}"] = 1e4 * (gross - cost) / (ask * c["shares"])
                rows.append(rec)
    df = pd.DataFrame(rows)
    out = th.OUT / "ask1_trades.csv"
    df.to_csv(out, index=False)
    ok = df[df.get("ask").notna()] if "ask" in df else df.iloc[0:0]
    print(f"{th.MODE}: 訊號 {df.groupby('var').size().to_dict()} 有賣一報價 {ok.groupby('var').size().to_dict()}")
    rng = np.random.default_rng(7)

    def dayci(x, col):
        g = x.groupby("day")[col].mean().values
        mu = g.mean()
        bs = rng.choice(g, (4000, len(g))).mean(1)
        return mu, np.percentile(bs, 5), np.percentile(bs, 95)

    pd.set_option("display.width", 250, "display.max_columns", 40)
    for h in HOLDS:
        for kind in ("bid", "last"):
            tab = []
            for v, x in ok.groupby("var"):
                x = x[~x[f"trunc{h}"].astype(bool)]
                if not len(x):
                    continue
                mu, lo, hi = dayci(x, f"bps{h}_{kind}")
                tab.append(dict(變體=v, 成交=len(x), 天=x.day.nunique(), 毛bps均=round(x[f"bps{h}_{kind}"].mean(), 1), 毛bps中位=round(x[f"bps{h}_{kind}"].median(), 1),
                                 日均毛=round(mu, 1), 日CI=f"{lo:.0f}~{hi:.0f}", 淨bps均=round(x[f"netbps{h}_{kind}"].mean(), 1),
                                 每筆淨NT=round(x[f"net{h}_{kind}"].mean()), 勝率=round(100 * (x[f"net{h}_{kind}"] > 0).mean())))
            print(f"\n== {th.MODE} 持有{h}分 出場={'買一(吃價)' if kind == 'bid' else '最後成交'} (不含未滿持有期者) ==")
            print(pd.DataFrame(tab).to_string(index=False))
    print("\n進場滑價 賣一 vs 卡片價(bps):", ok.assign(s=1e4 * (ok.ask / ok.card_px - 1)).groupby("var").s.median().round(1).to_dict())


if __name__ == "__main__":
    main()
