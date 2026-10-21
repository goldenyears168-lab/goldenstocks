#!/usr/bin/env python3
"""小哥筆記本 × 盤中快照（一次性，唯讀，2 次 REST 後登出）：筆記本名單（空方線索/多方線索/雷達）今天到目前的表現。"""
import json, sys, time
from datetime import datetime
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR  # noqa: E402
from order.fubon_session import connect_fubon, safe_logout  # noqa: E402

NB = DATA_DIR / "chip" / "xiaoge_notebook"


def main():
    nb = json.loads(sorted(NB.glob("notebook_20??-??-??.json"))[-1].read_text(encoding="utf-8"))
    sess = connect_fubon(realtime=True); quotes = []
    try:
        rest = sess.sdk.marketdata.rest_client.stock
        for m in ("TSE", "OTC"):
            r = rest.snapshot.quotes(market=m); quotes.extend((r.get("data", r) if isinstance(r, dict) else r) or []); time.sleep(1.5)
    finally:
        print("logout:", safe_logout(sess))
    q = {str(x["symbol"]): x for x in quotes}
    def perf(s):
        x = q.get(s)
        if not x or x.get("changePercent") is None or not (x.get("closePrice") or x.get("lastPrice")): return None
        last = x.get("closePrice") or x.get("lastPrice"); op = x.get("openPrice"); ch = x["changePercent"]; prev = last / (1 + ch / 100)
        return {"chg": ch, "gap": (op / prev - 1) * 100 if op else None, "vo": (last / op - 1) * 100 if op else None}
    u = [perf(s) for s in q if len(s) == 4 and s.isdigit()]; u = [p for p in u if p]
    bm = np.mean([p["chg"] for p in u]); bg = np.mean([p["gap"] for p in u if p["gap"] is not None])
    print(f"筆記本日 {nb['date']}｜全市場 {len(u)} 檔 平均 {bm:+.2f}% 跳空 {bg:+.2f}%（{datetime.now():%H:%M}）")
    def show(name, rows):
        ps = [(r, perf(r["stock"])) for r in rows]; ps = [(r, p) for r, p in ps if p]
        if not ps: print(f"{name:<22} n=0"); return
        c = np.array([p["chg"] for _, p in ps]); g = np.array([p["gap"] for _, p in ps if p["gap"] is not None]); v = np.array([p["vo"] for _, p in ps if p["vo"] is not None])
        se = c.std(ddof=1) / np.sqrt(len(c)) if len(c) > 1 else float("nan")
        print(f"{name:<22} n={len(ps):<4} 漲跌 {c.mean():+.2f}% 上漲 {(c>0).mean():.0%} 跳空 {g.mean():+.2f}% 開盤後 {v.mean():+.2f}% 超額 {c.mean()-bm:+.2f}% SE {se:.2f}")
    show("空方線索(shorts)", nb.get("shorts", [])); show("多方線索(longs)", nb.get("longs", []))
    rad = nb.get("radar", [])
    show("雷達：集中度>0(買超)", [r for r in rad if r["conc_pct"] > 0]); show("雷達：集中度<0(賣超)", [r for r in rad if r["conc_pct"] < 0])
    for r in nb.get("shorts", []) + nb.get("longs", []):
        p = perf(r["stock"]); print("  ", r["stock"], r["name"], f"{p['chg']:+.2f}% 跳空{p['gap']:+.2f}% 開盤後{p['vo']:+.2f}%" if p else "—")
    print("昨日覆盤清單：", [(x["stock"], x["side"]) for x in nb.get("review", [])][:10])


main()
