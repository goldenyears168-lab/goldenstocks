#!/usr/bin/env python3
"""【2026-10-25 修正】後續報酬一律用買賣中價（原先用最後一筆成交價，轉色窗以賣價成交造成約半個價差的彈跳假象）。
買盤／賣盤竭盡（使用者 2026-10-25 定義）：連次達 ±10 亮燈後，接下來 W 分鐘『沒有再破前高（低）』且『量縮』＝參與者離開；轉色與轉色量不是必要條件。

事件：連次第一次達 ±10 的那個 5 秒窗收盤 t_lit（PIT）；H0＝該連攻段起點到 t_lit 的最高（+）／最低（−）成交價。
確認窗口 W∈{1,3,5,10} 分：(t_lit, t_lit+W]；
  no_break ＝ 窗口內最高價 ≤ H0（+）／最低價 ≥ H0（−）；
  shrink   ＝ 窗口內成交速度（張／秒）< 0.5 × 連攻期間（段起點→t_lit）成交速度。ratio 另報（劑量：低 / 中 / 高三分位）。
確認時刻 t_c = t_lit + W；後續 h∈{1,3,5,10} 分：看 t_c 之後價格是否朝『反轉方向』走（買盤竭盡＝下跌、賣盤竭盡＝上漲）。
分組（同一批 lit 事件，互斥）：A 創新高低（continuation）/ B 沒創新高但量未縮 / C 沒創新高且量縮（使用者定義）。對照：D 轉色（舊定義，從轉色窗收盤起算）、Base 同股同日每 60 秒無條件時點。
統計：反轉機率 = P(反轉 | 價格有變動)；lift = 事件 − 同日同方向基準；以日期叢集（每日平均後的 t）。另報反轉方向平均報酬(bps)與該段 bid/ask 價差中位數。
"""
from __future__ import annotations
import glob, sys
from bisect import bisect_left, bisect_right
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L

P = Path.home() / "goldenstocks-data/research/exhaust_panel"
OPEN_OK, CLOSE_OK = 9 * 3600 + 5 * 60, 13 * 3600 + 15 * 60
WS = [60, 180, 300, 600]
HS = [60, 180, 300, 600]
COOL = 300.0


def fwd(t, px, tc, h):
    i0 = bisect_right(t, tc) - 1; i1 = bisect_right(t, tc + h) - 1
    if i0 < 0 or i1 <= i0 or tc + h > L.CLOSE_T:
        return None
    return (px[i1] / px[i0] - 1) * 1e4


def one_day(d):
    L.BIN = 5.0
    pan = L.load_day(P, d); rows, base = [], []
    for s, a in pan.items():
        side, bid, ask = L.classify(a)
        bn = L.bins(a, side)
        if bn is None or len(bn["b"]) < 60:
            continue
        c = a[:, 5] == 0
        t, px, dv = a[c, 0], a[c, 1], a[c, 2]
        okq = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid)
        mid = np.where(okq, (bid + ask) / 2.0, a[:, 1])[c]          # 結果用買賣中價（避免買賣價差彈跳假象）；事件判斷仍用成交價
        if len(t) < 200:
            continue
        cum = np.cumsum(dv)
        def vol_between(t0, t1):
            i0 = bisect_right(t, t0); i1 = bisect_right(t, t1)
            return (cum[i1 - 1] - (cum[i0 - 1] if i0 > 0 else 0)) if i1 > i0 else 0.0
        dr = np.sign(bn["o"] - bn["i"]).astype(int); nb = len(dr); k = 0; last_ev = -1e9
        while k < nb:
            if dr[k] == 0:
                k += 1; continue
            j = k
            while j + 1 < nb and dr[j + 1] == dr[k]:
                j += 1
            n = j - k + 1
            if n >= 10:
                dirn = int(dr[k]); t_lit = (bn["b"][k + 9] + 1) * 5.0; t_start = bn["b"][k] * 5.0
                if t_lit - last_ev >= COOL and OPEN_OK <= t_lit <= CLOSE_OK - 700:
                    i_a, i_b = bisect_left(t, t_start), bisect_right(t, t_lit)
                    if i_b - i_a >= 5:
                        H0 = px[i_a:i_b].max() if dirn > 0 else px[i_a:i_b].min()
                        pre_rate = vol_between(t_start - 1e-6, t_lit) / max(t_lit - t_start, 5.0)
                        for W in WS:
                            tc = t_lit + W
                            if tc + max(HS) > L.CLOSE_T:
                                continue
                            w0, w1 = bisect_right(t, t_lit), bisect_right(t, tc)
                            if w1 - w0 < 1:
                                continue
                            ext = px[w0:w1].max() if dirn > 0 else px[w0:w1].min()
                            no_break = (ext <= H0) if dirn > 0 else (ext >= H0)
                            ratio = (vol_between(t_lit, tc) / W) / pre_rate if pre_rate > 0 else np.nan
                            grp = "A創新高低" if not no_break else ("C沒破且量縮" if ratio < 0.5 else "B沒破但量未縮")
                            r = {"date": d, "sym": s, "dir": dirn, "n": n, "W": W, "grp": grp, "ratio": ratio, "t": tc,
                                 "spread": (ask[min(bisect_right(t, tc), len(ask) - 1)] - bid[min(bisect_right(t, tc), len(bid) - 1)]) / px[min(bisect_right(t, tc), len(px) - 1)] * 1e4}
                            for h in HS:
                                r[f"m{h}"] = fwd(t, mid, tc, h)
                            rows.append(r)
                    last_ev = t_lit
            # 舊定義：轉色（lit 後下一有成交窗反向）
            if n >= 10 and j + 1 < nb and dr[j + 1] == -dr[k]:
                tt = (bn["b"][j + 1] + 1) * 5.0
                if OPEN_OK <= tt <= CLOSE_OK - 700:
                    r = {"date": d, "sym": s, "dir": int(dr[k]), "n": n, "W": 0, "grp": "D轉色(舊)", "ratio": np.nan, "t": tt, "spread": np.nan}
                    for h in HS:
                        r[f"m{h}"] = fwd(t, mid, tt, h)
                    rows.append(r)
            k = j + 1
        for tt in np.arange(OPEN_OK, CLOSE_OK - 700, 60.0):
            r = {"date": d, "sym": s, "grp": "Base", "t": tt}
            for h in HS:
                r[f"m{h}"] = fwd(t, mid, tt, h)
            base.append(r)
    return rows, base


