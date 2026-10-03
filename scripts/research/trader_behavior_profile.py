#!/usr/bin/env python3
"""交易習慣診斷 · 第一層：委託／成交紀錄的描述統計（不碰價格資料）。

輸入＝trader_behavior_broker_pull.py 產出的 JSON。
輸出＝下單時段、委託型態、改單／刪單猶豫度、成交率、配對後持有期與處置效應。

手動 vs 自動的切法：自動袖一律帶 user_def（tmfch / dfshort / ss5d / manflat /
mf*），HTML 手動單 user_def 為空。成交紀錄不回傳 user_def，所以用 seq_no
join 回委託紀錄取標籤。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")

AUTO_PREFIXES = ("tmfch", "dfshort", "ss5d", "manflat", "mf", "dfmargin", "dfshortcv",
                 "dfshortks", "c18", "ld", "chase")
# function_type（details 內）：10 新單 · 20 改量/刪單 · 30 改價 · 其餘未知
FUNC = {10: "new", 20: "qty_or_cancel", 30: "price_amend"}
STATUS = {10: "accepted", 30: "working", 50: "filled_part_or_all", 90: "failed_or_cancelled"}


def _is_auto(user_def) -> bool:
    u = str(user_def or "").strip()
    if not u or u.lower() == "none":
        return False
    return any(u.startswith(p) for p in AUTO_PREFIXES)


def load(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for a in d["accounts"]:
        kind = a["account_type"]
        oh = pd.DataFrame(a.get("order_history") or [])
        fh = pd.DataFrame(a.get("filled_history") or [])
        for df in (oh, fh):
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"].str.replace("/", "-"))
                df["side"] = df["buy_sell"].str.replace("BSAction.", "", regex=False)
        if not oh.empty:
            oh["auto"] = oh["user_def"].map(_is_auto)
            oh["tag"] = oh["user_def"].fillna("manual")
            # 新單送出時間 = details 裡 function_type==10 的 modified_time
            oh["submit_time"] = oh["details"].map(_submit_time)
            oh["n_events"] = oh["details"].map(lambda x: len(x or []))
            oh["n_amend_price"] = oh["details"].map(lambda x: _count(x, 30))
            oh["n_amend_qty"] = oh["details"].map(lambda x: _count(x, 20))
        if not fh.empty and not oh.empty:
            # ⚠ 成交紀錄的 seq_no 全是 None，只有 order_no 可用；order_no 每日
            # 重複配發，所以 key 必須是 (date, order_no)。用 seq_no join 會 100%
            # 落空並把全部成交誤標成手動單（2026-10-02 第一版就是這樣）。
            key_o = oh["date"].dt.strftime("%Y%m%d") + "|" + oh["order_no"].astype(str).str.strip()
            key_f = fh["date"].dt.strftime("%Y%m%d") + "|" + fh["order_no"].astype(str).str.strip()
            tagmap = dict(zip(key_o, oh["tag"]))
            automap = dict(zip(key_o, oh["auto"]))
            fh["tag"] = key_f.map(tagmap).fillna("unknown")
            fh["auto"] = key_f.map(automap)
            fh["submit_time"] = key_f.map(dict(zip(key_o, oh["submit_time"])))
            fh["order_price"] = key_f.map(dict(zip(key_o, oh["price"])))
            fh["order_qty"] = key_f.map(dict(zip(key_o, oh.get("quantity", oh.get("lot")))))
            fh["price_type"] = key_f.map(dict(zip(key_o, oh["price_type"])))
            fh["order_type_o"] = key_f.map(dict(zip(key_o, oh["order_type"])))
            fh["market_type_o"] = key_f.map(dict(zip(key_o, oh.get("market_type"))))
        out[kind] = {"orders": oh, "fills": fh}
    return out


def _submit_time(details):
    for ev in details or []:
        if ev.get("function_type") == 10:
            return ev.get("modified_time")
    return (details or [{}])[0].get("modified_time")


def _count(details, ft):
    return sum(1 for ev in (details or []) if ev.get("function_type") == ft)


def _hhmm(t):
    return str(t)[:5] if t else None


def section(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(Path.home() / "goldenstocks-data/data/scratch/broker_history_v2.json"))
    args = ap.parse_args()
    data = load(Path(args.json))

    for kind in ("stock", "futopt"):
        if kind not in data:
            continue
        oh, fh = data[kind]["orders"], data[kind]["fills"]
        section(f"[{kind}] 原始輪廓")
        print(f"orders n={len(oh)}  fills n={len(fh)}")
        if not oh.empty:
            print(f"order 日期範圍 {oh['date'].min().date()} .. {oh['date'].max().date()}  交易日數={oh['date'].nunique()}")
            print("\n委託來源（手動 vs 自動）:")
            print(oh.groupby(oh["auto"].map({False: "manual", True: "auto"})).size().to_string())
            print("\n每個 tag 的委託數:")
            print(oh["tag"].value_counts().head(15).to_string())
        if not fh.empty:
            print(f"\nfill 日期範圍 {fh['date'].min().date()} .. {fh['date'].max().date()}  交易日數={fh['date'].nunique()}")
            print(fh["tag"].value_counts().head(10).to_string())
            print("unmatched fills (無對應委託):", int(fh["tag"].eq("unknown").sum()))
    # dump for stage 2
    out = Path(args.json).with_name("trader_behavior_tables.pkl")
    pd.to_pickle(data, out)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
