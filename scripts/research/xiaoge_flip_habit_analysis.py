#!/usr/bin/env python3
"""隔日沖慣性全市場檢定·階段 2：H1～H5（協議 docs/warrant-xiaoge/flip-habit-study-protocol.md，參數已凍結）。

輸入：階段 1 的 events.pkl；唯讀 stocks.db（日線）；config/xiaoge_named_branches.json。
輸出：`${DATA}/research/xiaoge_flip_habit/results.json` 與 `results.md`。
用法：nice PYTHONPATH=src .venv/bin/python scripts/research/xiaoge_flip_habit_analysis.py
"""
from __future__ import annotations

import json
import pickle
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

ROOT = Path(__file__).resolve().parents[2]
DB = Path.home() / "goldenstocks-data/data/stocks.db"
OUT = Path.home() / "goldenstocks-data/research/xiaoge_flip_habit"
EVAL_FROM, IS_END = "2024-10-01", "2025-06-30"
LIQ = 3e7
R: dict = {}


def cluster_diff(y: np.ndarray, g: np.ndarray, date: np.ndarray, pair: np.ndarray | None = None) -> dict:
    """y ~ 1 + g；叢集標準誤＝日期（單向）；若給 pair（股×分點）則改日期＋pair 雙向叢集（較保守）。"""
    if g.sum() < 30 or (1 - g).sum() < 30:
        return {"n_hab": int(g.sum()), "n_non": int((1 - g).sum())}
    X = sm.add_constant(g.astype(float))
    grp = pd.factorize(date)[0] if pair is None else np.column_stack([pd.factorize(date)[0], pd.factorize(pair)[0]])
    m = sm.OLS(y.astype(float), X).fit(cov_type="cluster", cov_kwds={"groups": grp})
    b, se = float(m.params[1]), float(m.bse[1])
    return {"n_hab": int(g.sum()), "n_non": int((1 - g).sum()), "rate_hab": float(y[g == 1].mean()), "rate_non": float(y[g == 0].mean()),
            "diff": b, "se": se, "ci_lo": b - 1.96 * se, "ci_hi": b + 1.96 * se, "t": b / se if se > 0 else None}


