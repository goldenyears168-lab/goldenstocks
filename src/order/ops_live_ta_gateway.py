"""ops_live_ta 的富邦 gateway（依賴反轉）。

`ops_live_ta` 不再 import `order.*`（R1）；富邦連線與帳戶快照由本模組提供，
並由入口腳本（scripts/order/run_ops_live_ta_poll.py）呼叫 `install()` 注入。
未注入 ＝ 無富邦連線，poll 依既有降級路徑改走 Yahoo（fail-closed，不碰券商）。
"""

from __future__ import annotations

from order.fubon_account import _held_qty, account_snapshot
from order.fubon_session import connect_fubon


def install() -> None:
    """把富邦實作注入 ops_live_ta。重複呼叫安全。"""
    import ops_live_ta

    ops_live_ta.install_fubon_gateway(
        ops_live_ta.FubonGateway(
            connect=connect_fubon,
            account_snapshot=account_snapshot,
            held_qty=_held_qty,
        )
    )
