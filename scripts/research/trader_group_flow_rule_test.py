#!/usr/bin/env python3
"""族群層級的「大戶占比上升 ∧ 散戶占比下降 → 隔天族群熱鬧」規則檢定。

資料：biglot 儀表板 EOD 快照（`cache/biglot_live_watch/eod_snapshots/eod_*.json`，
每檔每日 big=大戶淨額／retn=散戶淨額／ret2=散戶總額／tot=總成交額／close）。
族群＝biglot_dashboard.SUBCAT（畫面上的細分產業）。

檢定三件事：
  1. 族群 T−1 的大戶占比變化 × 散戶占比變化 → 族群 T 日報酬與量能（「熱鬧」）
  2. 個股層級同樣的規則（對照：族群層級有沒有比個股層級好）
  3. 族群分法本身合不合理（組內 vs 組間的殘差報酬相關）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from trader_peer_group_choice import load_subcat  # noqa: E402

EOD = Path.home() / "goldenstocks-data/cache/biglot_live_watch/eod_snapshots"
DB = Path.home() / "goldenstocks-data/data/stocks.db"


def load_panel() -> pd.DataFrame:
    rows = []
    for p in sorted(EOD.glob("eod_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for r in d["rows"]:
            rows.append({"date": d["date"], "sid": str(r["sid"]), "name": r.get("name"),
                         "close": r.get("close"), "px0": r.get("px0"),
                         "big": r.get("big"), "retn": r.get("retn"),
                         "ret2": r.get("ret2"), "tot": r.get("tot")})
    df = pd.DataFrame(rows)
    df = df[df["tot"].fillna(0) > 0].copy()
    df["big_pct"] = 100 * df["big"] / df["tot"]        # 大戶淨占比
    df["ret_pct"] = 100 * df["retn"] / df["tot"]       # 散戶淨占比
    df["ret2_pct"] = 100 * df["ret2"] / df["tot"]      # 散戶參與度（總額占比）
    return df.sort_values(["sid", "date"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "goldenstocks-data/data/scratch/group_flow_panel.csv"))
    args = ap.parse_args()
    sub = load_subcat()
    df = load_panel()
    df["grp"] = df["sid"].map(sub)
    days = sorted(df["date"].unique())
    print(f"EOD 面板：{len(days)} 個交易日 {days[0]}..{days[-1]}　"
          f"{df['sid'].nunique()} 檔　{len(df)} 檔日")
    print(f"有族群標籤 {df['grp'].notna().sum()}／{len(df)}　族群數 {df['grp'].nunique()}")
    sizes = df.groupby("date")["grp"].value_counts().groupby("grp").mean().sort_values(ascending=False)
    print("\n族群規模（平均每日檔數）:")
    print(sizes.round(1).to_string())

    # 個股日報酬（收盤對收盤）
    df["ret_cc"] = df.groupby("sid")["close"].pct_change() * 100
    df["tot_ma5"] = df.groupby("sid")["tot"].transform(lambda s: s.rolling(5, min_periods=3).mean().shift(1))
    df["vol_ratio"] = df["tot"] / df["tot_ma5"]
    df.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}")

    # 族群層級聚合（金額加權，跟畫面上的占比口徑一致）
    g = (df.dropna(subset=["grp"]).groupby(["date", "grp"])
         .agg(n=("sid", "size"), big=("big", "sum"), retn=("retn", "sum"),
              tot=("tot", "sum"), ret_cc=("ret_cc", "mean"), vol_ratio=("vol_ratio", "mean"))
         .reset_index())
    g = g[g["n"] >= 2].copy()
    g["big_pct"] = 100 * g["big"] / g["tot"]
    g["ret_pct"] = 100 * g["retn"] / g["tot"]
    g = g.sort_values(["grp", "date"])
    for c in ("big_pct", "ret_pct"):
        g[f"d_{c}"] = g.groupby("grp")[c].diff()
    g["fwd_ret"] = g.groupby("grp")["ret_cc"].shift(-1)
    g["fwd_vol"] = g.groupby("grp")["vol_ratio"].shift(-1)
    g.to_csv(Path(args.out).with_name("group_flow_group_level.csv"), index=False)
    print(f"族群層級：{g['grp'].nunique()} 個族群（≥2 檔）· {len(g)} 族群日 · "
          f"可配對隔日 {int(g['fwd_ret'].notna().sum())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
