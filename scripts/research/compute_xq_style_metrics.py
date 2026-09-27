#!/usr/bin/env python3
"""XQ全球贏家風格欄位補算(2026-09-27 jack 交辦:「剩下的請把資料補充到sqlite」)。

背景:jack 貼了兩張 XQ全球贏家 的欄位截圖問「FinMind有沒有現成、能不能自己算」,逐欄比對後
(見對話紀錄,已用合晶本益比523.91≈524、台積電市值精確到億元、合晶融資餘額增減−3866 三個數字
驗證過方法正確)大部分欄位本專案 DB 已有原始資料,只是還沒算成同一組欄位寫進去。本腳本把
「查得到公式、資料庫已有原始資料」的欄位算出來寫進新表 stock_xq_style_daily,供
biglot_dashboard.py 個股詳情頁使用。範圍=biglot 監控宇宙(pe_peer_group_research.load_subcat()
的 keys),不做全市場——目前只有這個 dashboard 會用這組欄位。

⚠ 兩個明確排除(不在本腳本算):
  - 員工平均營業額:需要員工人數,FinMind查無對應dataset,依 jack 指示暫時沿用 XQ 截圖數字,
    見 scripts/research/xq_manual_metrics.py(純人工維護,非本腳本產生)。
  - 多空淨力%:極可能是XQ(嘉實資訊)專有指標,公式未公開,不強行逼近。

逐欄公式與來源:
  - 換手率% = 當日成交量(股)÷已發行股數×100(stock_daily_bars.volume / shares_outstanding)
  - 一週% = close(t)/close(t-5)−1×100,按實際交易日、非日曆日
  - SMA20/EMA20/EMA-SMA20 = 收盤價20日簡單/指數移動平均,兩者之差(EMA-SMA(20日)欄)
  - MACD(12,26,9) = DIF(EMA12−EMA26) / DEA(DIF的9日EMA) / HIST=(DIF−DEA)×2(台股慣例乘2倍)
  - 歷史波動率% = 20日日報酬標準差×sqrt(252)×100,年化(樣本標準差,不含跳空调整)
  - 集中度% = 2026-09-27 jack 給的精確公式:「當日的主力買賣超張數 / 當日的成交量 × 100」
    ——「主力」取三大法人合計(stock_institutional_daily的
    foreign_net+investment_trust_net+dealer_self_net),分子分母皆為股數,比例不受股/張換算
    影響,直接相除即可,不需乘1000或除1000。
  - 外資/投信/自營商買賣超比% = 各自net÷當日成交量×100
    ⚠ 只有「集中度%」的公式是 jack 親自確認過的,這三欄是依同一慣例類推,未逐一跟XQ截圖比對過
    分母是否相同(也可能是用均量或股本當分母),先上,若之後發現對不上 XQ 數字要重看。
  - 借券賣出餘額增減(1日/5日) = stock_short_interest_daily.sbl_balance 對前1/5個交易日的差
    ⚠ 用 sbl_balance(TWT93U,真正的借券賣出/short interest)而非 stock_lending_daily
    .lending_balance(TWT72U,只約半數是真放空)——見記憶 twse-sbl-balance-vs-short-interest,
    這是本專案自己踩過的坑,弄混會把「借券餘額」誤植成「借券賣出餘額」。
  - 800大戶持股% / 10散戶持股% = stock_holding_dispersion_weekly(TWSE集保股權分散表,
    stock_id 各級距 percent)彙總:800大戶=level_lo>=800001各級加總,10張以下散戶=
    level_lo<=10000各級加總。取「≤該交易日最近一週」的官方快照(PIT,不偷看未來週);
    比%(週) = 與再前一週同一彙總值的差。兩個來源(tdcc/finmind)理論同值,優先用tdcc
    (集保結算所直接來源),缺值才退回finmind。
    已用台積電驗證:算出800大戶持股%=85.46,與XQ截圖85.53幾乎吻合(週別抓取差1~2天的正常誤差)。

用法:PYTHONPATH=src .venv/bin/python scripts/research/compute_xq_style_metrics.py
"""
from __future__ import annotations

