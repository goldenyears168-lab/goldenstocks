#!/usr/bin/env python3
"""官方定義版「買盤竭盡」檢定（2026-10-18）。

依「盤中當沖神器」說明欄：連次＝每 5 秒外>內 +1（連續累計）；**連量＝當前 5 秒窗的外盤成交張數**；
連次達 +10 亮燈＋音效；**急拉伴隨大量連次/連量後「突然轉為綠色量」＝買盤竭盡點**。
本腳本即官方口徑的事件檢定（初版曾把連量做成整段累計，已廢止並刪除）。

觸發 = 外攻段（連次 n≥N）被中斷的 5 秒窗收盤（PIT）。
特徵：n、段內單窗最大連量 vmax（÷該股 PIT 每 5 秒均量 ref5）、爆量窗數（≥M×ref5）、段內漲幅。
放空成本：t+1s 後第一筆 bid 進、h 後第一筆 ask 出（賣一進買一出）。base＝同股同日每 5 分鐘無條件放空。
"""
from __future__ import annotations
import glob, sys
from bisect import bisect_right
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
from exhaust_lib import *

P, OUT = sys.argv[1], sys.argv[2]
HZ = [5, 15, 30, 60, 300, 600, 1800]; COOL = 300
days = sorted(Path(f).stem for f in glob.glob(P + "/*.pkl"))
hist: dict[str, list[float]] = {}
rows = []
for d in days:
    for s, a in load_day(P, d).items():
        side, bid, ask = classify(a)
        c = a[:, 5] == 0
        h_ = hist.setdefault(s, []); ref = np.mean(h_[-10:]) / 265.0 if len(h_) >= 3 else np.nan
        h_.append(a[c, 2].sum())
        bn = bins(a, side)
        if not np.isfinite(ref) or ref <= 0 or bn is None or len(bn["b"]) < 100:
            continue
        ref5 = ref / 12.0                                   # 每 5 秒均量（張）
        t, px = a[:, 0], a[:, 1]
        dr = np.sign(bn["o"] - bn["i"]).astype(int)
        k, last_ev, nb = 0, -1e9, len(dr)
        while k < nb:
            if dr[k] > 0:
                j = k
                while j + 1 < nb and dr[j + 1] > 0:
                    j += 1
                n = j - k + 1
                if j + 1 < nb and n >= 3:
                    tt = (bn["b"][j + 1] + 1) * BIN
                    seg = bn["o"][k:j + 1]
                    i = bisect_right(t, tt + 1.0)
                    if (tt - last_ev >= COOL and tt <= CLOSE_T - 60 and i < len(t) and t[i] <= tt + 20
                            and np.isfinite(bid[i]) and abs(bid[i] / px[i] - 1) <= 0.05):
                        i0 = bisect_right(t, tt - 900) - 1
                        r = {"date": d, "sym": s, "t": tt, "n": n, "vmax": seg.max() / ref5, "vlast": seg[-1] / ref5,
                             "nb5": int((seg >= 5 * ref5).sum()), "nb10": int((seg >= 10 * ref5).sum()),
                             "push_bps": (bn["hi"][k:j + 1].max() / bn["first"][k] - 1) * 1e4,
                             "ret15": (px[bisect_right(t, tt) - 1] / px[max(i0, 0)] - 1) * 1e4,
                             "spread_bps": (ask[i] - bid[i]) / px[i] * 1e4 if np.isfinite(ask[i]) else np.nan}
                        for hh in HZ:
                            jj = bisect_right(t, t[i] + hh)
                            if jj >= len(t) or t[i] + hh > CLOSE_T:
                                r[f"r{hh}"] = r[f"m{hh}"] = np.nan; continue
                            ex = ask[jj]
                            r[f"r{hh}"] = (bid[i] - ex) / bid[i] * 1e4 if np.isfinite(ex) and abs(ex / px[jj] - 1) < .05 else np.nan
                            r[f"m{hh}"] = (px[i] - px[jj]) / px[i] * 1e4
                        rows.append(r); last_ev = tt
                k = j + 1
            else:
                k += 1
pd.DataFrame(rows).to_pickle(OUT); print(len(rows))
