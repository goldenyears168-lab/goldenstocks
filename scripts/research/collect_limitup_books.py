#!/usr/bin/env python3
"""現股漲停候選的五檔委託簿收集器 · 唯讀 · **不碰富邦 session、不佔 ws 連線額度**。

為什麼用 TWSE MIS 而不是富邦 websocket：mini 上已有 5 條富邦 ws（TMF 送單／
momentum-rotation 現股／指數期 books／個股期 books／成交回報），registry 實測第 6 條
會撞 "Maximum number of connections reached"（2026-08-21 那次讓指數組日盤掉 52%）。
MIS 是公開 API，另一條路，完全不與生產爭資源。

要收的東西（成交側原理上看不到的那一半）：
  漲停價上的**買方排隊量**與其變化 —— 判斷「後面排隊變多還是變少」。
  逐筆只看得到隊列被吃掉多少，看不到補進多少；後者才是鎖不鎖得住的決定變數。

兩層輪詢：
  掃描層 每 SCAN_SEC 秒把全宇宙掃一遍，漲幅 >= WATCH_PCT 的進追蹤名單（只加不減）
  追蹤層 每 POLL_SEC 秒對追蹤名單取五檔，逐筆寫 jsonl

輸出：${GOLDENSTOCKS_DATA_DIR}/cache/limitup_books/limitup_books_YYYY-MM-DD.jsonl
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from stock_db import DATA_DIR, DEFAULT_DB_PATH

TPE = timezone(timedelta(hours=8))
OUT_DIR = DATA_DIR / "cache" / "limitup_books"
MAP_FILE = OUT_DIR / "_market_map.json"
PEER_FILE = OUT_DIR / "_peer_map.json"
MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp"}

BATCH = 45                                              # 每次請求的檔數
SCAN_SEC = float(os.environ.get("LB_SCAN_SEC", "300"))  # 全宇宙掃描週期
POLL_SEC = float(os.environ.get("LB_POLL_SEC", "5"))    # 追蹤名單輪詢週期
# 5 秒是保守值：TWSE MIS 是快照服務、公認更新頻率約 5 秒，過度取樣沒有資訊增益、
# 只增加被封鎖的風險。收集器會自己統計「重複快照率」，第一天收完就能用證據調這個值。
WATCH_PCT = float(os.environ.get("LB_WATCH_PCT", "0.06"))
MAX_WATCH = int(os.environ.get("LB_MAX_WATCH", "180"))
# 同儕層：統計同儕（日報酬相關最高 K 檔）的委託簿也收，但用低頻——
# 族群隊列的變化尺度是分鐘級，不需要 3 秒。候選永遠優先佔位，同儕吃剩下的額度。
PEER_POLL_SEC = float(os.environ.get("LB_PEER_POLL_SEC", "15"))
MAX_PEER = int(os.environ.get("LB_MAX_PEER", "250"))
PEERS_ON = os.environ.get("LB_PEERS", "1") not in ("0", "false", "")
REQ_GAP = float(os.environ.get("LB_REQ_GAP", "0.35"))   # 請求間最小間隔（禮貌節流）

_last_req = 0.0


def fetch(chs: list[str]) -> list[dict]:
    """向 MIS 取一批；失敗回空 list（收集器不得因單次失敗而死）。"""
    global _last_req
    gap = REQ_GAP - (time.monotonic() - _last_req)
    if gap > 0:
        time.sleep(gap)
    url = f"{MIS}?json=1&delay=0&ex_ch={'|'.join(chs)}&_={int(time.time()*1000)}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HDR)
            with urllib.request.urlopen(req, timeout=15) as r:
                _last_req = time.monotonic()
                d = json.loads(r.read())
            return d.get("msgArray") or []
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
            _last_req = time.monotonic()
            if attempt == 2:
                print(f"  [warn] MIS 失敗 {type(exc).__name__}: {exc}", flush=True)
                return []
            time.sleep(2 ** attempt)
    return []


def universe() -> list[str]:
    """近 20 日有交易的四位數普通股，**不設成交值門檻** ——
    漲停股平常常常很冷門、是漲停當天才放量，用平時成交值篩會把它們濾掉。"""
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    cut = (datetime.now(TPE) - timedelta(days=20)).strftime("%Y-%m-%d")
    rows = con.execute(
        """SELECT stock_id FROM stock_daily_bars
           WHERE trade_date >= ? AND source='finmind' AND LENGTH(stock_id)=4
             AND stock_id NOT LIKE '00%'
           GROUP BY stock_id""", (cut,)).fetchall()
    return [r[0] for r in rows]


def market_map(syms: list[str]) -> dict:
    """symbol → 'tse'/'otc'，探測一次後快取（新股會自動補探）。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    m = json.loads(MAP_FILE.read_text()) if MAP_FILE.exists() else {}
    todo = [s for s in syms if s not in m]
    if todo:
        print(f"探測市場別 {len(todo)} 檔…", flush=True)
        for pref in ("tse", "otc"):
            rest = [s for s in todo if s not in m]
            for i in range(0, len(rest), BATCH):
                got = fetch([f"{pref}_{s}.tw" for s in rest[i:i+BATCH]])
                for g in got:
                    if g.get("c"):
                        m[g["c"]] = pref
        MAP_FILE.write_text(json.dumps(m))
        print(f"  對照表 {len(m)} 檔 → {MAP_FILE}", flush=True)
    return m


