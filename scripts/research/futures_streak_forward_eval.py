#!/usr/bin/env python3
"""期貨連次前向記錄的描述性評估（大台 TXF／台積電期貨 CDF、QFF；2026-10-21）。唯讀、不寫檔、不屬於任何預先登記檢定。

資料：`futflow_view.Book` 讀 `cache/{root}_trades` 全部逐筆（與頁面同一條管線），取凍結版 A 與補 tick rule 影子版 B 的亮燈事件
（連次剛達 10），報酬＝反向做（紅串後做空、綠串後做多）、持有 5／10／15 分鐘、以該分鐘收盤價計（毛報酬 bps，未扣成本）。
t 值以「盤別」為群集單位。台積電期貨沒有預先登記：樣本不足前只看、不下結論；大台的正式判定看 futures_streak_prereg_eval.py。
用法: PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/futures_streak_forward_eval.py [CDF QFF TXF ...]
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from biglot import futflow_view as F  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
HZ = (5, 10, 15)


def events(book, ver):
    w = book.wstreak if ver == "A" else book.wstreak_fill
    out = []
    for (stype, T), st in w.snapshot().items():
        bars = book.sessions.get((stype, T)) or {}
        for end, d, n in st["lits"]:
            tm = dt.datetime.fromtimestamp(end, TZ)
            mk = tm.hour * 60 + tm.minute + (1440 if (stype == "night" and tm.hour * 60 + tm.minute < 480) else 0)
            b0 = bars.get(mk)
            if not b0:
                continue
            r = {"stype": stype, "T": T, "d": d, "mk": mk}
            for h in HZ:
                b1 = bars.get(mk + h)
                r[h] = (-d * (b1["c"] / b0["c"] - 1) * 1e4) if b1 else None
            out.append(r)
    return out


def summarize(rows, label):
    line = [f"  {label:<14} n={len(rows):3d}"]
    for h in HZ:
        by = {}
        for r in rows:
            if r[h] is not None:
                by.setdefault((r["stype"], r["T"]), []).append(r[h])
        xs = [v for vs in by.values() for v in vs]
        if len(by) >= 3 and len(xs) >= 3:
            dm = [statistics.mean(v) for v in by.values()]
            sd = statistics.stdev(dm)
            t = f"t{statistics.mean(dm) / (sd / math.sqrt(len(dm))):+.1f}" if sd > 0 else "t-"
        else:
            t = "t-"
        line.append(f"{h}分 {statistics.mean(xs):+6.1f}bps({t},n={len(xs)})" if xs else f"{h}分 —")
    print("  ".join(line))


def main() -> None:
    roots = [r.upper() for r in sys.argv[1:]] or list(F.STREAK_BOOKS)
    for root in roots:
        b = F.Book(root)
        b.read(F._files(root))
        if b.wstreak is None:
            print(root, "沒有連次（不在 STREAK_BOOKS）")
            continue
        print(f"== {root} {F.FUT_NAME.get(root, '')} · 盤別 {len(b.sessions)}")
        for ver, lab in (("A", "凍結版"), ("B", "補tick rule版")):
            ev = events(b, ver)
            summarize(ev, lab + "·全部")
            summarize([e for e in ev if e["stype"] == "day"], lab + "·日盤")
            summarize([e for e in ev if e["stype"] == "night"], lab + "·夜盤")


if __name__ == "__main__":
    main()
