#!/usr/bin/env python3
"""固定觀察清單的五檔委託簿收集器 · 唯讀 · TWSE MIS · **不佔富邦 ws 連線額度**。

與 collect_limitup_books 同一套 MIS 五檔抓法（import 重用），差別：不掃漲停候選，
改成收一份**固定清單**（預設高波動 45 = _live_calib.json 的 universe，含台達電 2308）
的委託簿，每天逐筆記錄每檔在各價位的買/賣掛單量（牆），供「大戶的牆在哪、撐不撐得住」
研究——成交側看得到隊列被吃多少、看不到補進多少，後者才是關鍵，只有委託簿看得到。

輸出 ${GOLDENSTOCKS_DATA_DIR}/cache/watchlist_books/watchlist_books_YYYY-MM-DD.jsonl
清單來源：WL_SYMS=2308,2454,… 覆寫；否則 WL_FILE；否則預設 _live_calib.json 的 universe。
45 檔一次請求即收完（BATCH=45），POLL_SEC 5 秒可達（遠低於 limitup 的 180+ 檔負擔）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stock_db import DATA_DIR
from collect_limitup_books import fetch, parse_depth, market_map, in_session, BATCH, TPE

OUT_DIR = DATA_DIR / "cache" / "watchlist_books"
POLL_SEC = float(os.environ.get("WL_POLL_SEC", "5"))


def load_watchlist() -> list[str]:
    env = os.environ.get("WL_SYMS")
    if env:
        return [s.strip() for s in env.split(",") if s.strip()]
    wf = os.environ.get("WL_FILE") or str(
        DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json")
    d = json.loads(open(wf).read())
    uni = d["universe"] if isinstance(d, dict) else d
    out = []
    for r in uni:
        out.append(str(r["sid"] if isinstance(r, dict) else r))
    return out


def main() -> None:
    once = "--once" in sys.argv
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    syms = load_watchlist()
    mm = market_map(syms)
    chs = [f"{mm[s]}_{s}.tw" for s in syms if s in mm]
    print(f"觀察清單 {len(chs)}/{len(syms)} 檔（輪詢 {POLL_SEC:.0f}s）→ {OUT_DIR}", flush=True)
    last_snap: dict[str, tuple] = {}
    n_new = n_dup = 0
    last_stat = 0.0
    while True:
        now = datetime.now(TPE)
        if not once and not in_session(now):
            time.sleep(30)
            continue
        day = now.strftime("%Y-%m-%d")
        out = OUT_DIR / f"watchlist_books_{day}.jsonl"
        recs = []
        for i in range(0, len(chs), BATCH):
            for m in fetch(chs[i:i+BATCH]):
                r = parse_depth(m)
                if not r:
                    continue
                key = (r["t"], r["v"], tuple(r["bq"]), tuple(r["aq"]),
                       tuple(r["bp"]), tuple(r["ap"]))
                if last_snap.get(r["sym"]) == key:
                    n_dup += 1
                    continue
                last_snap[r["sym"]] = key
                n_new += 1
                r["ts"] = time.time()
                r["tier"] = "watch"
                recs.append(r)
        if recs:
            with out.open("a") as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if once:
            print(f"寫 {len(recs)} 筆（新 {n_new} 重複 {n_dup}）→ {out}", flush=True)
            return
        if time.monotonic() - last_stat >= 600:
            last_stat = time.monotonic()
            tot = n_new + n_dup
            if tot:
                print(f"  [stat] {now:%H:%M} 清單 {len(chs)}　新 {n_new:,} 重複 {n_dup:,}"
                      f"（重複率 {n_dup/tot*100:.0f}%）", flush=True)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
