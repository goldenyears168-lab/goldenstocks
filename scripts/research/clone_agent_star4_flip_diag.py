#!/usr/bin/env python3
"""唯讀診斷:V0★4 在 IS(9/29–10/6, 6天) 與 HOLD(9/3–9/24, 16天) 的反轉拆解。
資料:scratch/w5h{,_is}/spot_signal.csv(hold=60)+ 卡片 cands + ledger_V0(期貨口徑) + raw tape(算每日名單等權開→收)。
用法: nice PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_star4_flip_diag.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import trader_signal_watch as w  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
V = "V0 現行寬門檻★4"
rng = np.random.default_rng(7)
pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 200)


def ep(day, hms):
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


def load(tag, d, cdir):
    s = pd.read_csv(S / d / "spot_signal.csv")
    s = s[(s["var"] == V) & (s.hold == 60)].copy()
    s["seg"] = tag
    s["sid"] = s.sid.astype(str)
    cards = {}
    for p in (S / cdir).glob("candidates_*.jsonl"):
        for x in p.read_text(encoding="utf-8").splitlines():
            if x.strip():
                c = json.loads(x)
                cards[(c["day"], c["time"], str(c["sid"]))] = c
    for k in ("price", "dist_hi", "amt_pct", "chg_cm", "uname"):
        s[k] = [cards.get((r.day, r.time, r.sid), {}).get(k) for r in s.itertuples()]
    s["hh"] = [int(t[:2]) + int(t[3:5]) / 60 for t in s.time]
    s["slot"] = pd.cut(s.hh, [0, 9.5, 10, 11, 12, 13, 24], right=False, labels=["09:00-09:30", "09:30-10", "10-11", "11-12", "12-13", "13+"])
    return s


def boot_cl(g, col, n=4000):
    t = pd.DataFrame({"v": g[col].values, "c": (g.day + "_" + g.sid).values}).groupby("c").v.agg(["sum", "count"])
    i = rng.integers(0, len(t), (n, len(t)))
    mu = t["sum"].values[i].sum(1) / t["count"].values[i].sum(1)
    return np.percentile(mu, [5, 95])


def mkt_state(days, members):
    out = {}
    for d in days:
        tape = w.Tape(d)
        tape.poll()
        t0, t1 = ep(d, "09:30:00"), ep(d, "13:30:00")
        r = []
        for s in members:
            a, _ = tape.px_at(s, t0)
            b, _ = tape.px_at(s, t1)
            if a and b:
                r.append(1e4 * (b / a - 1))
        out[d] = (np.mean(r), np.median(r), len(r))
    return out


def main():
    is_, ho = load("IS", "w5h_is", "clone_agent_w5_nogate"), load("HOLD", "w5h", "w5h/cands")
    a = pd.concat([is_, ho], ignore_index=True)
    a["beta"] = a.gross - a.excess

    print("== 0 總覽(hold=60)")
    for sg, g in a.groupby("seg"):
        lo, hi = boot_cl(g, "gross")
        print(sg, "n", len(g), "天", g.day.nunique(), "簇", (g.day + g.sid).nunique(), "毛", round(g.gross.mean(), 1), f"CI {lo:.0f}~{hi:.0f}",
              "超額", round(g.excess.mean(), 1), "beta", round(g.beta.mean(), 1), "淨", round(g.net.mean(), 1), "截斷", int(g.trunc.sum()))

    print("\n== 1 逐日")
    dd = a.groupby(["seg", "day"]).agg(n=("gross", "size"), gross=("gross", "mean"), excess=("excess", "mean"), nsid=("sid", "nunique")).round(1)
    print(dd)
    for sg, g in a.groupby("seg"):
        day_m = g.groupby("day").gross.mean()
        sid_m = g.groupby("sid").gross.mean()
        dsum = g.groupby("day").gross.sum().sort_values()
        base = g.gross.mean()
        for lab, drop in (("去最好日", dsum.index[-1]), ("去最差日", dsum.index[0])):
            h = g[g.day != drop]
            print(sg, lab, drop, "n", len(h), "毛", round(h.gross.mean(), 1))
        h = g[~g.day.isin([dsum.index[-1], dsum.index[0]])]
        print(sg, "去最好+最差日 n", len(h), "毛", round(h.gross.mean(), 1), "| 全部", round(base, 1), "| 日平均的平均", round(day_m.mean(), 1), "正日", int((day_m > 0).sum()), "/", len(day_m))
        sg_sum = g.groupby("sid").gross.sum().sort_values()
        for lab, drop in (("去最好sid", sg_sum.index[-1]), ("去最差sid", sg_sum.index[0])):
            h = g[g.sid != drop]
            print(sg, lab, drop, "n", len(h), "毛", round(h.gross.mean(), 1))
        print(sg, "各sid 筆數/平均毛(前8依n):")
        print(g.groupby(["sid", "uname"]).gross.agg(["size", "mean"]).round(1).sort_values("size", ascending=False).head(8).T.to_string())

    print("\n== 2 組成")
    print("時段分布(筆數佔比%)與時段平均毛")
    t = a.groupby(["seg", "slot"], observed=True).gross.agg(["size", "mean"]).round(1)
    t["pct"] = (100 * t["size"] / t.groupby("seg")["size"].transform("sum")).round(0)
    print(t)
    print(a.groupby("seg")[["price", "dist_hi", "amt_pct", "chg_cm", "hh"]].agg(["mean", "median"]).round(2).T)
    si, sh = set(is_.sid), set(ho.sid)
    print("IS sid", len(si), "HOLD sid", len(sh), "僅HOLD", sorted(sh - si), "僅IS", sorted(si - sh), "共同", sorted(si & sh))
    onlyh = ho[ho.sid.isin(sh - si)]
    com = ho[ho.sid.isin(si & sh)]
    print("HOLD 僅HOLD標的 n", len(onlyh), "毛", round(onlyh.gross.mean(), 1), "| HOLD 與IS共同標的 n", len(com), "毛", round(com.gross.mean(), 1))
    print("共同標的 IS 毛", round(is_[is_.sid.isin(si & sh)].gross.mean(), 1), "n", int(is_.sid.isin(si & sh).sum()))
    print("dist_hi 分桶:")
    a["dh"] = pd.cut(a.dist_hi, [-99, -3, -2, -1.5, -1.0001])
    print(a.groupby(["seg", "dh"], observed=True).gross.agg(["size", "mean"]).round(1))

    print("\n== 3 beta vs 個股特有")
    print(a.groupby("seg")[["gross", "beta", "excess"]].mean().round(1))
    for sg, g in a.groupby("seg"):
        lo, hi = boot_cl(g.dropna(subset=["excess"]), "excess")
        print(sg, "excess CI", f"{lo:.0f}~{hi:.0f}")

    print("\n== 4 市場狀態(名單等權 09:30→13:30)")
    members = universe_sids(load_rules())
    ms = mkt_state(sorted(a.day.unique()), members)
    a["mk"] = a.day.map(lambda d: ms[d][0])
    a["down"] = a.mk < 0
    mdf = pd.DataFrame(ms, index=["ew_mean", "ew_med", "n"]).T.round(1)
    mdf["seg"] = ["IS" if d in set(is_.day) else "HOLD" for d in mdf.index]
    mdf["n4"] = a.groupby("day").size()
    mdf["g4"] = a.groupby("day").gross.mean().round(1)
    print(mdf)
    print(mdf.groupby("seg")[["ew_mean"]].agg(["mean", "median", lambda x: (x < 0).mean()]).round(1))
    print(a.groupby(["seg", "down"]).gross.agg(["size", "mean"]).round(1))
    print("日級 corr(名單開→收, ★4日平均毛):", round(mdf[["ew_mean", "g4"]].corr().iloc[0, 1], 2))

    print("\n== 5 與期貨口徑 ledger_V0(HOLD)")
    L = pd.read_csv(S / "w5h/ledger_V0.csv")
    L4 = L[L.stars == 4].copy() if "stars" in L else L
    L4["sid"] = L4.sid.astype(str)
    print("期貨口徑★4 n", len(L4), "毛", round(L4.gross_bps.mean(), 1), "淨", round(L4.net.mean()), "sid", sorted(L4.sid.unique()))
    share = sorted(set(L4.sid) & sh)
    print("與 93 筆共同 sid", share, "| 93 筆中屬期貨 sid 的筆數", int(ho.sid.isin(L4.sid).sum()), "毛", round(ho[ho.sid.isin(L4.sid)].gross.mean(), 1),
          "| 非期貨 sid 筆數", int((~ho.sid.isin(L4.sid)).sum()), "毛", round(ho[~ho.sid.isin(L4.sid)].gross.mean(), 1))
    k = set(zip(L4.day, L4.signal, L4.sid)) if "signal" in L4 else set()
    ho["inL"] = [(r.day, r.time, r.sid) in k for r in ho.itertuples()]
    print("同(day,time,sid)匹配", int(ho.inL.sum()), "筆 現貨毛", round(ho[ho.inL].gross.mean(), 1), "vs 期貨毛 (同筆)", round(L4.gross_bps.mean(), 1))

    print("\n== 6 差異檢定(IS−HOLD 平均毛)")
    def diff_stat(df):
        return df[df.seg == "IS"].gross.mean() - df[df.seg == "HOLD"].gross.mean()
    obs = diff_stat(a)
    # 簇 bootstrap:各段內重抽簇
    def seg_boot(g, n):
        t = pd.DataFrame({"v": g.gross.values, "c": (g.day + "_" + g.sid).values}).groupby("c").v.agg(["sum", "count"])
        i = rng.integers(0, len(t), (n, len(t)))
        return t["sum"].values[i].sum(1) / t["count"].values[i].sum(1)
    bd = seg_boot(is_, 10000) - seg_boot(ho, 10000)
    print("觀察差", round(obs, 1), "簇bootstrap 90%CI", np.percentile(bd, [5, 95]).round(0), "95%CI", np.percentile(bd, [2.5, 97.5]).round(0))
    # 以「日」為簇的置換:打亂日的段標籤(22天)
    days = sorted(a.day.unique())
    isd = set(is_.day)
    g = a.groupby("day").gross.agg(["sum", "count"])
    cnt = 0
    N = 20000
    for _ in range(N):
        pick = rng.choice(days, len(isd), replace=False)
        m1 = g.loc[pick]
        rest = g.drop(pick)
        d = m1["sum"].sum() / m1["count"].sum() - rest["sum"].sum() / rest["count"].sum()
        cnt += abs(d) >= abs(obs)
    print("日級置換(打亂22天段標籤) 雙尾 p =", round((cnt + 1) / (N + 1), 4))
    # 日級單位 Welch
    dm = a.groupby(["seg", "day"]).gross.mean()
    x, y = dm["IS"].values, dm["HOLD"].values
    se = np.sqrt(x.var(ddof=1) / len(x) + y.var(ddof=1) / len(y))
    print("日均單位 IS", x.mean().round(1), "(n", len(x), ") HOLD", y.mean().round(1), "(n", len(y), ") Welch t =", round((x.mean() - y.mean()) / se, 2))
    # 超額口徑
    ex = a.dropna(subset=["excess"])
    print("超額差", round(ex[ex.seg == "IS"].excess.mean() - ex[ex.seg == "HOLD"].excess.mean(), 1))
    # 單段是否異於0(日級)
    for sg, v in (("IS", x), ("HOLD", y)):
        print(sg, "日均 vs 0 t =", round(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))), 2))
    a.to_csv(S / "star4_flip_diag_rows.csv", index=False)


if __name__ == "__main__":
    main()