def summarize(ev, base):
    out = []
    for h in HS:
        col = f"m{h}"; bsub = base.dropna(subset=[col]); bsub = bsub[bsub[col] != 0]
        pfall = bsub.groupby("date")[col].apply(lambda x: (x < 0).mean())
        for (W, grp), g0 in ev.groupby(["W", "grp"]):
            x = g0.dropna(subset=[col]); x = x[x[col] != 0]
            if len(x) < 40: continue
            rev = np.where(x.dir == 1, x[col] < 0, x[col] > 0).astype(float)
            bp = x.date.map(pfall); bprob = np.where(x.dir == 1, bp, 1 - bp)
            x = x.assign(rev=rev, bprob=bprob, ret=-x.dir * x[col])
            g = x.groupby("date").agg(rev=("rev", "mean"), bprob=("bprob", "mean"), ret=("ret", "mean"), n=("rev", "size")); g = g[g.n >= 3]
            if len(g) < 8: continue
            lift = g.rev - g.bprob
            out.append({"W_min": W // 60, "grp": grp, "h_min": h // 60, "n": len(x), "days": len(g), "P_rev": x.rev.mean(), "P_base": x.bprob.mean(), "lift_pp": lift.mean() * 100,
                        "t_lift": lift.mean() / (lift.std(ddof=1) / np.sqrt(len(lift))), "ret_bps": x.ret.mean(), "t_ret": g.ret.mean() / (g.ret.std(ddof=1) / np.sqrt(len(g))),
                        "spread_med": float(np.nanmedian(x["spread"])) if ("spread" in x and x["spread"].notna().any()) else np.nan})
    return pd.DataFrame(out)


def main():
    days = [Path(f).stem for f in sorted(glob.glob(str(P / "*.pkl"))) if Path(f).stat().st_size > 1000]
    ev, base = [], []
    for d in days:
        r, b = one_day(d); ev += r; base += b
    ev, base = pd.DataFrame(ev), pd.DataFrame(base)
    print(f"{len(days)} 日；事件 {len(ev)}；基準 {len(base)}", flush=True)
    R = summarize(ev, base); R.to_csv(Path.home() / "goldenstocks-data/research/exhaust_nobreak_vol.csv", index=False)
    ev.to_pickle(Path.home() / "goldenstocks-data/research/exhaust_nobreak_vol_events.pkl")
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 400)
    for h in (1, 3, 5, 10):
        print(f"\n== 後續 {h} 分鐘（確認時刻之後）")
        print(R[R.h_min == h].sort_values(["W_min", "grp"])[["W_min", "grp", "n", "days", "P_rev", "P_base", "lift_pp", "t_lift", "ret_bps", "t_ret", "spread_med"]].round(3).to_string(index=False))
    # 劑量：窗口 W=1、C 組內依 ratio 三分位（只看 5 分鐘後續）
    for W in (60, 180):
        e = ev[(ev.W == W) & ev.ratio.notna() & (ev.grp != "A創新高低")]
        if len(e) > 300:
            e = e.assign(q=pd.qcut(e.ratio, 3, labels=["量縮最多", "中", "量縮最少"]))
            print(f"\n== 劑量（W={W//60}分、沒破前高低；量縮三分位；後續5分）")
            for q, g in e.groupby("q"):
                x = g.dropna(subset=["m300"]); x = x[x.m300 != 0]
                rev = np.where(x.dir == 1, x.m300 < 0, x.m300 > 0)
                print(f"  {q}: n={len(x)} P_rev={rev.mean():.3f} 反轉報酬 {(-x.dir * x.m300).mean():+.2f}bps")


if __name__ == "__main__":
    main()
