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
  - 800大戶持股% / 10散戶持股% / Beta:2026-09-27 DB清理路線圖 Step 3 拆表後**不再寫進
    本表**——兩者都不是「日頻事實」(前者是週頻集保快照、後者是stock_beta非時間序列的單一
    最新值,存成本表欄位只會製造「哪一列是新的」的過期問題),改成 biglot_dashboard.py 渲染
    時直接呼叫本檔案的 _load_holder_tiers()/_load_beta() 即時查 stock_holding_dispersion_weekly/
    stock_beta,不落地。這兩支函式仍留在本檔案(邏輯正確、biglot_dashboard.py 直接 import
    重用,不重複造一份)。已用台積電驗證過聚合邏輯本身正確:800大戶持股%=85.46,與XQ截圖
    85.53幾乎吻合(週別抓取差1~2天的正常誤差)。

2026-09-27 追加(jack 交辦「稽核還有哪些已排程但沒接進儀表板的資料」後同意放進個別頁面):
  - 當沖比例% = stock_daytrade_daily.daytrade_volume ÷ 當日成交量(stock_daily_bars.volume)×100。
    ⚠ 不用該表自帶的 daytrade_ratio_pct/total_volume 欄位(實測常是NULL,對應
    backfill_stock_chip_extras.py 記載的舊版「整欄恆等99%」bug 修復前遺留),自己重算才可信。
  - 外資持股比例%(水位) = stock_shareholding_daily.foreign_remaining_ratio,PIT取≤當日最近一筆。
    跟既有「外資買賣超比%」是互補的兩件事:一個是流量(當天買賣多少)、一個是存量(現在持有多少)。
  - 鉅額交易(block_volume/block_amount/block_count) = stock_block_trade 逐日欄位。這是稀疏事件
    (多數股票多數日子沒有鉅額交易成交,對應欄位為NULL,不是資料缺失)。

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
from source_dedup import dedup_query  # noqa: E402

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
    """2026-09-27 DB清理Step2修正:原本用 MAX(foreign_net)/MAX(investment_trust_net)/
    MAX(dealer_self_net) 各自獨立取MAX,如果同一天兩個來源數字不同,會把不同來源的欄位
    拼成一列從未真實存在過的組合(dealer_self_net 官方/finmind定義本來就不同,見schema
    stock_institutional_daily 註記)。改用 source_dedup 先選好唯一一列來源,不逐欄各自MAX。"""
    sql = dedup_query("stock_institutional_daily", ("stock_id", "trade_date"), inner_where="WHERE stock_id=?")
    rows = conn.execute(
        f"SELECT trade_date, foreign_net, investment_trust_net, dealer_self_net FROM ({sql})",
        (sid,)).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _load_sbl(conn, sid: str) -> dict[str, float]:
    sql = dedup_query("stock_short_interest_daily", ("stock_id", "trade_date"), inner_where="WHERE stock_id=?")
    rows = conn.execute(f"SELECT trade_date, sbl_balance FROM ({sql})", (sid,)).fetchall()
    return {r[0]: r[1] for r in rows if r[1] is not None}


def _load_holder_tiers(conn, sid: str):
    """回傳 (weeks_sorted, big800_by_week, retail10_by_week)。優先 tdcc,缺值退回 finmind。

    2026-09-27 DB清理Step3拆表後,這支函式不再被本檔案的 compute_for_stock() 呼叫
    (800大戶/10散戶不再寫進 stock_xq_style_daily,改由 biglot_dashboard.py 在渲染時
    直接 `from compute_xq_style_metrics import _load_holder_tiers` 即時查詢最新一兩週)。
    留在這裡而不搬移,是因為這是本檔案原本就有的正確邏輯(tdcc優先序),不重複造一份。"""
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


