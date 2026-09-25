#!/usr/bin/env python3
"""持有期研究共用模組(2026-09-25):127 日 5 分桶面板 + V2.5 分數路徑 + 事件產生 + 進出場模擬。

供多 agent 探索共用同一套口徑(避免各自重建 plumbing 產生不一致)。

用法(在 scripts/research 下 import):
    from biglot_hold_lab import load_lab, make_events, simulate, cl_t
    L = load_lab()                       # dict:panel d、價路徑 px、宇宙路徑 uidx、分數路徑 sc、壞標籤 bad、市場 mk、昨午後低 pmlow、buckets
    ev = make_events(L, kind="score", th=15)             # 或 kind="state", name="急跌·真空"
    res = simulate(L, ev, exit_rule=lambda p: ..., entry="close")   # DataFrame:每事件 ret/ex/hold/…
面板:pit100 × 2026-03-02~08-31;IS ≤06-30、OOS 07-01~。價=桶收(label=桶起點,09:00…13:20)。
路徑物件 p(dict):r[k]=進場後第 k 桶(5 分)的原始報酬 bps(k=0..12)、ur[k]=宇宙、s[k]=V2.5 分數、bad[k]=壞標籤、
mr[k]=大盤末30秒、brk[k]=觸昨午後低、w5[k]、r30[k]、sell[k]=30秒主動賣占比、b5n[k]=大戶5分淨占比、rb5[k]=散戶買參與。
exit_rule(p) 回傳出場桶 k(1..12)。entry:"close"=桶收價;"next"=下一桶收價(延遲 5 分);"dip:<bps>"=之後 N 桶內觸及進場價−bps 才成交。
"""
from __future__ import annotations
import numpy as np, pandas as pd
from pathlib import Path
from biglot_score_v23_fit import build, cl_t  # noqa: F401
from biglot_score_v25_fit import items25, V24

EX = Path.home() / "goldenstocks-data/scratch/biglot_panels/pit100_bucket5_exhaust_2026-03_08.csv"
PANEL = Path.home() / "goldenstocks-data/scratch/biglot_panels/pit100_bucket5_panel_2026-03_08.csv"
W25 = dict(V24); W25.update({"散戶接跌": 0, "急跌·賣壓未竭": 3, "急跌·真空": 12.5, "急跌·竭盡散戶接": -5.5, "急跌·大戶接∧未竭": 6,
                              "急跌·末30秒續跌": 2, "急拉·買壓竭盡": 4.5, "急拉·末30秒續漲": -3.5})
BAD = ("散戶虛拉", "過熱200-300", "過熱300-400", "過熱400-600", "急跌·竭盡散戶接")
IS_END = "2026-06-30"


def load_lab() -> dict:
    d = build().merge(pd.read_csv(EX, dtype={"sid": str}), on=["sid", "date", "bucket"], how="left")
    it = items25(d); names = list(it)
    X = np.column_stack([it[k][0].fillna(False).astype(float).values for k in names])
    d["s"] = np.clip(X @ np.array([W25.get(k, 0.0) for k in names]), -40, 40)
    for k in names:
        d["it:" + k] = it[k][0].fillna(False).astype(bool).values
    d["bad"] = np.logical_or.reduce([d["it:" + k].values for k in BAD])
    gb = d.groupby(["date", "bucket"])
    d["mkt_w5"] = gb["w5"].transform("mean"); d["mkt_r30s"] = gb["r30s"].transform("mean"); d["mkt_r30"] = gb["r30"].transform("mean")
    d["follow"] = (d["mkt_w5"] <= -20) & ((d["w5"] - d["mkt_w5"]) > -10)
    d["self_kill"] = (d["r30s"] <= -10) & (d["mkt_r30s"] >= 0)
    full = pd.read_csv(PANEL, dtype={"sid": str})
    px = full.pivot_table(index=["sid", "date"], columns="bucket", values="last_px"); buckets = list(px.columns)
    rel = px.div(px.iloc[:, 0], axis=0); uidx = rel.groupby(level="date").mean()
    dates = sorted(px.index.get_level_values("date").unique()); prev = {dates[i]: dates[i - 1] for i in range(1, len(dates))}
    pmlow = px[[b for b in buckets if b >= "12:00"]].min(axis=1)
    def piv(col, fill):
        return d.pivot_table(index=["sid", "date"], columns="bucket", values=col).reindex(columns=buckets).fillna(fill)
    paths = {"s": piv("s", 0.0), "bad": piv("bad", False), "mr": piv("mkt_r30s", 0.0), "w5": piv("w5", 0.0), "r30": piv("r30", 0.0),
             "sell": piv("sell30s", np.nan), "b5n": piv("b5n", 0.0), "rb5": piv("rbuy5", 0.0), "mkw5": piv("mkt_w5", 0.0)}
    d = d.sort_values(["sid", "date", "bucket"]).reset_index(drop=True)
    return {"d": d, "px": px, "uidx": uidx, "buckets": buckets, "prev": prev, "pmlow": pmlow, "paths": paths, "names": names}


