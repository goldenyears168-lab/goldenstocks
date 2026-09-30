#!/usr/bin/env python3
"""「什麼情況可以抱久一點」條件掃描(2026-09-30)。

早上的結論是**無條件**延長沒用:毛報酬對持有期平坦(+9.08@5分 → +8.17@60分)。
本檔問的是不同問題:有沒有**子群**的持有期曲線斜率為正?
方法:對每個事件算 r(k)=(買一價[k]/進場價−1)bps(k=1..12 桶=5..60 分),按進場當下
可得的條件(PIT)分組,看 r(12)−r(1) 的斜率與 r(12)−22 的絕對水準。
IS(≤2026-06-30)掃描、OOS 驗;斜率用日聚類 t。

用法:PYTHONPATH=src:scripts/research .venv/bin/python scripts/research/biglot_hold_extend_scan.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from biglot_score_v23_fit import cl_t
from biglot_hold_lab import load_lab

SRC = Path.home() / "goldenstocks-data/scratch/biglot_exit_paths_2026-09-30.parquet"
COST = 22.0
H = 12


def build():
    d = pd.read_parquet(SRC)
    d = d[d["filled"].values & np.isfinite(d["entry"].values) & (d["entry"].values > 0)].reset_index(drop=True)
    L = load_lab(); p = L["d"].sort_values(["sid", "date", "bucket"])
    g = p.groupby(["sid", "date"])
    p["cum_day"] = g["big_net"].cumsum() / g["tot_amt"].cumsum().replace(0, np.nan) * 100
    p["amt_z"] = g["tot_amt"].transform(lambda s: (s - s.expanding().mean()) / s.expanding().std())
    items = [c for c in p.columns if c.startswith("it:")]
    keep = ["sid", "date", "bucket", "b5n", "bigsh30", "cum_day", "share5", "rbuy5", "sell30s", "sell5m",
            "w5", "r30", "r30s", "mkt_w5", "mkt_r30s", "follow", "self_kill", "amt_z", "tot_amt"] + items
    d = d.merge(p[keep], on=["sid", "date", "bucket"], how="left")
    ff = lambda m: pd.DataFrame(m).ffill(axis=1).bfill(axis=1).values          # noqa: E731
    B = ff(np.column_stack([d[f"b{k}"].values for k in range(H + 1)]))
    R = (B / d["entry"].values[:, None] - 1) * 1e4          # 毛報酬路徑 bps
    S = np.column_stack([d[f"s{k}"].values for k in range(H + 1)])
    return d, R, S, items


def conds(d, items):
    """(名稱, 布林遮罩) 的候選清單 —— 全部是訊號當下可得。"""
    out = []
    sc = d["score"].values
    out += [("分數 >=18", sc >= 18), ("分數 15-16", sc < 17), ("分數 17", (sc >= 17) & (sc < 18))]
    out += [("自己殺", d["self_kill"].fillna(False).values.astype(bool)),
            ("跟盤殺", d["follow"].fillna(False).values.astype(bool)),
            ("大盤沒跌 mkt_r30s>=0", d["mkt_r30s"].fillna(0).values >= 0),
            ("大盤 w5 >=0", d["mkt_w5"].fillna(0).values >= 0)]
    for c, lo in (("b5n", 20), ("bigsh30", 10), ("cum_day", 10)):
        out.append((f"{c} >= {lo}", d[c].fillna(-99).values >= lo))
        out.append((f"{c} < 0", d[c].fillna(99).values < 0))
    out += [("散戶占比 share5 <25", d["share5"].fillna(99).values < 25),
            ("散戶占比 share5 >=40", d["share5"].fillna(0).values >= 40),
            ("30s主動賣 <50%", d["sell30s"].fillna(9).values < 0.5),
            ("30s主動賣 =100%", d["sell30s"].fillna(0).values >= 0.999),
            ("5m主動賣 >=70%", d["sell5m"].fillna(0).values >= 0.7),
            ("量能 amt_z >=2", d["amt_z"].fillna(-9).values >= 2),
            ("量能 amt_z <0", d["amt_z"].fillna(9).values < 0),
            ("當桶跌 r30 <=-50", d["r30"].fillna(0).values <= -50),
            ("當桶跌 r30 >-20", d["r30"].fillna(-99).values > -20),
            ("日內漲跌 w5 <=-200", d["w5"].fillna(0).values <= -200),
            ("日內漲跌 w5 >=0", d["w5"].fillna(-99).values >= 0)]
    b = d["bucket"].values
    out += [("早盤 <=10:00", b <= "10:00"), ("盤中 10:00-11:30", (b > "10:00") & (b <= "11:30")),
            ("午後 >11:30", b > "11:30")]
    for it in items:
        m = d[it].fillna(False).values.astype(bool)
        if 40 <= m.sum() <= len(d) - 40:
            out.append((it[3:], m))
    return out


def main():
    d, R, S, items = build()
    isb = d["is"].values.astype(bool); dates = d["date"].values
    print(f"樣本 n={len(d)}(掛買一成交) IS {isb.sum()} / OOS {(~isb).sum()}\n")
    print("==== 0. 全體持有期曲線(毛報酬 bps,未扣成本) ====")
    print("  " + "".join(f"{k*5:>8d}分" for k in (1, 2, 4, 6, 8, 12)))
    print("  " + "".join(f"{R[:, k].mean():+9.2f}" for k in (1, 2, 4, 6, 8, 12)))
    sl_all = R[:, 12] - R[:, 1]
    mu, t = cl_t(sl_all, dates)
    print(f"  斜率 r(60分)−r(5分) = {mu:+.2f} (t{t:+.2f})  → 平坦,這是早上的結論\n")

    print("==== 1. 條件掃描:哪些子群的持有期斜率為正 ====")
    print(f"  {'條件':<24}{'n':>5}  {'r5分':>8}{'r30分':>8}{'r60分':>8} {'斜率':>8}  IS斜率(t)      OOS斜率(t)    r60−22 IS/OOS")
    rows = []
    for name, m in conds(d, items):
        n = int(m.sum())
        if n < 40:
            continue
        sl = R[m, 12] - R[m, 1]
        mi, ti = cl_t(sl[isb[m]], dates[m][isb[m]]) if (m & isb).sum() >= 20 else (np.nan, np.nan)
        mo, to = cl_t(sl[~isb[m]], dates[m][~isb[m]]) if (m & ~isb).sum() >= 20 else (np.nan, np.nan)
        net_i = R[m & isb, 12].mean() - COST if (m & isb).sum() >= 20 else np.nan
        net_o = R[m & ~isb, 12].mean() - COST if (m & ~isb).sum() >= 20 else np.nan
        rows.append({"name": name, "n": n, "sl": sl.mean(), "mi": mi, "ti": ti, "mo": mo, "to": to,
                     "r1": R[m, 1].mean(), "r6": R[m, 6].mean(), "r12": R[m, 12].mean(),
                     "net_i": net_i, "net_o": net_o})
    rows.sort(key=lambda r: -(r["mi"] if np.isfinite(r["mi"]) else -999))
    for r in rows:
        star = " ***" if (np.isfinite(r["ti"]) and np.isfinite(r["to"]) and r["ti"] > 1.5 and r["to"] > 1.0) else ""
        print(f"  {r['name']:<24}{r['n']:>5}  {r['r1']:+8.1f}{r['r6']:+8.1f}{r['r12']:+8.1f} {r['sl']:+8.1f}  "
              f"{r['mi']:+7.1f}(t{r['ti']:+4.1f})  {r['mo']:+7.1f}(t{r['to']:+4.1f})  {r['net_i']:+6.1f}/{r['net_o']:+6.1f}{star}")
    print("\n  *** = IS 斜率 t>1.5 且 OOS 斜率 t>1.0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
