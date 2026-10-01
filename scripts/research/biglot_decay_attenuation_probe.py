"""大戶佔比 2026-09 失效：衰減機制診斷（2026-10-01）。

前提：`biglot_pit_tick_quality_audit.py` 已排除資料污染（覆蓋/TickType/快照閘/時間
覆蓋全期一致）。本腳本測「衰減是不是量能萎縮造成的量測誤差（attenuation）」。

假說：單筆≥1000萬的大戶成交筆數隨市場量能萎縮而減少 → bigpct 對「真實大戶意圖」的
量測誤差變大 → 迴歸係數被衰減、IC 下降。若成立，訊號沒死，是**訊號雜訊比隨量能循環**，
量能回來就會回來；若不成立（大戶參與度持平而 IC 仍崩），才是真的機制失效。

輸出三組：
  ① 逐月大戶毛額占比／大戶筆數（從 raw tick 重算，不靠面板）
  ② 逐月 bigpct 的日間秩自相關（大戶行為是否變隨機）
  ③ ①②與逐月 IC 併表
"""
import collections
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
SCR = Path.home() / "goldenstocks-data/scratch"
OPEN_T, CLOSE_T = 9 * 3600, 13 * 3600 + 1800
BIG = 1e7


def tsec(s: str) -> float:
    h, m, r = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(r)


def scan():
    rows = []
    files = sorted(p for p in CACHE.glob("*.json") if not p.name.startswith("_"))
    print(f"掃描 {len(files):,} 檔", flush=True)
    for i, f in enumerate(files):
        if i % 2000 == 0:
            print(f"  {i:,}", flush=True)
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        sess = [t for t in d if OPEN_T <= tsec(t["Time"]) < CLOSE_T]
        if len(sess) < 200:
            continue
        sess.sort(key=lambda x: x["Time"])
        ts = [tsec(t["Time"]) for t in sess]
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        mg = sum(gaps) / len(gaps) if gaps else 0
        if mg > 0:
            sd = math.sqrt(sum((g - mg) ** 2 for g in gaps) / len(gaps))
            if sd / mg < 0.5:
                continue
        tot = nbig = bigamt = 0.0
        for t in sess:
            a = t["deal_price"] * t["volume"] * 1000
            tot += a
            if a >= BIG:
                nbig += 1
                bigamt += a
        sid, date = f.stem.split("_")
        rows.append(dict(sid=sid, date=date, tot_amt=tot, big_amt=bigamt,
                         n_big=nbig, n_tick=len(sess)))
    return pd.DataFrame(rows)


def main():
    cache = SCR / "biglot_bigshare_by_day.csv"
    if cache.exists():
        df = pd.read_csv(cache, dtype={"sid": str})
        print(f"[快取] {cache}")
    else:
        df = scan()
        df.to_csv(cache, index=False)
        print(f"[寫出] {cache}")
    df["ym"] = df.date.str[:7]
    df["big_share"] = df.big_amt / df.tot_amt * 100

    print("\n① 逐月大戶（單筆≥1000萬）參與度 —— 從 raw tick 重算")
    print("月       股日   大戶毛額占比%  大戶筆數/股日  總筆數/股日  成交額中位(億)")
    g = df.groupby("ym")
    m1 = pd.DataFrame({
        "n": g.size(),
        "big_share": g.big_share.median(),
        "n_big": g.n_big.median(),
        "n_tick": g.n_tick.median(),
        "amt": g.tot_amt.median() / 1e8,
    })
    for ym, r in m1.iterrows():
        print(f"{ym} {r.n:>6.0f} {r.big_share:>13.2f} {r.n_big:>14.0f} "
              f"{r.n_tick:>12.0f} {r.amt:>14.1f}")

    # ② bigpct 日間秩自相關
    b = pd.read_csv(SCR / "biglot_panels/pit100_bucket5_panel_2026-03_09.csv",
                    dtype={"sid": str},
                    usecols=["sid", "date", "big_net", "tot_amt"])
    b = b.groupby(["sid", "date"], as_index=False).sum(numeric_only=True)
    b["bigpct"] = b.big_net / b.tot_amt * 100
    b = b.sort_values(["sid", "date"])
    b["bigpct_prev"] = b.groupby("sid").bigpct.shift(1)
    b["ym"] = b.date.str[:7]
    print("\n② bigpct 日間秩自相關（大戶行為是否變隨機）")
    ac = b.dropna(subset=["bigpct_prev"]).groupby("ym").apply(
        lambda x: x.groupby("date").apply(
            lambda y: y.bigpct.corr(y.bigpct_prev, method="spearman"),
            include_groups=False).mean(), include_groups=False)
    for ym, v in ac.items():
        print(f"  {ym}  rho={v:+.4f}")

    print("\n③ 併表（逐月 IC 來自 biglot_bigpct_robustness 第 ③ 節）")
    IC = {"2026-03": 0.1661, "2026-04": 0.1794, "2026-05": 0.1374, "2026-06": 0.1458,
          "2026-07": 0.1333, "2026-08": 0.0707, "2026-09": 0.0137}
    print("月       IC      大戶毛額占比%  大戶筆數/股日  秩自相關")
    for ym in sorted(IC):
        if ym in m1.index:
            r = m1.loc[ym]
            print(f"{ym} {IC[ym]:+.4f} {r.big_share:>13.2f} {r.n_big:>14.0f} "
                  f"{ac.get(ym, float('nan')):>10.4f}")
    sub = m1.loc[[k for k in sorted(IC) if k in m1.index]]
    icv = np.array([IC[k] for k in sub.index])
    for col in ("big_share", "n_big", "amt"):
        print(f"  corr(逐月IC, {col}) = {np.corrcoef(icv, sub[col].to_numpy())[0,1]:+.3f}  (n=7 月)")


if __name__ == "__main__":
    main()
