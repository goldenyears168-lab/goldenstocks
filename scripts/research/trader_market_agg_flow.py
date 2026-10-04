#!/usr/bin/env python3
"""全市場（42 檔宇宙）累計大戶淨額 − 散戶淨額的當日軌跡，與使用者下單時點對照。

使用者 2026-10-04 澄清：他看的不是台指期自身的大戶/散戶，而是**儀表板台指期圖
上疊的「全部股票大戶淨額 vs 散戶淨額」**（程式裡的 AGG），並且：
  · 要求 5 分、30 分、全日三個尺度的「大戶淨額 > 散戶淨額」
  · 進場時機偏好：差額從**低點近 5 分鐘反彈初期**，或**近 5 分鐘持續拉大**
  · 搭配 30 分鐘趨勢判讀
  · 舉例 9/29：盤中軌跡趨勢線明顯＝大戶買更多、散戶賣更多

本腳本逐分鐘重建這條軌跡（口徑同 _ingest_trade），標出他每一張委託的位置。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from trader_claim_verify import replay  # noqa: E402

SCR = Path.home() / "goldenstocks-data/data/scratch"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
TZ = timezone(timedelta(hours=8))


def agg_series(acc: dict, day: str) -> pd.DataFrame:
    """逐分鐘：全宇宙累計大戶淨額、散戶淨額、差額（萬元），及 5/30 分窗的增量。"""
    t0 = datetime.fromisoformat(f"{day}T09:00:00+08:00").timestamp()
    rows = []
    for mm in range(0, 281):
        t = t0 + mm * 60
        big = ret = tot = 0.0
        for A in acc.values():
            ts = A["ts"]
            j = np.searchsorted(ts, t, side="right")
            if j:
                big += A["c_big"][j]
                ret += A["c_rb"][j] - A["c_rs"][j]
                tot += A["c_amt"][j]
        rows.append({"t": datetime.fromtimestamp(t, TZ), "hm": datetime.fromtimestamp(t, TZ).strftime("%H:%M"),
                     "大戶累計萬": big / 1e4, "散戶累計萬": ret / 1e4,
                     "差額萬": (big - ret) / 1e4, "成交累計萬": tot / 1e4})
    d = pd.DataFrame(rows)
    d["差額_5分增量"] = d["差額萬"].diff(5)
    d["差額_30分增量"] = d["差額萬"].diff(30)
    # 差額的當日低點，以及「從低點反彈」的判定
    d["差額_當日最低"] = d["差額萬"].cummin()
    d["距當日低萬"] = d["差額萬"] - d["差額_當日最低"]
    d["剛破低後分鐘"] = (d["差額萬"] <= d["差額_當日最低"] + 1e-9).astype(int)
    since = []
    last = None
    for i, v in enumerate(d["剛破低後分鐘"]):
        if v:
            last = i
        since.append(None if last is None else i - last)
    d["距差額低點分鐘"] = since
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SCR / "trader_market_agg_flow.csv"))
    args = ap.parse_args()
    specs = json.loads((Path.home() / "goldenstocks-data/data/cache/taifex_stock_futures_specs.json")
                       .read_text(encoding="utf-8"))
    d = pd.read_pickle(SCR / "trader_behavior_tables.pkl")
    o = d["futopt"]["orders"]
    o = o[~o["auto"]].copy()
    o["day"] = o["date"].dt.strftime("%Y-%m-%d")
    o = o[o["day"].isin(DAYS) & o["order_type"].eq("FutOptOrderType.New")].copy()
    root = o["symbol"].str.slice(2).map(lambda r: r[:-1] if r.endswith("F") else r)
    o["uname"] = root.map(lambda r: specs.get(r, {}).get("name", r))
    o["hm"] = o["submit_time"].str.slice(0, 5)
    o["方向"] = np.where(o["side"].eq("Buy"), "買", "空")

    frames = []
    for day in DAYS:
        acc = replay(day)
        if not acc:
            continue
        s = agg_series(acc, day)
        s["day"] = day
        g = o[o["day"] == day]
        mark = g.groupby("hm").apply(
            lambda x: "／".join(f"{r.uname}{r.方向}" for r in x.itertuples()),
            include_groups=False)
        s["我的委託"] = s["hm"].map(mark).fillna("")
        frames.append(s)
        print(f"{day}: 重放 {len(acc)} 檔 · 委託 {len(g)} 張")
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}  {len(out)} 分鐘")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
