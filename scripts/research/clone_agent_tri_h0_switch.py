#!/usr/bin/env python3
"""研究用(預註冊):H0 能否用「進場當下已知」的大盤狀態當開關?

===== 預註冊規格(2026-10-07 19:38 CST,寫於任何統計程式之前;之後不得因看到結果而改動)=====
資料:~/goldenstocks-data/data/scratch/w5h_is/spot_signal.csv(IS 9/29–10/6)與 w5h/spot_signal.csv(HOLD 9/3–9/24)。
  欄位 var,day,sid,time,hold,trunc,gross,net,excess(bps)。trunc=True(未滿持有期)一律剔除。
  分析 var:'H0 三尺度∧距高∧額≥30'(主)、'V0 現行寬門檻'、'V0 現行寬門檻★4'(通用性檢查,同一規格)。

市場狀態(僅用 <= 卡片時間 t 的資料,PIT;t=卡片 time 的 epoch,不含進場延遲):
  籃子=13 檔名單(2327 2408 2455 2492 3037 3105 3189 3324 3532 6213 6274 6488 8046)等權現貨報酬。
  S1=自當日 09:00 後第一筆成交價(開盤參考)到 t 的籃子報酬 <= 0 -> 「開」。
  S2=t 前 30 分鐘籃子報酬 <= 0 -> 「開」。
  閾值固定 0,不掃參數,不做三分位。
  籃子某檔 t 時缺價就從等權中剔除該檔;可用檔數 < 8 則該筆狀態記缺失、不納入。
  操作化(同屬預註冊,先於看任何結果):
   - 價格來源=trader_signal_watch.Tape(day).poll() 的 _pxev(已濾試撮、開盤/收盤集合競價、每檔第一筆累計基準),
     「某時點價」=px_at(sid, 時點) 的最後成交價(<= 時點)。
   - 開盤參考=該檔 _pxev 中 t >= 09:00:00 的第一筆成交;若該筆晚於 09:10:00 則該檔 S1 開盤參考無效(視為缺價、剔除該檔)。
     (9/29 tape 10:12:44 才開始 -> 該日 S1 全缺;S2 在 t-30分 仍落在 tape 開始前者亦因缺價<8 檔而缺失。)
   - S2:若 t-30 分 < 09:00,窗起點改用同檔開盤參考(此時 S2 等於 S1);否則用 px_at(t-1800)。
   - tape 整體缺口:若某時點(t 或 S2 的起點)全籃子最近一筆成交距該時點皆 > 300 秒,視為 tape 缺口,該狀態記缺失
     (針對 9/9 11:32–13:08)。
   - 不另設單檔 stale 門檻。籃子含訊號本身標的。3189(9/10 前代號錯)、3324/3532(9/3–9/4 未訂閱)無資料則自然剔除。

假說 H_a(方向):H0 買的是相對落後標的(吸籌+回檔),大盤轉弱時才有反彈,故「開」組 H0 毛報酬(60 分,現貨訊號層級)> 「關」組。
  若相反(關組較好)也如實報;「開−關<0 且顯著」記為「反向結果」,不算通過。

結局:主要 gross60(bps);次要 excess60、gross90(同事件、各自剔除 trunc)。

日簇 bootstrap:先逐日取該組平均,再對「有該組資料的日」重抽(有放回,B=5000,seed=7),統計量=抽中日之日均的平均;
  CI=90%(第 5、95 百分位)。(開−關)差:重抽「兩組日聯集」,每次各組用抽中且有資料的日;任一組為空則該次略過。
  不使用 日×檔 簇。

通過標準(對 S1、S2 各自判定,全部須成立才算「開關可用」,不合併挑最好):
  (1) 開組 n >= 30(IS+HOLD 合併,gross60,剔 trunc 與狀態缺失)。
  (2) 開組 gross60 的日簇 bootstrap 90% CI 下界 > 0。
  (3) (開−關) 差的日簇 90% CI 不含 0,且差 > 0(H_a 方向)。
  (4) IS 與 HOLD 各自的「開−關」符號相同,且皆 > 0(任一側缺開或關資料則不通過)。
  (5) 控制同窗 bench 後(OLS gross60 ~ 1 + state + bench;bench=gross−excess,即同窗其餘 12 檔等權報酬;
      須 excess 非缺失)state 係數與未控制 bench 的(開−關)差(同一子樣本 OLS gross60 ~ 1 + state)同號且 > 0。
      另報:state 與 bench 的相關(事件層與日層)、state 係數的日簇 bootstrap 90% CI(僅報告,不影響通過)。
  S1、S2 兩個都過才不用修正多重比較;只過一個須註明 Bonferroni(α=0.05/2),並以 95% CI(第 2.5、97.5 百分位)重判 (2)(3)。
  通用性檢查:對三個 var 皆用同一規格跑;若狀態×報酬交互為真,V0/V0★4 大樣本應與 H0 同號(只報告,不改標準)。

之後任何額外探索一律標「事後」且不得進結論。
===================================================================================================
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_tri_h0_switch.py
"""
from __future__ import annotations

