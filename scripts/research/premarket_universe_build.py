#!/usr/bin/env python3
"""盤前試撮收集名單產生器：儀表板 42 檔（tier A）＋ 高成交值、高波動的科技股（tier B）。

選股（近 20 個交易日、唯讀 DB）：
  - 產業屬科技類（半導體／電子零組件／光電／電腦週邊／通信網路／其他電子／資訊服務…）
  - 4 位數普通股（排除 ETF／權證／DR）
  - 20 日均成交值 >= --min-turnover（預設 3 億）
  - 分數 = 成交值排名百分位 + 波動（20 日平均 (high-low)/close）排名百分位
輸出 ${GOLDENSTOCKS_DATA_DIR}/cache/premarket_universe.json
  {"asof","tier_a":[...],"tier_b":[...],"core":["0050","2317","2330","2454"]}

用法：PYTHONPATH=src .venv/bin/python scripts/research/premarket_universe_build.py [--n 300]
"""
from __future__ import annotations
import argparse, json, os, sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(os.environ.get("GOLDENSTOCKS_DATA_DIR") or Path.home() / "goldenstocks-data")
DB = ROOT / "data" / "stocks.db"
CALIB = ROOT / "data" / "cache" / "pit_universe_tick" / "_live_calib.json"
IND = ROOT / "data" / "cache" / "stock_industry.parquet"
OUT = ROOT / "data" / "cache" / "premarket_universe.json"
CORE = ["0050", "2317", "2330", "2454"]
TECH = {"半導體業", "電子零組件業", "光電業", "電腦及週邊設備業", "通信網路業",
        "其他電子業", "電子工業", "其他電子類", "資訊服務業"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300, help="總檔數上限（含 tier A）")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--min-turnover", type=float, default=3e8)
    a = ap.parse_args()

    tier_a = [str(u["sid"] if isinstance(u, dict) else u) for u in json.load(open(CALIB))["universe"]]
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    dates = [r[0] for r in con.execute(
        "select distinct trade_date from stock_daily_bars order by trade_date desc limit ?", (a.days,))]
    q = ("select stock_id, trade_date, max(high), min(low), max(close), max(volume) "
         "from stock_daily_bars where trade_date>=? group by 1,2")
    df = pd.DataFrame(con.execute(q, (min(dates),)).fetchall(),
                      columns=["sid", "d", "hi", "lo", "cl", "vol"])
    df = df[df.sid.str.fullmatch(r"\d{4}") & (df.cl > 0)]
    df["turn"] = df.cl * df.vol
    df["rng"] = (df.hi - df.lo) / df.cl
    g = df.groupby("sid").agg(turn=("turn", "mean"), rng=("rng", "mean"), n=("d", "count"))
    g = g[(g.n >= a.days * 0.8) & (g.turn >= a.min_turnover)]
    ind = pd.read_parquet(IND).drop_duplicates("stock_id").set_index("stock_id")["ind"]
    g = g[g.index.map(lambda s: ind.get(s) in TECH)]
    g["score"] = g.turn.rank(pct=True) + g.rng.rank(pct=True)
    fixed = set(tier_a) | set(CORE)
    pool = g[~g.index.isin(fixed)].sort_values("score", ascending=False)
    tier_b = list(pool.index[: max(0, a.n - len(fixed))])
    out = {"asof": datetime.now().strftime("%F"), "core": CORE, "tier_a": sorted(fixed),
           "tier_b": tier_b}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False))
    print(f"tier_a={len(out['tier_a'])} tier_b={len(tier_b)} 科技候選={len(g)} → {OUT}")
    print("tier_b 前 10：", [(s, round(g.loc[s, 'turn'] / 1e8, 1), round(g.loc[s, 'rng'] * 100, 1)) for s in tier_b[:10]])
    print("tier_b 末 3：", [(s, round(g.loc[s, 'turn'] / 1e8, 1), round(g.loc[s, 'rng'] * 100, 1)) for s in tier_b[-3:]])


if __name__ == "__main__":
    main()
