#!/usr/bin/env python3
"""「連次達 ±10 後轉色」的後續反轉機率與最適尺度（唯讀研究）。

資料：富邦 ws 逐筆面板（exhaust_extract.py 產出；2026-09-03 起，每日約 36–45 檔儀表板追蹤池）。內外盤判定同 exhaust_lib（過斷言）。
事件（PIT：只用 ≤ 觸發時刻的資料，觸發 = 該 5 秒窗收盤）：
  E_turn_N  ：同向連攻段 n≥N 之後，下一個有成交的窗『反向主導（轉色）』的那個窗收盤。買盤竭盡＝紅(外盤)連攻後轉綠；賣盤竭盡＝綠連攻後轉紅。
  E_lit_N   ：連次剛達 N 的那個窗收盤（亮燈、尚未轉色；對照用——轉色是否比單純亮燈多出資訊）。
  Base      ：同股同日每 60 秒一個無條件時點（09:05–13:15）。
反轉＝買盤竭盡後價格下跌 / 賣盤竭盡後價格上漲；反轉機率＝P(反轉 | 價格有變動)（平盤另報）。價格＝觸發後最近一筆成交價。
差（lift）＝事件反轉機率 − 同日同方向無條件基準；以『日期』為叢集（每日取平均，再對日間做 t）。
尺度：A) 官方 5 秒窗，N=10，後續視窗 1/3/5/10 分鐘；B) 連次改用 1/3/5/10 分鐘窗（K 根同向），後續視窗＝1 根與 2 根。
"""
from __future__ import annotations
import glob, sys
from bisect import bisect_right
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import exhaust_lib as L

P = Path.home() / "goldenstocks-data/research/exhaust_panel"
OPEN_OK, CLOSE_OK = 9 * 3600 + 5 * 60, 13 * 3600 + 15 * 60


def run_events(bn, bin_s, N):
    """逐窗掃描；回傳 [(dir, n, kind, trigger_t)]，kind ∈ {'lit','turn'}。"""
    dr = np.sign(bn["o"] - bn["i"]).astype(int); ev = []; k = 0; nb = len(dr)
    while k < nb:
        if dr[k] == 0:
            k += 1; continue
        j = k
        while j + 1 < nb and dr[j + 1] == dr[k]:
            j += 1
        n = j - k + 1
        if n >= N:
            ev.append((int(dr[k]), n, "lit", (bn["b"][k + N - 1] + 1) * bin_s))
            if j + 1 < nb and dr[j + 1] == -dr[k]:                  # 轉色（嚴格反向主導；平手不算）
                ev.append((int(dr[k]), n, "turn", (bn["b"][j + 1] + 1) * bin_s))
        k = j + 1
    return ev


def outcome(t, px, tt, h):
    i0 = bisect_right(t, tt) - 1
    i1 = bisect_right(t, tt + h) - 1
    if i0 < 0 or i1 <= i0 or tt + h > L.CLOSE_T:
        return None
    return (px[i1] / px[i0] - 1) * 1e4                                # bps，價格變動（正=上漲）


def day_rows(d, bin_s, N, horizons):
    rows, base = [], []
    pan = L.load_day(P, d)
    L.BIN = bin_s
    for s, a in pan.items():
        side, bid, ask = L.classify(a)
        bn = L.bins(a, side)
        if bn is None or len(bn["b"]) < 30:
            continue
        c = a[:, 5] == 0
        t, px = a[c, 0], a[c, 1]
        if len(t) < 200:
            continue
        for dirn, n, kind, tt in run_events(bn, bin_s, N):
            if not (OPEN_OK <= tt <= CLOSE_OK):
                continue
            r = {"date": d, "sym": s, "dir": dirn, "n": n, "kind": kind, "t": tt}
            for h in horizons:
                r[f"m{h}"] = outcome(t, px, tt, h)
            rows.append(r)
        for tt in np.arange(OPEN_OK, CLOSE_OK, 60.0):                  # 無條件基準（同股同日）
            r = {"date": d, "sym": s, "kind": "base", "t": tt}
            for h in horizons:
                r[f"m{h}"] = outcome(t, px, tt, h)
            base.append(r)
    return rows, base


