#!/usr/bin/env python3
"""小哥權證分點收集（盤後、唯讀、不下單）：補上『買超認購權證金額＋誰買了認購權證』這塊（小哥選股主表「主力收購權證監控表」的資料端）。

對象（標的）：當日漲幅≥5% 且 PIT 流動性足（前20日均成交值≥3千萬）的個股 ∪ 前一日盤點命中的 M09／M07 追蹤股。
每個標的取『認購權證』當日成交金額前 N 檔（預設 8），逐檔抓富邦行情站權證頁（買超／賣超前約15名分點，單位張），
換算估計金額＝淨張數×1000×當日均價（均價＝成交金額÷成交股數，來自證交所 MI_INDEX 0999／櫃買 dailyQuotes EW）。
輸出：${DATA}/chip/xiaoge_warrant/<日>/warrant_branch.parquet（逐權證逐分點）、summary.json（逐標的：買超前5分點估計金額）。
權證清單快取：${DATA}/chip/xiaoge_warrant/_list/<日>.json。請求間隔 ≥1.2 秒；可續跑（已抓的權證不重抓）；時間預算用完即存檔結束。
用法：PYTHONPATH=src .venv/bin/python scripts/ops/xiaoge_warrant_tape.py [--date 2026-10-20] [--topn 8] [--max-minutes 20] [--dry-run]
退出碼：0 成功；3 富邦當日資料未就緒。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import ssl
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from fubon_branch_tape import FubonTape  # noqa: E402
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

OUT = DATA_DIR / "chip" / "xiaoge_warrant"
INV = DATA_DIR / "chip" / "xiaoge_inventory"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_CTX = ssl.create_default_context()
_CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT


def num(s) -> float:
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return 0.0


def get_json(url: str):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40, context=_CTX).read())


def warrant_list(day: str, names: dict[str, str]) -> list[dict]:
    f = OUT / "_list" / f"{day}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    rows = []
    p = get_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={day.replace('-', '')}&type=0999&response=json")
    for t in p.get("tables") or []:
        fl = [str(x).strip() for x in t.get("fields") or []]
        if "證券代號" in fl and "標的代號" in fl:
            ix = {n: i for i, n in enumerate(fl)}
            for it in t.get("data") or []:
                sh, amt = num(it[ix["成交股數"]]), num(it[ix["成交金額"]])
                rows.append({"code": it[ix["證券代號"]], "name": it[ix["證券名稱"]], "und": str(it[ix["標的代號"]]).strip(), "shares": sh, "amt": amt, "px": amt / sh if sh else 0.0, "mkt": "TWSE"})
    time.sleep(1.2)
    try:                                              # 櫃買權證（7 開頭）沒有標的欄，用名稱前綴對上市櫃個股名
        q = get_json(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={day.replace('-', '/')}&type=EW&response=json")
        by_len = sorted(names.items(), key=lambda kv: -len(kv[1]))
        for t in q.get("tables") or []:
            fl = [str(x).strip() for x in t.get("fields") or []]
            ci = next((i for i, n in enumerate(fl) if "代號" in n), None); ni = next((i for i, n in enumerate(fl) if "名稱" in n), None)
            si = next((i for i, n in enumerate(fl) if "成交股數" in n), None); ai = next((i for i, n in enumerate(fl) if "成交金額" in n), None)
            if None in (ci, ni, si, ai):
                continue
            for it in t.get("data") or []:
                code = str(it[ci]).strip()
                if len(code) == 6 and code[0] == "7":
                    nm = str(it[ni]); und = next((sid for sid, sn in by_len if sn and nm.startswith(sn)), "")
                    sh, amt = num(it[si]), num(it[ai])
                    rows.append({"code": code, "name": nm, "und": und, "shares": sh, "amt": amt, "px": amt / sh if sh else 0.0, "mkt": "TPEX"})
    except Exception as exc:  # noqa: BLE001
        print("櫃買權證清單失敗（略過）:", str(exc)[:80], flush=True)
    if rows:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def underlyings(c, day: str) -> list[str]:
    q = pd.read_sql_query("select stock_id, trade_date d, close, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date>date(?, '-45 day') and trade_date<=? "
                          "and length(stock_id)=4 and stock_id glob '[1-9][0-9][0-9][0-9]'", c, params=(day, day)).drop_duplicates(["stock_id", "d"]).sort_values(["stock_id", "d"])
    q["ret"] = q.groupby("stock_id").close.pct_change()
    q["tv20"] = (q.close * q.volume).groupby(q.stock_id).transform(lambda s: s.shift(1).rolling(20).mean())
    x = q[(q.d == day) & (q.ret >= 0.05) & (q.tv20 >= 3.0e7)].sort_values("ret", ascending=False)
    sids = list(x.stock_id)
    prev = sorted(p for p in INV.glob("inventory_20??-??-??.json") if p.stem.replace("inventory_", "") < day)
    if prev:
        inv = json.loads(prev[-1].read_text(encoding="utf-8"))
        for k in ("M09", "M07"):
            for h in inv["methods"].get(k, {}).get("hits", []):
                if h["stock"] not in sids:
                    sids.append(h["stock"])
    return sids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--topn", type=int, default=8); ap.add_argument("--max-minutes", type=float, default=20)
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--delay", type=float, default=1.2)
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True); c.execute("pragma query_only=1")
    day = a.date or c.execute("select max(trade_date) from stock_daily_bars where source in ('twse_mi_index','tpex_daily')").fetchone()[0]
    names = dict(c.execute("select stock_id,name from stock_beta").fetchall())
    sids = underlyings(c, day)
    wl = warrant_list(day, names)
    if not wl:
        print(f"{day} 取不到權證清單（非交易日或來源失敗）"); return 0
    by_und: dict[str, list[dict]] = {}
    for w in wl:
        if w["und"] in sids and "購" in w["name"] and w["amt"] > 0:
            by_und.setdefault(w["und"], []).append(w)
    plan = [(u, sorted(ws, key=lambda w: -w["amt"])[: a.topn]) for u, ws in by_und.items()]
    n_pages = sum(len(ws) for _, ws in plan)
    print(f"{day} 標的 {len(sids)} 檔（有認購權證成交 {len(plan)} 檔），預計 {n_pages} 頁（≈{n_pages * a.delay / 60:.0f} 分鐘）", flush=True)
    if a.dry_run:
        return 0
    out = OUT / day; out.mkdir(parents=True, exist_ok=True)
    pq = out / "warrant_branch.parquet"
    have = pd.read_parquet(pq) if pq.exists() else pd.DataFrame()
    done = set(have["warrant"]) if len(have) else set()
    tape = FubonTape({}, delay=a.delay)
    if not tape.stock_page("2330", day):
        print(f"{day} 富邦尚無資料", flush=True); return 3
    t0 = time.time(); rows = []; errs = 0; fetched = 0
    def save():
        df = pd.concat([have, pd.DataFrame(rows)], ignore_index=True) if rows else have
        if len(df):
            tmp = pq.with_suffix(".tmp"); df.to_parquet(tmp, index=False); tmp.replace(pq)
        return df
    stop = False
    for u, ws in sorted(plan, key=lambda p: sids.index(p[0])):
        for w in ws:
            if w["code"] in done:
                continue
            if a.max_minutes and time.time() - t0 > a.max_minutes * 60:
                stop = True; break
            try:
                pg = tape.stock_page(w["code"], day) or []
            except Exception as exc:  # noqa: BLE001
                errs += 1; continue
            fetched += 1
            for r in pg:
                rows.append({"und": u, "warrant": w["code"], "wname": w["name"], "branch_id": r["id"], "branch": r["name"], "buy_lots": r["buy"], "sell_lots": r["sell"], "px": w["px"], "wamt": w["amt"]})
            if fetched % 40 == 0:
                save(); print(f"  權證頁 {fetched}/{n_pages} {time.time() - t0:.0f}s 錯誤{errs}", flush=True)
        if stop:
            print(f"時間預算用完（{a.max_minutes} 分鐘），已抓 {fetched} 頁", flush=True); break
    df = save()
    summ = {}
    if len(df):
        df["net"] = df.buy_lots - df.sell_lots
        df["amt_est"] = df.net * 1000 * df.px
        for u, g in df.groupby("und"):
            b = g.groupby(["branch_id", "branch"]).agg(net=("net", "sum"), amt=("amt_est", "sum")).reset_index().sort_values("amt", ascending=False)
            summ[u] = {"n_warrants": int(g.warrant.nunique()), "top_buyers": [{"id": r.branch_id, "name": r.branch, "net_lots": int(r.net), "amt_est": float(r.amt)} for r in b.head(5).itertuples() if r.net > 0],
                       "top_sellers": [{"id": r.branch_id, "name": r.branch, "net_lots": int(r.net), "amt_est": float(r.amt)} for r in b.tail(3).itertuples() if r.net < 0],
                       "buy_amt_top5": float(b.head(5).amt.clip(lower=0).sum())}
    (out / "summary.json").write_text(json.dumps({"date": day, "fetched": fetched, "errors": errs, "underlyings": summ}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"mode": "warrant_tape", "day": day, "underlyings": len(summ), "pages": fetched, "errors": errs, "seconds": round(time.time() - t0)}, ensure_ascii=False), flush=True)
    return 2 if errs > 0.2 * max(1, fetched + errs) else 0


if __name__ == "__main__":
    sys.exit(main())
