#!/usr/bin/env python3
"""「關鍵一條線」(youtube 分析師楊育華,2026-09-25 jack 交辦)規則重建與合理性研究。唯讀 DB(?mode=ro)。

規則(依節目逐字稿精確化):
  某日 K 棒 i 若同時滿足三條件,即為「觸發棒」,關鍵線 = 觸發棒的最低點(含影線):
    (a) 紅K:收盤 > 開盤
    (b) 收盤漲幅 > 前一日收盤 +4%
    (c) 突破前高:收盤 > 前 N 個交易日(不含當日)的最高價(或最高收盤,兩種都測)
  同一檔股票取「最近一次」觸發棒的最低點當線(線會隨新觸發棒往上移,舊觸發棒作廢)。
  回顧期(scan back)有限,超過都找不到觸發棒 → 該股「沒有這條線」。

本研究要回答:
  1. 前高回顧期 N(20/60/120 日)、突破用「最高價」還是「最高收盤」,對觸發率與線的穩定性影響?
  2. 目前 42 檔(或全市場)裡「找不到線」的比例,是否接近她口頭估計的 30~50%?
  3. 跟節目裡點名的具體股票(南亞/景碩/南電/欣興/信昌電/禾伸堂/華新科/國巨在 9/3 附近的低點)是否量級相符(非精確驗證,只是合理性檢查)?
  4. 「拉回到線附近才買」這個規則,歷史上(不限本輪行情)拉回到線 ±X% 內買、抱到下一個訊號或固定天數,報酬是否為正(粗略統計,非嚴謹回測,只當合理性檢查)。

用法:PYTHONPATH=src .venv/bin/python scripts/research/key_line_research.py
"""
from __future__ import annotations
import sqlite3, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src")
import stock_db  # noqa: E402

UNIVERSE_42 = None  # 用 _live_calib.json 抓,見 main()


def load_bars(sids, start="2024-06-01", end="2026-09-25"):
    con = sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
    q = ("SELECT stock_id sid, trade_date date, open, high, low, close FROM stock_daily_bars "
         "WHERE stock_id IN ({}) AND trade_date BETWEEN ? AND ? AND close>0 AND open>0 "
         "ORDER BY stock_id, trade_date").format(",".join("?" * len(sids)))
    b = pd.read_sql(q, con, params=[*sids, start, end])
    con.close()
    return b.drop_duplicates(["sid", "date"], keep="last").reset_index(drop=True)


def find_key_line(b: pd.DataFrame, lookback: int, break_col: str, scan_days: int) -> pd.DataFrame:
    """對每個 sid,回傳最新一筆觸發棒(line_date/line_price/asof)。break_col='high' 或 'close'。"""
    out = []
    for sid, g in b.groupby("sid"):
        g = g.sort_values("date").reset_index(drop=True)
        n = len(g)
        if n < lookback + 5:
            out.append({"sid": sid, "line_date": None, "line_price": None, "asof": g["date"].iloc[-1] if n else None})
            continue
        prior_hi = g[break_col].rolling(lookback, min_periods=lookback).max().shift(1)
        red = g["close"] > g["open"]
        pct = g["close"] / g["close"].shift(1) - 1
        brk = g["close"] > prior_hi
        trig = red & (pct > 0.04) & brk
        # 只在最近 scan_days 個交易日內找「最新一次」觸發
        window = g.iloc[-scan_days:] if n > scan_days else g
        tw = trig.iloc[-scan_days:] if n > scan_days else trig
        idx = window.index[tw.reindex(window.index, fill_value=False)]
        if len(idx) == 0:
            out.append({"sid": sid, "line_date": None, "line_price": None, "asof": g["date"].iloc[-1]})
        else:
            i = idx[-1]
            out.append({"sid": sid, "line_date": g.loc[i, "date"], "line_price": g.loc[i, "low"], "asof": g["date"].iloc[-1],
                        "n_trig_in_window": int(tw.sum())})
    return pd.DataFrame(out)


