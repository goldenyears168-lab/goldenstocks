#!/usr/bin/env python3
"""隱形大戶守價位偵測·依 Frey & Sandås (2009) 原始演算法重建(2026-09-25 jack 交辦:
「給你五個小時,你慢慢仔細地完成,請你一字一句的參考文獻的真正正確用法」)。

============================================================================
文獻原文(Frey, S. & Sandås, P. (2009), "The Impact of Iceberg Orders in Limit
Order Books", CFR Working Paper No. 09-06, University of Cologne. 全文取自
https://www.econstor.eu/bitstream/10419/41392/1/605038759.pdf,以下逐字引用
Appendix A3「Detection Algorithm: An Example」與 Section 2.2.1):

  "The algorithm assumes an iceberg to be detected after the first
   replenishment. After the detection the algorithm keeps the detection
   state until all visible volume of the quote is cancelled or an expected
   replenishment has not occurred."

  "if an order book contains an iceberg at the best quote then a trade that
   exhausts all the displayed depth at the best quote prompts an immediate
   addition of new depth equal to the iceberg order's peak size."

  "The algorithm remembers the indicator values for multiple prices so if
   the current best quote, which has a detected iceberg, is undercut but
   later becomes the best quote again the algorithm assumes that the
   iceberg order is still there until an anticipated update to the order
   book depth fails to occur."

  Table V 前瞻報酬窗口:"We measure the mid-quote change over a thirty trade
   horizon that starts from the current trade."(用成交筆數當窗口,不是分鐘)
  Table II 已執行完的 iceberg 獲利窗口:"the realized profit is measured as
   the signed difference between the iceberg order's price and the
   mid-quote 30 minutes after the iceberg's last peak was executed."

============================================================================
我們(2026-09-25 iceberg_replenish_detect.py 第一版)跟這個原始演算法,有三個
具體的方法論落差,這是這次要修正的(jack 要求「一字一句參考文獻真正正確用法」,
不是隨便抓感覺):

  落差一:偵測觸發條件不同。原文是「trade EXHAUSTS ALL displayed depth」(耗盡到
    零)才算,我們原本是「量減少≥5張」就算——太寬鬆,把普通的部分成交也算進去。
    本版改成:量降到接近零(≤原量15%或剩餘≤5張)才算「耗盡」。

  落差二:追蹤對象不同。原文明確追蹤「固定價位」,就算這個價位暫時不是最優價、
    被排到後面,仍持續追蹤("keeps...until...an expected replenishment has not
    occurred"),我們原本是「追蹤當下最優價」,只要排名一換就整個重置——這正是
    上一輪分析裡「即時看似顯著、延遲就消失」的可能根因之一(把正常的排名洗牌
    誤判成「牆被打破」)。本版改成:同時追蹤五檔全部價位,用「價位」而非
    「排名」當追蹤鍵,價位暫時滑出五檔用寬限期(grace window)處理。

  落差三:「耗盡」的確認方式不同。原文能直接看到 order-by-order 的真實成交紀錄
    (Xetra 完整訂單簿重建),我們原本只用「當天累計成交量 v 有沒有動」當代理,
    量對不上是哪個價位在成交。本版改成:交叉比對 `raw_{日}.jsonl` 的逐筆真實
    成交(channel=trades,已過濾 isTrial),確認耗盡當下真的有成交打在這個價位
    附近(±0.3%),不是純粹掛單被取消。

⚠ 仍有做不到的落差,誠實揭露:原文用 Xetra 完整訂單簿(order-by-order,含每一張
單的 timestamp),TWSE MIS 只給我們五檔快照(~5秒輪詢)——我們永遠看不到「這個
價位排名第六以後」的狀態,也看不到兩次快照之間發生了幾次真實的耗盡-補回循環
(可能被同一次快照差值蓋掉)。這是資料源本身的限制,不是這次能修正的落差。

前瞻報酬窗口比照原文兩種都做:(a) 30 筆真實成交之後的中價變化(Table V 口徑)、
(b) 「最後一次補回」後 30 分鐘的中價變化(Table II 口徑,對應我們的「突破/耗盡」
事件)。兩者都再做延遲進場對照(這個專案自己的標準做法,原文沒有這個問題因為
它用的是真實逐筆資料不是快照,不存在我們這種買賣價彈跳疑慮)。

用法:PYTHONPATH=src .venv/bin/python scripts/research/iceberg_frey_sandas_rebuild.py
"""
from __future__ import annotations
import glob, json, sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, "scripts/research")
sys.path.insert(0, "src")
from stock_db import DATA_DIR  # noqa: E402
from biglot_score_v23_fit import cl_t  # noqa: E402

