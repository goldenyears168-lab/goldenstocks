#!/usr/bin/env python3
"""M09 歷史回放（唯讀）：用已抓的 chip/xiaoge/<日>/stock_top15.parquet 把 M09 在「更早的日子」重放一遍，對每個 T 算 T+1 實際發生什麼，
並用預註冊假說 H1–H6 計分。定位＝經驗樣本（假說由 10/20 觀察形成，這些日子早於形成日，屬歷史偽樣本外；不是前瞻 OOS）。
PIT：T 日名單只用 date ≤ T 的資料；habit＝該股該分點過去（d1 ≤ T）前5大買超(≥3%量)→隔日賣≥70% 的次數 ≥1。
選擇偏誤防護：只用 turnover20 非空的列（= 當日流動性前 N 名的 PIT 母體）；--burst 回補的列（turnover20 為空）排除，因為它們依 10/20 的結果挑股。
輸出：${DATA}/chip/xiaoge_inventory/m09_replay.parquet；印假說計分與分組。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts/ops"))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402
import xiaoge_validation_journal as VJ  # noqa: E402

CH = DATA_DIR / "chip" / "xiaoge"
MINSH, ALPHA, MIN_HIST = 0.03, 0.7, 15


def main():
    days = sorted(p.name for p in CH.iterdir() if (p / "stock_top15.parquet").exists())
    frames = {}
    for d in days:
        f = pd.read_parquet(CH / d / "stock_top15.parquet")
        f = f[f.turnover20.notna()]                       # PIT 母體（排除 burst 回補列）
        frames[d] = f.assign(branch_id=f.branch_id.astype(str))
    days = [d for d in days if len(frames[d])]
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    bars = pd.read_sql_query("select stock_id, trade_date d, open, high, low, close, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') "
                             "and trade_date>=? and length(stock_id)=4 and stock_id glob '[1-9][0-9][0-9][0-9]'", c, params=(days[0],)).drop_duplicates(["stock_id", "d"])
    bars = bars.sort_values(["stock_id", "d"]); bars["ret"] = bars.groupby("stock_id").close.pct_change()
    bars["tv20"] = bars.groupby("stock_id").apply(lambda g: (g.close * g.volume).shift(1).rolling(20).mean()).reset_index(level=0, drop=True)
    B = bars.set_index(["stock_id", "d"])
    sh = dict(c.execute("select stock_id,shares_issued from stock_shareholding_daily where trade_date=(select max(trade_date) from stock_shareholding_daily)").fetchall())
    # (stock, day) → {branch: (buy, sell, name)}
    D = {}
    for d in days:
        for r in frames[d].itertuples():
            D.setdefault((r.stock_id, d), {})[r.branch_id] = (r.buy_lots, r.sell_lots, r.branch)
    vol = {k: v / 1000.0 for k, v in bars.set_index(["stock_id", "d"]).volume.items()}

    def top5(sid, d):
        v, m = vol.get((sid, d)), D.get((sid, d))
        if not v or not m:
            return []
        xs = sorted(((b, x[0] - x[1], x[2]) for b, x in m.items()), key=lambda z: -z[1])[:5]
        return [(b, n, nm) for b, n, nm in xs if n > 0 and n >= MINSH * v]

    rows = []
    for ti in range(MIN_HIST, len(days) - 1):
        T, N = days[ti], days[ti + 1]
        for sid in {s for s, d in D if d == T}:
            if (sid, T) not in B.index or (sid, N) not in B.index:
                continue
            tb = B.loc[(sid, T)]
            if not (tb.ret >= 0.05) or not (tb.tv20 >= 3.0e7):
                continue
            nxt = D.get((sid, N))
            if not nxt:
                continue
            fl = []
            for b, n, nm in top5(sid, T):
                ev = flips = 0
                for i in range(ti):                    # 事件日 d0=days[i]，賣出日 d1=days[i+1] ≤ T
                    d0, d1 = days[i], days[i + 1]
                    hit = [x for x in top5(sid, d0) if x[0] == b]
                    if not hit:
                        continue
                    ev += 1
                    s1 = D.get((sid, d1), {}).get(b)
                    if s1 and s1[1] >= ALPHA * hit[0][1]:
                        flips += 1
                if flips >= 1:
                    fl.append((b, n, nm, flips, ev))
            if not fl:
                continue
            nb = B.loc[(sid, N)]
            net_t = float(sum(x[1] for x in fl))
            sells = float(sum(nxt[x[0]][1] for x in fl if x[0] in nxt))
            allT = D[(sid, T)]; top_net_T = float(sum(max(v[0] - v[1], 0) for v in allT.values()))
            vol_n = vol[(sid, N)]
            others = sum(max(v[0] - v[1], 0) for k, v in nxt.items() if k not in {x[0] for x in fl})
            rows.append({"T": T, "N": N, "method": "M09", "stock": sid, "name": "", "chg_T": tb.ret * 100, "turnover_T": (tb.volume / sh[sid] * 100) if sid in sh else np.nan,
                         "flagged": "、".join(x[2] for x in fl), "flagged_net_T": net_t, "flagged_sell_N": sells, "flip_ratio": sells / net_t if net_t else np.nan,
                         "flag_of_buys_T_pct": net_t / top_net_T * 100 if top_net_T else np.nan, "sell_share_pct": sells / vol_n * 100, "absorb_pct": others / vol_n * 100,
                         "gap_pct": (nb.open / tb.close - 1) * 100, "oc_pct": (nb.close / nb.open - 1) * 100, "cc_pct": (nb.close / tb.close - 1) * 100,
                         "high_pct": (nb.high / tb.close - 1) * 100, "low_pct": (nb.low / tb.close - 1) * 100, "strong": max(x[3] for x in fl) >= 2})
    df = pd.DataFrame(rows)
    out = DATA_DIR / "chip" / "xiaoge_inventory" / "m09_replay.parquet"
    df.to_parquet(out, index=False)
    print(f"回放 {df['T'].nunique()} 個訊號日（{df['T'].min()}～{df['T'].max()}），M09 命中 {len(df)} 筆；母體=PIT 流動性前N；母體日數 {len(days)}")
    ev = VJ.evaluate(df)
    for k, t in VJ.HYP.items():
        r = ev[k]
        print(k, {a: (round(b, 3) if isinstance(b, float) else b) for a, b in r.items()}, "|", t[:40])
    print("\n全體 T+1：gap %.2f%% oc %.2f%% cc %.2f%% | 上漲占比 oc<0: %.0f%%" % (df.gap_pct.mean(), df.oc_pct.mean(), df.cc_pct.mean(), (df.oc_pct < 0).mean() * 100))
    for lo, hi, nm in ((-99, 2, "跳空<2%"), (2, 4, "跳空2~4%"), (4, 6, "跳空4~6%"), (6, 99, "跳空≥6%")):
        g = df[(df.gap_pct >= lo) & (df.gap_pct < hi)]
        if len(g): print(f"  {nm:<9} n={len(g):<4} oc {g.oc_pct.mean():+.2f}% cc {g.cc_pct.mean():+.2f}% oc<0占比 {(g.oc_pct<0).mean():.0%}")
    for nm, g in (("昨日漲停", df[df.chg_T >= 9.5]), ("未漲停", df[df.chg_T < 9.5]), ("強(≥2次)", df[df.strong]), ("弱(1次)", df[~df.strong])):
        if len(g): print(f"  {nm:<8} n={len(g):<4} gap {g.gap_pct.mean():+.2f}% oc {g.oc_pct.mean():+.2f}% cc {g.cc_pct.mean():+.2f}%")


if __name__ == "__main__":
    main()