def make_events(L: dict, kind="score", th=15.0, name=None, first_only=True, max_bucket="12:20", extra_mask=None) -> pd.DataFrame:
    """kind='score':s>=th;kind='state':it:<name> 為真;first_only=同日同檔連續成立只取首桶。回傳 sid/date/bucket/s/… 欄。"""
    d = L["d"]
    m = (d["s"] >= th) if kind == "score" else d["it:" + name].astype(bool)
    if extra_mask is not None:
        m = m & extra_mask
    if first_only:
        m = m & ~(m.shift(1, fill_value=False) & (d["sid"].shift(1) == d["sid"]) & (d["date"].shift(1) == d["date"]))
    e = d[m & (d["bucket"] <= max_bucket)].copy()
    e["k_same"] = e.groupby(["date", "bucket"])["sid"].transform("size")
    e["is_"] = e["date"] <= IS_END; e["is"] = e["is_"]
    return e


def path(L: dict, sid: str, date: str, b0: str, horizon=12):
    buckets = L["buckets"]; i = buckets.index(b0)
    if (sid, date) not in L["px"].index: return None
    p = L["px"].loc[(sid, date)].iloc[i:i + horizon + 1].values; u = L["uidx"].loc[date].iloc[i:i + horizon + 1].values
    if len(p) < horizon + 1 or np.isnan(p).any(): return None
    out = {"r": (p / p[0] - 1) * 1e4, "ur": (u / u[0] - 1) * 1e4, "px": p}
    for k, P in L["paths"].items():
        out[k] = P.loc[(sid, date)].iloc[i:i + horizon + 1].values if (sid, date) in P.index else np.zeros(horizon + 1)
    pl = L["pmlow"].get((sid, L["prev"].get(date)), np.nan)
    out["brk"] = (p <= pl * 1.002) if not np.isnan(pl) else np.zeros(horizon + 1, bool)
    return out


def exit_score_le(th=0.0, consecutive=1, grace=0, cap=12):
    """分數 ≤th 連續 consecutive 桶(進場後前 grace 桶不看)→ 出;否則 cap。"""
    def f(p):
        run = 0
        for k in range(1, cap + 1):
            if k > grace and p["s"][k] <= th:
                run += 1
                if run >= consecutive: return k
            else:
                run = 0
        return cap
    return f


def simulate(L: dict, ev: pd.DataFrame, exit_rule, entry="close", horizon=12, cost_bps=0.0) -> pd.DataFrame:
    """每事件:依 entry 決定進場桶/價,exit_rule(p) 決定出場桶。ret/ex 以進場價為基準(bps),扣 cost。"""
    rows = []
    for e in ev.itertuples():
        p = path(L, e.sid, e.date, e.bucket, horizon)
        if p is None: continue
        k0 = 0; entry_px = p["px"][0]
        if entry == "next":
            k0 = 1; entry_px = p["px"][1]
        elif entry.startswith("dip:"):
            dip = float(entry.split(":")[1]); target = p["px"][0] * (1 - dip / 1e4); k0 = None
            for k in range(1, 7):
                if p["px"][k] <= target: k0 = k; entry_px = target; break
            if k0 is None:
                rows.append({"sid": e.sid, "date": e.date, "bucket": e.bucket, "is": e.is_, "filled": False, "ret": 0.0, "ex": 0.0, "hold": 0, "k_exit": None, "k_same": e.k_same, "s0": e.s}); continue
        q = {k: v[k0:] for k, v in p.items()}
        q["r"] = (q["px"] / entry_px - 1) * 1e4; q["ur"] = (q["ur"] - q["ur"][0])
        kx = exit_rule(q); kx = min(kx, len(q["r"]) - 1)
        rows.append({"sid": e.sid, "date": e.date, "bucket": e.bucket, "is": e.is_, "filled": True, "ret": q["r"][kx] - cost_bps, "ex": q["r"][kx] - q["ur"][kx] - cost_bps,
                     "hold": kx * 5, "k_exit": kx, "k_same": e.k_same, "s0": e.s, "min_s": float(np.min(q["s"][1:kx + 1])) if kx >= 1 else float(q["s"][0]),
                     "r60": p["r"][horizon], "ex60": p["r"][horizon] - p["ur"][horizon]})
    return pd.DataFrame(rows)


def summary(res: pd.DataFrame, label="") -> str:
    """IS/OOS:超額均/日t、原始、勝率、均持、SD、n(獨立=事件數)。"""
    out = []
    for lab, m in (("IS", res["is"]), ("OOS", ~res["is"])):
        g = res[m & res["filled"]]
        if len(g) < 20: out.append(f"{lab} n<20"); continue
        mu, t = cl_t(g["ex"].values, g["date"].values)
        out.append(f"{lab} n={len(g):5d} 超額 {mu:+6.1f} (t{t:+4.1f}) 原始 {g['ret'].mean():+6.1f} 勝 {(g['ret']>0).mean()*100:3.0f}% 持 {g['hold'].mean():4.0f}分 SD {g['ret'].std():5.0f}")
    fill = res["filled"].mean() * 100 if len(res) else 0
    return f"{label:40s} | " + " | ".join(out) + (f" | 成交 {fill:.0f}%" if fill < 100 else "")


if __name__ == "__main__":
    L = load_lab(); ev = make_events(L, "score", 15)
    print("事件", len(ev))
    for lab, rule in (("固定60", lambda p: 12), ("分數≤0出", exit_score_le(0)), ("分數≤0 寬限10分", exit_score_le(0, grace=2))):
        print(summary(simulate(L, ev, rule), lab))