import bisect
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import trader_signal_watch as w  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
SRC = {"IS": S / "w5h_is" / "spot_signal.csv", "HOLD": S / "w5h" / "spot_signal.csv"}
BASKET = ["2327", "2408", "2455", "2492", "3037", "3105", "3189", "3324", "3532", "6213", "6274", "6488", "8046"]
VARS = ["H0 三尺度∧距高∧額≥30", "V0 現行寬門檻", "V0 現行寬門檻★4"]
MIN_STOCKS = 8
GAP_SEC = 300
OPEN_VALID_SEC = 10 * 60
B = 5000
FIX = os.environ.get("H0SW_ORIG") != "1"   # 實作修正旗標(見 state()):H0SW_ORIG=1 重現第一版
SUF = "" if FIX else "_orig"


def ep(day, hms):
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


class DayState:
    def __init__(self, day):
        self.day = day
        tape = w.Tape(day)
        tape.poll()
        self.ts, self.px = {}, {}
        for sid in BASKET:
            ev = tape._pxev.get(sid, [])
            self.ts[sid] = [t for t, _ in ev]
            self.px[sid] = [p for _, p in ev]
        t900 = ep(day, "09:00:00")
        self.open = {}
        for sid in BASKET:
            j = bisect.bisect_left(self.ts[sid], t900)
            if j < len(self.ts[sid]) and self.ts[sid][j] <= t900 + OPEN_VALID_SEC:
                self.open[sid] = self.px[sid][j]
        self.t900 = t900

    def last(self, sid, x):
        j = bisect.bisect_right(self.ts[sid], x)
        return (self.px[sid][j - 1], self.ts[sid][j - 1]) if j else (None, None)

    def gap(self, x):
        ages = [x - self.last(s, x)[1] for s in BASKET if self.last(s, x)[1] is not None]
        return (not ages) or min(ages) > GAP_SEC

    def state(self, t):
        """回傳 dict: s1,s2(True=開/False=關/None=缺),r1,r2,n1,n2,why1,why2"""
        out = {}
        # S1
        if self.gap(t):
            out.update(s1=None, r1=np.nan, n1=0, why1="tape缺口")
        else:
            rs = []
            for sid in BASKET:
                pt, _ = self.last(sid, t)
                if pt is not None and sid in self.open:
                    rs.append(pt / self.open[sid] - 1)
            if len(rs) < MIN_STOCKS:
                out.update(s1=None, r1=np.nan, n1=len(rs), why1="可用檔數<8")
            else:
                r = float(np.mean(rs))
                out.update(s1=bool(r <= 0), r1=r, n1=len(rs), why1="")
        # S2
        x0 = t - 1800
        clip = x0 < self.t900
        if FIX and not clip and not any(self.last(sid, x0)[1] is not None for sid in BASKET):
            clip = True   # 實作修正:x0 落在開盤第一筆成交之前(9:30 卡片的 x0≈09:00:xx),不是 tape 缺口,改用開盤參考
        if self.gap(t) or (not clip and self.gap(x0)):
            out.update(s2=None, r2=np.nan, n2=0, why2="tape缺口")
        else:
            rs = []
            for sid in BASKET:
                pt, _ = self.last(sid, t)
                if pt is None:
                    continue
                if clip:
                    p0 = self.open.get(sid)
                else:
                    p0, _ = self.last(sid, x0)
                if p0 is not None:
                    rs.append(pt / p0 - 1)
            if len(rs) < MIN_STOCKS:
                out.update(s2=None, r2=np.nan, n2=len(rs), why2="可用檔數<8")
            else:
                r = float(np.mean(rs))
                out.update(s2=bool(r <= 0), r2=r, n2=len(rs), why2="")
        return out


