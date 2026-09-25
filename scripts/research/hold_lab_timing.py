#!/usr/bin/env python3
"""持有期研究——進場時機與進場價(2026-09-25)。出場固定 exit_score_le(0),事件 make_events(L,"score",15)。

背景:秒級檢定顯示真空只持續 5–25 秒、報酬在進入後 30 秒內兌現;桶收價是賣方砸出來的價,實務抓不到。
五題:1 進場價(close/next/dip:N + 未成交機會成本) 2 確認進場 3 首次 vs 再觸發 4 進場桶 w5 分箱 5 延後 1/2 桶但分數仍 ≥15。
判準:OOS 與 IS 同號、|t_OOS|≥2、扣 25 bps 後 OOS 原始 >0 → KEEP;否則 DROP/UNDECIDED。
用法:cd goldenstocks && PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/hold_lab_timing.py
"""
from __future__ import annotations
import time
import numpy as np, pandas as pd
from biglot_hold_lab import load_lab, make_events, path, exit_score_le, cl_t, IS_END

TH, COST, H = 15.0, 25.0, 12


def get_paths(L, ev):
    """事件 → 預算路徑(避免每個變體重算)。"""
    out = {}
    for e in ev.itertuples():
        p = path(L, e.sid, e.date, e.bucket, H + 6) or path(L, e.sid, e.date, e.bucket, H)   # 多留 6 桶給延後進場者仍可抱滿 60 分
        if p is not None:
            out[(e.sid, e.date, e.bucket)] = p
    return out


def sim(ev, P, entry="close", cond=None, k_delay=None):
    """與 biglot_hold_lab.simulate 同義,但吃預算路徑;另支援 k_delay(第 k 桶收價進場,cond(p) 為真才進)。
    未成交/未進場列 filled=False,並附從事件桶收價抱 60 分的 r60/ex60(機會成本)。"""
    rows = []
    for e in ev.itertuples():
        p = P.get((e.sid, e.date, e.bucket))
        if p is None: continue
        base = {"sid": e.sid, "date": e.date, "bucket": e.bucket, "is": e.is_, "s0": e.s, "w5_0": float(p["w5"][0]),
                "r60_close": float(p["r"][H]), "ex60_close": float(p["r"][H] - p["ur"][H])}
        k0 = 0; entry_px = p["px"][0]
        if k_delay is not None:
            k0 = k_delay
            if cond is not None and not cond(p):
                rows.append({**base, "filled": False, "ret": np.nan, "ex": np.nan, "hold": 0}); continue
            entry_px = p["px"][k0]
        elif entry == "next":
            k0 = 1; entry_px = p["px"][1]
        elif entry.startswith("dip:"):
            dip = float(entry.split(":")[1]); target = p["px"][0] * (1 - dip / 1e4); k0 = None
            for k in range(1, 7):
                if p["px"][k] <= target: k0 = k; entry_px = target; break
            if k0 is None:
                rows.append({**base, "filled": False, "ret": np.nan, "ex": np.nan, "hold": 0}); continue
        q = {k: v[k0:] for k, v in p.items()}
        q["r"] = (q["px"] / entry_px - 1) * 1e4; q["ur"] = q["ur"] - q["ur"][0]
        cap = min(H, len(q["r"]) - 1)                       # 進場後最多抱 12 桶(60 分);尾盤資料不足者以可用桶數為上限
        kx = exit_score_le(0, cap=cap)(q); kx = min(kx, cap)
        rows.append({**base, "filled": True, "ret": q["r"][kx], "ex": q["r"][kx] - q["ur"][kx], "hold": kx * 5, "k0": k0})
    return pd.DataFrame(rows)


def stats(res):
    """IS/OOS:n、超額均、日群聚 t、原始均、勝率、均持;成交率;未成交機會成本(r60/ex60 from close)。"""
    o = {}
    for lab, m in (("IS", res["is"]), ("OOS", ~res["is"])):
        g = res[m & res["filled"]]
        if len(g) < 20:
            o.update({f"{lab}_n": len(g), f"{lab}_ex": np.nan, f"{lab}_t": np.nan, f"{lab}_ret": np.nan, f"{lab}_win": np.nan, f"{lab}_hold": np.nan}); continue
        mu, t = cl_t(g["ex"].values, g["date"].values)
        o.update({f"{lab}_n": len(g), f"{lab}_ex": mu, f"{lab}_t": t, f"{lab}_ret": g["ret"].mean(), f"{lab}_win": (g["ret"] > 0).mean() * 100, f"{lab}_hold": g["hold"].mean()})
        u = res[m & ~res["filled"]]
        o[f"{lab}_unf_n"] = len(u); o[f"{lab}_unf_r60"] = u["r60_close"].mean() if len(u) else np.nan; o[f"{lab}_unf_ex60"] = u["ex60_close"].mean() if len(u) else np.nan
    o["fill"] = res["filled"].mean() * 100 if len(res) else np.nan; o["n_ev"] = len(res)
    return o


