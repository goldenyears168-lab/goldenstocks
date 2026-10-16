"""C18acc · triple WMA kinematic timeline cache (relative MV/RV structure)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

import pandas as pd

from market_benchmark import load_benchmark_close
from research.backtest.archive.c18acc_hold3d_event_study import (
    _prepare_c18acc_context,
    c18acc_live_s2_config,
)
from research.backtest.archive.c18acc_s2_dual_wma_annot import (
    _collect_annot_dates,
    _exit_frame_idx_for_date,
)
from research.backtest.archive.c18acc_triple_wma_extreme_profile import (
    _frame_idx_for_minute,
)
from research.backtest.dual_wma_signal_backtest import _build_stock_frame_maps, _price_at
from research.backtest.finpilot_local_backtest import load_price_panels
from research.backtest.rrg_mono_score_swap_c import simulate_score_swap_c
from research.backtest.triple_wma_pullback_sweep import _mv, _q, _rv
from rrg_universe_intraday_panel import (
    WMA_MICRO_LENGTH,
    build_dual_wma_intraday_trajectories,
    intraday_poll_minutes,
)
from abc_v3_f1_kinematics import (  # noqa: F401  (re-export; 2026-10 自本模組搬出)
    KinematicSnap,
    _build_kinematic_timeline,
    _mv_order,
    _mv_ranks,
    _safe_gap,
)


def _leg_from_timeline(
    period: dict[str, Any],
    timeline: list[KinematicSnap],
) -> dict[str, Any] | None:
    if len(timeline) < 2:
        return None
    entry = timeline[0]
    peak_i = max(range(len(timeline)), key=lambda i: timeline[i].px)
    trough_i = min(range(len(timeline)), key=lambda i: timeline[i].px)
    peak = timeline[peak_i]
    actual_ret = float(period.get("return_pct") or 0)
    return {
        "leg_id": f"{period.get('entry_date')}|{period.get('stock_id')}|{period.get('slot', 0)}",
        "stock_id": period.get("stock_id"),
        "stock_name": period.get("stock_name") or "",
        "entry_date": period.get("entry_date"),
        "exit_date": period.get("exit_date"),
        "entry_minute": period.get("entry_minute"),
        "hold_days": period.get("hold_days"),
        "return_pct": actual_ret,
        "outcome": "win" if actual_ret > 0 else "loss",
        "peak_ret_pct": round(peak.ret_from_entry_pct, 3),
        "trough_ret_pct": round(timeline[trough_i].ret_from_entry_pct, 3),
        "peak_frame_idx": peak_i,
        "entry_t0": entry.to_dict(),
        "timeline": [s.to_dict() for s in timeline],
    }


def build_kinematic_legs_from_periods(
    conn: sqlite3.Connection,
    periods: list[dict[str, Any]],
    *,
    close: pd.DataFrame | None = None,
    full_dates: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Annotate executed sim periods with KinematicSnap 30m timelines."""
    if close is None:
        close, _, _ = load_price_panels(conn)
    dates = full_dates or close.index.astype(str).tolist()
    stock_ids = sorted({str(p["stock_id"]) for p in periods})
    annot_dates = _collect_annot_dates(periods, dates)
    print(
        f"kinematic-legs: {len(periods)} periods · {len(stock_ids)} stocks · "
        f"{len(annot_dates)} dates …",
        flush=True,
    )
    bench = load_benchmark_close(conn).reindex(close.index).astype(float)
    frames, trajectories, traj_meta = build_dual_wma_intraday_trajectories(
        conn,
        dates=annot_dates,
        stock_ids=stock_ids,
        poll_minutes=intraday_poll_minutes(),
        micro_lengths=(WMA_MICRO_LENGTH,),
    )
    stock_maps = _build_stock_frame_maps(trajectories, frames)

    legs: list[dict[str, Any]] = []
    for p in periods:
        sid = str(p["stock_id"])
        entry = str(p["entry_date"])
        exit_d = str(p.get("exit_date") or "")
        entry_px = p.get("entry_px")
        if not exit_d or not entry_px or float(entry_px) <= 0:
            continue
        points = stock_maps.get(sid)
        if not points:
            continue
        entry_i = _frame_idx_for_minute(frames, entry, p.get("entry_minute"))
        exit_i = _exit_frame_idx_for_date(frames, exit_d)
        if exit_i < entry_i:
            continue
        timeline = _build_kinematic_timeline(
            conn,
            bench,
            stock_id=sid,
            entry_i=entry_i,
            exit_i=exit_i,
            frames=frames,
            points_by_idx=points,
            entry_px=float(entry_px),
        )
        leg = _leg_from_timeline(p, timeline)
        if leg:
            legs.append(leg)
    return legs, traj_meta


def run_c18acc_kinematic_timeline_cache(
    conn: sqlite3.Connection,
    *,
    date_start: str = "2025-01-02",
    date_end: str | None = None,
    n_slots: int = 99,
) -> dict[str, Any]:
    close, _, _ = load_price_panels(conn)
    full_dates = close.index.astype(str).tolist()
    end = date_end or full_dates[-1]
    trade_dates = [d for d in full_dates if date_start <= d <= end]
    if len(trade_dates) < 5:
        raise ValueError(f"insufficient trade dates: {len(trade_dates)}")

    ctx = _prepare_c18acc_context(conn, trade_dates)
    cfg = c18acc_live_s2_config(n_slots=n_slots)
    periods, sim_summary = simulate_score_swap_c(
        conn,
        trade_dates=trade_dates,
        full_dates=ctx["full_dates"],
        close=ctx["close"],
        bench=ctx["bench"],
        fresh_by_date=ctx["fresh_by_date"],
        zone_by_date=ctx["zone_by_date"],
        config=cfg,
        mono_by_date=ctx["mono_by_date"],
        mono_up_by_date=ctx["mono_up_by_date"],
        mono_up_fresh_by_date=ctx["mono_up_fresh_by_date"],
        kbar_cache=ctx["kbar_cache"],
        rs_mom=ctx["rs_mom"],
        rs_ratio=ctx["rs_ratio"],
    )
    legs, traj_meta = build_kinematic_legs_from_periods(
        conn,
        periods,
        close=ctx["close"],
        full_dates=ctx["full_dates"],
    )

    return {
        "schema": "c18acc_kinematic_timeline-v1",
        "date_start": trade_dates[0],
        "date_end": trade_dates[-1],
        "n_slots": n_slots,
        "poll": "30m",
        "sim_summary": sim_summary,
        "trajectory_meta": traj_meta,
        "n_legs": len(legs),
        "legs": legs,
    }
