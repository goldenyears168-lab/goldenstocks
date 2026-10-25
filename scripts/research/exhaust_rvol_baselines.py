#!/usr/bin/env python3
"""相對成交量（RVOL）的基準比較：XQ 風格 vs 原基準（同一批轉色事件；中價、含零）。
事件：連次≥3 的同向連攻段被反向主導窗打斷（轉色），訊號＝轉色窗收盤（PIT）。分子＝近 60 秒成交量。基準（分母＝平均每 60 秒成交量）：
  R0 原基準：過去 10 個交易日整日平均成交率（總量÷265 分）。
  R5  前 5 分鐘（XQ『爆量長紅』：前 N 根均量，N=5）。  R20 前 20 分鐘。
  RD  當日已過的所有分鐘（上限 200 分；XQ『分鐘暴量n%』均量期數 200，但僅用當日）。
  RT  過去 10 個交易日同一時段（訊號前 60 秒，相同時刻）平均成交量＝時段調整後的相對成交量。
比較：日內 Spearman IC、各基準取前 15%／30% 的含零平均與較基準高出、四基準同時放入日期叢集 OLS，並報分數在開盤前後的分布（檢查時段偏差）。
"""
import glob, sys
from bisect import bisect_right
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

MINN = 3
def main():
    days = [Path(f).stem for f in sorted(glob.glob(str(N.P / "*.pkl"))) if Path(f).stat().st_size > 1000]
    hist, tapes, rows, nsd = {}, {}, [], 0
    for d in days:
        L.BIN = 5.0; pan = L.load_day(N.P, d)
        for s, a in pan.items():
            side, bid, ask = L.classify(a); c = a[:, 5] == 0
            t_all, dv = a[c, 0], a[c, 2]
            cum = np.concatenate([[0.0], np.cumsum(dv)])
            def vol(t0, t1, t_=t_all, cum_=cum):
                return cum_[bisect_right(t_, t1)] - cum_[bisect_right(t_, t0)]
            prev = tapes.setdefault(s, [])                        # 前幾日 (t, cum)
            h_ = hist.setdefault(s, []); ref = np.mean(h_[-10:]) / 265.0 if len(h_) >= 3 else np.nan
            h_.append(dv.sum()); prev_days = prev[-10:]
            prev.append((t_all, cum))
            bn = L.bins(a, side)
            if bn is None or len(bn["b"]) < 60 or not np.isfinite(ref) or ref <= 0 or c.sum() < 200 or len(prev_days) < 3: continue
            nsd += 1
            okq = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid)
            mid = np.where(okq, (bid + ask) / 2.0, a[:, 1])[c]
            dr = np.sign(bn["o"] - bn["i"]).astype(int); nb = len(dr); k = 0
            while k < nb:
                if dr[k] == 0: k += 1; continue
                j = k
                while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
                n = j - k + 1; dirn = int(dr[k])
                if n >= MINN and j + 1 < nb and dr[j + 1] == -dirn:
                    tt = (bn["b"][j + 1] + 1) * 5.0
                    if N.OPEN_OK + 300 <= tt <= N.CLOSE_OK - 700:
                        num = vol(tt - 60, tt)
                        def per_min(t0, t1):
                            return vol(t0, t1) / max((t1 - t0) / 60.0, 1e-9)
                        r5 = per_min(tt - 360, tt - 60); r20 = per_min(max(tt - 1260, 9 * 3600), tt - 60); rd = per_min(max(tt - 12000, 9 * 3600), tt - 60)
                        slot = np.mean([ (cm[bisect_right(tp, tt)] - cm[bisect_right(tp, tt - 60)]) for tp, cm in prev_days ])
                        r = {"date": d, "sym": s, "n": n, "t": tt, "tod": tt / 3600.0,
                             "R0": num / ref, "R5": num / r5 if r5 > 0 else np.nan, "R20": num / r20 if r20 > 0 else np.nan, "RD": num / rd if rd > 0 else np.nan, "RT": num / slot if slot > 0 else np.nan}
                        for h in (60, 180, 300):
                            v = N.fwd(t_all, mid, tt, h); r[f"r{h}"] = -dirn * v if v is not None else np.nan
                        rows.append(r)
                k = j + 1
    E = pd.DataFrame(rows); E.to_pickle(Path.home() / "goldenstocks-data/research/exhaust_rvol_baselines_events.pkl")
    names = {"R0": "R0 原基準(10日整日均速)", "R5": "R5 前5分鐘均量", "R20": "R20 前20分鐘均量", "RD": "RD 當日前面所有分鐘(≤200)", "RT": "RT 過去10日同時段均量"}
    pd.set_option("display.width", 220)
    print(f"{len(days)} 日；股票日 {nsd}；轉色事件(連次≥{MINN}) {len(E)}")
    print("\n各基準 RVOL 的分位(25/50/75/90)與『開盤後1小時內 vs 之後』中位數（時段偏差）：")
    for k, nm in names.items():
        e = E[k].replace([np.inf, -np.inf], np.nan).dropna()
        m1 = E[(E.tod < 10)][k].median(); m2 = E[(E.tod >= 10)][k].median()
        print(f"  {nm:<28} {np.percentile(e, [25, 50, 75, 90]).round(2).tolist()}  中位:開盤後1小時 {m1:.2f}｜之後 {m2:.2f}")
    print("\nA) 日內 Spearman IC（RVOL vs 反轉方向含零報酬；日均IC｜t）")
    for h in (60, 180, 300):
        out = []
        for k, nm in names.items():
            ics = []
            for d, g in E.dropna(subset=[f"r{h}", k]).groupby("date"):
                if len(g) >= 15 and g[k].nunique() > 2: ics.append(g[[k, f"r{h}"]].rank().corr().iloc[0, 1])
            ics = np.array(ics); out.append(f"{k} {ics.mean():+.3f}(t{ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))):+.1f})")
        print(f"  持有{h // 60}分：" + "；".join(out))
    print("\nB) RVOL 門檻（≥2、≥3、≥5 倍）：每股票日訊號數｜含零bps（1／3／5分）｜較全轉色高出（3分，t）")
    base = {h: E.groupby("date")[f"r{h}"].mean() for h in (60, 180, 300)}
    for k, nm in names.items():
        for th in (2, 3, 5):
            x = E[E[k] >= th]; res = []
            for h in (60, 180, 300):
                y = x.dropna(subset=[f"r{h}"]); res.append(f"{y[f'r{h}'].mean():+.1f}")
            y = x.dropna(subset=["r180"]); g = y.groupby("date")["r180"].agg(["mean", "size"]); g = g[g["size"] >= 3]; dd = g["mean"] - base[180].reindex(g.index)
            print(f"  {nm:<28} ≥{th}×  每股票日 {len(x) / nsd:5.1f}｜{' / '.join(res)}｜+{dd.mean():.1f}(t{dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd))):+.1f})")
    import statsmodels.formula.api as smf
    P = E.replace([np.inf, -np.inf], np.nan).dropna(subset=list(names)).copy()
    for k in names: P[k + "p"] = P[k].rank(pct=True)
    print("\nC) 五個 RVOL（百分位）同時放入日期叢集 OLS（係數＝百分位從 0 到 1 的 bps）")
    for h in (60, 180, 300):
        x = P.dropna(subset=[f"r{h}"]); m = smf.ols(f"r{h} ~ " + " + ".join(k + "p" for k in names), x).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(x.date)[0]})
        print(f"  持有{h // 60}分：" + "；".join(f"{k}={m.params[k + 'p']:+.1f}(t{m.tvalues[k + 'p']:+.1f})" for k in names))
        m1 = {k: smf.ols(f"r{h} ~ {k}p", x).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(x.date)[0]}) for k in names}
        print("     單獨：" + "；".join(f"{k}={m1[k].params[k + 'p']:+.1f}(t{m1[k].tvalues[k + 'p']:+.1f})" for k in names))

if __name__ == "__main__":
    main()
