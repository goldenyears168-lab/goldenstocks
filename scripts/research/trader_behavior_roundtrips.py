#!/usr/bin/env python3
"""交易習慣診斷 · 第三層：FIFO 配對成往返交易 → 持有期／勝率／處置效應。

只用手動（user_def 空）成交紀錄。毛報酬與「扣成本後」各報一次；成本用
公開費率的保守估計，不是實際對帳單數字。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 60)

# 保守成本假設：現股手續費 0.1425% × 6 折雙邊 + 賣出證交稅 0.3%
STOCK_FEE = 0.001425 * 0.6
STOCK_TAX = 0.003
# 個股期貨：手續費抓單邊 25 元/口、期交稅 0.002%（雙邊），contract 2000 股
FUT_FEE_PER_LOT = 25.0
FUT_TAX_RATE = 0.00002

# 契約乘數：指數期貨是「元/點」，個股期貨是「股/口」。全部當 2000 會把
# 微台（10 元/點）的損益放大 200 倍（2026-10-02 第一版踩到）。
INDEX_MULT = {"FITX": 200.0, "FIMTX": 50.0, "FITM": 10.0, "FIEX": 100.0}
_SPECS_PATH = Path.home() / "goldenstocks-data/data/cache/taifex_stock_futures_specs.json"
_SPECS = json.loads(_SPECS_PATH.read_text(encoding="utf-8")) if _SPECS_PATH.exists() else {}


def fut_mult(symbol: str) -> float:
    """富邦 symbol = 'FI' + TAIFEX 商品代號 + 'F'。小型個股期貨是 100 股，
    標準型 2,000 股，ETF 期貨 10,000／1,000 —— 一律查官方表，不要預設 2,000。
    （產生表：scripts/research/fetch_taifex_stock_futures_specs.py）"""
    sym = str(symbol)
    if sym in INDEX_MULT:
        return INDEX_MULT[sym]
    core = sym[2:-1] if sym.startswith("FI") and sym.endswith("F") else sym
    spec = _SPECS.get(core)
    if spec and spec.get("contract_shares"):
        return float(spec["contract_shares"])
    raise KeyError(f"找不到 {sym} 的契約規模（core={core}）——先跑 fetch_taifex_stock_futures_specs.py")


def sec(t):
    print("\n" + "─" * 70)
    print(t)
    print("─" * 70)


def _dt(df):
    tt = df["filled_time"].fillna("00:00:00")
    return pd.to_datetime(df["date"].dt.strftime("%Y-%m-%d") + " " + tt.str.slice(0, 12),
                          errors="coerce")


def fifo(df, sym_col, qty_col, mult=1.0):
    """按標的做 FIFO 配對；同時支援先買後賣（多）與先賣後買（空）。"""
    trades = []
    for sym, g in df.sort_values("ts").groupby(sym_col):
        book = []  # (dir, qty_left, price, ts)
        for _, r in g.iterrows():
            d = 1 if r["side"] == "Buy" else -1
            qty, px, ts = float(r[qty_col]), float(r["filled_price"]), r["ts"]
            while qty > 0 and book and book[0][0] == -d:
                bd, bq, bpx, bts = book[0]
                m = min(qty, bq)
                entry_px, exit_px = (bpx, px)
                direction = "long" if bd == 1 else "short"
                pnl_px = (exit_px - entry_px) if bd == 1 else (entry_px - exit_px)
                trades.append({
                    "symbol": sym, "direction": direction, "qty": m,
                    "entry_ts": bts, "exit_ts": ts,
                    "entry_px": entry_px, "exit_px": exit_px,
                    "gross_pnl": pnl_px * m * mult,
                    "ret_pct": 100 * pnl_px / entry_px,
                    "hold_min": (ts - bts).total_seconds() / 60.0,
                    "notional": entry_px * m * mult,
                })
                qty -= m
                if bq - m <= 1e-9:
                    book.pop(0)
                else:
                    book[0] = (bd, bq - m, bpx, bts)
            if qty > 0:
                book.append((d, qty, px, ts))
    return pd.DataFrame(trades)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl", default=str(Path.home() / "goldenstocks-data/data/scratch/trader_behavior_tables.pkl"))
    ap.add_argument("--kind", default="stock", choices=["stock", "futopt"])
    args = ap.parse_args()
    d = pd.read_pickle(args.pkl)
    f = d[args.kind]["fills"]
    f = f[f["tag"] == "manual"].copy()
    f["ts"] = _dt(f)

    if args.kind == "stock":
        sym, qty, mult = "stock_no", "filled_qty", 1.0
    else:
        f["sym_exp"] = f["symbol"] + "_" + f["expiry_date"].astype(str)
        sym, qty, mult = "sym_exp", "filled_lot", None

    if mult is None:
        parts = []
        for root, g in f.groupby(f["symbol"]):
            parts.append(fifo(g, sym, qty, fut_mult(root)))
        t = pd.concat([x for x in parts if not x.empty], ignore_index=True) if parts else pd.DataFrame()
    else:
        t = fifo(f, sym, qty, mult)
    if t.empty:
        print("no round trips")
        return 0
    if args.kind == "stock":
        t["cost"] = t["notional"] * (2 * STOCK_FEE) + t["exit_px"] * t["qty"] * STOCK_TAX
    else:
        lots = t["qty"]
        t["cost"] = lots * FUT_FEE_PER_LOT * 2 + 2 * t["notional"] * FUT_TAX_RATE
    t["net_pnl"] = t["gross_pnl"] - t["cost"]
    t["net_ret_pct"] = 100 * t["net_pnl"] / t["notional"]
    t["same_day"] = t["entry_ts"].dt.date == t["exit_ts"].dt.date
    t["win"] = t["net_pnl"] > 0
    t["entry_slot"] = t["entry_ts"].dt.strftime("%H:%M")

    sec(f"[{args.kind}] 往返交易總覽（手動單 · FIFO）")
    print(f"n_trades={len(t)}　標的數={t['symbol'].nunique()}　"
          f"期間 {t['entry_ts'].min().date()}..{t['exit_ts'].max().date()}")
    print(f"總名目={t['notional'].sum():,.0f}　毛損益={t['gross_pnl'].sum():,.0f}　"
          f"估成本={t['cost'].sum():,.0f}　淨損益={t['net_pnl'].sum():,.0f}")
    print(f"勝率(淨)={100 * t['win'].mean():.1f}%　"
          f"平均淨報酬={t['net_ret_pct'].mean():.3f}%　中位={t['net_ret_pct'].median():.3f}%")
    w, l = t[t["win"]], t[~t["win"]]
    print(f"平均賺 {w['net_ret_pct'].mean():.3f}%（n={len(w)}）　"
          f"平均賠 {l['net_ret_pct'].mean():.3f}%（n={len(l)}）　"
          f"賺賠比={abs(w['net_ret_pct'].mean() / l['net_ret_pct'].mean()):.2f}")
    print(f"多/空：{t.groupby('direction').size().to_dict()}")

    sec("持有期 vs 結果（處置效應檢查）")
    print("持有時間（分鐘）分位:")
    print(t["hold_min"].describe(percentiles=[.1, .25, .5, .75, .9]).round(1).to_string())
    g = t.groupby("win")["hold_min"].agg(["size", "median", "mean"]).round(1)
    print("\n賺錢單 vs 賠錢單的持有時間:")
    print(g.rename(index={True: "賺", False: "賠"}).to_string())
    from scipy import stats
    if len(w) > 5 and len(l) > 5:
        u = stats.mannwhitneyu(w["hold_min"], l["hold_min"], alternative="two-sided")
        print(f"Mann-Whitney U p={u.pvalue:.4f}（賺單持有 vs 賠單持有是否同分布）")

    sec("當日沖銷 vs 留倉")
    g = t.groupby("same_day").agg(n=("net_ret_pct", "size"), 勝率=("win", "mean"),
                                  平均淨報酬=("net_ret_pct", "mean"), 中位=("net_ret_pct", "median"),
                                  淨損益=("net_pnl", "sum"))
    g["勝率"] = (100 * g["勝率"]).round(1)
    print(g.round(3).rename(index={True: "當日平倉", False: "留倉過夜"}).to_string())

    sec("進場時段 × 結果")
    def slot(ts):
        hm = ts.hour + ts.minute / 60
        if hm < 9.0:
            return "b 試撮前"
        if hm < 9.25:
            return "c 開盤15分"
        if hm < 10:
            return "d 09:15-10:00"
        if hm < 12:
            return "e 10:00-12:00"
        if hm < 13:
            return "f 12:00-13:00"
        if hm < 13.6:
            return "g 13:00-13:35"
        return "i 盤後"
    t["slot"] = t["entry_ts"].map(slot)
    g = t.groupby("slot").agg(n=("net_ret_pct", "size"), 勝率=("win", "mean"),
                              平均淨報酬=("net_ret_pct", "mean"), 淨損益=("net_pnl", "sum"))
    g["勝率"] = (100 * g["勝率"]).round(1)
    print(g.round(3).to_string())

    sec("報復性交易：前一筆出場結果 → 下一次進場的間隔與績效")
    t2 = t.sort_values("exit_ts").reset_index(drop=True)
    nxt = []
    for i, row in t2.iterrows():
        later = t2[t2["entry_ts"] > row["exit_ts"]]
        if later.empty:
            continue
        nx = later.iloc[0]
        nxt.append({"prev_win": bool(row["win"]),
                    "prev_ret": row["net_ret_pct"],
                    "gap_min": (nx["entry_ts"] - row["exit_ts"]).total_seconds() / 60,
                    "next_ret": nx["net_ret_pct"], "next_win": bool(nx["win"])})
    nx = pd.DataFrame(nxt)
    if not nx.empty:
        g = nx.groupby("prev_win").agg(n=("gap_min", "size"), 間隔中位分鐘=("gap_min", "median"),
                                       下一筆勝率=("next_win", "mean"), 下一筆平均報酬=("next_ret", "mean"))
        g["下一筆勝率"] = (100 * g["下一筆勝率"]).round(1)
        print(g.round(3).rename(index={True: "上一筆賺", False: "上一筆賠"}).to_string())
        quick = nx[nx["gap_min"] <= 10]
        print(f"\n出場後 ≤10 分鐘就再進場：n={len(quick)}　"
              f"其中上一筆是賠的占 {100 * quick['prev_win'].eq(False).mean():.1f}%　"
              f"這些單的平均報酬={quick['next_ret'].mean():.3f}%　勝率={100 * quick['next_win'].mean():.1f}%")

    sec("標的集中度 Top 10（按淨損益）")
    g = t.groupby("symbol").agg(n=("net_pnl", "size"), 勝率=("win", "mean"),
                                淨損益=("net_pnl", "sum"), 平均報酬=("net_ret_pct", "mean"))
    g["勝率"] = (100 * g["勝率"]).round(1)
    print(g.sort_values("淨損益").head(6).round(2).to_string())
    print("...")
    print(g.sort_values("淨損益", ascending=False).head(6).round(2).to_string())

    out = Path(args.pkl).with_name(f"trader_roundtrips_{args.kind}.csv")
    t.to_csv(out, index=False)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
