#!/usr/bin/env python3
"""進場集合探索(2026-09-25):出場固定 exit_score_le(0);基準 make_events(L,"score",15)。
變體:分數門檻 / 單一狀態格 / 組合過濾 / 時段 / 每檔集中度。判準:OOS 與 IS 同號、|t_OOS|≥2、OOS 原始扣 25bps>0。
"""
from __future__ import annotations
import numpy as np, pandas as pd
from biglot_hold_lab import load_lab, make_events, simulate, exit_score_le, cl_t

COST = 25.0
EXIT = exit_score_le(0)


def stats(res):
    out = {}
    for lab, m in (("IS", res["is"]), ("OOS", ~res["is"])):
        g = res[m & res["filled"]]
        if len(g) < 20:
            out[lab] = None; continue
        mu, t = cl_t(g["ex"].values, g["date"].values)
        out[lab] = dict(n=len(g), ex=mu, t=t, raw=g["ret"].mean(), raw_c=g["ret"].mean() - COST,
                        win=(g["ret"] > 0).mean() * 100, hold=g["hold"].mean(), sd=g["ret"].std(), ndays=g["date"].nunique())
    return out


def verdict(st):
    a, b = st["IS"], st["OOS"]
    if a is None or b is None: return "UNDECIDED(n<20)"
    if b["n"] < 30: return "UNDECIDED(OOS n<30)"
    same = np.sign(a["ex"]) == np.sign(b["ex"])
    if same and abs(b["t"]) >= 2 and b["raw_c"] > 0 and b["ex"] > 0: return "KEEP"
    if (not same) or (abs(b["t"]) >= 2 and b["ex"] <= 0): return "DROP"
    return "UNDECIDED"


def fmt(st, lab):
    def one(k):
        s = st[k]
        if s is None: return f"{k} n<20"
        return (f"{k} n={s['n']:5d}/{s['ndays']:3d}日 超額{s['ex']:+6.1f}(t{s['t']:+4.1f}) 原始{s['raw']:+6.1f} 扣25→{s['raw_c']:+6.1f} "
                f"勝{s['win']:3.0f}% 持{s['hold']:3.0f}分 SD{s['sd']:4.0f}")
    return f"{lab:34s} | {one('IS')} | {one('OOS')} | {verdict(st)}"


def run(L, label, ev, rows, store=None):
    res = simulate(L, ev, EXIT)
    st = stats(res); rows.append((label, st)); print(fmt(st, label), flush=True)
    if store is not None: store[label] = res
    return res


def concentration(res, lab):
    for k, m in (("IS", res["is"]), ("OOS", ~res["is"]), ("ALL", res["is"] | ~res["is"])):
        g = res[m & res["filled"]]
        tot = g["ex"].sum(); by = g.groupby("sid")["ex"].sum().sort_values(ascending=False)
        top5 = by.head(5); share = top5.sum() / tot * 100 if tot > 0 else np.nan
        # 去掉前 5 檔後重算
        g2 = g[~g["sid"].isin(top5.index)]
        mu2, t2 = cl_t(g2["ex"].values, g2["date"].values) if len(g2) >= 20 else (np.nan, np.nan)
        print(f"  [{lab}] {k}: 超額總和 {tot:+.0f} bps·事件 | 前5檔 {', '.join(f'{s}:{v:+.0f}' for s, v in top5.items())} 占 {share:.0f}% "
              f"| 剔前5檔 n={len(g2)} 超額 {mu2:+.1f}(t{t2:+.1f}) | 檔數 {by.size} | 事件數前5檔 {g['sid'].value_counts().head(5).to_dict()}"
              + (" | UNDECIDED(前5檔>50%)" if share > 50 else ""))


