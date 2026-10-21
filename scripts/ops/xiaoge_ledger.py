#!/usr/bin/env python3
"""小哥訊號帳本：累積「條件成立後的成績」，回答「這套盤點／筆記本到底有沒有用」。唯讀 stocks.db。

兩個來源（帳本分開，不混算）：
  backfill  價格型戰法 P01–P05（M01–M05 去掉分點籌碼條件）在歷史日線上逐日重現，立刻有大樣本（--backfill，一次性，可重跑）。
  live      每日盤點 inventory_*.json（M01–M08）與筆記本 notebook_*.json（空方/多方/雷達買超/賣超）的真實命中；T+1 日線入庫後逐日補成績（--live，每日排程）。
成績（PIT；分點收盤後才有 → T+1 開盤進場）：gap=T收→T+1開；oc=T+1開→T+1收；o5/o20=T+1開→T+5/T+20收。
超額＝個股報酬 − 同日流動性母體等權平均；beta 調整＝再減 beta×母體報酬（beta 用訊號日前 60 日估，PIT）。
統計：先按訊號日取平均，再對「日均超額」做 t（h>1 用 HAC lag=h，因視窗重疊）。價格未還原，|報酬|>40% 的觀測剔除。
輸出：${DATA}/chip/xiaoge_ledger/{ledger_backfill,ledger_live}.parquet、summary.json（8771 /inventory 底部讀）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts/ops"))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402
from xiaoge_inventory import features  # noqa: E402

OUT = DATA_DIR / "chip" / "xiaoge_ledger"
INV = DATA_DIR / "chip" / "xiaoge_inventory"
NB = DATA_DIR / "chip" / "xiaoge_notebook"
HZ = {"oc": 0, "o5": 5, "o20": 20}
PNAMES = {"open_bb": "P01 開布林", "narrow": "P02 窄帶寬", "range": "P03 多頭區間", "high_out": "P04 高檔出貨", "floor": "P05 地板股"}


def load_bars(c, start):
    df = pd.read_sql_query("select stock_id, trade_date d, open, close, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') "
                           "and trade_date>=? and length(stock_id)=4 and stock_id glob '[1-9][0-9][0-9][0-9]'", c, params=(start,))
    return df.drop_duplicates(["stock_id", "d"]).sort_values(["stock_id", "d"])


def forward(g: pd.DataFrame) -> pd.DataFrame:
    o, cl = g.open, g.close
    g["gap"] = o.shift(-1) / cl - 1
    g["oc"] = cl.shift(-1) / o.shift(-1) - 1
    g["o5"] = cl.shift(-5) / o.shift(-1) - 1
    g["o20"] = cl.shift(-20) / o.shift(-1) - 1
    g["r1"] = cl.pct_change()
    return g


def build_panel(bars: pd.DataFrame, with_signals: bool, min_len: int = 120) -> pd.DataFrame:
    parts = []
    for sid, g in bars.groupby("stock_id"):
        if len(g) < min_len:
            continue
        g = forward(g.reset_index(drop=True))
        if with_signals:
            g = features(g)
        else:
            g["turn20"] = (g.close * g.volume).shift(1).rolling(20).mean()
        g["stock_id"] = sid
        parts.append(g)
    p = pd.concat(parts, ignore_index=True)
    return p


def add_excess(p: pd.DataFrame) -> pd.DataFrame:
    liq = p[p.turn20 >= 3.0e7]
    mk = liq.groupby("d")[["gap", "oc", "o5", "o20", "r1"]].mean().add_prefix("m_")
    p = p.merge(mk, left_on="d", right_index=True, how="left")
    # beta：訊號日前 60 日個股 r1 對母體 r1（PIT：shift(1)）
    wide = p.pivot(index="d", columns="stock_id", values="r1")
    m = mk["m_r1"].reindex(wide.index)
    cov = wide.rolling(60, min_periods=40).cov(m)  # DataFrame.rolling.cov(Series) → per stock
    var = m.rolling(60, min_periods=40).var()
    beta = cov.div(var, axis=0).shift(1).stack().rename("beta").reset_index()
    p = p.merge(beta, on=["d", "stock_id"], how="left")
    p["beta"] = p["beta"].fillna(1.0).clip(-1, 3)
    for h in HZ:
        p[f"x_{h}"] = p[h] - p[f"m_{h}"]
        p[f"xb_{h}"] = p[h] - p["m_" + h] * p["beta"]
    return p


def summarize(df: pd.DataFrame, group_col: str) -> dict:
    import statsmodels.api as sm
    res = {}
    for name, g in df.groupby(group_col):
        r = {"n_obs": int(len(g)), "n_days": int(g.d.nunique())}
        for h, lag in (("oc", 0), ("o5", 5), ("o20", 20)):
            col = g[[h, f"x_{h}", f"xb_{h}", "d"]].dropna()
            col = col[col[h].abs() < 0.4]
            if len(col) < 10:
                r[h] = None; continue
            daily = col.groupby("d")[[h, f"x_{h}", f"xb_{h}"]].mean().sort_index()
            y = daily[f"x_{h}"].to_numpy(); yb = daily[f"xb_{h}"].to_numpy()
            if len(y) < 8:
                r[h] = {"n_days": len(y)}; continue
            ones = np.ones(len(y))
            fit = sm.OLS(y, ones).fit(cov_type="HAC", cov_kwds={"maxlags": max(lag, 1)})
            fitb = sm.OLS(yb, ones).fit(cov_type="HAC", cov_kwds={"maxlags": max(lag, 1)})
            # 主指標＝等權母體超額 x（母體本身恆為 0）；beta 調整僅作敏感度（母體列非 0 代表 beta 調整有偏，見 docstring）
            r[h] = {"n_days": int(len(y)), "mean_pct": float(daily[h].mean() * 100), "excess_pct": float(y.mean() * 100), "t": float(fit.tvalues[0]),
                    "excess_beta_pct": float(yb.mean() * 100), "t_beta": float(fitb.tvalues[0]), "up_share": float((col[h] > 0).mean())}
        gp = g[["gap"]].dropna()
        r["gap_mean_pct"] = float(gp.gap.mean() * 100) if len(gp) else None
        res[name] = r
    return res


def run_backfill(c, start):
    bars = load_bars(c, start)
    p = add_excess(build_panel(bars, True))
    p = p[(p.turn20 >= 3.0e7) & (p.d >= "2024-10-01")]
    rows = []
    for k, name in PNAMES.items():
        s = p[p[k].fillna(False).astype(bool)].copy(); s["list"] = name; rows.append(s)
    led = pd.concat(rows, ignore_index=True)
    base = p.copy(); base["list"] = "母體(流動性全體)"
    OUT.mkdir(parents=True, exist_ok=True)
    keep = ["list", "d", "stock_id", "gap", "oc", "o5", "o20", "x_oc", "x_o5", "x_o20", "xb_oc", "xb_o5", "xb_o20", "beta"]
    led[keep].to_parquet(OUT / "ledger_backfill.parquet")
    summ = summarize(led, "list")
    summ["母體(流動性全體)"] = summarize(base.assign(list="母體"), "list")["母體"]
    return summ


def run_live(c):
    files = sorted(INV.glob("inventory_20??-??-??.json"))
    nbs = {p.stem.replace("notebook_", ""): p for p in NB.glob("notebook_20??-??-??.json")}
    rows = []
    for f in files:
        inv = json.loads(f.read_text(encoding="utf-8")); day = inv["date"]
        for k, m in inv["methods"].items():
            for h in m["hits"]:
                rows.append((f"{k} {m['name']}", day, h["stock"]))
        if day in nbs:
            nb = json.loads(nbs[day].read_text(encoding="utf-8"))
            for r in nb.get("shorts", []): rows.append(("NB 空方線索", day, r["stock"]))
            for r in nb.get("longs", []): rows.append(("NB 多方線索", day, r["stock"]))
            for r in nb.get("radar", []): rows.append(("NB 雷達主力買超" if r["conc_pct"] > 0 else "NB 雷達主力賣超", day, r["stock"]))
    if not rows:
        return {}
    sig = pd.DataFrame(rows, columns=["list", "d", "stock_id"]).drop_duplicates()
    start = (pd.Timestamp(sig.d.min()) - pd.Timedelta(days=250)).strftime("%Y-%m-%d")
    p = add_excess(build_panel(load_bars(c, start), False, 70))
    led = sig.merge(p, on=["d", "stock_id"], how="left")
    OUT.mkdir(parents=True, exist_ok=True)
    led.to_parquet(OUT / "ledger_live.parquet")
    return summarize(led.dropna(subset=["oc"]), "list")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--start", default="2023-06-01")
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True); c.execute("pragma query_only=1")
    sp = OUT / "summary.json"
    summary = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    if a.backfill:
        summary["backfill"] = run_backfill(c, a.start)
        OUT.mkdir(parents=True, exist_ok=True); sp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.live:
        summary["live"] = run_live(c)
    summary["generated_at"] = datetime.now().isoformat(timespec="seconds")
    OUT.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for part in ("backfill", "live"):
        if part in summary:
            print(f"== {part}")
            for k, r in summary[part].items():
                o = r.get("oc") or {}; o5 = r.get("o5") or {}; o20 = r.get("o20") or {}
                print(f"{k:<18} n={r['n_obs']:<7} days={r['n_days']:<4} gap={r.get('gap_mean_pct') or 0:+.2f}% | oc 超額 {o.get('excess_pct', float('nan')):+.3f}% t={o.get('t', float('nan')):+.1f}"
                      f" | o5 {o5.get('excess_pct', float('nan')):+.2f}% t={o5.get('t', float('nan')):+.1f} | o20 {o20.get('excess_pct', float('nan')):+.2f}% t={o20.get('t', float('nan')):+.1f}")


if __name__ == "__main__":
    main()
