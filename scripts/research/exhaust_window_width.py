#!/usr/bin/env python3
"""連次的窗長（官方 5 秒）改成 2／3／4／5／6 秒：同批逐筆、同樣的轉色事件定義，重算並比較（買賣兩邊合計）。
每個窗長 W：外盤量>內盤量記 +1（反之 −1，平手中斷，無成交窗跳過），連次≥N 的同向段被反向主導窗打斷＝轉色，訊號＝轉色窗收盤（PIT）。
量條件（不隨窗長改變，才可比）：RVOL＝近 30 秒（時間窗）成交量 ÷ (30 秒 × 該股過去 10 日整日平均成交率)；累計連量＝連攻期間各窗主導方向量加總 ÷ 該股分均量（分鐘）。
報酬：反轉方向、買賣中價、含零（1／3／5 分）。輸出事件檔 exhaust_window_events.pkl（欄位含 W、dir）。
"""
import glob, sys
from bisect import bisect_right
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

WS = [2, 3, 4, 5, 6]
def main():
    days = [Path(f).stem for f in sorted(glob.glob(str(N.P / "*.pkl"))) if Path(f).stat().st_size > 1000]
    hist, rows, nsd = {}, [], 0
    for d in days:
        pan = L.load_day(N.P, d)
        for s, a in pan.items():
            L.BIN = 5.0
            side, bid, ask = L.classify(a); c = a[:, 5] == 0
            h_ = hist.setdefault(s, []); ref = np.mean(h_[-10:]) / 265.0 if len(h_) >= 3 else np.nan
            h_.append(a[c, 2].sum())
            if not np.isfinite(ref) or ref <= 0 or c.sum() < 200: continue
            nsd += 1
            t, dv = a[c, 0], a[c, 2]; cum = np.concatenate([[0.0], np.cumsum(dv)])
            okq = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid)
            mid = np.where(okq, (bid + ask) / 2.0, a[:, 1])[c]
            rate_ref = ref / 60.0
            for W in WS:
                L.BIN = float(W)
                bn = L.bins(a, side)
                if bn is None or len(bn["b"]) < 60: continue
                dr = np.sign(bn["o"] - bn["i"]).astype(int); nb = len(dr); k = 0
                while k < nb:
                    if dr[k] == 0: k += 1; continue
                    j = k
                    while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
                    n = j - k + 1; dirn = int(dr[k])
                    if n >= 3 and j + 1 < nb and dr[j + 1] == -dirn:
                        tt = (bn["b"][j + 1] + 1) * float(W)
                        if N.OPEN_OK <= tt <= N.CLOSE_OK - 700:
                            dom = (bn["o"] if dirn > 0 else bn["i"])[k:j + 1]
                            v30 = cum[bisect_right(t, tt)] - cum[bisect_right(t, tt - 30)]
                            r = {"W": W, "date": d, "sym": s, "dir": dirn, "n": n, "t": tt, "S1": v30 / 30.0 / rate_ref, "S2": dom.sum() / ref}
                            for h in (60, 180, 300):
                                v = N.fwd(t, mid, tt, h); r[f"r{h}"] = -dirn * v if v is not None else np.nan
                            rows.append(r)
                    k = j + 1
    E = pd.DataFrame(rows); E.attrs["nsd"] = nsd
    E.to_pickle(Path.home() / "goldenstocks-data/research/exhaust_window_events.pkl")
    print(f"{len(days)} 日；股票日 {nsd}；事件 {len(E)}；各窗長事件數：", E.groupby("W").size().to_dict())
    open(Path.home() / "goldenstocks-data/research/exhaust_window_nsd.txt", "w").write(str(nsd))

if __name__ == "__main__":
    main()