def parse_depth(m: dict) -> dict | None:
    def nums(s):
        return [float(x) for x in (s or "").split("_") if x not in ("", "-")]
    try:
        z = float(m.get("z")) if m.get("z") not in (None, "-", "") else None
        y = float(m.get("y")) if m.get("y") not in (None, "-", "") else None
        u = float(m.get("u")) if m.get("u") not in (None, "-", "") else None
    except (TypeError, ValueError):
        return None
    if not y:
        return None
    bp, bq = nums(m.get("b")), nums(m.get("g"))
    ap, aq = nums(m.get("a")), nums(m.get("f"))
    return dict(sym=m.get("c"), t=m.get("t"), d=m.get("d"), z=z, y=y, u=u,
                v=m.get("v"), h=m.get("h"), l=m.get("l"), o=m.get("o"),
                bp=bp, bq=bq, ap=ap, aq=aq,
                # 便利欄位：漲停價上的買方排隊量（就是我們要的那半邊）
                lu_bid_qty=(bq[bp.index(u)] if (u and u in bp and len(bq) > bp.index(u)) else 0.0),
                at_lu=(z is not None and u is not None and abs(z - u) < 1e-9))


def load_peers() -> dict:
    if not (PEERS_ON and PEER_FILE.exists()):
        return {}
    try:
        d = json.loads(PEER_FILE.read_text())
    except (ValueError, OSError):
        return {}
    print(f"同儕表 {len(d.get('peers') or {})} 檔（形成期 {d.get('formation')}，"
          f"最高相關中位 {d.get('median_top_corr')}）", flush=True)
    return d.get("peers") or {}


