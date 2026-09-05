#!/usr/bin/env python3
"""大戶損益 5 分鐘格重算（每格帳歸零），重點放開盤第一小時。

每格：只看該 5 分鐘內成交的大單（單筆>=THRESH），用加權平均成本記帳，
      已實現(多平/空補) + 該格結束時剩餘淨部位的未實現。
格與格之間互不繼承 —— 所以每格是一個近乎獨立的觀測。
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
sys.path.insert(0, "src")
from stock_db import DATA_DIR
sys.path.insert(0, "scripts/research")
from biglot_unrealized import load, book   # noqa: E402


def buckets(step_min=5, start="09:00", end="13:30"):
    h0, m0 = int(start[:2]), int(start[3:]); h1, m1 = int(end[:2]), int(end[3:])
    s = h0 * 60 + m0; e = h1 * 60 + m1; out = []
    while s < e:
        n = min(s + step_min, e)
        out.append((f"{s//60:02d}:{s%60:02d}", f"{s//60:02d}:{s%60:02d}:00",
                    f"{n//60:02d}:{n%60:02d}:00"))
        s = n
    return out


def run(date, thresh, step):
    df = load(date)
    big = df[(df.amt >= thresh) & (df.d != 0)]
    BK = buckets(step)
    rows = []
    for s, g in df.groupby("sym"):
        b = big[big.sym == s]
        if len(b) < 10:
            continue
        for lab, t0, t1 in BK:
            P = g[g.t < t1]
            if not len(P):
                continue
            P = float(P.px.iloc[-1])
            w = b[(b.t >= t0) & (b.t < t1)]
            if not len(w):
                continue
            rL, rS, pos, avg, qL, qS = book(list(zip(w.d, w.sz, w.px)))
            un = pos * 1000 * (P - avg) if pos else 0.0
            rows.append(dict(date=date, sym=s, bkt=lab, n=len(w),
                             notional=float((w.sz * 1000 * w.px).sum()),
                             real_L=rL, real_S=rS, unreal=un, pos=pos,
                             pnl=rL + rS + un))
    return pd.DataFrame(rows), [b[0] for b in BK]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=5_000_000)
    ap.add_argument("--step", type=int, default=5)
    a = ap.parse_args()
    uni = json.loads((DATA_DIR / "cache/pit_universe_tick/_hivol_universe_v3.json").read_text())
    V3 = {x["sid"]: x["name"] for x in uni["universe"]}
    try:
        info = pd.read_pickle("/tmp/stock_info.pkl")
        NM = dict(zip(info.stock_id.astype(str), info.stock_name))
    except Exception:
        NM = {}
    NAME = lambda s: V3.get(s, NM.get(s, ""))
    res = {}
    for date in ("2026-09-03", "2026-09-04"):
        r, BK = run(date, a.thresh, a.step); res[date] = r
        first = [x for x in BK if x < "10:00"]
        print(f"\n{'='*100}\n### {date} 開盤第一小時 · 每 {a.step} 分鐘（萬元，格與格獨立）")
        print(f"{'時段':<8}{'檔數':>5}{'大單筆':>7}{'投入(億)':>9}{'合計':>10}"
              f"{'已實現多':>10}{'已實現空':>10}{'未實現':>10}   最賺 / 最賠")
        for k in first:
            s = r[r.bkt == k]
            if s.empty:
                continue
            top = s.nlargest(1, "pnl").iloc[0]; bot = s.nsmallest(1, "pnl").iloc[0]
            print(f"{k:<8}{s.sym.nunique():>5}{int(s.n.sum()):>7}{s.notional.sum()/1e8:>9.1f}"
                  f"{s.pnl.sum()/1e4:>10,.0f}{s.real_L.sum()/1e4:>10,.0f}"
                  f"{s.real_S.sum()/1e4:>10,.0f}{s.unreal.sum()/1e4:>10,.0f}   "
                  f"{NAME(top.sym) or top.sym} {top.pnl/1e4:+,.0f} / "
                  f"{NAME(bot.sym) or bot.sym} {bot.pnl/1e4:+,.0f}")
        rest = r[r.bkt >= "10:00"]
        print(f"{'10:00 以後合計':<8}{rest.sym.nunique():>0}檔 {int(rest.n.sum())}筆 "
              f"{rest.notional.sum()/1e8:.1f}億 {rest.pnl.sum()/1e4:,.0f} 萬")
        f1 = r[r.bkt < "10:00"].pnl.sum()
        print(f"  第一小時 {f1/1e4:,.0f} 萬 / 全日 {r.pnl.sum()/1e4:,.0f} 萬 = "
              f"{f1/r.pnl.sum()*100:.1f}%")

    print(f"\n{'='*100}\n### 統計性質（{a.step} 分鐘格 vs 1 小時格）")
    for date in res:
        r = res[date]
        p = r.pivot_table(index="sym", columns="bkt", values="pnl")
        cols = sorted(p.columns)
        ac = np.nanmean([p[cols[i]].corr(p[cols[i + 1]]) for i in range(len(cols) - 1)
                         if p[cols[i]].notna().sum() > 5 and p[cols[i + 1]].notna().sum() > 5])
        print(f"  {date}  相鄰 5 分鐘格相關 = {ac:+.3f}；有大單的 (檔,格) 觀測 = {len(r)}")

    print(f"\n### 第一小時逐格 · 兩日對照（全體合計，萬元）")
    BK5 = [x for x in sorted(set(res['2026-09-04'].bkt)) if x < "10:00"]
    print(f"{'時段':<8}{'09-03':>10}{'09-04':>10}   {'同號':>4}")
    for k in BK5:
        a3 = res["2026-09-03"].query("bkt==@k").pnl.sum() / 1e4
        a4 = res["2026-09-04"].query("bkt==@k").pnl.sum() / 1e4
        print(f"{k:<8}{a3:>10,.0f}{a4:>10,.0f}   {'✓' if a3*a4>0 else '':>4}")


if __name__ == "__main__":
    main()
