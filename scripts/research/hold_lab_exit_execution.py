#!/usr/bin/env python3
"""出場執行方式的 tick 重放(2026-09-25,jack 提案):分數 <5 就先掛賣單在較好的價位(賣一),分數越低再改價加速;原則=提早掛、少排隊、不打市價。

事件:V2.5 ≥15 首次進入(1,013 個有逐筆)。k5 = 進場後第一個分數 <5 的桶、k0 = 第一個分數 ≤0 的桶(基準出場桶)。
出場方法(賣單成交判定:買方主動成交價 > 掛價 = 嚴格穿越(FIFO 保守);≥ 掛價 = 樂觀):
  M0 基準:k0 桶收後打買一(下一筆賣方主動成交價)。
  M1 提早掛:k5 桶收後掛在賣一(下一筆買方主動成交價);到 k0 桶收仍未成交 → 打買一。
  M2 提早掛+階梯:同 M1,但每個桶收若仍未成交且分數再降 → 撤單改掛新的賣一(失去順位);k0 → 打買一。
  M3 提早掛+1 tick:k5 掛賣一+1 tick;k0 未成交 → 打買一。
  M4 k0 才掛賣一(不提早),60 秒未成交 → 打買一。
指標:出場價相對 k0 桶收(bps)、成交率、比 M0 多拿幾 bps(配對、日聚類 t)、提早出場的機會成本(M1 成交後到 k0 桶收的價差)。
用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/hold_lab_exit_execution.py
"""
from __future__ import annotations
import sys
import numpy as np, pandas as pd
from bisect import bisect_left
from biglot_hold_lab import load_lab, make_events, simulate, exit_score_le, cl_t
from hold_lab_tick_replay import load_ticks, first_after, bsec


def tick_size(p):
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0


def sell_fill(tk, t_from, t_to, limit, strict=True):
    """(t_from, t_to] 內第一筆買方主動成交價 >(或 ≥)limit 的時間;無則 None。"""
    t, px, ty = tk; i = bisect_left(t, t_from)
    while i < len(t) and t[i] <= t_to:
        if ty[i] == "1" and (px[i] > limit if strict else px[i] >= limit): return t[i]
        i += 1
    return None