def in_session(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 - 5 <= hm <= 13 * 60 + 35


def main() -> None:
    once = "--once" in sys.argv
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    uni = universe()
    mm = market_map(uni)
    chs_all = [f"{mm[s]}_{s}.tw" for s in uni if s in mm]
    print(f"宇宙 {len(chs_all)} 檔（掃描 {SCAN_SEC:.0f}s／追蹤 {POLL_SEC:.0f}s／"
          f"門檻 +{WATCH_PCT*100:.0f}%／上限 {MAX_WATCH}）", flush=True)

    peer_map = load_peers()
    watch: dict[str, str] = {}        # 候選（漲幅 >= 門檻），3 秒輪
    peerwatch: dict[str, str] = {}    # 候選的統計同儕，15 秒輪
    dropped = drop_peer = 0
    n_new = n_dup = 0
    last_snap: dict[str, tuple] = {}
    last_stat = 0.0
    scan_i, last_scan, last_peer = 0, 0.0, 0.0
    while True:
        now = datetime.now(TPE)
        if not once and not in_session(now):
            time.sleep(30)
            continue
        day = now.strftime("%Y-%m-%d")
        out = OUT_DIR / f"limitup_books_{day}.jsonl"

        # 掃描層：分批攤在整個週期裡，避免瞬間打爆 MIS
        nbatch = (len(chs_all) + BATCH - 1) // BATCH
        if time.monotonic() - last_scan >= SCAN_SEC / max(1, nbatch):
            last_scan = time.monotonic()
            chunk = chs_all[scan_i*BATCH:(scan_i+1)*BATCH]
            scan_i = (scan_i + 1) % max(1, nbatch)
            for m in fetch(chunk):
                r = parse_depth(m)
                if not r or r["z"] is None:
                    continue
                if r["z"] / r["y"] - 1 >= WATCH_PCT and r["sym"] not in watch:
                    if len(watch) >= MAX_WATCH:
                        dropped += 1
                        continue
                    watch[r["sym"]] = mm.get(r["sym"], "tse")
                    peerwatch.pop(r["sym"], None)          # 升級成候選就不再算同儕
                    added = 0
                    for ps in peer_map.get(r["sym"], []):
                        if ps in watch or ps in peerwatch or ps not in mm:
                            continue
                        if len(peerwatch) >= MAX_PEER:
                            drop_peer += 1
                            continue
                        peerwatch[ps] = mm[ps]; added += 1
                    print(f"  + 追蹤 {r['sym']} {(r['z']/r['y']-1)*100:+.1f}% "
                          f"(候選 {len(watch)} / 同儕 {len(peerwatch)}，本檔帶入 {added})",
                          flush=True)

        # 追蹤層（候選，高頻）
        def poll(names: dict, tier: str) -> int:
            nonlocal n_new, n_dup
            if not names:
                return 0
            wl = [f"{v}_{k}.tw" for k, v in names.items()]
            recs = []
            for i in range(0, len(wl), BATCH):
                for m in fetch(wl[i:i+BATCH]):
                    r = parse_depth(m)
                    if not r:
                        continue
                    # 去重：同一檔的揭示時間、累積量、五檔全同 = 沒有新資訊，不寫
                    key = (r["t"], r["v"], tuple(r["bq"]), tuple(r["aq"]),
                           tuple(r["bp"]), tuple(r["ap"]))
                    if last_snap.get(r["sym"]) == key:
                        n_dup += 1
                        continue
                    last_snap[r["sym"]] = key
                    n_new += 1
                    r["ts"] = time.time(); r["tier"] = tier
                    recs.append(r)
            if recs:
                with out.open("a") as f:
                    for r in recs:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            return len(recs)

        poll(watch, "cand")
        # 同儕層（低頻）
        if peerwatch and time.monotonic() - last_peer >= PEER_POLL_SEC:
            last_peer = time.monotonic()
            poll(peerwatch, "peer")
        # 每 10 分鐘回報一次取樣效率（重複率高 = 輪太快，可調高 LB_POLL_SEC）
        if not once and time.monotonic() - last_stat >= 600:
            last_stat = time.monotonic()
            tot = n_new + n_dup
            if tot:
                print(f"  [stat] {now:%H:%M} 候選 {len(watch)}／同儕 {len(peerwatch)}　"
                      f"新快照 {n_new:,} 重複 {n_dup:,}（重複率 {n_dup/tot*100:.0f}%）"
                      f"　丟棄 候選{dropped}/同儕{drop_peer}", flush=True)

        if once:
            poll(peerwatch, "peer")
            print(f"候選 {len(watch)} / 同儕 {len(peerwatch)} → {out}", flush=True)
            if dropped:
                print(f"  ⚠️ 候選因上限丟棄 {dropped} 檔（調高 LB_MAX_WATCH）", flush=True)
            if drop_peer:
                print(f"  ⚠️ 同儕因上限丟棄 {drop_peer} 檔（調高 LB_MAX_PEER）", flush=True)
            return
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
