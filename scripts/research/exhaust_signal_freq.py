#!/usr/bin/env python3
"""訊號頻率：每一檔每一天有幾個訊號（轉色；連次門檻 N=5/7/10；量條件 S1 最後30秒速度倍數／S2 累計連量分鐘）。
分母＝面板內所有『股票日』（有足夠逐筆資料者）。報：平均每股票日訊號數、至少1個／至少3個的股票日占比、有訊號之股票日的平均數；
並報『冷卻 300 秒』後（同股相鄰訊號相隔 ≥300 秒才算獨立）的版本。
"""
import glob, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

def main():
    days = [Path(f).stem for f in sorted(glob.glob(str(N.P / "*.pkl"))) if Path(f).stat().st_size > 1000]
    hist, ev, sd = {}, [], []
    for d in days:
        L.BIN = 5.0; pan = L.load_day(N.P, d)
        for s, a in pan.items():
            side, bid, ask = L.classify(a); c = a[:, 5] == 0
            h_ = hist.setdefault(s, []); ref = np.mean(h_[-10:]) / 265.0 if len(h_) >= 3 else np.nan
            h_.append(a[c, 2].sum())
            bn = L.bins(a, side)
            if bn is None or len(bn["b"]) < 60 or not np.isfinite(ref) or ref <= 0 or c.sum() < 200: continue
            sd.append((d, s, float(a[c, 2].sum())))
            dr = np.sign(bn["o"] - bn["i"]).astype(int); nb = len(dr); k = 0
            tot = bn["o"] + bn["i"]; rate_ref = ref / 60.0
            while k < nb:
                if dr[k] == 0: k += 1; continue
                j = k
                while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
                n = j - k + 1; dirn = int(dr[k])
                if n >= 5 and j + 1 < nb and dr[j + 1] == -dirn:
                    tt = (bn["b"][j + 1] + 1) * 5.0
                    if N.OPEN_OK <= tt <= N.CLOSE_OK - 60:
                        dom = (bn["o"] if dirn > 0 else bn["i"])[k:j + 1]
                        ev.append({"date": d, "sym": s, "n": n, "t": tt, "S1": tot[k:j + 1][-6:].sum() / 30.0 / rate_ref if n >= 6 else np.nan, "S2": dom.sum() / ref})
                k = j + 1
    E = pd.DataFrame(ev); SD = pd.DataFrame(sd, columns=["date", "sym", "vol"]); nsd = len(SD)
    print(f"{len(days)} 日；股票日 {nsd}（每日平均 {nsd / len(days):.1f} 檔）；轉色事件(n≥5) {len(E)}")
    print("股票日成交量(張) 分位 25/50/75：", np.percentile(SD.vol, [25, 50, 75]).round(0).tolist())
    def freq(x, label, cool=False):
        if cool:
            x = x.sort_values(["date", "sym", "t"]); keep = []; last = {}
            for r in x.itertuples():
                key = (r.date, r.sym)
                if key not in last or r.t - last[key] >= 300: keep.append(r.Index); last[key] = r.t
            x = x.loc[keep]
        cnt = x.groupby(["date", "sym"]).size().reindex(pd.MultiIndex.from_frame(SD[["date", "sym"]]), fill_value=0)
        pos = cnt[cnt > 0]
        return f"{label:<40} 訊號 {len(x):>6}｜平均/股票日 {cnt.mean():5.2f}｜≥1個占 {(cnt >= 1).mean():6.1%}｜≥3個占 {(cnt >= 3).mean():6.1%}｜有訊號日平均 {pos.mean() if len(pos) else 0:4.1f}"
    for cool in (False, True):
        print(f"\n===== {'冷卻 300 秒（獨立訊號）' if cool else '原始（相鄰事件全算）'}")
        for Nn in (5, 7, 10):
            base = E[E.n >= Nn]
            print(freq(base, f"連次≥{Nn} ∧ 轉色", cool))
            for lab, m in ((f"  ∧ S1≥2×", base.S1 >= 2), ("  ∧ S1≥3×", base.S1 >= 3), ("  ∧ S2≥1分", base.S2 >= 1), ("  ∧ S2≥2分", base.S2 >= 2),
                           ("  ∧ S1≥2× ∧ S2≥1分", (base.S1 >= 2) & (base.S2 >= 1)), ("  ∧ S1≥3× ∧ S2≥2分", (base.S1 >= 3) & (base.S2 >= 2))):
                print(freq(base[m], lab, cool))
    # 個股層面：哪些股票平均每天 ≥3 個（連次≥10∧轉色，原始）
    b = E[E.n >= 10]; per = b.groupby("sym").size().reindex(SD.sym.unique(), fill_value=0) / SD.groupby("sym").size().reindex(SD.sym.unique())
    print(f"\n連次≥10∧轉色：每檔股票『平均每天訊號數』分位 25/50/75/90：{per.quantile([.25, .5, .75, .9]).round(2).tolist()}；平均每天≥3 個的股票占 {(per >= 3).mean():.1%}")
    b = E[(E.n >= 10) & (E.S1 >= 3) & (E.S2 >= 2)]; per = b.groupby("sym").size().reindex(SD.sym.unique(), fill_value=0) / SD.groupby("sym").size().reindex(SD.sym.unique())
    print(f"連次≥10∧轉色∧S1≥3×∧S2≥2分：每檔平均每天訊號數分位 25/50/75/90：{per.quantile([.25, .5, .75, .9]).round(2).tolist()}；平均每天≥3 個的股票占 {(per >= 3).mean():.1%}")

if __name__ == "__main__":
    main()
