#!/usr/bin/env python3
"""高波動個股期貨宇宙 v3 —— 可重現的產生器。

取代 `_live_calib.json`（那份沒有產生器、無法重現，且 `vol20` 欄位混了兩種量：
下段 33 檔是 20 日日報酬 sd、上段 12 檔對不上任何波動定義，導致排序前段是錯的）。

篩選（全部可調，寫進輸出的 meta）：
  1. 有個股期貨，且近 N_LIQ 個交易日「日盤最大量合約」的中位成交口數 >= MIN_FUT_VOL
  2. 非金融
  3. 波動 = 近 N_VOL 個交易日的日報酬標準差（%），**先濾掉 |ret|>SPLIT_GUARD 的未還原分割日**
     （6669 緯穎 2026-09-02 三合一未還原，不濾會被灌成全市場第一）
  4. 依波動由大到小取前 TOP_N

期貨↔現貨對映：期交所官方對照表 config/taifex_stock_futures_map.json
（`scripts/research/fetch_taifex_stock_futures_map.py` 產生）。全市場有 50 檔標的同時掛
兩個契約（例：台光電 2383 = PJF 舊 + SFF 新），一律取流動性視窗內成交量大的那個——
舊契約常常只剩 100 口/日，用到它會讓 08:45 開盤價變成雜訊。

用法：
  PYTHONPATH=src .venv/bin/python scripts/research/build_hivol_futures_universe.py
  （--top 45 --min-fut-vol 500 --out <path> 可覆寫）
"""
from __future__ import annotations
import argparse, glob, json, os, re, sqlite3, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "src")
import stock_db
from stock_db import DATA_DIR
from finmind_client import fetch_finmind_json

SCHEMA_VERSION = 3
TPE = timezone(timedelta(hours=8))
FUT_DIR = Path("/Users/jackm4/goldenstocks-data/cache/stock_futures_daily")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=45)
    p.add_argument("--min-fut-vol", type=float, default=500)
    p.add_argument("--n-vol", type=int, default=20)
    p.add_argument("--n-liq", type=int, default=16)
    p.add_argument("--split-guard", type=float, default=0.20)
    p.add_argument("--map-tol", type=float, default=0.02)
    p.add_argument("--asof", default=None, help="YYYY-MM-DD，預設=DB 最新交易日")
    p.add_argument("--out", default=str(DATA_DIR / "cache/pit_universe_tick/_hivol_universe_v3.json"))
    return p.parse_args()


