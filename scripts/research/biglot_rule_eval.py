#!/usr/bin/env python3
"""大戶未平倉規則的正式評估器 —— 25 天樣本收滿後跑這一支。

讀 biglot-bucket-eod 落的 CSV，一次算出所有事前登記的格，並且**同時報告兩種
「多空同時連勝」的處理方式**（見下）。不重跑任何收集。

事前登記（2026-09-06 鎖，2026-09-08 補 tie-break）：
  時鐘   equal（收盤回算，含未來資訊）· fixed（live 可實作）—— 兩種都測
  N      {30, 60}（40 只當內插檢查）
  規則   A 連勝>=k · C 連勝>=k 且 bps[t]>bps[t-1] · E 連勝>=k 且 bps[t]>前兩格平均
  k      3
  執行   持有版（觸發後抱到反向訊號）· 台指期 · 來回成本 2 bps

⚠ tie-break（2026-09-08 補登記）
  多空**同時**滿足 k 連勝的情況在 2026-09-08 一天就出現 4 次，不是罕見狀況。
  原本的實作沒有規定該站哪邊，是 `if 多 … elif 空` 的程式碼順序在決定 —— 那是
  一個沒被登記、沒被思考過的自由度。這裡把兩種處理都算出來、都報告：
    long_first  沿用原行為（同時滿足時取多方）
    larger_bps  取該格 bps 較大的一邊（有意義的判準）
  ⚠ 這不需要改收集：long_bps / short_bps 都已在 CSV 裡，兩種都能事後重算。

同時輸出「當日換邊次數」分層 —— 換邊次數當下就能算、不含未來資訊，若它與損益
強負相關，就能當成事前可用的開關（2026-09-08 觀察：A 在高換邊日崩、C/E 守住）。

用法：
  PYTHONPATH=src .venv/bin/python scripts/research/biglot_rule_eval.py
  PYTHONPATH=src .venv/bin/python scripts/research/biglot_rule_eval.py --min-date 2026-09-07
"""
from __future__ import annotations
import argparse, os, sys
import numpy as np, pandas as pd

CSV = os.path.expanduser("~/goldenstocks-data/data/cache/biglot_bucket/bucket_reset_{tag}{n}.csv")
RULES = ("A", "C", "E")


def signal(L, S, rule, k, tie):
    """回傳每格的訊號（+1 多 / -1 空 / 0 無）"""
    n = len(L); sl = ss = 0; v = np.zeros(n, int)
    for i in range(n):
        sl = sl + 1 if L[i] > 0 else 0
        ss = ss + 1 if S[i] > 0 else 0
        ok_l = ok_s = True
        if rule == "C":
            ok_l = i >= 1 and L[i] > L[i - 1]; ok_s = i >= 1 and S[i] > S[i - 1]
        elif rule == "E":
            ok_l = i >= 2 and L[i] > (L[i - 1] + L[i - 2]) / 2
            ok_s = i >= 2 and S[i] > (S[i - 1] + S[i - 2]) / 2
        hit_l = sl >= k and ok_l
        hit_s = ss >= k and ok_s
        if hit_l and hit_s:
            v[i] = 1 if (tie == "long_first" or L[i] >= S[i]) else -1
        elif hit_l:
            v[i] = 1
        elif hit_s:
            v[i] = -1
    return v


def hold(v):
    h = np.zeros(len(v), int); c = 0
    for i, x in enumerate(v):
        if x:
            c = x
        h[i] = c
    return h


