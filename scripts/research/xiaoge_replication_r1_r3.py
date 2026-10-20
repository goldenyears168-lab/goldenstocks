#!/usr/bin/env python3
"""複製檢定 R1（關鍵分點重現）與 R3-代理（做空名單與隔日沖慣性的關聯）。唯讀。

R1：小哥點名為「關鍵分點／地緣／高手」且 repo 有分點代號的 (分點, 個股)，在我們以 2024-07～2026-07-16 分點 tape
    估算的「區間損益」排名（同股所有分點）中的百分位。損益＝Σ(賣−買)×當日收盤＋期末淨部位×期末收盤（估算，非逐筆成交價）。
    同時列出可成(2474) 的損益前 15 名，對照小哥 2022 年畫面的名單。
R3-proxy：地面真值 75 篇做空（2024-06～2025-12）。對評估期內（≥2024-10-01）的選股，查「監控表日」該股買超前 5 名分點中
    本檔慣犯（近 90 日同股≥2 次大買後隔日賣）的個數，與同日流動性母體的比例相比。注意：小哥的原條件用權證買超排名，這裡只是現貨代理。
輸出：${DATA}/research/xiaoge_flip_habit/r1_r3.json
"""
from __future__ import annotations

import json
import pickle
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DB = Path.home() / "goldenstocks-data/data/stocks.db"
OUT = Path.home() / "goldenstocks-data/research/xiaoge_flip_habit"
START, END = "2024-07-01", "2026-07-16"


def r1(c):
    cfg = json.loads((ROOT / "config/xiaoge_named_branches.json").read_text(encoding="utf-8"))
    pairs = []
    for e in cfg["branches"]:
        if e.get("role") not in ("關鍵分點", "地緣", "高手", "公司派"):
            continue
        codes = {m for s in e["stocks"] for m in re.findall(r"\((\d{4})\)", s)}
        for tid in e["ids"]:
            for sc in codes:
                pairs.append((e["name"], e["role"], tid, sc))
    stocks = sorted({p[3] for p in pairs} | {"2474"})
    names = dict(c.execute("select stock_id,name from stock_beta").fetchall())
    bnames = json.loads((ROOT / "config/xiaoge_all_branches.json").read_text(encoding="utf-8"))["branches"]
    res, detail = [], {}
    for sc in stocks:
        df = pd.read_sql_query("select trade_date d, securities_trader_id br, buy, sell from stock_broker_branch_daily "
                               "where stock_id=? and trade_date between ? and ?", c, params=(sc, START, END))
        px = pd.read_sql_query("select trade_date d, close from stock_daily_bars where stock_id=? and source in ('twse_mi_index','tpex_daily') "
                               "and trade_date between ? and ?", c, params=(sc, START, END)).drop_duplicates("d").set_index("d")["close"]
        if df.empty or px.empty:
            continue
        df["buy"] = df["buy"].fillna(0); df["sell"] = df["sell"].fillna(0)
        df = df[df["d"].isin(px.index)]
        ok = df.groupby("d")["buy"].sum()
        df = df[df["d"].isin(ok[ok > 0].index)]              # 只用有 tape 的日子
        df["c"] = df["d"].map(px)
        df["cash"] = (df["sell"] - df["buy"]) * df["c"]
        g = df.groupby("br").agg(cash=("cash", "sum"), pos=("buy", "sum"), sold=("sell", "sum"))
        g["pos"] = g["pos"] - g["sold"]
        g["pnl"] = g["cash"] + g["pos"] * float(px.iloc[-1])
        g = g.sort_values("pnl", ascending=False)
        g["rank"] = np.arange(1, len(g) + 1)
        detail[sc] = {"name": names.get(sc, ""), "n_branches": int(len(g)),
                      "top15": [(b, bnames.get(b, ""), round(float(r.pnl) / 1e8, 2)) for b, r in g.head(15).iterrows()]}
        for nm, role, tid, code in pairs:
            if code == sc and tid in g.index:
                r = g.loc[tid]
                res.append({"branch": nm, "role": role, "id": tid, "stock": sc, "stock_name": names.get(sc, ""), "rank": int(r["rank"]),
                            "n": int(len(g)), "pct_from_top": float(1 - (r["rank"] - 1) / len(g)), "pnl_100m": round(float(r.pnl) / 1e8, 2)})
        print(sc, names.get(sc, ""), len(g), flush=True)
    return res, detail


def r3(c):
    d = pickle.load(open(OUT / "events.pkl", "rb"))
    ev, cal = d["events"], d["cal"]
    ev = ev[ev["in_p"]].copy()
    ev["date"] = ev["idx"].map(lambda i: cal[i])
    gt = pd.read_csv(ROOT / "docs/warrant-xiaoge/groundtruth_2024-06_2025-12.csv", dtype=str)
    gt = gt[gt["side"] == "short"].copy()
    calset = sorted(cal)
    def prev_td(dt):
        i = np.searchsorted(calset, dt, side="left")
        return calset[i - 1] if i > 0 else None
    rows = []
    for r in gt.itertuples():
        t = r.table_date if isinstance(r.table_date, str) and re.match(r"\d{4}-\d{2}-\d{2}", r.table_date or "") else None
        t = (t or prev_td(r.article_date)).replace("/", "-")
        if t < "2024-10-01" or t not in set(calset):
            continue
        code = r.code_final if hasattr(r, "code_final") and isinstance(r.code_final, str) else r.stock_code
        sub = ev[(ev["stock_id"] == code) & (ev["date"] == t) & (ev["rank"] <= 5) & (ev["share"] >= 0.03)]
        rows.append({"article": r.article_date, "table_date": t, "stock": code, "n_big_buyers": int(len(sub)),
                     "n_habit": int((sub["A70_90_p"] >= 2).sum()), "has_events": bool(len(sub))})
    pick = pd.DataFrame(rows)
    # 基準：同日期、同評估期內，所有（流動性不限）股票日的相同統計
    base = ev[(ev["rank"] <= 5) & (ev["share"] >= 0.03) & (ev["date"].isin(set(pick["table_date"])) if len(pick) else False)]
    bs = base.groupby(["stock_id", "date"]).agg(nh=("A70_90_p", lambda s: int((s >= 2).sum())), n=("br", "size")).reset_index()
    out = {"n_picks": int(len(pick)), "picks_with_events": int(pick["has_events"].sum()),
           "pick_share_ge2_habit": float((pick["n_habit"] >= 2).mean()) if len(pick) else None,
           "pick_mean_habit": float(pick["n_habit"].mean()) if len(pick) else None,
           "base_share_ge2_habit_same_dates": float((bs["nh"] >= 2).mean()), "base_mean_habit": float(bs["nh"].mean()),
           "base_stockdays": int(len(bs)), "detail": rows[:80]}
    return out


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.execute("pragma query_only=1")
    r3res = r3(c)
    print(json.dumps({k: v for k, v in r3res.items() if k != "detail"}, ensure_ascii=False), flush=True)
    res, detail = r1(c)
    pct = [x["pct_from_top"] for x in res]
    out = {"R3_proxy": r3res, "R1": {"pairs": res, "median_pct_from_top": float(np.median(pct)) if pct else None,
                                      "share_top15": float(np.mean([x["rank"] <= 15 for x in res])) if res else None,
                                      "share_top10pct": float(np.mean([x["pct_from_top"] >= 0.9 for x in res])) if res else None},
           "stocks_detail": detail}
    (OUT / "r1_r3.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["R1"] | {"pairs": len(res)}, ensure_ascii=False)[:600], flush=True)


if __name__ == "__main__":
    main()
