#!/usr/bin/env python3
"""收盤後掃描當日逐筆流,偵測「機械切單」指紋(TWAP/程式分批單)。唯讀研究,無送單路徑。

事前登記的偵測規格(2026-09-14 鎖定,收滿 20 個交易日前不調參數):
  資料    cache/biglot_live_watch/raw_{date}.jsonl 的 trades 頻道逐筆
          (serial 去重殭屍重送、Δvolume 計量、isContinuous 為真才算 ——
           處置/分盤股本來就無法切單,排除不影響研究目的)
  方向    price >= ask → 買方主動 B;price <= bid → 賣方主動 S;其餘捨棄
  指紋    同 (sid, 方向, 張數) 分組:張數 >= 2、筆數 >= 15、
          相鄰間隔(排除 <=1 秒的同秒串)>= 12 個、間隔中位數 20~600 秒、
          規律度 = IQR/中位數 <= 0.9
  輸出    cache/biglot_live_watch/fingerprints/fp_{date}.json
          (groups: 每組指紋明細;per_sid: 每檔買/賣程式合計金額 —— 之後檢定
           「當日有買程式 → 隔日開盤」用 per_sid 即可,毋須重掃)
  檢定    收滿 20 個交易日後測「買程式淨額 → 隔夜超額」,在此之前只累積不下結論。

用法:
  biglot_fingerprint_scan.py --date 2026-09-14   # 單日
  biglot_fingerprint_scan.py --backfill          # 補掃所有還沒有輸出的 raw 檔
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from stock_db import DATA_DIR

RAW_DIR = Path(DATA_DIR).parent / "cache" / "biglot_live_watch"
OUT_DIR = RAW_DIR / "fingerprints"

MIN_LOTS = 2
MIN_TRADES = 15
MIN_INTERVALS = 12
MED_IV_RANGE = (20.0, 600.0)
MAX_DISPERSION = 0.9  # IQR / 中位間隔


def parse_trades(raw_path: Path) -> dict[str, list[tuple[float, float, int, str]]]:
    """回傳 sid -> [(epoch_sec, price, d_lots, side)];serial 去重、Δvolume 計量。"""
    trades: dict[str, list[tuple[float, float, int, str]]] = {}
    last: dict[str, tuple[int, int]] = {}  # sid -> (serial, cum_volume)
    with raw_path.open() as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("event") != "data" or payload.get("channel") != "trades":
                continue
            d = payload["data"]
            if not d.get("isContinuous"):
                continue
            sid = d["symbol"]
            serial = d.get("serial", 0)
            if sid in last and serial <= last[sid][0]:
                continue  # 殭屍重送
            vol = d.get("volume", 0)
            d_lots = vol - last[sid][1] if sid in last else int(d.get("size", 0))
            last[sid] = (serial, vol)
            if d_lots <= 0:
                continue
            price, bid, ask = d.get("price"), d.get("bid"), d.get("ask")
            if price is None:
                continue
            if ask and price >= ask:
                side = "B"
            elif bid and price <= bid:
                side = "S"
            else:
                continue
            trades.setdefault(sid, []).append((d["time"] / 1e6, float(price), d_lots, side))
    return trades


def detect(trades: dict[str, list[tuple[float, float, int, str]]]) -> list[dict]:
    groups = []
    for sid, rows in trades.items():
        by_key: dict[tuple[str, int], list[tuple[float, float]]] = {}
        for t, px, lots, side in rows:
            if lots >= MIN_LOTS:
                by_key.setdefault((side, lots), []).append((t, px))
        for (side, lots), arr in by_key.items():
            if len(arr) < MIN_TRADES:
                continue
            times = sorted(t for t, _ in arr)
            ivs = [b - a for a, b in zip(times, times[1:]) if b - a > 1.0]
            if len(ivs) < MIN_INTERVALS:
                continue
            ivs.sort()
            med = statistics.median(ivs)
            if not (MED_IV_RANGE[0] <= med <= MED_IV_RANGE[1]):
                continue
            q1 = ivs[len(ivs) // 4]
            q3 = ivs[(3 * len(ivs)) // 4]
            if (q3 - q1) / med > MAX_DISPERSION:
                continue
            amount = sum(px for _, px in arr) * lots * 1000
            groups.append(
                {
                    "sid": sid,
                    "side": side,
                    "lots": lots,
                    "count": len(arr),
                    "median_interval_s": round(med, 1),
                    "dispersion": round((q3 - q1) / med, 3),
                    "amount": round(amount),
                    "t_first": times[0],
                    "t_last": times[-1],
                }
            )
    groups.sort(key=lambda g: -g["amount"])
    return groups


def scan_one(date: str, force: bool = False) -> bool:
    raw = RAW_DIR / f"raw_{date}.jsonl"
    out = OUT_DIR / f"fp_{date}.json"
    if not raw.is_file() or raw.stat().st_size == 0:
        print(f"skip {date}: raw 不存在或為空")
        return False
    if out.exists() and not force:
        print(f"skip {date}: 已有輸出")
        return False
    trades = parse_trades(raw)
    groups = detect(trades)
    per_sid: dict[str, dict[str, float]] = {}
    for g in groups:
        slot = per_sid.setdefault(g["sid"], {"buy_amt": 0, "sell_amt": 0, "n_groups": 0})
        slot["buy_amt" if g["side"] == "B" else "sell_amt"] += g["amount"]
        slot["n_groups"] += 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "date": date,
                "n_trades_parsed": sum(len(v) for v in trades.values()),
                "n_symbols": len(trades),
                "groups": groups,
                "per_sid": per_sid,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    n_buy = sum(1 for g in groups if g["side"] == "B")
    print(f"{date}: {len(groups)} 組指紋(買{n_buy}/賣{len(groups) - n_buy}) → {out.name}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--backfill", action="store_true", help="補掃所有缺輸出的 raw 檔")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.backfill:
        for raw in sorted(RAW_DIR.glob("raw_????-??-??.jsonl")):
            scan_one(raw.stem.removeprefix("raw_"), force=args.force)
    elif args.date:
        scan_one(args.date, force=args.force)
    else:
        ap.error("--date 或 --backfill 擇一")


if __name__ == "__main__":
    main()
