#!/usr/bin/env python3
"""手動下單的「同族群內挑誰」邏輯檢定。

使用者自述第三條判準：同族群裡挑（a）今天漲幅較低的，或（b）離 20MA 乖離較小的。
本腳本照儀表板同一口徑還原：
  · 族群 = biglot_dashboard.SUBCAT（45 檔手動策展細分產業，就是畫面上的族群）
  · 漲幅 = px / 官方前一日收盤 − 1（儀表板 chg_pct）
  · 20MA 乖離 = px / ma20 − 1（儀表板 bias20；ma20 取訊號日前 20 個交易日收盤均值，PIT）
  · px = 委託送出那一秒的最後成交價（原始逐筆重放，見 trader_biglot_decision_replay）

輸出每張新倉委託在「同族群且當天在宇宙內」的同業裡，漲幅與乖離各排第幾。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from trader_biglot_decision_replay import RAW_DIR, replay_day  # noqa: E402

DB = Path.home() / "goldenstocks-data/data/stocks.db"


def load_subcat() -> dict[str, str]:
    """直接從 biglot_dashboard.py 原始碼取 SUBCAT，避免 import 整支儀表板。"""
    src = Path("scripts/research/biglot_dashboard.py").read_text(encoding="utf-8")
    i = src.index("SUBCAT = {")
    j = src.index("}", src.index("3481", i))
    body = src[i + len("SUBCAT = "): j + 1]
    body = "\n".join(ln.split("#")[0] for ln in body.splitlines())
    return eval(body)  # noqa: S307 -- 純字面量 dict


def day_universe(day: str) -> set[str]:
    syms = set()
    with open(RAW_DIR / f"raw_{day}.jsonl") as fh:
        for line in fh:
            if '"subscribed"' in line and '"trades"' in line:
                try:
                    syms.add(str(json.loads(line)["payload"]["data"]["symbol"]))
                except Exception:  # noqa: BLE001
                    continue
    return syms


def ma20_and_prevclose(symbols: set[str], day: str) -> dict[str, tuple[float, float]]:
    """PIT：只用 < day 的收盤；finmind 優先、twse_mi_index/tpex_daily 補。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = f"""select stock_id, trade_date, close, source from stock_daily_bars
            where trade_date < ? and trade_date >= date(?, '-70 day')
              and stock_id in ({','.join('?' * len(symbols))})
              and source in ('finmind','twse_mi_index','tpex_daily')"""
    df = pd.read_sql(q, con, params=[day, day, *sorted(symbols)])
    con.close()
    df["pri"] = df["source"].map({"finmind": 0, "twse_mi_index": 1, "tpex_daily": 2})
    df = df.sort_values("pri").drop_duplicates(["stock_id", "trade_date"])
    out = {}
    for sid, g in df.sort_values("trade_date").groupby("stock_id"):
        cl = g["close"].tolist()
        if len(cl) < 20:
            continue
        out[sid] = (sum(cl[-20:]) / 20, cl[-1])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decision", default=str(Path.home() / "goldenstocks-data/data/scratch/trader_biglot_decision.csv"))
    ap.add_argument("--days", nargs="+", default=["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"])
    ap.add_argument("--out", default=str(Path.home() / "goldenstocks-data/data/scratch/trader_peer_choice.csv"))
    args = ap.parse_args()

    subcat = load_subcat()
    c = pd.read_csv(args.decision)
    c = c[c["big_day"].notna()].copy()
    c["stock"] = c["stock"].astype(str)
    rows = []
    for day, g in c[c["day"].isin(args.days)].groupby("day"):
        uni = day_universe(day)
        ref = ma20_and_prevclose(uni, day)
        acc = replay_day(day, uni)
        acc = {k: v for k, v in acc.items() if v["ts"]}
        print(f"{day}: 宇宙 {len(uni)} · 有 ma20 {len(ref)} · 有逐筆 {len(acc)}")
        for r in g.itertuples():
            grp = subcat.get(r.stock)
            peers = [s for s in uni if subcat.get(s) == grp] if grp else []
            peers = [s for s in peers if s in ref and s in acc]
            t = datetime.fromisoformat(f"{day}T{r.submit}+08:00").timestamp()
            snap = {}
            for s in peers:
                A = acc[s]
                i = bisect.bisect_right(A["ts"], t) - 1
                if i < 0:
                    continue
                px = A["px"][i]
                ma, pc = ref[s]
                snap[s] = (100 * (px / pc - 1), 100 * (px / ma - 1))
            rec = {"day": day, "submit": r.submit, "stock": r.stock, "uname": r.uname,
                   "side": r.side, "order_type": r.order_type, "group": grp,
                   "n_peers": len(snap), "status": r.status}
            if r.stock in snap and len(snap) >= 2:
                chg = pd.Series({k: v[0] for k, v in snap.items()})
                bia = pd.Series({k: v[1] for k, v in snap.items()})
                rec.update({
                    "chg": round(chg[r.stock], 2),
                    "chg_rank": int(chg.rank()[r.stock]),
                    "chg_pctile": round(100 * (chg < chg[r.stock]).mean(), 1),
                    "chg_min": round(chg.min(), 2), "chg_max": round(chg.max(), 2),
                    "bias20": round(bia[r.stock], 2),
                    "bias_rank": int(bia.rank()[r.stock]),
                    "bias_pctile": round(100 * (bia < bia[r.stock]).mean(), 1),
                    "bias_min": round(bia.min(), 2), "bias_max": round(bia.max(), 2),
                    "peers": "|".join(f"{k}:{v[0]:+.1f}/{v[1]:+.0f}" for k, v in sorted(snap.items())),
                })
            rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)
    print(f"wrote {args.out}  n={len(out)}　可比對(同族群≥2檔) n={int(out['chg'].notna().sum())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
