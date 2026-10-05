#!/usr/bin/env python3
"""複製人代理人單日回測:當天所有建議委託若「全部照送」,哪些會成交、成交後 N 分鐘績效。

資料:期貨逐筆 `cache/{root}_trades/` (依 trade_time+serial 去重) + 五檔委託簿 `cache/{root}_books/`。
成交口徑(同一批單並列):
  strict = 成交價 < 限價(價格穿越;下界,不依賴排隊)
  opt    = 成交價 <= 限價(觸價即成交;上界)
  q*     = 排隊模擬(FIFO,我們是 1 口、排在該價位最後):
           送出時前面排隊量 = 簿上該價位口數;之後該價位成交口數依序消耗前面的量;
           簿上該價位減少、但減少量超過同區間成交量的部分 = 撤單。撤單在隊伍的位置看不到,分三種假設:
           qcons = 撤單全在我們後面(前面只被成交消耗,保守)
           qpro  = 撤單依前/後口數比例分配(主要估計)
           qopt  = 撤單全在我們前面(樂觀)
           簿上口數增加視為新單排在我們後面;成交價 < 限價 = 該價位整檔被掃光,必成交。
           送出當下限價 >= 賣一 → 直接以賣一成交(不排隊)。
績效 = 成交後 hold 分鐘的「最後成交價」與「賣在當時買一」兩種出場。成本 = 手續費 40x2 + 稅 0.002%x2。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

CACHE = Path.home() / "goldenstocks-data/cache"
CLONE = Path.home() / "goldenstocks-data/data/clone_agent"
TZ = "Asia/Taipei"
SUBMIT_LAG = 5          # 秒:人看到卡片到送出的最短延遲
CLOSE_HMS = "13:45:00"  # 個股期貨日盤收盤
FEE, TAX = 40.0, 0.00002
QMODES = ("qcons", "qpro", "qopt")
PRICE_MIN, PRICE_MAX = 350.0, 2000.0   # 可交易範圍:價格區間 + 必須有族群


def load_subcat():
    """從儀表板原始碼取 SUBCAT(45+ 檔手動策展族群),避免 import 整支儀表板。"""
    src = (Path(__file__).resolve().parent / "biglot_dashboard.py").read_text(encoding="utf-8")
    i = src.index("SUBCAT = {")
    j = src.index("}", src.index("3481", i))
    body = "\n".join(ln.split("#")[0] for ln in src[i + len("SUBCAT = "): j + 1].splitlines())
    return eval(body)  # noqa: S307 -- 純字面量 dict


def epoch(day, hms):
    return pd.Timestamp(f"{day} {hms}", tz=TZ).timestamp()


def resolve_root(sym, day):
    """收集器的目錄名是產品代碼:多數是 2 碼(RA),但 IRF/LUF/CCF 等是 3 碼,逐一試到有檔為止。"""
    for r in (sym[:2], sym[:3]):
        if r and (CACHE / f"{r.lower()}_trades" / f"{r.lower()}_trades_{day}.jsonl").exists():
            return r
    return ""


def load_trades(root, day):
    p = CACHE / f"{root.lower()}_trades" / f"{root.lower()}_trades_{day}.jsonl"
    if not p.exists():
        return None
    seen, rows = set(), []
    for line in p.open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("stale") or r["ts"][:10] != day:
            continue
        k = (r["trade_time"], r["serial"])
        if k in seen:
            continue
        seen.add(k)
        t = r["trade_time"] / 1e6
        if epoch(day, "08:45:00") <= t <= epoch(day, CLOSE_HMS) + 5:
            rows.append((t, float(r["price"]), r.get("bid"), r.get("ask"), float(r["size"])))
    rows.sort()
    return rows


def load_books(root, day):
    p = CACHE / f"{root.lower()}_books" / f"{root.lower()}_books_{day}.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("stale") or r.get("session") != "day" or r["ts"][:10] != day:
            continue
        t = r["book_time"] / 1e6
        if epoch(day, "08:45:00") <= t <= epoch(day, CLOSE_HMS) + 5:
            # 單邊簿(漲跌停鎖死、薄契約)照樣保留:丟掉會讓簿狀態凍結在上一筆雙邊簿
            bids = {float(x["price"]): float(x["size"]) for x in r.get("bids") or [] if float(x["price"]) > 0}
            asks = [float(x["price"]) for x in r.get("asks") or [] if float(x["price"]) > 0]
            rows.append((t, bids, min(bids) if bids else float("inf"), min(asks) if asks else float("inf")))
    rows.sort(key=lambda x: x[0])
    return rows


def level_size(book, lowest, L):
    """簿上價位 L 的口數;低於可見最低買價時看不到(回 0 並標記不可見)。"""
    if L in book:
        return book[L], True
    return 0.0, L >= lowest


def simulate_queue(tr, bk, L, t0, mode, close_t):
    """回傳 (成交時間, 成交價, 前方排隊量, 是否可見, 累計撤單估計)。未成交時成交時間為 None。"""
    bt = [b[0] for b in bk]
    i = bisect.bisect_right(bt, t0) - 1
    if i < 0:
        return None, None, None, False, 0.0
    _, book, lowest, ask = bk[i]
    if L >= ask:
        return t0, ask, 0.0, True, 0.0
    s_last, visible = level_size(book, lowest, L)
    ahead, vL, cancels_total = s_last, 0.0, 0.0
    ev = [(t, 0, p, size) for (t, p, _, _, size) in tr if t0 < t <= close_t]
    ev += [(b[0], 1, b[1], b[2]) for b in bk[i + 1:] if b[0] <= close_t]
    ev.sort(key=lambda x: (x[0], x[1]))
    for t, kind, p, extra in ev:
        if kind == 0:
            if p < L:
                return t, L, ahead, visible, cancels_total
            if p == L:
                vL += extra
                ahead -= extra
                if ahead <= -1 + 1e-9:
                    return t, L, ahead, visible, cancels_total
        else:
            s_new, vis_new = level_size(p, extra, L)
            visible = visible or vis_new
            canc = max(0.0, (s_last - s_new) - vL)
            base = s_last - vL
            if canc > 0:
                cancels_total += canc
                if ahead > 0:
                    if mode == "qopt":
                        ahead = max(0.0, ahead - canc)
                    elif mode == "qpro" and base > 0:
                        ahead = max(0.0, ahead - canc * min(1.0, ahead / base))
            s_last, vL = s_new, 0.0
    return None, None, ahead, visible, cancels_total


def main():
    global CLOSE_HMS
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--cand-dir", default=str(CLONE), help="候選檔目錄(歷史重算用 scratch/clone_agent_scan)")
    ap.add_argument("--lag", type=int, default=SUBMIT_LAG, help="卡片出現到送出委託的秒數(人工批准延遲)")
    ap.add_argument("--close", default=CLOSE_HMS, help="強制平倉/停止排隊時間(預設 13:45:00 收盤;PRD 為 13:25:00)")
    ap.add_argument("--hold", type=int, default=60, help="成交後幾分鐘結算")
    ap.add_argument("--out", default=str(Path.home() / "goldenstocks-data/data/scratch/clone_agent_day_backtest.csv"))
    a = ap.parse_args()
    CLOSE_HMS = a.close
    day = a.day
    cands = [json.loads(l) for l in (Path(a.cand_dir) / f"candidates_{day}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    fp = json.loads((CACHE / f"biglot_live_watch/futprice_{day}.json").read_text(encoding="utf-8"))
    subcat = load_subcat()
    tcache, bcache, out = {}, {}, []
    close_t = epoch(day, CLOSE_HMS)
    tz = pd.Timestamp(0, tz=TZ).tz
    for c in cands:
        sid = c["sid"]
        root = resolve_root((fp.get(sid) or {}).get("sym", ""), day)
        L = c["price"]
        rec = {"day": day, "time": c["time"], "sid": sid, "name": c["uname"], "stars": c["stars"], "limit": L,
               "shares": c["shares"], "root": root, "group": subcat.get(sid),
               "margin": c.get("margin"), "lag": a.lag, "in_univ": bool(subcat.get(sid)) and PRICE_MIN <= L <= PRICE_MAX}
        tr = tcache.setdefault(root, load_trades(root, day)) if root else None
        if not tr:
            rec["note"] = "當天無期貨逐筆資料(futprice 無此檔或收集器未訂閱)" if not root else "逐筆檔為空"
            out.append(rec)
            continue
        bk = bcache.setdefault(root, load_books(root, day))
        ts = [x[0] for x in tr]
        t0 = epoch(day, c["time"]) + a.lag
        i0 = bisect.bisect_right(ts, t0)
        fills = {}
        for mode, test in (("strict", lambda p, L: p < L), ("opt", lambda p, L: p <= L)):
            j = next((k for k in range(i0, len(tr)) if test(tr[k][1], L)), None)
            fills[mode] = (None, None) if j is None else (tr[j][0], L)
        fills["all"] = (t0, L)   # 不問排隊:送出即以限價成交(假設無保證金/成交限制的理論上限)
        for mode in QMODES:
            tf, px, ahead, vis, canc = simulate_queue(tr, bk, L, t0, mode, close_t) if bk else (None,) * 5
            fills[mode] = (tf, px)
            if mode == "qpro":
                rec["q_vis"] = vis
                rec["q_cancels"] = round(canc, 1)
        if bk:
            i = bisect.bisect_right([b[0] for b in bk], t0) - 1
            if i >= 0:
                rec["q_ahead0"] = level_size(bk[i][1], bk[i][2], L)[0]
                rec["q_marketable"] = L >= bk[i][3]
        for mode, (tf, entry) in fills.items():
            if tf is None:
                rec[f"{mode}_fill"] = None
                continue
            j = max(bisect.bisect_left(ts, tf), 0)
            rec[f"{mode}_fill"] = datetime.fromtimestamp(tf, tz).strftime("%H:%M:%S")
            rec[f"{mode}_wait_min"] = round((tf - t0 + a.lag) / 60, 1)
            t1 = min(tf + a.hold * 60, close_t)
            k = max(bisect.bisect_right(ts, t1) - 1, j)
            exit_last = tr[k][1]
            bids = [x[2] for x in tr[j:k + 1] if x[2]]
            exit_bid = bids[-1] if bids else exit_last
            span = [x[1] for x in tr[j:k + 1]] or [exit_last]
            rec[f"{mode}_held_min"] = round((t1 - tf) / 60, 1)
            rec[f"{mode}_trunc"] = (t1 - tf) < a.hold * 60 - 1
            rec[f"{mode}_entry"] = entry
            rec[f"{mode}_last"] = exit_last
            rec[f"{mode}_bid"] = exit_bid
            rec[f"{mode}_min"] = min(span)
            rec[f"{mode}_max"] = max(span)
            for kind, px in (("last", exit_last), ("bid", exit_bid)):
                gross = (px - entry) * c["shares"]
                cost = 2 * FEE + TAX * (entry + px) * c["shares"]
                rec[f"{mode}_net_{kind}"] = round(gross - cost)
                rec[f"{mode}_bps_{kind}"] = round(10000 * (px / entry - 1), 1)
        out.append(rec)
    df = pd.DataFrame(out)
    df.to_csv(a.out, index=False)
    print(f"wrote {a.out} n={len(df)}")


if __name__ == "__main__":
    sys.exit(main())