def auc(score: np.ndarray, y: np.ndarray) -> float:
    s = pd.Series(score)
    r = s.rank(method="average").to_numpy()
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def main():
    d = pickle.load(open(OUT / "events.pkl", "rb"))
    ev, cal = d["events"], d["cal"]
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    bars = pd.read_sql_query(
        "select stock_id, trade_date, open, close, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') "
        "and trade_date between '2024-05-15' and '2026-07-20' and stock_id glob '[1-9][0-9][0-9][0-9]'", c)
    c.close()
    bars = bars.drop_duplicates(["stock_id", "trade_date"]).sort_values(["stock_id", "trade_date"])
    bars["tv"] = bars["close"] * bars["volume"]
    bars["tv20"] = bars.groupby("stock_id")["tv"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())   # 前 20 日，不含當日（PIT）
    ev["date"] = ev["idx"].map(lambda i: cal[i])
    ev["ndate"] = ev["idx"].map(lambda i: cal[i + 1])
    ev = ev.merge(bars[["stock_id", "trade_date", "tv20"]].rename(columns={"trade_date": "date"}), on=["stock_id", "date"], how="left")
    ev["liq"] = ev["tv20"] >= LIQ
    ev["period"] = np.where(ev["date"] <= IS_END, "IS", "OOS")
    P = ev[(ev["in_p"]) & (ev["date"] >= EVAL_FROM)].copy()
    R["events_total"] = int(len(ev)); R["population_all"] = int(len(P)); R["population_liquid"] = int(P["liq"].sum())
    Pl = P[P["liq"]].copy()
    R["stocks_liquid"] = int(Pl["stock_id"].nunique()); R["branches_liquid"] = int(Pl["br"].nunique())

    # ---------- H1 ----------
    R["H1"] = {}
    for per, sub in (("OOS", Pl[Pl.period == "OOS"]), ("IS", Pl[Pl.period == "IS"]), ("ALL", Pl)):
        for kind in ("A", "B"):
            y = sub[f"f{kind}70"].to_numpy().astype(int)
            hab = (sub[f"{kind}70_90_p"].to_numpy() >= 2).astype(int)
            pr = (sub["stock_id"] + "|" + sub["br"]).to_numpy()
            R["H1"][f"{per}_{kind}"] = cluster_diff(y, hab, sub["date"].to_numpy(), pr)       # 日期＋(股,分點) 雙向叢集（主）
            R["H1"][f"{per}_{kind}_dateonly"] = cluster_diff(y, hab, sub["date"].to_numpy())  # 單向（協議原版，對照）
            # 穩健：非慣犯限定「有 ≥2 次大買史但隔日沖<2」的事件（排除無歷史）
            m = (sub["n90_p"].to_numpy() >= 2)
            if m.sum() > 100:
                R["H1"][f"{per}_{kind}_hist2"] = cluster_diff(y[m], hab[m], sub["date"].to_numpy()[m])
    # 敏感度（OOS，口徑 A）：α、視窗、MINSH、MINF、TOPN
    sens = {}
    O = Pl[Pl.period == "OOS"]
    dd = O["date"].to_numpy(); prO = (O["stock_id"] + "|" + O["br"]).to_numpy()
    for a in (50, 70, 90):
        for kind in ("A", "B"):
            sens[f"alpha{a}_{kind}"] = cluster_diff(O[f"f{kind}{a}"].to_numpy().astype(int), (O[f"{kind}{a}_90_p"].to_numpy() >= 2).astype(int), dd, prO)
    for w in (60, 90, 180):
        sens[f"win{w}_A"] = cluster_diff(O["fA70"].to_numpy().astype(int), (O[f"A70_{w}_p"].to_numpy() >= 2).astype(int), dd, prO)
    for mf in (1, 2, 3):
        sens[f"minf{mf}_A"] = cluster_diff(O["fA70"].to_numpy().astype(int), (O["A70_90_p"].to_numpy() >= mf).astype(int), dd, prO)
    for fn in ("t3", "m1", "m5"):
        S = ev[(ev[f"in_{fn}"]) & (ev["date"] >= "2025-07-01") & (ev["liq"])]
        for kind in ("A", "B"):
            sens[f"filter_{fn}_{kind}"] = cluster_diff(S[f"f{kind}70"].to_numpy().astype(int), (S[f"{kind}70_90_{fn}"].to_numpy() >= 2).astype(int), S["date"].to_numpy(), (S["stock_id"] + "|" + S["br"]).to_numpy())
    R["H1_sensitivity_OOS"] = sens

    # ---------- H2 劑量反應（OOS，A，p 集合）----------
    h2 = []
    for k in (0, 1, 2, 3):
        m = (O["A70_90_p"] == k) if k < 3 else (O["A70_90_p"] >= 3)
        h2.append({"prior_flips": f"{k}{'+' if k == 3 else ''}", "n": int(m.sum()), "rate_A": float(O.loc[m, "fA70"].mean()), "rate_B": float(O.loc[m, "fB70"].mean())})
    R["H2"] = h2
    X = sm.add_constant(np.minimum(O["A70_90_p"].to_numpy(), 3).astype(float))
    mm = sm.OLS(O["fA70"].to_numpy().astype(float), X).fit(cov_type="cluster", cov_kwds={"groups": np.column_stack([pd.factorize(dd)[0], pd.factorize(prO)[0]])})
    R["H2_trend"] = {"slope": float(mm.params[1]), "se": float(mm.bse[1]), "t": float(mm.params[1] / mm.bse[1])}

    # ---------- H3 個股專屬 vs 分點通用（OOS）----------
    # 分點跨股（全部流動性事件）事前已知計數：同一分點、idx ≤ t−2、90 日內
    A = ev[(ev["in_p"]) & (ev["date"] >= "2024-07-01")].copy().sort_values(["br", "idx"])
    gid = pd.factorize(A["br"])[0].astype(np.int64); idx = A["idx"].to_numpy().astype(np.int64)
    td = np.array([pd.Timestamp(x).toordinal() for x in cal])[idx]
    k_idx = gid * 100000 + idx; k_td = gid * 10**8 + td
    starts = np.searchsorted(gid, gid, side="left")
    hi = np.searchsorted(k_idx, gid * 100000 + idx - 2, side="right")
    lo = np.maximum(np.searchsorted(k_td, gid * 10**8 + td - 90, side="left"), starts)
    hi = np.maximum(hi, lo)
    f = A["fA70"].to_numpy().astype(np.int64)
    cs = np.concatenate([[0], np.cumsum(f)])
    A["n_br"] = hi - lo; A["f_br"] = cs[hi] - cs[lo]
    A["n_oth"] = A["n_br"] - A["n90_p"]; A["f_oth"] = A["f_br"] - A["A70_90_p"]
    H = A[(A["date"] >= "2025-07-01") & (A["liq"]) & (A["n_oth"] >= 5)].copy()
    H["rate_oth"] = H["f_oth"] / H["n_oth"]
    H["own_f"] = np.minimum(H["A70_90_p"], 3)
    X = sm.add_constant(H[["own_f", "rate_oth"]].astype(float))
    grp3 = np.column_stack([pd.factorize(H["date"])[0], pd.factorize(H["stock_id"] + "|" + H["br"])[0]])
    m3 = sm.Logit(H["fA70"].astype(float), X).fit(disp=0, cov_type="cluster", cov_kwds={"groups": grp3})
    Hh = H[H["n90_p"] >= 1]
    R["H3"] = {"n": int(len(H)), "coef_own_f": float(m3.params["own_f"]), "t_own_f": float(m3.tvalues["own_f"]),
               "coef_rate_oth": float(m3.params["rate_oth"]), "t_rate_oth": float(m3.tvalues["rate_oth"]),
               "model": "logit（日期＋分點股配對 雙向叢集）",
               "n_with_own_history": int(len(Hh)),
               "auc_own_hist_only": auc((Hh["A70_90_p"] / Hh["n90_p"]).to_numpy(), Hh["fA70"].to_numpy().astype(int)),
               "auc_other_same_rows": auc(Hh["rate_oth"].to_numpy(), Hh["fA70"].to_numpy().astype(int)),
               "base_rate": float(H["fA70"].mean())}

    # ---------- H4 價格結果（stock-day）----------
    b2 = bars.copy()
    b2["prev_close"] = b2.groupby("stock_id")["close"].shift(1)
    nb = b2[["stock_id", "trade_date", "open", "close", "prev_close"]].rename(columns={"trade_date": "ndate", "open": "n_open", "close": "n_close", "prev_close": "t_close"})
    T = Pl[Pl["rank"] <= 3].copy()
    T["hab"] = (T["A70_90_p"] >= 2).astype(int)
    SD = T.groupby(["stock_id", "date", "ndate", "period"], as_index=False).agg(g=("hab", "max"), n3=("br", "size"))
    SD = SD.merge(nb, on=["stock_id", "ndate"], how="left").dropna(subset=["n_open", "n_close", "t_close"])
    SD["oc"] = SD["n_close"] / SD["n_open"] - 1
    SD["cc"] = SD["n_close"] / SD["t_close"] - 1
    SD["black"] = (SD["n_close"] < SD["n_open"]).astype(float)
    SD = SD[(SD["oc"].abs() < 0.12) & (SD["cc"].abs() < 0.12)]
    for col in ("oc", "cc", "black"):
        SD[col + "_dm"] = SD[col] - SD.groupby("ndate")[col].transform("mean")
    R["H4"] = {}
    for per in ("OOS", "IS"):
        S = SD[SD.period == per]
        R["H4"][per] = {col: cluster_diff(S[col + "_dm"].to_numpy(), S["g"].to_numpy().astype(int), S["date"].to_numpy(), S["stock_id"].to_numpy()) for col in ("oc", "cc", "black")}
        # 控制「合格大買家數」（慣犯組取最多 3 人之 max，名額越多越易含慣犯）
        for col in ("oc", "black"):
            X4 = sm.add_constant(S[["g", "n3"]].astype(float))
            m4 = sm.OLS(S[col + "_dm"].astype(float), X4).fit(cov_type="cluster", cov_kwds={"groups": np.column_stack([pd.factorize(S["date"])[0], pd.factorize(S["stock_id"])[0]])})
            R["H4"][per][col + "_ctrl_n3"] = {"coef_g": float(m4.params["g"]), "t_g": float(m4.tvalues["g"])}
        R["H4"][per]["n_stock_days"] = int(len(S)); R["H4"][per]["share_habit"] = float(S["g"].mean())
        R["H4"][per]["mean_oc_hab"] = float(S.loc[S.g == 1, "oc"].mean()); R["H4"][per]["mean_oc_non"] = float(S.loc[S.g == 0, "oc"].mean())

    # ---------- H5 名單重現 ----------
    cfg = json.loads((ROOT / "config/xiaoge_named_branches.json").read_text(encoding="utf-8"))
    pairs = []
    for e in cfg["branches"]:
        if e.get("role") not in ("隔日沖", "假主力"):
            continue
        codes = {m for s in e["stocks"] for m in re.findall(r"\((\d{4})\)", s)}
        for tid in e["ids"]:
            for sc in codes:
                pairs.append((e["name"], tid, sc))
    Pe = ev[(ev["in_p"]) & (ev["date"] >= EVAL_FROM) & (ev["liq"])]
    stat = Pe.groupby(["stock_id", "br"]).agg(n=("fA70", "size"), rate=("fA70", "mean"), hab=("A70_90_p", lambda s: int((s >= 2).any()))).reset_index()
    rows, pct = [], []
    for nm, tid, sc in pairs:
        r = stat[(stat.stock_id == sc) & (stat.br == tid)]
        if r.empty or int(r.n.iloc[0]) < 3:
            rows.append((nm, tid, sc, None, None, None)); continue
        others = stat[(stat.stock_id == sc) & (stat.br != tid) & (stat.n >= 3)]
        if len(others) < 5:
            rows.append((nm, tid, sc, int(r.n.iloc[0]), float(r.rate.iloc[0]), None)); continue
        p = float((others.rate < r.rate.iloc[0]).mean() + 0.5 * (others.rate == r.rate.iloc[0]).mean())
        rows.append((nm, tid, sc, int(r.n.iloc[0]), float(r.rate.iloc[0]), p)); pct.append(p)
    hab_named, hab_same_stock = [], []
    for nm, tid, sc in pairs:
        r0 = stat[(stat.stock_id == sc) & (stat.br == tid) & (stat.n >= 3)]
        oth = stat[(stat.stock_id == sc) & (stat.br != tid) & (stat.n >= 3)]
        if r0.empty or len(oth) < 5:
            continue
        hab_named.append(int(r0.hab.iloc[0])); hab_same_stock.append(float(oth.hab.mean()))
    base_hab = float(np.mean(hab_same_stock)) if hab_same_stock else None
    R["H5"] = {"pairs_total": len(pairs), "pairs_with_events": sum(1 for r in rows if r[3] is not None), "pairs_rank_ok": len(pct),
               "median_percentile": float(np.median(pct)) if pct else None, "share_above_median": float(np.mean([p > 0.5 for p in pct])) if pct else None,
               "named_ever_habit": float(np.mean(hab_named)) if hab_named else None, "same_stock_others_ever_habit": base_hab, "pairs_hab_compare_n": len(hab_named),
               "detail": [list(r) for r in rows[:60]]}
    (OUT / "results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: R[k] for k in ("population_liquid", "H1", "H2", "H3")}, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
