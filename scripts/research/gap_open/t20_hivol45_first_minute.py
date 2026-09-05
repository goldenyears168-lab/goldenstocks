#!/usr/bin/env python3
"""高波動 45 檔（個股期貨可交易池）開盤第一分鐘：跳空延續 vs 回檔。

兩層資料：
  T2  stock_kbar_1m source='finmind'（34/45 檔，2022-02~2026-08）—— 開盤單一價 → 09:01
  T3  pit_universe_tick 逐筆（24/45 檔，2026-03~2026-09）—— 秒級，可測「不同進場秒數」
統計一律以 **交易日** 為 cluster（45 檔九成是半導體/電子，同日高度相關，
不 cluster 會把 t 值灌大數倍）。
"""
from __future__ import annotations
import json, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, "src")
from stock_db import DATA_DIR

OUT = os.path.expanduser("~/goldenstocks-data/data/cache/gap_open")
uni = json.loads((DATA_DIR / "cache/pit_universe_tick/_live_calib.json").read_text())["universe"]
SIDS = [str(r["sid"]) for r in uni]
NAME = {str(r["sid"]): r["name"] for r in uni}
TICKBPS = {str(r["sid"]): r["tick_bps"] for r in uni}

BKT = [-0.105, -0.05, -0.03, -0.02, -0.01, -0.0025, 0.0025, 0.01, 0.02, 0.03, 0.05, 0.105]
LAB = ["<-5%", "-5~-3%", "-3~-2%", "-2~-1%", "-1~-0.25%", "±0.25%",
       "+0.25~1%", "+1~2%", "+2~3%", "+3~5%", ">+5%"]


def cl_t(df, val, by="date"):
    """日內先平均再對日序列做 t 檢定（cluster by date）。回傳 mean(bps), t, n_day, n_obs"""
    if len(df) == 0:
        return np.nan, np.nan, 0, 0
    g = df.groupby(by)[val].mean()
    if len(g) < 3:
        return float(df[val].mean() * 1e4), np.nan, len(g), len(df)
    t = g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))
    return float(g.mean() * 1e4), float(t), len(g), len(df)


def bucket_table(df, val, title, extra=None):
    print(f"\n### {title}")
    hdr = f"{'gap 桶':<11}{'n股日':>7}{'n日':>5}{'平均':>9}{'t':>7}{'延續率':>8}{'中位':>8}"
    print(hdr); print("-" * len(hdr))
    for lab in LAB:
        s = df[df.lab == lab]
        m, t, nd, no = cl_t(s, val)
        if no == 0:
            continue
        cont = np.nan if lab == "±0.25%" else float((np.sign(s[val]) == np.sign(s.gap)).mean())
        med = float(s[val].median() * 1e4)
        print(f"{lab:<11}{no:>7}{nd:>5}{m:>8.1f}{'' if np.isnan(t) else f'{t:>7.2f}'}"
              f"{'' if np.isnan(cont) else f'{cont*100:>7.1f}%'}{med:>8.1f}")


def signed_table(df, val, title, base_bps):
    """扣掉零跳空基準漂移後，換算成『跳空方向』的超額（正=延續、負=回檔）"""
    print(f"\n### {title}（已扣零跳空基準 {base_bps:+.1f} bps；正=延續 負=回檔）")
    hdr = f"{'gap 桶':<11}{'n股日':>7}{'超額bps':>9}{'t':>7}"
    print(hdr); print("-" * len(hdr))
    for lab in LAB:
        if lab == "±0.25%":
            continue
        s = df[df.lab == lab].copy()
        if len(s) == 0:
            continue
        s["sx"] = np.sign(s.gap) * (s[val] - base_bps / 1e4)
        m, t, nd, no = cl_t(s, "sx")
        print(f"{lab:<11}{no:>7}{m:>9.1f}{'' if np.isnan(t) else f'{t:>7.2f}'}")


