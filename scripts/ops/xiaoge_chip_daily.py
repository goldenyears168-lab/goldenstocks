#!/usr/bin/env python3
"""小哥籌碼每日抓取（Phase 1）· 盤後 · observe only（不碰下單層、不寫正本 DB）。

抓三樣東西到 `${GOLDENSTOCKS_DATA_DIR}/chip/xiaoge/<日期>/`：
  1. stock_top15.parquet  流動性前 N 檔（PIT 20 日均成交值 ≥ 5,000 萬）的個股頁：買超前 15、賣超前 15 分點（張）。
  2. branch_pages.parquet 全部活躍分點（config/xiaoge_all_branches.json，約 1,000 個代號）的分點頁，依張數（kind=E）；
                          小哥具名分點（config/xiaoge_named_branches.json，125 個代號）另抓依金額（kind=B，仟元）。
                          每頁為該分點當日買超／賣超各前約 50 名股票。
  3. meta.json            範圍、筆數、耗時、錯誤；失敗不寫半成品（先寫 .tmp 再 rename）。
來源＝富邦行情站（src/fubon_branch_tape.py；非正式 API，請求間隔 1.2 秒）。可續跑：已抓過的股票／分點不重抓。
  4. --burst 模式：當日「漲停／爆量」股票（不論是否在流動性前 N）補抓個股頁，並回補其近 30 個交易日缺的個股頁，
     讓「剛爆量的股票」也有分點歷史（台郡 6269 事件：10/16 成交量小未入前 579 檔 → 10/20 才大漲時沒有 10/16 的富邦證券買進紀錄）。
用法：PYTHONPATH=src .venv/bin/python scripts/ops/xiaoge_chip_daily.py [--date 2026-10-20] [--top 579] [--no-branches] [--limit 20]
      PYTHONPATH=src .venv/bin/python scripts/ops/xiaoge_chip_daily.py --burst [--burst-days 30] [--max-minutes 25] [--dry-run]
退出碼：0 成功；2 錯誤比例過高；3 富邦當日資料尚未就緒。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from fubon_branch_tape import FubonTape  # noqa: E402
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

CFG = ROOT / "config/xiaoge_named_branches.json"
CFG_ALL = ROOT / "config/xiaoge_all_branches.json"
MIN_TURNOVER = 5e7


def latest_bar_date(c) -> str:
    return c.execute("select max(trade_date) from stock_daily_bars where source in ('twse_mi_index','tpex_daily')").fetchone()[0]


def liquid_universe(c, day: str, top: int) -> list[tuple[str, float]]:
    rows = c.execute(
        "select stock_id, close, volume, trade_date from stock_daily_bars "
        "where source in ('twse_mi_index','tpex_daily') and trade_date<=? and trade_date>date(?, '-40 day') "
        "and stock_id glob '[1-9][0-9][0-9][0-9]'", (day, day)).fetchall()
    df = pd.DataFrame(rows, columns=["sid", "close", "vol", "d"]).drop_duplicates(["sid", "d"])
    df["tv"] = df["close"] * df["vol"]
    last20 = df.sort_values("d").groupby("sid").tail(20)
    avg = last20.groupby("sid").agg(tv=("tv", "mean"), n=("tv", "size"), last=("d", "max"))
    avg = avg[(avg.n >= 15) & (avg["last"] == day) & (avg.tv >= MIN_TURNOVER)].sort_values("tv", ascending=False)
    return [(s, float(v)) for s, v in avg["tv"].head(top).items()]


def have_stocks(c, day: str) -> set[str]:
    """該日已有「前 15 名以上」資料的股票：DB 該股該日 ≥25 列，或已有 parquet。"""
    rows = c.execute("select stock_id from stock_broker_branch_daily where trade_date=? group by stock_id having count(*)>=25", (day,)).fetchall()
    have = {r[0] for r in rows}
    pq = DATA_DIR / "chip" / "xiaoge" / day / "stock_top15.parquet"
    if pq.exists():
        have |= set(pd.read_parquet(pq, columns=["stock_id"])["stock_id"])
    return have


def pick_backfill_day(c, latest: str, top: int):
    days = [r[0] for r in c.execute(
        "select distinct trade_date from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date<=? "
        "order by trade_date desc limit 64", (latest,)).fetchall()]
    for d in days[1:]:                                   # 最新日由主流程負責
        uni = liquid_universe(c, d, top)
        if not uni:
            continue
        have = have_stocks(c, d)
        miss = [(s, v) for s, v in uni if s not in have]
        if len(miss) > 0.05 * len(uni):
            return d, miss
    return None, []


BURST_RET, BURST_VR, BURST_MIN_TV = 0.09, 3.0, 2.0e7


def burst_stocks(c, day: str) -> list[tuple[str, str]]:
    """當日漲停（≥9%）或爆量（量≥前20日均量3倍）且成交值≥2千萬的 4 碼股。回傳 [(代號, 原因)]。"""
    rows = c.execute(
        "select stock_id, trade_date, close, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') "
        "and trade_date<=? and trade_date>date(?, '-45 day') and stock_id glob '[1-9][0-9][0-9][0-9]'", (day, day)).fetchall()
    df = pd.DataFrame(rows, columns=["sid", "d", "close", "vol"]).drop_duplicates(["sid", "d"]).sort_values(["sid", "d"])
    out = []
    for sid, g in df.groupby("sid"):
        if len(g) < 21 or g["d"].iloc[-1] != day:
            continue
        cl, v = g["close"].to_numpy(), g["vol"].to_numpy().astype(float)
        ret = cl[-1] / cl[-2] - 1
        vr = v[-1] / max(v[-21:-1].mean(), 1.0)
        tv = cl[-1] * v[-1]
        if tv < BURST_MIN_TV:
            continue
        why = [w for w, ok in (("漲停", ret >= BURST_RET), ("爆量", vr >= BURST_VR)) if ok]
        if why:
            out.append((sid, "+".join(why) + f"(漲{ret*100:.1f}%,量比{vr:.1f})"))
    return out


def run_burst(a, c, day: str) -> int:
    bl = burst_stocks(c, day)
    # 追蹤名單：前一交易日盤點命中的 M09／M07 隔日沖慣犯股，不論今日是否爆量，都要有「隔天」的分點頁（驗證隔日沖實際賣了多少，xiaoge_m09_followup.py）
    inv_dir = DATA_DIR / "chip" / "xiaoge_inventory"
    prev = sorted(p for p in inv_dir.glob("inventory_20??-??-??.json") if p.stem.replace("inventory_", "") < day)
    if prev:
        inv = json.loads(prev[-1].read_text(encoding="utf-8"))
        have_ids = {s for s, _ in bl}
        for k in ("M09", "M07"):
            for h in inv["methods"].get(k, {}).get("hits", []):
                if h["stock"] not in have_ids:
                    bl.append((h["stock"], f"追蹤({k},{inv['date']})")); have_ids.add(h["stock"])
    days = [r[0] for r in c.execute(
        "select distinct trade_date from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date<=? "
        "order by trade_date desc limit ?", (day, a.burst_days)).fetchall()]
    need = []
    for d in sorted(days, reverse=True):                  # 最近的日子優先
        have = have_stocks(c, d)
        need += [(sid, d) for sid, _ in bl if sid not in have]
    print(f"{day} 漲停/爆量 {len(bl)} 檔；近 {len(days)} 日缺 {len(need)} 個(股,日)頁", flush=True)
    if a.dry_run:
        for sid, why in bl[:60]:
            print(" ", sid, why)
        return 0
    c.close()
    tape = FubonTape({}, delay=a.delay)
    if not tape.stock_page("2330", day):
        print(f"{day} 富邦尚無資料", flush=True)
        return 3
    t0 = time.time(); errs = 0; fetched = 0; buf: dict[str, list] = {}

    def flush():
        for d, rows in list(buf.items()):
            if not rows:
                continue
            out = DATA_DIR / "chip" / "xiaoge" / d
            out.mkdir(parents=True, exist_ok=True)
            ck = out / "stock_top15.parquet"
            have = pd.read_parquet(ck) if ck.exists() else pd.DataFrame()
            _save(ck, have, rows)
            buf[d] = []
    for i, (sid, d) in enumerate(need, 1):
        if a.max_minutes and (time.time() - t0) > a.max_minutes * 60:
            print(f"時間預算用完（{a.max_minutes} 分鐘），已抓 {fetched}/{len(need)}", flush=True)
            break
        try:
            pg = tape.stock_page(sid, d)
        except Exception as e:  # noqa: BLE001
            errs += 1; print("  err", sid, d, str(e)[:60], flush=True); continue
        buf.setdefault(d, []).extend({"stock_id": sid, "trade_date": d, "branch_id": r["id"], "branch": r["name"],
                                      "buy_lots": r["buy"], "sell_lots": r["sell"], "turnover20": float("nan")} for r in pg or [])
        fetched += 1
        if i % 40 == 0:
            flush(); print(f"  burst {i}/{len(need)} {time.time()-t0:.0f}s 錯誤{errs}", flush=True)
    flush()
    print(json.dumps({"mode": "burst", "day": day, "burst_stocks": len(bl), "fetched": fetched, "errors": errs, "seconds": round(time.time() - t0)}, ensure_ascii=False), flush=True)
    return 2 if errs > 0.2 * max(1, fetched + errs) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--top", type=int, default=579)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-branches", action="store_true")
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--backfill", action="store_true", help="回補近 63 個交易日中資料最缺的一天（只抓個股頁，跳過 DB 已有完整 tape 的股票）")
    ap.add_argument("--max-minutes", type=float, default=0, help="個股頁抓取的時間預算（分鐘），0＝不限")
    ap.add_argument("--burst", action="store_true", help="漲停/爆量股補抓個股頁並回補近 N 日缺的頁")
    ap.add_argument("--burst-days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    c.execute("pragma query_only=1")
    day = a.date or latest_bar_date(c)
    if a.burst:
        return run_burst(a, c, day)
    if a.backfill:
        day, uni = pick_backfill_day(c, day, a.top)
        if day is None:
            print("近 63 個交易日個股頁已齊，無需回補", flush=True)
            return 0
        a.no_branches = True
    else:
        uni = liquid_universe(c, day, a.top)
    if a.limit:
        uni = uni[: a.limit]
    c.close()
    out = DATA_DIR / "chip" / "xiaoge" / day
    out.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    named_ids = set(cfg["all_ids"])
    all_ids = sorted(set(json.loads(CFG_ALL.read_text(encoding="utf-8"))["branches"]) | named_ids)
    ids = all_ids
    tape = FubonTape({}, delay=a.delay)
    t0 = time.time()
    if not tape.stock_page("2330", day):
        print(f"{day} 富邦尚無資料（2330 個股頁為空）", flush=True)
        return 3
    ck = out / "stock_top15.parquet"
    have = pd.read_parquet(ck) if ck.exists() else pd.DataFrame()
    done = set(have["stock_id"]) if len(have) else set()
    rows, errs = [], []
    for i, (sid, tv) in enumerate(uni, 1):
        if sid in done:
            continue
        if a.max_minutes and (time.time() - t0) > a.max_minutes * 60:
            print(f"時間預算用完（{a.max_minutes} 分鐘），已抓 {i-1}/{len(uni)}", flush=True)
            break
        try:
            pg = tape.stock_page(sid, day)
        except Exception as e:  # noqa: BLE001
            errs.append((sid, str(e)[:80])); continue
        for r in pg or []:
            rows.append({"stock_id": sid, "trade_date": day, "branch_id": r["id"], "branch": r["name"],
                         "buy_lots": r["buy"], "sell_lots": r["sell"], "turnover20": tv})
        if i % 50 == 0:
            print(f"個股頁 {i}/{len(uni)} {time.time()-t0:.0f}s 錯誤{len(errs)}", flush=True)
            _save(out / "stock_top15.parquet", have, rows)
    stock_df = _save(out / "stock_top15.parquet", have, rows)
    brows, berrs = [], []
    if not a.no_branches:
        for j, b in enumerate(ids, 1):
            for kind in (("E", "B") if b in named_ids else ("E",)):
                try:
                    pg = tape.branch_page(b, day, kind)
                except Exception as e:  # noqa: BLE001
                    berrs.append((b, kind, str(e)[:80])); continue
                for sid, (bu, se) in (pg or {}).items():
                    brows.append({"branch_id": b, "kind": kind, "stock_id": sid, "trade_date": day, "buy": bu, "sell": se})
            if j % 25 == 0:
                print(f"分點頁 {j}/{len(ids)} {time.time()-t0:.0f}s 錯誤{len(berrs)}", flush=True)
    bdf = pd.DataFrame(brows)
    if len(bdf):
        _write(out / "branch_pages.parquet", bdf)
    meta = {"trade_date": day, "universe": len(uni), "stocks_with_rows": int(stock_df["stock_id"].nunique()) if len(stock_df) else 0,
            "stock_rows": int(len(stock_df)), "branch_ids": len(ids), "named_branch_ids": len(named_ids), "branch_rows": int(len(bdf)),
            "errors_stock": errs[:20], "errors_branch": berrs[:20], "seconds": round(time.time() - t0),
            "generated": date.today().isoformat(), "no_branches": a.no_branches}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False), flush=True)
    bad = (len(errs) + len(berrs)) / max(1, len(uni) + (0 if a.no_branches else len(ids) + len(named_ids)))
    return 2 if bad > 0.2 else 0


def _write(path: Path, df: pd.DataFrame) -> None:
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _save(path: Path, have: pd.DataFrame, rows: list[dict]) -> pd.DataFrame:
    df = pd.concat([have, pd.DataFrame(rows)], ignore_index=True) if rows else have
    if len(df):
        _write(path, df)
    return df


if __name__ == "__main__":
    sys.exit(main())
