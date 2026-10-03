#!/usr/bin/env python3
"""逐張委託的「當下畫面快照 + 哪幾欄特別」引擎（2026-09-29～10-02，52 張新倉單）。

三個資料層：
  L1 存檔 HTML 面板（09-30 起 · 30 分一格 · 43 欄精確值 · 42 檔完整橫斷面）
  L2 原始逐筆重放（四天 · 任意秒 · 流量/動能/量能類欄位）
  L3 使用者視角列序 —— 使用者自述主表「按全日大戶淨額排序」，而存檔 HTML 是
     伺服器預設排序（實測 Spearman −0.006，完全不同），所以列序必須自己重算。

三種「特別」的定義（全部都要跟安慰劑對照比）：
  X1 橫斷面百分位：同一時點 42 檔裡排第幾
  X2 自身時序百分位：同一檔當天所有時點裡，這一刻排第幾
  X3 族群內百分位：同 SUBCAT 族群（≥2 檔）裡排第幾
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from trader_biglot_decision_replay import replay_day  # noqa: E402
from trader_peer_group_choice import day_universe, load_subcat  # noqa: E402

DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
SCR = Path.home() / "goldenstocks-data/data/scratch"
BIGCOL = "全日大戶 淨額·萬#num"
BIG_AMT = 10_000_000
RETAIL_CAP = 5_000_000


def load_orders() -> pd.DataFrame:
    specs = json.loads((Path.home() / "goldenstocks-data/data/cache/taifex_stock_futures_specs.json")
                       .read_text(encoding="utf-8"))
    d = pd.read_pickle(SCR / "trader_behavior_tables.pkl")
    o = d["futopt"]["orders"]
    o = o[~o["auto"]].copy()
    o["day"] = o["date"].dt.strftime("%Y-%m-%d")
    o = o[o["day"].isin(DAYS) & o["order_type"].eq("FutOptOrderType.New")].copy()
    o["root"] = o["symbol"].str.slice(2)
    key = o["root"].map(lambda r: r[:-1] if r.endswith("F") else r)
    o["sid"] = key.map(lambda r: specs.get(r, {}).get("stock_id"))
    o["uname"] = key.map(lambda r: specs.get(r, {}).get("name"))
    o["intent"] = np.where(o["side"].eq("Buy"), "開多", "開空")
    return o.sort_values(["day", "submit_time"]).reset_index(drop=True)


def replay_cols(acc: dict, sid: str, t: float) -> dict:
    """從逐筆重放算出流量/動能類欄位（口徑對齊 _ingest_trade）。"""
    A = acc.get(sid)
    if not A or not A["ts"]:
        return {}
    ts = A["ts"]
    j = bisect.bisect_right(ts, t)
    if j == 0:
        return {}
    out = {"rp_px": A["px"][j - 1], "rp_px0": A["px0"]}
    for lab, w in (("5m", 300), ("30m", 1800), ("day", None)):
        i = 0 if w is None else bisect.bisect_left(ts, t - w)
        if j <= i:
            continue
        tot = A["cum_amt"][j] - A["cum_amt"][i]
        big = A["cum_big"][j] - A["cum_big"][i]
        ret = A["cum_ret"][j] - A["cum_ret"][i]
        out[f"rp_big_{lab}"] = big / 1e4
        out[f"rp_bigpct_{lab}"] = 100 * big / tot if tot else None
        out[f"rp_ret_{lab}"] = ret / 1e4
        out[f"rp_retpct_{lab}"] = 100 * ret / tot if tot else None
        out[f"rp_amt_{lab}"] = tot / 1e4
        p0 = A["px"][i] if w is not None else A["px0"]
        out[f"rp_pxret_{lab}"] = 100 * (A["px"][j - 1] / p0 - 1) if p0 else None
    # 前瞻（評估用，不是決策資訊）
    for lab, w in (("f30", 1800), ("f_close", None)):
        k = len(ts) - 1 if w is None else bisect.bisect_right(ts, t + w) - 1
        out[lab] = 100 * (A["px"][k] / A["px"][j - 1] - 1) if k > j - 1 else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SCR / "trader_order_snapshots.csv"))
    args = ap.parse_args()
    sub = load_subcat()
    orders = load_orders()
    print(f"新倉委託 {len(orders)} 張（開多 {int(orders.intent.eq('開多').sum())}／"
          f"開空 {int(orders.intent.eq('開空').sum())}）· 有標的對映 {int(orders.sid.notna().sum())}")
    panel = pd.read_parquet(SCR / "html_snapshot_panel.parquet")
    numcols = [c for c in panel.columns if c.endswith("#num")]

    recs = []
    for day in DAYS:
        g = orders[orders["day"] == day]
        if g.empty:
            continue
        uni = day_universe(day)
        acc = {k: v for k, v in replay_day(day, uni).items() if v["ts"]}
        hp = panel[panel["day"] == day]
        slots = sorted(hp["slot"].unique()) if not hp.empty else []
        print(f"\n{day}: {len(g)} 張 · 逐筆 {len(acc)} 檔 · HTML 時點 {len(slots)}")
        # 每個時點的「使用者視角列序」= 按全日大戶淨額降序
        view = {}
        for s in slots:
            sl = hp[hp["slot"] == s].copy()
            sl["view_rank"] = sl[BIGCOL].rank(ascending=False, method="min")
            view[s] = sl.set_index("sid")
        for r in g.itertuples():
            t = datetime.fromisoformat(f"{day}T{r.submit_time}+08:00").timestamp()
            hm = str(r.submit_time)[:5]
            prior = [s for s in slots if s <= hm]
            slot = prior[-1] if prior else None
            rec = {"day": day, "submit": r.submit_time, "sid": r.sid, "uname": r.uname,
                   "intent": r.intent, "lot": r.lot, "limit_px": r.price,
                   "status": r.status, "grp": sub.get(r.sid),
                   "html_slot": slot,
                   "stale_min": (None if slot is None else
                                 round((t - datetime.fromisoformat(
                                     f"{day}T{slot}:00+08:00").timestamp()) / 60, 1))}
            # --- L3 使用者視角列序（兩種來源都算）
            if slot and r.sid in view[slot].index:
                row = view[slot].loc[r.sid]
                rec["view_rank_html"] = float(row["view_rank"])
                rec["n_universe_html"] = int(view[slot][BIGCOL].notna().sum())
                for c in numcols:
                    v = row.get(c)
                    rec[f"H:{c[:-4]}"] = v
                    col = view[slot][c]
                    if pd.notna(v) and col.notna().sum() >= 10:
                        rec[f"P:{c[:-4]}"] = round(100 * (col < v).mean(), 1)
            # 用逐筆重算的視角列序（四天都有，9/29 唯一來源）
            bigday = {}
            for s2, A in acc.items():
                ts = A["ts"]
                j = bisect.bisect_right(ts, t)
                if j:
                    bigday[s2] = A["cum_big"][j]
            if r.sid in bigday and len(bigday) >= 10:
                ser = pd.Series(bigday)
                rec["view_rank_replay"] = int((ser > ser[r.sid]).sum()) + 1
                rec["n_universe_replay"] = len(ser)
            rec.update(replay_cols(acc, r.sid, t))
            recs.append(rec)
    out = pd.DataFrame(recs)
    out.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}  {len(out)} 張 × {len(out.columns)} 欄")
    print(f"有 HTML 快照 {int(out.html_slot.notna().sum())} 張 · "
          f"落後中位 {out.stale_min.median():.1f} 分 · 最久 {out.stale_min.max():.1f} 分")
    print(f"有視角列序(重放) {int(out.view_rank_replay.notna().sum())} 張")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
