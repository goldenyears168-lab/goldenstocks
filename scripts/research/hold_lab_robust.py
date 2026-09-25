#!/usr/bin/env python3
"""對抗型審查(2026-09-25):設法推翻「V2.5 ≥15 進場、分數 ≤0 出場」(IS 超額 +23.1/t5.2、OOS +23.2/t6.2、均持 11 分)。

八項檢定:①隨機進場安慰劑 ②進場桶平移 ③成本敏感度 ④修剪(前5%事件/前5檔) ⑤逐月 ⑥日 bootstrap
⑦分數出場 vs 價格出場 vs 錯位分數出場 ⑧出場桶宇宙報酬(擇時是否來自市場)。
引擎:向量化重寫 simulate(entry="close")並先與共用 simulate() 對帳(必須完全一致才往下跑)。
執行:cd /Users/jackm4/goldenstocks && PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/hold_lab_robust.py
"""
from __future__ import annotations
import sys, time
import numpy as np, pandas as pd
from biglot_hold_lab import load_lab, make_events, simulate, exit_score_le, cl_t, IS_END

H = 12                # 桶數上限(60 分)
TH_IN, TH_OUT = 15.0, 0.0
N_PLACEBO, N_BOOT, SEED = 200, 1000, 20260925
rng = np.random.default_rng(SEED)


