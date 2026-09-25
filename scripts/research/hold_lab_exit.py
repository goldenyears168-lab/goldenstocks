#!/usr/bin/env python3
"""出場只用分數——變體探索(2026-09-25)。共用 biglot_hold_lab 口徑。

基準:事件 make_events(L,"score",15)、entry="close"、exit_score_le(0)。對照:固定 60 分。
判準(KEEP):OOS 與 IS 同號、|t_OOS|≥2,且相對基準在「超額/次、SD、持有時間」至少一項改善 ≥10%、其他項不惡化 >20%。
執行:cd goldenstocks && PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/hold_lab_exit.py
"""
from __future__ import annotations
import sys, time
import numpy as np, pandas as pd
from biglot_hold_lab import load_lab, make_events, simulate, path, exit_score_le, cl_t


# ---------- 出場規則 ----------
def exit_grace_bad(th=0.0, grace=2, cap=12):
    """寬限期內只看壞標籤(bad 為真即出),寬限後看分數 ≤th。"""
    def f(p):
        for k in range(1, cap + 1):
            if k <= grace:
                if bool(p["bad"][k]): return k
            elif p["s"][k] <= th: return k
        return cap
    return f


def exit_rel_drop(drop=10.0, cap=12):
    """分數較進場時掉 ≥drop 就出(不論絕對值)。"""
    def f(p):
        for k in range(1, cap + 1):
            if p["s"][k] <= p["s"][0] - drop: return k
        return cap
    return f


def exit_peak_dd(dd=10.0, cap=12):
    """分數較進場後最高值回落 ≥dd 就出。"""
    def f(p):
        hi = p["s"][0]
        for k in range(1, cap + 1):
            hi = max(hi, p["s"][k])
            if hi - p["s"][k] >= dd: return k
        return cap
    return f


def exit_combo_le_or_rel(th=0.0, drop=15.0, cap=12):
    def f(p):
        for k in range(1, cap + 1):
            if p["s"][k] <= th or p["s"][k] <= p["s"][0] - drop: return k
        return cap
    return f


# ---------- 統計 ----------
def stats(res: pd.DataFrame) -> dict:
    out = {}
    for lab, m in (("IS", res["is"]), ("OOS", ~res["is"])):
        g = res[m & res["filled"]]
        if len(g) < 20:
            out[lab] = None; continue
        mu, t = cl_t(g["ex"].values, g["date"].values)
        out[lab] = dict(n=len(g), ex=mu, t=t, raw=g["ret"].mean(), win=(g["ret"] > 0).mean() * 100, hold=g["hold"].mean(), sd=g["ret"].std())
    g = res[res["filled"]]
    out["ALL"] = dict(n=len(g), ex=g["ex"].mean(), raw=g["ret"].mean(), win=(g["ret"] > 0).mean() * 100, hold=g["hold"].mean(), sd=g["ret"].std())
    return out


def verdict(st: dict, base: dict) -> str:
    a, b = st["IS"], st["OOS"]
    if a is None or b is None: return "UNDECIDED(n)"
    if np.sign(a["ex"]) != np.sign(b["ex"]) or abs(b["t"]) < 2: return "DROP"
    ba, bb, bA = base["IS"], base["OOS"], base["ALL"]
    # 三項:超額/次(取 IS、OOS 各自比較)、SD、持有時間(全樣本)
    ex_gain = min(a["ex"] / ba["ex"], b["ex"] / bb["ex"]) - 1          # 兩期都要
    sd_gain = 1 - st["ALL"]["sd"] / bA["sd"]
    hold_gain = 1 - st["ALL"]["hold"] / bA["hold"]
    ex_worse = max(1 - a["ex"] / ba["ex"], 1 - b["ex"] / bb["ex"])
    sd_worse = st["ALL"]["sd"] / bA["sd"] - 1
    hold_worse = st["ALL"]["hold"] / bA["hold"] - 1
    improved = [g >= 0.10 for g in (ex_gain, sd_gain, hold_gain)]
    worsened = [w > 0.20 for w in (ex_worse, sd_worse, hold_worse)]
    if any(improved) and not any(worsened): return "KEEP"
    if any(worsened): return "DROP"
    return "UNDECIDED"


