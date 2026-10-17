#!/usr/bin/env python3
"""Read-only ground-truth check (2026-08-08): does the real Fubon broker
realized P&L for TMF over the last N calendar days agree in sign/magnitude
with tonight's true-re-simulation backtest, which shows the current live
PAPER_RECIPE at pooled mean=-37/day across 265 historical days?

Pulls close_position_record fills for a date range via query_tmf_close_position_legs,
FIFO-pairs them with build_session_blotter/pair_fills_fifo (existing production
code, not reimplemented here), and prints realized net P&L per calendar day.

Read-only: only calls query_* functions. Never places, cancels, or amends
any order. Does not touch config/order.yaml, .env, launchd/, or any live
strategy file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

sys.path.insert(0, "src")

from order.fubon_futopt_orders import (  # noqa: E402
    pick_futopt_account,
    query_tmf_close_position_legs,
    query_tmf_margin_equity,
)
from order.fubon_session import connect_fubon  # noqa: E402
from tmf_channel.blotter import pair_fills_fifo  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="lookback calendar days")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)

    print(f"Connecting (read-only query use only)...", flush=True)
    session = connect_fubon(realtime=False)
    acc = pick_futopt_account(session)
    print(f"account={getattr(acc, 'account', acc)!r}", flush=True)

    legs = query_tmf_close_position_legs(
        session, acc=acc, start_date=start.isoformat(), end_date=end.isoformat()
    )
    print(f"fetched {len(legs)} close-position legs from {start} to {end}", flush=True)

    fill_legs = []
    for leg in legs:
        d = leg.get("date")
        fill_legs.append(
            {
                "t": f"{d}T00:00:00+08:00" if d else None,
                "date": d,
                "s": leg.get("s"),
                "px": leg.get("px"),
                "n": leg.get("n"),
                "order_no": leg.get("order_no"),
                "tax": leg.get("tax"),
                "fee": leg.get("fee"),
                "source": leg.get("source"),
            }
        )

    days = sorted({f["date"] for f in fill_legs if f.get("date")})
    print(f"distinct trading days with fills: {len(days)} -> {days}", flush=True)

    # Single FIFO pass across the WHOLE range (chronological), not per-day --
    # a per-day pass with an empty seed would misprice any position opened
    # on one calendar day and closed on the next (common for TMF, which
    # trades across the day/night boundary). Attribute each realized trade
    # to the calendar day it EXITED on for the per-day breakdown below.
    trades, residual = pair_fills_fifo(fill_legs, seed=[])
    realized = [t for t in trades if t.get("pnl") is not None]
    print(f"n_realized_trades={len(realized)} residual_open_legs={len(residual)}", flush=True)

    per_day = {}
    for t in realized:
        d = str(t.get("xt") or "")[:10]
        row = per_day.setdefault(d, {"n_realized_trades": 0, "net_pts": 0.0})
        row["n_realized_trades"] += 1
        row["net_pts"] += float(t["pnl"])
    for d in sorted(per_day):
        per_day[d]["net_pts"] = round(per_day[d]["net_pts"], 1)
        v = per_day[d]
        print(f"  {d}: net={v['net_pts']:+.1f}pt n_trades={v['n_realized_trades']}", flush=True)

    total_net = sum(v["net_pts"] for v in per_day.values())
    print(f"\nTOTAL realized net across {len(per_day)} days: {total_net:+.1f}pt "
          f"(mean={total_net/max(1,len(per_day)):+.2f}pt/day)")

    equity = None
    try:
        equity = query_tmf_margin_equity(session, acc=acc)
    except Exception as exc:  # noqa: BLE001
        print(f"query_tmf_margin_equity failed: {exc}")
    if equity:
        print(f"\ntoday's broker margin-equity snapshot: {json.dumps(equity, indent=2, ensure_ascii=False)}")

    out = {"per_day": per_day, "total_net_pts": round(total_net, 1), "equity_today": equity}
    out_path = "reports/research/channel_lab/tmf_realized_pnl_history_check_result.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
