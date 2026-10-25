#!/usr/bin/env python3
"""小哥口訣『空在爆量漲不動』：以『轉色』為扳機（連次≥10 後反向主導窗收盤，PIT），單獨檢驗『漲不動』（價格停滯）的增量。
漲不動 stall_S：連攻段『最後 S 個窗』的最高價（賣盤竭盡看最低價）沒有超過『段內更早窗』的最高（低）價（S=3/6/12 窗＝15/30/60 秒）。
爆量 heavy_S：最後 S 個窗的總成交速度 ≥ 3 × 該股平均速度（前≤10 日連續盤日量 ÷265 分鐘；PIT）。
交叉：爆量∧漲不動 / 爆量∧在漲 / 量平∧漲不動 / 量平∧在漲。另做日期叢集 OLS：反轉報酬 ~ stall + heavy + stall×heavy + cum≥2分。
後續報酬用買賣中價；基準＝同股同日每 60 秒時點；反轉＝買盤竭盡後下跌／賣盤竭盡後上漲。
"""
import glob, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

HS = N.HS; SS = [3, 6, 12]
def main():
    days = [Path(f).stem for f in sorted(glob.glob(str(N.P / "*.pkl"))) if Path(f).stat().st_size > 1000]
    hist, rows, base = {}, [], []
    for d in days:
        L.BIN = 5.0; pan = L.load_day(N.P, d)
        for s, a in pan.items():
            side, bid, ask = L.classify(a); c = a[:, 5] == 0
            h_ = hist.setdefault(s, []); ref = np.mean(h_[-10:]) / 265.0 if len(h_) >= 3 else np.nan
            h_.append(a[c, 2].sum())
            bn = L.bins(a, side)
            if bn is None or len(bn["b"]) < 60 or not np.isfinite(ref) or ref <= 0: continue
            t = a[c, 0]
            if len(t) < 200: continue
            okq = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid)
            mid = np.where(okq, (bid + ask) / 2.0, a[:, 1])[c]
            dr = np.sign(bn["o"] - bn["i"]).astype(int); nb = len(dr); k = 0
            tot = bn["o"] + bn["i"]; rate_ref = ref / 60.0          # 張/秒
            while k < nb:
                if dr[k] == 0: k += 1; continue
                j = k
                while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
                n = j - k + 1; dirn = int(dr[k])
                if n >= 10 and j + 1 < nb and dr[j + 1] == -dirn:
                    tt = (bn["b"][j + 1] + 1) * 5.0
                    if N.OPEN_OK <= tt <= N.CLOSE_OK - 700:
                        ext = (bn["hi"] if dirn > 0 else bn["lo"])[k:j + 1]
                        dom = (bn["o"] if dirn > 0 else bn["i"])[k:j + 1]
                        r = {"date": d, "sym": s, "dir": dirn, "n": n, "t": tt, "cum_x": dom.sum() / ref}
                        for S in SS:
                            if len(ext) <= S + 1:                       # 段太短，『更早的窗』不足 → 此 S 不判斷
                                r[f"stall{S}"] = np.nan; r[f"heavy{S}"] = np.nan; continue
                            early, late = ext[:-S], ext[-S:]
                            r[f"stall{S}"] = float(late.max() <= early.max()) if dirn > 0 else float(late.min() >= early.min())
                            r[f"heavy{S}"] = float(tot[k:j + 1][-S:].sum() / (S * 5.0) >= 3.0 * rate_ref)
                        for h in HS: r[f"m{h}"] = N.fwd(t, mid, tt, h)
                        rows.append(r)
                k = j + 1
            for tt in np.arange(N.OPEN_OK, N.CLOSE_OK - 700, 60.0):
                r = {"date": d, "sym": s, "grp": "Base"}
                for h in HS: r[f"m{h}"] = N.fwd(t, mid, tt, h)
                base.append(r)
    E, B = pd.DataFrame(rows), pd.DataFrame(base)
    pd.set_option("display.width", 200); pd.set_option("display.max_rows", 400)
    print(f"{len(days)} 日；轉色事件 {len(E)}；可判斷筆數 S=3/6/12：{[int(E[f'stall{S}'].notna().sum()) for S in SS]}；stall 比例：{[round(E[f'stall{S}'].mean(), 3) for S in SS]}；heavy 比例：{[round(E[f'heavy{S}'].mean(), 3) for S in SS]}")
    for S in SS:
        Ea = E.dropna(subset=[f"stall{S}"]); Ea = Ea.assign(**{f"stall{S}": Ea[f"stall{S}"].astype(bool), f"heavy{S}": Ea[f"heavy{S}"].astype(bool)})
        g = {}
        for nm, m in (("爆量∧漲不動", Ea[f"heavy{S}"] & Ea[f"stall{S}"]), ("爆量∧在漲", Ea[f"heavy{S}"] & ~Ea[f"stall{S}"]), ("量平∧漲不動", ~Ea[f"heavy{S}"] & Ea[f"stall{S}"]), ("量平∧在漲", ~Ea[f"heavy{S}"] & ~Ea[f"stall{S}"]),
                       ("漲不動(不分量)", Ea[f"stall{S}"]), ("在漲(不分量)", ~Ea[f"stall{S}"])):
            g[nm] = m
        X = pd.concat([Ea[m].assign(W=0, grp=k) for k, m in g.items()], ignore_index=True)
        R = N.summarize(X, B)
        print(f"\n=== S={S} 窗（{S * 5} 秒）")
        print(R[R.h_min.isin([1, 3, 5])][["grp", "h_min", "n", "days", "P_rev", "P_base", "lift_pp", "t_lift", "ret_bps", "t_ret"]].sort_values(["h_min", "grp"]).round(3).to_string(index=False))
    # 日期叢集 OLS：反轉報酬(bps) ~ stall + heavy + stall×heavy + cum≥2
    import statsmodels.formula.api as smf
    for S in SS:
        for h in (60, 180):
            x = E.dropna(subset=[f"m{h}", f"stall{S}"]).copy(); x["ret"] = -x.dir * x[f"m{h}"]; x["stall"] = x[f"stall{S}"].astype(int); x["heavy"] = x[f"heavy{S}"].astype(int); x["cum2"] = (x.cum_x >= 2).astype(int)
            m = smf.ols("ret ~ stall + heavy + stall:heavy + cum2", x).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(x.date)[0]})
            print(f"\nOLS S={S} h={h // 60}分：" + "；".join(f"{k} {m.params[k]:+.2f}bps(t {m.tvalues[k]:+.1f})" for k in ("Intercept", "stall", "heavy", "stall:heavy", "cum2")))
    E.to_pickle(Path.home() / "goldenstocks-data/research/exhaust_stall_events.pkl")

if __name__ == "__main__":
    main()
