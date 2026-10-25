#!/usr/bin/env python3
"""轉色（連次≥10 後反向主導）加上連量門檻的對照（使用者 2026-10-25 問：舊定義是不是連量門檻沒設好）。後續報酬用買賣中價。
D0 任何轉色；D1 打斷窗主導量 ≥ 2.5×該股每 5 秒均量；D2 = D1 ∧ 連攻期間爆量窗(≥5×均量)≥3；D3 = D1 ∧ 爆量窗≥3 ∧ 連攻期間價格漲不動(打斷窗前最後 3 窗未創段內新高)。
每 5 秒均量 ref5：前 ≤10 日連續盤日量均值 ÷ 265 ÷ 12（PIT）。基準＝同股同日每 60 秒無條件時點。
"""
import glob, sys
from bisect import bisect_right
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L
import exhaust_nobreak_vol as N

HS = N.HS
def main():
    days = [Path(f).stem for f in sorted(glob.glob(str(N.P / "*.pkl"))) if Path(f).stat().st_size > 1000]
    hist, rows, base = {}, [], []
    for d in days:
        L.BIN = 5.0
        pan = L.load_day(N.P, d)
        for s, a in pan.items():
            side, bid, ask = L.classify(a)
            c = a[:, 5] == 0
            h_ = hist.setdefault(s, []); ref = np.mean(h_[-10:]) / 265.0 if len(h_) >= 3 else np.nan
            h_.append(a[c, 2].sum())
            bn = L.bins(a, side)
            if bn is None or len(bn["b"]) < 60 or not np.isfinite(ref) or ref <= 0:
                continue
            ref5 = ref / 12.0
            t, px = a[c, 0], a[c, 1]
            if len(t) < 200: continue
            okq = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid)
            mid = np.where(okq, (bid + ask) / 2.0, a[:, 1])[c]
            dr = np.sign(bn["o"] - bn["i"]).astype(int); nb = len(dr); k = 0
            while k < nb:
                if dr[k] == 0: k += 1; continue
                j = k
                while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
                n = j - k + 1
                if n >= 10 and j + 1 < nb and dr[j + 1] == -dr[k]:
                    tt = (bn["b"][j + 1] + 1) * 5.0
                    if N.OPEN_OK <= tt <= N.CLOSE_OK - 700:
                        dirn = int(dr[k])
                        seg = (bn["o"] if dirn > 0 else bn["i"])[k:j + 1]
                        flip = (bn["i"] if dirn > 0 else bn["o"])[j + 1]
                        nb5 = int((seg >= 5 * ref5).sum())
                        ext = (bn["hi"] if dirn > 0 else bn["lo"])[k:j + 1]
                        stall = (ext[-3:].max() <= ext[:-3].max()) if dirn > 0 and len(ext) > 3 else (ext[-3:].min() >= ext[:-3].min() if len(ext) > 3 else False)
                        r = {"date": d, "sym": s, "dir": dirn, "n": n, "t": tt, "flip_x": flip / ref5, "nb5": nb5, "stall": bool(stall)}
                        for h in HS: r[f"m{h}"] = N.fwd(t, mid, tt, h)
                        rows.append(r)
                k = j + 1
            for tt in np.arange(N.OPEN_OK, N.CLOSE_OK - 700, 60.0):
                r = {"date": d, "sym": s, "grp": "Base"}
                for h in HS: r[f"m{h}"] = N.fwd(t, mid, tt, h)
                base.append(r)
    ev, base = pd.DataFrame(ev := rows), pd.DataFrame(base)
    groups = {"D0 任何轉色": ev.index == ev.index, "D1 打斷窗≥2.5×均量": ev.flip_x >= 2.5, "D1b 打斷窗≥5×均量": ev.flip_x >= 5,
              "D2 D1∧爆量窗≥3": (ev.flip_x >= 2.5) & (ev.nb5 >= 3), "D3 D2∧漲不動": (ev.flip_x >= 2.5) & (ev.nb5 >= 3) & ev.stall, "D4 打斷窗<1×均量(量小的轉色)": ev.flip_x < 1}
    out = []
    for name, m in groups.items():
        x0 = ev[m].copy(); x0["W"] = 0; x0["grp"] = name; out.append(x0)
    E = pd.concat(out, ignore_index=True)
    R = N.summarize(E, base)
    R.to_csv(Path.home() / "goldenstocks-data/research/exhaust_flip_volume.csv", index=False)
    pd.set_option("display.width", 200)
    print(f"{len(days)} 日；轉色事件 {len(ev)}")
    print(R[["grp", "h_min", "n", "days", "P_rev", "P_base", "lift_pp", "t_lift", "ret_bps", "t_ret"]].round(3).to_string(index=False))

if __name__ == "__main__":
    main()