def main() -> int:
    a = parse_args()
    conn = sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
    asof = a.asof or pd.read_sql("SELECT MAX(trade_date) d FROM stock_daily_bars", conn).d.iloc[0]
    days = sorted(pd.read_sql(
        "SELECT DISTINCT trade_date FROM stock_daily_bars WHERE trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", conn, params=(asof, a.n_liq)).trade_date)
    print(f"as-of {asof}；流動性視窗 {days[0]}~{days[-1]}（{len(days)} 日）", flush=True)

    # ---- 1) 全市場期貨日線（逐日抓；FinMind 不帶 data_id 時只回 start_date 當天）
    frames = []
    for d in days:
        j = fetch_finmind_json({"dataset": "TaiwanFuturesDaily", "start_date": d})
        x = pd.DataFrame(j.get("data") or [])
        if len(x):
            frames.append(x)
        time.sleep(0.35)
    f = pd.concat(frames)
    f = f[(f.trading_session == "position") & (f.volume > 0)]
    f = f.sort_values(["futures_id", "date", "volume"]).groupby(
        ["futures_id", "date"], as_index=False).last()
    agg = f.groupby("futures_id").agg(fut_vol=("volume", "median"), n=("date", "count"))
    cand = agg[(agg.n >= len(days) * 0.6) & (agg.fut_vol >= a.min_fut_vol)]
    print(f"期貨商品 {len(agg)} → 中位量>={a.min_fut_vol:.0f} 口的 {len(cand)}", flush=True)

    # ---- 2) 對映 futures_id -> stock_id（期交所官方對照表；同一標的有兩個契約時取近期量大的）
    mp = pd.DataFrame(json.loads(
        Path("config/taifex_stock_futures_map.json").read_text())["rows"]).astype(str)
    mp = mp[(mp.is_future == "True") & mp.stock_id.str.fullmatch(r"\d{4}")]
    mp = mp[mp.futures_id.isin(cand.index)]
    mp["fut_vol"] = mp.futures_id.map(cand.fut_vol)
    n_multi = int((mp.groupby("stock_id").size() > 1).sum())
    m = mp.sort_values(["stock_id", "fut_vol"]).groupby("stock_id", as_index=False).last()
    m["map_by"] = "taifex"; m["map_dev"] = np.nan
    m = m[["futures_id", "stock_id", "fut_vol", "map_dev", "map_by"]].rename(
        columns={"stock_id": "sid"})
    print(f"官方對照表命中 {len(m)} 檔標的（其中 {n_multi} 檔有兩個契約，取量大的）", flush=True)

    # ---- 3) 波動（濾未還原分割）+ 產業
    info = pd.DataFrame(fetch_finmind_json({"dataset": "TaiwanStockInfo"}).get("data") or [])
    info = info.drop_duplicates(subset=["stock_id"])
    NM = dict(zip(info.stock_id.astype(str), info.stock_name))
    IC = dict(zip(info.stock_id.astype(str), info.industry_category))
    out = []
    for _, r in m.iterrows():
        sid = r.sid
        ic = str(IC.get(sid, ""))
        if any(k in ic for k in ("金融", "保險", "銀行")):
            continue
        d = pd.read_sql("SELECT trade_date,high,low,close FROM stock_daily_bars "
                        "WHERE stock_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 60",
                        conn, params=(sid, asof))
        d = d.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
        d["ret"] = d.close.pct_change()
        n_split = int((d.ret.abs() > a.split_guard).sum())
        d = d[d.ret.abs() <= a.split_guard].tail(a.n_vol)
        if len(d) < a.n_vol * 0.75:
            continue
        out.append(dict(sid=sid, name=NM.get(sid, ""), cat=ic, futures_id=r.futures_id,
                        px=float(d.close.iloc[-1]),
                        vol20_sd=float((d.ret * 100).std()),
                        amp20=float(((d.high - d.low) / d.close * 100).mean()),
                        fut_vol_med=float(r.fut_vol),
                        map_by=r.map_by, map_dev=None if pd.isna(r.map_dev) else float(r.map_dev),
                        n_split_filtered=n_split))
    o = pd.DataFrame(out).sort_values("vol20_sd", ascending=False).reset_index(drop=True)
    sel = o.head(a.top).copy()
    sel.insert(0, "rank", range(1, len(sel) + 1))
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(TPE).isoformat(timespec="seconds"),
        "asof_trade_date": asof,
        "params": {k: getattr(a, k) for k in
                   ("top", "min_fut_vol", "n_vol", "n_liq", "split_guard", "map_tol")},
        "field_defs": {
            "vol20_sd": f"近 {a.n_vol} 個交易日的日報酬標準差(%)，已剔除 |ret|>{a.split_guard} 的未還原分割日",
            "amp20": f"近 {a.n_vol} 日 (high-low)/close 平均(%)",
            "fut_vol_med": f"近 {a.n_liq} 日『日盤最大量合約』成交口數中位數",
            "map_by": "taifex=期交所官方標的證券對照表 config/taifex_stock_futures_map.csv",
            "futures_id": "同一標的有兩個契約時（全市場 50 檔），取流動性視窗內量大的那個",
        },
        "candidate_pool_n": int(len(o)),
        "universe": sel.to_dict("records"),
    }
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1))

    # 另外寫一份「舊 schema 相容」檔，讓 biglot_live_watch / verify_45_capture 等
    # 既有消費者不用改程式就能吃 v3 名單（欄位：sid/name/cat/market/px/tick_bps/
    # fut_vol/vol20/p99/p90/fut_code）。p99/p90 舊檔本來就全是 0，這裡沿用。
    def _tick(px: float) -> float:            # 台股檔位
        for hi, t in ((10, .01), (50, .05), (100, .1), (500, .5), (1000, 1.0)):
            if px < hi:
                return t
        return 5.0
    mkt = {}
    try:
        _mp = pd.DataFrame(json.loads(
            Path("config/taifex_stock_futures_map.json").read_text())["rows"]).astype(str)
        mkt = dict(zip(_mp.stock_id, _mp.market))
    except Exception:
        pass
    legacy = [{
        "sid": r["sid"], "name": r["name"], "cat": r["cat"],
        "market": "OTC" if mkt.get(r["sid"]) == "上櫃" else "TSE",
        "px": r["px"], "tick_bps": round(_tick(r["px"]) / r["px"] * 1e4, 1),
        "fut_vol": r["fut_vol_med"], "fut_code": r["futures_id"],
        "vol20": r["vol20_sd"], "amp20": r["amp20"], "p99": 0, "p90": 0,
    } for r in doc["universe"]]
    legacy_path = Path(a.out).with_name("_live_calib_v3.json")
    legacy_path.write_text(json.dumps(
        {"universe": legacy, "disp_pct": {}, "disp_n": 0,
         "note": f"v3 相容檔 · generated_at={doc['generated_at']} · asof={asof} · "
                 f"產生器 scripts/research/build_hivol_futures_universe.py"},
        ensure_ascii=False, indent=1))
    print(f"相容檔寫出 {legacy_path}")
    print(f"\n寫出 {a.out}：母體 {len(o)} → 取前 {len(sel)}")
    print(sel[["rank", "sid", "name", "futures_id", "vol20_sd", "amp20", "fut_vol_med",
               "map_by"]].to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
