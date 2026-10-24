#!/usr/bin/env python3
"""自營商『避險／自行買賣』拆分（官方 TWSE T86 + 櫃買 insti/dailyTrade）→ ${DATA}/chip/dealer_split/<日>.parquet。唯讀公開資料，不寫正本 DB。

動機：小哥的『自營比』來自權證避險買盤；XQ 內建欄位『自營商避險買賣超』與『自營商自行買賣買賣超』分開。我們 DB 的 stock_institutional_daily 只存自營商『合計』。
欄位（單位：股）：dealer_self_net、dealer_hedge_net、dealer_total_net、dealer_hedge_buy、dealer_hedge_sell。可續跑（已存的日子略過），請求間隔 ≥2.5 秒。
用法：PYTHONPATH=src .venv/bin/python scripts/ops/xiaoge_dealer_split.py [--start 2024-09-02] [--end 2026-10-23] [--delay 2.5]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import ssl
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

OUT = DATA_DIR / "chip" / "dealer_split"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "Referer": "https://www.twse.com.tw/"}
_CTX = ssl.create_default_context(); _CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT


def num(s) -> float:
    t = str(s).replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return 0.0


def get(url: str, tries: int = 4):
    for k in range(tries):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60, context=_CTX).read()
            return json.loads(raw) if raw.strip() else None
        except Exception as exc:  # noqa: BLE001
            if k == tries - 1:
                raise
            print("    retry", k + 1, str(exc)[:60], flush=True); time.sleep(6 * (k + 1))


def twse(day: str) -> list[dict]:
    p = get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={day.replace('-', '')}&selectType=ALL&response=json")
    if not p or p.get("stat") != "OK":
        return []
    ix = {n.strip(): i for i, n in enumerate(p["fields"])}
    need = ("證券代號", "自營商買賣超股數", "自營商買賣超股數(自行買賣)", "自營商買賣超股數(避險)", "自營商買進股數(避險)", "自營商賣出股數(避險)")
    if any(k not in ix for k in need):
        return []
    return [{"stock_id": str(r[ix["證券代號"]]).strip(), "dealer_total_net": num(r[ix["自營商買賣超股數"]]), "dealer_self_net": num(r[ix["自營商買賣超股數(自行買賣)"]]),
             "dealer_hedge_net": num(r[ix["自營商買賣超股數(避險)"]]), "dealer_hedge_buy": num(r[ix["自營商買進股數(避險)"]]), "dealer_hedge_sell": num(r[ix["自營商賣出股數(避險)"]]), "mkt": "TWSE"}
            for r in p.get("data") or []]


def tpex(day: str) -> list[dict]:
    p = get(f"https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?date={day.replace('-', '/')}&type=Daily&response=json")
    out = []
    for t in (p or {}).get("tables") or []:
        for r in t.get("data") or []:
            if len(r) < 24:
                continue
            # 欄位：代號,名稱, [外資不含自營 3][外資自營 3][外資合計 3][投信 3][自營自行 3][自營避險 3][自營合計 3], 三大合計
            g = lambda k: (num(r[2 + 3 * k]), num(r[3 + 3 * k]), num(r[4 + 3 * k]))
            self_, hedge, tot = g(4), g(5), g(6)
            out.append({"stock_id": str(r[0]).strip(), "dealer_total_net": tot[2], "dealer_self_net": self_[2], "dealer_hedge_net": hedge[2], "dealer_hedge_buy": hedge[0], "dealer_hedge_sell": hedge[1], "mkt": "TPEX"})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-09-02"); ap.add_argument("--end"); ap.add_argument("--delay", type=float, default=2.5)
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    end = a.end or c.execute("select max(trade_date) from stock_daily_bars where source in ('twse_mi_index','tpex_daily')").fetchone()[0]
    days = [r[0] for r in c.execute("select trade_date from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date between ? and ? and length(stock_id)=4 group by trade_date having count(*)>=600 order by 1", (a.start, end))]
    OUT.mkdir(parents=True, exist_ok=True)
    todo = [d for d in days if not (OUT / f"{d}.parquet").exists()]
    print(f"交易日 {len(days)}，待抓 {len(todo)}（≈{len(todo) * 2 * a.delay / 60:.0f} 分鐘）", flush=True)
    t0 = time.time()
    for i, d in enumerate(todo, 1):
        rows = []
        try:
            rows += twse(d); time.sleep(a.delay)
            rows += tpex(d); time.sleep(a.delay)
        except Exception as exc:  # noqa: BLE001
            print(f"  {d} 失敗：{str(exc)[:80]}（略過，下次續跑）", flush=True); continue
        if rows:
            df = pd.DataFrame(rows); df = df[df.stock_id.str.fullmatch(r"\d{4}")]
            tmp = OUT / f"{d}.tmp"; df.to_parquet(tmp, index=False); tmp.replace(OUT / f"{d}.parquet")
        if i % 20 == 0:
            print(f"  {i}/{len(todo)} {time.time() - t0:.0f}s", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
