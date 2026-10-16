"""strategy_signal_radar 的富邦持倉讀取（依賴反轉）。

雷達（賣出建議）不再 import `order.*`（R1）；唯讀持倉查詢由本模組提供，並由入口腳本
呼叫 `install()` 注入。只呼叫 `accounting.inventories`，**不送單、不改單**。
未注入 ＝ 雷達不登入券商，改讀 `order_holdings_snapshot`（既有降級路徑）。

入口：scripts/run_sell_signal_radar.py · scripts/run_launchd_replay.py · scripts/run_signal_radar_replay.py
"""

from __future__ import annotations

from order.fubon_orders import holdings_shares_by_symbol
from order.fubon_session import connect_fubon


def fetch_holdings() -> dict[str, int]:
    session = connect_fubon(realtime=False)
    return holdings_shares_by_symbol(session)


def install() -> None:
    """把富邦持倉讀取注入雷達。重複呼叫安全。"""
    import strategy_signal_radar

    strategy_signal_radar.install_holdings_fetcher(fetch_holdings)
