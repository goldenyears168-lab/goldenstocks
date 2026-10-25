#!/usr/bin/env python3
"""連次門檻降到 5～10，量條件照做（使用者 2026-10-25：連次不用超過10，≥5 也可以，只是量要做到）。
事件：連次 n≥5 的同向連攻段被反向主導窗打斷（轉色），訊號＝轉色窗收盤（PIT）。S1＝轉色前最後 min(n,6) 窗總量速度÷該股平均速度；S2＝連攻期間主導方向量加總÷該股分均量（幾分鐘量）。
後續報酬：買賣中價、含零、反轉方向 bps。lift＝組均值 − 同日所有 n≥5 轉色均值（日期叢集 t）。另報：每股票日訊號數（原始／冷卻300秒）、連次分層（量標準固定時，連次是否仍有貢獻）。
"""
import glob, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

MINN = int(sys.argv[1]) if len(sys.argv) > 1 else 5

def main():
    days = [Path(f).stem for f in sorted(glob.glob(str(N.P / "*.pkl"))) if Path(f).stat().st_size > 1000]
    hist, rows, nsd = {}, [], 0
    for d in days:
        L.BIN = 5.0; pan = L.load_day(N.P, d)
        for s, a in pan.items():
            side, bid, ask = L.classify(a); c = a[:, 5] == 0
            h_ = hist.setdefault(s, []); ref = np.mean(h_[-10:]) / 265.0 if len(h_) >= 3 else np.nan
            h_.append(a[c, 2].sum())
            bn = L.bins(a, side)
            if bn is None or len(bn["b"]) < 60 or not np.isfinite(ref) or ref <= 0 or c.sum() < 200: continue
            nsd += 1
            t = a[c, 0]
            okq = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid)
            mid = np.where(okq, (bid + ask) / 2.0, a[:, 1])[c]
            dr = np.sign(bn["o"] - bn["i"]).astype(int); nb = len(dr); k = 0
            tot = bn["o"] + bn["i"]; rate_ref = ref / 60.0
            while k < nb:
                if dr[k] == 0: k += 1; continue
                j = k
                while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
                n = j - k + 1; dirn = int(dr[k])
                if n >= MINN and j + 1 < nb and dr[j + 1] == -dirn:
                    tt = (bn["b"][j + 1] + 1) * 5.0
                    if N.OPEN_OK <= tt <= N.CLOSE_OK - 700:
                        w = min(n, 6); dom = (bn["o"] if dirn > 0 else bn["i"])[k:j + 1]
                        r = {"date": d, "sym": s, "n": n, "t": tt, "S1": tot[k:j + 1][-w:].sum() / (w * 5.0) / rate_ref, "S2": dom.sum() / ref}
                        for h in (60, 180, 300):
                            v = N.fwd(t, mid, tt, h); r[f"r{h}"] = -dirn * v if v is not None else np.nan
                        rows.append(r)
                k = j + 1
    E = pd.DataFrame(rows); E.to_pickle(Path.home() / "goldenstocks-data/research/exhaust_n5_events.pkl")
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 300)
    print(f"{len(days)} 日；股票日 {nsd}；轉色事件(n≥5) {len(E)}")
    base = {h: E.groupby("date")[f"r{h}"].mean() for h in (60, 180, 300)}
    def cool(x):
        x = x.sort_values(["date", "sym", "t"]); keep, last = [], {}
        for r in x.itertuples():
            key = (r.date, r.sym)
            if key not in last or r.t - last[key] >= 300: keep.append(r.Index); last[key] = r.t
        return len(keep)
    def line(x, label):
        out = [f"{label:<34} 每股票日 {len(x) / nsd:5.1f}(獨立{cool(x) / nsd:4.1f})"]
        for h in (60, 180, 300):
            y = x.dropna(subset=[f"r{h}"]); g = y.groupby("date")[f"r{h}"].agg(["mean", "size"]); g = g[g["size"] >= 3]
            if len(g) < 8: out.append("—"); continue
            dd = g["mean"] - base[h].reindex(g.index)
            out.append(f"{y[f'r{h}'].mean():+5.1f}|{dd.mean():+4.1f}(t{dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd))):+4.1f})")
        return "  ".join(out)
    print("\n格式：每股票日訊號數  |  1分、3分、5分：含零bps|lift(t)    （基準＝同日所有 n≥5 轉色）")
    print(line(E, f"連次≥{MINN} ∧ 轉色（無量條件）"))
    for Nn in sorted({MINN, MINN + 1, 5, 6, 8, 10}):
        b = E[E.n >= Nn]
        print(f"\n--- 連次≥{Nn}")
        for lab, m in (("無量條件", b.index == b.index), ("S2≥1分", b.S2 >= 1), ("S2≥2分", b.S2 >= 2), ("S2≥3分", b.S2 >= 3), ("S1≥2×", b.S1 >= 2), ("S1≥3×", b.S1 >= 3),
                       ("S1≥2×∧S2≥1分", (b.S1 >= 2) & (b.S2 >= 1)), ("S1≥3×∧S2≥2分", (b.S1 >= 3) & (b.S2 >= 2))):
            print(line(b[m], f"  {lab}"))
    print("\n=== 量標準固定（S1≥2×∧S2≥1分）時，連次是否仍有貢獻？依連次分層")
    v = E[(E.S1 >= 2) & (E.S2 >= 1)]
    for lab, m in (("n=3～4", v.n.between(3, 4)), ("n=5～6", v.n.between(5, 6)), ("n=7～9", v.n.between(7, 9)), ("n=10～14", v.n.between(10, 14)), ("n≥15", v.n >= 15)):
        print(line(v[m], f"  {lab}"))
    import statsmodels.formula.api as smf
    P = E.copy(); P["S1p"] = P.S1.rank(pct=True); P["S2p"] = P.S2.rank(pct=True); P["nl"] = np.log(P.n)
    print("\n=== 日期叢集 OLS：反轉報酬 ~ S1p + S2p + log(連次)  （連次的係數＝在量固定下，連次翻倍的 bps）")
    for h in (60, 180, 300):
        x = P.dropna(subset=[f"r{h}"]); m = smf.ols(f"r{h} ~ S1p + S2p + nl", x).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(x.date)[0]})
        print(f"  持有{h // 60}分：S1p {m.params['S1p']:+.2f}(t{m.tvalues['S1p']:+.1f})；S2p {m.params['S2p']:+.2f}(t{m.tvalues['S2p']:+.1f})；log(連次) {m.params['nl']:+.2f}(t{m.tvalues['nl']:+.1f})")

if __name__ == "__main__":
    main()
