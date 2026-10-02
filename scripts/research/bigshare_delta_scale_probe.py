#!/usr/bin/env python3
"""大戶佔比「增加量」的尺度比較:5 分鐘 vs 30 分鐘(2026-10-03)。

承接 quiet_bigflow_aggr_probe.py 的結果:大戶佔比**水準**高是扣分項(+30 分超額 -1.82、
+60 分 -3.62),jack 改提「不要水準高,要**增加**高」——水準反映的可能只是那檔股票
本來就大單多(結構性),而上升才是新資金進場。本檔比較三種變化量定義與兩種尺度。

定義(全部 PIT,只用當下往前的視窗;金額已修正 ×1000,FinMind tick 的 volume 是張):
  bs5   近 5 分鐘「單筆 ≥100 萬」金額佔比
  bs30  近 30 分鐘同上
  d_rel = bs5 − bs30          短期相對長期的超出(不需 lag,最穩)
  d_5   = bs5(t) − bs5(t−300) 5 分鐘尺度的自身變化
  d_30  = bs30(t) − bs30(t−1800) 30 分鐘尺度的自身變化

用法:PYTHONPATH=src .venv/bin/python scripts/research/bigshare_delta_scale_probe.py [--days 20]
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np

TICK = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OPEN_S, CLOSE_S = 9 * 3600, 13 * 3600 + 25 * 60
BIG_NTD, LOT = 1_000_000.0, 1000.0
HOLD = (5, 10, 30, 60)


def _sec(t):
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + float(t[6:])


def load(sid, date):
    f = TICK / f"{sid}_{date}.json"
    if not f.exists():
        return None
    try:
        arr = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    t, px, vol, ty = [], [], [], []
    for x in arr:
        tt = x.get("Time") or ""
        k = str(x.get("TickType", "0"))
        if len(tt) < 8 or k not in ("1", "2"):
            continue
        s = _sec(tt)
        if s < OPEN_S or s > CLOSE_S:
            continue
        t.append(s); px.append(float(x["deal_price"])); vol.append(float(x.get("volume") or 0))
        ty.append(1 if k == "1" else 2)
    if len(t) < 300:
        return None
    t = np.asarray(t); o = np.argsort(t, kind="stable")
    return t[o], np.asarray(px)[o], np.asarray(vol)[o], np.asarray(ty, dtype=np.int8)[o]


def ratio(t, amt, sel, win):
    cs = np.concatenate([[0.0], np.cumsum(np.where(sel, amt, 0.0))])
    ca = np.concatenate([[0.0], np.cumsum(amt)])
    lo = np.searchsorted(t, t - win, side="left"); hi = np.arange(len(t)) + 1
    tot = ca[hi] - ca[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(tot > 0, (cs[hi] - cs[lo]) / tot, np.nan), hi - lo


def lagged(t, v, lag):
    """取 lag 秒前的 v 值;不足 lag 秒的位置給 nan（避免用開盤前的不完整視窗）。"""
    j = np.searchsorted(t, t - lag, side="left") - 1
    out = np.full(len(t), np.nan)
    ok = (j >= 0) & (t - t[0] >= lag)
    out[ok] = v[j[ok]]
    return out


def compress(t, px, win):
    lo = np.searchsorted(t, t - win, side="left"); hi = np.arange(len(t)) + 1
    out = np.full(len(t), np.nan)
    for i in range(len(t)):
        a, b = lo[i], hi[i]
        if b - a < 3:
            continue
        seg = px[a:b]; mx, mn = seg.max(), seg.min(); mid = (mx + mn) / 2
        if mid > 0:
            out[i] = (mx - mn) / mid * 1e4
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=20)
    args = ap.parse_args()
    files = sorted(TICK.glob("*_2026-*.json"))
    dates = sorted({f.name.split("_")[-1][:-5] for f in files})[-args.days:]
    sids = sorted({f.name.split("_")[0] for f in files})
    print(f"取樣 {len(dates)} 日({dates[0]} ~ {dates[-1]}) × 最多 {len(sids)} 檔", flush=True)

    cols = {k: [] for k in ("bs5", "bs30", "d_rel", "d_5", "d_30", "cp", "r5", "n1", "day", "sec")}
    FWD = {m: [] for m in HOLD}
    pairs = 0
    for d in dates:
        for sid in sids:
            tk = load(sid, d)
            if tk is None:
                continue
            pairs += 1
            t, px, vol, ty = tk
            amt = px * vol * LOT
            big = amt >= BIG_NTD
            bs5, _ = ratio(t, amt, big, 300)
            bs30, _ = ratio(t, amt, big, 1800)
            r5, _ = ratio(t, amt, ty == 1, 300)
            _r1, n1 = ratio(t, amt, ty == 1, 60)
            cp = compress(t, px, 300)
            d5 = bs5 - lagged(t, bs5, 300)
            d30 = bs30 - lagged(t, bs30, 1800)
            keep = np.concatenate([[True], np.diff(np.floor(t / 10)) > 0])
            m = keep & np.isfinite(bs5) & np.isfinite(bs30) & np.isfinite(cp)
            idx = np.where(m)[0]
            if not len(idx):
                continue
            for k, v in (("bs5", bs5), ("bs30", bs30), ("d_rel", bs5 - bs30), ("d_5", d5),
                         ("d_30", d30), ("cp", cp), ("r5", r5), ("n1", n1), ("sec", t)):
                cols[k].append(v[m])
            cols["day"].append(np.full(len(idx), dates.index(d), dtype=np.int16))
            for mins in HOLD:
                tgt = np.searchsorted(t, t[idx] + mins * 60, side="left")
                ok = tgt < len(t)
                f = np.full(len(idx), np.nan)
                f[ok] = (px[tgt[ok]] / px[idx[ok]] - 1) * 1e4
                FWD[mins].append(f)
    X = {k: np.concatenate(v) for k, v in cols.items()}
    fwd = {k: np.concatenate(v) for k, v in FWD.items()}
    key = X["day"].astype(np.int64) * 100000 + (X["sec"] // 60).astype(np.int64)
    exf = {}
    for mins, v in fwd.items():
        ok = np.isfinite(v)
        o = np.argsort(key[ok], kind="stable")
        k2 = key[ok][o]; v2 = v[ok][o]
        b = np.concatenate([[0], np.flatnonzero(np.diff(k2)) + 1, [len(k2)]])
        mu = np.empty(len(k2))
        for i in range(len(b) - 1):
            mu[b[i]:b[i + 1]] = v2[b[i]:b[i + 1]].mean()
        base = np.full(len(v), np.nan)
        base[np.flatnonzero(ok)[o]] = mu
        exf[mins] = v - base
    liq = X["n1"] >= 10
    print(f"檔日 {pairs}、取樣點 {len(liq):,}（活躍 {liq.sum():,}）\n")

    def table(name, var, mask, label):
        v = var[mask]
        qs = np.nanpercentile(v, [20, 40, 60, 80])
        print(f"\n-- {name}（{label}，分位切 5 桶）")
        print(f"   {'區間':<20}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
        edges = [-np.inf, *qs, np.inf]
        for i in range(5):
            m = mask & (var > edges[i]) & (var <= edges[i + 1])
            if m.sum() < 300:
                continue
            row = f"   ({edges[i]:+.3f},{edges[i + 1]:+.3f}]".ljust(23) + f"{m.sum():>9,}"
            for mins in HOLD:
                row += f"{np.nanmean(exf[mins][m]):>9.2f}"
            print(row)

    print("==== A. 三種變化量定義 × 全樣本（超額 bps）====")
    for nm, k in (("d_rel = bs5 − bs30（短期超出長期）", "d_rel"),
                  ("d_5  = bs5 的 5 分鐘變化", "d_5"),
                  ("d_30 = bs30 的 30 分鐘變化", "d_30")):
        table(nm, X[k], liq & np.isfinite(X[k]), "全樣本")

    q_cp = np.nanpercentile(X["cp"][liq], 25)
    quiet = liq & (X["cp"] <= q_cp)
    print(f"\n\n==== B. 同樣三種,限「盤整」子集（壓縮 ≤ p25 = {q_cp:.1f} bps）====")
    for nm, k in (("d_rel", "d_rel"), ("d_5", "d_5"), ("d_30", "d_30")):
        table(nm, X[k], quiet & np.isfinite(X[k]), "盤整")

    print("\n\n==== C. 水準 vs 變化量：直接對照（+30 分超額，盤整子集）====")
    print(f"   {'變數':<28}{'最低桶':>10}{'最高桶':>10}{'高−低':>10}")
    for nm, k in (("bs5（水準）", "bs5"), ("bs30（水準）", "bs30"),
                  ("d_rel（5m 超出 30m）", "d_rel"), ("d_5（5m 變化）", "d_5"), ("d_30（30m 變化）", "d_30")):
        v = X[k]; m0 = quiet & np.isfinite(v)
        if m0.sum() < 1000:
            continue
        lo_q, hi_q = np.nanpercentile(v[m0], [20, 80])
        a = m0 & (v <= lo_q); b = m0 & (v >= hi_q)
        va, vb = np.nanmean(exf[30][a]), np.nanmean(exf[30][b])
        print(f"   {nm:<28}{va:>10.2f}{vb:>10.2f}{vb - va:>10.2f}")
    print("\n   成本:掛買一等 22 bps;不排隊吃賣一約 41 bps。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
