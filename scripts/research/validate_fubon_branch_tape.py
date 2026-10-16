#!/usr/bin/env python3
"""並行驗證:富邦行情站(fubon-ebrokerdj)『依分點看個股』頁 vs 資料庫既有 FinMind 分點 tape(只讀,不寫 DB)。

追蹤範圍＝專家池 POOLS 的 (核心分點, 股票) 配對(branch-tape-prewarm 實際在乎的部位)。
對每個核心分點、每個交易日抓一次頁面(買超前 50 + 賣超前 50,單位:張),檢查:
  recall    DB 有該 (分點,股票,日) 列時,頁面有沒有出現
  agreement 兩邊都有時,買進/賣出張數是否一致(DB 單位為股,頁面為張,容許 ±1 張)
  trunc     該分點該日頁面是否滿 100 列(Top-50 限制是否在起作用)
  spurious  DB 沒有該列、頁面卻有

用法  .venv/bin/python scripts/research/validate_fubon_branch_tape.py --days 20
快取  <狀態根>/data/research/fubon_branch_validate/page_<branch>_<date>.json(重跑不重抓)
請求間隔預設 1.2 秒。
"""
import argparse
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts" / "research"), str(ROOT / "scripts" / "order")]
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

URL = "https://fubon-ebrokerdj.fbs.com.tw/z/zg/zgb/zgb0.djhtm?a={b}&b={hexb}&c={k}&e={d}&f={d}"
ROW = re.compile(r"GenLink2stk\('AS(\w+)','([^']*)'\);\s*//-->\s*</SCRIPT>\s*</td>\s*<td[^>]*>([\d,]+)</td>\s*<td[^>]*>([\d,]+)</td>\s*<td[^>]*>(-?[\d,]+)</td>", re.S)


def hex_id(b):                       # 分點代號以 UTF-16BE 十六進位傳入,保留大小寫(585b≠585B)
    return "".join("%04X" % ord(c) for c in b)