HDR = f"{'變體':34s} | {'IS超額':>7s} {'t':>5s} {'原始':>6s} {'勝':>4s} | {'OOS超額':>7s} {'t':>5s} {'原始':>6s} {'勝':>4s} | {'均持':>4s} {'SD':>4s} | 判決"


def row(label, st, base=None):
    def seg(x):
        return f"{x['ex']:+7.1f} {x['t']:+5.1f} {x['raw']:+6.1f} {x['win']:3.0f}%" if x else "   n<20             "
    v = verdict(st, base) if base is not None else "(基準)"
    return f"{label:34s} | {seg(st['IS'])} | {seg(st['OOS'])} | {st['ALL']['hold']:4.0f} {st['ALL']['sd']:4.0f} | {v}"


def main():
    t0 = time.time()
    L = load_lab(); ev = make_events(L, "score", 15)
    print(f"事件 {len(ev)}(IS {int(ev['is_'].sum())} / OOS {int((~ev['is_']).sum())})  load {time.time()-t0:.0f}s\n")
    run = lambda rule, entry="close", e=ev: simulate(L, e, rule, entry=entry)

    base_res = run(exit_score_le(0)); base = stats(base_res)
    fix60_res = run(lambda p: 12); fix60 = stats(fix60_res)

    print("=" * 140); print("總表(超額 bps/次 · t=日聚類 · SD=每筆原始報酬 · 判決相對基準 分數≤0)"); print("=" * 140)
    print(HDR)
    print(row("基準 分數≤0(連1)", base))
    print(row("對照 固定60分", fix60, base))

    # 0. 診斷:分數路徑的持續性(s[k]>0 的比例、s[k]==0 的比例、面板有無列)
    print("\n-- 0. 診斷:進場後第 k 桶分數狀態(事件層)--")
    hr = L["d"].pivot_table(index=["sid", "date"], columns="bucket", values="s", aggfunc="size").reindex(columns=L["buckets"]).notna()
    S = []; H = []
    for e in ev.itertuples():
        p = path(L, e.sid, e.date, e.bucket, 12)
        if p is None: continue
        S.append(p["s"]); i = L["buckets"].index(e.bucket); H.append(hr.loc[(e.sid, e.date)].iloc[i:i + 13].values)
    S = np.array(S); H = np.array(H)
    print("k(桶)      " + " ".join(f"{k:5d}" for k in range(13)))
    print("P(s>0)     " + " ".join(f"{(S[:, k] > 0).mean()*100:4.0f}%" for k in range(13)))
    print("P(s>5)     " + " ".join(f"{(S[:, k] > 5).mean()*100:4.0f}%" for k in range(13)))
    print("P(s==0)    " + " ".join(f"{(S[:, k] == 0).mean()*100:4.0f}%" for k in range(13)))
    print("P(s<0)     " + " ".join(f"{(S[:, k] < 0).mean()*100:4.0f}%" for k in range(13)))
    print("P(面板有列) " + " ".join(f"{H[:, k].mean()*100:4.0f}%" for k in range(13)))
    print("s 均值     " + " ".join(f"{S[:, k].mean():5.1f}" for k in range(13)))
    kx = base_res.loc[base_res["filled"], "k_exit"].values.astype(int)
    print(f"基準出場桶分布: " + ", ".join(f"k={k}:{(kx==k).mean()*100:.0f}%" for k in range(1, 13) if (kx == k).mean() >= 0.005))
    sx = np.array([S[i, k] for i, k in enumerate(kx)]); hx = np.array([H[i, k] for i, k in enumerate(kx)])
    print(f"基準出場桶的分數: ==0 {(sx==0).mean()*100:.0f}% / <0 {(sx<0).mean()*100:.0f}% / >0(cap) {(sx>0).mean()*100:.0f}%;出場桶面板無列(s 被補 0) {(~hx).mean()*100:.0f}%")

    print("\n-- 0b. 對照:固定短持有 / 嚴格 <0 --")
    for cap in (1, 2, 3):
        print(row(f"對照 固定{cap*5}分", stats(run(lambda p, c=cap: c)), base))
    print(row("分數<0(嚴格,0 不出)", stats(run(exit_score_le(-0.01))), base))
    print(row("分數<0 ∨ 面板無列視同 0 → 同基準", base))

    # 1. 門檻 × 連續桶
    print("\n-- 1. 出場門檻 × 連續桶 --")
    T1 = {}
    for th in (5, 0, -5, -10):
        for c in (1, 2):
            st = stats(run(exit_score_le(th, consecutive=c))); T1[(th, c)] = st
            print(row(f"分數≤{th:+d} 連{c}桶", st, base))

    # 2. 寬限期
    print("\n-- 2. 寬限期(前 g 桶不看分數)/ 寬限期只看壞標籤 --")
    T2 = {}
    for g in (1, 2, 3):
        st = stats(run(exit_score_le(0, grace=g))); T2[("g", g)] = st
        print(row(f"寬限{g*5}分 後分數≤0", st, base))
    for g in (1, 2, 3):
        st = stats(run(exit_grace_bad(0, grace=g))); T2[("gb", g)] = st
        print(row(f"寬限{g*5}分內只看壞標籤 後≤0", st, base))

    # 3. 相對變化
    print("\n-- 3. 分數相對變化 --")
    T3 = {}
    for dr in (10, 15):
        st = stats(run(exit_rel_drop(dr))); T3[("rel", dr)] = st
        print(row(f"較進場掉≥{dr} 出", st, base))
    for dd in (10, 15):
        st = stats(run(exit_peak_dd(dd))); T3[("dd", dd)] = st
        print(row(f"較峰值回落≥{dd} 出", st, base))
    st = stats(run(exit_combo_le_or_rel(0, 15))); T3["combo"] = st
    print(row("分數≤0 ∨ 較進場掉≥15", st, base))

    # 5. 時間上限
    print("\n-- 5. 時間上限 × 分數≤0 --")
    T5 = {}
    for cap in (6, 9, 12):
        st = stats(run(exit_score_le(0, cap=cap))); T5[cap] = st
        print(row(f"分數≤0 cap {cap*5}分", st, base))
    for cap in (6, 9):
        st = stats(run(lambda p, c=cap: c)); T5[("fix", cap)] = st
        print(row(f"對照 固定{cap*5}分", st, base))

    # 6. 進場分數層級
    print("\n-- 6. 依進場分數層級(基準相對於各層自己的 分數≤0)--")
    strata = (("15–20", (ev["s"] >= 15) & (ev["s"] < 20)), ("20–30", (ev["s"] >= 20) & (ev["s"] < 30)), ("≥30", ev["s"] >= 30))
    for name, m in strata:
        e = ev[m]
        if len(e) < 40:
            print(f"[{name}] n={len(e)} 太少"); continue
        b_st = stats(run(exit_score_le(0), e=e))
        print(f"[{name}] 事件 {len(e)}(IS {int(e['is_'].sum())}/OOS {int((~e['is_']).sum())})")
        print(row(f"  {name} 分數≤0(層基準)", b_st))
        for th in (5, -5, -10):
            print(row(f"  {name} 分數≤{th:+d}", stats(run(exit_score_le(th), e=e)), b_st))
        print(row(f"  {name} 分數≤0 連2桶", stats(run(exit_score_le(0, consecutive=2), e=e)), b_st))
        print(row(f"  {name} 寬限10分 後≤0", stats(run(exit_score_le(0, grace=2), e=e)), b_st))
        print(row(f"  {name} 較進場掉≥15", stats(run(exit_rel_drop(15), e=e)), b_st))
        print(row(f"  {name} 固定60分", stats(run(lambda p: 12, e=e)), b_st))

    # 4. 反事實
    print("\n-- 4. 反事實 --")
    # (a) 用固定 60 分 sim 的 min_s(k=1..12 全程最低分)與 r60
    f = fix60_res[fix60_res["filled"]].copy()
    ever_le0 = f["min_s"] <= 0
    big = f["r60"] > 50
    print(f"(a) 抱 60 分 r60>+50 的事件 n={int(big.sum())}({big.mean()*100:.0f}% 全體):其中中途分數曾 ≤0 的比例 = {ever_le0[big].mean()*100:.1f}%(全體事件曾 ≤0 比例 {ever_le0.mean()*100:.1f}%)")
    for lab, m in (("IS", f["is"]), ("OOS", ~f["is"])):
        bb = big & m
        print(f"    {lab}: r60>+50 n={int(bb.sum())} 曾≤0 {ever_le0[bb].mean()*100:.1f}%")
    for lo, hi in ((50, 100), (100, 200), (200, 1e9)):
        mm = (f["r60"] > lo) & (f["r60"] <= hi)
        print(f"    r60 ∈({lo},{hi if hi<1e9 else '∞'}] n={int(mm.sum())} 曾≤0 {ever_le0[mm].mean()*100:.1f}%")
    # 曾≤0 vs 從未≤0 的 r60 分布
    for lab, m in (("曾≤0", ever_le0), ("從未≤0", ~ever_le0)):
        g = f[m]
        mu, t = cl_t(g["ex60"].values, g["date"].values)
        print(f"    {lab}: n={len(g)} r60 均 {g['r60'].mean():+.1f} ex60 均 {mu:+.1f}(t{t:+.1f}) r60>+50 比例 {(g['r60']>50).mean()*100:.0f}% r60<−50 比例 {(g['r60']<-50).mean()*100:.0f}%")
    # 錯過的:曾≤0 且 r60>+50 —— 分數出場實拿 vs 60 分
    b = base_res[base_res["filled"]].set_index(["sid", "date", "bucket"])
    f2 = f.set_index(["sid", "date", "bucket"])
    j = f2.join(b[["ret", "ex", "hold"]].rename(columns={"ret": "ret_s", "ex": "ex_s", "hold": "hold_s"}), how="inner")
    missed = j[(j["min_s"] <= 0) & (j["r60"] > 50)]
    print(f"    「錯過」集合(曾≤0 ∧ r60>+50)n={len(missed)}:分數出場實拿 原始 {missed['ret_s'].mean():+.1f}/超額 {missed['ex_s'].mean():+.1f}(均持 {missed['hold_s'].mean():.0f} 分) vs 抱60 原始 {missed['r60'].mean():+.1f}/超額 {missed['ex60'].mean():+.1f}")
    trapped = j[(j["min_s"] <= 0) & (j["r60"] < -50)]
    print(f"    「躲過」集合(曾≤0 ∧ r60<−50)n={len(trapped)}:分數出場實拿 原始 {trapped['ret_s'].mean():+.1f}/超額 {trapped['ex_s'].mean():+.1f}(均持 {trapped['hold_s'].mean():.0f} 分) vs 抱60 原始 {trapped['r60'].mean():+.1f}/超額 {trapped['ex60'].mean():+.1f}")
    both = j[j["min_s"] <= 0]
    print(f"    全部曾≤0 n={len(both)}:分數出場 原始 {both['ret_s'].mean():+.1f}/超額 {both['ex_s'].mean():+.1f} vs 抱60 原始 {both['r60'].mean():+.1f}/超額 {both['ex60'].mean():+.1f} → 淘汰淨效果(超額) {both['ex_s'].mean()-both['ex60'].mean():+.1f} bps/次")

    # (b) 分數一直 >0 但價格一度 <0
    rows = []
    for e in ev.itertuples():
        p = path(L, e.sid, e.date, e.bucket, 12)
        if p is None: continue
        r = p["r"]; s = p["s"]
        rows.append(dict(sid=e.sid, date=e.date, bucket=e.bucket, is_=e.is_, min_s=float(np.min(s[1:])), min_r=float(np.min(r[1:])),
                         min_r_k=int(np.argmin(r[1:]) + 1), r60=float(r[12]), ex60=float(r[12] - p["ur"][12]), s0=float(s[0])))
    P = pd.DataFrame(rows).set_index(["sid", "date", "bucket"])
    P = P.join(b[["ret", "ex", "hold"]].rename(columns={"ret": "ret_s", "ex": "ex_s", "hold": "hold_s"}), how="inner")
    alive = P["min_s"] > 0
    dip = P["min_r"] < 0
    dip50 = P["min_r"] <= -50
    print(f"\n(b) 分數 60 分內從未 ≤0 的事件 n={int(alive.sum())}({alive.mean()*100:.0f}%);其中價格一度 <0 n={int((alive&dip).sum())}、一度 ≤−50bps n={int((alive&dip50).sum())}")
    for lab, m in (("分數一直>0 ∧ 價格一度<0", alive & dip), ("分數一直>0 ∧ 價格一度≤−50", alive & dip50), ("分數一直>0 ∧ 價格從未<0", alive & ~dip),
                   ("分數曾≤0 ∧ 價格一度<0", ~alive & dip), ("分數曾≤0 ∧ 價格從未<0", ~alive & ~dip)):
        g = P[m]
        if len(g) < 10: print(f"    {lab}: n={len(g)} 太少"); continue
        dd = g.index.get_level_values("date").values; mu60, t60 = cl_t(g["ex60"].values, dd); mus, ts = cl_t(g["ex_s"].values, dd)
        print(f"    {lab:28s} n={len(g):4d}  抱60:原始 {g['r60'].mean():+6.1f} 超額 {mu60:+6.1f}(t{t60:+4.1f}) 勝 {(g['r60']>0).mean()*100:3.0f}% | 分數出場:原始 {g['ret_s'].mean():+6.1f} 超額 {mus:+6.1f}(t{ts:+4.1f}) 均持 {g['hold_s'].mean():3.0f}分 | 最低價位均 {g['min_r'].mean():+5.0f}bps@{g['min_r_k'].mean()*5:.0f}分")
    # (b') 重新定義:分數在前 K 桶都 >0(=基準規則至少抱 K 桶)且前 K 桶價格一度 <0
    rows = []
    for e in ev.itertuples():
        p = path(L, e.sid, e.date, e.bucket, 12)
        if p is None: continue
        r = p["r"]; s_ = p["s"]
        for K in (1, 2, 3):
            rows.append(dict(sid=e.sid, date=e.date, bucket=e.bucket, K=K, is_=e.is_, alive=bool(np.min(s_[1:K + 1]) > 0), dip=bool(np.min(r[1:K + 1]) < 0),
                             dip50=bool(np.min(r[1:K + 1]) <= -50), r60=float(r[12]), ex60=float(r[12] - p["ur"][12]), rK=float(r[K]), exK=float(r[K] - p["ur"][K])))
    Q = pd.DataFrame(rows).set_index(["sid", "date", "bucket"]).join(b[["ret", "ex", "hold"]].rename(columns={"ret": "ret_s", "ex": "ex_s", "hold": "hold_s"}), how="inner")
    print("\n(b') 分數前 K 桶都 >0(基準至少抱 K 桶)× 前 K 桶價格是否一度 <0 → 之後結果")
    for K in (1, 2, 3):
        q = Q[Q["K"] == K]
        print(f"  K={K}({K*5}分):分數撐過 n={int(q['alive'].sum())}({q['alive'].mean()*100:.0f}%)")
        for lab, m in ((f"撐過∧價一度<0", q["alive"] & q["dip"]), (f"撐過∧價一度≤−50", q["alive"] & q["dip50"]), (f"撐過∧價從未<0", q["alive"] & ~q["dip"]),
                       (f"未撐過∧價一度<0", ~q["alive"] & q["dip"]), (f"未撐過∧價從未<0", ~q["alive"] & ~q["dip"])):
            g = q[m]
            if len(g) < 15: print(f"    {lab:16s} n={len(g)} 太少"); continue
            dd = g.index.get_level_values("date").values
            mu60, t60 = cl_t(g["ex60"].values, dd); mus, ts = cl_t(g["ex_s"].values, dd); muK, tK = cl_t(g["exK"].values, dd)
            print(f"    {lab:16s} n={len(g):4d} 勝60 {(g['r60']>0).mean()*100:3.0f}% | 抱60 超額 {mu60:+6.1f}(t{t60:+4.1f}) 原始 {g['r60'].mean():+6.1f} | 分數出場 超額 {mus:+6.1f}(t{ts:+4.1f}) 原始 {g['ret_s'].mean():+6.1f} 持 {g['hold_s'].mean():3.0f}分 | 第K桶即出 超額 {muK:+6.1f}(t{tK:+4.1f})")
        for lab, m in (("IS", q["is_"]), ("OOS", ~q["is_"])):
            g = q[q["alive"] & q["dip"] & m]
            if len(g) < 15: continue
            dd = g.index.get_level_values("date").values; mu60, t60 = cl_t(g["ex60"].values, dd); mus, ts = cl_t(g["ex_s"].values, dd)
            print(f"      {lab} 撐過∧價一度<0 n={len(g)} 抱60 超額 {mu60:+.1f}(t{t60:+.1f}) | 分數出場 超額 {mus:+.1f}(t{ts:+.1f})")

    for lab, m in (("IS", P["is_"]), ("OOS", ~P["is_"])):
        g = P[alive & dip & m]
        if len(g) < 10: continue
        mu60, t60 = cl_t(g["ex60"].values, g.index.get_level_values("date").values)
        print(f"      {lab} 分數一直>0∧價一度<0 n={len(g)} 抱60 超額 {mu60:+.1f}(t{t60:+.1f}) 原始 {g['r60'].mean():+.1f} 勝 {(g['r60']>0).mean()*100:.0f}%")

    # 7. 配對檢定:同事件 分數出場 − 固定持有(日聚類 t)
    print("\n-- 7. 配對檢定(同事件,分數≤0 出場 − 固定 N 分;超額 bps;日聚類 t)--")
    bi = base_res[base_res["filled"]].set_index(["sid", "date", "bucket"])
    for lab, rule in (("固定5分", lambda p: 1), ("固定10分", lambda p: 2), ("固定15分", lambda p: 3), ("固定60分", lambda p: 12), ("分數≤+5", exit_score_le(5)), ("分數≤−5", exit_score_le(-5))):
        o = run(rule); o = o[o["filled"]].set_index(["sid", "date", "bucket"])
        j = bi[["ex", "is"]].join(o[["ex"]].rename(columns={"ex": "ex2"}), how="inner"); diff = (j["ex"] - j["ex2"]).values
        dd = j.index.get_level_values("date").values
        mu, t = cl_t(diff, dd)
        m_is = j["is"].values; mu_i, t_i = cl_t(diff[m_is], dd[m_is]); mu_o, t_o = cl_t(diff[~m_is], dd[~m_is])
        print(f"  基準 − {lab:8s}: 全 {mu:+6.1f}(t{t:+4.1f}) | IS {mu_i:+6.1f}(t{t_i:+4.1f}) | OOS {mu_o:+6.1f}(t{t_o:+4.1f})")
    print("  超額/SD(OOS):" + ", ".join(f"{lab} {st['OOS']['ex']/st['ALL']['sd']:.3f}" for lab, st in (("基準", base), ("固定60", fix60), ("≤+5", T1[(5, 1)]), ("≤−5", T1[(-5, 1)]), ("≤−10", T1[(-10, 1)]), ("固定30", T5[("fix", 6)]))))

    print(f"\n完成 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
