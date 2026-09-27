"""共用來源優先序去重 helper(2026-09-27,biglot DB清理路線圖 Step 2)。

背景:biglot生態系裡至少5種寫法各自處理多來源表的去重(見 docs/研究記錄):
  - bare `MAX(close)`(biglot_dashboard.py 的 _load_daily_trend/_load_key_line/_load_atr_state/
    _load_prev_close_db,對 stock_daily_bars):同一天多個來源時,假設「同價,誰贏都一樣」,
    但這是數字碰運氣,不是明確的優先序規則。
  - `ROW_NUMBER() OVER (... ORDER BY CASE source ...)`(_load_vol_risk_flags 對 stock_margin_daily):
    這是目前唯一正確的做法,本檔案把它收斂成共用SSOT。
  - 完全沒去重(_load_vol_risk_flags 對 stock_lending_balance_daily)。
  - `_load_institutional`(compute_xq_style_metrics.py)對 stock_institutional_daily 用
    `MAX(foreign_net), MAX(investment_trust_net), MAX(dealer_self_net)` **各自獨立取MAX**——
    這是目前最嚴重的一個:如果兩個來源同一天數字不同,會把不同來源的欄位拼成同一列虛構出一筆
    從未真實存在過的組合(而且 dealer_self_net 兩個來源定義本來就不同,見 stock_institutional_daily
    schema 註記),不是「同價誰贏都一樣」可以蒙混過去的情況,必須先選一個來源的完整那一列。
  - `_load_holder_tiers`(compute_xq_style_metrics.py)對 stock_holding_dispersion_weekly:
    Python手刻 tdcc-優先-over-finmind 逐格合併,這個形狀跟其他表不同(要合併同一週不同級距的
    列,不是單純選一列),邏輯本身正確,不勉強套進本檔案的SQL primitive,維持原樣。

用法:
    from source_dedup import dedup_query
    sql = dedup_query("stock_margin_daily", ("stock_id", "trade_date"))
    rows = conn.execute(f"{sql} AND stock_id=? ORDER BY trade_date DESC", (sid,)).fetchall()

新表要用這支 helper,先把優先序決定好、登記進 SOURCE_PRIORITY,不要自己在呼叫端刻優先序。
"""
from __future__ import annotations

# 各表的來源優先序(SSOT)。決定依據(2026-09-27 逐表核對真實 DB 內 source 值 + 既有程式碼註記):
#   stock_daily_bars: finmind有adj_close最完整,twse/tpex官方源補finmind缺口,yfinance是最後備援
#     (見 backfill_twse_daily_prices.py/backfill_tpex_daily_prices.py 檔頭註記)。
#   stock_margin_daily: twse_mi_margn官方優先(既有_load_vol_risk_flags已經這樣做,本檔案只是
#     把它變成SSOT供其他呼叫端共用)。
#   stock_institutional_daily: 官方(twse_t86/tpex_insti)優先於finmind——dealer_self_net兩來源
#     定義不同(finmind只算自行買賣,官方含避險),官方口徑較完整,見schema註記。
#   stock_daytrade_daily: 官方(twse_twtb4u)優先,但無論哪個來源都不能信這張表自己的
#     daytrade_ratio_pct/total_volume欄(見compute_xq_style_metrics.py docstring另外的說明)。
#   stock_holding_dispersion_weekly: tdcc(集保結算所直接來源)優先於finmind。
#   stock_lending_balance_daily/stock_short_interest_daily/stock_block_trade: 目前實測都只有
#     單一來源,優先序是防禦性登記,不代表真的有衝突。
SOURCE_PRIORITY: dict[str, tuple[str, ...]] = {
    "stock_daily_bars": ("finmind", "twse_mi_index", "tpex_daily", "yfinance"),
    "stock_margin_daily": ("twse_mi_margn", "finmind"),
    "stock_lending_balance_daily": ("twse",),
    "stock_institutional_daily": ("twse_t86", "tpex_insti", "finmind"),
    "stock_daytrade_daily": ("twse_twtb4u", "finmind"),
    "stock_holding_dispersion_weekly": ("tdcc", "finmind"),
    "stock_short_interest_daily": ("twse",),
    "stock_block_trade": ("finmind",),
    "stock_shareholding_daily": ("finmind",),
}


def priority_case_sql(table: str, source_col: str = "source") -> str:
    """回傳 `CASE source WHEN 'x' THEN 0 ... ELSE N END` 片段。"""
    priority = SOURCE_PRIORITY.get(table)
    if not priority:
        raise KeyError(f"{table} 未登記在 SOURCE_PRIORITY,新表要先決定優先序再用這支 helper")
    whens = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(priority))
    return f"CASE {source_col} {whens} ELSE {len(priority)} END"


def dedup_query(table: str, key_cols: tuple[str, ...], inner_where: str = "") -> str:
    """回傳完整去重 subquery(`SELECT * FROM (...) WHERE rn=1`)。

    `inner_where`(例如 `"WHERE stock_id IN (?,?,?)"`)會被推到最內層、ROW_NUMBER() 計算
    *之前* 先篩選——這是既有 `_load_vol_risk_flags` 對 stock_margin_daily 的正確寫法(篩完
    再開窗,不是全表開窗再篩),對大表(如 stock_daily_bars 多來源後可能上百萬列)效能差很多,
    不要省略只把條件加在最外層 `WHERE rn=1 AND ...` 後面。
    """
    partition = ", ".join(key_cols)
    case_sql = priority_case_sql(table)
    return (f"SELECT * FROM (SELECT *, ROW_NUMBER() OVER "
            f"(PARTITION BY {partition} ORDER BY {case_sql}) AS rn "
            f"FROM {table} {inner_where}) WHERE rn=1")
