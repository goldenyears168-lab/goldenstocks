#!/usr/bin/env python3
"""交易習慣診斷 · 第二層：手動（HTML）下單的行為指紋。

只看 user_def 為空的手動單。輸出全部是可重算的描述統計，不做因果宣稱。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)


def sec(t):
    print("\n" + "─" * 70)
    print(t)
    print("─" * 70)


def to_sec(t):
    if not t or not isinstance(t, str):
        return np.nan
    try:
        h, m, rest = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    except Exception:  # noqa: BLE001
        return np.nan


def bucket(t):
    s = to_sec(t)
    if np.isnan(s):
        return "unknown"
    hm = s / 3600
    if hm < 8.5:
        return "a 盤前(<08:30)"
    if hm < 9.0:
        return "b 試撮08:30-09:00"
    if hm < 9.25:
        return "c 開盤15分"
    if hm < 10.0:
        return "d 09:15-10:00"
    if hm < 12.0:
        return "e 10:00-12:00"
    if hm < 13.0:
        return "f 12:00-13:00"
    if hm < 13.4167:
        return "g 13:00-13:25"
    if hm < 13.55:
        return "h 尾盤競價13:25-13:33"
    return "i 收盤後"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl", default=str(Path.home() / "goldenstocks-data/data/scratch/trader_behavior_tables.pkl"))
    ap.add_argument("--kind", default="stock", choices=["stock", "futopt"])
    args = ap.parse_args()
    d = pd.read_pickle(args.pkl)
    oh = d[args.kind]["orders"]
    fh = d[args.kind]["fills"]
    qty_col = "quantity" if "quantity" in oh.columns else "lot"
    fqty_col = "filled_qty" if "filled_qty" in fh.columns else "filled_lot"

    o = oh[~oh["auto"]].copy()
    f = fh[fh["tag"] == "manual"].copy()
    print(f"手動委託 n={len(o)}　手動成交 n={len(f)}　"
          f"委託日 {o['date'].min().date()}..{o['date'].max().date()}　"
          f"成交日 {f['date'].min().date()}..{f['date'].max().date()}")

    sec("A. 下單時段分布（委託送出時刻 · 新單）")
    o["slot"] = o["submit_time"].map(bucket)
    tbl = o.groupby("slot").agg(n=("slot", "size"),
                                buy=("side", lambda s: (s == "Buy").sum()),
                                sell=("side", lambda s: (s == "Sell").sum()))
    tbl["占比%"] = (100 * tbl["n"] / len(o)).round(1)
    print(tbl.to_string())

    sec("B. 委託型態")
    for col in ("order_type", "price_type", "time_in_force", "market_type", "side"):
        if col not in o.columns:
            continue
        vc = o[col].value_counts()
        print(f"\n{col}:")
        print(pd.DataFrame({"n": vc, "%": (100 * vc / len(o)).round(1)}).to_string())
    print(f"\n{qty_col} 分布:")
    print(o[qty_col].describe(percentiles=[.25, .5, .75, .9]).round(2).to_string())

    sec("C. 委託結局（status）與改單／刪單")
    st_map = {10: "10 已接受(預約/零股)", 30: "30 委託中/未成交", 50: "50 有成交", 90: "90 失敗或刪單"}
    vc = o["status"].value_counts().sort_index()
    print(pd.DataFrame({"n": vc, "%": (100 * vc / len(o)).round(1)}).rename(index=st_map).to_string())
    print(f"\n改價事件(function_type=30) 總次數={int(o['n_amend_price'].sum())}　"
          f"有改價的委託 {int((o['n_amend_price'] > 0).sum())}／{len(o)}")
    print(f"改量/刪單事件(function_type=20) 總次數={int(o['n_amend_qty'].sum())}　"
          f"有此事件的委託 {int((o['n_amend_qty'] > 0).sum())}／{len(o)}")
    print("\n各時段的成交率（status==50 視為有成交）:")
    g = o.assign(filled=o["status"] == 50).groupby("slot").agg(n=("filled", "size"), 成交率=("filled", "mean"))
    g["成交率%"] = (100 * g["成交率"]).round(1)
    print(g[["n", "成交率%"]].to_string())

    sec("D. 送出→成交延遲（秒）")
    f["t_submit"] = f["submit_time"].map(to_sec)
    f["t_fill"] = f["filled_time"].map(to_sec)
    f["lag"] = f["t_fill"] - f["t_submit"]
    lag = f["lag"].dropna()
    lag = lag[lag >= -1]
    print(f"n={len(lag)}  中位={lag.median():.1f}s  平均={lag.mean():.1f}s")
    print("分位:", {q: round(float(lag.quantile(q)), 1) for q in (.1, .25, .5, .75, .9, .99)})
    f["slot"] = f["submit_time"].map(bucket)
    print("\n立即成交(≤3秒)比率 by 時段:")
    gg = f.assign(fast=f["lag"] <= 3).groupby("slot").agg(n=("fast", "size"), r=("fast", "mean"))
    gg["立即成交%"] = (100 * gg["r"]).round(1)
    print(gg[["n", "立即成交%"]].to_string())

    sec("E. 每日交易密度")
    per_day = f.groupby(f["date"].dt.date).agg(n_fills=("filled_price", "size"),
                                               n_sym=("stock_no" if "stock_no" in f.columns else "symbol", "nunique"),
                                               notional=(fqty_col, lambda s: np.nan))
    per_day = per_day.drop(columns=["notional"])
    print(per_day.describe().round(2).to_string())
    print("\n最忙的 8 天:")
    print(per_day.sort_values("n_fills", ascending=False).head(8).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