def fetch(b, d, cache, sleep, kind="E"):
    f = cache / (f"page_{hex_id(b)}_{d}.json" if kind == "E" else f"pageB_{hex_id(b)}_{d}.json")      # E=依張數排名、B=依金額(仟元)排名
    if f.exists():
        return json.loads(f.read_text())
    req = urllib.request.Request(URL.format(b=b, hexb=hex_id(b), d=d, k=kind), headers={"User-Agent": "Mozilla/5.0 (research; goldenstocks)"})
    last = None
    for attempt in range(3):
        try:
            raw = urllib.request.urlopen(req, timeout=40).read().decode("big5", errors="ignore")
            break
        except Exception as e:          # 網路暫時失敗:退避重試
            last = e
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"{b} {d}: {last}")
    rows = {sid: [int(x.replace(",", "")) for x in (bu, se)] for sid, _, bu, se, _ in ROW.findall(raw)}
    out = {"n": raw.count("GenLink2stk("), "rows": rows, "nodata": "查無" in raw}
    f.write_text(json.dumps(out))
    time.sleep(sleep)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--sleep", type=float, default=1.2)
    ap.add_argument("--kind", choices=["E", "B"], default="E", help="E=張數排名(預設)、B=金額排名;B 只比『有沒有出現』,不比數值")
    a = ap.parse_args()
    import run_expert_pool_watch as P
    pairs = [(sid, b) for sid, spec in P.POOLS.items() for b in spec.core]
    branches = sorted({b for _, b in pairs})
    cache = DATA_DIR / "research" / "fubon_branch_validate"
    cache.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    dates = [r[0] for r in db.execute("select distinct trade_date from stock_broker_branch_daily where trade_date>='2026-08-01' order by 1 desc limit ?", (a.days,))][::-1]
    print(f"配對 {len(pairs)}、分點 {len(branches)}、日期 {dates[0]}~{dates[-1]}({len(dates)} 日)", flush=True)
    stocks = sorted({s for s, _ in pairs})
    ph = ",".join("?" * len(stocks))
    dbrows = {}
    for d, tid, sid, bu, se in db.execute(f"select trade_date,securities_trader_id,stock_id,buy,sell from stock_broker_branch_daily where trade_date in ({','.join('?' * len(dates))}) and stock_id in ({ph})", dates + stocks):
        dbrows[(d, tid, sid)] = (bu or 0, se or 0)
    ids_in_db = {k[1] for k in dbrows}
    print("核心分點在 DB 出現:", sum(b in ids_in_db for b in branches), "/", len(branches), "缺:", [b for b in branches if b not in ids_in_db], flush=True)
    want = {}                                                   # (分點,日) → 該分點在池內的股票集合
    for sid, b in pairs:
        want.setdefault(b, set()).add(sid)
    floors = {sid: float(spec.net_floor) for sid, spec in P.POOLS.items()}      # 各池觸發訊號所需的淨買賣金額門檻(元)
    px = {(sid, d): c for sid, d, c in db.execute(f"select stock_id,trade_date,close from stock_daily_bars where trade_date in ({','.join('?' * len(dates))}) and stock_id in ({ph})", dates + stocks)}
    sized = []                                                  # (淨金額(元), 該池門檻, 是否在頁面)
    stat = dict(db_rows=0, found=0, agree=0, disagree=0, spurious=0, pairs_checked=0, trunc_days=0, branch_days=0, nodata=0)
    miss, bad = [], []
    for i, b in enumerate(branches):
        for d in dates:
            pg = fetch(b, d, cache, a.sleep, a.kind)
            stat["branch_days"] += 1
            stat["nodata"] += pg["nodata"]
            trunc = pg["n"] >= 100
            stat["trunc_days"] += trunc
            for sid in want[b]:
                stat["pairs_checked"] += 1
                dbv = dbrows.get((d, b, sid))
                inpage = sid in pg["rows"]
                if dbv:
                    stat["db_rows"] += 1
                    c = px.get((sid, d))
                    if c:
                        sized.append((abs(dbv[0] - dbv[1]) * c, floors[sid], inpage))
                    if inpage:
                        stat["found"] += 1
                        pb, ps = pg["rows"][sid]
                        if a.kind == "B" or (abs(pb - dbv[0] / 1000) <= 1 and abs(ps - dbv[1] / 1000) <= 1):
                            stat["agree"] += 1
                        else:
                            stat["disagree"] += 1
                            bad.append((b, d, sid, (pb, ps), (round(dbv[0] / 1000), round(dbv[1] / 1000))))
                    else:
                        miss.append((b, d, sid, round(dbv[0] / 1000), round(dbv[1] / 1000), trunc))
                elif inpage:
                    stat["spurious"] += 1
        print(f"  [{i + 1}/{len(branches)}] {b} 完成", flush=True)
    print("\n== 彙總 ==")
    print(json.dumps(stat, ensure_ascii=False))
    if stat["db_rows"]:
        print(f"recall = {stat['found']}/{stat['db_rows']} = {stat['found'] / stat['db_rows']:.1%};  一致 = {stat['agree']}/{stat['found']}")
    print(f"分點日頁面達 100 列(Top-50 限制生效): {stat['trunc_days']}/{stat['branch_days']}")
    print("\n依部位大小(|淨買賣金額| = 淨股數 × 收盤價)的召回率(頁面有出現的比例):")
    for lab, cond in (("全部", lambda a, f: True), ("≥2000 萬", lambda a, f: a >= 2e7), ("≥5000 萬", lambda a, f: a >= 5e7),
                      ("≥1 億", lambda a, f: a >= 1e8), ("≥ 各池觸發門檻", lambda a, f: a >= f)):
        sel = [x for x in sized if cond(x[0], x[1])]
        if sel:
            print(f"  {lab:<12} n={len(sel):>4}  召回 {sum(1 for x in sel if x[2])}/{len(sel)} = {sum(1 for x in sel if x[2]) / len(sel):.1%}")
    big = sorted(miss, key=lambda r: -(r[3] + r[4]))
    print(f"漏掉的 DB 列 {len(miss)} 筆;其中頁面已滿 100 列者 {sum(1 for r in miss if r[5])};金額最大前 8:")
    for r in big[:8]:
        print("  ", r)
    print(f"數值不一致 {len(bad)} 筆,前 5:", bad[:5])
    (cache / ("summary.json" if a.kind == "E" else "summaryB.json")).write_text(json.dumps({"stat": stat, "miss": miss, "bad": bad}, ensure_ascii=False))


if __name__ == "__main__":
    main()
