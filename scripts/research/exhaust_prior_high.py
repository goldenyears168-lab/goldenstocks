#!/usr/bin/env python3
"""『第二次沒過前高』（使用者 2026-10-25）：以轉色為扳機（連次≥10 後反向主導窗收盤，PIT）。
前高 H_prev ＝ 本段連攻『開始前』、回看 L 分鐘（L=5/15/30，或當日開盤以來）的最高成交價（賣盤竭盡＝最低價）。段頂 P_top ＝ 連攻期間最高（低）價。
分組：DT 雙頂＝P_top 沒超過 H_prev 且離 H_prev 在 x bps 內（x=30/50/100）；BRK 突破＝P_top > H_prev；FAR 離前高很遠＝P_top 低於 H_prev 超過 x bps；NA 回看範圍內沒有資料。
報酬一律『含零』（反轉方向、買賣中價）；另報反轉機率（條件於有變動）。lift（bps）＝組平均 − 同日所有轉色平均，日期叢集 t。再與 爆量(最後6窗≥3×均速)、累計連量≥2 分 交叉。
"""
import glob, sys
from bisect import bisect_left, bisect_right
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

HS = [60, 180, 300]; LBS = [1, 5, 15, 30, 0]       # 0＝當日開盤以來
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
            t, px = a[c, 0], a[c, 1]
            if len(t) < 200: continue
            okq = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid)
            mid = np.where(okq, (bid + ask) / 2.0, a[:, 1])[c]
            dr = np.sign(bn["o"] - bn["i"]).astype(int); nb = len(dr); k = 0; tot = bn["o"] + bn["i"]; rate_ref = ref / 60.0
            while k < nb:
                if dr[k] == 0: k += 1; continue
                j = k
                while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
                n = j - k + 1; dirn = int(dr[k])
                if n >= 10 and j + 1 < nb and dr[j + 1] == -dirn:
                    tt = (bn["b"][j + 1] + 1) * 5.0; t0 = bn["b"][k] * 5.0
                    if N.OPEN_OK <= tt <= N.CLOSE_OK - 700:
                        i_a, i_b = bisect_left(t, t0), bisect_right(t, tt)
                        seg = px[i_a:i_b]
                        if len(seg) >= 5:
                            ptop = seg.max() if dirn > 0 else seg.min()
                            dom = (bn["o"] if dirn > 0 else bn["i"])[k:j + 1]
                            r = {"date": d, "sym": s, "dir": dirn, "n": n, "t": tt, "cum_x": dom.sum() / ref,
                                 "heavy6": float(tot[k:j + 1][-6:].sum() / 30.0 >= 3.0 * rate_ref) if n > 7 else np.nan}
                            for Lm in LBS:
                                lo_t = (-1.0 if Lm == 0 else t0 - Lm * 60)
                                p0 = bisect_left(t, lo_t) if Lm else 0
                                prev = px[p0:i_a]
                                if len(prev) < 5: r[f"gap{Lm}"] = np.nan; continue
                                hp = prev.max() if dirn > 0 else prev.min()
                                r[f"gap{Lm}"] = (hp / ptop - 1) * 1e4 * (1 if dirn > 0 else -1)   # >0＝段頂低於前高(沒過)，bps；<0＝已突破
                            for h in HS:
                                i0, i1 = bisect_right(t, tt) - 1, bisect_right(t, tt + h) - 1
                                r[f"m{h}"] = (mid[i1] / mid[i0] - 1) * 1e4 if (i0 >= 0 and i1 > i0 and tt + h <= L.CLOSE_T) else np.nan
                            rows.append(r)
                k = j + 1
    E = pd.DataFrame(rows); E.to_pickle(Path.home() / "goldenstocks-data/research/exhaust_prior_high_events.pkl")
    for h in HS: E[f"r{h}"] = -E.dir * E[f"m{h}"]
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 300)
    print(f"{len(days)} 日；轉色事件 {len(E)}")
    def stat(df, col, base_by_date):
        x = df.dropna(subset=[col]); 
        if len(x) < 30: return None
        g = x.groupby("date")[col].agg(["mean", "size"]); g = g[g["size"] >= 3]
        if len(g) < 8: return None
        diff = g["mean"] - base_by_date.reindex(g.index)
        chg = x[x[col] != 0]
        prev = (chg[col] > 0).mean() if len(chg) else np.nan
        return len(x), len(g), x[col].mean(), diff.mean(), diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))), prev
    for xbps in (50,):
        for Lm in LBS:
            gcol = f"gap{Lm}"; sub = E.dropna(subset=[gcol])
            name = "當日" if Lm == 0 else f"{Lm}分"
            print(f"\n=== 回看 {name}（近前高門檻 {xbps}bps）：可判斷 {len(sub)}/{len(E)} 筆")
            groups = {"DT 雙頂(沒過前高且≤%dbps)" % xbps: (sub[gcol] >= 0) & (sub[gcol] <= xbps), "BRK 已突破前高": sub[gcol] < 0, "FAR 離前高很遠": sub[gcol] > xbps}
            out = []
            for gn, m in groups.items():
                for ext, mm in (("", m), ("∧爆量", m & (sub.heavy6 == 1)), ("∧累計≥2分", m & (sub.cum_x >= 2)), ("∧爆量∧累計≥2分", m & (sub.heavy6 == 1) & (sub.cum_x >= 2))):
                    for h in HS:
                        base = E.groupby("date")[f"r{h}"].mean()
                        r = stat(sub[mm], f"r{h}", base)
                        if r: out.append({"組": gn + ext, "持有": f"{h//60}分", "n": r[0], "日": r[1], "含零平均bps": r[2], "對全轉色lift": r[3], "t": r[4], "P_rev|有變動": r[5]})
            print(pd.DataFrame(out).round(3).to_string(index=False))
    # 近前高門檻敏感度（回看 15 分，DT，持有 1 分）
    print("\n=== 近前高門檻敏感度（回看15分、持有1分、含零）")
    sub = E.dropna(subset=["gap15"]); base = E.groupby("date")["r60"].mean()
    for xb in (30, 50, 100, 200):
        r = stat(sub[(sub.gap15 >= 0) & (sub.gap15 <= xb)], "r60", base)
        if r: print(f"  ≤{xb}bps：n={r[0]} 含零平均 {r[2]:+.2f}bps lift {r[3]:+.2f} (t {r[4]:+.1f}) P_rev {r[5]:.3f}")

if __name__ == "__main__":
    main()
