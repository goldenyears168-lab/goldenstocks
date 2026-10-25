#!/usr/bin/env python3
"""『爆量』三種定義並排比較（同一批轉色事件：連次≥10 後反向主導窗收盤，PIT；中價、含零）。
分數（每個事件一個連續值，越大越「量大」）：
  S1 近30秒相對成交量(RVOL)  ＝ 轉色前最後 6 窗 (外盤+內盤) 總量 ÷30 秒 ÷ 該股平均相對成交量
  S2 累計連量(分鐘)   ＝ 連攻期間各窗主導方向量加總 ÷ 該股每分鐘均量
  S3 單窗爆量次數(x5)     ＝ 連攻期間 單窗主導量 ≥ 5× 該股每5秒均量 的窗數（儀表板爆量窗邏輯的 PIT 版；p90 門檻檔含未來資料故不用）
  S4 單窗最大倍數     ＝ 連攻期間 最大單窗主導量 ÷ 該股每5秒均量
比較方式：A) 日期內 Spearman（分數 vs 反轉方向報酬）的日均與 t；B) 各分數取前 15%／30% 事件的含零平均與 較基準高出；C) 四分數的百分位同時放進日期叢集 OLS 看獨立貢獻。
"""
import glob, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

def main():
    days = [Path(f).stem for f in sorted(glob.glob(str(N.P / "*.pkl"))) if Path(f).stat().st_size > 1000]
    hist, rows = {}, []
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
            tot = bn["o"] + bn["i"]; rate_ref = ref / 60.0; ref5 = ref / 12.0
            while k < nb:
                if dr[k] == 0: k += 1; continue
                j = k
                while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
                n = j - k + 1; dirn = int(dr[k])
                if n >= 10 and j + 1 < nb and dr[j + 1] == -dirn:
                    tt = (bn["b"][j + 1] + 1) * 5.0
                    if N.OPEN_OK <= tt <= N.CLOSE_OK - 700:
                        dom = (bn["o"] if dirn > 0 else bn["i"])[k:j + 1]
                        r = {"date": d, "sym": s, "dir": dirn, "n": n, "t": tt,
                             "S1": tot[k:j + 1][-6:].sum() / 30.0 / rate_ref, "S2": dom.sum() / ref,
                             "S3": float((dom >= 5 * ref5).sum()), "S4": dom.max() / ref5}
                        for h in (60, 180, 300):
                            v = N.fwd(t, mid, tt, h); r[f"r{h}"] = -dirn * v if v is not None else np.nan
                        rows.append(r)
                k = j + 1
    E = pd.DataFrame(rows); E.to_pickle(Path.home() / "goldenstocks-data/research/exhaust_burst_defs_events.pkl")
    pd.set_option("display.width", 200)
    names = {"S1": "S1 近30秒相對成交量(RVOL)", "S2": "S2 累計連量(分鐘)", "S3": "S3 單窗爆量次數(≥5×)", "S4": "S4 單窗最大倍數"}
    print(f"{len(days)} 日；事件 {len(E)}；分數分位(25/50/75/90)：")
    for k in names: print(f"  {names[k]}: {np.percentile(E[k], [25, 50, 75, 90]).round(2).tolist()}")
    print("\nA) 日內 Spearman IC（分數 vs 反轉方向含零報酬；日均IC｜t）")
    for h in (60, 180, 300):
        out = []
        for k in names:
            ics = []
            for d, g in E.dropna(subset=[f"r{h}"]).groupby("date"):
                if len(g) >= 15 and g[k].nunique() > 2: ics.append(g[[k, f"r{h}"]].rank().corr().iloc[0, 1])
            ics = np.array(ics); out.append(f"{names[k]} {ics.mean():+.3f}｜t{ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))):+.1f}")
        print(f"  持有{h // 60}分：" + "；".join(out))
    print("\nB) 取前 15%／30% 的事件（含零bps｜較全轉色高出(t)）")
    for h in (60, 180, 300):
        base = E.groupby("date")[f"r{h}"].mean(); print(f"  持有{h // 60}分（全轉色 {E[f'r{h}'].mean():+.2f}）")
        for q in (0.85, 0.70):
            for k in names:
                thr = E[k].quantile(q); x = E[(E[k] >= thr)].dropna(subset=[f"r{h}"])
                g = x.groupby("date")[f"r{h}"].agg(["mean", "size"]); g = g[g["size"] >= 3]; dd = g["mean"] - base.reindex(g.index)
                print(f"    前{int((1 - q) * 100)}% {names[k]}: n={len(x)} {x[f'r{h}'].mean():+.2f}｜{dd.mean():+.2f}(t{dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd))):+.1f})")
    import statsmodels.formula.api as smf
    P = E.copy()
    for k in names: P[k + "p"] = P[k].rank(pct=True)
    print("\nC) 四個分數（百分位）同時放進日期叢集 OLS（係數＝百分位從0到1的 bps 差）")
    for h in (60, 180, 300):
        x = P.dropna(subset=[f"r{h}"]); m = smf.ols(f"r{h} ~ S1p + S2p + S3p + S4p", x).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(x.date)[0]})
        print(f"  持有{h // 60}分：" + "；".join(f"{k}={m.params[k]:+.2f}(t{m.tvalues[k]:+.1f})" for k in ("S1p", "S2p", "S3p", "S4p")))
        m1 = {k: smf.ols(f"r{h} ~ {k}p", x).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(x.date)[0]}) for k in names}
        print("     單獨：" + "；".join(f"{k}={m1[k].params[k + 'p']:+.2f}(t{m1[k].tvalues[k + 'p']:+.1f})" for k in names))

if __name__ == "__main__":
    main()
