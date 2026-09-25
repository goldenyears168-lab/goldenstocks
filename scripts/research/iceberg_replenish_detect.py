#!/usr/bin/env python3
"""隱形大戶補單偵測(iceberg / hidden-liquidity replenishment,2026-09-25 jack 交辦:「現在就開工」)。

背景:上一輪查證欣興(3037)09-08「950有賣牆」是誤判(全天最大掛單僅5.83分鐘量,見
memory xinxing-950-wall-misread),但使用者接著問的假說——「機構是不是用被動單默默在某個
價位吃貨/守著,盤中委託簿補單可以看出來」——是有學術根據的獨立問題(iceberg order /
hidden liquidity detection),跟「牆」不是同一件事:牆是「掛單量夠大」,補單是「掛單被吃掉後
會不會很快補回同一個價位」,後者才是真正對應「隱形大戶守價位」的行為特徵。

⚠ 誠實揭露三點限制(先講清楚,不要包裝成已驗證訊號):
  1. 資料源是 TWSE MIS 現股五檔輪詢快照(~5秒一次,watchlist_books collector),**不是**
     交易所 order-by-order 的真實 L3 資料。真正的 iceberg order 偵測需要看到同一張委託單的
     ID 在成交後立刻重新掛出——那個層級的資料我們拿不到。這裡做的是用五檔快照重建出來的
     **近似**:某價位的量被吃掉(quantity 下降 ∧ 同時間成交量真的有增加)後,很快又補回同一
     價位。這只能當「候選線索」,不是嚴謹的訂單層級證據。
  2. 樣本只有 2026-09-05 起累積的 15 個交易日,上一輪研究(biglot-accumulation-distribution-
     unidentifiable memory)已經設定門檻要 ≥60 天才夠做統計檢定。使用者要求「現在就開工,
     連前瞻報酬也要」——這裡確實算了(見 forward_return_test),但 15 天日聚類樣本(cl_t 的
     cluster 數上限=15)遠低於過去設的門檻,t 值只能當「方向感」參考,**不構成 KEEP/DROP 判決**,
     不進分數、不上儀表板排序。跟欣興950同一種錯覺的風險仍在,已在每一行輸出前提醒。
  3. 只限這 42 檔儀表板宇宙(watchlist_books 收集器本身是 45 檔觀察名單,已在腳本內過濾)。

方法(僅限最優買一/賣一,避免五檔快照本身的雜訊、也最貼近「守住一個價位」的直覺):
  沿時間軸走訪同一檔股票同一天的快照。當「最優買一價」連續多筆快照維持同一個價位(一段
  "streak"),在這段 streak 期間:
    · 「被吃」事件 = 買一量比上一筆快照減少 ∧ 同時間累計成交量(v)真的有增加(排除純掛單
      取消,量至少要真的有交易發生過)。
    · 「補回」事件 = 被吃之後,買一量後來又回升到接近或超過被吃前的水準,且價位仍維持不變
      (沒有因為買方力竭而被打穿)。
    · 一個 streak 裡「被吃→補回」重複越多次,代表這個價位越像有人在默默守、一直補單——
      這才是使用者說的「隱形大戶守在一個價格吃貨」的操作特徵。賣一同理,守賣一=隱形壓力/
      默默出貨。
  過濾雜訊:量變動 < 5 張(watch_books 常見的快照抖動)不算「被吃」或「補回」。

用法:PYTHONPATH=src .venv/bin/python scripts/research/iceberg_replenish_detect.py
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
MIN_QTY_CHANGE = 5.0     # 張;低於此視為快照雜訊,不算被吃/補回
MIN_REFILL_CYCLES = 3    # 一個 streak 內至少要有幾次被吃→補回,才算候選


def load_universe():
    cal = json.load(open(DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"))
    return {r["sid"]: r["name"] for r in cal["universe"]}


def scan_side(rows, price_key, qty_key):
    """rows = 依時間排序的 (ts, t, price_arr, qty_arr, v) list。回傳這一天此側所有 streak 的偵測結果。

    2026-09-25 發現的資料坑:TWSE MIS 五檔快照偶爾在 bp[0]/ap[0] 回傳 0.0(價格)搭配異常巨量
    (上萬張),同時 bp[1]/ap[1] 才是真正的最優價——0 元不可能是真實委託價,這是 MIS 欄位語意
    的雜訊(可能是市價單彙總或其他非價格量欄位被誤映射到最優價位置),必須排除,否則會誤判成
    「隱形大戶掛0元巨量」。過濾:價格必須 > 0 才收。
    """
    streaks = []
    cur = None
    for ts, t, price_arr, qty_arr, v in rows:
        if not price_arr:
            if cur:
                streaks.append(cur); cur = None
            continue
        # 0 元雜訊防禦:取陣列中第一個「價格>0」的位置當最優價,而非死板取 index 0
        p0 = q0 = None
        for pp, qq in zip(price_arr, qty_arr):
            if pp is not None and qq is not None and pp > 0:
                p0, q0 = pp, qq
                break
        if p0 is None:
            continue
        if cur is None or cur["price"] != p0:
            if cur and cur["hits"] >= MIN_REFILL_CYCLES:
                streaks.append(cur)
            cur = {"price": p0, "start_t": t, "end_t": t, "start_ts": ts, "end_ts": ts,
                   "last_qty": q0, "peak_qty": q0,
                   "hits": 0, "refills": 0, "was_hit": False, "vol_absorbed": 0.0, "last_v": v}
            continue
        cur["end_t"] = t
        cur["end_ts"] = ts
        dq = q0 - cur["last_qty"]
        dv = (v - cur["last_v"]) if (v is not None and cur["last_v"] is not None) else 0.0
        if dq <= -MIN_QTY_CHANGE and dv > 0:
            cur["hits"] += 1
            cur["was_hit"] = True
            cur["vol_absorbed"] += dv
        elif dq >= MIN_QTY_CHANGE and cur["was_hit"]:
            cur["refills"] += 1
            cur["was_hit"] = False
            cur["peak_qty"] = max(cur["peak_qty"], q0)
        cur["last_qty"] = q0
        if v is not None:
            cur["last_v"] = v
    if cur and cur["hits"] >= MIN_REFILL_CYCLES:
        streaks.append(cur)
    return [s for s in streaks if s["refills"] >= 2]   # 至少補回2次才算候選(被吃1次沒補回不算守價)


def mid_series(rows):
    """(ts, mid) 時間序列,mid=(買一+賣一)/2,兩邊都要有效價格才取;供事件後前瞻報酬查找用。
    rows = (ts, t, bp, bq, v, ap, aq) — 與 per_sid.append 的欄位順序一致。"""
    out = []
    for ts, _t, bp, bq, _v, ap, aq in rows:
        b = next((p for p, q in zip(bp, bq) if p and q and p > 0), None)
        a = next((p for p, q in zip(ap, aq) if p and q and p > 0), None)
        if b and a:
            out.append((ts, (b + a) / 2))
    return out


def fwd_price(series, from_ts, horizon_sec):
    """series 已按 ts 排序;回傳第一筆 ts >= from_ts+horizon_sec 的 mid 價,找不到回 None。"""
    target = from_ts + horizon_sec
    for ts, mid in series:
        if ts >= target:
            return mid
    return None


def load_next_close(uni_sids, days_ymd):
    """回傳 {(sid, 'YYYY-MM-DD'): (該日收盤, 下一交易日收盤)},供隔夜前瞻報酬用。"""
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    out = {}
    for sid in uni_sids:
        rows = conn.execute(
            "SELECT trade_date, MAX(close) FROM stock_daily_bars WHERE stock_id=? AND trade_date>=? "
            "GROUP BY trade_date ORDER BY trade_date", (sid, min(days_ymd))).fetchall()
        rows = [r for r in rows if r[1]]
        for i in range(len(rows) - 1):
            out[(sid, rows[i][0])] = (rows[i][1], rows[i + 1][1])
    conn.close()
    return out


def main():
    uni = load_universe()
    files = sorted(glob.glob(str(BOOKS_DIR / "watchlist_books_*.jsonl")))
    print(f"掃描 {len(files)} 個交易日(watchlist_books),限定 42 檔宇宙")
    all_candidates = []
    n_quotes = defaultdict(int)   # 正規化分母:該股 15 日內總有效報價快照數(流動性高的股票報價更新本來就多,不除會虛高排名)
    days_ymd = []
    for fp in files:
        day = Path(fp).stem.replace("watchlist_books_", "")   # 檔名本身已是 YYYY-MM-DD,不需再轉換
        day_ymd = day
        days_ymd.append(day_ymd)
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
            seen_ts = set(); dedup = []
            for r in rows:
                if r[0] in seen_ts:
                    continue
                seen_ts.add(r[0]); dedup.append(r)
            n_quotes[sid] += len(dedup)
            bid_rows = [(ts, t, bp, bq, v) for ts, t, bp, bq, v, _ap, _aq in dedup]
            ask_rows = [(ts, t, ap, aq, v) for ts, t, _bp, _bq, v, ap, aq in dedup]
            mids = mid_series(dedup)
            for side, srows in (("買一(守價吃貨)", bid_rows), ("賣一(守價出貨)", ask_rows)):
                for s in scan_side(srows, 0, 0):
                    px0 = fwd_price(mids, s["end_ts"], 0)
                    fwd = {h: fwd_price(mids, s["end_ts"], h * 60) for h in (5, 15, 30)}
                    ex = {h: ((fwd[h] / px0 - 1) * 1e4) if (px0 and fwd[h]) else None for h in (5, 15, 30)}
                    all_candidates.append({
                        "day": day, "day_ymd": day_ymd, "sid": sid, "name": uni[sid], "side": side,
                        "price": s["price"], "hits": s["hits"], "refills": s["refills"],
                        "start_t": s["start_t"], "end_t": s["end_t"], "end_ts": s["end_ts"],
                        "vol_absorbed_lots": round(s["vol_absorbed"], 1),
                        "ex5": ex[5], "ex15": ex[15], "ex30": ex[30]})

    all_candidates.sort(key=lambda c: (c["refills"], c["hits"]), reverse=True)
    print(f"\n找到 {len(all_candidates)} 個候選「守價位」streak(被吃≥{MIN_REFILL_CYCLES}次∧補回≥2次)\n")
    print(f"{'日期':10s} {'代號':5s} {'名稱':6s} {'方向':14s} {'價位':>8s} {'被吃':>4s} {'補回':>4s} {'時段':17s} {'吸收量(張,近似)':>14s}")
    for c in all_candidates[:40]:
        print(f"{c['day']:10s} {c['sid']:5s} {c['name']:6s} {c['side']:14s} {c['price']:8.1f} "
              f"{c['hits']:4d} {c['refills']:4d} {c['start_t']}~{c['end_t']:8s} {c['vol_absorbed_lots']:14.1f}")

    by_sid = defaultdict(int)
    for c in all_candidates:
        by_sid[c["sid"]] += 1
    print("\n==== 按股票彙總·原始次數(未校正流動性,流動性高的股票報價更新本來就多,排名會虛高) ====")
    for sid, n in sorted(by_sid.items(), key=lambda x: -x[1])[:10]:
        print(f"  {sid} {uni[sid]}: {n} 次(報價快照數 {n_quotes.get(sid, 0)})")

    print("\n==== 按股票彙總·校正流動性後(每千筆報價快照出現候選次數,較公平的『誰更常守價』排名) ====")
    rate = {sid: n / n_quotes[sid] * 1000 for sid, n in by_sid.items() if n_quotes.get(sid)}
    for sid, r in sorted(rate.items(), key=lambda x: -x[1])[:15]:
        print(f"  {sid} {uni[sid]}: {r:.2f}‰(原始 {by_sid[sid]} 次 / 報價快照 {n_quotes[sid]})")

    n_days = len({c["day"] for c in all_candidates})
    print(f"\n==== 盤中前瞻報酬:守價位事件結束後(5/15/30分鐘,mid price)⚠僅{n_days}個交易日,日聚類 cluster 數上限={n_days},"
          f"遠低於過去門檻(≥60日),t值僅供方向參考,不構成KEEP/DROP判決 ====")
    for side_lbl in ("買一(守價吃貨)", "賣一(守價出貨)"):
        sub = [c for c in all_candidates if c["side"] == side_lbl]
        print(f"  -- {side_lbl}(n={len(sub)}) --")
        for h in (5, 15, 30):
            vals = [(c[f"ex{h}"], c["day_ymd"]) for c in sub if c[f"ex{h}"] is not None]
            if len(vals) < 10:
                print(f"    持{h:2d}分:n<10({len(vals)}),樣本不足"); continue
            arr = [v for v, _ in vals]; dates = [d for _, d in vals]
            mu, t = cl_t(np.array(arr), np.array(dates))
            win = sum(1 for x in arr if x > 0) / len(arr) * 100
            print(f"    持{h:2d}分:n={len(vals):4d} 均{mu:+6.1f}bps(日聚類t{t:+4.2f},cluster={len(set(dates))}日) 勝率{win:4.1f}%")

    print(f"\n==== 隔夜前瞻報酬:當日『被吃最多次』的守價事件 → 次日官方收盤 vs 當日收盤 ⚠同樣僅{n_days}日,不構成判決 ====")
    next_close = load_next_close(list(uni), days_ymd)
    best_per_day_side = {}
    for c in all_candidates:
        key = (c["sid"], c["day_ymd"], c["side"])
        if key not in best_per_day_side or c["hits"] > best_per_day_side[key]["hits"]:
            best_per_day_side[key] = c
    for side_lbl in ("買一(守價吃貨)", "賣一(守價出貨)"):
        rows_on = []
        for (sid, day_ymd, side), c in best_per_day_side.items():
            if side != side_lbl:
                continue
            cc = next_close.get((sid, day_ymd))
            if not cc or not cc[0] or not cc[1]:
                continue
            ex = (cc[1] / cc[0] - 1) * 1e4
            rows_on.append((ex, day_ymd))
        print(f"  -- {side_lbl}(day-side 事件數 n={len(rows_on)}) --")
        if len(rows_on) < 10:
            print("    n<10,樣本不足,略過"); continue
        arr = [v for v, _ in rows_on]; dates = [d for _, d in rows_on]
        mu, t = cl_t(np.array(arr), np.array(dates))
        win = sum(1 for x in arr if x > 0) / len(arr) * 100
        print(f"    隔夜:n={len(rows_on):4d} 均{mu:+6.1f}bps(日聚類t{t:+4.2f},cluster={len(set(dates))}日) 勝率{win:4.1f}%")


if __name__ == "__main__":
    main()
