#!/usr/bin/env python3
"""持有期研究:多檔同時達標與容量(2026-09-25)。

口徑:make_events(L,"score",15) · entry="close" · exit_score_le(0) · 每筆固定 100 萬名目(1 bps=100 元)。
組合層:依 (date,bucket) 時序吃事件,持有到 k_exit;同時持倉上限 K;同桶多檔的選法可換。
IS ≤2026-06-30 / OOS 07-01~。每日損益基準=面板全部交易日(無單日記 0)。
執行:cd goldenstocks && PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/hold_lab_portfolio.py
"""
from __future__ import annotations
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from biglot_hold_lab import load_lab, make_events, simulate, exit_score_le, cl_t, IS_END

RNG = np.random.default_rng(20260925)
NOTIONAL = 100.0  # 元/bps(100 萬名目)


def attach(L, res):
    """把進場桶的 sell30s / r30s / 桶序號 對回事件。"""
    d = L["d"][["sid", "date", "bucket", "sell30s", "r30s"]]
    r = res.merge(d, on=["sid", "date", "bucket"], how="left")
    bi = {b: i for i, b in enumerate(L["buckets"])}
    r["bi"] = r["bucket"].map(bi); r["k_exit"] = r["k_exit"].astype(int)
    r["nth"] = r.groupby(["sid", "date"]).cumcount() + 1
    return r


def portfolio(r, K, pick="first", reenter=False, window=None, equal_split=False, seed=None):
    """回傳每筆成交(含 weight)DataFrame。pick: first|score|sell30s|r30s|random。"""
    rows = []
    if window is not None:
        r = r[(r["bucket"] >= window[0]) & (r["bucket"] <= window[1])]
    rng = np.random.default_rng(seed) if seed is not None else RNG
    for date, g in r.groupby("date", sort=True):
        open_pos = []  # (exit_bi, sid)
        for bi, gb in g.groupby("bi", sort=True):
            open_pos = [(x, s) for x, s in open_pos if x > bi]  # 出場桶收=可再進
            if not reenter:
                held = {s for _, s in open_pos}; gb = gb[~gb["sid"].isin(held)]
            if len(gb) == 0: continue
            free = np.inf if K is None else K - len(open_pos)
            if free <= 0: continue
            if pick == "score": gb = gb.sort_values("s0", ascending=False, kind="stable")
            elif pick == "sell30s": gb = gb.assign(_k=gb["sell30s"].fillna(-1)).sort_values("_k", ascending=False, kind="stable")
            elif pick == "r30s": gb = gb.assign(_k=gb["r30s"].fillna(0)).sort_values("_k", ascending=True, kind="stable")
            elif pick == "random": gb = gb.iloc[rng.permutation(len(gb))]
            # first: 保持 sid 序(面板無桶內先後,≈任意)
            if equal_split and len(gb) > free:
                w = free / len(gb); take = gb
            else:
                w = 1.0; take = gb.iloc[: int(min(free, len(gb)))]
            for e in take.itertuples():
                rows.append((date, e.bucket, e.sid, e.ret, e.ex, e.k_exit, w, e.s0))
                open_pos.append((bi + e.k_exit, e.sid))
    return pd.DataFrame(rows, columns=["date", "bucket", "sid", "ret", "ex", "k_exit", "w", "s0"])


def daily(tr, dates):
    tr = tr.assign(pnl=tr["ret"] * tr["w"] * NOTIONAL)
    p = tr.groupby("date")["pnl"].sum().reindex(dates).fillna(0.0)
    n = tr.groupby("date")["w"].sum().reindex(dates).fillna(0.0)
    return p, n


def stats(p, n):
    if len(p) == 0 or p.std() == 0: return dict(EV=0, SD=0, SR=0, pos=0, worst=0, npd=0, tot=0)
    return dict(EV=p.mean(), SD=p.std(), SR=p.mean() / p.std(), pos=(p > 0).mean() * 100, worst=p.min(), npd=n.mean(), tot=p.sum())