import bisect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).parent))
import stock_db  # noqa: E402
from pe_peer_group_research import load_subcat  # noqa: E402

TZ = timezone(timedelta(hours=8))
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
VOL_WINDOW = 20


def _ema_series(vals: list[float | None], span: int) -> list[float | None]:
    k = 2.0 / (span + 1)
    out: list[float | None] = [None] * len(vals)
    e = None
    for i, v in enumerate(vals):
        if v is None:
            continue
        e = v if e is None else v * k + e * (1 - k)
        out[i] = e
    return out


def _load_bars(conn, sid: str):
    rows = conn.execute(
        "SELECT trade_date, MAX(close) c, MAX(volume) v, MAX(shares_outstanding) so "
        "FROM stock_daily_bars WHERE stock_id=? GROUP BY trade_date ORDER BY trade_date",
        (sid,)).fetchall()
    rows = [r for r in rows if r[1]]
    return [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows]


def _load_institutional(conn, sid: str) -> dict[str, tuple]:
    rows = conn.execute(
        "SELECT trade_date, MAX(foreign_net) f, MAX(investment_trust_net) t, MAX(dealer_self_net) d "
        "FROM stock_institutional_daily WHERE stock_id=? GROUP BY trade_date", (sid,)).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _load_sbl(conn, sid: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT trade_date, MAX(sbl_balance) FROM stock_short_interest_daily "
        "WHERE stock_id=? GROUP BY trade_date", (sid,)).fetchall()
    return {r[0]: r[1] for r in rows if r[1] is not None}


def _load_holder_tiers(conn, sid: str):
    """回傳 (weeks_sorted, big800_by_week, retail10_by_week)。優先 tdcc,缺值退回 finmind。"""
    rows = conn.execute(
        "SELECT as_of_date, level_lo, percent, source FROM stock_holding_dispersion_weekly "
        "WHERE stock_id=? AND level_lo IS NOT NULL ORDER BY as_of_date", (sid,)).fetchall()
    by_week: dict[str, dict[int, float]] = {}
    src_used: dict[str, dict[int, str]] = {}
    for asof, lo, pct, src in rows:
        if pct is None:
            continue
        cur_src = src_used.setdefault(asof, {}).get(lo)
        if cur_src is None or (cur_src != "tdcc" and src == "tdcc"):
            by_week.setdefault(asof, {})[lo] = pct
            src_used[asof][lo] = src
    weeks_sorted = sorted(by_week)
    big800: dict[str, float] = {}
    retail10: dict[str, float] = {}
    for wk in weeks_sorted:
        tiers = by_week[wk]
        big800[wk] = sum(v for lo, v in tiers.items() if lo >= 800001)
        retail10[wk] = sum(v for lo, v in tiers.items() if lo <= 10000)
    return weeks_sorted, big800, retail10


