#!/usr/bin/env python3
"""大台日盤『可判定方向的主動成交口數』日合計 → cache/futflow_ref30_TXF.json（竭盡 v2 的 RVOL 基準：過去 ≤10 日盤平均 ÷ 600 = 每 30 秒平均口數）。
與 /futflow 共用 src/futures_flow.py（TradeStream、BurstMerger、WindowStreakV2）；頁面重啟時讀此檔，當日合計由頁面隨收盤補寫。唯讀 tick 快取，只寫這個小 JSON。
"""
import glob, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]
import futures_flow as FF  # noqa: E402
from stock_db import DATA_DIR  # noqa: E402

CACHE = DATA_DIR.parent / "cache"

def main(root="TXF"):
    files = sorted(glob.glob(str(CACHE / f"{root.lower()}_trades" / f"{root.lower()}_trades_*.jsonl")))
    stream = FF.TradeStream(); ws = FF.WindowStreakV2()
    merger = FF.BurstMerger(FF.PV[root], lambda b: ws.add(b) if (b.stype == "day" and b.trade_time is not None) else None, streaming=True)
    n = 0
    for f in files:
        fd = os.path.basename(f).split("_")[-1][:10]
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                t = stream.feed(line, fd, f)
                if t is None: continue
                if t.dv > 0: merger.add(t)
                n += 1
                if n % 2000 == 0: merger.flush_old()
    merger.flush_all(); ws.flush_all()
    tot = {T: round(v, 1) for T, v in sorted(ws.day_tot.items())}
    out = CACHE / f"futflow_ref30_{root}.json"
    tmp = out.with_suffix(".tmp"); tmp.write_text(json.dumps(tot, ensure_ascii=False, indent=0), encoding="utf-8"); tmp.replace(out)
    print(root, len(tot), "個日盤", list(tot.items())[-3:], "→", out)

if __name__ == "__main__":
    main(*(sys.argv[1:2] or ["TXF"]))
