#!/usr/bin/env python3
"""大戶−散戶「差額拉大」的盤中探查(2026-10-03)。

jack 構想:同時看大戶佔比與散戶佔比,找**差額逐漸拉大**(大戶進、散戶退)的狀態。

兩個先避開的坑:
1. **桶互斥** —— 高價股單筆 1 張可能就 ≥100 萬,會同時被算進大戶桶與散戶桶
   ([[quiet-accumulation-retail-arrival-verdict]] 記過的雙重記帳)。本檔定義:
   大戶 = amt ≥ BIG_NTD;散戶 = vol == 1 **且** amt < BIG_NTD。
2. **單位** —— FinMind tick 的 volume 是張不是股,金額要 ×1000(2026-10-03 對帳 2330)。

已知先驗([[biglot-diff-normalization-verdict]],隔夜線):控制大戶佔比後,差值是 -41/t-1.9,
即「差值」不如單純大戶佔比 —— 但那是**水準**、且是隔夜尺度。本檔測的是盤中的**變化量**。

變數(全部 PIT):
  bs5/bs30   大戶佔比(5/30 分鐘)
  rs5/rs30   散戶佔比
  diff5/30   bs − rs
  d_diff     diff5 − diff30      ← 「差額拉大」:短期差額超出長期多少
  d_rs       rs5 − rs30          ← 「散戶佔比下降」:負值代表短期散戶退場

用法:PYTHONPATH=src .venv/bin/python scripts/research/bigretail_diff_widen_probe.py [--days 20]
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

    keys = ("bs5", "rs5", "diff5", "d_diff", "d_rs", "d_bs", "cp", "n1", "day", "sec")
    cols = {k: [] for k in keys}
    FWD = {m: [] for m in HOLD}
    pairs, dbl = 0, 0
    for d in dates:
        for sid in sids:
            tk = load(sid, d)
            if tk is None:
                continue
            pairs += 1
            t, px, vol, ty = tk
            amt = px * vol * LOT
            big = amt >= BIG_NTD
            retail = (vol == 1) & ~big          # 互斥:1 張但金額已達大戶門檻的不算散戶
            dbl += int(((vol == 1) & big).sum())
            bs5, _ = ratio(t, amt, big, 300); bs30, _ = ratio(t, amt, big, 1800)
            rs5, _ = ratio(t, amt, retail, 300); rs30, _ = ratio(t, amt, retail, 1800)
            _r1, n1 = ratio(t, amt, ty == 1, 60)
            cp = compress(t, px, 300)
            diff5, diff30 = bs5 - rs5, bs30 - rs30
            keep = np.concatenate([[True], np.diff(np.floor(t / 10)) > 0])
            m = keep & np.isfinite(bs5) & np.isfinite(bs30) & np.isfinite(rs5) & np.isfinite(cp)
            idx = np.where(m)[0]
            if not len(idx):
                continue
            for k, v in (("bs5", bs5), ("rs5", rs5), ("diff5", diff5), ("d_diff", diff5 - diff30),
                         ("d_rs", rs5 - rs30), ("d_bs", bs5 - bs30), ("cp", cp), ("n1", n1), ("sec", t)):
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
    q_cp = np.nanpercentile(X["cp"][liq], 25)
    quiet = liq & (X["cp"] <= q_cp)
    print(f"檔日 {pairs}、取樣點 {len(liq):,}（活躍 {liq.sum():,}、盤整 {quiet.sum():,}）")
    print(f"桶互斥檢查:單筆 1 張但金額 ≥{BIG_NTD:,.0f} 的成交 {dbl:,} 筆（已從散戶桶排除）\n")

    def table(name, var, mask):
        v = var[mask]
        qs = np.nanpercentile(v, [20, 40, 60, 80])
        print(f"\n-- {name}")
        print(f"   {'區間':<24}{'n':>9}" + "".join(f"{f'+{m}分':>9}" for m in HOLD))
        edges = [-np.inf, *qs, np.inf]
        for i in range(5):
            m = mask & (var > edges[i]) & (var <= edges[i + 1])
            if m.sum() < 300:
                continue
            row = f"   ({edges[i]:+.3f},{edges[i + 1]:+.3f}]".ljust(27) + f"{m.sum():>9,}"
            for mins in HOLD:
                row += f"{np.nanmean(exf[mins][m]):>9.2f}"
            print(row)

    print("==== A. 散戶佔比與其變化（盤整子集，超額 bps）====")
    table("rs5 散戶佔比（水準）", X["rs5"], quiet & np.isfinite(X["rs5"]))
    table("d_rs 散戶佔比變化（負=散戶退場）", X["d_rs"], quiet & np.isfinite(X["d_rs"]))

    print("\n\n==== B. 差額與其變化（盤整子集）====")
    table("diff5 = 大戶−散戶（水準）", X["diff5"], quiet & np.isfinite(X["diff5"]))
    table("d_diff 差額拉大（正=短期差額超出長期）", X["d_diff"], quiet & np.isfinite(X["d_diff"]))

    print("\n\n==== C. 三個變數的直接對照（+30 分超額，盤整子集，最高桶−最低桶）====")
    print(f"   {'變數':<34}{'最低桶':>10}{'最高桶':>10}{'高−低':>10}")
    for nm, k, note in (("rs5（散戶佔比水準）", "rs5", ""),
                        ("d_rs（散戶佔比變化）", "d_rs", "負向較好代表散戶退場有利"),
                        ("diff5（大戶−散戶 水準）", "diff5", ""),
                        ("d_bs（大戶佔比變化，前輪基準）", "d_bs", "前輪 +1.00"),
                        ("d_diff（差額拉大）", "d_diff", "")):
        v = X[k]; m0 = quiet & np.isfinite(v)
        lo_q, hi_q = np.nanpercentile(v[m0], [20, 80])
        a, b = m0 & (v <= lo_q), m0 & (v >= hi_q)
        va, vb = np.nanmean(exf[30][a]), np.nanmean(exf[30][b])
        print(f"   {nm:<34}{va:>10.2f}{vb:>10.2f}{vb - va:>10.2f}   {note}")

    print("\n\n==== D. 組合：盤整 ∧ 散戶退場 ∧ 差額拉大 ====")
    rs_dn = X["d_rs"] <= np.nanpercentile(X["d_rs"][quiet], 20)
    dw = X["d_diff"] >= np.nanpercentile(X["d_diff"][quiet], 80)
    for lab, m in (("盤整（基準）", quiet),
                   ("盤整 ∧ 散戶退場", quiet & rs_dn),
                   ("盤整 ∧ 差額拉大", quiet & dw),
                   ("★ 盤整 ∧ 散戶退場 ∧ 差額拉大", quiet & rs_dn & dw)):
        if m.sum() < 300:
            print(f"   {lab:<30} n={m.sum():>8,}  n<300"); continue
        print(f"   {lab:<30} n={m.sum():>8,}  " + "".join(f"{np.nanmean(exf[mins][m]):>9.2f}" for mins in HOLD))
    print(f"   {'(欄位:+5/+10/+30/+60 分)':<30}")
    print("\n   成本:掛買一等 22 bps;不排隊吃賣一約 41 bps。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
