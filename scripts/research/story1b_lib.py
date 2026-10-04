#!/usr/bin/env python3
"""故事 1B：假說 A（護盤式卡位）與假說 B（下跌時量縮 vs 爆量）的檢定基礎設施。

紀律（全部來自 2026-10-05 其他 agent 踩出來的坑）：
 · 主要結果一律「每檔每日只取第一個觸發」去重
 · 超額 = 減同分鐘 23 檔 CORE 平均
 · 日聚類 t（4 天日均值）
 · 選股 α = 去 (day,sid) 均值後的殘差（分離選股 vs 選時機）
 · 分鐘內洗牌虛無（且洗牌後同樣去重）
 · 報最大 sid 貢獻佔比 + 剔除最大貢獻檔
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd

PANEL = pathlib.Path.home() / "goldenstocks-data/data/scratch/trader_feature_panel.parquet"
ROLES = pathlib.Path("/Users/jackm4/goldenstocks/config/biglot_roles.json")


def load() -> tuple[pd.DataFrame, set[str]]:
    p = pd.read_parquet(PANEL)
    r = json.loads(ROLES.read_text())
    drop = ({x["sid"] for x in r["reference_in_pool"]}
            | {x["sid"] for x in r["independent"]} | {"2481", "1303"})
    core = set(p.sid.unique()) - drop
    p = p.sort_values(["day", "sid", "t"]).reset_index(drop=True)
    g = p.groupby(["day", "sid"], sort=False)["px"]
    for k in (1, 5, 15, 30):
        p[f"r{k}"] = g.transform(lambda s, k=k: s / s.shift(k) - 1.0) * 100.0
    p["is_core"] = p.sid.isin(core)

    # ---- 市場代理：同分鐘橫斷面中位數（42 檔 = 使用者畫面；CORE 版做敏感度）
    key = ["day", "hm"]
    for k in (1, 5, 15, 30):
        # 市場代理用橫斷面「平均」：42 檔分鐘報酬的中位數因為 tick 離散常常恰為 0
        # （mkt5 中位數 = 0、只有 14% 分鐘為負）,中位數在這個樣本上測不出「市場在跌」。
        p[f"mkt{k}"] = p.groupby(key)[f"r{k}"].transform("mean")
        p[f"mktmed{k}"] = p.groupby(key)[f"r{k}"].transform("median")
        c = p[p.is_core].groupby(key)[f"r{k}"].median().rename(f"mktc{k}")
        p = p.merge(c, left_on=key, right_index=True, how="left")
        p[f"res{k}"] = p[f"r{k}"] - p[f"mkt{k}"]
        p[f"res{k}_pct"] = p.groupby(key)[f"res{k}"].rank(pct=True) * 100.0

    # ---- 市場跌幅 z：在「時段」桶內標準化（開盤分鐘波動天然大,池化門檻會變成時段指示器）
    for k in (1, 5, 15, 30):
        m = p.groupby(key)[f"mkt{k}"].first().reset_index()
        m["時段"] = m.hm.str.slice(0, 2)
        mu = m.groupby("時段")[f"mkt{k}"].transform("mean")
        sd = m.groupby("時段")[f"mkt{k}"].transform("std")
        m[f"mkt{k}_z"] = (m[f"mkt{k}"] - mu) / sd
        p = p.merge(m[key + [f"mkt{k}_z"]], on=key, how="left")

    # ---- 自身成交額：在 (sid × 時段) 內 z（跨時段不可比,跨檔不可比）
    p["seg"] = p.hm.str.slice(0, 2)
    p["lamt5"] = np.log(p.amt_5m.clip(lower=1))
    gs = p.groupby(["sid", "seg"])["lamt5"]
    p["amt5_z"] = (p.lamt5 - gs.transform("mean")) / gs.transform("std")
    p["lamt30"] = np.log(p.amt_30m.clip(lower=1))
    gs30 = p.groupby(["sid", "seg"])["lamt30"]
    p["amt30_z"] = (p.lamt30 - gs30.transform("mean")) / gs30.transform("std")

    # ---- 超額前瞻報酬：減同分鐘 CORE 平均
    for c in ("fwd30", "fwd60", "fwd_close", "fwd_nextclose"):
        base = p[p.is_core].groupby(key)[c].mean().rename(f"b_{c}")
        p = p.merge(base, left_on=key, right_index=True, how="left")
        p[f"x_{c}"] = p[c] - p[f"b_{c}"]
    # 選時機殘差：去 (day,sid) 均值
    for c in ("fwd30", "fwd60", "fwd_close"):
        p[f"d_{c}"] = p[f"x_{c}"] - p.groupby(["day", "sid"])[f"x_{c}"].transform("mean")
    return p, core


def dedup(d: pd.DataFrame) -> pd.DataFrame:
    return d.sort_values("t").groupby(["day", "sid"], as_index=False).first()


def day_t(trips: pd.DataFrame, col: str) -> float:
    dm = trips.groupby("day")[col].mean().dropna()
    if len(dm) < 2:
        return np.nan
    return float(dm.mean() / (dm.std(ddof=1) / np.sqrt(len(dm))))


def shuffle_null_dedup(p: pd.DataFrame, mask: pd.Series, col: str,
                       B: int = 500, seed: int = 11) -> np.ndarray:
    """分鐘內洗牌：保留「哪些分鐘被觸發、每分鐘幾檔」,只隨機換是哪幾檔(限 CORE),
    洗完同樣做 first-per-(day,sid) 去重 —— 這樣虛無分布才跟觀測量同口徑。
    全向量化（原本 DataFrame 版太慢,450 次評估跑不完）。"""
    rng = np.random.default_rng(seed)
    cp = p[p.is_core & p[col].notna()].sort_values("t").reset_index(drop=True)
    v = cp[col].to_numpy()
    ds = pd.factorize(cp.day + "|" + cp.sid)[0]
    minute = (cp.day + " " + cp.hm).to_numpy()
    trig = mask.reindex(cp.index.map(lambda i: i))  # placeholder, replaced below
    tm = (p.day + " " + p.hm)[mask.fillna(False) & p.is_core & p[col].notna()]
    cnt = tm.value_counts()
    pos = {k: np.flatnonzero(minute == k) for k in cnt.index}
    out = []
    for _ in range(B):
        acc = []
        for k, c in cnt.items():
            a = pos[k]
            if len(a) == 0:
                continue
            acc.append(rng.choice(a, size=min(int(c), len(a)), replace=False))
        if not acc:
            continue
        arr = np.sort(np.concatenate(acc))           # cp 已按 t 排序 → 位置序=時間序
        _, first = np.unique(ds[arr], return_index=True)
        out.append(v[arr[first]].mean())
    return np.array(out)


def evaluate(p: pd.DataFrame, mask: pd.Series, name: str, col: str = "x_fwd_close",
             B: int = 500, verbose: bool = True) -> dict:
    m = mask.fillna(False) & p.is_core
    rows = p[m & p[col].notna()]
    res = {"rule": name, "col": col, "n_rows": len(rows),
           "rows_excess": rows[col].mean() if len(rows) else np.nan,
           "rows_win": 100 * (rows[col] > 0).mean() if len(rows) else np.nan}
    tr = dedup(rows)
    res["n_trips"] = len(tr)
    if len(tr) == 0:
        return res
    res["excess"] = tr[col].mean()
    res["raw"] = tr[col.replace("x_", "")].mean()
    res["xwin"] = 100 * (tr[col] > 0).mean()
    res["t_day"] = day_t(tr, col)
    dcol = col.replace("x_", "d_")
    res["alpha_timing"] = tr[dcol].mean() if dcol in tr else np.nan
    res["t_timing"] = day_t(tr, dcol) if dcol in tr else np.nan
    res["days_pos"] = int((tr.groupby("day")[col].mean() > 0).sum())
    res["n_days"] = tr.day.nunique()
    res["n_sids"] = tr.sid.nunique()
    # 集中度：各 sid 對總和的貢獻
    tot = tr[col].sum()
    contrib = tr.groupby("sid")[col].sum()
    res["max_sid_share"] = float(contrib.max() / tot) if tot else np.nan
    res["max_sid"] = contrib.idxmax() if len(contrib) else None
    worst = contrib.abs().idxmax()
    res["ex_top_sid"] = tr[tr.sid != worst][col].mean()
    res["ex_top_sid_name"] = worst
    res["drop2_excess"] = tr.nlargest(max(len(tr) - 2, 0), col)[col].iloc[2:].mean() \
        if len(tr) > 4 else np.nan
    srt = tr.sort_values(col, ascending=False)
    res["drop_top2"] = srt.iloc[2:][col].mean() if len(tr) > 4 else np.nan
    if B:
        nd = shuffle_null_dedup(p, m, col, B=B)
        res["null_mean"] = nd.mean()
        res["null_p99"] = np.quantile(nd, 0.99)
        res["null_p95"] = np.quantile(nd, 0.95)
        res["pass_p99"] = bool(res["excess"] > res["null_p99"])
        res["pval"] = float((nd >= res["excess"]).mean())
    if verbose:
        print(f"[{name}] rows={res['n_rows']} trips={res['n_trips']} "
              f"excess={res['excess']:+.3f}% xwin={res['xwin']:.1f}% "
              f"t_day={res['t_day']:+.2f} timing_a={res['alpha_timing']:+.3f} "
              f"maxsid={res['max_sid_share']:.0%}({res['max_sid']}) "
              f"exTop={res['ex_top_sid']:+.3f} p99={res.get('null_p99',float('nan')):+.3f} "
              f"pass={res.get('pass_p99')} p={res.get('pval')}")
    return res
