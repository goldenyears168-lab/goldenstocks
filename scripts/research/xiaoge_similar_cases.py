#!/usr/bin/env python3
"""找與「台郡 6269（10/20）」相似的訊號：訊號日大漲、買超前5有分點在『該股』過去有 買進隔日賣出 紀錄。唯讀，用已抓的 chip/xiaoge/<日>/stock_top15.parquet。"""
import json, sqlite3, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402
CH = DATA_DIR / "chip" / "xiaoge"
T = sys.argv[1] if len(sys.argv) > 1 else "2026-10-20"
days = sorted(p.name for p in CH.iterdir() if (p / "stock_top15.parquet").exists() and p.name <= T)
frames = {d: pd.read_parquet(CH / d / "stock_top15.parquet") for d in days[-60:]}
days = sorted(frames)
c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
bars = pd.read_sql_query("select stock_id, trade_date d, open, close, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date between ? and ? and length(stock_id)=4", c, params=(days[0], T)).drop_duplicates(["stock_id", "d"])
vol = {(r.stock_id, r.d): r.volume / 1000 for r in bars.itertuples()}
bars = bars.sort_values(["stock_id", "d"]); bars["ret"] = bars.groupby("stock_id").close.pct_change()
ret = {(r.stock_id, r.d): r.ret for r in bars.itertuples()}
named = json.loads((ROOT / "config/xiaoge_named_branches.json").read_text(encoding="utf-8"))
role = {i: e["role"] for e in named["branches"] for i in e["ids"]}
# (stock, day) -> {branch: (buy, sell, name)}
D = {}
for d, f in frames.items():
    for r in f.itertuples():
        D.setdefault((r.stock_id, d), {})[str(r.branch_id)] = (r.buy_lots, r.sell_lots, r.branch)
def top5(sid, d, minsh=0.03):
    v = vol.get((sid, d)); m = D.get((sid, d))
    if not v or not m: return []
    xs = sorted(((b, x[0] - x[1], x[2]) for b, x in m.items()), key=lambda z: -z[1])[:5]
    return [(b, n, nm) for b, n, nm in xs if n > 0 and n >= minsh * v]
out = []
for sid in sorted({s for s, d in D if d == T}):
    rt = ret.get((sid, T))
    if rt is None: continue
    tb = top5(sid, T)
    if not tb: continue
    info = []
    for b, n, nm in tb:
        ev = fl = 0; ex = []
        for i in range(len(days) - 1):
            d0, d1 = days[i], days[i + 1]
            if d1 > T or d0 >= T: break
            hit = [x for x in top5(sid, d0) if x[0] == b]
            if not hit: continue
            ev += 1
            nxt = D.get((sid, d1), {}).get(b)
            if nxt and nxt[1] >= 0.7 * hit[0][1]:
                fl += 1; ex.append(f"{d0[5:]}買{hit[0][1]}→{d1[5:]}賣{nxt[1]}")
        info.append({"id": b, "name": nm, "role": role.get(b), "net": int(n), "events": ev, "flips": fl, "ex": ex})
    out.append({"stock": sid, "ret": rt, "top5": info})
tw = [o for o in out if o["stock"] == "6269"]
print("台郡：", json.dumps(tw, ensure_ascii=False)[:1500])
print("\n相似案例（T=%s 漲幅≥5%%，買超前5有≥1個『該股』曾買進隔日賣出）：" % T)
names = dict(sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True).execute("select stock_id,name from stock_beta").fetchall())
rows = []
for o in out:
    hab = [x for x in o["top5"] if x["flips"] >= 1]
    named_fl = [x for x in o["top5"] if x["role"] == "隔日沖"]
    if o["ret"] >= 0.05 and hab:
        rows.append((len(hab), len(named_fl), o))
for nh, nn, o in sorted(rows, key=lambda r: (-r[0], -r[1], -r[2]["ret"])):
    print(f"{o['stock']} {names.get(o['stock'],'')} 漲{o['ret']*100:+.1f}% 慣犯分點{nh}個 具名隔日沖{nn}個 | " + "；".join(f"{x['name']}{x['net']}張[{x['flips']}/{x['events']}]" + (f"({'、'.join(x['ex'][:2])})" if x['flips'] else "") for x in o["top5"][:5]))
print("\n（只含該股前15名資料；歷史天數", len(days), "天，", days[0], "起；慣犯判讀=曾在該股前5大買超(≥3%量)隔日賣出≥70%淨買）")
