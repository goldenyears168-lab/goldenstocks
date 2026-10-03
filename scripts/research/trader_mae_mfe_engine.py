#!/usr/bin/env python3
"""MAE／MFE ＋ 未成交單錯過成本引擎（接在雙時點引擎之後）。

為什麼需要：
  1. 已驗證「訊號越強、成交後前 30 分鐘回檔越深」，但只有區間端點報酬，
     不知道中間最深回到哪 → 無法訂停損。MAE（最大不利偏移）補這一塊。
  2. 52 張新倉有 29 張沒成交，其中包含訊號全場第一的華邦電（掛低 1 元）與
     走勢最強的環球晶（送出後 30 分漲 3.07%）。錯過成本完全沒進帳本。
  3. 「掛價距離」原本拿期貨掛價去比**現股**價格，有基差所以是錯的。本引擎
     改用同一檔**個股期貨的買一／賣一**，並以「檔數」為單位（跨價位可比）。

資料源：`~/goldenstocks-data/cache/{root}_books/{root}_books_{date}.jsonl`
（個股期貨五檔逐筆快照，23/25 檔標的、48/52 張委託在那四天都有）。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

CACHE = Path.home() / "goldenstocks-data/cache"
SCR = Path.home() / "goldenstocks-data/data/scratch"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]


def tick_size(px: float) -> float:
    """TAIFEX 股票期貨最小升降單位（與現股相同級距）。"""
    if px < 10:
        return 0.01
    if px < 50:
        return 0.05
    if px < 100:
        return 0.1
    if px < 500:
        return 0.5
    if px < 1000:
        return 1.0
    return 5.0


def load_book(root: str, day: str) -> dict | None:
    for cand in (root.lower(), root.lower() + "f"):
        p = CACHE / f"{cand}_books" / f"{cand}_books_{day}.jsonl"
        if p.exists() and p.stat().st_size > 1000:
            break
    else:
        return None
    ts, b1, a1, bs, as_, bids, asks = [], [], [], [], [], [], []
    with open(p) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if r.get("stale") or r.get("session") != "day":
                continue
            bb, aa = r.get("bids") or [], r.get("asks") or []
            if not bb or not aa:
                continue
            t = datetime.fromisoformat(r["ts"]).timestamp()
            ts.append(t)
            b1.append(float(bb[0]["price"]))
            a1.append(float(aa[0]["price"]))
            bs.append(int(bb[0]["size"]))
            as_.append(int(aa[0]["size"]))
            bids.append([(float(x["price"]), int(x["size"])) for x in bb])
            asks.append([(float(x["price"]), int(x["size"])) for x in aa])
    if not ts:
        return None
    order = sorted(range(len(ts)), key=lambda i: ts[i])
    pick = lambda L: [L[i] for i in order]  # noqa: E731
    return {"ts": pick(ts), "bid1": pick(b1), "ask1": pick(a1),
            "bsize": pick(bs), "asize": pick(as_), "bids": pick(bids), "asks": pick(asks)}


def at(bk: dict, t: float) -> int | None:
    j = bisect.bisect_right(bk["ts"], t) - 1
    return j if j >= 0 else None


def mid(bk: dict, j: int) -> float:
    return (bk["bid1"][j] + bk["ask1"][j]) / 2


def queue_ahead(bk: dict, j: int, price: float, side: str) -> int | None:
    """你掛的價位上，你前面排了幾張（同價位既有掛量）。價位在五檔外回 None。"""
    levels = bk["bids"][j] if side == "Buy" else bk["asks"][j]
    for p, s in levels:
        if abs(p - price) < 1e-9:
            return s
    return None


def excursion(bk: dict, t0: float, fill_px: float, side: str, seconds: int | None):
    """從成交那一刻起算的最大不利／最大有利偏移（用中價，避開買賣價彈跳）。"""
    i = at(bk, t0)
    if i is None:
        return (None,) * 4
    j = len(bk["ts"]) - 1 if seconds is None else (at(bk, t0 + seconds) or i)
    if j <= i:
        return (None,) * 4
    seg = [mid(bk, k) for k in range(i, j + 1)]
    sgn = 1 if side == "Buy" else -1
    pnl = [sgn * (m - fill_px) for m in seg]
    worst, best = min(pnl), max(pnl)
    tk = tick_size(fill_px)
    return (round(100 * worst / fill_px, 3), round(worst / tk, 1),
            round(100 * best / fill_px, 3), round(best / tk, 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SCR / "trader_mae_mfe.csv"))
    args = ap.parse_args()
    specs = json.loads((Path.home() / "goldenstocks-data/data/cache/taifex_stock_futures_specs.json")
                       .read_text(encoding="utf-8"))
    d = pd.read_pickle(SCR / "trader_behavior_tables.pkl")
    o = d["futopt"]["orders"]
    o = o[~o["auto"]].copy()
    o["day"] = o["date"].dt.strftime("%Y-%m-%d")
    o = o[o["day"].isin(DAYS) & o["order_type"].eq("FutOptOrderType.New")].copy()
    o["root"] = o["symbol"].str.slice(2).map(lambda r: r[:-1] if r.endswith("F") else r)
    o["uname"] = o["root"].map(lambda r: specs.get(r, {}).get("name", r))
    o["K"] = o["day"] + "|" + o["order_no"].astype(str).str.strip()
    f = d["futopt"]["fills"]
    f = f[f["tag"] == "manual"].copy()
    f["day"] = f["date"].dt.strftime("%Y-%m-%d")
    f["K"] = f["day"] + "|" + f["order_no"].astype(str).str.strip()
    fpx = f.groupby("K")["filled_price"].mean().to_dict()
    ftm = f.groupby("K")["filled_time"].min().to_dict()

    books: dict[tuple[str, str], dict | None] = {}
    recs = []
    for r in o.sort_values(["day", "submit_time"]).itertuples():
        bk = books.setdefault((r.root, r.day), load_book(r.root, r.day))
        rec = {"日期": r.day, "送出時間": r.submit_time, "標的": r.uname, "代號": r.root,
               "方向": "開多" if r.side == "Buy" else "開空", "口數": r.lot,
               "掛價": r.price, "狀態": r.status,
               "成交時間": ftm.get(r.K), "成交價": fpx.get(r.K)}
        if bk is None:
            rec["備註"] = "無期貨買賣簿"
            recs.append(rec)
            continue
        t1 = datetime.fromisoformat(f"{r.day}T{str(r.submit_time)[:12]}+08:00").timestamp()
        i = at(bk, t1)
        if i is not None:
            tk = tick_size(bk["ask1"][i])
            rec["送出_期貨買一"] = bk["bid1"][i]
            rec["送出_期貨賣一"] = bk["ask1"][i]
            rec["送出_價差檔"] = round((bk["ask1"][i] - bk["bid1"][i]) / tk, 1)
            rec["送出_買一量"] = bk["bsize"][i]
            rec["送出_賣一量"] = bk["asize"][i]
            rec["送出_五檔總量"] = sum(s for _, s in bk["bids"][i]) + sum(s for _, s in bk["asks"][i])
            ref = bk["ask1"][i] if r.side == "Buy" else bk["bid1"][i]
            sgn = 1 if r.side == "Buy" else -1
            rec["掛價距對手價檔"] = round(sgn * (r.price - ref) / tk, 1)
            own = bk["bid1"][i] if r.side == "Buy" else bk["ask1"][i]
            rec["掛價距同向最優檔"] = round(sgn * (r.price - own) / tk, 1)
            rec["可立即成交"] = bool(sgn * (r.price - ref) >= 0)
            rec["前面排隊張數"] = queue_ahead(bk, i, r.price, r.side)
        filled = rec["成交價"] is not None and not pd.isna(rec["成交價"])
        if filled and isinstance(rec["成交時間"], str):
            t2 = datetime.fromisoformat(f"{r.day}T{str(rec['成交時間'])[:12]}+08:00").timestamp()
            j = at(bk, t2 - 0.001)
            if j is not None:
                tk2 = tick_size(rec["成交價"])
                rec["成交時_買一"] = bk["bid1"][j]
                rec["成交時_賣一"] = bk["ask1"][j]
                fp = float(rec["成交價"])
                if r.side == "Buy":
                    kind = ("主動吃賣一" if fp >= bk["ask1"][j]
                            else ("被動被賣單打到" if fp <= bk["bid1"][j] else "價差中間"))
                else:
                    kind = ("主動吃買一" if fp <= bk["bid1"][j]
                            else ("被動被買單打到" if fp >= bk["ask1"][j] else "價差中間"))
                rec["成交性質"] = kind
                rec["成交價優於對手價檔"] = round(
                    (1 if r.side == "Buy" else -1) * (bk["ask1"][j] - fp) / tk2
                    if r.side == "Buy" else (fp - bk["bid1"][j]) / tk2, 1)
            for lab, sec in (("30分", 1800), ("60分", 3600), ("收盤", None)):
                mae_pct, mae_tick, mfe_pct, mfe_tick = excursion(
                    bk, t2, float(rec["成交價"]), r.side, sec)
                rec[f"MAE_{lab}%"] = mae_pct
                rec[f"MAE_{lab}檔"] = mae_tick
                rec[f"MFE_{lab}%"] = mfe_pct
                rec[f"MFE_{lab}檔"] = mfe_tick
                k = at(bk, t2 + sec) if sec else len(bk["ts"]) - 1
                if k is not None and k > (at(bk, t2) or 0):
                    sg = 1 if r.side == "Buy" else -1
                    rec[f"損益_{lab}%"] = round(
                        100 * sg * (mid(bk, k) - float(rec["成交價"])) / float(rec["成交價"]), 3)
        elif i is not None:
            # 未成交：掛價後來有沒有被觸及？若當時直接吃對手價會怎樣？
            tk = tick_size(bk["ask1"][i])
            if r.side == "Buy":
                after = bk["ask1"][i:]
                best = min(after) if after else None
                rec["掛價曾被觸及"] = bool(best is not None and best <= r.price)
                rec["最接近差幾檔"] = round((best - r.price) / tk, 1) if best is not None else None
            else:
                after = bk["bid1"][i:]
                best = max(after) if after else None
                rec["掛價曾被觸及"] = bool(best is not None and best >= r.price)
                rec["最接近差幾檔"] = round((r.price - best) / tk, 1) if best is not None else None
            hypo = bk["ask1"][i] if r.side == "Buy" else bk["bid1"][i]
            rec["假設當時吃對手價"] = hypo
            for lab, sec in (("30分", 1800), ("60分", 3600), ("收盤", None)):
                k = at(bk, t1 + sec) if sec else len(bk["ts"]) - 1
                if k is not None and k > i:
                    sg = 1 if r.side == "Buy" else -1
                    rec[f"錯過成本_{lab}%"] = round(100 * sg * (mid(bk, k) - hypo) / hypo, 3)
                mae_pct, mae_tick, mfe_pct, mfe_tick = excursion(bk, t1, hypo, r.side, sec)
                rec[f"假設MAE_{lab}%"] = mae_pct
                rec[f"假設MFE_{lab}%"] = mfe_pct
        recs.append(rec)
    out = pd.DataFrame(recs)
    out.to_csv(args.out, index=False)
    print(f"wrote {args.out}  {len(out)} 張 × {len(out.columns)} 欄")
    print(f"  有期貨買賣簿 {int(out['送出_期貨賣一'].notna().sum())} 張")
    print(f"  有成交且算出 MAE {int(out['MAE_30分%'].notna().sum())} 張")
    print(f"  未成交且算出錯過成本 {int(out['錯過成本_30分%'].notna().sum())} 張")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