def load():
    parts = []
    for tag, p in SRC.items():
        d = pd.read_csv(p)
        d["src"] = tag
        d["sid"] = d.sid.astype(str)
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    df = df[df["var"].isin(VARS)]
    df = df[~df.trunc.astype(bool)].copy()
    cache = {}
    st = {}
    for d in sorted(df.day.unique()):
        ds = DayState(d)
        for tm in df[df.day == d].time.unique():
            st[(d, tm)] = ds.state(ep(d, tm))
        print("state done", d, flush=True)
    for k in ("s1", "s2", "r1", "r2", "n1", "n2", "why1", "why2"):
        df[k] = [st[(a, b)][k] for a, b in zip(df.day, df.time)]
    df["bench"] = df.gross - df.excess
    return df


def day_means(g, col):
    return g.groupby("day")[col].mean()


def boot_mean(g, col, rng, q=(5, 95)):
    dm = day_means(g.dropna(subset=[col]), col).values
    if len(dm) == 0:
        return np.nan, np.nan
    i = rng.integers(0, len(dm), (B, len(dm)))
    mu = dm[i].mean(1)
    return tuple(np.percentile(mu, q))


def boot_diff(on, off, col, rng, q=(5, 95)):
    a = day_means(on.dropna(subset=[col]), col)
    b = day_means(off.dropna(subset=[col]), col)
    days = np.array(sorted(set(a.index) | set(b.index)))
    if len(a) == 0 or len(b) == 0:
        return np.nan, np.nan
    av = a.reindex(days).values
    bv = b.reindex(days).values
    res = []
    for _ in range(B):
        j = rng.integers(0, len(days), len(days))
        x, y = av[j], bv[j]
        x, y = x[~np.isnan(x)], y[~np.isnan(y)]
        if len(x) and len(y):
            res.append(x.mean() - y.mean())
    return tuple(np.percentile(res, q))


def dm_diff(on, off, col):
    a, b = on[col].dropna(), off[col].dropna()
    if not len(a) or not len(b):
        return np.nan
    return day_means(on.dropna(subset=[col]), col).mean() - day_means(off.dropna(subset=[col]), col).mean()


def ols(y, X):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def state_coef(g, with_bench):
    g = g.dropna(subset=["bench"])
    X = np.column_stack([np.ones(len(g)), g.state.values.astype(float)] + ([g.bench.values] if with_bench else []))
    return ols(g.gross.values, X)[1]


def boot_coef(g, rng, with_bench):
    g = g.dropna(subset=["bench"])
    days = g.day.unique()
    byd = {d: x for d, x in g.groupby("day")}
    res = []
    for _ in range(2000):
        pick = rng.choice(days, len(days))
        h = pd.concat([byd[d] for d in pick])
        if h.state.nunique() < 2:
            continue
        res.append(state_coef(h, with_bench))
    return tuple(np.percentile(res, (5, 95)))