def _load_daytrade(conn, sid: str) -> dict[str, float]:
    """回傳 trade_date -> daytrade_volume(股)。不用該表自帶的 daytrade_ratio_pct/total_volume
    (常是NULL,見 backfill_stock_chip_extras.py 對舊版「整欄恆等99%」bug 的說明),自己拿
    daytrade_volume 除以 stock_daily_bars 的當日成交量重算比例,才不會沿用壞掉的分母。"""
    sql = dedup_query("stock_daytrade_daily", ("stock_id", "trade_date"), inner_where="WHERE stock_id=?")
    rows = conn.execute(f"SELECT trade_date, daytrade_volume FROM ({sql})", (sid,)).fetchall()
    return {r[0]: r[1] for r in rows if r[1] is not None}


def _load_foreign_holding(conn, sid: str) -> dict[str, float]:
    sql = dedup_query("stock_shareholding_daily", ("stock_id", "trade_date"), inner_where="WHERE stock_id=?")
    rows = conn.execute(f"SELECT trade_date, foreign_remaining_ratio FROM ({sql})", (sid,)).fetchall()
    return {r[0]: r[1] for r in rows if r[1] is not None}


def _load_block_trade(conn, sid: str) -> dict[str, tuple]:
    """稀疏事件表(不是每天都有鉅額交易),回傳 trade_date -> (volume, amount, count)。"""
    sql = dedup_query("stock_block_trade", ("stock_id", "trade_date"), inner_where="WHERE stock_id=?")
    rows = conn.execute(
        f"SELECT trade_date, block_volume, block_amount, block_count FROM ({sql})", (sid,)).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _load_beta(conn, sid: str) -> tuple[float | None, str | None]:
    """stock_beta 非時間序列(每次 resync 覆蓋同一列,見 schema 註解),只取目前唯一一列。
    優先 yahoo_computed(目前唯一 source),缺值回 (None, None)。

    2026-09-27 DB清理Step3拆表後不再寫進 stock_xq_style_daily,改由 biglot_dashboard.py
    渲染時直接 import 呼叫這支函式即時查——理由跟 _load_holder_tiers 一樣:beta 本身
    不是時間序列,存成本表欄位只會製造「哪一列是新的」的過期問題,不如每次現查。"""
    row = conn.execute(
        "SELECT beta, as_of_date FROM stock_beta WHERE stock_id=? "
        "ORDER BY as_of_date DESC LIMIT 1", (sid,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


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
    daytrade = _load_daytrade(conn, sid)
    foreign_hold = _load_foreign_holding(conn, sid)
    foreign_hold_dates = sorted(foreign_hold)
    block = _load_block_trade(conn, sid)

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

        dtv = daytrade.get(d)
        daytrade_ratio = (dtv / vols[i] * 100) if (dtv is not None and vols[i]) else None

        fh_idx = bisect.bisect_right(foreign_hold_dates, d) - 1
        foreign_holding = foreign_hold[foreign_hold_dates[fh_idx]] if fh_idx >= 0 else None

        b_vol, b_amt, b_cnt = block.get(d, (None, None, None))

        out.append((
            sid, d, turnover, ret1w, sma20[i], ema20[i], ema_sma_diff,
            dif[i], dea[i], hist[i], hist_vol[i], conc, foreign_pct, trust_pct, dealer_pct,
            sbl_chg1, sbl_chg5, daytrade_ratio, foreign_holding, b_vol, b_amt, b_cnt,
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
                "(stock_id, trade_date, turnover_pct, ret_chg5d_pct, sma_20d, ema_20d, ema_sma_20d_diff, "
                " macd_dif, macd_dea, macd_hist, hist_vol_20d_pct, concentration_pct, "
                " foreign_net_pct, trust_net_pct, dealer_net_pct, sbl_sell_chg1d, sbl_sell_chg5d, "
                " daytrade_pct, foreign_holding_pct, block_volume, block_amount, block_count, "
                " source, synced_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            total += len(rows)
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(universe)} 檔,累計 {total} 列")
    conn.close()
    print(f"完成,共寫入 {total} 列")


if __name__ == "__main__":
    main()