def main():
    L = load_lab(); ev = make_events(L, "score", 15); B = L["buckets"]
    base = simulate(L, ev, exit_score_le(0)); base = base[base["filled"]].rename(columns={"is": "is_"})
    S = L["paths"]["s"]; PX = L["px"]
    rows = []
    for e in base.itertuples():
        tk = load_ticks(e.sid, e.date)
        if tk is None: continue
        i0 = B.index(e.bucket); sp = S.loc[(e.sid, e.date)].values; k0 = int(e.k_exit)
        k5 = next((k for k in range(1, k0 + 1) if sp[i0 + k] < 5), k0)
        t5, t0 = bsec(B[i0 + k5]) + 300, bsec(B[i0 + k0]) + 300
        c0 = PX.loc[(e.sid, e.date)].iloc[i0 + k0]
        _, bid0 = first_after(tk, t0, "2"); bid0 = bid0 or c0
        _, ask5 = first_after(tk, t5, "1")
        if ask5 is None: continue
        rec = {"sid": e.sid, "date": e.date, "is_": e.is_, "k5": k5, "k0": k0, "same": k5 == k0, "c0": c0, "M0": bid0}
        for strict, tag in ((True, ""), (False, "_opt")):
            # M1
            tf = sell_fill(tk, t5, t0 + 60, ask5, strict); rec["M1" + tag] = ask5 if tf else bid0; rec["M1f" + tag] = tf is not None
            rec["M1_early" + tag] = (tf is not None and tf < t0)
            # M3:+1 tick
            lim3 = ask5 + tick_size(ask5); tf3 = sell_fill(tk, t5, t0 + 60, lim3, strict); rec["M3" + tag] = lim3 if tf3 else bid0; rec["M3f" + tag] = tf3 is not None
            # M2 階梯:每桶收未成交且分數再降 → 改掛新賣一
            lim, tstart, filled_px, done = ask5, t5, None, False
            for k in range(k5, k0 + 1):
                tb = bsec(B[i0 + k]) + 300; tend = t0 + 60 if k == k0 else bsec(B[i0 + k + 1]) + 300
                if k > k5:
                    if sp[i0 + k] < sp[i0 + k - 1]:
                        _, na = first_after(tk, tb, "1")
                        if na is not None: lim = na
                    tstart = tb
                tf2 = sell_fill(tk, tstart, tend, lim, strict)
                if tf2 is not None: filled_px = lim; done = True; break
            rec["M2" + tag] = filled_px if done else bid0; rec["M2f" + tag] = done
            # M4:k0 才掛賣一,60 秒
            _, ask0 = first_after(tk, t0, "1"); ask0 = ask0 or bid0
            tf4 = sell_fill(tk, t0, t0 + 60, ask0, strict); rec["M4" + tag] = ask0 if tf4 else bid0; rec["M4f" + tag] = tf4 is not None
        rows.append(rec)
    d = pd.DataFrame(rows); print(f"事件 {len(d)}(IS {d['is_'].sum()} / OOS {(~d['is_']).sum()});k5==k0(分數直接跳到≤0)佔 {d['same'].mean()*100:.0f}%;k5 均 {d['k5'].mean():.1f} 桶、k0 均 {d['k0'].mean():.1f} 桶")
    def rel(col): return (d[col] / d["c0"] - 1) * 1e4
    print(f"\n{'方法':22s}{'成交率':>7s}{'出場價 vs k0桶收(IS/OOS bps)':>28s}{'比 M0 多拿(IS)':>14s}{'t':>6s}{'(OOS)':>8s}{'t':>6s}")
    for tag, lab in (("", "嚴格穿越"), ("_opt", "≥掛價(樂觀)")):
        print(f"-- {lab}")
        for m in ("M0", "M1", "M2", "M3", "M4"):
            col = m if m == "M0" else m + tag; fcol = m + "f" + tag
            r = rel(col); diff = r - rel("M0"); fr = d[fcol].mean() * 100 if fcol in d else 100.0
            out = f"{m:22s}{fr:6.0f}%"
            for sl, mk in (("IS", d["is_"]), ("OOS", ~d["is_"])):
                out += f"{r[mk].mean():+8.1f}"
            for sl, mk in (("IS", d["is_"]), ("OOS", ~d["is_"])):
                mu, t = cl_t(diff[mk].values, d.loc[mk, "date"].values); out += f"{mu:+10.1f}{t:+6.1f}"
            print(out)
    print("\n== 提早成交的機會成本(M1 嚴格,在 k0 之前就成交者):成交價 vs k0 桶收、vs k0 買一")
    g = d[d["M1_early"]]
    for sl, mk in (("IS", g["is_"]), ("OOS", ~g["is_"])):
        gg = g[mk]; print(f"  {sl} n={len(gg)} 提早成交佔全部 {len(gg)/max(1,d['is_'].sum() if sl=='IS' else (~d['is_']).sum())*100:.0f}% | 成交價−k0桶收 {((gg['M1']/gg['c0']-1)*1e4).mean():+.1f} | 成交價−k0買一 {((gg['M1']/gg['M0']-1)*1e4).mean():+.1f}")
    print("\n== 依 k5 與 k0 的差距(桶)分箱:M1 vs M0(嚴格)")
    d["gap"] = d["k0"] - d["k5"]
    for lo, hi in ((0, 0), (1, 1), (2, 3), (4, 12)):
        g = d[(d["gap"] >= lo) & (d["gap"] <= hi)]
        if len(g) < 20: continue
        diff = (g["M1"] / g["c0"] - 1) * 1e4 - (g["M0"] / g["c0"] - 1) * 1e4; mu, t = cl_t(diff.values, g["date"].values)
        print(f"  差 {lo}~{hi} 桶 n={len(g):4d} M1 成交率 {g['M1f'].mean()*100:3.0f}% 比 M0 多拿 {mu:+.1f} (t{t:+.1f})")


if __name__ == "__main__":
    sys.exit(main())
