#!/usr/bin/env python3
"""累計連量標準（使用者 2026-10-25）：算連次的同時把同方向每個窗的主導量加總；累計量要達到該股自己的標準才算。
累計連量 cum_x ＝ Σ(連攻期間各窗主導方向張數) ÷ 該股每分鐘均量（前≤10 日連續盤日量均值 ÷265，PIT）＝『幾分鐘的成交量』。
(1) 轉色事件（連次≥10 後反向主導窗收盤）依 cum_x 分組：全部 / ≥1 / ≥2 / ≥3 / ≥5 分鐘；
(2) 亮燈事件（連次第一次達 10；cum_x 為亮燈當下的累計）→ 之後 W 分鐘『沒破前高低 ∧ 量縮』，限定 cum_x≥3 分鐘 vs <3 分鐘。
後續報酬一律用買賣中價；基準＝同股同日每 60 秒無條件時點；日期叢集 t。
"""
import glob, sys
from bisect import bisect_left, bisect_right
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

HS = N.HS; WS = [60, 180, 300]
def main():
    days = [Path(f).stem for f in sorted(glob.glob(str(N.P / "*.pkl"))) if Path(f).stat().st_size > 1000]
    hist, flips, lits, base = {}, [], [], []
    for d in days:
        L.BIN = 5.0; pan = L.load_day(N.P, d)
        for s, a in pan.items():
            side, bid, ask = L.classify(a); c = a[:, 5] == 0
            h_ = hist.setdefault(s, []); ref = np.mean(h_[-10:]) / 265.0 if len(h_) >= 3 else np.nan   # 每分鐘均量(張)
            h_.append(a[c, 2].sum())
            bn = L.bins(a, side)
            if bn is None or len(bn["b"]) < 60 or not np.isfinite(ref) or ref <= 0: continue
            t, px, dv = a[c, 0], a[c, 1], a[c, 2]
            if len(t) < 200: continue
            okq = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid)
            mid = np.where(okq, (bid + ask) / 2.0, a[:, 1])[c]; cum = np.cumsum(dv)
            def vb(t0, t1):
                i0, i1 = bisect_right(t, t0), bisect_right(t, t1)
                return (cum[i1 - 1] - (cum[i0 - 1] if i0 > 0 else 0)) if i1 > i0 else 0.0
            dr = np.sign(bn["o"] - bn["i"]).astype(int); nb = len(dr); k = 0; last_lit = -1e9
            while k < nb:
                if dr[k] == 0: k += 1; continue
                j = k
                while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
                n = j - k + 1; dirn = int(dr[k]); dom = (bn["o"] if dirn > 0 else bn["i"])
                if n >= 10:
                    # (1) 轉色
                    if j + 1 < nb and dr[j + 1] == -dirn:
                        tt = (bn["b"][j + 1] + 1) * 5.0
                        if N.OPEN_OK <= tt <= N.CLOSE_OK - 700:
                            r = {"date": d, "sym": s, "dir": dirn, "n": n, "t": tt, "cum_x": dom[k:j + 1].sum() / ref}
                            for h in HS: r[f"m{h}"] = N.fwd(t, mid, tt, h)
                            flips.append(r)
                    # (2) 亮燈 + 之後 W 分鐘沒破 ∧ 量縮
                    t_lit = (bn["b"][k + 9] + 1) * 5.0; t_start = bn["b"][k] * 5.0
                    if t_lit - last_lit >= 300 and N.OPEN_OK <= t_lit <= N.CLOSE_OK - 700:
                        i_a, i_b = bisect_left(t, t_start), bisect_right(t, t_lit)
                        if i_b - i_a >= 5:
                            H0 = px[i_a:i_b].max() if dirn > 0 else px[i_a:i_b].min()
                            pre_rate = vb(t_start - 1e-6, t_lit) / max(t_lit - t_start, 5.0)
                            cx = dom[k:k + 10].sum() / ref
                            for W in WS:
                                tc = t_lit + W
                                if tc + max(HS) > L.CLOSE_T: continue
                                w0, w1 = bisect_right(t, t_lit), bisect_right(t, tc)
                                if w1 - w0 < 1: continue
                                ext = px[w0:w1].max() if dirn > 0 else px[w0:w1].min()
                                nobrk = (ext <= H0) if dirn > 0 else (ext >= H0)
                                ratio = (vb(t_lit, tc) / W) / pre_rate if pre_rate > 0 else np.nan
                                r = {"date": d, "sym": s, "dir": dirn, "n": n, "W": W, "t": tc, "cum_x": cx, "nobrk": bool(nobrk), "shrink": bool(ratio < 0.5)}
                                for h in HS: r[f"m{h}"] = N.fwd(t, mid, tc, h)
                                lits.append(r)
                        last_lit = t_lit
                k = j + 1
            for tt in np.arange(N.OPEN_OK, N.CLOSE_OK - 700, 60.0):
                r = {"date": d, "sym": s, "grp": "Base"}
                for h in HS: r[f"m{h}"] = N.fwd(t, mid, tt, h)
                base.append(r)
    F, Lt, B = pd.DataFrame(flips), pd.DataFrame(lits), pd.DataFrame(base)
    pd.set_option("display.width", 200); pd.set_option("display.max_rows", 400)
    print(f"{len(days)} 日；轉色 {len(F)}；亮燈觀察 {len(Lt)}；cum_x 分位 25/50/75/90：{np.percentile(F.cum_x, [25, 50, 75, 90]).round(2)}")
    g1 = {"轉色·全部": F.index == F.index, "轉色·cum<1分": F.cum_x < 1, "轉色·cum≥1分": F.cum_x >= 1, "轉色·cum≥2分": F.cum_x >= 2, "轉色·cum≥3分": F.cum_x >= 3, "轉色·cum≥5分": F.cum_x >= 5}
    E1 = pd.concat([F[m].assign(W=0, grp=k) for k, m in g1.items()], ignore_index=True)
    R1 = N.summarize(E1, B); print("\n(1) 轉色依累計連量標準分組"); print(R1[["grp", "h_min", "n", "days", "P_rev", "P_base", "較基準高出_pp", "t_較基準高出", "ret_bps", "t_ret"]].round(3).to_string(index=False))
    g2 = {}
    for nm, big in (("cum≥3分", Lt.cum_x >= 3), ("cum<3分", Lt.cum_x < 3)):
        g2[f"沒破∧量縮·{nm}"] = big & Lt.nobrk & Lt.shrink
        g2[f"沒破∧量未縮·{nm}"] = big & Lt.nobrk & ~Lt.shrink
        g2[f"創新高低·{nm}"] = big & ~Lt.nobrk
    E2 = pd.concat([Lt[m].assign(grp=k) for k, m in g2.items()], ignore_index=True)
    R2 = N.summarize(E2, B); R2.to_csv(Path.home() / "goldenstocks-data/research/exhaust_cum_volume.csv", index=False)
    print("\n(2) 亮燈後 W 分鐘（沒破前高低 / 量縮）× 累計連量標準；只列後續 3、5 分")
    print(R2[R2.h_min.isin([3, 5])][["W_min", "grp", "h_min", "n", "days", "P_rev", "較基準高出_pp", "t_較基準高出", "ret_bps", "t_ret"]].sort_values(["h_min", "W_min", "grp"]).round(3).to_string(index=False))

if __name__ == "__main__":
    main()
