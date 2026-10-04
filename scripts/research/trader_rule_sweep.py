#!/usr/bin/env python3
"""進場 × 出場規則 sweep（面板驅動,不重放逐筆,所以幾秒跑完幾百組）。

使用者 2026-10-04 指定的方向：
  · 出場改用**全日／兩日尺度**,門檻拉高（5 分尺度在盤中會反覆穿越零軸,太敏感）
  · 「沒什麼大問題就抱著」→ 預設續抱,只有日尺度條件真的破了才出
  · 進場門檻也要更高、訊號品質更好

## 紀律（4 天樣本做 sweep 一定會掃出東西,所以這些不能省）
1. 指標是**超額**（減掉無條件基準）,不是絕對報酬 —— 四天全多頭,隨便買都賺
2. **分鐘內洗牌**當虛無：保留「每分鐘挑幾檔」的時間結構,只隨機換是哪幾檔。
   隨機抽整個面板會打散時間聚集,把門檻設得太鬆
3. 報**整個分布**,不只最好那格;並報日一致性（4 天各自是否贏基準）
4. 出場一律含成本 0.8bp 來回
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

P = Path.home() / "goldenstocks-data/data"
COST_PCT = 0.008        # 0.8bp 來回

# ---- 進場條件積木（名稱→面板上的布林式）
def entry_conds(d: pd.DataFrame) -> dict:
    return {
        "大戶佔比day前15%": d.big_day_pct >= 85,
        "大戶佔比day前30%": d.big_day_pct >= 70,
        "大戶佔比5m前15%": d.big_5m_pct >= 85,
        "大減散5m前15%": d.bigless_5m_pct >= 85,
        "大減散day前30%": d.bigless_day_pct >= 70,
        "成交額30m前30%": d.amt_30m_pct >= 70,
        "成交額30m前15%": d.amt_30m_pct >= 85,
        "距差額低點≤30分": d.agg_from_low_min <= 30,
        "族群漲幅最低一半": d.chg_pct_grp <= 50,
        "族群乖離最小一半": d.bias_pct_grp <= 50,
    }


# ---- 出場條件積木（全日／兩日尺度為主,門檻拉高）
def exit_conds(d: pd.DataFrame) -> dict:
    return {
        "全日大戶佔比掉出前50%": d.big_day_pct < 50,
        "全日大戶佔比掉出前30%": d.big_day_pct < 70,
        "全日大減散<0": d.bigless_day < 0,
        "兩日大減散<0": d.bigless_2d < 0,
        "兩日大戶佔比掉出前50%": d.big_2d_pct < 50,
        "5分大減散<0(舊規則,對照用)": d.bigless_5m < 0,
        "永不出場(抱到最後)": pd.Series(False, index=d.index),
    }


def simulate_rule(d: pd.DataFrame, enter: pd.Series, exit_m: pd.Series,
                  max_hold_min: float | None = None) -> pd.DataFrame:
    """對每個 (day,sid) 走一遍時間軸：第一次 enter 成立就進,之後第一次 exit 成立就出。
    一檔標的在一天內只做一趟（避免同一波行情被重複計入,那會灌大樣本數）。"""
    out = []
    d = d.sort_values(["sid", "t"])
    for (sid, day), g in d.groupby(["sid", "day"], sort=False):
        e = g[enter.loc[g.index].fillna(False).to_numpy()]
        if e.empty:
            continue
        row = e.iloc[0]
        p_in = row["entry_px"]
        if not p_in or np.isnan(p_in):
            continue
        after = g[g.t > row.t]
        xm = exit_m.loc[after.index].fillna(False).to_numpy() if len(after) else np.array([], bool)
        if max_hold_min is not None and len(after):
            xm = xm | ((after.t - row.t).to_numpy() / 60 >= max_hold_min)
        hit = after[xm]
        if len(hit):
            xr = hit.iloc[0]
            ret = 100 * (xr["entry_px"] / p_in - 1) if xr["entry_px"] else np.nan
            hold = (xr.t - row.t) / 60
            how = "條件出場"
        else:                      # 沒觸發 → 抱到當日收盤
            ret, hold, how = row["fwd_close"], None, "抱到收盤"
        if ret is None or (isinstance(ret, float) and np.isnan(ret)):
            continue
        out.append({"day": day, "sid": sid, "hm": row["hm"], "grp": row["grp"],
                    "ret": ret - COST_PCT, "hold": hold, "how": how,
                    "fwd_close": row["fwd_close"], "fwd_next": row["fwd_nextclose"]})
    return pd.DataFrame(out)


def shuffle_null(d: pd.DataFrame, enter: pd.Series, col: str, B: int = 200, seed: int = 7):
    rng = np.random.default_rng(seed)
    key = d.day + " " + d.hm
    cnt = key[enter.fillna(False)].value_counts()
    grp = {k: v[col].dropna().to_numpy() for k, v in d.groupby(key)}
    res = []
    for _ in range(B):
        acc = [rng.choice(a, size=min(c, len(a)), replace=False)
               for k, c in cnt.items() if (a := grp.get(k)) is not None and len(a)]
        if acc:
            res.append(np.concatenate(acc).mean())
    return np.array(res)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(P / "scratch/trader_feature_panel.parquet"))
    ap.add_argument("--out", default=str(P / "scratch/trader_rule_sweep.csv"))
    ap.add_argument("--entry-k", type=int, default=3)
    args = ap.parse_args()
    d = pd.read_parquet(args.panel)
    EC, XC = entry_conds(d), exit_conds(d)
    base_close = d["fwd_close"].mean()

    rows = []
    for combo in itertools.combinations(EC, args.entry_k):
        enter = np.logical_and.reduce([EC[c].fillna(False) for c in combo])
        enter = pd.Series(enter, index=d.index)
        if enter.sum() < 60:
            continue
        for xname, xm in XC.items():
            tr = simulate_rule(d, enter, xm)
            if len(tr) < 25:
                continue
            dm = tr.groupby("day")["ret"].mean()
            se = dm.std(ddof=1) / np.sqrt(len(dm)) if len(dm) > 1 else np.nan
            rows.append({
                "進場": " ∧ ".join(combo), "出場": xname, "趟數": len(tr),
                "平均報酬%": tr.ret.mean(), "中位%": tr.ret.median(),
                "勝率%": 100 * (tr.ret > 0).mean(),
                "標準差%": tr.ret.std(ddof=1),
                "日一致": f"{int((dm>0).sum())}/{len(dm)}",
                "t_日聚類": tr.ret.mean() / se if se and se > 0 else np.nan,
                "中位持有分": tr.hold.median(),
                "條件出場佔比%": 100 * (tr.how == "條件出場").mean(),
                "族群數": tr.grp.nunique(),
                "超額vs無條件%": tr.ret.mean() - base_close,
            })
    t = pd.DataFrame(rows)
    t.to_csv(args.out, index=False)
    print(f"wrote {args.out}  {len(t)} 組（進場 {args.entry_k} 層 × 出場 {len(XC)} 種）",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