def verdict(o):
    if np.isnan(o.get("OOS_t", np.nan)) or np.isnan(o.get("IS_ex", np.nan)): return "UNDECIDED(n)"
    same = np.sign(o["IS_ex"]) == np.sign(o["OOS_ex"]); sig = abs(o["OOS_t"]) >= 2; net = o["OOS_ret"] - COST > 0
    if same and sig and net and o["OOS_ex"] > 0: return "KEEP"
    if (not same and sig) or (o["OOS_ex"] < 0 and sig): return "DROP"
    if same and sig and o["OOS_ex"] > 0 and not net: return "DROP(成本)"
    return "UNDECIDED"


def line(label, o):
    def blk(lab):
        if np.isnan(o[f"{lab}_ex"]): return f"{lab} n={o[f'{lab}_n']:<4d}<20"
        return (f"{lab} n={o[f'{lab}_n']:5d} 超額 {o[f'{lab}_ex']:+6.1f}(t{o[f'{lab}_t']:+4.1f}) 原始 {o[f'{lab}_ret']:+6.1f} "
                f"淨 {o[f'{lab}_ret']-COST:+6.1f} 勝 {o[f'{lab}_win']:3.0f}% 持 {o[f'{lab}_hold']:3.0f}分")
    extra = f" | 成交 {o['fill']:3.0f}%" if o["fill"] < 99.5 else ""
    return f"{label:34s} | {blk('IS')} | {blk('OOS')}{extra} | {verdict(o)}"


def unfilled_line(label, o):
    parts = []
    for lab in ("IS", "OOS"):
        n = o.get(f"{lab}_unf_n", 0)
        parts.append(f"{lab} 未成交 n={n:5d} 抱60分 r60 {o.get(f'{lab}_unf_r60', np.nan):+6.1f} 超額60 {o.get(f'{lab}_unf_ex60', np.nan):+6.1f}" if n else f"{lab} 未成交 n=0")
    return f"{'  ↳ ' + label + ' 機會成本':34s} | " + " | ".join(parts)