def compute_for_stock(conn, sid: str, now_iso: str) -> list[tuple]:
    dates, closes, vols, shares = _load_bars(conn, sid)
    n = len(dates)
    if n < 30:
        return []
    sma20: list[float | None] = [None] * n
    for i in range(19, n):
        window = closes[i - 19:i + 1]
        sma20[i] = sum(window) / 20
    ema20 = _ema_series(closes, 20)
    ema_fast = _ema_series(closes, MACD_FAST)
    ema_slow = _ema_series(closes, MACD_SLOW)
    dif = [(a - b) if (a is not None and b is not None) else None for a, b in zip(ema_fast, ema_slow)]
    dea = _ema_series(dif, MACD_SIGNAL)
    hist = [(2 * (d - s)) if (d is not None and s is not None) else None for d, s in zip(dif, dea)]

    rets: list[float | None] = [None] * n
    for i in range(1, n):
        if closes[i - 1]:
            rets[i] = closes[i] / closes[i - 1] - 1
    hist_vol: list[float | None] = [None] * n
    for i in range(VOL_WINDOW, n):
        window = [r for r in rets[i - VOL_WINDOW + 1:i + 1] if r is not None]
        if len(window) >= 15:
            m = sum(window) / len(window)
            var = sum((x - m) ** 2 for x in window) / (len(window) - 1)
            hist_vol[i] = (var ** 0.5) * (252 ** 0.5) * 100

    inst = _load_institutional(conn, sid)
    sbl = _load_sbl(conn, sid)
    weeks_sorted, big800_wk, retail10_wk = _load_holder_tiers(conn, sid)

    out = []
    for i in range(n):
        d = dates[i]
        turnover = (vols[i] / shares[i] * 100) if shares[i] else None
        ret1w = ((closes[i] / closes[i - 5] - 1) * 100) if (i >= 5 and closes[i - 5]) else None
        ema_sma_diff = (ema20[i] - sma20[i]) if (ema20[i] is not None and sma20[i] is not None) else None

        f, t, dl = inst.get(d, (None, None, None))
        net_total = None
        if f is not None or t is not None or dl is not None:
            net_total = (f or 0) + (t or 0) + (dl or 0)
        conc = (net_total / vols[i] * 100) if (net_total is not None and vols[i]) else None
        foreign_pct = (f / vols[i] * 100) if (f is not None and vols[i]) else None
        trust_pct = (t / vols[i] * 100) if (t is not None and vols[i]) else None
        dealer_pct = (dl / vols[i] * 100) if (dl is not None and vols[i]) else None

        cur_sbl = sbl.get(d)
        prev1_sbl = sbl.get(dates[i - 1]) if i >= 1 else None
        prev5_sbl = sbl.get(dates[i - 5]) if i >= 5 else None
        sbl_chg1 = (cur_sbl - prev1_sbl) if (cur_sbl is not None and prev1_sbl is not None) else None
        sbl_chg5 = (cur_sbl - prev5_sbl) if (cur_sbl is not None and prev5_sbl is not None) else None

        big800 = big800_chg_w = retail10 = retail10_chg_w = None
        holder_wk = None
        if weeks_sorted:
            wj = bisect.bisect_right(weeks_sorted, d) - 1
            if wj >= 0:
                holder_wk = weeks_sorted[wj]
                big800 = big800_wk.get(holder_wk)
                retail10 = retail10_wk.get(holder_wk)
                if wj >= 1:
                    pwk = weeks_sorted[wj - 1]
                    if big800 is not None and big800_wk.get(pwk) is not None:
                        big800_chg_w = big800 - big800_wk[pwk]
                    if retail10 is not None and retail10_wk.get(pwk) is not None:
                        retail10_chg_w = retail10 - retail10_wk[pwk]

        out.append((
            sid, d, turnover, ret1w, sma20[i], ema20[i], ema_sma_diff,
            dif[i], dea[i], hist[i], hist_vol[i], conc, foreign_pct, trust_pct, dealer_pct,
            sbl_chg1, sbl_chg5, big800, big800_chg_w, retail10, retail10_chg_w, holder_wk,
            "computed", now_iso,
        ))
    return out


def main():
    universe = sorted(load_subcat())
    print(f"監控宇宙 {len(universe)} 檔")
    conn = stock_db.connect()
    now_iso = datetime.now(TZ).isoformat()
    total = 0
    for i, sid in enumerate(universe):
        rows = compute_for_stock(conn, sid, now_iso)
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO stock_xq_style_daily "
                "(stock_id, trade_date, turnover_pct, ret_1w_pct, sma20, ema20, ema_sma20_diff, "
                " macd_dif, macd_dea, macd_hist, hist_vol20_pct, concentration_pct, "
                " foreign_pct, trust_pct, dealer_pct, sbl_sell_chg_1d, sbl_sell_chg_5d, "
                " big800_holder_pct, big800_holder_pct_chg_w, retail10_holder_pct, "
                " retail10_holder_pct_chg_w, holder_asof_week, source, synced_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            total += len(rows)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(universe)} 檔,累計 {total} 列")
    conn.close()
    print(f"完成,共寫入 {total} 列")


if __name__ == "__main__":
    main()
