"""ABC-v3-F1／C18acc 共用的 K 線軌跡、止盈與加碼判斷邏輯（純邏輯）。

自 research.backtest.* 逐字搬出（2026-10 R4 清理）：下單層的 position monitor 與研究回測
共用同一份決策邏輯，故置於中性模組，兩邊皆依賴它；原研究模組改為從本模組 re-export，
對外名稱不變。本檔不得 import research.* 或 order.*。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Literal
import sqlite3

import pandas as pd

from flow_returns import stock_close
from rrg_universe_snapshot import kbar_close_at_minute


# --- from research.backtest.archive.c18acc_kinematic_gap_rv3_study
GapLogic = Literal["or", "and", "w20_w5_only", "w5_w3_only"]


# --- from research.backtest.archive.c18acc_kinematic_gap_rv3_study
Rv3Mode = Literal["none", "drv", "poll_drop", "drv_and_poll", "drv_or_poll", "below_entry"]


# --- from research.backtest.archive.c18acc_kinematic_gap_rv3_study
@dataclass(frozen=True)
class GapRv3Params:
    gap_logic: GapLogic
    gw5_min: float
    gw3_min: float
    gap_expand_min: float
    rv3_mode: Rv3Mode
    drv3_min: float
    dmv3_min: float
    min_ret_pct: float
    require_pullback: bool
    dist_min: float
    consecutive: int

    @property
    def variant_id(self) -> str:
        pb = "pb1" if self.require_pullback else "pb0"
        rv = self.rv3_mode
        dmv = int(self.dmv3_min * 100) if self.dmv3_min else 0
        exp = int(self.gap_expand_min) if self.gap_expand_min else 0
        return (
            f"GR_{self.gap_logic}_g5{int(self.gw5_min)}_g3{self.gw3_min:g}_x{exp}"
            f"_rv{rv}_d{int(self.drv3_min*100)}_mv3{dmv}"
            f"_r{int(self.min_ret_pct)}_{pb}_dist{int(self.dist_min)}_c{self.consecutive}"
        )


# --- from research.backtest.archive.c18acc_kinematic_gap_rv3_study
def _gap_ok(
    snap: dict[str, Any],
    entry: dict[str, Any],
    params: GapRv3Params,
) -> bool:
    g5 = snap.get("gap_mv_w20_w5")
    g3 = snap.get("gap_mv_w5_w3")
    w5_ok = g5 is not None and float(g5) >= params.gw5_min - 1e-9
    w3_ok = g3 is not None and float(g3) >= params.gw3_min - 1e-9
    if params.gap_logic == "or":
        gap = w5_ok or w3_ok
    elif params.gap_logic == "and":
        gap = w5_ok and w3_ok
    elif params.gap_logic == "w20_w5_only":
        gap = w5_ok
    else:
        gap = w3_ok
    if params.gap_expand_min > 0:
        eg5 = entry.get("gap_mv_w20_w5")
        eg3 = entry.get("gap_mv_w5_w3")
        ex = False
        if g5 is not None and eg5 is not None and float(g5) - float(eg5) >= params.gap_expand_min - 1e-9:
            ex = True
        if g3 is not None and eg3 is not None and float(g3) - float(eg3) >= params.gap_expand_min - 1e-9:
            ex = True
        gap = gap and ex
    return gap


# --- from research.backtest.triple_wma_pullback_sweep
def _q(point: dict[str, Any], leg: str) -> str | None:
    leg_d = point.get(leg) or {}
    q = leg_d.get("quadrant")
    return str(q) if q else None


# --- from research.backtest.triple_wma_pullback_sweep
def _rv(point: dict[str, Any], leg: str) -> float | None:
    v = (point.get(leg) or {}).get("rs_ratio")
    return float(v) if v is not None else None


# --- from research.backtest.triple_wma_pullback_sweep
def _mv(point: dict[str, Any], leg: str) -> float | None:
    v = (point.get(leg) or {}).get("rs_momentum")
    return float(v) if v is not None else None


# --- from research.backtest.dual_wma_signal_backtest
def _price_at(
    conn: sqlite3.Connection,
    bench: pd.Series,
    stock_id: str,
    trade_date: str,
    minute: str,
) -> tuple[float | None, float | None]:
    px = kbar_close_at_minute(conn, stock_id, trade_date, minute)
    if px is None or px <= 0:
        px = stock_close(conn, stock_id, trade_date)
    bpx = kbar_close_at_minute(conn, "IX0001", trade_date, minute)
    if bpx is None or bpx <= 0:
        if trade_date in bench.index:
            bpx = float(bench.at[trade_date])
        else:
            bpx = None
    return px, bpx


# --- from research.backtest.dual_wma_signal_backtest
def _build_stock_frame_maps(
    trajectories: list[dict[str, Any]],
    frames: list[dict[str, str]],
) -> dict[str, list[dict[str, Any] | None]]:
    frame_ids = [f["id"] for f in frames]
    out: dict[str, list[dict[str, Any] | None]] = {}
    for t in trajectories:
        by_id = {p["frame_id"]: p for p in t["points"]}
        out[t["stock_id"]] = [by_id.get(fid) for fid in frame_ids]
    return out


# --- from research.backtest.archive.c18acc_s2_dual_wma_annot
def _entry_frame_idx(frames: list[dict[str, str]], entry_date: str) -> int:
    for i, f in enumerate(frames):
        if f["date"] > entry_date:
            return max(0, i)
        if f["date"] == entry_date:
            return i
    return 0


# --- from research.backtest.archive.c18acc_triple_wma_extreme_profile
def _frame_idx_for_minute(
    frames: list[dict[str, str]],
    trade_date: str,
    minute: str | None,
) -> int:
    if minute:
        for i, f in enumerate(frames):
            if f["date"] == trade_date and f["minute"] == minute:
                return i
        for i, f in enumerate(frames):
            if f["date"] == trade_date and f["minute"] >= minute:
                return i
    return _entry_frame_idx(frames, trade_date)


# --- from research.backtest.archive.abc_v3_f1_kinematic_tp_sl_study
Snap = dict[str, Any]


# --- from research.backtest.archive.abc_v3_f1_kinematic_tp_sl_study
RvMode = Literal["none", "drv", "poll_drop", "drv_and_poll", "drv_or_poll"]


# --- from research.backtest.archive.abc_v3_f1_kinematic_tp_sl_study
@dataclass(frozen=True)
class TakeProfitParams:
    tp_mode: Literal["gap_rv", "rv_only", "trail_giveback"] = "gap_rv"
    gap_logic: Literal["or", "w20_w5_only"] = "or"
    gw5_min: float = 6.0
    gw3_min: float = 2.0
    gap_expand_min: float = 0.0
    rv_leg: Literal["w3", "w5"] = "w3"
    rv_mode: RvMode = "poll_drop"
    drv_min: float = 0.35
    min_ret_pct: float = 8.0
    trail_giveback_pp: float = 2.0
    consecutive: int = 1

    @property
    def variant_id(self) -> str:
        if self.tp_mode == "trail_giveback":
            return (
                f"TP_trail_gb{self.trail_giveback_pp:g}_r{int(self.min_ret_pct)}"
                f"_c{self.consecutive}"
            )
        if self.tp_mode == "rv_only":
            return (
                f"TP_rv{self.rv_leg}{self.rv_mode}_d{int(self.drv_min*100)}"
                f"_r{int(self.min_ret_pct)}_c{self.consecutive}"
            )
        return (
            f"TP_{self.gap_logic}_g5{int(self.gw5_min)}_g3{self.gw3_min:g}"
            f"_rv{self.rv_leg}{self.rv_mode}_d{int(self.drv_min*100)}"
            f"_r{int(self.min_ret_pct)}_c{self.consecutive}"
        )


# --- from research.backtest.archive.abc_v3_f1_kinematic_tp_sl_study
@dataclass(frozen=True)
class StopLossParams:
    sl_mode: Literal["kinematic", "underwater"] = "kinematic"
    dmv_w5_min: float = 0.35
    dmv_w3_min: float = 0.0
    drv_w5_min: float = 0.25
    drv_w3_min: float = 0.0
    rv_leg: Literal["w3", "w5", "both"] = "w5"
    rv_mode: RvMode = "drv"
    require_w5_lagging: bool = False
    require_w3_lagging: bool = False
    gap_w20_w5_collapse: float = 0.0
    arm_ret_max_pct: float = 5.0
    consecutive: int = 1

    @property
    def variant_id(self) -> str:
        lag = ""
        if self.require_w5_lagging:
            lag += "w5L"
        if self.require_w3_lagging:
            lag += "w3L"
        return (
            f"SL_{self.sl_mode}_dmv5{int(self.dmv_w5_min*100)}_drv5{int(self.drv_w5_min*100)}"
            f"_rv{self.rv_leg}{self.rv_mode}_{lag or 'x'}"
            f"_arm{int(self.arm_ret_max_pct)}_c{self.consecutive}"
        )


# --- from research.backtest.archive.abc_v3_f1_kinematic_tp_sl_study
@dataclass(frozen=True)
class TpSlCombo:
    tp: TakeProfitParams
    sl: StopLossParams

    @property
    def variant_id(self) -> str:
        return f"{self.tp.variant_id}__{self.sl.variant_id}"


# --- from research.backtest.archive.abc_v3_f1_kinematic_tp_sl_study
def _gap_params_from_tp(tp: TakeProfitParams) -> GapRv3Params:
    return GapRv3Params(
        gap_logic="or" if tp.gap_logic == "or" else "w20_w5_only",
        gw5_min=tp.gw5_min,
        gw3_min=tp.gw3_min,
        gap_expand_min=tp.gap_expand_min,
        rv3_mode="none",
        drv3_min=0.0,
        dmv3_min=0.0,
        min_ret_pct=0.0,
        require_pullback=False,
        dist_min=0.0,
        consecutive=1,
    )


# --- from research.backtest.archive.abc_v3_f1_kinematic_tp_sl_study
def _rv_snap_ok(
    snap: Snap,
    prev: Snap | None,
    entry: Snap,
    *,
    leg: Literal["w3", "w5"],
    mode: RvMode,
    drv_min: float,
) -> bool:
    if mode == "none":
        return True
    key_drv = f"drv_{leg}"
    key_rv = f"{leg}_rv"
    drv = snap.get(key_drv)
    rv = snap.get(key_rv)
    prev_rv = prev.get(key_rv) if prev else None
    ent_rv = entry.get(key_rv)
    poll_drop = (
        rv is not None
        and prev_rv is not None
        and float(rv) < float(prev_rv) - 1e-9
    )
    drv_bad = drv is not None and float(drv) <= -drv_min + 1e-9
    if mode == "drv":
        return drv_bad
    if mode == "poll_drop":
        return poll_drop
    if mode == "drv_and_poll":
        return drv_bad and poll_drop
    if mode == "drv_or_poll":
        return drv_bad or poll_drop
    if mode == "below_entry":
        return (
            rv is not None
            and ent_rv is not None
            and float(rv) < float(ent_rv) - 1e-9
        )
    return False


# --- from research.backtest.archive.abc_v3_f1_kinematic_tp_sl_study
def _tp_snap_ok(
    snap: Snap,
    prev: Snap | None,
    entry: Snap,
    tp: TakeProfitParams,
    *,
    running_peak_ret: float,
) -> bool:
    ret = float(snap.get("ret_from_entry_pct") or 0)
    if ret < tp.min_ret_pct - 1e-9:
        return False
    if tp.tp_mode == "trail_giveback":
        return ret <= running_peak_ret - tp.trail_giveback_pp + 1e-9
    if tp.tp_mode == "rv_only":
        return _rv_snap_ok(
            snap, prev, entry, leg=tp.rv_leg, mode=tp.rv_mode, drv_min=tp.drv_min
        )
    gp = _gap_params_from_tp(tp)
    if not _gap_ok(snap, entry, gp):
        return False
    return _rv_snap_ok(
        snap, prev, entry, leg=tp.rv_leg, mode=tp.rv_mode, drv_min=tp.drv_min
    )


# --- from research.backtest.archive.abc_v3_f1_kinematic_tp_sl_study
NEVER_SL = StopLossParams(
    dmv_w5_min=0.0,
    drv_w5_min=99.0,
    arm_ret_max_pct=-999.0,
    consecutive=99,
)


# --- from research.backtest.abc_v3_f1_structural_pyramid_study
Leg = dict[str, Any]


# --- from research.backtest.abc_v3_f1_structural_pyramid_study
ConditionKind = Literal[
    "any_underwater",
    "underwater_stable",
    "underwater_rebound",
    "underwater_gap_intact",
    "underwater_stable_rebound",
]


# --- from research.backtest.abc_v3_f1_structural_pyramid_study
@dataclass(frozen=True)
class PyramidCondition:
    id: str
    label: str
    kind: ConditionKind
    dmv_w5_floor: float = -0.35
    rebound_min: float = 0.3
    gap_converge_max: float = 1.0

    def spec(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "dmv_w5_floor": self.dmv_w5_floor,
            "rebound_min": self.rebound_min,
            "gap_converge_max": self.gap_converge_max,
        }


# --- from research.backtest.abc_v3_f1_structural_pyramid_study
def default_pyramid_conditions() -> tuple[PyramidCondition, ...]:
    """3-4 preregistered candidates (RP-2 §4.1) + unconditional baseline B."""
    return (
        PyramidCondition(
            id="underwater_stable",
            label="ret<0 · w5_quad∈{leading,improving} · dmv_w5>-0.35",
            kind="underwater_stable",
        ),
        PyramidCondition(
            id="underwater_rebound",
            label="ret<0 · W3 RV rebound from in-hold trough ≥0.3",
            kind="underwater_rebound",
        ),
        PyramidCondition(
            id="underwater_gap_intact",
            label="ret<0 · gap_mv_w20_w5 converged <1.0 vs entry",
            kind="underwater_gap_intact",
        ),
        PyramidCondition(
            id="underwater_stable_rebound",
            label="ret<0 · stable(w5 quad+dmv) AND rv3 rebound≥0.3",
            kind="underwater_stable_rebound",
        ),
    )


# --- from research.backtest.abc_v3_f1_structural_pyramid_study
def _stable_ok(snap: dict[str, Any], cond: PyramidCondition) -> bool:
    quad = str(snap.get("w5_quad") or "").lower()
    if quad not in ("leading", "improving"):
        return False
    dmv5 = snap.get("dmv_w5")
    return dmv5 is not None and float(dmv5) > cond.dmv_w5_floor + 1e-9


# --- from research.backtest.abc_v3_f1_structural_pyramid_study
def _cond_ok(
    snap: dict[str, Any],
    cond: PyramidCondition,
    *,
    entry_gap_w20_w5: float | None,
    rv3_rebound: float | None,
) -> bool:
    if cond.kind == "any_underwater":
        return True
    if cond.kind == "underwater_stable":
        return _stable_ok(snap, cond)
    if cond.kind == "underwater_rebound":
        return rv3_rebound is not None and rv3_rebound >= cond.rebound_min - 1e-9
    if cond.kind == "underwater_gap_intact":
        gap = snap.get("gap_mv_w20_w5")
        if gap is None or entry_gap_w20_w5 is None:
            return False
        return float(gap) >= float(entry_gap_w20_w5) - cond.gap_converge_max - 1e-9
    if cond.kind == "underwater_stable_rebound":
        return (
            _stable_ok(snap, cond)
            and rv3_rebound is not None
            and rv3_rebound >= cond.rebound_min - 1e-9
        )
    return False


# --- from research.backtest.abc_v3_f1_structural_pyramid_study
def find_add_idx(leg: Leg, cond: PyramidCondition) -> int | None:
    """First poll where ``cond`` holds while underwater.

    Only information available at poll i is used: ret_from_entry_pct,
    w5_quad, dmv_w5 (drawdown from *running* MV peak), gap_mv_w20_w5 vs the
    entry snapshot, and the running W3-RV trough over snaps 0..i.  The add
    must happen strictly after entry (i>=1) and strictly before the final
    exit snap (i<=len-2).
    """
    timeline = leg.get("timeline") or []
    if len(timeline) < 3:
        return None
    entry = leg.get("entry_t0") or timeline[0]
    entry_gap = entry.get("gap_mv_w20_w5")

    trough: float | None = None
    for i, snap in enumerate(timeline):
        rv3 = snap.get("w3_rv")
        rebound: float | None = None
        if rv3 is not None:
            rv3 = float(rv3)
            if trough is None or rv3 < trough:
                trough = rv3
            rebound = round(rv3 - trough, 4)
        if i == 0 or i > len(timeline) - 2:
            continue
        ret = snap.get("ret_from_entry_pct")
        if ret is None or float(ret) >= 0.0 - 1e-12:
            continue
        if _cond_ok(snap, cond, entry_gap_w20_w5=entry_gap, rv3_rebound=rebound):
            return i
    return None


# --- from research.backtest.archive.c18acc_kinematic_timeline_cache
def _safe_gap(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, 4)


# --- from research.backtest.archive.c18acc_kinematic_timeline_cache
def _mv_order(mv: dict[str, float | None]) -> str:
    legs = [(k, v) for k, v in mv.items() if v is not None]
    if len(legs) < 2:
        return ""
    legs.sort(key=lambda x: x[1], reverse=True)
    return ">".join(k for k, _ in legs)


# --- from research.backtest.archive.c18acc_kinematic_timeline_cache
def _mv_ranks(mv: dict[str, float | None]) -> dict[str, int | None]:
    legs = [(k, v) for k, v in mv.items() if v is not None]
    out: dict[str, int | None] = {k: None for k in mv}
    if not legs:
        return out
    legs.sort(key=lambda x: x[1], reverse=True)
    for rank, (k, _) in enumerate(legs, start=1):
        out[k] = rank
    return out


# --- from research.backtest.archive.c18acc_kinematic_timeline_cache
@dataclass(frozen=True)
class KinematicSnap:
    frame_idx: int
    trade_date: str
    minute: str
    px: float
    ret_from_entry_pct: float
    w20_rv: float | None
    w20_mv: float | None
    w20_quad: str | None
    w5_rv: float | None
    w5_mv: float | None
    w5_quad: str | None
    w3_rv: float | None
    w3_mv: float | None
    w3_quad: str | None
    spread_class: str | None
    spread_dist: float | None
    gap_mv_w20_w5: float | None
    gap_mv_w5_w3: float | None
    gap_mv_w20_w3: float | None
    gap_rv_w20_w5: float | None
    gap_rv_w5_w3: float | None
    gap_rv_w20_w3: float | None
    gap_rv_w5_w20: float | None
    dmv_w20: float | None
    dmv_w5: float | None
    dmv_w3: float | None
    drv_w20: float | None
    drv_w5: float | None
    drv_w3: float | None
    mv_order: str
    mv_rank_w20: int | None
    mv_rank_w5: int | None
    mv_rank_w3: int | None
    w20_mv_decline_streak: int
    w5_mv_decline_streak: int
    w3_mv_decline_streak: int
    parallel_3leg_dmv_deepening: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_idx": self.frame_idx,
            "trade_date": self.trade_date,
            "minute": self.minute,
            "px": round(self.px, 4),
            "ret_from_entry_pct": round(self.ret_from_entry_pct, 3),
            "w20_rv": self.w20_rv,
            "w20_mv": self.w20_mv,
            "w20_quad": self.w20_quad,
            "w5_rv": self.w5_rv,
            "w5_mv": self.w5_mv,
            "w5_quad": self.w5_quad,
            "w3_rv": self.w3_rv,
            "w3_mv": self.w3_mv,
            "w3_quad": self.w3_quad,
            "spread_class": self.spread_class,
            "spread_dist": self.spread_dist,
            "gap_mv_w20_w5": self.gap_mv_w20_w5,
            "gap_mv_w5_w3": self.gap_mv_w5_w3,
            "gap_mv_w20_w3": self.gap_mv_w20_w3,
            "gap_rv_w20_w5": self.gap_rv_w20_w5,
            "gap_rv_w5_w3": self.gap_rv_w5_w3,
            "gap_rv_w20_w3": self.gap_rv_w20_w3,
            "gap_rv_w5_w20": self.gap_rv_w5_w20,
            "dmv_w20": self.dmv_w20,
            "dmv_w5": self.dmv_w5,
            "dmv_w3": self.dmv_w3,
            "drv_w20": self.drv_w20,
            "drv_w5": self.drv_w5,
            "drv_w3": self.drv_w3,
            "mv_order": self.mv_order,
            "mv_rank_w20": self.mv_rank_w20,
            "mv_rank_w5": self.mv_rank_w5,
            "mv_rank_w3": self.mv_rank_w3,
            "w20_mv_decline_streak": self.w20_mv_decline_streak,
            "w5_mv_decline_streak": self.w5_mv_decline_streak,
            "w3_mv_decline_streak": self.w3_mv_decline_streak,
            "parallel_3leg_dmv_deepening": self.parallel_3leg_dmv_deepening,
        }


# --- from research.backtest.archive.c18acc_kinematic_timeline_cache
def _build_kinematic_timeline(
    conn: sqlite3.Connection,
    bench: pd.Series,
    *,
    stock_id: str,
    entry_i: int,
    exit_i: int,
    frames: list[dict[str, str]],
    points_by_idx: list[dict[str, Any] | None],
    entry_px: float,
) -> list[KinematicSnap]:
    if entry_px <= 0:
        return []
    snaps: list[KinematicSnap] = []
    w20_peak = w5_peak = w3_peak = None
    rv20_peak = rv5_peak = rv3_peak = None
    prev_w20 = prev_w5 = prev_w3 = None
    w20_streak = w5_streak = w3_streak = 0
    prev_dmv: dict[str, float | None] = {"w20": None, "w5": None, "w3": None}

    for j in range(entry_i, min(exit_i + 1, len(frames))):
        px, _ = _price_at(conn, bench, stock_id, frames[j]["date"], frames[j]["minute"])
        if px is None or px <= 0:
            continue
        p = points_by_idx[j]
        if not p:
            continue

        w20mv = _mv(p, "w20")
        w5mv = _mv(p, "w5")
        w3mv = _mv(p, "w3")
        w20rv = _rv(p, "w20")
        w5rv = _rv(p, "w5")
        w3rv = _rv(p, "w3")

        if w20mv is not None:
            w20_peak = w20mv if w20_peak is None else max(w20_peak, w20mv)
            w20_streak = w20_streak + 1 if prev_w20 is not None and w20mv < prev_w20 - 1e-9 else 0
        if w5mv is not None:
            w5_peak = w5mv if w5_peak is None else max(w5_peak, w5mv)
            w5_streak = w5_streak + 1 if prev_w5 is not None and w5mv < prev_w5 - 1e-9 else 0
        if w3mv is not None:
            w3_peak = w3mv if w3_peak is None else max(w3_peak, w3mv)
            w3_streak = w3_streak + 1 if prev_w3 is not None and w3mv < prev_w3 - 1e-9 else 0

        if w20rv is not None:
            rv20_peak = w20rv if rv20_peak is None else max(rv20_peak, w20rv)
        if w5rv is not None:
            rv5_peak = w5rv if rv5_peak is None else max(rv5_peak, w5rv)
        if w3rv is not None:
            rv3_peak = w3rv if rv3_peak is None else max(rv3_peak, w3rv)

        dmv_w20 = round(w20mv - w20_peak, 4) if w20mv is not None and w20_peak is not None else None
        dmv_w5 = round(w5mv - w5_peak, 4) if w5mv is not None and w5_peak is not None else None
        dmv_w3 = round(w3mv - w3_peak, 4) if w3mv is not None and w3_peak is not None else None
        drv_w20 = round(w20rv - rv20_peak, 4) if w20rv is not None and rv20_peak is not None else None
        drv_w5 = round(w5rv - rv5_peak, 4) if w5rv is not None and rv5_peak is not None else None
        drv_w3 = round(w3rv - rv3_peak, 4) if w3rv is not None and rv3_peak is not None else None

        parallel = (
            dmv_w20 is not None
            and dmv_w5 is not None
            and dmv_w3 is not None
            and dmv_w20 < 0
            and dmv_w5 < 0
            and dmv_w3 < 0
            and (
                prev_dmv["w20"] is None
                or (
                    dmv_w20 <= (prev_dmv["w20"] or 0) - 1e-9
                    and dmv_w5 <= (prev_dmv["w5"] or 0) - 1e-9
                    and dmv_w3 <= (prev_dmv["w3"] or 0) - 1e-9
                )
            )
        )

        mv_map = {"w20": w20mv, "w5": w5mv, "w3": w3mv}
        ranks = _mv_ranks(mv_map)
        ret = (px / entry_px - 1.0) * 100.0
        f = frames[j]
        snaps.append(
            KinematicSnap(
                frame_idx=j,
                trade_date=f["date"],
                minute=f["minute"],
                px=float(px),
                ret_from_entry_pct=ret,
                w20_rv=w20rv,
                w20_mv=w20mv,
                w20_quad=_q(p, "w20"),
                w5_rv=w5rv,
                w5_mv=w5mv,
                w5_quad=_q(p, "w5"),
                w3_rv=w3rv,
                w3_mv=w3mv,
                w3_quad=_q(p, "w3"),
                spread_class=p.get("spread_class"),
                spread_dist=float(p["spread_dist"]) if p.get("spread_dist") is not None else None,
                gap_mv_w20_w5=_safe_gap(w20mv, w5mv),
                gap_mv_w5_w3=_safe_gap(w5mv, w3mv),
                gap_mv_w20_w3=_safe_gap(w20mv, w3mv),
                gap_rv_w20_w5=_safe_gap(w20rv, w5rv),
                gap_rv_w5_w3=_safe_gap(w5rv, w3rv),
                gap_rv_w20_w3=_safe_gap(w20rv, w3rv),
                gap_rv_w5_w20=_safe_gap(w5rv, w20rv),
                dmv_w20=dmv_w20,
                dmv_w5=dmv_w5,
                dmv_w3=dmv_w3,
                drv_w20=drv_w20,
                drv_w5=drv_w5,
                drv_w3=drv_w3,
                mv_order=_mv_order(mv_map),
                mv_rank_w20=ranks["w20"],
                mv_rank_w5=ranks["w5"],
                mv_rank_w3=ranks["w3"],
                w20_mv_decline_streak=w20_streak,
                w5_mv_decline_streak=w5_streak,
                w3_mv_decline_streak=w3_streak,
                parallel_3leg_dmv_deepening=parallel,
            )
        )
        prev_dmv = {"w20": dmv_w20, "w5": dmv_w5, "w3": dmv_w3}
        if w20mv is not None:
            prev_w20 = w20mv
        if w5mv is not None:
            prev_w5 = w5mv
        if w3mv is not None:
            prev_w3 = w3mv
    return snaps