def analyze(df, var, sk, rng, report):
    g0 = df[(df["var"] == var) & (df.hold == 60)].copy()
    g0["state"] = g0[sk]
    miss = g0[g0.state.isna()]
    g = g0.dropna(subset=["state"]).copy()
    g["state"] = g.state.astype(bool)
    g90 = df[(df["var"] == var) & (df.hold == 90)].copy()
    g90["state"] = g90[sk]
    g90 = g90.dropna(subset=["state"])
    g90["state"] = g90.state.astype(bool)
    on, off = g[g.state], g[~g.state]
    res = dict(var=var, S=sk.upper(), n_all=len(g0), n_miss=len(miss),
               miss_why="; ".join(f"{k}:{v}" for k, v in miss[("why" + sk[1])].value_counts().items()) if len(miss) else "-")
    rows = []
    for nm, grp, grp90 in (("開", on, g90[g90.state]), ("關", off, g90[~g90.state])):
        lo, hi = boot_mean(grp, "gross", rng)
        rows.append(dict(var=var, S=sk.upper(), 組=nm, n=len(grp), 天=grp.day.nunique(), gross60=round(grp.gross.mean(), 1),
                         日均gross60=round(day_means(grp, "gross").mean(), 1) if len(grp) else np.nan,
                         CI90=f"{lo:.0f}~{hi:.0f}", excess60=round(grp.excess.mean(), 1) if len(grp) else np.nan, gross90=round(grp90.gross.mean(), 1) if len(grp90) else np.nan))
    report["table"].extend(rows)
    d = dm_diff(on, off, "gross")
    dlo, dhi = boot_diff(on, off, "gross", rng)
    dlo95, dhi95 = boot_diff(on, off, "gross", rng, q=(2.5, 97.5))
    lo95, hi95 = boot_mean(on, "gross", rng, q=(2.5, 97.5))
    lo90 = float(rows[0]["CI90"].split("~")[0]) if len(on) else np.nan
    lo90 = boot_mean(on, "gross", rng)[0] if len(on) else np.nan
    # IS / HOLD split
    sp = {}
    for src in ("IS", "HOLD"):
        a, b = on[on.src == src], off[off.src == src]
        sp[src] = dm_diff(a, b, "gross") if len(a) and len(b) else np.nan
    # (5)
    gb = g.dropna(subset=["bench"])
    c_raw = state_coef(gb, False) if gb.state.nunique() == 2 else np.nan
    c_ctl = state_coef(gb, True) if gb.state.nunique() == 2 else np.nan
    cl, ch = boot_coef(gb, rng, True) if gb.state.nunique() == 2 else (np.nan, np.nan)
    corr_ev = float(np.corrcoef(gb.state.astype(float), gb.bench)[0, 1]) if gb.state.nunique() == 2 else np.nan
    dd = gb.groupby("day").agg(s=("state", "mean"), b=("bench", "mean"))
    corr_day = float(np.corrcoef(dd.s, dd.b)[0, 1]) if len(dd) > 2 else np.nan
    crit = {
        "(1)開組n>=30": len(on) >= 30,
        "(2)開組CI下界>0": bool(lo90 > 0),
        "(3)差CI不含0且>0": bool(dlo > 0),
        "(4)IS/HOLD皆>0": bool(sp["IS"] > 0 and sp["HOLD"] > 0),
        "(5)控bench仍同號且>0": bool(c_raw > 0 and c_ctl > 0),
    }
    res.update(diff=d, diff_ci90=(dlo, dhi), diff_ci95=(dlo95, dhi95), on_ci95=(lo95, hi95), IS_diff=sp["IS"], HOLD_diff=sp["HOLD"],
               coef_raw=c_raw, coef_ctl=c_ctl, coef_ctl_ci90=(cl, ch), corr_event=corr_ev, corr_day=corr_day, crit=crit,
               all_pass=all(crit.values()), n_bench=len(gb), diff_excess=dm_diff(on, off, "excess"))
    report["res"].append(res)


def main():
    df = load()
    df.to_csv(S / f"h0_switch_events{SUF}.csv", index=False)
    rng = np.random.default_rng(7)
    report = dict(table=[], res=[])
    for var in VARS:
        for sk in ("s1", "s2"):
            analyze(df, var, sk, rng, report)
    pd.set_option("display.width", 250, "display.max_columns", 40)
    print("\n===== 各 var×狀態×(開/關)(hold=60 為主;gross90 同事件) =====")
    print(pd.DataFrame(report["table"]).to_string(index=False))
    print("\n===== 逐條通過標準 =====")
    for r in report["res"]:
        print(f"\n[{r['var']}] {r['S']}: 事件(60分,非截斷)={r['n_all']} 狀態缺失={r['n_miss']}({r['miss_why']}) 有bench={r['n_bench']}")
        print(f"  開−關 日均差={r['diff']:.1f}  90%CI={r['diff_ci90'][0]:.1f}~{r['diff_ci90'][1]:.1f}  95%CI={r['diff_ci95'][0]:.1f}~{r['diff_ci95'][1]:.1f}  excess差={r['diff_excess']:.1f}")
        print(f"  開組95%CI={r['on_ci95'][0]:.1f}~{r['on_ci95'][1]:.1f}  IS差={r['IS_diff']:.1f} HOLD差={r['HOLD_diff']:.1f}")
        print(f"  OLS state係數 未控bench={r['coef_raw']:.1f} 控bench={r['coef_ctl']:.1f} 90%CI={r['coef_ctl_ci90'][0]:.1f}~{r['coef_ctl_ci90'][1]:.1f}; corr(state,bench) 事件={r['corr_event']:.2f} 日={r['corr_day']:.2f}")
        print("  " + " | ".join(f"{k}:{'PASS' if v else 'FAIL'}" for k, v in r["crit"].items()) + f"  => {'開關可用' if r['all_pass'] else '不成立'}")
    print("\n===== 狀態缺失彙總(全事件含 hold=60/90) =====")
    for sk in ("1", "2"):
        d = df[(df.hold == 60)]
        x = d[d["s" + sk].isna()].groupby(["var", "day", "why" + sk]).size()
        print(f"S{sk}:\n{x.to_string() if len(x) else '無'}")


if __name__ == "__main__":
    main()