def fmt(s):
    return f"EV {s['EV']:+7.0f} SD {s['SD']:6.0f} SR {s['SR']:+5.2f} 正日 {s['pos']:3.0f}% 最差 {s['worst']:+7.0f} 筆/日 {s['npd']:4.1f} 總 {s['tot']/1e4:+6.1f}萬"


def report(label, tr, dates_is, dates_oos, out):
    p_is, n_is = daily(tr[tr["date"] <= IS_END], dates_is); p_oos, n_oos = daily(tr[tr["date"] > IS_END], dates_oos)
    s_is, s_oos = stats(p_is, n_is), stats(p_oos, n_oos)
    out.append(f"{label:34s} | IS  {fmt(s_is)} | OOS {fmt(s_oos)}")
    return s_is, s_oos, p_is, p_oos


def sr_diff_ci(pa, pb, B=2000):
    """配對 day-bootstrap:Sharpe(a)−Sharpe(b) 的 90% CI。"""
    a, b = pa.values, pb.values; n = len(a); out = []
    for _ in range(B):
        idx = RNG.integers(0, n, n); x, y = a[idx], b[idx]
        if x.std() == 0 or y.std() == 0: continue
        out.append(x.mean() / x.std() - y.mean() / y.std())
    return np.percentile(out, [5, 95])


def verdict(base, new, lab, pa_is, pb_is, pa_oos, pb_oos):
    """判準:IS/OOS Sharpe 改善同號、且最差日不惡化 >20%。"""
    d_is = new[0]["SR"] - base[0]["SR"]; d_oos = new[1]["SR"] - base[1]["SR"]
    worst_ok = (new[0]["worst"] >= base[0]["worst"] * 1.2) and (new[1]["worst"] >= base[1]["worst"] * 1.2)
    ci_is = sr_diff_ci(pa_is, pb_is); ci_oos = sr_diff_ci(pa_oos, pb_oos)
    same = np.sign(d_is) == np.sign(d_oos)
    if same and d_is > 0 and d_oos > 0 and worst_ok and ci_oos[0] > 0: v = "KEEP"
    elif same and d_is < 0 and d_oos < 0: v = "DROP"
    elif not same: v = "DROP(IS/OOS 異號)"
    else: v = "UNDECIDED"
    return f"{lab:34s} ΔSR IS {d_is:+.2f} [{ci_is[0]:+.2f},{ci_is[1]:+.2f}] OOS {d_oos:+.2f} [{ci_oos[0]:+.2f},{ci_oos[1]:+.2f}] 最差日OK={worst_ok} → {v}"


