#!/usr/bin/env python3
"""M09／M07 隔日追蹤（唯讀）：訊號日 T 命中的「隔日沖慣犯」股，T+1 實際發生什麼——這些分點賣了多少、價格怎麼走、盤中氣氛線索。

對每個 T（inventory_<T>.json 有 M09 命中）且 T+1 的分點頁（chip/xiaoge/<N>/stock_top15.parquet）與日線已入庫者：
  flagged 分點＝命中時列出的「在該股有買進隔日賣紀錄」的分點（名稱對回 chip.top5 的代號）。
  flip_ratio     ＝ Σ flagged 在 N 日賣出張數 ÷ Σ flagged 在 T 日淨買張數（≥0.7 即照小哥慣性「隔天賣」）
  sell_share     ＝ Σ flagged N 日賣出 ÷ N 日成交量（賣壓占量）
  absorb         ＝ N 日前15名中「非 flagged」淨買超合計 ÷ N 日成交量（接手力）
  價格：gap（T收→N開）、oc（N開→N收）、cc、盤中最高／最低相對 T 收。
  盤中氣氛（若 xiaoge_m09_intraday 有記）：試撮價 vs T 收、10:00 價 vs 開盤、是否曾漲停／跌停、收盤時漲停、13:20 委買／委賣量。
輸出：${DATA}/chip/xiaoge_inventory/m09_followup_<N>.json 與累積 m09_followup.parquet（供 /inventory 讀）。
用法：PYTHONPATH=src .venv/bin/python scripts/ops/xiaoge_m09_followup.py [--all]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

INV = DATA_DIR / "chip" / "xiaoge_inventory"
CH = DATA_DIR / "chip" / "xiaoge"
INTRA = DATA_DIR / "chip" / "xiaoge_intraday"
WARR = DATA_DIR / "chip" / "xiaoge_warrant"


def next_day(c, t: str):
    r = c.execute("select trade_date from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date>? group by trade_date "
                  "having count(*)>1500 order by trade_date limit 1", (t,)).fetchone()
    return r[0] if r else None


def intraday_feats(N: str, sid: str, prev_close: float) -> dict:
    p = INTRA / N / "m09_ticks.jsonl"
    if not p.exists():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
    rows = [r for r in rows if r["s"] == sid]
    if not rows:
        return {}
    d = pd.DataFrame(rows)
    out = {"n_ticks": len(d)}
    pre = d[d.ts < "09:00:00"]
    tr = pre[pre.trial_px.notna()]
    if len(tr) and prev_close:
        out["trial_gap_pct"] = round((float(tr.trial_px.iloc[-1]) / prev_close - 1) * 100, 2)
    op = d.open.dropna()
    t1000 = d[(d.ts >= "10:00:00")].head(1)
    if len(op) and len(t1000):
        out["ret_1000_vs_open_pct"] = round((float(t1000["last"].iloc[0]) / float(op.iloc[0]) - 1) * 100, 2)
    out["lu_touched"] = bool(d.lu.fillna(False).astype(bool).any())
    out["ld_touched"] = bool(d.ld.fillna(False).astype(bool).any())
    # 開板：曾漲停鎖定、之後又不鎖
    lu = d.lu.fillna(False).astype(bool).tolist()
    out["lu_opens"] = int(sum(1 for i in range(1, len(lu)) if lu[i - 1] and not lu[i]))
    tail = d[(d.ts >= "13:15:00")]
    if len(tail):
        out["tail_bid_sum"] = float(tail.bsum.iloc[-1] or 0); out["tail_ask_sum"] = float(tail.asum.iloc[-1] or 0)
        out["close_lu"] = bool(tail.lu.iloc[-1] is True)
    return out


def warrant_flip(T: str, N: str, sid: str) -> dict:
    """T 日認購權證買超前5分點（xiaoge_warrant_tape summary）在 T+1 於同標的權證的淨買賣：w_flip＝max(0,−N淨)/T淨（≥0.7＝隔天倒）。"""
    sp, npq = WARR / T / "summary.json", WARR / N / "warrant_branch.parquet"
    if not sp.exists():
        return {}
    u = json.loads(sp.read_text(encoding="utf-8")).get("underlyings", {}).get(sid)
    if not u or not u["top_buyers"]:
        return {}
    out = {"w_buy_top5_wan": round(u["buy_amt_top5"] / 1e4), "w_buyers": "、".join(b["name"] for b in u["top_buyers"][:3])}
    if npq.exists():
        n = pd.read_parquet(npq); n = n[n.und == sid]
        if len(n):
            ids = {b["id"] for b in u["top_buyers"]}
            net_n = float((n[n.branch_id.isin(ids)].buy_lots - n[n.branch_id.isin(ids)].sell_lots).sum())
            net_t = float(sum(b["net_lots"] for b in u["top_buyers"]))
            out["w_net_T_lots"] = net_t; out["w_net_N_lots"] = net_n
            out["w_flip_ratio"] = round(max(0.0, -net_n) / net_t, 2) if net_t else None
    return out


def process(c, inv_path: Path) -> list[dict]:
    inv = json.loads(inv_path.read_text(encoding="utf-8")); T = inv["date"]
    N = next_day(c, T)
    if not N or not (CH / N / "stock_top15.parquet").exists():
        return []
    pq = pd.read_parquet(CH / N / "stock_top15.parquet")
    rows = []
    for k in ("M09", "M07"):
        for h in inv["methods"].get(k, {}).get("hits", []):
            sid = h["stock"]
            flagged_names = set(re.findall(r"([^、(]+)\(", h["branch"]))
            top5 = (h.get("chip") or {}).get("top5", [])
            fl = {x["id"]: x for x in top5 if x["name"] in flagged_names}
            b = pd.read_sql_query("select trade_date d,open,high,low,close,volume from stock_daily_bars where stock_id=? and source in ('twse_mi_index','tpex_daily') and trade_date in (?,?)",
                                  c, params=(sid, T, N)).drop_duplicates("d").set_index("d")
            g = pq[pq.stock_id == sid]
            if len(b) < 2 or not len(g) or not fl:
                continue
            tc, nb = b.loc[T], b.loc[N]
            vol_n = nb.volume / 1000.0
            gid = g.branch_id.astype(str)
            sells = float(g[gid.isin(fl)].sell_lots.sum())
            net_t = float(sum(x["net_lots"] for x in fl.values()))
            gt = pd.read_parquet(CH / T / "stock_top15.parquet") if (CH / T / "stock_top15.parquet").exists() else pd.DataFrame()
            gt = gt[gt.stock_id == sid]
            top_net_T = float((gt.buy_lots - gt.sell_lots).clip(lower=0).sum()) if len(gt) else 0.0
            others = g[~gid.isin(fl)]
            absorb = float((others.buy_lots - others.sell_lots).clip(lower=0).sum()) / vol_n
            r = {"T": T, "N": N, "method": k, "stock": sid, "name": h["name"], "chg_T": h.get("chg_pct"), "turnover_T": h.get("turnover_pct"),
                 "flagged": "、".join(x["name"] for x in fl.values()), "flagged_net_T": net_t, "flagged_sell_N": sells,
                 "flip_ratio": round(sells / net_t, 2) if net_t else None, "flag_of_buys_T_pct": round(net_t / top_net_T * 100, 1) if top_net_T else None, "sell_share_pct": round(sells / vol_n * 100, 1), "absorb_pct": round(absorb * 100, 1),
                 "gap_pct": round((nb.open / tc.close - 1) * 100, 2), "oc_pct": round((nb.close / nb.open - 1) * 100, 2), "cc_pct": round((nb.close / tc.close - 1) * 100, 2),
                 "high_pct": round((nb.high / tc.close - 1) * 100, 2), "low_pct": round((nb.low / tc.close - 1) * 100, 2)}
            r.update(intraday_feats(N, sid, float(tc.close)))
            r.update(warrant_flip(T, N, sid))
            rows.append(r)
    for h in inv["methods"].get("M10", {}).get("hits", []):
        sid = h["stock"]
        b = pd.read_sql_query("select trade_date d,open,high,low,close from stock_daily_bars where stock_id=? and source in ('twse_mi_index','tpex_daily') and trade_date in (?,?)",
                              c, params=(sid, T, N)).drop_duplicates("d").set_index("d")
        if len(b) < 2:
            continue
        tc, nb = b.loc[T], b.loc[N]
        r = {"T": T, "N": N, "method": "M10", "stock": sid, "name": h["name"], "chg_T": h.get("chg_pct"), "turnover_T": h.get("turnover_pct"), "flagged": h.get("branch") or "",
             "gap_pct": round((nb.open / tc.close - 1) * 100, 2), "oc_pct": round((nb.close / nb.open - 1) * 100, 2), "cc_pct": round((nb.close / tc.close - 1) * 100, 2),
             "high_pct": round((nb.high / tc.close - 1) * 100, 2), "low_pct": round((nb.low / tc.close - 1) * 100, 2), "dealer_r_pct": h.get("dealer_r_pct")}
        r.update(intraday_feats(N, sid, float(tc.close))); r.update(warrant_flip(T, N, sid))
        rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--all", action="store_true"); a = ap.parse_args()
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    files = sorted(INV.glob("inventory_20??-??-??.json")); files = files if a.all else files[-6:]
    allrows = []
    for f in files:
        allrows += process(c, f)
    if not allrows:
        print("尚無可追蹤的 (T, T+1) 配對（需 T+1 日線與分點頁入庫）"); return
    df = pd.DataFrame(allrows)
    pqp = INV / "m09_followup.parquet"
    if pqp.exists():
        old = pd.read_parquet(pqp)
        df = pd.concat([old[~old.set_index(["T", "method", "stock"]).index.isin(df.set_index(["T", "method", "stock"]).index)], df], ignore_index=True)
    df.to_parquet(pqp, index=False)
    for N, g in df.groupby("N"):
        (INV / f"m09_followup_{N}.json").write_text(json.dumps(g.to_dict("records"), ensure_ascii=False, default=float, indent=1), encoding="utf-8")
    last = df[df.N == df.N.max()]
    pd.set_option("display.width", 220)
    cols = ["method", "stock", "name", "flagged", "flip_ratio", "sell_share_pct", "absorb_pct", "gap_pct", "oc_pct", "cc_pct", "high_pct", "low_pct"] + [c for c in ("trial_gap_pct", "ret_1000_vs_open_pct", "lu_touched", "ld_touched", "lu_opens", "close_lu") if c in last]
    print(last[cols].to_string(index=False))
    m9 = df[df.method == "M09"].drop_duplicates(["T", "stock"])
    print(f"\n累積 M09 {len(m9)} 筆（{m9['T'].nunique()} 個訊號日）：")
    for name, x, y in (("gap vs cc", "gap_pct", "cc_pct"), ("sell_share vs oc", "sell_share_pct", "oc_pct"), ("absorb vs oc", "absorb_pct", "oc_pct"), ("flip_ratio vs oc", "flip_ratio", "oc_pct")):
        d = m9[[x, y]].dropna()
        if len(d) >= 6:
            print(f"  {name}: n={len(d)} Spearman {d.corr(method='spearman').iloc[0,1]:+.2f}")


if __name__ == "__main__":
    main()