def day_pnl(g, rule, k, tie, cost):
    g = g.sort_values("bk").reset_index(drop=True)
    v = signal(g.long_bps.values, g.short_bps.values, rule, k, tie)
    h = hold(v)
    r = g.txf.pct_change(fill_method=None).shift(-1).values * 1e4
    gross = float(np.nansum(h * np.nan_to_num(r)))
    sw = int((np.abs(np.diff(np.r_[0, h])) > 0).sum())
    both = int(((np.r_[0, np.diff(v)] != 0) & (v != 0)).sum())  # 訊號次數（診斷用）
    return gross - sw * cost, sw, gross, both


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--cost", type=float, default=2.0)
    ap.add_argument("--min-date", default="2026-09-07",
                    help="正式樣本起算日（09-03/09-04 用舊 biglot 訂閱清單，預設排除）")
    ap.add_argument("--ns", nargs="*", type=int, default=[30, 60])
    a = ap.parse_args()
    rows = []
    for tag, clock in (("n", "equal"), ("fx", "fixed")):
        for N in a.ns:
            f = CSV.format(tag=tag, n=N)
            if not os.path.exists(f):
                continue
            d = pd.read_csv(f, dtype={"date": str})
            d = d[d.date >= a.min_date]
            if d.empty:
                continue
            for rule in RULES:
                for tie in ("long_first", "larger_bps"):
                    for dt, g in d.groupby("date"):
                        net, sw, gross, nsig = day_pnl(g, rule, a.k, tie, a.cost)
                        rows.append(dict(clock=clock, N=N, rule=rule, tie=tie, date=dt,
                                         net=net, sw=sw, gross=gross, nsig=nsig,
                                         nbk=len(g)))
    if not rows:
        print(f"沒有 >= {a.min_date} 的資料"); return 1
    r = pd.DataFrame(rows)
    nd = r.date.nunique()
    print(f"正式樣本 {nd} 天（{r.date.min()} ~ {r.date.max()}）· k={a.k} · 成本 {a.cost} bps/次換邊")
    print(f"⚠ 目標 25 天，目前 {nd} 天 —— {'樣本不足，以下僅供追蹤，不得作為結論' if nd < 25 else '樣本已足'}\n")

    g = r.groupby(["clock", "N", "rule", "tie"]).agg(
        天=("date", "nunique"), 每日淨=("net", "mean"), 每日換邊=("sw", "mean"),
        正日=("net", lambda x: int((x > 0).sum())))
    g["t"] = r.groupby(["clock", "N", "rule", "tie"]).net.apply(
        lambda x: x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 else np.nan)
    pd.set_option("display.width", 200)
    print("=== 事前登記格 × 兩種 tie-break ===")
    print(g.reset_index().to_string(index=False, float_format=lambda x: f"{x:,.1f}"))

    print("\n=== tie-break 影響多大（larger_bps − long_first，每日淨 bps）===")
    p = g.reset_index().pivot_table(index=["clock", "N", "rule"], columns="tie", values="每日淨")
    p["差"] = p.larger_bps - p.long_first
    print(p.to_string(float_format=lambda x: f"{x:+,.1f}"))

    print("\n=== 逐日（fixed / N=60 / larger_bps）===")
    s = r.query("clock=='fixed' and N==60 and tie=='larger_bps'")
    if not s.empty:
        print(s.pivot_table(index="date", columns="rule", values="net")
              .join(s.groupby("date").sw.mean().rename("平均換邊"))
              .to_string(float_format=lambda x: f"{x:+,.1f}"))

    print("\n=== 換邊次數分層（是否能當事前開關）===")
    s = r.query("tie=='larger_bps'")
    med = s.sw.median()
    print(f"每日換邊次數中位 {med:.0f}")
    for rule in RULES:
        x = s[s.rule == rule]
        lo = x[x.sw <= med].net; hi = x[x.sw > med].net
        print(f"  {rule}  低換邊日 {lo.mean():+7.1f} bps (n={len(lo)})   "
              f"高換邊日 {hi.mean():+7.1f} bps (n={len(hi)})   差 {lo.mean()-hi.mean():+7.1f}")
    print("\n※ 判準：25 天後 Bonferroni 於 12 格的門檻＝|t| > 2.9；"
          "若 fixed 與 equal 結論相反、或 N=30 與 N=60 相反 → 參數過擬合，收線。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