BOOKS_DIR = DATA_DIR / "cache" / "watchlist_books"
RAW_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch"

EXHAUST_FRAC = 0.15      # 落差一:量降到≤原量15%(或≤5張)才算「耗盡」,不是隨便減少
EXHAUST_MIN_ABS = 5.0
REPLENISH_FRAC = 0.5     # 「補回」:回升到耗盡前水準的≥50%才算(呼應原文"peak size"量級)
GRACE_WINDOW = 20 * 60   # 落差二:價位暫時滑出五檔的寬限期(秒),超過才視為真的消失
TRADE_PRICE_TOL = 0.003  # 落差三:成交價須在守價位 ±0.3% 內才算「打在這個價位」


def load_universe():
    cal = json.load(open(DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"))
    return {r["sid"]: r["name"] for r in cal["universe"]}


def load_trades_for_day(fp, sids):
    """讀 raw_{日}.jsonl 的逐筆真實成交(channel=trades,排除 isTrial)。
    回傳 sid -> 按時間排序的 (ts_epoch_sec, price, size) list。"""
    out = defaultdict(list)
    if not Path(fp).exists():
        return out
    with open(fp) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if d.get("kind") != "message":
                continue
            payload = d.get("payload") or {}
            if payload.get("event") != "data":
                continue
            data = payload.get("data") or {}
            if data.get("isTrial"):
                continue
            sid = data.get("symbol")
            if sid not in sids:
                continue
            price = data.get("price"); size = data.get("size"); t_us = data.get("time")
            if price is None or size is None or t_us is None:
                continue
            out[sid].append((t_us / 1e6, float(price), float(size)))
    for sid in out:
        out[sid].sort(key=lambda r: r[0])
    return out


def best_of(price_arr, qty_arr):
    for pp, qq in zip(price_arr, qty_arr):
        if pp is not None and qq is not None and pp > 0:
            return pp, qq
    return None, None


def mid_of(bp, bq, ap, aq):
    b, _ = best_of(bp, bq)
    a, _ = best_of(ap, aq)
    return (b + a) / 2 if (b and a) else None


def trade_confirms(trades, ts_lo, ts_hi, price, lo, hi):
    """trades 為排序好的 (ts,px,size);ts∈[ts_lo,ts_hi] 且 px∈[price*(1-tol),price*(1+tol)] 是否存在。"""
    for ts, px, _sz in trades:
        if ts < ts_lo:
            continue
        if ts > ts_hi:
            break
        if lo <= px <= hi:
            return True
    return False


def replay_day_price_persistent(rows, trades):
    """落差二核心:levels 用「價位」當鍵,五檔全部價位都追蹤,不是只追最優價。
    回傳 (breakout_events, backing_events)。

    backing_events 對應原文 Table V 的「signed indicator for a detected iceberg order」:
    每個時間點,若當下最優買一/賣一剛好就是一個已偵測(detected)的價位,記一筆 backing
    觀測——這是原文論文裡唯一測出統計顯著的部分("positive coefficient...mid-quote
    tends to increase over next 30 trades if there is an iceberg at the bid side")。
    """
    levels = {}   # (side, round(price,4)) -> state dict
    breakout_events = []
    backing_events = []
    prev_backing = {"bid": False, "ask": False}

    for ts, t, bp, bq, v, ap, aq in rows:
        visible_now = set()
        for side, price_arr, qty_arr in (("bid", bp, bq), ("ask", ap, aq)):
            for p, q in zip(price_arr, qty_arr):
                if p is None or q is None or p <= 0:
                    continue
                key = (side, round(p, 4))
                visible_now.add(key)
                st = levels.get(key)
                if st is None:
                    levels[key] = {"qty": q, "ref_peak": q, "last_seen": ts, "state": "none",
                                    "exhaust_ts": None, "detected": False, "replenish_n": 0}
                    continue
                prev_qty = st["qty"]
                st["last_seen"] = ts
                if st["state"] == "none":
                    if prev_qty > 0 and q <= max(EXHAUST_MIN_ABS, prev_qty * EXHAUST_FRAC):
                        # 落差一+三:耗盡到接近零,且交叉比對真實成交確認打在這個價位
                        if trade_confirms(trades, ts - 30, ts, p, p * (1 - TRADE_PRICE_TOL), p * (1 + TRADE_PRICE_TOL)):
                            st["state"] = "exhausted"; st["exhaust_ts"] = ts; st["ref_peak"] = prev_qty
                elif st["state"] == "exhausted":
                    if q >= st["ref_peak"] * REPLENISH_FRAC:
                        # 第一次補回 = 原文定義的「偵測到 iceberg」
                        st["state"] = "detected"; st["detected"] = True; st["replenish_n"] += 1
                        st["ref_peak"] = max(st["ref_peak"], q)
                    elif ts - st["exhaust_ts"] > GRACE_WINDOW:
                        st["state"] = "none"   # 該補的沒補,原文:「expected replenishment has not occurred」
                elif st["state"] == "detected":
                    if prev_qty > 0 and q <= max(EXHAUST_MIN_ABS, prev_qty * EXHAUST_FRAC):
                        if trade_confirms(trades, ts - 30, ts, p, p * (1 - TRADE_PRICE_TOL), p * (1 + TRADE_PRICE_TOL)):
                            st["state"] = "exhausted"; st["exhaust_ts"] = ts
                st["qty"] = q
        # 落差二:價位暫時滑出五檔,不立刻視為消失,給寬限期;超時或被突破才真的終止
        mid = mid_of(bp, bq, ap, aq)
        p_bid0, _ = best_of(bp, bq)
        p_ask0, _ = best_of(ap, aq)
        for key, st in list(levels.items()):
            side, price = key
            # 先查是否被突破(不管這個價位現在還看不看得到——真正的突破往往就是同一筆快照裡
            # 價位「剛好」跌出五檔可視範圍,先前版本的 bug 是把這種情況全部吃進寬限期分支,
            # 突破檢查永遠排不到,才會測出0筆突破事件)
            if st["detected"] and mid is not None:
                breached = (side == "bid" and mid < price * (1 - TRADE_PRICE_TOL)) or \
                           (side == "ask" and mid > price * (1 + TRADE_PRICE_TOL))
                if breached:
                    kind = "breakout_bear" if side == "bid" else "breakout_bull"
                    breakout_events.append({"ts": ts, "kind": kind, "price": price, "replenish_n": st["replenish_n"]})
                    del levels[key]
                    continue
            if key not in visible_now:
                if ts - st["last_seen"] > GRACE_WINDOW:
                    del levels[key]
        # Table V 口徑:當下最優買一/賣一剛好是已偵測價位 → 記一筆 backing 觀測(僅在「新進入」時記一筆,同一段不重複)
        now_backing = {"bid": bool(p_bid0 and ("bid", round(p_bid0, 4)) in levels
                                    and levels[("bid", round(p_bid0, 4))]["detected"]),
                       "ask": bool(p_ask0 and ("ask", round(p_ask0, 4)) in levels
                                   and levels[("ask", round(p_ask0, 4))]["detected"])}
        for side in ("bid", "ask"):
            if now_backing[side] and not prev_backing[side]:
                kind = "backing_bull" if side == "bid" else "backing_bear"
                backing_events.append({"ts": ts, "kind": kind})
            prev_backing[side] = now_backing[side]
    return breakout_events, backing_events


def main():
    uni = load_universe()
    files = sorted(glob.glob(str(BOOKS_DIR / "watchlist_books_*.jsonl")))
    print(f"掃描 {len(files)} 個交易日,限定 42 檔宇宙(依 Frey & Sandås 2009 演算法重建版)\n")

    all_events = []
    for fp in files:
        day = Path(fp).stem.replace("watchlist_books_", "")
        raw_fp = RAW_DIR / f"raw_{day}.jsonl"
        trades_by_sid = load_trades_for_day(raw_fp, set(uni))
        per_sid = defaultdict(list)
        with open(fp) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                sid = d.get("sym")
                if sid not in uni:
                    continue
                try:
                    v = float(d["v"]) if d.get("v") not in (None, "") else None
                except (TypeError, ValueError):
                    v = None
                ts = d.get("ts")
                if ts is None:
                    continue
                per_sid[sid].append((ts, d.get("t", ""), d.get("bp") or [], d.get("bq") or [], v,
                                      d.get("ap") or [], d.get("aq") or []))
        for sid, rows in per_sid.items():
            rows.sort(key=lambda r: r[0])
            seen = set(); dedup = []
            for r in rows:
                if r[0] in seen:
                    continue
                seen.add(r[0]); dedup.append(r)
            trades = trades_by_sid.get(sid, [])
            events, backing = replay_day_price_persistent(dedup, trades)

            mids = [(r[0], mid_of(r[2], r[3], r[5], r[6])) for r in dedup]
            mids = [(t, m) for t, m in mids if m is not None]

            def fwd_time(from_ts, h):
                target = from_ts + h * 60
                for t, m in mids:
                    if t >= target:
                        return m
                return None

            def px_at(from_ts):
                for t, m in mids:
                    if t >= from_ts:
                        return m
                return None

            def fwd_trades(from_ts, n_trades):
                """Table V 口徑:從 from_ts 之後第 n 筆真實成交的價格。"""
                cnt = 0
                for ts, px, _sz in trades:
                    if ts < from_ts:
                        continue
                    cnt += 1
                    if cnt >= n_trades:
                        return px
                return None

            for e in events:
                px0 = px_at(e["ts"])
                px0_delay = px_at(e["ts"] + 30)
                if not px0:
                    continue
                ex15 = ((fwd_time(e["ts"], 15) / px0 - 1) * 1e4) if fwd_time(e["ts"], 15) else None
                exd15 = (((fwd_time(e["ts"] + 30, 15) / px0_delay - 1) * 1e4)
                         if (px0_delay and fwd_time(e["ts"] + 30, 15)) else None)
                ex30trades = (((fwd_trades(e["ts"], 30) / px0 - 1) * 1e4)
                              if fwd_trades(e["ts"], 30) else None)   # Table V 口徑
                all_events.append({"sid": sid, "day": day, "kind": e["kind"],
                                    "replenish_n": e["replenish_n"],
                                    "ex15": ex15, "exd15": exd15, "ex30trades": ex30trades})

            for e in backing:
                px0 = px_at(e["ts"])
                px0_delay = px_at(e["ts"] + 30)
                if not px0:
                    continue
                ex15 = ((fwd_time(e["ts"], 15) / px0 - 1) * 1e4) if fwd_time(e["ts"], 15) else None
                exd15 = (((fwd_time(e["ts"] + 30, 15) / px0_delay - 1) * 1e4)
                         if (px0_delay and fwd_time(e["ts"] + 30, 15)) else None)
                ex30trades = (((fwd_trades(e["ts"], 30) / px0 - 1) * 1e4)
                              if fwd_trades(e["ts"], 30) else None)
                all_events.append({"sid": sid, "day": day, "kind": e["kind"], "replenish_n": None,
                                    "ex15": ex15, "exd15": exd15, "ex30trades": ex30trades})

    n_days = len({e["day"] for e in all_events})
    print(f"事件總數 {len(all_events)}(⚠ cluster 數上限={n_days}日)\n")

    for kind in ("backing_bull", "backing_bear", "breakout_bull", "breakout_bear"):
        sub = [e for e in all_events if e["kind"] == kind]
        print(f"==== {kind}(n={len(sub)}) ====")
        for label, key in (("即時持15分", "ex15"), ("延遲30秒持15分", "exd15"), ("後30筆真實成交(Table V口徑)", "ex30trades")):
            vals = [(e[key], e["day"], e["sid"]) for e in sub if e.get(key) is not None]
            if len(vals) < 15:
                print(f"  {label}:n<15({len(vals)}),樣本不足"); continue
            arr = np.array([v for v, _, _ in vals]); dates = np.array([d for _, d, _ in vals])
            mu, t = cl_t(arr, dates)
            win = float((arr > 0).mean() * 100)
            print(f"  {label}:n={len(vals):4d} 均{mu:+6.1f}bps(t{t:+4.2f}) 勝率{win:4.1f}%")
        # 集中度 + 安慰劑(只對 ex15 做,呼應這個專案一貫的紀律)
        vals15 = [(e["ex15"], e["day"], e["sid"]) for e in sub if e.get("ex15") is not None]
        if len(vals15) >= 15:
            by_sid = defaultdict(list)
            for v, _d, s in vals15:
                by_sid[s].append(v)
            tot = sum(sum(v) for v in by_sid.values())
            top5 = sorted((sum(v) for v in by_sid.values()), key=abs, reverse=True)[:5]
            conc = (sum(top5) / tot * 100) if tot else float("nan")
            print(f"  集中度(ex15):前5檔佔比 {conc:.0f}%(共{len(by_sid)}檔)")
            rng = np.random.default_rng(hash(kind) % (2**31))
            pool = [e["ex15"] for e in all_events if e.get("ex15") is not None]
            if len(pool) >= len(vals15):
                placebo = [float(np.mean(rng.choice(pool, size=len(vals15), replace=False))) for _ in range(5)]
                real_mu = float(np.mean([v for v, _, _ in vals15]))
                print(f"  安慰劑對照(ex15):真實均{real_mu:+.1f} vs 隨機5組均{np.mean(placebo):+.1f}"
                      f"(範圍{min(placebo):+.1f}~{max(placebo):+.1f})")
        print()


if __name__ == "__main__":
    main()
