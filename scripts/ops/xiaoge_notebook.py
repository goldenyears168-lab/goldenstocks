#!/usr/bin/env python3
"""每日小哥籌碼筆記本（Phase 2）· 盤後 · 唯讀 DB · 不下單。

讀 `xiaoge_chip_daily.py` 當日落地的 parquet（個股前 15 名、分點頁）、日線與法人（唯讀 stocks.db），
產生 `${DATA}/chip/xiaoge_notebook/notebook_<日期>.json`（與 latest），供 8771 `/xiaoge` 頁面讀取。
章節：大盤籌碼、今日名單（代理版）、分點雷達（買超前 5 名＋小哥具名分點標示＋隔日沖慣犯）、故事重放、昨日覆盤、記分板、資料覆蓋。
隔日沖慣犯＝近 90 日該分點在該股「大買後隔日賣出」≥2 次（口徑與協議 docs/warrant-xiaoge/flip-habit-study-protocol.md 一致；
史料不足的股票不判讀，頁面顯示累積天數）。
註：做空／做多名單為**代理版**——小哥原做法用「主力收購權證監控表」的權證買超排名，我們尚無該資料。
用法：PYTHONPATH=src .venv/bin/python scripts/ops/xiaoge_notebook.py [--date 2026-10-20]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

CHIP = DATA_DIR / "chip" / "xiaoge"
OUT = DATA_DIR / "chip" / "xiaoge_notebook"
TOPN, MINSH, ALPHA, WIN, MINF = 5, 0.03, 0.7, 90, 2
HIST_MIN_DAYS = 30


def load_named():
    cfg = json.loads((ROOT / "config/xiaoge_named_branches.json").read_text(encoding="utf-8"))
    by_id = {}
    for e in cfg["branches"]:
        for tid in e["ids"]:
            by_id[tid] = e
    return cfg, by_id


def trade_days(c, end, n):
    r = c.execute("select distinct trade_date from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date<=? "
                  "order by trade_date desc limit ?", (end, n)).fetchall()
    return sorted(x[0] for x in r)


def load_history(c, days, stocks):
    """days 內每日的分點列：DB 全 tape 或 parquet（個股前 15）。回傳 dict[(sid,d)] = (DataFrame[br,buy,sell], full:bool)。"""
    H = {}
    lo, hi = days[0], days[-1]
    q = ("select trade_date, stock_id, securities_trader_id as br, buy, sell from stock_broker_branch_daily "
         "where trade_date between ? and ?")
    for d in days:
        df = pd.read_sql_query(q, c, params=(d, d))
        df = df[df["stock_id"].isin(stocks)]
        for sid, g in df.groupby("stock_id"):
            if len(g) < 25:
                continue                              # 收集池外零星列（per-branch backfill 殘影，約 9 列）不算資料
            full = len(g) >= 300                      # 完整 tape 每股數百分點
            H[(sid, d)] = (g[["br", "buy", "sell"]].reset_index(drop=True), full)
        pq = CHIP / d / "stock_top15.parquet"
        if pq.exists():
            p = pd.read_parquet(pq)
            for sid, g in p.groupby("stock_id"):
                if (sid, d) in H and H[(sid, d)][1]:
                    continue
                H[(sid, d)] = (pd.DataFrame({"br": g["branch_id"].to_numpy(), "buy": g["buy_lots"].to_numpy() * 1000.0,
                                             "sell": g["sell_lots"].to_numpy() * 1000.0}), False)
    return H


def build_events(H, vol_by, days):
    """一次建好：(股票, 分點) → [(日序 i, 隔日沖旗標)]（A 口徑；不可觀測者不收）。"""
    EV = {}
    for i in range(len(days) - 1):
        d, nd = days[i], days[i + 1]
        for (sid, dd), (rows, full) in list(((k, v) for k, v in H.items() if k[1] == d)):
            if (sid, nd) not in H:
                continue
            v = vol_by.get((sid, d))
            if not v:
                continue
            net = (rows["buy"] - rows["sell"]).to_numpy()
            order = np.argsort(-net)[:TOPN]
            nxt, nfull = H[(sid, nd)]
            nsell = dict(zip(nxt["br"].to_numpy(), nxt["sell"].to_numpy()))
            brs = rows["br"].to_numpy()
            for j in order:
                if net[j] <= 0 or net[j] < MINSH * v:
                    continue
                b = brs[j]
                if b not in nsell and not nfull:
                    continue
                sell_n = float(nsell.get(b, 0.0))
                EV.setdefault((sid, b), []).append((i, int(sell_n >= ALPHA * net[j])))
    return EV


def habit_for(EV, days, sid, br, t_idx):
    """分點 br 在股 sid 的近 90 日大買事件與隔日沖次數（只用事件日序 ≤ t−2）。回傳 (n, flips, hist_days)。"""
    t = days[t_idx]; lo = (datetime.fromisoformat(t) - timedelta(days=WIN)).date().isoformat()
    n = f = 0
    for i, fl in EV.get((sid, br), []):
        if i <= t_idx - 2 and days[i] >= lo:
            n += 1; f += fl
    return n, f, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    c.execute("pragma query_only=1")
    D = a.date or c.execute("select max(trade_date) from stock_daily_bars where source in ('twse_mi_index','tpex_daily')").fetchone()[0]
    pq = CHIP / D / "stock_top15.parquet"
    if not pq.exists():
        print(f"{D} 尚無 stock_top15.parquet（先跑 xiaoge_chip_daily.py）", file=sys.stderr); return 3
    cfg, by_id = load_named()
    top = pd.read_parquet(pq)
    meta = json.loads((CHIP / D / "meta.json").read_text(encoding="utf-8")) if (CHIP / D / "meta.json").exists() else {}
    bp = CHIP / D / "branch_pages.parquet"
    bpages = pd.read_parquet(bp) if bp.exists() else pd.DataFrame()
    days = trade_days(c, D, 66)
    t_idx = days.index(D)
    stocks = set(top["stock_id"].unique())
    bars = pd.read_sql_query(
        "select stock_id, trade_date, open, close, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') "
        "and trade_date between ? and ? and stock_id glob '[1-9][0-9][0-9][0-9]'", c, params=(days[0], D)).drop_duplicates(["stock_id", "trade_date"])
    names = dict(c.execute("select stock_id, name from stock_beta").fetchall())
    inst = pd.read_sql_query("select stock_id, foreign_net, investment_trust_net, dealer_self_net from stock_institutional_daily where trade_date=?", c, params=(D,))
    inst = inst.drop_duplicates("stock_id").set_index("stock_id")
    H = load_history(c, days, stocks)
    vol_by = {(r.stock_id, r.trade_date): float(r.volume) for r in bars.itertuples()}
    EV = build_events(H, vol_by, days)
    hist_by = {}
    for (sid, dd) in H:
        hist_by[sid] = hist_by.get(sid, 0) + (1 if dd >= (datetime.fromisoformat(D) - timedelta(days=WIN)).date().isoformat() and dd < D else 0)
    bd = bars.sort_values(["stock_id", "trade_date"])
    cand, radar = [], []
    mkt_pos = mkt_neg = 0
    for sid, g in top.groupby("stock_id"):
        v = vol_by.get((sid, D))
        b = bd[bd.stock_id == sid]
        if not v or b.empty or b.iloc[-1].trade_date != D:
            continue
        close = float(b.iloc[-1].close); prev = float(b.iloc[-2].close) if len(b) > 1 else close
        g = g.assign(net=(g["buy_lots"] - g["sell_lots"]) * 1000.0).sort_values("net", ascending=False)
        buyers = g[g["net"] > 0].head(15); sellers = g[g["net"] < 0].tail(15)
        main_net = float(buyers["net"].sum() + sellers["net"].sum())          # 買超前 15 + 賣超前 15（負）
        conc = main_net / v
        mkt_pos += conc >= 0.10; mkt_neg += conc <= -0.10
        cl = b["close"].astype(float).to_numpy()
        pos = None
        if len(cl) >= 20:
            ma = cl[-20:].mean(); sd = cl[-20:].std(ddof=0)
            if sd > 0:
                pos = float((cl[-1] - (ma - 2 * sd)) / (4 * sd))
        top5 = []
        n_hab = n_named = 0; hist_ok = 0
        for r in g[g["net"] > 0].head(TOPN).itertuples():
            share = r.net / v
            if share < MINSH:
                continue
            n, f, _ = habit_for(EV, days, sid, r.branch_id, t_idx)
            hist = hist_by.get(sid, 0)
            hist_ok = max(hist_ok, hist)
            nm = by_id.get(r.branch_id)
            habit = f >= MINF
            n_hab += habit; n_named += bool(nm and nm.get("role") in ("隔日沖", "假主力"))
            top5.append({"br": r.branch_id, "name": r.branch, "net_lots": round(r.net / 1000), "share_pct": round(share * 100, 1),
                         "role": nm["role"] if nm else None, "says": (nm["says"][:50] if nm else None),
                         "habit": {"n": n, "flips": f, "is_habit": bool(habit), "hist_days": hist}})
        ins = inst.loc[sid] if sid in inst.index else None
        row = {"stock": sid, "name": names.get(sid, ""), "close": close, "chg_pct": round((close / prev - 1) * 100, 2),
               "main_net_lots": round(main_net / 1000), "conc_pct": round(conc * 100, 1), "boll_pos": None if pos is None else round(pos, 2),
               "foreign": None if ins is None else int(ins.foreign_net), "trust": None if ins is None else int(ins.investment_trust_net),
               "top5": top5, "hist_days": hist_ok, "n_habit": int(n_hab), "n_named_flip": int(n_named)}
        radar.append(row)
    # ---- 今日名單（代理版）----
    shorts = [r for r in radar if r["top5"] and (r["n_habit"] >= 2 or r["n_named_flip"] >= 2) and r["chg_pct"] > 0]
    shorts.sort(key=lambda r: (-(r["n_habit"] + r["n_named_flip"]), -sum(x["share_pct"] for x in r["top5"])))
    for r in shorts:
        reasons = []
        if r["n_habit"]:
            reasons.append(f"買超前 5 名中 {r['n_habit']} 個為本檔隔日沖慣犯（近 90 日大買後隔日賣出 ≥2 次）")
        if r["n_named_flip"]:
            reasons.append(f"{r['n_named_flip']} 個為小哥點名的隔日沖／假主力分點")
        if r["boll_pos"] is not None:
            reasons.append("布林位置 " + ("靠近上軌（小哥：僅極短空）" if r["boll_pos"] > 0.8 else "下軌附近（利於放空）" if r["boll_pos"] < 0.25 else "中段"))
        r["reasons"] = reasons
    longs = [r for r in radar if any(x["role"] in ("關鍵分點", "地緣", "高手") for x in r["top5"]) and r["conc_pct"] > 0
             and (r["boll_pos"] is None or r["boll_pos"] < 0.6)]
    longs.sort(key=lambda r: -r["conc_pct"])
    # ---- 故事重放（軌 B）：小哥具名分點 × 個股 ----
    stories = []
    pairs = []
    for e in cfg["branches"]:
        for tid in e["ids"]:
            for s in e["stocks"]:
                import re as _re
                for code in _re.findall(r"\((\d{4})\)", s):
                    pairs.append((e, tid, code))
    tmap = {(r.stock_id, r.branch_id): r for r in top.itertuples()}
    for e, tid, code in pairs:
        r = tmap.get((code, tid))
        if r is None:
            continue
        net = (r.buy_lots - r.sell_lots)
        if abs(net) < 200:
            continue
        stories.append({"branch": e["name"], "id": tid, "role": e["role"], "stock": code, "name": names.get(code, ""),
                        "net_lots": int(net), "buy": int(r.buy_lots), "sell": int(r.sell_lots), "says": (e["says"] or "")[:70]})
    stories.sort(key=lambda s: -abs(s["net_lots"]))
    # ---- 昨日覆盤 ----
    prev_day = days[t_idx - 1]
    review = []
    pj = OUT / f"notebook_{prev_day}.json"
    if pj.exists():
        pn = json.loads(pj.read_text(encoding="utf-8"))
        bmap = {(r.stock_id, r.trade_date): r for r in bars.itertuples()}
        for side, key in (("short", "shorts"), ("long", "longs")):
            for r in pn.get(key, [])[:10]:
                x0 = bmap.get((r["stock"], prev_day)); x1 = bmap.get((r["stock"], D))
                if x0 is not None and x1 is not None:
                    review.append({"side": side, "stock": r["stock"], "name": r["name"], "prev_close": float(x0.close),
                                   "open": float(x1.open), "close": float(x1.close),
                                   "oc_pct": round((x1.close / x1.open - 1) * 100, 2), "cc_pct": round((x1.close / x0.close - 1) * 100, 2)})
    # ---- 記分板 ----
    res = OUT.parent.parent / "research" / "xiaoge_flip_habit" / "results.json"
    score = json.loads(res.read_text(encoding="utf-8")) if res.exists() else {}
    cover = {"universe": meta.get("universe"), "stocks_with_rows": int(top["stock_id"].nunique()),
             "branch_rows": meta.get("branch_rows"), "branch_ids": meta.get("branch_ids"),
             "stocks_hist_ge_%d" % HIST_MIN_DAYS: int(sum(1 for r in radar if r["hist_days"] >= HIST_MIN_DAYS)),
             "history_median_days": float(np.median([r["hist_days"] for r in radar])) if radar else 0,
             "errors": (meta.get("errors_stock") or [])[:5]}
    nb = {"date": D, "generated_at": datetime.now().isoformat(timespec="seconds"),
          "disclaimer": "做空／做多名單為代理版（缺權證買超排名）；本頁是研究筆記，不是進場訊號；方法論複製檢定尚未全數通過。",
          "market": {"stocks_conc_ge10": int(mkt_pos), "stocks_conc_le_m10": int(mkt_neg), "n": len(radar)},
          "shorts": shorts[:15], "longs": longs[:15], "radar": sorted(radar, key=lambda r: -abs(r["conc_pct"]))[:60],
          "stories": stories[:60], "review": review, "scoreboard": {k: score.get(k) for k in ("H1", "H2", "H3", "H4", "H5")} if score else {},
          "coverage": cover}
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / f"notebook_{D}.json.tmp"
    tmp.write_text(json.dumps(nb, ensure_ascii=False, default=float), encoding="utf-8"); tmp.replace(OUT / f"notebook_{D}.json")
    (OUT / "notebook_latest.json").write_text(json.dumps(nb, ensure_ascii=False, default=float), encoding="utf-8")
    print(f"{D} 筆記本：雷達 {len(radar)} 檔、做空候選 {len(shorts)}、做多候選 {len(longs)}、故事 {len(stories)}、覆盤 {len(review)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