def main():
    t0 = time.time()
    L = load_lab(); print(f"面板載入 {time.time()-t0:.0f}s;IS ≤{IS_END}")
    ev1 = make_events(L, "score", TH, first_only=True); ev0 = make_events(L, "score", TH, first_only=False)
    P = get_paths(L, ev0)
    print(f"事件:first_only=True {len(ev1)}(有路徑 {sum((e.sid,e.date,e.bucket) in P for e in ev1.itertuples())}),first_only=False {len(ev0)}(有路徑 {len(P)});出場=分數≤0;成本判準 {COST:.0f} bps")
    rows = []

    def run(label, ev, **kw):
        o = stats(sim(ev, P, **kw)); o["label"] = label; rows.append(o); print(line(label, o)); return o

    print("\n==== 1. 進場價:桶收 / 下一桶收(延遲5分) / 深單代理 dip:N(之後6桶收價觸及進場價−N bps 才成交;桶收價≤目標=保守成交口徑)====")
    run("1a entry=close(桶收價,樂觀)", ev1, entry="close")
    run("1b entry=next(下一桶收,延遲5分)", ev1, entry="next")
    for n in (30, 50, 100):
        o = run(f"1c entry=dip:{n}", ev1, entry=f"dip:{n}"); print(unfilled_line(f"dip:{n}", o))
    # 診斷:事件桶收→下一桶收(首 5 分)的原始/超額報酬 = close 與 next 之差的來源
    r1 = pd.DataFrame([{"is": e.is_, "date": e.date, "r1": P[k]["r"][1], "ex1": P[k]["r"][1] - P[k]["ur"][1]} for e in ev1.itertuples() if (k := (e.sid, e.date, e.bucket)) in P])
    for lab, m in (("IS", r1["is"]), ("OOS", ~r1["is"])):
        g = r1[m]; mu, t = cl_t(g["ex1"].values, g["date"].values)
        print(f"  首 5 分(事件桶收→下一桶收){lab} n={len(g)} 原始 {g['r1'].mean():+.1f} 超額 {mu:+.1f}(t{t:+.1f}) 首5分為正比率 {(g['r1']>0).mean()*100:.0f}%")

    print("\n==== 2. 確認進場:s0≥15 且下一桶 s≥15 才進(以下一桶收價進);對照=不確認但同樣延遲(1b)====")
    conf = lambda p: p["s"][1] >= TH
    run("2a 確認(s[1]≥15)→next 收價進", ev1, k_delay=1, cond=conf)
    run("2b 未過確認(s[1]<15)→next 收價進", ev1, k_delay=1, cond=lambda p: p["s"][1] < TH)
    run("2c 確認→再等一桶(px[2])進", ev1, k_delay=2, cond=conf)
    run("2d 確認(s[1]≥15)·dip:30 從 px[1] 起算", ev1[[ (P.get((e.sid,e.date,e.bucket)) is not None and P[(e.sid,e.date,e.bucket)]["s"][1] >= TH) for e in ev1.itertuples()]], entry="dip:30")

    print("\n==== 3. 首次 vs 再觸發(first_only=True=每段連續成立取首桶;False=每桶都算)====")
    key = lambda df: list(zip(df["sid"], df["date"], df["bucket"]))
    ev0 = ev0.copy(); k1 = set(key(ev1)); ev0["run_start"] = [k in k1 for k in key(ev0)]
    first_day = ev0.groupby(["sid", "date"])["bucket"].transform("min") == ev0["bucket"]
    ev0["kind"] = np.where(first_day, "當日首次", np.where(ev0["run_start"], "再觸發(隔桶後重新達標)", "延續(連續桶內非首桶)"))
    for entry in ("close", "next"):
        run(f"3 first_only=True  entry={entry}", ev1, entry=entry)
        run(f"3 first_only=False entry={entry}", ev0, entry=entry)
        for kd in ("當日首次", "再觸發(隔桶後重新達標)", "延續(連續桶內非首桶)"):
            run(f"3   {kd} entry={entry}", ev0[ev0["kind"] == kd], entry=entry)
    print("  事件桶次序(first_only=False):", ev0["kind"].value_counts().to_dict())

    print("\n==== 4. 進場當桶價格位置:事件桶 w5(近5分報酬 bps)分箱;entry=close 與 next 各看 ====")
    w5 = pd.Series({(e.sid, e.date, e.bucket): P[(e.sid, e.date, e.bucket)]["w5"][0] for e in ev1.itertuples() if (e.sid, e.date, e.bucket) in P})
    ev1w = ev1.copy(); ev1w["w5_0"] = [w5.get(k, np.nan) for k in key(ev1w)]
    bins = [(-1e9, -60, "w5≤−60"), (-60, -20, "−60<w5≤−20"), (-20, 0, "−20<w5≤0"), (0, 1e9, "w5>0")]
    for entry in ("close", "next"):
        for lo, hi, lab in bins:
            sub = ev1w[(ev1w["w5_0"] > lo) & (ev1w["w5_0"] <= hi)]
            run(f"4 {lab:12s} entry={entry}", sub, entry=entry)

    print("\n==== 5. 分數達標後多久進(桶級):延後 k 桶、只在該桶分數仍 ≥15 時以該桶收價進;對照秒級「等 30 秒再進為負」====")
    run("5 k=0 立即(=1a)", ev1, entry="close")
    run("5 k=1 s[1]≥15(=2a)", ev1, k_delay=1, cond=lambda p: p["s"][1] >= TH)
    run("5 k=2 s[2]≥15", ev1, k_delay=2, cond=lambda p: p["s"][2] >= TH)
    run("5 k=2 s[1]≥15∧s[2]≥15", ev1, k_delay=2, cond=lambda p: p["s"][1] >= TH and p["s"][2] >= TH)
    run("5 k=1 無條件(=1b)", ev1, k_delay=1)
    run("5 k=2 無條件", ev1, k_delay=2)
    run("5 k=3 s[3]≥15", ev1, k_delay=3, cond=lambda p: p["s"][3] >= TH)
    # 分數仍≥15 的比率(達標後 k 桶)
    ks = {k: np.mean([P[(e.sid, e.date, e.bucket)]["s"][k] >= TH for e in ev1.itertuples() if (e.sid, e.date, e.bucket) in P]) * 100 for k in (1, 2, 3)}
    print("  達標後分數仍 ≥15 的比率:", {f"k={k}": f"{v:.0f}%" for k, v in ks.items()})

    print("\n==== 總表(超額 bps;t=日群聚;淨=OOS 原始−25)====")
    T = pd.DataFrame(rows)
    T["verdict"] = [verdict(o) for o in rows]; T["OOS_net"] = T["OOS_ret"] - COST
    cols = ["label", "IS_n", "IS_ex", "IS_t", "IS_ret", "OOS_n", "OOS_ex", "OOS_t", "OOS_ret", "OOS_net", "OOS_win", "OOS_hold", "fill", "verdict"]
    with pd.option_context("display.width", 250, "display.max_rows", 200, "display.float_format", lambda x: f"{x:+.1f}" if abs(x) < 1e4 else f"{x:.0f}"):
        print(T[cols].to_string(index=False))
    print(f"\n耗時 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