# ---------------------------------------------------------------- 向量化引擎
class Engine:
    def __init__(self, L):
        px = L["px"]; self.buckets = L["buckets"]; self.nb = len(self.buckets)
        self.idx = px.index                                    # (sid,date)
        self.P = px.values.astype(float)                       # rows × buckets
        self.S = L["paths"]["s"].reindex(px.index).fillna(0.0).values.astype(float)
        self.dates = list(L["uidx"].index); self.U = L["uidx"].values.astype(float)
        self.row_of = {k: i for i, k in enumerate(px.index)}
        self.date_row = {d: i for i, d in enumerate(self.dates)}
        self.sid_arr = px.index.get_level_values("sid").values; self.date_arr = px.index.get_level_values("date").values
        self.bidx = {b: i for i, b in enumerate(self.buckets)}
        self.max_i = self.bidx["12:20"]

    def locate(self, ev):
        r = np.array([self.row_of.get((s, d), -1) for s, d in zip(ev["sid"], ev["date"])])
        i = np.array([self.bidx[b] for b in ev["bucket"]])
        return r, i

    def windows(self, r, i, shift=0):
        """回傳 dict(r=報酬 bps, ur=宇宙 bps, s=分數, valid=遮罩)。shift 平移進場桶(−1=提前、+1=延後)。"""
        i = i + shift; ok = (r >= 0) & (i >= 0) & (i + H < self.nb)
        rr = np.where(ok, r, 0); ii = np.where(ok, i, 0)
        w = ii[:, None] + np.arange(H + 1)[None, :]
        Pw = self.P[rr[:, None], w]; Sw = self.S[rr[:, None], w]
        drow = np.array([self.date_row[d] for d in self.date_arr[rr]])
        Uw = self.U[drow[:, None], w]
        ok &= ~np.isnan(Pw).any(1) & ~np.isnan(Uw).any(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            rb = (Pw / Pw[:, :1] - 1) * 1e4; ub = (Uw / Uw[:, :1] - 1) * 1e4
        return {"r": rb, "ur": ub, "s": Sw, "valid": ok, "row": rr}

    @staticmethod
    def first_k(cond):
        """cond shape n×H(對應 k=1..H);第一個 True 的 k,否則 H。"""
        anyc = cond.any(1); return np.where(anyc, cond.argmax(1) + 1, H)

    def evaluate(self, W, kx, cost=0.0):
        n = len(kx); a = np.arange(n)
        ret = W["r"][a, kx] - cost; ur = W["ur"][a, kx]
        return pd.DataFrame({"ret": ret, "ex": ret - ur, "ur_exit": ur, "k": kx, "valid": W["valid"],
                             "sid": self.sid_arr[W["row"]], "date": self.date_arr[W["row"]]})


def exit_score(W, th=TH_OUT, lag=0):
    """lag=0 正常;lag=+1 用前一桶分數(錯位/延遲);lag=−1 用下一桶分數(偷看)。"""
    S = W["s"]
    if lag == 0:   cond = S[:, 1:H + 1] <= th
    elif lag == 1: cond = S[:, 0:H] <= th
    else:          # 偷看:k 桶用 s[k+1];最後一桶無下一桶→用自身
        nxt = np.concatenate([S[:, 2:H + 1], S[:, H:H + 1]], axis=1); cond = nxt <= th
    return Engine.first_k(cond)


def exit_price(W, th=0.0):
    return Engine.first_k(W["r"][:, 1:H + 1] <= th)


def stat(df, m=None):
    g = df[df["valid"]] if m is None else df[df["valid"] & m]
    if len(g) < 20: return dict(n=len(g), ex=np.nan, t=np.nan, raw=np.nan, win=np.nan, hold=np.nan, ur=np.nan, ndays=g["date"].nunique())
    mu, t = cl_t(g["ex"].values, g["date"].values)
    return dict(n=len(g), ex=mu, t=t, raw=g["ret"].mean(), win=(g["ret"] > 0).mean() * 100, hold=g["k"].mean() * 5, ur=g["ur_exit"].mean(), ndays=g["date"].nunique())


def line(df, label):
    is_ = df["date"] <= IS_END; a, b = stat(df, is_), stat(df, ~is_)
    f = lambda s, k: f"{k} n={s['n']:5d} 超額{s['ex']:+6.1f}(t{s['t']:+4.1f}) 原始{s['raw']:+6.1f} 勝{s['win']:3.0f}% 持{s['hold']:3.0f}分 ur出{s['ur']:+5.1f}"
    return f"{label:42s} | {f(a, 'IS')} | {f(b, 'OOS')}"


def main():
    t0 = time.time(); L = load_lab(); E = Engine(L)
    ev = make_events(L, "score", TH_IN); ev = ev.reset_index(drop=True)
    print(f"# hold_lab_robust 2026-09-25 | 事件 {len(ev)} | 面板 rows {E.P.shape[0]} × 桶 {E.nb} | load {time.time()-t0:.0f}s")

    # ---- 對帳:向量化 vs 共用 simulate()
    ref = simulate(L, ev, exit_score_le(TH_OUT)); ref = ref[ref["filled"]]
    r, i = E.locate(ev); W = E.windows(r, i); kx = exit_score(W); base = E.evaluate(W, kx)
    b = base[base["valid"]].reset_index(drop=True)
    ok = (len(b) == len(ref)) and np.allclose(b["ret"].values, ref["ret"].values, atol=1e-6) and np.allclose(b["ex"].values, ref["ex"].values, atol=1e-6) and (b["k"].values == ref["k_exit"].values).all()
    print(f"對帳 vectorized vs simulate(): n {len(b)} vs {len(ref)} | ret/ex/k_exit 全等 = {ok}")
    if not ok: sys.exit("向量化引擎與共用 simulate() 不一致,停止")
    print("\n== 基準 ==")
    print(line(base, "V2.5≥15 進場 · 分數≤0 出"))
    print(line(E.evaluate(W, np.full(len(kx), H)), "同進場 · 固定 60 分"))
    is_ev = (base["date"] <= IS_END).values
    verdicts = []

    # ================================================================ ① 安慰劑
    print("\n== ① 安慰劑:每日隨機挑同樣數量的 (sid,bucket),同一出場規則(分數≤0),重複", N_PLACEBO, "次 ==")
    # 可抽池:所有 (sid,date) 面板列 × 桶 ≤12:20,且 px/uidx 60 分窗無 NaN
    rows_all = np.repeat(np.arange(E.P.shape[0]), E.max_i + 1); i_all = np.tile(np.arange(E.max_i + 1), E.P.shape[0])
    Wall = E.windows(rows_all, i_all); pool = np.flatnonzero(Wall["valid"])
    pool_date = E.date_arr[rows_all[pool]]; pool_s0 = Wall["s"][pool, 0]
    by_date = {d: pool[pool_date == d] for d in np.unique(pool_date)}
    by_date_pos = {d: pool[(pool_date == d) & (pool_s0 > 0) & (pool_s0 < TH_IN)] for d in np.unique(pool_date)}
    need = base[base["valid"]].groupby("date").size()
    print(f"  可抽池 {len(pool)} 個 (sid,date,bucket);真實事件日 {len(need)} 日;池內分數分布: ≤0 {np.mean(pool_s0<=0)*100:.0f}% / (0,15) {np.mean((pool_s0>0)&(pool_s0<15))*100:.0f}% / ≥15 {np.mean(pool_s0>=15)*100:.0f}%")

    def placebo(pool_map, exit_fn, label):
        out = {"IS": [], "OOS": [], "ALL": [], "hold": []}
        for _ in range(N_PLACEBO):
            pick = np.concatenate([rng.choice(pool_map[d], size=min(n, len(pool_map[d])), replace=False) for d, n in need.items() if len(pool_map.get(d, [])) > 0])
            Wp = {k: (v[pick] if isinstance(v, np.ndarray) else v) for k, v in Wall.items()}
            kp = exit_fn(Wp); dfp = E.evaluate(Wp, kp); isp = dfp["date"] <= IS_END
            out["IS"].append(dfp.loc[isp, "ex"].mean()); out["OOS"].append(dfp.loc[~isp, "ex"].mean()); out["ALL"].append(dfp["ex"].mean()); out["hold"].append(dfp["k"].mean() * 5)
        res = {}
        for k in ("IS", "OOS", "ALL"):
            v = np.array(out[k]); real = base.loc[base["valid"] & ((base["date"] <= IS_END) if k == "IS" else (~(base["date"] <= IS_END)) if k == "OOS" else True), "ex"].mean()
            pct = (v < real).mean() * 100; z = (real - v.mean()) / v.std()
            res[k] = (v.mean(), np.percentile(v, 95), real, pct, z)
            print(f"  [{label}] {k:3s} 安慰劑均 {v.mean():+6.1f} 95分位 {np.percentile(v,95):+6.1f} 最大 {v.max():+6.1f} | 真實 {real:+6.1f} 位於 {pct:5.1f} 百分位 z={z:+.1f} | 安慰劑均持 {np.mean(out['hold']):.0f} 分")
        return res

    pa = placebo(by_date, exit_score, "a 全隨機進場·分數≤0出")
    pb = placebo(by_date_pos, exit_score, "b 隨機但限 0<s<15·分數≤0出")
    pc = placebo(by_date, exit_price, "c 全隨機進場·價格≤0出")
    # d:真實進場 + 隨機出場(從真實持有分布重抽)→ 出場是否有資訊
    kreal = base.loc[base["valid"], "k"].values; dd = []
    for _ in range(N_PLACEBO):
        kr = rng.choice(kreal, size=len(kx), replace=True); dfr = E.evaluate(W, kr); v = dfr[dfr["valid"]]; isr = v["date"] <= IS_END
        dd.append((v.loc[isr, "ex"].mean(), v.loc[~isr, "ex"].mean(), v["ex"].mean()))
    dd = np.array(dd); real_all = base.loc[base["valid"], "ex"].mean()
    real_is = base.loc[base["valid"] & is_ev, "ex"].mean(); real_oos = base.loc[base["valid"] & ~is_ev, "ex"].mean()
    for j, (k, real) in enumerate((("IS", real_is), ("OOS", real_oos), ("ALL", real_all))):
        v = dd[:, j]; print(f"  [d 真實進場·隨機出場(持有分布重抽)] {k:3s} 均 {v.mean():+6.1f} 95分位 {np.percentile(v,95):+6.1f} | 真實 {real:+6.1f} 位於 {(v<real).mean()*100:5.1f} 百分位 z={(real-v.mean())/v.std():+.1f}")
    v1 = "未推翻" if (pa["ALL"][3] >= 95 and pb["ALL"][3] >= 95) else ("部分" if pa["ALL"][3] >= 95 else "推翻")
    verdicts.append(("① 隨機進場安慰劑", v1, f"全隨機 95分位 {pa['ALL'][1]:+.1f} / 0<s<15 95分位 {pb['ALL'][1]:+.1f} vs 真 {pa['ALL'][2]:+.1f}"))

    # ================================================================ ② 平移
    print("\n== ② 進場桶平移(同一事件集,三者共同有效子集) ==")
    Wm = E.windows(r, i, -1); Wp1 = E.windows(r, i, +1)
    common = W["valid"] & Wm["valid"] & Wp1["valid"]
    sh = {}
    for lab, Wx in (("提前 1 桶(用未來:應更好)", Wm), ("原位", W), ("延後 1 桶(延遲:應變差)", Wp1)):
        dfx = E.evaluate(Wx, exit_score(Wx)); dfx["valid"] = common; sh[lab] = dfx; print(line(dfx, lab))
    ex_m, ex_0, ex_p = (sh[k].loc[common, "ex"].mean() for k in sh)
    mono = ex_m > ex_0 > ex_p; decay = 1 - ex_p / ex_0 if ex_0 else np.nan
    v2 = "未推翻" if (ex_0 > ex_p and decay < 0.5) else ("部分" if ex_0 > ex_p else "推翻")
    verdicts.append(("② 進場平移", v2, f"提前 {ex_m:+.1f}(反轉訊號:提前=接到下跌本身,不適用) / 原位 {ex_0:+.1f} / 延後5分 {ex_p:+.1f}(衰減 {decay*100:.0f}%,執行風險)"))

    # ================================================================ ③ 成本
    print("\n== ③ 成本敏感度(原始報酬 − cost;超額同扣) ==")
    be = {}
    for c in (0, 15, 25, 35):
        dfc = E.evaluate(W, kx, cost=c); a, b_ = stat(dfc, is_ev), stat(dfc, ~is_ev)
        print(f"  cost={c:2d} | IS 原始 {a['raw']:+6.1f} 超額 {a['ex']:+6.1f}(t{a['t']:+4.1f}) | OOS 原始 {b_['raw']:+6.1f} 超額 {b_['ex']:+6.1f}(t{b_['t']:+4.1f})")
    be["IS"] = base.loc[base["valid"] & is_ev, "ret"].mean(); be["OOS"] = base.loc[base["valid"] & ~is_ev, "ret"].mean()
    print(f"  原始報酬損益兩平成本:IS {be['IS']:.1f} bps / OOS {be['OOS']:.1f} bps(單趟進出合計)")
    v3 = "未推翻" if min(be.values()) > 25 else ("部分" if min(be.values()) > 15 else "推翻")
    v3 = "推翻(可交易性)" if min(be.values()) < 25 else v3        # 專案慣例 COST=25(hold_lab_entry.py):25 bps 下 IS/OOS 原始皆負
    verdicts.append(("③ 成本敏感度", v3, f"原始損益兩平 IS {be['IS']:.0f} / OOS {be['OOS']:.0f} bps < 專案慣例 25"))

    # ================================================================ ④ 修剪
    print("\n== ④ 修剪 ==")
    g = base[base["valid"]].copy(); g["is"] = g["date"] <= IS_END; trim = {}
    for lab, m in (("IS", g["is"]), ("OOS", ~g["is"])):
        h = g[m]; cut = h["ret"].quantile(0.95); h2 = h[h["ret"] < cut]
        mu, t = cl_t(h2["ex"].values, h2["date"].values); trim[lab] = (mu, t)
        print(f"  [{lab}] 去掉 ret 前 5%(≥{cut:+.0f} bps, {len(h)-len(h2)} 筆) 後:n={len(h2)} 超額 {mu:+6.1f}(t{t:+4.1f}) 原始 {h2['ret'].mean():+6.1f} | 原 {h['ex'].mean():+.1f}")
        by = h.groupby("sid")["ex"].sum().sort_values(ascending=False); tot = h["ex"].sum()
        loo = []
        for s in by.index:
            h3 = h[h["sid"] != s]; loo.append((s, h3["ex"].mean() - h["ex"].mean(), len(h) - len(h3)))
        loo = sorted(loo, key=lambda x: x[1])[:5]
        h5 = h[~h["sid"].isin(by.head(5).index)]; mu5, t5 = cl_t(h5["ex"].values, h5["date"].values)
        print(f"  [{lab}] 前5檔貢獻 {', '.join(f'{s}:{v:+.0f}' for s, v in by.head(5).items())} 占總和 {by.head(5).sum()/tot*100 if tot>0 else np.nan:.0f}% | 剔前5檔 n={len(h5)} 超額 {mu5:+6.1f}(t{t5:+4.1f}) | 檔數 {by.size}")
        print(f"  [{lab}] leave-one-out 最大衝擊(剔除後均值變化):{', '.join(f'{s}:{d:+.1f}({n}筆)' for s, d, n in loo)}")
        trim[lab + "5"] = (mu5, t5)
    v4 = "未推翻" if all(trim[k][0] > 0 and trim[k][1] >= 2 for k in ("IS", "OOS", "IS5", "OOS5")) else ("部分" if all(trim[k][0] > 0 for k in trim) else "推翻")
    verdicts.append(("④ 修剪", v4, f"去前5%事件 IS {trim['IS'][0]:+.1f}/OOS {trim['OOS'][0]:+.1f};剔前5檔 IS {trim['IS5'][0]:+.1f}/OOS {trim['OOS5'][0]:+.1f}"))

    # ================================================================ ⑤ 逐月
    print("\n== ⑤ 逐月 ==")
    g["ym"] = g["date"].str[:7]; pos = 0; nm = 0
    for ym, h in g.groupby("ym"):
        mu, t = cl_t(h["ex"].values, h["date"].values); nm += 1; pos += mu > 0
        print(f"  {ym} n={len(h):4d}/{h['date'].nunique():2d}日 超額 {mu:+6.1f}(t{t:+4.1f}) 原始 {h['ret'].mean():+6.1f} 勝 {(h['ret']>0).mean()*100:3.0f}% 持 {h['k'].mean()*5:3.0f}分 ur出 {h['ur_exit'].mean():+5.1f}")
    print(f"  同號(>0)月數 {pos}/{nm}")
    v5 = "未推翻" if pos == nm else ("部分" if pos >= nm - 1 else "推翻")
    verdicts.append(("⑤ 逐月", v5, f"{pos}/{nm} 月超額>0"))

    # ================================================================ ⑥ 日 bootstrap
    print("\n== ⑥ 日 bootstrap(以日為單位重抽", N_BOOT, "次) ==")
    bs = {}
    for lab, m in (("IS", g["is"]), ("OOS", ~g["is"]), ("ALL", g["is"] | ~g["is"])):
        h = g[m]; dsum = h.groupby("date")["ex"].agg(["sum", "size"]); D = len(dsum)
        S_, N_ = dsum["sum"].values, dsum["size"].values; draws = rng.integers(0, D, size=(N_BOOT, D))
        mus = S_[draws].sum(1) / N_[draws].sum(1); lo, hi = np.percentile(mus, [2.5, 97.5]); bs[lab] = (lo, hi)
        print(f"  [{lab}] {D} 日 點估 {h['ex'].mean():+6.1f} 95%CI [{lo:+6.1f}, {hi:+6.1f}] P(≤0)={np.mean(mus<=0)*100:.1f}%")
    v6 = "未推翻" if all(bs[k][0] > 0 for k in ("IS", "OOS")) else ("部分" if bs["ALL"][0] > 0 else "推翻")
    verdicts.append(("⑥ 日 bootstrap", v6, f"IS CI [{bs['IS'][0]:+.1f},{bs['IS'][1]:+.1f}] OOS CI [{bs['OOS'][0]:+.1f},{bs['OOS'][1]:+.1f}]"))

    # ================================================================ ⑦ 出場資訊
    print("\n== ⑦ 分數出場 vs 價格出場 vs 錯位分數出場(同一進場集) ==")
    alt = {"分數≤0 出(原)": exit_score(W), "價格≤進場價 出(r≤0)": exit_price(W), "分數≤0 用前一桶分數(錯位/延遲 1 桶)": exit_score(W, lag=1),
           "分數≤0 用下一桶分數(偷看 1 桶)": exit_score(W, lag=-1), "價格≤0 或 分數≤0 任一": np.minimum(exit_score(W), exit_price(W)), "固定 60 分": np.full(len(kx), H)}
    seven = {}
    for lab, k_ in alt.items():
        dfx = E.evaluate(W, k_); seven[lab] = dfx; print(line(dfx, lab))
    ks, kp_ = alt["分數≤0 出(原)"], alt["價格≤進場價 出(r≤0)"]; v = W["valid"]
    same = (ks[v] == kp_[v]).mean() * 100; corr = np.corrcoef(ks[v], kp_[v])[0, 1]
    # 出場當桶:分數≤0 時價格是否也 ≤0(分數出場 = 跌了就出?)
    a_ = np.arange(len(kx)); r_at_exit = W["r"][a_, ks]; score_exit = (ks < H) & v
    print(f"  出場桶完全相同 {same:.0f}% | 出場桶相關 {corr:.2f} | 分數出場(非到期)時進場以來報酬 ≤0 的比例 {np.mean(r_at_exit[score_exit] <= 0)*100:.0f}% 均 {r_at_exit[score_exit].mean():+.1f} bps")
    # 只看兩規則出場不同的事件:哪個比較好
    diff = v & (ks != kp_)
    ds = E.evaluate(W, ks)[diff]; dp = E.evaluate(W, kp_)[diff]
    print(f"  兩規則出場不同的 {diff.sum()} 筆:分數出 超額 {ds['ex'].mean():+.1f} 持 {ds['k'].mean()*5:.0f}分 | 價格出 超額 {dp['ex'].mean():+.1f} 持 {dp['k'].mean()*5:.0f}分 | 配對差 {(ds['ex'].values-dp['ex'].values).mean():+.1f}(t{cl_t(ds['ex'].values-dp['ex'].values, ds['date'].values)[1]:+.1f})")
    # 配對差:原 vs 錯位
    dl = E.evaluate(W, alt["分數≤0 用前一桶分數(錯位/延遲 1 桶)"])[v]; do = seven["分數≤0 出(原)"][v]
    d_lag = do["ex"].values - dl["ex"].values; mu_l, t_l = cl_t(d_lag, do["date"].values)
    print(f"  配對差 原−錯位(延遲1桶) {mu_l:+.1f}(t{t_l:+.1f}) | 原−價格出 見上")
    e_s, e_p, e_l = (seven[k][v]["ex"].mean() for k in ("分數≤0 出(原)", "價格≤進場價 出(r≤0)", "分數≤0 用前一桶分數(錯位/延遲 1 桶)"))
    spread = max(e_s, e_p, e_l) - min(e_s, e_p, e_l)
    v7 = "推翻" if spread < 3 else ("部分" if (e_s - e_p) < 3 or abs(t_l) < 2 else "未推翻")
    verdicts.append(("⑦ 分數出場有無獨立資訊", v7, f"分數出 {e_s:+.1f} / 價格出 {e_p:+.1f} / 錯位 {e_l:+.1f};原−價格配對 t{cl_t(ds['ex'].values-dp['ex'].values, ds['date'].values)[1]:+.1f}、原−錯位 t{t_l:+.1f}"))

    # ================================================================ ⑧ 出場桶宇宙報酬
    print("\n== ⑧ 出場桶的宇宙報酬(擇時是否來自市場) ==")
    ur_path = W["ur"]; ur5 = np.diff(ur_path, axis=1)          # 每桶宇宙 5 分報酬 (n×H) 對應 k=1..H
    ur_exit_bucket = ur5[a_, ks - 1]                          # 出場當桶宇宙 5 分報酬
    ur_cum_exit = ur_path[a_, ks]                             # 進場→出場宇宙累積
    ur_after = ur_path[a_, H] - ur_path[a_, ks]               # 出場後→60 分宇宙報酬(錯過的市場)
    r_after = W["r"][a_, H] - W["r"][a_, ks]                  # 出場後→60 分個股報酬(錯過的個股)
    uncond = ur5[v].mean()
    for lab, m in (("分數出場(非到期)", score_exit), ("持到到期(60分)", v & (ks == H)), ("全部", v)):
        print(f"  [{lab:12s}] n={m.sum():5d} 出場當桶宇宙5分 {ur_exit_bucket[m].mean():+5.1f}(無條件桶均 {uncond:+.1f}) | 進場→出場宇宙累積 {ur_cum_exit[m].mean():+5.1f} | 出場後→60分 宇宙 {ur_after[m].mean():+5.1f} 個股 {r_after[m].mean():+5.1f} 個股超額 {(r_after-ur_after)[m].mean():+5.1f}")
    # 出場後個股超額若為負→出場有資訊(不是市場擇時);若出場桶宇宙為正→出在市場反彈
    ex_after_score = (r_after - ur_after)[score_exit].mean(); t_after = cl_t((r_after - ur_after)[score_exit], E.date_arr[W["row"]][score_exit])[1]
    print(f"  分數出場後 5~60 分個股超額 {ex_after_score:+.1f}(t{t_after:+.1f}):負=出場避開了後續走弱(有資訊);≈0=出場只是截斷")
    # 反事實:若分數出場改成「同一桶出、但改用市場 5 分報酬符號」
    mkt_up_at_exit = (ur_exit_bucket[score_exit] > 0).mean() * 100
    print(f"  分數出場當桶宇宙上漲的比例 {mkt_up_at_exit:.0f}%(基準:所有桶 {np.mean(ur5[v]>0)*100:.0f}%)")
    mkt_timed = ur_exit_bucket[score_exit].mean() > uncond + 1
    v8 = "部分" if mkt_timed else "未推翻"
    verdicts.append(("⑧ 出場桶宇宙報酬", v8, f"出場桶宇宙 {ur_exit_bucket[score_exit].mean():+.1f} vs 無條件 {uncond:+.1f}(出場=市場止跌桶);但 ex 已淨宇宙、出場後個股超額 {ex_after_score:+.1f}(t{t_after:+.1f})→出場只是截斷不避損"))

    # ================================================================ ⑨ 補充:邊在哪一桶、進場由哪些項目觸發、時段
    print("\n== ⑨ 補充:邊的所在桶 / 觸發項目 / 時段 ==")
    rk = W["r"][v]; uk = W["ur"][v]; exk = rk - uk
    print("  進場後累積超額(全事件,不出場) k=1..12:", " ".join(f"{exk[:,k].mean():+5.1f}" for k in range(1, H + 1)))
    print("  進場後累積原始           k=1..12:", " ".join(f"{rk[:,k].mean():+5.1f}" for k in range(1, H + 1)))
    kk = ks[v]; exq = base.loc[base['valid'], 'ex'].values
    for kv in (1, 2, 3):
        m = kk == kv; print(f"  出場 k={kv}(持 {kv*5} 分) n={m.sum():4d} ({m.mean()*100:.0f}%) 超額 {exq[m].mean():+6.1f} 原始 {base.loc[base['valid'],'ret'].values[m].mean():+6.1f}")
    m = kk >= 4; print(f"  出場 k≥4(持≥20分)   n={m.sum():4d} ({m.mean()*100:.0f}%) 超額 {exq[m].mean():+6.1f} 原始 {base.loc[base['valid'],'ret'].values[m].mean():+6.1f}")
    print(f"  首桶(k=1)超額佔 60 分累積超額比例: {exk[:,1].mean()/exk[:,H].mean()*100:.0f}% | 事件路徑最低點(k=1..12)中位 {np.median(rk[:,1:].min(1)):+.0f} bps")
    evv = ev.iloc[np.flatnonzero(W["valid"])]
    itcols = [c for c in evv.columns if c.startswith("it:")]
    share = evv[itcols].mean().sort_values(ascending=False); share = share[share > 0.05]
    print("  進場時為真的項目佔比(>5%):", ", ".join(f"{c[3:]} {v_*100:.0f}%" for c, v_ in share.items()))
    hh = evv["bucket"].str[:2]; tb = pd.DataFrame({"h": hh.values, "ex": exq, "ret": base.loc[base['valid'],'ret'].values, "date": evv["date"].values})
    for h_, gq in tb.groupby("h"):
        mu, t = cl_t(gq["ex"].values, gq["date"].values); print(f"  時段 {h_}:xx n={len(gq):4d} 超額 {mu:+6.1f}(t{t:+4.1f}) 原始 {gq['ret'].mean():+6.1f}")
    op = tb["h"] == "09"; mu_o, t_o = cl_t(tb.loc[~op, "ex"].values, tb.loc[~op, "date"].values)
    print(f"  剔除 09:xx 後 n={(~op).sum()} 超額 {mu_o:+.1f}(t{t_o:+.1f})")

    # ================================================================ 總表
    print("\n== 判決總表 ==")
    print(f"{'檢定':28s} | {'判決':6s} | 依據")
    for k, vv, why in verdicts: print(f"{k:28s} | {vv:6s} | {why}")
    print(f"\n耗時 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
