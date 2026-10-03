#!/usr/bin/env python3
"""手動下單判準的尺度檢定（只看最近四天，不外推）。

使用者自述的型態：大戶占比「逐漸變多」、散戶占比「逐漸下降」，且偏好
「大戶不賣、散戶在賣」時進場撿便宜。本腳本把這些描述翻成可算的量：
  · 水位：[t−W, t] 的大戶淨占比／散戶淨占比
  · 斜率：[t−30m, t] 的占比 減 [t−60m, t−30m] 的占比（＝「逐漸變多」）
  · 型態：大戶淨 ≥ 0（不賣）∧ 散戶淨 < 0（在賣）
並比較「族群內相對排名」與「全宇宙相對排名」哪個對事後報酬更有解釋力。

事後報酬一律用**標的現股**在下單後的前瞻報酬，把使用者的出場技巧隔離掉；
另外扣掉同族群其他檔的同期平均報酬，得到特異報酬（＝不是整片一起漲的部分）。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from trader_biglot_decision_replay import replay_day, window  # noqa: E402
from trader_peer_group_choice import day_universe, load_subcat  # noqa: E402

DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]


def seg(A, t, lo_sec, hi_sec):
    """[t−hi, t−lo] 這一段的大戶淨占比／散戶淨占比（lo=0 就是到現在）。"""
    ts = A["ts"]
    if not ts:
        return None, None
    j = bisect.bisect_right(ts, t - lo_sec)
    i = bisect.bisect_left(ts, t - hi_sec)
    if j <= i:
        return None, None
    tot = A["cum_amt"][j] - A["cum_amt"][i]
    if not tot:
        return None, None
    return (100 * (A["cum_big"][j] - A["cum_big"][i]) / tot,
            100 * (A["cum_ret"][j] - A["cum_ret"][i]) / tot)


def fwd(A, t, sec=None):
    ts, px = A["ts"], A["px"]
    i = bisect.bisect_right(ts, t) - 1
    if i < 0:
        return None
    j = len(ts) - 1 if sec is None else bisect.bisect_right(ts, t + sec) - 1
    return 100 * (px[j] / px[i] - 1) if j > i else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decision", default=str(Path.home() / "goldenstocks-data/data/scratch/trader_biglot_decision.csv"))
    ap.add_argument("--out", default=str(Path.home() / "goldenstocks-data/data/scratch/trader_slope_features.csv"))
    args = ap.parse_args()
    sub = load_subcat()
    c = pd.read_csv(args.decision)
    c = c[c["big_day"].notna() & c["day"].isin(DAYS)].copy()
    c["stock"] = c["stock"].astype(str)

    rows = []
    for day, g in c.groupby("day"):
        uni = day_universe(day)
        acc = {k: v for k, v in replay_day(day, uni).items() if v["ts"]}
        print(f"{day}: 宇宙 {len(uni)} · 有逐筆 {len(acc)}")
        for r in g.itertuples():
            A = acc.get(r.stock)
            if not A:
                continue
            t = datetime.fromisoformat(f"{day}T{r.submit}+08:00").timestamp()
            rec = {"day": day, "submit": r.submit, "stock": r.stock, "uname": r.uname,
                   "side": r.side, "order_type": r.order_type, "status": r.status,
                   "group": sub.get(r.stock)}
            # 水位
            for lab, w in (("5m", 300), ("15m", 900), ("30m", 1800), ("60m", 3600)):
                bb, rr = seg(A, t, 0, w)
                rec[f"big_{lab}"] = bb if bb is None else round(bb, 2)
                rec[f"ret_{lab}"] = rr if rr is None else round(rr, 2)
            bd, rd = window(A, t, None)[0], window(A, t, None)[1]
            rec["big_day"] = None if bd is None else round(bd, 2)
            rec["ret_day"] = None if rd is None else round(rd, 2)
            # 斜率：近 30 分 vs 前一段 30 分
            b_prev, r_prev = seg(A, t, 1800, 3600)
            b_now, r_now = seg(A, t, 0, 1800)
            rec["big_prev30"] = None if b_prev is None else round(b_prev, 2)
            rec["ret_prev30"] = None if r_prev is None else round(r_prev, 2)
            rec["big_slope"] = (None if (b_prev is None or b_now is None)
                                else round(b_now - b_prev, 2))
            rec["ret_slope"] = (None if (r_prev is None or r_now is None)
                                else round(r_now - r_prev, 2))
            # 全日占比的斜率：現在的全日占比 vs 30 分鐘前的全日占比
            bd30 = window(A, t - 1800, None)[0]
            rec["big_day_30min_ago"] = None if bd30 is None else round(bd30, 2)
            rec["big_day_slope"] = (None if (bd is None or bd30 is None) else round(bd - bd30, 2))
            rd30 = window(A, t - 1800, None)[1]
            rec["ret_day_slope"] = (None if (rd is None or rd30 is None) else round(rd - rd30, 2))
            # 前瞻
            for lab, s in (("f15", 900), ("f30", 1800), ("f60", 3600), ("f_close", None)):
                v = fwd(A, t, s)
                rec[lab] = None if v is None else round(v, 3)
            # 族群同期平均（排除自己）→ 特異報酬
            peers = [s for s in uni if sub.get(s) == rec["group"] and s in acc and s != r.stock]
            for lab, s in (("f30", 1800), ("f_close", None)):
                vals = [fwd(acc[p], t, s) for p in peers]
                vals = [v for v in vals if v is not None]
                gm = sum(vals) / len(vals) if vals else None
                rec[f"grp_{lab}"] = None if gm is None else round(gm, 3)
                rec[f"idio_{lab}"] = (None if (gm is None or rec[lab] is None)
                                      else round(rec[lab] - gm, 3))
            rec["n_peers"] = len(peers)
            rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)
    print(f"wrote {args.out}  n={len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
