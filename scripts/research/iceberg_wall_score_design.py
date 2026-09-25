#!/usr/bin/env python3
"""隱形大戶「靠山/突破」分數設計(2026-09-25 jack 交辦:「已經15天資料量很夠了...可以先上線這個分數」)。

⚠ 使用者明確指示採納,覆蓋掉先前「等≥60天才測」的預設立場——依此案先例(SMFI/B2 同樣模式),
專業做法是誠實把限制做完整揭露、盡量把方法論做扎實,而不是拒絕執行。這裡把小樣本的每一個
弱點都做檢查(安慰劑、集中度、逐年/逐日穩健性),权重用重度縮水(比照 0.7 縮水更保守,
這裡樣本更小,縮到 0.5),供之後資料變多時重新擬合。

兩個因子的精確定義(把上一輪對話的文字直覺轉成可程式判定的規則):

  A. 靠山(backing)——當下價格附近,是否有一個「還在生效中」的守價位:
     · 「生效中」= 最近一次補回發生在過去 15 分鐘內(避免抓到已經沒人守的殘影價位)
     · 「附近」= 距離 ≤ 1.5 倍 ATR14(沿用這次 ATR 研究已驗證的正規化方式,不用原始%)
     · 方向:價格上方有買一守價位撐著(價格在守價位之上)= 支撐靠山(backing_bull);
             價格下方有賣一守價位壓著(價格在守價位之下)= 壓力靠山(backing_bear)

  B. 突破(breakout)——一個打得夠久的守價位終於被攻破,要求 hits≥5(比候選門檻3更嚴,
     避免抓到晃兩下就過去的雜訊價位):
     · 賣一守價位被向上攻破(新的最優賣價 > 原守價位)= 突破壓力(breakout_bull)
     · 買一守價位被向下攻破(新的最優買價 < 原守價位)= 跌破支撐(breakout_bear)
     · 價格只是移動但沒有反著方向穿越,不算(用「移動方向 vs 守方想守住的方向」判斷)

方法論延續 atr_key_line_research.py / key_line_daily_rigorous.py 的紀律:日聚類 cl_t、
安慰劑對照、集中度檢查、逐日穩健性(15天無法逐年只能逐日看)。前瞻報酬用 mid price
(⚠ 這是已知弱點,見腳本結尾說明,不是嚴謹的真實成交模擬)。

用法:PYTHONPATH=src .venv/bin/python scripts/research/iceberg_wall_score_design.py
"""
from __future__ import annotations
import glob, json, sqlite3, sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, "scripts/research")
sys.path.insert(0, "src")
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402
from biglot_score_v23_fit import cl_t  # noqa: E402

BOOKS_DIR = DATA_DIR / "cache" / "watchlist_books"
MIN_QTY_CHANGE = 5.0
MIN_HITS_CANDIDATE = 3
MIN_REFILLS_CANDIDATE = 2
MIN_HITS_BREAKOUT = 5          # 突破要求更嚴:打得夠久才算「好不容易」
BACKING_ATR_MULT = 1.5         # 靠山距離門檻:1.5倍ATR內
BACKING_ACTIVE_MIN = 15 * 60   # 「生效中」:最近補回在過去15分鐘內(秒)
ATR_N = 14


