#!/usr/bin/env python3
"""關鍵分點「進場訊號」事件研究（唯讀）。

事件：小哥點名（關鍵分點／地緣／高手／公司派）且有代號的 (分點, 個股)，該分點當日為該股買超前5且淨買≥1%成交量。
對照：同股其他日（含無事件日）。回歸：fwd ~ key + conc(主力集中度) + ret_t + 股票固定效果，日期叢集 SE。
結果：o2c(t+1開→收)、c2c1(t收→t+1收)、c2c5(t收→t+5收)。tape 2024-07-01~2026-07-16；PIT：t 日資料只決定 t 日旗標。
輸出：${DATA}/research/xiaoge_flip_habit/key_entry.json
"""
import json, re, sqlite3
from pathlib import Path
import numpy as np, pandas as pd
import statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parents[2]
DB = Path.home() / "goldenstocks-data/data/stocks.db"
OUT = Path.home() / "goldenstocks-data/research/xiaoge_flip_habit/key_entry.json"
ROLES = ("關鍵分點", "地緣", "高手", "公司派")


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c.execute("pragma query_only=1")
    cfg = json.loads((ROOT / "config/xiaoge_named_branches.json").read_text(encoding="utf-8"))
    pairs = {}
    for e in cfg["branches"]:
        if e.get("role") in ROLES:
            for s in e["stocks"]:
                for m in re.findall(r"\((\d{4})\)", s):
                    for t in e["ids"]:
                        pairs.setdefault(m, {})[t] = e["role"]
    frames = []
    for sc, keys in sorted(pairs.items()):
        tp = pd.read_sql_query("select trade_date d, securities_trader_id br, buy, sell from stock_broker_branch_daily "
                               "where stock_id=? and trade_date between '2024-07-01' and '2026-07-16'", c, params=(sc,))
        px = pd.read_sql_query("select trade_date d, open, close from stock_daily_bars where stock_id=? and source in ('twse_mi_index','tpex_daily') "
                               "and trade_date between '2024-07-01' and '2026-07-31'", c, params=(sc,)).drop_duplicates("d").sort_values("d").reset_index(drop=True)
        if tp.empty or len(px) < 100: continue
        tp[["buy", "sell"]] = tp[["buy", "sell"]].fillna(0); tp["net"] = tp.buy - tp.sell
        vol = tp.groupby("d")["buy"].sum(); ok = vol[vol > 0].index
        tp = tp[tp.d.isin(ok)]; vol = vol[vol.index.isin(ok)]
        tp["rk"] = tp.groupby("d")["net"].rank(ascending=False, method="first")
        top15 = tp[tp.rk <= 15].groupby("d")["net"].sum()
        bot15 = tp[tp.groupby("d")["net"].rank(method="first") <= 15].groupby("d")["net"].sum()
        day = pd.DataFrame({"conc": (top15 + bot15) / vol}).reset_index()
        ev = tp[(tp.br.isin(keys)) & (tp.rk <= 5) & (tp.net >= 0.01 * tp.d.map(vol))]
        role_of = ev.assign(role=ev.br.map(keys)).groupby("d")["role"].first()
        day["key"] = day.d.isin(ev.d).astype(int); day["role"] = day.d.map(role_of)
        p = px.copy(); p["ret_t"] = p.close.pct_change()
        p["o2c"] = p.close.shift(-1) / p.open.shift(-1) - 1
        p["c1"] = p.close.shift(-1) / p.close - 1; p["c5"] = p.close.shift(-5) / p.close - 1
        m = day.merge(p, on="d"); m["stock"] = sc
        frames.append(m)
        print(sc, int(m.key.sum()), flush=True)
    df = pd.concat(frames); df = df[(df[["o2c", "c1", "c5", "ret_t"]].abs() < 0.12).all(axis=1)].dropna(subset=["conc", "o2c", "c1", "c5", "ret_t"])
    res = {"n_days": int(len(df)), "n_events": int(df.key.sum()), "events_by_role": df[df.key == 1].role.value_counts().to_dict(),
           "event_mean_pct": {k: float(df[df.key == 1][k].mean() * 100) for k in ("o2c", "c1", "c5")},
           "all_mean_pct": {k: float(df[k].mean() * 100) for k in ("o2c", "c1", "c5")}}
    for y in ("o2c", "c1", "c5"):
        r = smf.ols(f"{y} ~ key + conc + ret_t + C(stock)", df).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(df.d)[0]})
        res[y] = {"key_coef_pp": float(r.params["key"] * 100), "t": float(r.tvalues["key"]), "conc_coef": float(r.params["conc"]), "conc_t": float(r.tvalues["conc"])}
        for role in ROLES:
            d2 = df[(df.key == 0) | (df.role == role)]
            if d2.key.sum() >= 10:
                r2 = smf.ols(f"{y} ~ key + conc + ret_t + C(stock)", d2).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(d2.d)[0]})
                res[y][role] = {"n": int(d2.key.sum()), "coef_pp": float(r2.params["key"] * 100), "t": float(r2.tvalues["key"])}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"); print(json.dumps(res, ensure_ascii=False, indent=1))

main()
