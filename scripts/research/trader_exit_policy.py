#!/usr/bin/env python3
"""平倉（出場）委託單逐單研究 —— 整個行為研究唯一還沒碰過的一塊。

為什麼需要：
  進場側已經做到逐單（掛價錨點／五檔位置／MAE-MFE／錯過成本），但 26 張平倉委託
  從來沒分析過。使用者 2026-10-04 自述的出場規則是「條件完成任務不用急著平倉，
  有時候掛好還是等待最高的價格排隊賣出」—— 這是一個可直接檢定的宣稱：
  把賣單掛在對手價上方 N 檔等排隊，比立刻吃對手價好還是差？

口徑：
  · 全部距離用「檔」（tick），不用 %，因為跨價位級距不可比
  · 賣出平倉的對手價 = 期貨買一；買進平倉（回補）的對手價 = 期貨賣一
  · 掛價距對手價為正 = 掛在對自己有利的方向（等排隊）；為負 = 可立即成交
  · 四種對照政策都用同一份五檔逐筆快照重放，成交價口徑一致

資料源：`~/goldenstocks-data/cache/{root}_books/{root}_books_{date}.jsonl`
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from trader_mae_mfe_engine import load_book, tick_size  # noqa: E402

SCR = Path.home() / "goldenstocks-data/data/scratch"
CLOSE_HHMM = "13:30:00"


def _epoch(day: str, hms: str) -> float:
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


def _at(B: dict, t: float) -> int:
    """回傳 <= t（epoch 秒）的最後一筆五檔快照索引；沒有就回 -1。"""
    return bisect.bisect_right(B["ts"], t) - 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", default=str(SCR / "trader_orders_tagged.pkl"))
    ap.add_argument("--out", default=str(SCR / "trader_exit_policy.csv"))
    args = ap.parse_args()

    o = pd.read_pickle(args.orders)
    cl = o[o.ot == "Close"].copy()
    root = cl.symbol.str.slice(2).map(lambda r: r[:-1] if r.endswith("F") else r)
    cl["root"] = root

    rows = []
    for r in cl.sort_values(["day", "submit_time"]).itertuples():
        rec = {"日期": r.day, "送出": str(r.submit_time)[:8], "標的": r.nm, "代號": r.root,
               "方向": "賣出平倉" if r.side == "Sell" else "買進平倉(回補)",
               "口數": r.lot, "掛價": r.price, "狀態": r.status,
               "成交時間": str(r.fill_t)[:8] if pd.notna(r.fill_t) else None,
               "成交價": r.fill_px if pd.notna(r.fill_px) else None}
        B = load_book(r.root, r.day)
        if B is None:
            rec["備註"] = "無期貨買賣簿"
            rows.append(rec); continue
        i = _at(B, _epoch(r.day, rec["送出"]))
        if i < 0:
            rec["備註"] = "送出時點早於買賣簿起始"
            rows.append(rec); continue
        sell = r.side == "Sell"
        b1, a1 = B["bid1"][i], B["ask1"][i]
        opp = b1 if sell else a1            # 對手價
        same = a1 if sell else b1           # 同向最優價
        tk = tick_size(r.price)
        # 掛價距對手價（檔）：正 = 往有利方向掛、要排隊；負 = 可立即成交
        rec["送出_買一"], rec["送出_賣一"] = b1, a1
        rec["送出_價差檔"] = round((a1 - b1) / tk, 1) if (b1 and a1) else None
        rec["掛價距對手價檔"] = round((r.price - opp) / tk * (1 if sell else -1), 1) if opp else None
        rec["掛價距同向最優檔"] = round((r.price - same) / tk * (1 if sell else -1), 1) if same else None
        rec["可立即成交"] = bool(rec["掛價距對手價檔"] is not None and rec["掛價距對手價檔"] <= 0)

        # 送出後路徑（到成交或收盤）
        end = rec["成交時間"] or CLOSE_HHMM
        j = _at(B, _epoch(r.day, end))
        seg = slice(i, max(i + 1, j + 1))
        bb, aa = B["bid1"][seg], B["ask1"][seg]
        if sell:
            rec["送出後最高買一"] = max([x for x in bb if x] or [None]) if any(bb) else None
            best = rec["送出後最高買一"]
        else:
            rec["送出後最低賣一"] = min([x for x in aa if x] or [None]) if any(aa) else None
            best = rec["送出後最低賣一"]
        rec["掛價曾被觸及"] = bool(best is not None and ((best >= r.price) if sell else (best <= r.price)))

        # 到收盤的整段極值（量化「再等下去會不會更好」）
        k = _at(B, _epoch(r.day, CLOSE_HHMM))
        seg2 = slice(i, max(i + 1, k + 1))
        bb2, aa2 = B["bid1"][seg2], B["ask1"][seg2]
        if sell:
            rec["到收盤最高買一"] = max([x for x in bb2 if x] or [None]) if any(bb2) else None
        else:
            rec["到收盤最低賣一"] = min([x for x in aa2 if x] or [None]) if any(aa2) else None

        # 四種政策的成交價（同一份逐筆重放）
        pol = {}
        pol["P1_立刻吃對手價"] = opp
        pol["P2_掛同向最優價"] = _first_touch(B, i, k, same, sell)
        pol["P3_實際做法"] = r.fill_px if pd.notna(r.fill_px) else None
        pol["P4_掛到收盤最高/最低"] = (rec.get("到收盤最高買一") if sell else rec.get("到收盤最低賣一"))
        for name, px in pol.items():
            rec[name] = px
            rec[name + "_vs_P1檔"] = (round((px - opp) / tk * (1 if sell else -1), 1)
                                      if (px and opp) else None)
        # 沒成交的話，收盤平倉（代理人一定要有這個退路）
        if pd.isna(r.fill_px):
            cp = B["bid1"][k] if sell else B["ask1"][k]
            rec["若收盤強制平倉價"] = cp
            rec["收盤平倉_vs_P1檔"] = round((cp - opp) / tk * (1 if sell else -1), 1) if (cp and opp) else None
        if rec["成交時間"]:
            rec["等待分鐘"] = round((pd.Timestamp(f"{r.day} {rec['成交時間']}")
                                 - pd.Timestamp(f"{r.day} {rec['送出']}")).total_seconds() / 60, 1)
        rows.append(rec)

    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)
    print(f"wrote {args.out}  n={len(out)}  有買賣簿 {int(out['掛價距對手價檔'].notna().sum())}")
    return 0


def _first_touch(B: dict, i: int, k: int, limit: float | None, sell: bool) -> float | None:
    """掛在 limit 價，從索引 i 往後找第一次被對手價觸及的時點；沒觸及回 None。"""
    if not limit:
        return None
    for n in range(i, max(i + 1, k + 1)):
        p = B["bid1"][n] if sell else B["ask1"][n]
        if p and ((p >= limit) if sell else (p <= limit)):
            return limit
    return None


if __name__ == "__main__":
    raise SystemExit(main())