def load_universe():
    cal = json.load(open(DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"))
    return {r["sid"]: r["name"] for r in cal["universe"]}


def load_atr14(sids):
    """昨收為止的 ATR14(價格單位),同 biglot_dashboard._load_atr_state 的算法。"""
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    out = {}
    for sid in sids:
        rows = conn.execute(
            "SELECT trade_date, MAX(high) h, MAX(low) l, MAX(close) c FROM stock_daily_bars "
            "WHERE stock_id=? GROUP BY trade_date ORDER BY trade_date DESC LIMIT 60", (sid,)).fetchall()
        rows = [r for r in rows if all(r[1:])][::-1]
        if len(rows) < ATR_N + 2:
            continue
        h = [r[1] for r in rows]; lo = [r[2] for r in rows]; c = [r[3] for r in rows]
        tr = [None] * len(rows)
        for i in range(1, len(rows)):
            tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
        atr = sum(tr[1:ATR_N + 1]) / ATR_N
        for i in range(ATR_N + 1, len(rows)):
            atr = (atr * (ATR_N - 1) + tr[i]) / ATR_N
        out[sid] = atr
    conn.close()
    return out


def mid_of(bp, bq, ap, aq):
    b = next((p for p, q in zip(bp, bq) if p and q and p > 0), None)
    a = next((p for p, q in zip(ap, aq) if p and q and p > 0), None)
    return (b + a) / 2 if (b and a) else None


def best_of(price_arr, qty_arr):
    for pp, qq in zip(price_arr, qty_arr):
        if pp is not None and qq is not None and pp > 0:
            return pp, qq
    return None, None


def replay_day(rows, atr):
    """rows = (ts, t, bp, bq, v, ap, aq) 已排序去重。回傳:
    backing_events = [(ts, side, mid_price)]  (每次「新進入靠山狀態」記一筆,同一段連續維持不重複記)
    breakout_events = [(ts, kind)]  kind in {'breakout_bull','breakout_bear'}
    """
    backing_events, breakout_events = [], []
    cur = {"bid": None, "ask": None}     # 目前 streak 狀態,含 last_refill_ts
    in_backing = {"bid": False, "ask": False}
    for ts, _t, bp, bq, v, ap, aq in rows:
        p_bid, q_bid = best_of(bp, bq)
        p_ask, q_ask = best_of(ap, aq)
        mid = mid_of(bp, bq, ap, aq)
        for side, p0, q0 in (("bid", p_bid, q_bid), ("ask", p_ask, q_ask)):
            if p0 is None:
                continue
            c = cur[side]
            if c is None or c["price"] != p0:
                # streak 換價位:若舊 streak 已達突破門檻,判斷是否為「反著方向穿越」= breakout
                if c and c["hits"] >= MIN_HITS_BREAKOUT and c["refills"] >= MIN_REFILLS_CANDIDATE:
                    if side == "ask" and p0 > c["price"]:
                        breakout_events.append((ts, "breakout_bull"))
                    elif side == "bid" and p0 < c["price"]:
                        breakout_events.append((ts, "breakout_bear"))
                cur[side] = {"price": p0, "hits": 0, "refills": 0, "was_hit": False,
                             "last_qty": q0, "last_v": v, "last_refill_ts": None}
                in_backing[side] = False
                continue
            dq = q0 - c["last_qty"]
            dv = (v - c["last_v"]) if (v is not None and c["last_v"] is not None) else 0.0
            if dq <= -MIN_QTY_CHANGE and dv > 0:
                c["hits"] += 1; c["was_hit"] = True
            elif dq >= MIN_QTY_CHANGE and c["was_hit"]:
                c["refills"] += 1; c["was_hit"] = False; c["last_refill_ts"] = ts
            c["last_qty"] = q0
            if v is not None:
                c["last_v"] = v
        # 靠山狀態偵測(每筆快照都檢查):是否有「生效中」的守價位在附近
        if atr and mid:
            for side in ("bid", "ask"):
                c = cur[side]
                active = bool(c and c["hits"] >= MIN_HITS_CANDIDATE and c["refills"] >= MIN_REFILLS_CANDIDATE
                              and c.get("last_refill_ts") is not None and ts - c["last_refill_ts"] <= BACKING_ACTIVE_MIN)
                near = bool(active and abs(mid - c["price"]) <= BACKING_ATR_MULT * atr)
                if near and not in_backing[side]:
                    kind = "backing_bull" if (side == "bid" and mid > c["price"]) else \
                           "backing_bear" if (side == "ask" and mid < c["price"]) else None
                    if kind:
                        backing_events.append((ts, kind, mid))
                in_backing[side] = near
    return backing_events, breakout_events


def main():
    uni = load_universe()
    atr14 = load_atr14(list(uni))
    files = sorted(glob.glob(str(BOOKS_DIR / "watchlist_books_*.jsonl")))
    print(f"掃描 {len(files)} 個交易日,限定 42 檔宇宙(ATR14 可用 {len(atr14)}/{len(uni)} 檔)")

    all_events = []   # dicts: sid, day, kind, ts, mid, ex5/ex15/ex30
    for fp in files:
        day = Path(fp).stem.replace("watchlist_books_", "")
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
            mids = [(r[0], mid_of(r[2], r[3], r[5], r[6])) for r in dedup]
            mids = [(t, m) for t, m in mids if m is not None]
            backing, breakout = replay_day(dedup, atr14.get(sid))

            def fwd(from_ts, h):
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

            for ts, kind, mid in backing:
                px0 = px_at(ts)
                px0_delay = px_at(ts + 30)   # 延遲30秒進場對照(見 fade-edge-refuted-by-delay-test 教訓)
                if not px0:
                    continue
                ex = {h: (((fwd(ts, h) / px0 - 1) * 1e4) if fwd(ts, h) else None) for h in (5, 15, 30)}
                exd = {h: (((fwd(ts + 30, h) / px0_delay - 1) * 1e4) if (px0_delay and fwd(ts + 30, h)) else None) for h in (5, 15, 30)}
                all_events.append({"sid": sid, "day": day, "kind": kind, "ts": ts,
                                    **{f"ex{h}": ex[h] for h in (5, 15, 30)},
                                    **{f"exd{h}": exd[h] for h in (5, 15, 30)}})
            for ts, kind in breakout:
                px0 = px_at(ts)
                px0_delay = px_at(ts + 30)
                if not px0:
                    continue
                ex = {h: (((fwd(ts, h) / px0 - 1) * 1e4) if fwd(ts, h) else None) for h in (5, 15, 30)}
                exd = {h: (((fwd(ts + 30, h) / px0_delay - 1) * 1e4) if (px0_delay and fwd(ts + 30, h)) else None) for h in (5, 15, 30)}
                all_events.append({"sid": sid, "day": day, "kind": kind, "ts": ts,
                                    **{f"ex{h}": ex[h] for h in (5, 15, 30)},
                                    **{f"exd{h}": exd[h] for h in (5, 15, 30)}})

    n_days = len({e["day"] for e in all_events})
    print(f"\n事件總數 {len(all_events)},涉及 {n_days} 個交易日(⚠ 日聚類 cluster 數上限={n_days},"
          f"遠低於這個問題過去設的≥60日門檻;使用者已明確指示先上線,權重將重度縮水並揭露此限制)\n")

    fitted = {}
    for kind in ("backing_bull", "backing_bear", "breakout_bull", "breakout_bear"):
        sub = [e for e in all_events if e["kind"] == kind]
        print(f"==== {kind}(n={len(sub)}) ====")
        for h in (5, 15, 30):
            vals = [(e[f"ex{h}"], e["day"]) for e in sub if e[f"ex{h}"] is not None]
            if len(vals) < 15:
                print(f"  持{h:2d}分:n<15({len(vals)}),樣本不足,不擬合"); continue
            arr = np.array([v for v, _ in vals]); dates = np.array([d for _, d in vals])
            mu, t = cl_t(arr, dates)
            win = float((arr > 0).mean() * 100)
            print(f"  持{h:2d}分:n={len(vals):4d} 均{mu:+6.1f}bps(t{t:+4.2f},cluster={len(set(dates))}日) 勝率{win:4.1f}%")
            if h == 15:   # 用15分鐘當代表持有期擬合權重(貼近訊號本身的時間尺度)
                fitted[kind] = {"mu": mu, "t": t, "n": len(vals)}
        print("  -- 延遲30秒進場對照(exd,見 fade-edge-refuted-by-delay-test 教訓:延續與反轉常是同一量測偏誤兩面) --")
        for h in (5, 15, 30):
            vals = [(e[f"exd{h}"], e["day"]) for e in sub if e.get(f"exd{h}") is not None]
            if len(vals) < 15:
                print(f"    延遲持{h:2d}分:n<15({len(vals)}),樣本不足"); continue
            arr = np.array([v for v, _ in vals]); dates = np.array([d for _, d in vals])
            mu, t = cl_t(arr, dates)
            print(f"    延遲持{h:2d}分:n={len(vals):4d} 均{mu:+6.1f}bps(t{t:+4.2f})")

        # 安慰劑
        if len(sub) >= 15:
            rng = np.random.default_rng(hash(kind) % (2**31))
            pool = [e["ex15"] for e in all_events if e["ex15"] is not None]
            if len(pool) >= 15:
                placebo = [np.mean(rng.choice(pool, size=min(len(sub), len(pool)), replace=False)) for _ in range(5)]
                real15 = np.mean([e["ex15"] for e in sub if e["ex15"] is not None])
                print(f"  安慰劑對照(15分):真實均{real15:+.1f} vs 隨機5組均{np.mean(placebo):+.1f}"
                      f"(範圍{min(placebo):+.1f}~{max(placebo):+.1f})")
        # 集中度
        by_sid = defaultdict(list)
        for e in sub:
            if e["ex15"] is not None:
                by_sid[e["sid"]].append(e["ex15"])
        if by_sid:
            tot = sum(sum(v) for v in by_sid.values())
            top5 = sorted((sum(v) for v in by_sid.values()), key=abs, reverse=True)[:5]
            print(f"  集中度:前5檔貢獻佔比 {sum(top5)/tot*100:.0f}%(共{len(by_sid)}檔)" if tot else "  集中度:分母為0,略過")
        print()

    print("==== 建議的分數權重(15分鐘均值 × 0.5 縮水,|t|<1.0 歸零——樣本小,門檻比照既有慣例放寬到1.0而非2.0) ====")
    for kind, f in fitted.items():
        w = round(f["mu"] * 0.5, 1) if abs(f["t"]) >= 1.0 else 0.0
        print(f"  {kind}: 原始{f['mu']:+.1f}bps(t{f['t']:+.2f}) → 縮水後權重 {w:+.1f}bps" + ("" if w else "(未過門檻,權重=0,先不進分數)"))


if __name__ == "__main__":
    main()