# ---------------------------------------------------------------- T2 1 分鐘
def tier2():
    meta = pd.read_pickle(f"{OUT}/min_meta.pkl")
    close = np.load(f"{OUT}/min_close.npy")
    sid = meta.index.get_level_values(0).astype(str)
    date = meta.index.get_level_values(1)
    df = pd.DataFrame({
        "sid": sid, "date": date, "base": meta.base.values, "open0": meta.open0.values,
        "prev_close": meta.prev_close.values, "adv20": meta.adv20.values,
        "atr20": meta.atr20.values, "d_close": meta.d_close.values,
        "c1": close[:, 0], "c2": close[:, 1], "c5": close[:, 4], "c15": close[:, 14],
    })
    df = df[df.sid.isin(SIDS)]
    df["gap"] = df.open0 / df.base - 1
    df = df[(df.gap.abs() <= 0.105) & (df.prev_close >= 10) & (df.adv20 >= 5e7)]
    df = df.dropna(subset=["c1", "open0"])
    df["m1"] = df.c1 / df.open0 - 1            # 開盤單一價 → 09:01
    df["m1_2"] = df.c2 / df.c1 - 1             # 09:01 → 09:02（可執行版）
    df["m1_15"] = df.c15 / df.c1 - 1           # 09:01 → 09:15
    df["o2c"] = df.d_close / df.open0 - 1
    df["lab"] = pd.cut(df.gap, BKT, labels=LAB)
    print(f"\n[T2] 1 分鐘層：{df.sid.nunique()} 檔 / {len(df)} 股日 / "
          f"{df.date.nunique()} 個交易日 / {df.date.min()} ~ {df.date.max()}")
    base_bps, bt, _, bn = cl_t(df[df.lab == "±0.25%"], "m1")
    print(f"零跳空基準漂移 open→09:01 = {base_bps:+.1f} bps (t={bt:.2f}, n={bn})")
    bucket_table(df, "m1", "T2-A 開盤單一價 → 09:01（原始報酬 bps）")
    signed_table(df, "m1", "T2-B 同上，換成跳空方向超額", base_bps)
    b2, *_ = cl_t(df[df.lab == "±0.25%"], "m1_2")
    signed_table(df, "m1_2", "T2-C 可執行版：09:01 收盤進場 → 09:02", b2)
    b15, *_ = cl_t(df[df.lab == "±0.25%"], "m1_15")
    signed_table(df, "m1_15", "T2-D 延伸：09:01 → 09:15", b15)

    # 逐年一致性（跳空方向超額，全桶合併，只看 |gap|>1%）
    print("\n### T2-E 逐年一致性（|gap|>1%，跳空方向超額 bps）")
    s = df[df.gap.abs() > 0.01].copy()
    s["sx"] = np.sign(s.gap) * (s.m1 - base_bps / 1e4)
    s["yr"] = s.date.str.slice(0, 4)
    print(f"{'年':<6}{'n':>7}{'超額bps':>9}{'t':>7}")
    for y, g in s.groupby("yr"):
        m, t, nd, no = cl_t(g, "sx")
        print(f"{y:<6}{no:>7}{m:>9.1f}{'' if np.isnan(t) else f'{t:>7.2f}'}")
    m, t, nd, no = cl_t(s, "sx")
    print(f"{'全期':<6}{no:>7}{m:>9.1f}{t:>7.2f}")

    # 上下跳空拆開
    print("\n### T2-F 漲空 / 跌空分開（原始 open→09:01 bps）")
    for nm, sel in [("漲空 >+1%", df[df.gap > 0.01]), ("跌空 <-1%", df[df.gap < -0.01])]:
        m, t, nd, no = cl_t(sel, "m1")
        cont = float((np.sign(sel.m1) == np.sign(sel.gap)).mean())
        print(f"{nm:<11}n={no:<6} 平均 {m:+7.1f} bps  t={t:+5.2f}  延續率 {cont*100:.1f}%")
    return df


# ---------------------------------------------------------------- T3 秒級
def tier3():
    meta = pd.DataFrame(pd.read_pickle(f"{OUT}/tick_meta.pkl"))
    z = np.load(f"{OUT}/tick_arrs.npz")
    px = z["px"].astype("float64")
    keep = meta.sid.isin(SIDS).values
    meta = meta[keep].reset_index(drop=True); px = px[keep]
    # 首筆成交前的秒回填成開盤價（不回填會靜默只留下高活躍子集 → 假顯著）
    op = meta.open_px.to_numpy()[:, None]
    px = np.where(np.isnan(px), np.nan, px)
    first_valid = np.isnan(px).argmin(axis=1)
    for i in range(px.shape[0]):
        px[i, :first_valid[i]] = op[i, 0]
    px = pd.DataFrame(px).ffill(axis=1).to_numpy()

    daily = pd.read_pickle(f"{OUT}/daily_panel.pkl")[
        ["stock_id", "trade_date", "base", "prev_close", "adv20", "close"]]
    daily.columns = ["sid", "date", "base", "prev_close", "adv20", "d_close"]
    m = meta.merge(daily, on=["sid", "date"], how="left")
    ok = m.base.notna().to_numpy()
    m = m[ok].reset_index(drop=True); px = px[ok]
    m["gap"] = m.open_px / m.base - 1
    sel = ((m.gap.abs() <= 0.105) & (m.prev_close >= 10)).to_numpy()
    m = m[sel].reset_index(drop=True); px = px[sel]
    m["lab"] = pd.cut(m.gap, BKT, labels=LAB)
    m["r60"] = px[:, 60] / m.open_px - 1          # 開盤價 → +60s
    m["r30_60"] = px[:, 60] / px[:, 30] - 1       # +30s 進 → +60s
    m["r10_60"] = px[:, 60] / px[:, 10] - 1
    m["r60_300"] = px[:, 300] / px[:, 60] - 1
    print(f"\n[T3] 秒級層：{m.sid.nunique()} 檔 / {len(m)} 股日 / {m.date.nunique()} 日 / "
          f"{m.date.min()} ~ {m.date.max()}")
    b, bt, _, bn = cl_t(m[m.lab == "±0.25%"], "r60")
    print(f"零跳空基準 open→+60s = {b:+.1f} bps (t={bt:.2f}, n={bn})")
    bucket_table(m, "r60", "T3-A 開盤價 → +60 秒（原始 bps）")
    signed_table(m, "r60", "T3-B 同上，跳空方向超額", b)
    for col, nm in [("r10_60", "+10s 進場 → +60s"), ("r30_60", "+30s 進場 → +60s"),
                    ("r60_300", "+60s 進場 → +300s")]:
        bb, *_ = cl_t(m[m.lab == "±0.25%"], col)
        signed_table(m, col, f"T3-C 可執行版：{nm}", bb)
    # 最近一個月
    last = sorted(m.date.unique())[-21:]
    r = m[m.date.isin(last)]
    print(f"\n### T3-D 最近 21 個交易日（{last[0]} ~ {last[-1]}）")
    for nm, s in [("漲空 >+1%", r[r.gap > 0.01]), ("跌空 <-1%", r[r.gap < -0.01])]:
        mm, t, nd, no = cl_t(s, "r60")
        cont = float((np.sign(s.r60) == np.sign(s.gap)).mean()) if no else np.nan
        print(f"{nm:<11}n={no:<5} open→+60s {mm:+7.1f} bps  t={t if t==t else float('nan'):+5.2f}"
              f"  延續率 {cont*100:.1f}%")
    return m


if __name__ == "__main__":
    d2 = tier2()
    d3 = tier3()