def main():
    out = []; P = out.append
    L = load_lab(); dates = sorted(L["uidx"].index); dates_is = [x for x in dates if x <= IS_END]; dates_oos = [x for x in dates if x > IS_END]
    ev = make_events(L, "score", 15); res = simulate(L, ev, exit_score_le(0), entry="close")
    r = attach(L, res[res["filled"]].copy())
    P(f"事件 {len(ev)} 成交 {len(r)} 日數 {len(dates)}(IS {len(dates_is)}/OOS {len(dates_oos)}) 每日事件 {len(r)/len(dates):.1f} 均持 {r['hold'].mean():.0f} 分 k_exit 分布 {r['k_exit'].value_counts().sort_index().to_dict()}")
    ks = r["k_same"].value_counts().sort_index(); P(f"k_same 分布 {ks.to_dict()} ; 群發桶(k_same≥2)事件占 {(r['k_same']>=2).mean()*100:.0f}%")
    mu1, t1 = cl_t(r.loc[r.k_same == 1, "ex"].values, r.loc[r.k_same == 1, "date"].values); mu2, t2 = cl_t(r.loc[r.k_same >= 2, "ex"].values, r.loc[r.k_same >= 2, "date"].values)
    P(f"單發桶 超額 {mu1:+.1f}(t{t1:+.1f}, n={int((r.k_same==1).sum())}) vs 群發桶 超額 {mu2:+.1f}(t{t2:+.1f}, n={int((r.k_same>=2).sum())})")

    # ---------- 1. 容量 ----------
    P("\n== 1. 容量:同時持倉上限 K(先到先做,同檔持有中不重複進) ==")
    cap = {}
    for K in (1, 2, 3, 5, None):
        tr = portfolio(r, K); cap[K] = report(f"K={K if K else '∞'}", tr, dates_is, dates_oos, out)
    # 每日最大同時持倉需求(K=∞)
    trinf = portfolio(r, None); need = []
    for date, g in trinf.groupby("date"):
        bi = {b: i for i, b in enumerate(L["buckets"])}; occ = np.zeros(len(L["buckets"]))
        for e in g.itertuples():
            i0 = bi[e.bucket]; occ[i0:i0 + e.k_exit] += 1
        need.append(occ.max())
    need = pd.Series(need, index=trinf["date"].unique())
    P(f"K=∞ 每日峰值同時持倉:中位 {need.median():.0f} P75 {need.quantile(.75):.0f} P90 {need.quantile(.9):.0f} 最大 {need.max():.0f};K=3 吃到的總損益占 K=∞ 的 {(cap[3][0]['tot']+cap[3][1]['tot'])/(cap[None][0]['tot']+cap[None][1]['tot'])*100:.0f}%,K=5 占 {(cap[5][0]['tot']+cap[5][1]['tot'])/(cap[None][0]['tot']+cap[None][1]['tot'])*100:.0f}%")
    P(f"K=∞ 年化 SR IS {cap[None][0]['SR']*np.sqrt(252):+.1f} OOS {cap[None][1]['SR']*np.sqrt(252):+.1f};K=3 IS {cap[3][0]['SR']*np.sqrt(252):+.1f} OOS {cap[3][1]['SR']*np.sqrt(252):+.1f}")

    # ---------- 2. 群發桶選法 ----------
    P("\n== 2. 群發桶(k_same≥2)選法,K=2 / K=3 ==")
    for c in ("sell30s", "r30s", "s0"):
        g2 = r[r.k_same >= 2]
        q = pd.qcut(g2[c].rank(method="first"), 3, labels=["低", "中", "高"]) if g2[c].notna().sum() > 30 else None
        if q is not None:
            parts = [f"{lab}:{cl_t(g2.loc[q==lab,'ex'].values, g2.loc[q==lab,'date'].values)[0]:+.0f}(t{cl_t(g2.loc[q==lab,'ex'].values, g2.loc[q==lab,'date'].values)[1]:+.1f})" for lab in ("低", "中", "高")]
            P(f"  群發桶內 {c} 三分位 超額: " + " ".join(parts))
    sel = {}
    for K in (2, 3):
        base = None
        for pick, lab in (("first", "先到(sid序)"), ("random", "隨機序×20均"), ("score", "分數最高"), ("sell30s", "30秒主動賣最高"), ("r30s", "末30秒跌最多"), ("equal", "等權全做(名目平均分)")):
            if pick == "random":
                ps = [portfolio(r, K, "random", seed=s) for s in range(20)]
                trs = [report(f"K={K} {lab} #{s}", t, dates_is, dates_oos, []) for s, t in enumerate(ps)]
                srs_is = np.mean([x[0]["SR"] for x in trs]); srs_oos = np.mean([x[1]["SR"] for x in trs])
                P(f"K={K} {lab:26s} | IS SR {srs_is:+.2f}(min {min(x[0]['SR'] for x in trs):+.2f} max {max(x[0]['SR'] for x in trs):+.2f}) | OOS SR {srs_oos:+.2f}(min {min(x[1]['SR'] for x in trs):+.2f} max {max(x[1]['SR'] for x in trs):+.2f})")
                continue
            tr = portfolio(r, K, "first" if pick == "equal" else pick, equal_split=(pick == "equal"))
            s = report(f"K={K} {lab}", tr, dates_is, dates_oos, out); sel[(K, pick)] = s
            if base is None: base = s
        for pick, lab in (("score", "分數最高"), ("sell30s", "30秒主動賣最高"), ("r30s", "末30秒跌最多"), ("equal", "等權全做")):
            P("  判決 " + verdict(base, sel[(K, pick)], f"K={K} {lab} vs 先到", sel[(K, pick)][2], base[2], sel[(K, pick)][3], base[3]))

    # ---------- 3. 同檔再觸發 ----------
    P("\n== 3. 同檔當日再觸發 ==")
    for nth, lab in ((1, "首次"), (2, "第二次"), (3, "第三次+")):
        g = r[r.nth == nth] if nth < 3 else r[r.nth >= 3]
        for isl, m in (("IS", g.date <= IS_END), ("OOS", g.date > IS_END)):
            gg = g[m]
            if len(gg) >= 10:
                mu, t = cl_t(gg["ex"].values, gg["date"].values); P(f"  first_only=True {lab:6s} {isl:3s} n={len(gg):4d} 超額 {mu:+6.1f}(t{t:+.1f}) 原始 {gg['ret'].mean():+6.1f} 勝 {(gg['ret']>0).mean()*100:.0f}%")
            else: P(f"  first_only=True {lab:6s} {isl:3s} n={len(gg)} <10")
    ev2 = make_events(L, "score", 15, first_only=False); res2 = simulate(L, ev2, exit_score_le(0), entry="close"); r2 = attach(L, res2[res2["filled"]].copy())
    g = r2[r2.nth >= 2]
    for isl, m in (("IS", g.date <= IS_END), ("OOS", g.date > IS_END)):
        gg = g[m]; mu, t = cl_t(gg["ex"].values, gg["date"].values)
        P(f"  first_only=False 第2次+(含連續桶) {isl:3s} n={len(gg):4d} 超額 {mu:+6.1f}(t{t:+.1f}) 原始 {gg['ret'].mean():+6.1f} 勝 {(gg['ret']>0).mean()*100:.0f}%")
    b3 = cap[3]
    a = report("K=3 只做每檔當日首次", portfolio(r[r.nth == 1], 3), dates_is, dates_oos, out)
    b = report("K=3 first_only=True(基準)", portfolio(r, 3), dates_is, dates_oos, out)
    c = report("K=3 first_only=True 允許加碼(持有中再進)", portfolio(r, 3, reenter=True), dates_is, dates_oos, out)
    e = report("K=3 first_only=False(連續桶也進)", portfolio(r2, 3), dates_is, dates_oos, out)
    P("  判決 " + verdict(b3, a, "只做首次 vs 基準", a[2], b3[2], a[3], b3[3]))
    P("  判決 " + verdict(b3, c, "允許加碼 vs 基準", c[2], b3[2], c[3], b3[3]))
    P("  判決 " + verdict(b3, e, "first_only=False vs 基準", e[2], b3[2], e[3], b3[3]))

    # ---------- 4. 時段 ----------
    P("\n== 4. 進場時段,K=3 ==")
    for lo, hi in (("09:30", "09:55"), ("10:00", "12:00"), ("12:05", "12:20")):  # V2.5 09:30 前不計分,事件最早 09:30
        g = r[(r.bucket >= lo) & (r.bucket <= hi)]
        for isl, m in (("IS", g.date <= IS_END), ("OOS", g.date > IS_END)):
            gg = g[m]; mu, t = cl_t(gg["ex"].values, gg["date"].values) if len(gg) > 5 else (np.nan, np.nan)
            P(f"  事件層 {lo}-{hi} {isl:3s} n={len(gg):4d} 超額 {mu:+6.1f}(t{t:+.1f}) 原始 {gg['ret'].mean():+6.1f}")
    w = report("K=3 只做 10:00–12:00", portfolio(r, 3, window=("10:00", "12:00")), dates_is, dates_oos, out)
    w2 = report("K=3 只做 09:30–11:00", portfolio(r, 3, window=("09:30", "11:00")), dates_is, dates_oos, out)
    P("  判決 " + verdict(b3, w, "10:00–12:00 vs 全時段", w[2], b3[2], w[3], b3[3]))
    P("  判決 " + verdict(b3, w2, "09:30–11:00 vs 全時段", w2[2], b3[2], w2[3], b3[3]))

    # ---------- 5. 市場 regime ----------
    P("\n== 5. 市場 regime:當日宇宙報酬(uidx 末桶−1)三等分(事後分箱,非 PIT;僅診斷) ==")
    uret = (L["uidx"].iloc[:, -1] - 1) * 1e4; ter = pd.qcut(uret, 3, labels=["跌日", "平日", "漲日"])
    P(f"  三等分邊界 bps: {uret.quantile([1/3, 2/3]).round(0).to_dict()}  各 {ter.value_counts().to_dict()}")
    tr3 = portfolio(r, 3); trinf = portfolio(r, None)
    for lab in ("跌日", "平日", "漲日"):
        ds = [x for x in dates if ter.get(x) == lab]; ds_is = [x for x in ds if x <= IS_END]; ds_oos = [x for x in ds if x > IS_END]
        g = r[r.date.isin(ds)]
        for isl, m in (("IS", g.date <= IS_END), ("OOS", g.date > IS_END)):
            gg = g[m]; mu, t = cl_t(gg["ex"].values, gg["date"].values); mur, tr_ = cl_t(gg["ret"].values, gg["date"].values)
            P(f"  {lab} {isl:3s} 事件 n={len(gg):4d}({len(ds_is) if isl=='IS' else len(ds_oos)}日) 超額 {mu:+6.1f}(t{t:+.1f}) 原始 {mur:+6.1f}(t{tr_:+.1f}) 勝 {(gg['ret']>0).mean()*100:.0f}% 持 {gg['hold'].mean():.0f}分")
        for nm, tr in (("K=3", tr3), ("K=∞", trinf)):
            p_is, n_is = daily(tr[tr.date.isin(ds_is)], ds_is); p_oos, n_oos = daily(tr[tr.date.isin(ds_oos)], ds_oos)
            P(f"    {nm} 每日 | IS  {fmt(stats(p_is, n_is))} | OOS {fmt(stats(p_oos, n_oos))}")

    # ---------- 6. 集中度 ----------
    P("\n== 6. 集中度 ==")
    for nm, tr in (("K=3", tr3), ("K=∞", trinf)):
        tr = tr.assign(pnl=tr["ret"] * tr["w"] * NOTIONAL)
        shares = []; nsid = []
        for date, g in tr.groupby("date"):
            bys = g.groupby("sid")["pnl"].sum(); a = bys.abs().sort_values(ascending=False)
            shares.append(a.iloc[:5].sum() / a.sum() if a.sum() > 0 else np.nan); nsid.append(len(bys))
        shares = pd.Series(shares); nsid = pd.Series(nsid)
        tot = tr["pnl"].sum(); byday = tr.groupby("date")["pnl"].sum().sort_values(ascending=False); bysid = tr.groupby("sid")["pnl"].sum().sort_values(ascending=False)
        pos = tr.loc[tr.pnl > 0, "pnl"].sort_values(ascending=False)
        P(f"  {nm}: 每日前5檔|損益|占比 中位 {shares.median()*100:.0f}% 均 {shares.mean()*100:.0f}% >50% 的日數 {(shares>0.5).sum()}/{len(shares)}(每日檔數中位 {nsid.median():.0f});"
          f" 全樣本前5交易日占總損益 {byday.iloc[:5].sum()/tot*100:.0f}%、前5檔股票占 {bysid.iloc[:5].sum()/tot*100:.0f}%、前5%筆數占正損益 {pos.iloc[:max(1,int(len(pos)*.05))].sum()/pos.sum()*100:.0f}%;剔最好5日後總損益 {(tot-byday.iloc[:5].sum())/1e4:+.1f}萬(原 {tot/1e4:+.1f}萬)")
        P(f"    前5檔股票: {', '.join(f'{s}:{v/1e4:+.1f}萬' for s, v in bysid.iloc[:5].items())};前5日: {', '.join(f'{s}:{v/1e4:+.1f}萬' for s, v in byday.iloc[:5].items())}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