def main():
    import json
    cal = json.load(open(f"{stock_db.DATA_DIR}/cache/pit_universe_tick/_live_calib.json"))
    sids42 = [r["sid"] for r in cal["universe"]]
    b = load_bars(sids42)
    latest = b.groupby("sid")["close"].last()
    print(f"42 檔,日線資料 {b['date'].min()}~{b['date'].max()}")

    print("\n==== 1. 回顧期 N × 突破用高點/收盤 → 觸發率與「無線」比例(scan_days=250,約1年) ====")
    for N in (20, 60, 120):
        for col in ("high", "close"):
            r = find_key_line(b, N, col, 250)
            none_pct = r["line_price"].isna().mean() * 100
            dist = ((latest.reindex(r.set_index("sid")["line_price"].dropna().index if False else None))) if False else None
            print(f"  N={N:3d} 突破={col:5s}: 無線比例 {none_pct:4.1f}%  有線 {r['line_price'].notna().sum()}/{len(r)}")

    print("\n==== 2. 採用 N=60、突破用『收盤』(較穩健,不受單日長上影誤觸發)的完整結果 ====")
    r = find_key_line(b, 60, "close", 250)
    r = r.merge(latest.rename("current_px"), on="sid")
    r["dist_pct"] = (r["current_px"] / r["line_price"] - 1) * 100
    r["name"] = r["sid"].map({x["sid"]: x["name"] for x in cal["universe"]})
    none_pct = r["line_price"].isna().mean() * 100
    print(f"無線比例 {none_pct:.1f}%(節目口頭估計 30~50%)")
    print(r[["sid", "name", "line_date", "line_price", "current_px", "dist_pct"]].sort_values("dist_pct", ascending=False).to_string(index=False, float_format=lambda v: f"{v:,.1f}"))

    print("\n==== 3. 合理性檢查:節目點名 9/3 附近低點 vs 我們算出的線 ====")
    named = {"1303": ("南亞", 221.0), "3189": ("景碩", 775.0), "8046": ("南電", 1010.0), "3037": ("欣興", 873.0),
             "6173": ("信昌電", 237.5), "3527": ("禾伸堂", 638.0), "2492": ("華新科", 271.5), "2327": ("國巨", 511.0)}
    for sid, (nm, lo930) in named.items():
        row = r[r["sid"] == sid]
        if row.empty:
            print(f"  {sid} {nm}: 不在 42 檔宇宙或無資料"); continue
        lp = row["line_price"].iloc[0]; ld = row["line_date"].iloc[0]
        print(f"  {sid} {nm}: 節目稱 9/3 低點≈{lo930}  我方算出的線={lp}@{ld}  差={((lp/lo930-1)*100 if lp else float('nan')):+.1f}%" if lp else f"  {sid} {nm}: 我方算不出線(節目稱低點≈{lo930})")

    print("\n==== 4. 粗略回測:拉回到線 ±2% 內買、持有 10/20 日,報酬分布(不限本輪,全樣本 sid×date 逐日掃) ====")
    # 對每個 sid,重算「當下有效線」逐日序列(不只看最後一天),測「首次進入 [line, line*1.02] 區間」後續報酬
    rows = []
    for sid, g in b.groupby("sid"):
        g = g.sort_values("date").reset_index(drop=True); n = len(g)
        if n < 130: continue
        prior_hi = g["close"].rolling(60, min_periods=60).max().shift(1)
        red = g["close"] > g["open"]; pct = g["close"] / g["close"].shift(1) - 1; brk = g["close"] > prior_hi
        trig = (red & (pct > 0.04) & brk).values
        line = np.full(n, np.nan)
        cur = np.nan
        for i in range(n):
            if trig[i]:
                cur = g["low"].iloc[i]
            line[i] = cur
        g["line"] = line
        g["in_zone"] = (g["close"] >= g["line"]) & (g["close"] <= g["line"] * 1.02) & g["line"].notna()
        first_entry = g["in_zone"] & ~(g["in_zone"].shift(1).fillna(False) & (g["line"] == g["line"].shift(1)))
        for i in g.index[first_entry]:
            for h in (10, 20):
                if i + h < n:
                    rows.append({"sid": sid, "date": g.loc[i, "date"], "hz": h, "ret": (g["close"].iloc[i + h] / g["close"].iloc[i] - 1) * 100})
    rr = pd.DataFrame(rows)
    for h in (10, 20):
        x = rr[rr["hz"] == h]
        if len(x) < 10: continue
        mu = x["ret"].mean(); sd = x["ret"].std(); t = mu / (sd / np.sqrt(len(x)))
        print(f"  持有{h}日: n={len(x)} 均報酬{mu:+.2f}% (t{t:+.2f}) 勝率{(x['ret']>0).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
