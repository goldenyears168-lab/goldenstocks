#!/usr/bin/env python3
"""大戶未實現損益 —— 累積帳（09:00 起同一本），每 5 分鐘 mark 一次。

只輸出未實現。已實現不看。
「每 5 分鐘重算」指的是**重新 mark**，不是把帳歸零 —— 帳一歸零就沒有部位，
也就沒有未實現可言。

未實現 = 淨部位(張) × 1000 × (該時點市價 − 加權平均成本)
距成本% = (市價 / 平均成本 − 1) × 100     ← 一個跳空就吃掉多少的尺度
"""
from __future__ import annotations
import argparse, json, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, "scripts/research")
from stock_db import DATA_DIR
from biglot_unrealized import load, book  # noqa: E402


def marks(step=5, start="09:05", end="13:30"):
    s = int(start[:2]) * 60 + int(start[3:]); e = int(end[:2]) * 60 + int(end[3:])
    out = []
    while s <= e:
        out.append(f"{s//60:02d}:{s%60:02d}")
        s += step
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-04")
    ap.add_argument("--thresh", type=float, default=5_000_000)
    ap.add_argument("--step", type=int, default=5)
    a = ap.parse_args()
    V3 = {x["sid"]: x["name"] for x in json.loads(
        (DATA_DIR / "cache/pit_universe_tick/_hivol_universe_v3.json").read_text())["universe"]}
    try:
        info = pd.read_pickle("/tmp/stock_info.pkl")
        NM = dict(zip(info.stock_id.astype(str), info.stock_name))
    except Exception:
        NM = {}
    name = lambda s: V3.get(s, NM.get(s, ""))

    df = load(a.date)
    big = df[(df.amt >= a.thresh) & (df.d != 0)]
    MK = marks(a.step) + ["收盤"]   # 收盤 = 含 13:30 集合競價那一筆
    rows = []
    for s, g in df.groupby("sym"):
        b = big[big.sym == s]
        if len(b) < 10:
            continue
        for mk in MK:
            cut = "13:31:00" if mk == "收盤" else mk + ":00"
            q = g[g.t < cut]
            if not len(q):
                continue
            P = float(q.px.iloc[-1])
            cum = b[b.t < cut]
            if not len(cum):
                continue
            _, _, pos, avg, _, _ = book(list(zip(cum.d, cum.sz, cum.px)))
            rows.append(dict(sym=s, mk=mk, pos=pos, avg=avg, px=P,
                             unreal=pos * 1000 * (P - avg) if pos else 0.0,
                             gap_pct=(P / avg - 1) * 100 if avg else np.nan))
    r = pd.DataFrame(rows)
    r["dir"] = np.where(r.pos > 0, "多", np.where(r.pos < 0, "空", "—"))
    r["u"] = r.unreal / 1e4

    print(f"=== {a.date} 大戶（單筆≥{a.thresh/1e4:.0f}萬）未實現損益 · 累積帳 · 每{a.step}分鐘 mark ===")
    print("\n[全體] 未實現總額走勢（萬元）")
    print(f"{'時點':<7}{'合計':>10}{'多方部位':>10}{'空方部位':>10}{'持多檔':>7}{'持空檔':>7}")
    for mk in MK:
        s_ = r[r.mk == mk]
        if s_.empty:
            continue
        L = s_[s_.pos > 0]; S = s_[s_.pos < 0]
        print(f"{mk:<7}{s_.u.sum():>10,.0f}{L.u.sum():>10,.0f}{S.u.sum():>10,.0f}"
              f"{len(L):>7}{len(S):>7}")

    first = [m for m in MK if m <= "10:00"]
    p = r[r.mk.isin(first)].pivot_table(index="sym", columns="mk", values="u")[first]
    p["name"] = [name(i) for i in p.index]
    p["v3"] = ["★" if i in V3 else "" for i in p.index]
    t = p.sort_values("10:00", ascending=False)
    pd.set_option("display.width", 250)
    print(f"\n[第一小時] 逐檔未實現（萬元）· 前 8 / 後 6")
    print(pd.concat([t.head(8), t.tail(6)])[["v3", "name"] + first]
          .to_string(float_format=lambda x: f"{x:,.0f}"))

    last = r[r.mk == MK[-1]].set_index("sym")
    last = last.assign(name=[name(i) for i in last.index],
                       v3=["★" if i in V3 else "" for i in last.index]).sort_values("u", ascending=False)
    print(f"\n[收盤 {MK[-1]}] 未實現排行")
    print(f"{'':<3}{'代號':<7}{'名稱':<9}{'方向':<5}{'淨部位(張)':>12}{'平均成本':>10}"
          f"{'市價':>9}{'距成本%':>9}{'未實現(萬)':>12}")
    for sym, x in pd.concat([last.head(10), last.tail(6)]).iterrows():
        print(f"{x.v3:<3}{sym:<7}{x['name']:<9}{x['dir']:<5}{x.pos:>12,.0f}{x.avg:>10,.1f}"
              f"{x.px:>9,.1f}{x.gap_pct:>9.2f}{x.u:>12,.0f}")
    print(f"\n合計未實現 {last.u.sum():+,.0f} 萬（多方 {last[last.pos>0].u.sum():+,.0f} / "
          f"空方 {last[last.pos<0].u.sum():+,.0f}）")
    print(f"距成本 |%| 中位 {last.gap_pct.abs().median():.2f}%")
    r.to_pickle(f"/tmp/unreal5_{a.date}.pkl")


if __name__ == "__main__":
    main()