def main():
    L = load_lab(); d = L["d"]
    print("面板列", len(d), "日", d["date"].nunique(), "檔", d["sid"].nunique(), "| 出場=分數≤0 | 成本 25 bps 只扣原始欄")
    print("狀態格欄:", ", ".join(L["names"]))
    rows = []; store = {}

    print("\n=== 1. 分數門檻 ===")
    for th in (10, 15, 20, 25, 30):
        run(L, f"分數≥{th}", make_events(L, "score", th), rows, store)

    print("\n=== 2. 單一狀態格(kind=state,不看分數) ===")
    for nm in ("急跌·真空", "急跌·賣壓未竭", "急跌·大戶接∧未竭", "急跌≤−600", "純機構", "蓄勢深", "逆弱≤−100"):
        if nm not in L["names"]:
            print(f"{nm}: 欄位不存在,跳過"); continue
        ev = make_events(L, "state", name=nm)
        print(f"   (進場桶分數均值 {ev['s'].mean():+.1f}, 分數≥15 占 {(ev['s']>=15).mean()*100:.0f}%)")
        run(L, f"格:{nm}", ev, rows, store)

    print("\n=== 3. 組合過濾(分數≥15 ∧ …) ===")
    g = d.groupby(["sid", "date"])
    s_prev = g["s"].shift(1)
    rising = (d["s"] > s_prev).fillna(False)
    masks = {
        "∧ self_kill": d["self_kill"].astype(bool),
        "∧ ~follow": ~d["follow"].astype(bool),
        "∧ ~bad": ~d["bad"].astype(bool),
        "∧ mkt_r30s≥0": d["mkt_r30s"] >= 0,
        "∧ w5≤−20": d["w5"] <= -20,
        "∧ 分數較前桶上升": rising,
        "∧ 分數較前桶上升(前桶<15)": rising & (s_prev < 15).fillna(False),
    }
    for lab, m in masks.items():
        run(L, f"≥15 {lab}", make_events(L, "score", 15, extra_mask=m), rows, store)
    # 補集對照
    print("   -- 補集對照 --")
    for lab, m in (("∧ ~self_kill", ~d["self_kill"].astype(bool)), ("∧ follow", d["follow"].astype(bool)),
                   ("∧ bad", d["bad"].astype(bool)), ("∧ mkt_r30s<0", d["mkt_r30s"] < 0), ("∧ w5>−20", d["w5"] > -20)):
        run(L, f"≥15 {lab}", make_events(L, "score", 15, extra_mask=m), rows, store)

    print("\n=== 4. 時段(分數≥15,依進場桶) ===")
    b = d["bucket"]
    for lab, m in (("09:30–10:00", (b >= "09:30") & (b < "10:00")), ("10:00–12:00", (b >= "10:00") & (b < "12:00")),
                   ("12:00–12:20", (b >= "12:00") & (b <= "12:20"))):
        run(L, f"≥15 時段 {lab}", make_events(L, "score", 15, extra_mask=m), rows, store)

    print("\n=== 5. 每檔集中度 ===")
    keeps = [(lab, st) for lab, st in rows if verdict(st) == "KEEP"]
    ranked = sorted(keeps, key=lambda x: x[1]["OOS"]["t"], reverse=True)
    for lab, st in [("分數≥15", dict(rows)["分數≥15"])] + ranked[:4]:
        concentration(store[lab], lab)

    print("\n=== 總表 ===")
    print(f"{'變體':34s} | {'IS n':>6s} {'超額':>6s} {'t':>5s} {'扣25原始':>8s} | {'OOS n':>6s} {'超額':>6s} {'t':>5s} {'扣25原始':>8s} {'持':>4s} | 判決")
    for lab, st in rows:
        a, o = st["IS"], st["OOS"]
        fa = f"{a['n']:6d} {a['ex']:+6.1f} {a['t']:+5.1f} {a['raw_c']:+8.1f}" if a else f"{'n<20':>28s}"
        fo = f"{o['n']:6d} {o['ex']:+6.1f} {o['t']:+5.1f} {o['raw_c']:+8.1f} {o['hold']:4.0f}" if o else f"{'n<20':>33s}"
        print(f"{lab:34s} | {fa} | {fo} | {verdict(st)}")


if __name__ == "__main__" and __import__("sys").argv[1:] != ["extra"]:
    main()


# ---- 6. 補驗:KEEP 變體的延遲 5 分進場 + 日集中度 + ≥20 扣掉 ≤−600 的增量集合 ----
def day_concentration(res, lab):
    for k, m in (("IS", res["is"]), ("OOS", ~res["is"])):
        g = res[m & res["filled"]]
        tot = g["ex"].sum(); by = g.groupby("date")["ex"].sum().sort_values(ascending=False)
        share = by.head(5).sum() / tot * 100 if tot > 0 else np.nan
        pos_days = (by > 0).mean() * 100
        print(f"  [{lab}] {k}: 日數 {by.size} 前5日占 {share:.0f}% 正日比 {pos_days:.0f}% 前5日 {', '.join(f'{d_}:{v:+.0f}' for d_, v in by.head(5).items())}")


def extra():
    L = load_lab(); d = L["d"]; rows = []
    print("\n=== 6a. 延遲 5 分進場(entry=next)對照 ===")
    variants = {
        "分數≥15": make_events(L, "score", 15),
        "分數≥20": make_events(L, "score", 20),
        "格:急跌≤−600": make_events(L, "state", name="急跌≤−600"),
        "≥15 ∧ self_kill": make_events(L, "score", 15, extra_mask=d["self_kill"].astype(bool)),
        "≥15 ∧ mkt_r30s≥0": make_events(L, "score", 15, extra_mask=d["mkt_r30s"] >= 0),
        "≥15 ∧ w5>−20": make_events(L, "score", 15, extra_mask=d["w5"] > -20),
        "≥15 時段 09:30–10:00": make_events(L, "score", 15, extra_mask=(d["bucket"] >= "09:30") & (d["bucket"] < "10:00")),
        "≥20 ∧ ~急跌≤−600(增量集合)": make_events(L, "score", 20, extra_mask=~d["it:急跌≤−600"].astype(bool)),
    }
    for lab, ev in variants.items():
        for ent in ("close", "next"):
            rule = EXIT if ent == "close" else exit_score_le(0, cap=11)  # next:路徑少 1 桶,cap=11 避免越界
            res = simulate(L, ev, rule, entry=ent); st = stats(res); rows.append((f"{lab} [{ent}]", st))
            print(fmt(st, f"{lab} [{ent}]"))
    print("\n=== 6b. 日集中度(entry=close) ===")
    for lab in ("分數≥20", "格:急跌≤−600", "≥15 ∧ self_kill", "≥15 ∧ mkt_r30s≥0", "≥15 ∧ w5>−20", "≥15 時段 09:30–10:00"):
        day_concentration(simulate(L, variants[lab], EXIT), lab)
    print("\n=== 6c. 急跌≤−600 事件的日期分布(OOS) ===")
    ev = variants["格:急跌≤−600"]; print(ev[~ev["is_"]].groupby("date").size().to_string())


if __name__ == "__main__" and __import__("sys").argv[1:] == ["extra"]:
    extra()
