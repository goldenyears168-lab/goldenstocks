#!/usr/bin/env python3
"""出場 sweep 第二輪 —— 補上第一輪漏掉的兩種出場。

jack 2026-10-04 指正兩件事,兩件我第一輪都沒測：
  1. 「一定不是持有到收盤,盤中見好就收才對」→ 沒測**停利目標**
  2. 「平倉是因為被更好的訊號挑戰」→ 沒測**挑戰式換股**

第一輪只測了「進場條件破了就出」,結論是「抱到收盤最好」。那個結論只在
「條件破 vs 不出場」這個二選一裡成立,不能外推成「停利沒用」。這一輪補齊。

路徑模擬：對每個 (day,sid) 的進場,沿著面板的分鐘序列往前走,
第一個滿足出場條件的分鐘就出。停利／停損用 px 相對進場價;
挑戰式換股用「同一分鐘有沒有分數更高的候選」。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

P = Path.home() / "goldenstocks-data/data"
COST_PCT = 0.008


def entry_mask(d: pd.DataFrame) -> pd.Series:
    """v4b 進場：全日為主、搭 30 分、共動族群、避開午盤。"""
    return ((d.big_day_pct >= 70) & (d.bigless_30m_pct >= 85)
            & (d.amt_30m_pct >= 70) & (d.chg_cm <= 50)
            & (~d.時段.isin([12, 13])))


def entry_score(d: pd.DataFrame) -> pd.Series:
    """候選強度分數（挑戰式換股要用來比大小）。"""
    return (d.big_day_pct.fillna(0) + d.bigless_30m_pct.fillna(0)
            + d.amt_30m_pct.fillna(0)) / 3


def run_path(d: pd.DataFrame, enter: pd.Series, *, tp: float | None = None,
             sl: float | None = None, trail: float | None = None,
             challenge: float | None = None, max_hold: float | None = None) -> pd.DataFrame:
    """走路徑模擬。tp=停利%, sl=停損%, trail=回撤出場%(從最高點回落),
    challenge=被更好訊號挑戰時的分數差門檻, max_hold=最長持有分鐘。
    全部沒觸發就抱到當日收盤（fwd_close）。"""
    d = d.sort_values(["sid", "day", "t"])
    sc = entry_score(d)
    # 每分鐘全市場最高候選分數（挑戰者）
    best = sc.where(enter).groupby([d.day, d.t]).transform("max")
    out = []
    for (sid, day), g in d.groupby(["sid", "day"], sort=False):
        e = g[enter.loc[g.index].fillna(False).to_numpy()]
        if e.empty:
            continue
        row = e.iloc[0]
        p_in = row["entry_px"]
        if not p_in or np.isnan(p_in):
            continue
        my_score = sc.loc[row.name]
        after = g[g.t > row.t]
        hit, how = None, None
        peak = p_in
        for idx, nx in after.iterrows():
            px = nx["px"]
            if px and px == px:
                peak = max(peak, px)
            r = 100 * (px / p_in - 1) if px else 0.0
            hold = (nx.t - row.t) / 60
            if sl is not None and r <= -sl:
                hit, how = (r, "停損"); break
            if tp is not None and r >= tp:
                hit, how = (r, "停利"); break
            if trail is not None and peak > p_in and 100 * (px / peak - 1) <= -trail and r > 0:
                hit, how = (r, "回撤出場"); break
            if challenge is not None:
                b = best.loc[idx]
                if b == b and b >= my_score + challenge:
                    hit, how = (r, "被更好訊號挑戰"); break
            if max_hold is not None and hold >= max_hold:
                hit, how = (r, "持有上限"); break
        if hit is None:
            ret, how, hold = row["fwd_close"], "抱到收盤", None
        else:
            ret, hold = hit, (nx.t - row.t) / 60
        if ret is None or (isinstance(ret, float) and np.isnan(ret)):
            continue
        out.append({"day": day, "sid": sid, "grp": g.grp.iloc[0], "hm": row["hm"],
                    "ret": ret - COST_PCT, "hold": hold, "how": how})
    return pd.DataFrame(out)


def summarise(tr: pd.DataFrame, label: str, base: float) -> dict:
    if len(tr) < 10:
        return {}
    dm = tr.groupby("day")["ret"].mean()
    se = dm.std(ddof=1) / np.sqrt(len(dm)) if len(dm) > 1 else np.nan
    return {"出場規則": label, "趟數": len(tr), "平均%": tr.ret.mean(),
            "中位%": tr.ret.median(), "勝率%": 100 * (tr.ret > 0).mean(),
            "標準差%": tr.ret.std(ddof=1),
            "日一致": f"{int((dm>0).sum())}/{len(dm)}",
            "t": tr.ret.mean() / se if se and se > 0 else np.nan,
            "中位持有分": tr.hold.median(),
            "提前出場%": 100 * (tr.how != "抱到收盤").mean(),
            "族群": tr.grp.nunique()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(P / "scratch/trader_feature_panel.parquet"))
    ap.add_argument("--out", default=str(P / "scratch/trader_exit_sweep2.csv"))
    args = ap.parse_args()
    d = pd.read_parquet(args.panel)
    e = entry_mask(d)
    base = d.fwd_close.mean()
    print(f"進場訊號 {e.sum():,} 個（{e.sum()/4:.0f}/日）· 無條件基準 {base:+.3f}%", file=sys.stderr)

    rows = []
    rows.append(summarise(run_path(d, e), "抱到收盤（對照組）", base))
    for tp in (0.5, 0.8, 1.0, 1.5, 2.0, 3.0):
        rows.append(summarise(run_path(d, e, tp=tp), f"停利 +{tp}%", base))
    for tr_ in (0.3, 0.5, 0.8, 1.2):
        rows.append(summarise(run_path(d, e, trail=tr_), f"回撤出場 −{tr_}%（從最高點）", base))
    for ch in (0, 3, 5, 10, 15):
        rows.append(summarise(run_path(d, e, challenge=ch), f"被更好訊號挑戰（分數差≥{ch}）", base))
    for tp, sl in ((1.0, 1.0), (1.5, 1.0), (2.0, 1.5), (1.0, 2.0)):
        rows.append(summarise(run_path(d, e, tp=tp, sl=sl), f"停利+{tp}% / 停損−{sl}%", base))
    for mh in (30, 60, 120):
        rows.append(summarise(run_path(d, e, max_hold=mh), f"持有上限 {mh} 分", base))
    t = pd.DataFrame([r for r in rows if r])
    t.to_csv(args.out, index=False)
    print(t.round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
