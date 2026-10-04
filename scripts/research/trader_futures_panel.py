#!/usr/bin/env python3
"""個股期貨價格面板 —— 把報酬口徑從現貨換成期貨。

為什麼必須換：大戶／散戶流量是從**現貨逐筆**算的（儀表板就是這樣,這部分沒問題）,
但先前所有回測的**報酬也用現貨價**算。jack 實際交易的是個股期貨,兩者有基差
（先前量到 ~0.72%）。而他的交易戰場 23 檔裡有 22 檔四天都有期貨五檔快照,
所以這個誤差現在可以直接消掉,不必再用現貨當代理。

口徑（三種都算,因為它們回答不同問題）：
  · `fut_mid`  中價 (bid1+ask1)/2 —— 無摩擦的「價格有沒有動」
  · `fut_ask`  賣一 —— 做多**吃對手價**進場要付的價
  · `fut_bid`  買一 —— 做多出場能拿到的價
真實一趟做多 = 進場付 ask、出場收 bid,所以 `fwd_*_cross` 內含來回價差,
那才是可實現的報酬。`fwd_*_mid` 用來跟先前的現貨數字對照（同樣無摩擦口徑）。

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

from trader_mae_mfe_engine import load_book  # noqa: E402

P = Path.home() / "goldenstocks-data/data"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]


def core_universe(panel_sids: set[str]) -> list[str]:
    """交易戰場 23 檔：宇宙扣掉參考列與獨立檔,再扣第9/10群的池內成員。"""
    r = json.loads(Path("config/biglot_roles.json").read_text(encoding="utf-8"))
    drop = ({x["sid"] for x in r["reference_in_pool"]}
            | {x["sid"] for x in r["independent"]}
            | {"2481", "1303"})          # 強茂(第9群)、南亞(第10群)
    return sorted(panel_sids - drop)


def sid_to_root(specs: dict) -> dict[str, str]:
    """證券代號 → 期貨商品根代碼。同一標的有大型/小型時取**大型**（他只用大型）。"""
    out: dict[str, tuple[str, int]] = {}
    for root, s in specs.items():
        sid = str(s.get("underlying_id") or s.get("stock_id") or "")
        sh = s.get("contract_shares") or 0
        if not sid or not sh:
            continue
        if sid not in out or sh > out[sid][1]:
            out[sid] = (root, sh)
    return {k: v[0] for k, v in out.items()}


def _at(bk: dict, t: float) -> int | None:
    j = bisect.bisect_right(bk["ts"], t) - 1
    return j if j >= 0 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(P / "scratch/trader_feature_panel.parquet"))
    ap.add_argument("--out", default=str(P / "scratch/trader_futures_panel.parquet"))
    args = ap.parse_args()

    d = pd.read_parquet(args.panel, columns=["day", "t", "hm", "sid"])
    specs = json.loads((P / "cache/taifex_stock_futures_specs.json").read_text())
    s2r = sid_to_root(specs)
    core = core_universe(set(d.sid.unique()))
    print(f"交易戰場 {len(core)} 檔", file=sys.stderr)

    rows, missing = [], []
    for sid in core:
        root = s2r.get(sid)
        if not root:
            missing.append((sid, "無期貨契約")); continue
        for day in DAYS:
            bk = load_book(root, day)
            if bk is None:
                missing.append((sid, f"{day} 無五檔")); continue
            sub = d[(d.day == day) & (d.sid == sid)]
            for t in sub.t:
                j = _at(bk, t)
                if j is None:
                    continue
                b, a = bk["bid1"][j], bk["ask1"][j]
                if not b or not a or a <= b:
                    continue          # 0 元雜訊 / 交叉價一律丟棄
                rows.append({"day": day, "sid": sid, "t": t, "root": root,
                             "fut_bid": b, "fut_ask": a, "fut_mid": (a + b) / 2,
                             "fut_spread_pct": 100 * (a - b) / ((a + b) / 2)})
    f = pd.DataFrame(rows)
    if f.empty:
        print("沒有任何期貨報價", file=sys.stderr); return 1

    # 前瞻報酬：mid→mid（無摩擦,可跟現貨數字對照）與 ask→bid（含來回價差,可實現）
    f = f.sort_values(["sid", "day", "t"])
    out = []
    for (sid, day), g in f.groupby(["sid", "day"], sort=False):
        ts = g.t.to_numpy(); mid = g.fut_mid.to_numpy()
        bid = g.fut_bid.to_numpy(); ask = g.fut_ask.to_numpy()
        n = len(ts)
        for i in range(n):
            rec = {"day": day, "sid": sid, "t": ts[i]}
            for lab, sec in (("30", 1800.0), ("60", 3600.0)):
                k = bisect.bisect_right(ts, ts[i] + sec) - 1
                rec[f"fut_fwd{lab}_mid"] = 100 * (mid[k] / mid[i] - 1) if k > i else None
                rec[f"fut_fwd{lab}_cross"] = 100 * (bid[k] / ask[i] - 1) if k > i else None
            k = n - 1
            rec["fut_fwd_close_mid"] = 100 * (mid[k] / mid[i] - 1) if k > i else None
            rec["fut_fwd_close_cross"] = 100 * (bid[k] / ask[i] - 1) if k > i else None
            out.append(rec)
    fw = pd.DataFrame(out)
    res = f.merge(fw, on=["day", "sid", "t"], how="left")
    res.to_parquet(args.out, index=False)
    print(f"wrote {args.out}  {len(res):,} 列 · {res.sid.nunique()} 檔", file=sys.stderr)
    if missing:
        print(f"缺資料 {len(missing)} 筆: {missing[:6]}", file=sys.stderr)
    print(f"期貨買賣價差中位 {res.fut_spread_pct.median():.3f}%"
          f"（來回吃價差成本 ≈ {res.fut_spread_pct.median():.3f}%）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
