#!/usr/bin/env python3
"""四天 × 每分鐘 × 42 檔的特徵面板 —— sweep 的共用底盤。

為什麼要先建面板再 sweep：逐筆回放是整件事最貴的一步,如果每個參數組合都重放一次,
掃 200 組就要重放 200 次。先把**所有尺度的特徵算一次**存成 parquet,sweep 就變成
純粹的 DataFrame 過濾,幾秒鐘跑完幾百組。

## 尺度（使用者 2026-10-04 指定）
5 分 / 30 分 / 全日 / **兩日**。兩日 = 前一交易日全日 + 當日至今（跨日累計）。
⚠ 2026-09-29 的逐筆錄製 10:12:44 才開始（富邦 9/28 13:14→9/29 10:10 連續 21 小時
503,見 src/order/fubon_session.py 的斷路器註解）。所以：
  · 9/29 的「全日」其實是「10:12 之後」,已標記 `partial_day=True`
  · 9/30 的「兩日」會吃到那個殘缺的 9/29 → 也標記
  · 9/29 沒有前一日資料 → 兩日欄位為 NaN

## 前瞻報酬（評估進場品質用,不進篩選條件）
+30 分 / +60 分 / 到當日收盤 / 到隔日收盤。全部用**訊號分鐘結束後第 1 秒之後的
第一筆成交價**當進場價,避免同秒成交製造假 alpha。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from trader_biglot_decision_replay import _cum, replay_day, window  # noqa: E402
from trader_peer_group_choice import day_universe, load_subcat, ma20_and_prevclose  # noqa: E402

P = Path.home() / "goldenstocks-data/data"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
PARTIAL = {"2026-09-29"}          # 錄製缺口
TICK_SEC = 60
SCALES = {"5m": 300, "30m": 1800, "day": None}


def _ep(day: str, hms: str) -> float:
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


def _prep(acc: dict) -> dict:
    for A in acc.values():
        if "cum_amt" not in A:
            A["cum_amt"], A["cum_big"], A["cum_ret"] = _cum(A["amt"]), _cum(A["big"]), _cum(A["ret"])
            A["px0"] = A["px"][0]
    return acc


def _fwd(A: dict, t: float, horizon_sec: float | None, t_cap: float | None = None):
    """進場價 = t 之後第一筆成交；出場價 = t+horizon 之前最後一筆。回傳報酬%。"""
    ts = A["ts"]
    j = bisect.bisect_right(ts, t)
    if j >= len(ts):
        return None, None
    p_in = A["px"][j]
    t_end = (t + horizon_sec) if horizon_sec is not None else (t_cap if t_cap else ts[-1])
    k = bisect.bisect_right(ts, t_end) - 1
    if k <= j:
        return p_in, None
    return p_in, 100 * (A["px"][k] / p_in - 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="+", default=DAYS)
    ap.add_argument("--out", default=str(P / "scratch/trader_feature_panel.parquet"))
    args = ap.parse_args()
    subcat = load_subcat()

    ACC, REF = {}, {}
    for d in args.days:
        uni = day_universe(d)
        a = {k: v for k, v in replay_day(d, uni).items() if v.get("ts")} if uni else {}
        if a:
            ACC[d], REF[d] = _prep(a), ma20_and_prevclose(set(a), d)
            print(f"{d}: 回放 {len(a)} 檔", file=sys.stderr)

    # 每檔每日的「全日」累計總量,給兩日尺度用
    dayagg: dict[tuple[str, str], tuple[float, float, float]] = {}
    for d, acc in ACC.items():
        for sid, A in acc.items():
            dayagg[(d, sid)] = (A["cum_amt"][-1], A["cum_big"][-1], A["cum_ret"][-1])

    rows = []
    for di, day in enumerate(args.days):
        acc = ACC.get(day)
        if not acc:
            continue
        prev = args.days[di - 1] if di > 0 else None
        close_t = _ep(day, "13:30:00")
        nxt = args.days[di + 1] if di + 1 < len(args.days) else None
        # 42 檔加總差額（他說的「台指期面板上的大戶散戶差額」）
        evs = sorted((t, b - r) for A in acc.values() for t, b, r in zip(A["ts"], A["big"], A["ret"]))
        ats, acum, s = [], [], 0.0
        for t, v in evs:
            s += v; ats.append(t); acum.append(s)

        t = _ep(day, "09:00:00")
        while t <= _ep(day, "13:25:00"):
            i = bisect.bisect_right(ats, t) - 1
            aggv = acum[i] if i >= 0 else 0.0
            lo_i = min(range(i + 1), key=lambda k: acum[k]) if i >= 0 else 0
            i5 = bisect.bisect_left(ats, t - 300)
            i30 = bisect.bisect_left(ats, t - 1800)
            agg = {"agg_diff": aggv,
                   "agg_d5": aggv - (acum[i5] if i5 <= i else aggv),
                   "agg_d30": aggv - (acum[i30] if i30 <= i else aggv),
                   "agg_from_low_min": (t - ats[lo_i]) / 60 if i >= 0 else None}
            snap = {}
            for sid, A in acc.items():
                r = {}
                for nm, sec in SCALES.items():
                    bp, rp, amt, _, _, px = window(A, t, sec)
                    r[f"big_{nm}"], r[f"ret_{nm}"], r[f"amt_{nm}"] = bp, rp, amt
                    r[f"bigless_{nm}"] = (bp - rp) if (bp is not None and rp is not None) else None
                    if nm == "day":
                        r["px"] = px
                if r.get("amt_5m") is None or r.get("px") is None:
                    continue
                # 兩日 = 前一日全日 + 當日至今
                pa = dayagg.get((prev, sid)) if prev else None
                if pa and r.get("amt_day"):
                    j = bisect.bisect_right(A["ts"], t)
                    tot = pa[0] + A["cum_amt"][j]
                    big = pa[1] + A["cum_big"][j]
                    ret = pa[2] + A["cum_ret"][j]
                    r["big_2d"] = 100 * big / tot if tot else None
                    r["ret_2d"] = 100 * ret / tot if tot else None
                    r["bigless_2d"] = (r["big_2d"] - r["ret_2d"]) if r["big_2d"] is not None else None
                    r["amt_2d"] = tot
                else:
                    r["big_2d"] = r["ret_2d"] = r["bigless_2d"] = r["amt_2d"] = None
                snap[sid] = r
            if len(snap) < 5:
                t += TICK_SEC; continue
            S = pd.DataFrame(snap).T
            for c in ("amt_5m", "amt_30m", "amt_day", "amt_2d",
                      "big_5m", "big_30m", "big_day", "big_2d",
                      "bigless_5m", "bigless_30m", "bigless_day", "bigless_2d"):
                if c in S:
                    S[c + "_pct"] = pd.to_numeric(S[c], errors="coerce").rank(pct=True) * 100
            ref = REF.get(day, {})
            for sid, r in S.iterrows():
                A = acc[sid]
                grp = subcat.get(sid)
                peers = [x for x in S.index if subcat.get(x) == grp and x in ref]
                chg_p = bias_p = None
                if grp and len(peers) >= 2 and sid in ref:
                    chg = {x: S.loc[x, "px"] / ref[x][1] for x in peers}
                    bia = {x: S.loc[x, "px"] / ref[x][0] for x in peers}
                    chg_p = 100 * sum(v < chg[sid] for v in chg.values()) / len(chg)
                    bias_p = 100 * sum(v < bia[sid] for v in bia.values()) / len(bia)
                p_in, f30 = _fwd(A, t, 1800)
                _, f60 = _fwd(A, t, 3600)
                _, fcl = _fwd(A, t, None, close_t)
                fnx = None
                if nxt and nxt in ACC and sid in ACC[nxt]:
                    B = ACC[nxt][sid]
                    if p_in and B["px"]:
                        fnx = 100 * (B["px"][-1] / p_in - 1)
                rows.append({"day": day, "t": t,
                             "hm": datetime.fromtimestamp(t).strftime("%H:%M"),
                             "sid": sid, "grp": grp, "partial_day": day in PARTIAL,
                             "partial_2d": (prev in PARTIAL) if prev else None,
                             **{k: r.get(k) for k in S.columns},
                             "chg_pct_grp": chg_p, "bias_pct_grp": bias_p,
                             "n_peers": len(peers), **agg,
                             "entry_px": p_in, "fwd30": f30, "fwd60": f60,
                             "fwd_close": fcl, "fwd_nextclose": fnx})
            t += TICK_SEC
        print(f"{day}: 面板累積 {len(rows)} 列", file=sys.stderr)

    df = pd.DataFrame(rows)
    for c in df.columns:
        if c not in ("day", "hm", "sid", "grp"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df.to_parquet(args.out, index=False)
    print(f"wrote {args.out}  {len(df)} 列 × {len(df.columns)} 欄")
    print(f"前瞻報酬可用率: fwd30 {df.fwd30.notna().mean():.1%} · "
          f"fwd60 {df.fwd60.notna().mean():.1%} · 收盤 {df.fwd_close.notna().mean():.1%} · "
          f"隔日收盤 {df.fwd_nextclose.notna().mean():.1%}")
    print(f"兩日尺度可用率: {df.big_2d.notna().mean():.1%}（9/29 無前一日）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
