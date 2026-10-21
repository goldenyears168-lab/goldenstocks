#!/usr/bin/env python3
"""小哥盤點 × 盤中快照：用富邦 REST 全市場快照（TSE+OTC 各 1 次呼叫，唯讀，用完即登出）
對照「前一交易日盤點命中股」今天到目前的表現。一次性、不訂閱 ws、不下單。
輸出：${DATA}/chip/xiaoge_inventory/livecheck_<今日>_<HHMM>.json，並印出各戰法 vs 母體的對照。
"""
import json, sys, time
from datetime import datetime
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR  # noqa: E402
from order.fubon_session import connect_fubon, safe_logout  # noqa: E402

INV = DATA_DIR / "chip" / "xiaoge_inventory"
BEARISH = {"M04", "M07", "M08"}


def main():
    inv_path = sorted(INV.glob("inventory_20??-??-??.json"))[-1]
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    sess = connect_fubon(realtime=True)
    quotes = []
    try:
        rest = sess.sdk.marketdata.rest_client.stock
        for market in ("TSE", "OTC"):
            resp = rest.snapshot.quotes(market=market)
            data = resp.get("data", resp) if isinstance(resp, dict) else resp
            quotes.extend(data or [])
            print(f"snapshot {market}: {len(data or [])} 檔", flush=True)
            time.sleep(1.5)
    finally:
        print("logout:", safe_logout(sess), flush=True)
    q = {str(x.get("symbol")): x for x in quotes}
    if "--sample" in sys.argv:
        print(json.dumps(next(iter(q.values())), ensure_ascii=False)); return
    def perf(sid):
        x = q.get(sid)
        if not x: return None
        last = x.get("closePrice") or x.get("lastPrice"); op = x.get("openPrice"); ch = x.get("changePercent")
        if last is None or ch is None: return None
        prev = last / (1 + ch / 100) if ch > -100 else None
        return {"chg": float(ch), "gap": (op / prev - 1) * 100 if (op and prev) else None, "vs_open": (last / op - 1) * 100 if op else None, "vol": x.get("tradeVolume")}
    allmem = {}
    for k, m in inv["methods"].items():
        for h in m["hits"]:
            allmem.setdefault(h["stock"], []).append(k)
    # 母體＝盤點母體（流動性）：以 M02/M04/M08 以外全市場快照中盤點母體無法還原，改用全體 4 碼股快照的中位/均值
    univ = [perf(s) for s in q if len(s) == 4 and s.isdigit()]
    univ = [p for p in univ if p]
    base = {k: float(np.mean([p[k] for p in univ if p[k] is not None])) for k in ("chg", "gap", "vs_open")}
    base_med = float(np.median([p["chg"] for p in univ]))
    res = {"asof": datetime.now().isoformat(timespec="seconds"), "inventory_date": inv["date"], "base": base, "base_median_chg": base_med, "n_universe": len(univ), "methods": {}}
    print(f"\n盤點日 {inv['date']} → 現在 {res['asof']}｜全市場4碼股 {len(univ)} 檔：平均漲跌 {base['chg']:+.2f}% 中位 {base_med:+.2f}% 平均跳空 {base['gap']:+.2f}%\n")
    print(f"{'戰法':<26}{'n':>4}{'平均漲跌%':>10}{'中位%':>8}{'上漲占比':>8}{'跳空%':>8}{'開盤後%':>8}{'超額%':>8}{'SE':>7}")
    for k, m in inv["methods"].items():
        ps = [perf(h["stock"]) for h in m["hits"]]; ps = [p for p in ps if p]
        if not ps: print(f"{k+' '+m['name']:<26}{0:>4}"); continue
        chg = np.array([p["chg"] for p in ps]); gap = np.array([p["gap"] for p in ps if p["gap"] is not None]); vo = np.array([p["vs_open"] for p in ps if p["vs_open"] is not None])
        se = float(chg.std(ddof=1) / np.sqrt(len(chg))) if len(chg) > 1 else float("nan")
        res["methods"][k] = {"n": len(ps), "mean": float(chg.mean()), "median": float(np.median(chg)), "up_share": float((chg > 0).mean()), "gap": float(gap.mean()), "vs_open": float(vo.mean()), "excess": float(chg.mean() - base["chg"]), "se": se,
                             "stocks": [{"stock": h["stock"], "name": h["name"], **perf(h["stock"])} for h in m["hits"] if perf(h["stock"])][:60]}
        print(f"{k+' '+m['name']:<26}{len(ps):>4}{chg.mean():>+10.2f}{np.median(chg):>+8.2f}{(chg>0).mean():>8.0%}{gap.mean():>+8.2f}{vo.mean():>+8.2f}{chg.mean()-base['chg']:>+8.2f}{se:>7.2f}")
    out = INV / f"livecheck_{datetime.now():%Y-%m-%d_%H%M}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"); print("\nsaved", out)


if __name__ == "__main__":
    main()
