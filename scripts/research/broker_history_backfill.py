#!/usr/bin/env python3
"""把既有的券商歷史 JSON 快照回填進 broker_history.db（不打 API）。

來源＝trader_behavior_broker_pull.py 產出的 JSON（`data/scratch/broker_history*.json`）。
用這支先把手上已有的歷史存進持久化資料庫，之後才靠每月 job 增量累積。

冪等：可重複執行，不會長出重複列（三張表都有自然主鍵）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "src")

import broker_history as bh  # noqa: E402
from stock_db import DATA_DIR  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", nargs="*", default=None,
                    help="來源 JSON；預設掃 data/scratch/broker_history*.json")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    srcs = ([Path(p) for p in args.json] if args.json
            else sorted((DATA_DIR / "scratch").glob("broker_history*.json")))
    srcs = [p for p in srcs if p.exists()]
    if not srcs:
        print("找不到來源 JSON", file=sys.stderr)
        return 1
    # ⚠ 不要按檔案大小排序。2026-10-04 踩過：早期（v1）拉取腳本有 enum 序列化
    # bug，會把 SDK enum 展開成「類別上所有列舉成員」的巨大字典，所以那個壞檔
    # 反而是最大的（31MB vs 正確版 5.8MB）。按大小降序排 → 壞檔先寫，而當時
    # ON CONFLICT 又只覆蓋部分欄位，結果 3,101 列的買賣方向全壞。
    # 現在兩道防護：① broker_history._enum() 擋掉壞值、side 不是 Buy/Sell 就剔除
    # ② ON CONFLICT 覆蓋全部可變欄位，所以「後寫的贏」＝按修改時間舊到新排序即可。
    srcs.sort(key=lambda p: p.stat().st_mtime)
    print("來源（舊→新，後寫覆蓋前寫）:")
    for q in srcs:
        print(f"  {q.name}  {q.stat().st_size:,} bytes")
    con = bh.connect(args.db)
    pulled_at = datetime.now().astimezone().isoformat(timespec="seconds")
    tot = {"orders": 0, "fills": 0, "realized": 0}
    try:
        for src in srcs:
            d = json.loads(src.read_text(encoding="utf-8"))
            accs = d.get("accounts") or []
            no = nf = nr = 0
            for a in accs:
                kind = a.get("account_type") or "unknown"
                no += bh.upsert_orders(con, a.get("order_history") or [],
                                       account_type=kind, pulled_at=pulled_at)
                nf += bh.upsert_fills(con, a.get("filled_history") or [],
                                      account_type=kind, pulled_at=pulled_at)
                for key in ("realized_gains_and_loses", "realized_gains_and_loses_summary"):
                    nr += bh.upsert_realized(con, a.get(key) or [], pulled_at=pulled_at)
            bh.log_pull(con, pulled_at=pulled_at,
                        start=str(d.get("start") or ""), end=str(d.get("end") or ""),
                        n_orders=no, n_fills=nf, n_realized=nr,
                        note=f"backfill from {src.name}")
            con.commit()
            print(f"  {src.name}: 委託 {no} · 成交 {nf} · 已實現 {nr}")
            tot["orders"] += no
            tot["fills"] += nf
            tot["realized"] += nr
        print(f"\n讀入列數合計（含重複，實際入庫以下方覆蓋為準）: {tot}")
        cov = bh.coverage(con)
        print(f"\n=== broker_history.db 覆蓋 ===")
        for tbl in ("broker_orders", "broker_fills", "broker_realized"):
            c = cov[tbl]
            print(f"  {tbl:17s} {c['n']:>6} 列   {c['first']} .. {c['last']}")
        print(f"  其中手動（HTML）委託 {cov['manual_orders']} 列")
        print(f"  帳戶別分布 {cov['by_account']}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