def summarize(ev, base, horizons, label):
    out = []
    for h in horizons:
        col = f"m{h}"
        # 基準：每日、每方向 P(下跌 | 有變動)；對 dir=+1(買盤竭盡) 反轉=下跌，dir=-1 反轉=上漲
        bsub = base.dropna(subset=[col]); bsub = bsub[bsub[col] != 0]
        bday = bsub.groupby("date")[col].apply(lambda x: (x < 0).mean())             # P(跌)
        for kind in ("lit", "turn"):
            e = ev[(ev.kind == kind)].dropna(subset=[col]); e = e[e[col] != 0]
            for dirn, name in ((1, "買盤竭盡(紅→)"), (-1, "賣盤竭盡(綠→)"), (0, "合併")):
                x = e if dirn == 0 else e[e.dir == dirn]
                if len(x) < 30:
                    continue
                rev = np.where(x.dir == 1, x[col] < 0, x[col] > 0).astype(float)
                x = x.assign(rev=rev, ret_rev=-x.dir * x[col])                 # 反轉方向的報酬(bps)，正=反轉
                bp = x.date.map(bday)
                bprob = np.where(x.dir == 1, bp, 1 - bp)
                x = x.assign(bprob=bprob)
                g = x.groupby("date").agg(rev=("rev", "mean"), bprob=("bprob", "mean"), ret=("ret_rev", "mean"), n=("rev", "size"))
                g = g[g.n >= 3]
                if len(g) < 8:
                    continue
                lift = g.rev - g.bprob
                t = lift.mean() / (lift.std(ddof=1) / np.sqrt(len(lift)))
                tr = g.ret.mean() / (g.ret.std(ddof=1) / np.sqrt(len(g)))
                out.append({"scale": label, "h_s": h, "kind": kind, "dir": name, "n": len(x), "days": len(g), "P_rev": x.rev.mean(), "P_base": x.bprob.mean(),
                            "lift_pp": lift.mean() * 100, "t_lift": t, "ret_rev_bps": x.ret_rev.mean(), "t_ret": tr})
    return pd.DataFrame(out)


def main():
    days = sorted(Path(f).stem for f in glob.glob(str(P / "*.pkl")))
    days = [d for d in days if Path(P / f"{d}.pkl").stat().st_size > 1000]
    print(f"{len(days)} 個交易日", flush=True)
    res = []
    # A) 官方 5 秒窗，N=10 與 N=7（劑量對照），後續視窗 1/3/5/10 分
    HA = [60, 180, 300, 600]
    for N in (10, 7):
        ev, base = [], []
        for d in days:
            r, b = day_rows(d, 5.0, N, HA); ev += r; base += b
        ev, base = pd.DataFrame(ev), pd.DataFrame(base)
        print(f"[5秒窗 N={N}] 事件 {len(ev)}（lit {int((ev.kind=='lit').sum())}／turn {int((ev.kind=='turn').sum())}），基準點 {len(base)}", flush=True)
        res.append(summarize(ev, base, HA, f"5秒窗 N≥{N}"))
    # B) 連次改用 1/3/5/10 分鐘窗
    for B, K in ((60, 5), (60, 3), (180, 3), (300, 3), (600, 3), (600, 2)):
        HB = [B, 2 * B]
        ev, base = [], []
        for d in days:
            r, b = day_rows(d, float(B), K, HB); ev += r; base += b
        ev, base = pd.DataFrame(ev), pd.DataFrame(base)
        print(f"[{B//60}分鐘窗 K={K}] 事件 {len(ev)}（turn {int((ev.kind=='turn').sum())}）", flush=True)
        res.append(summarize(ev, base, HB, f"{B//60}分鐘窗 K≥{K}"))
    R = pd.concat(res, ignore_index=True)
    R.to_csv(Path.home() / "goldenstocks-data/research/exhaust_reversal_scale.csv", index=False)
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 300)
    for lab, g in R.groupby("scale", sort=False):
        print(f"\n== {lab}")
        print(g[g.dir == "合併"][["kind", "h_s", "n", "days", "P_rev", "P_base", "lift_pp", "t_lift", "ret_rev_bps", "t_ret"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
