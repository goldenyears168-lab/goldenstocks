"""C18acc screen 的下單層 hooks（依賴反轉）。

`rrg_mono_swap_accel_screen` 不再 import `order.*`（R1）；送單、加碼台帳、
成交報告、候選池 digest 改由本模組實作，並由 **入口腳本** 呼叫 `install()` 注入。
未注入 ＝ screen 只產生訊號與 intent，不送單、不寫台帳、不寄信（fail-closed）。

入口：scripts/run_rrg_mono_swap_accel_screen.py · scripts/run_launchd_replay.py
"""

from __future__ import annotations

from typing import Any

from order.c18acc_monitor_config import load_c18acc_pyramid_add_monitor_config
from order.c18acc_order import process_c18acc_orders
from order.c18acc_order_config import assert_pool_matches_champion, load_c18acc_order_config
from order.c18acc_position_monitor import scan_c18acc_pyramid_adds
from order.c18acc_pyramid_ledger import append_pyramid_entry, load_c18acc_pyramid_ledger
from order.order_fill_report import maybe_send_c18acc_pool_digest, maybe_send_fill_report


def _pyramid_scan(conn, *, slots, session, poll_minute, trading_dates, cfg):
    return scan_c18acc_pyramid_adds(
        conn,
        state={"slots": slots},
        session_date=session,
        poll_minute=poll_minute,
        trading_dates=trading_dates,
        pyramid_cfg=cfg,
        ledger=load_c18acc_pyramid_ledger(),
    )


def _pyramid_record(hit, *, session, poll_minute, dry):
    append_pyramid_entry(
        pyramid_parent_cid=hit.position.parent_cid,
        symbol=hit.position.symbol,
        session_date=session,
        poll_minute=poll_minute,
        slot=hit.position.slot,
        status="dry_run" if dry else "intent_emitted",
        dry_run=dry,
        note=hit.reason,
        ledger=load_c18acc_pyramid_ledger(),
    )


def _process_orders(actions, *, session_date, poll_minute, dry):
    """回傳 (order_submit | None, applied_actions | None)。與原 inline 邏輯逐行等價。"""
    cfg = load_c18acc_order_config()
    if not (cfg.order_enabled and (cfg.auto_submit or not dry)):
        return None, None
    order_submit = process_c18acc_orders(
        actions,
        session_date=session_date,
        poll_minute=poll_minute,
        dry_run=dry or not cfg.auto_submit,
    )
    applied = None
    if not dry and cfg.auto_submit:
        applied = list(order_submit.get("applied_actions") or [])
    return order_submit, applied


def _send_fill_report(rows: list[dict[str, Any]]) -> None:
    maybe_send_fill_report(
        rows,
        sleeve="C18acc",
        env_flag="RUN_RRG_C18ACC_EMAIL",
        header="C18acc 下單結果（白話成交報告）",
    )


def install() -> None:
    """把下單層實作注入 screen。重複呼叫安全。"""
    import rrg_mono_swap_accel_screen as screen

    screen.install_order_hooks(
        screen.C18accOrderHooks(
            assert_pool=assert_pool_matches_champion,
            pyramid_cfg=load_c18acc_pyramid_add_monitor_config,
            pyramid_scan=_pyramid_scan,
            pyramid_record=_pyramid_record,
            process_orders=_process_orders,
            send_fill_report=_send_fill_report,
            send_pool_digest=maybe_send_c18acc_pool_digest,
        )
    )
