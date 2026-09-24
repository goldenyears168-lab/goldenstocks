#!/usr/bin/env python3
"""盤中分 V2.3 權重擬合 + V2.2 vs V2.3 IS/OOS 對照(2026-09-24)。

資料:pit100 × 127 日 5 分桶面板(scratch/biglot_panels/pit100_bucket5_panel_2026-03_08.csv),
     只讀 CSV,不碰 DB。IS = 2026-03~06-30,OOS = 07-01~08。
方法:
  1. 每項改成「區間指標」(急跌 200~300/300~400/400~600/≤−600 各自一格,不用累積門檻)。
  2. 只用 IS 做聯合 OLS(y = 未來 60 分對宇宙等權的超額 bps),按日聚類 SE;
     09:30–10:00 與 10:00–12:25 分開擬合(時段係數逐項而非一律 ×0.5)。
  3. 權重 = 0.7 × 係數(收縮),|cl-t| < 2 者歸零,四捨五入到 0.5。
  4. OOS 完全不參與擬合,直接拿權重表評分。
輸出:權重表(可貼進 biglot_dashboard.py 的 V23_W)、IS/OOS 的 IC/分箱/|分|≥15 多空/延遲 5 分/校準斜率。

用法:PYTHONPATH=src .venv/bin/python scripts/research/biglot_score_v23_fit.py
"""
from __future__ import annotations
import numpy as np, pandas as pd
from pathlib import Path

P = Path.home() / "goldenstocks-data/scratch/biglot_panels/pit100_bucket5_panel_2026-03_08.csv"
SHRINK, TMIN, COST = 0.7, 2.0, 25.0


def build():
    df = pd.read_csv(P, dtype={"sid": str}).sort_values(["sid", "date", "bucket"]).reset_index(drop=True)
    g = df.groupby(["sid", "date"], sort=False); px = df["last_px"]
    df["w5"] = (px / g["last_px"].shift(1) - 1) * 1e4
    df["r30"] = (px / g["last_px"].shift(6) - 1) * 1e4
    df["fwd60"] = (g["last_px"].shift(-12) / px - 1) * 1e4
    df["fwd60_d"] = (g["last_px"].shift(-13) / g["last_px"].shift(-1) - 1) * 1e4   # 延遲 1 桶進場
    df["day_ret"] = (px / g["last_px"].transform("first") - 1) * 100
    tot = df["tot_amt"].replace(0, np.nan)
    df["rbuy5"] = (df["ret_amt"] + df["ret_net"]) / 2 / tot * 100
    df["share5"] = df["ret_amt"] / tot * 100
    df["dshare5"] = df["share5"] - g["share5"].shift(1)
    df["b5n"] = df["big_net"] / tot * 100
    df["big5p"] = g["b5n"].shift(1)
    bn6 = g["big_net"].transform(lambda s: s.shift(1).rolling(6).sum())
    ta6 = g["tot_amt"].transform(lambda s: s.shift(1).rolling(6).sum())
    df["bigp30"] = bn6 / ta6.replace(0, np.nan) * 100
    gb = df.groupby(["date", "bucket"])
    df["mkt30"] = gb["r30"].transform("mean")
    df["ex60"] = df["fwd60"] - gb["fwd60"].transform("mean")
    df["ex60_d"] = df["fwd60_d"] - gb["fwd60_d"].transform("mean")
    ma12 = g["last_px"].transform(lambda s: s.rolling(12, min_periods=8).mean())
    df["cmp"] = (df["last_px"] / ma12 - 1) * 100                                   # 壓縮:對近1h均價 %
    df["bigsh30"] = g["big_net"].transform(lambda s: s.rolling(6).sum()) / g["tot_amt"].transform(lambda s: s.rolling(6).sum()).replace(0, np.nan) * 100
    hm = df["bucket"]
    return df[(hm >= "09:30") & (hm <= "12:25") & df["fwd60"].notna() & df["r30"].notna()].copy()


def items(d):
    w5, r30, dr, mk = d["w5"], d["r30"], d["day_ret"], d["mkt30"]
    a10 = d["bucket"] >= "10:00"
    return {  # name: (mask, V2.2 權重)
        "散戶虛拉":      ((w5 > 20) & (d["rbuy5"] >= 5), -8),
        "勿追5m":        ((w5 > 20) & ((d["dshare5"] > 5) | (d["b5n"] < -5)), -8),
        "散戶接跌":      ((w5 < -20) & (d["rbuy5"] >= 5), 4),
        "過熱150-200":   ((r30 >= 150) & (r30 < 200), -10),
        "過熱200-300":   ((r30 >= 200) & (r30 < 300), -13),
        "過熱300-400":   ((r30 >= 300) & (r30 < 400), -18),
        "過熱400-600":   ((r30 >= 400) & (r30 < 600), -10.5),
        "過熱≥600":      (r30 >= 600, 0),
        "急跌200-300":   ((r30 <= -200) & (r30 > -300), 6),
        "急跌300-400":   ((r30 <= -300) & (r30 > -400), 14),
        "急跌400-600":   ((r30 <= -400) & (r30 > -600), 22),
        "急跌≤−600":     (r30 <= -600, 23),
        "順漲":          ((mk >= 5) & (r30 >= 20) & a10 & (d["bucket"] < "12:00"), -3),
        "順跌":          ((mk <= -5) & (r30 <= -20), 3),
        "逆強":          ((mk <= -5) & (r30 >= 20), -7),
        "逆弱20-50":     ((mk >= 5) & (r30 <= -20) & (r30 > -50), 6),
        "逆弱50-100":    ((mk >= 5) & (r30 <= -50) & (r30 > -100), 8),
        "逆弱≤−100":     ((mk >= 5) & (r30 <= -100), 11),
        "對開盤3-5%":    ((dr > 3) & (dr <= 5), -9),
        "對開盤>5%":     (dr > 5, -15),
        "對開盤−3~−5%":  ((dr < -3) & (dr >= -5), 8),
        "對開盤<−5%":    (dr < -5, 12),
        "純機構":        (a10 & (d["b5n"] > 10) & (d["share5"] < 5) & (d["bigp30"] < 0) & (d["big5p"] < 0), 12),
        "蓄勢":          ((d["cmp"] < 0) & (d["bigsh30"] >= 10), 0),      # V2.2 無此項;V2.3 +4.5
        "倒貨":          ((d["cmp"] > 0) & (d["bigsh30"] <= -10), 0),     # V2.2 無此項;V2.3 −3.5
    }


#: 儀表板 V23_W 最終(含單調補格 過熱400-600/逆弱50-100、急跌≤−600 受上限 40)
V23_FINAL = {"散戶虛拉": -4.5, "勿追5m": -3.0, "散戶接跌": 2.5, "過熱200-300": -5.0, "過熱300-400": -8.5, "過熱400-600": -8.5,
             "急跌≤−600": 40.0, "逆弱20-50": 1.5, "逆弱50-100": 1.5, "逆弱≤−100": 5.5, "純機構": 9.0, "蓄勢": 4.5, "倒貨": -3.5}


def ols_cluster(X, y, cl):
    Xc = np.column_stack([np.ones(len(y)), X])
    XtX_inv = np.linalg.pinv(Xc.T @ Xc); b = XtX_inv @ Xc.T @ y; e = y - Xc @ b
    meat = np.zeros_like(XtX_inv)
    for gid in np.unique(cl):
        m = cl == gid; s = Xc[m].T @ e[m]; meat += np.outer(s, s)
    se = np.sqrt(np.diag(XtX_inv @ meat @ XtX_inv))
    return b[1:], se[1:]


def cl_t(v, cl):
    s = pd.Series(v).groupby(cl).mean(); return float(v.mean()), float(v.mean() / (s.std() / np.sqrt(len(s))))


def score_v22(X, names, wts, early):
    Xs = X.copy(); i0, i1 = names.index("散戶虛拉"), names.index("勿追5m")
    both = (X[:, i0] > 0) & (X[:, i1] > 0); Xs[both, i1] = 0          # 同源取一次
    s = Xs @ wts; s = np.where(early, s * 0.5, s); return np.clip(s, -40, 40)


def evaluate(d, s, lab):
    d = d.assign(s=s)
    out = [f"---- {lab}  n={len(d)}"]
    ic = d.groupby(["date", "bucket"]).apply(lambda g: g["s"].corr(g["ex60"], method="spearman") if g["s"].nunique() >= 3 else np.nan).dropna()
    icd = ic.groupby(level=0).mean()
    out.append(f"IC(60分,橫斷面 Spearman) 均 {ic.mean():+.3f}  日均 t{icd.mean()/(icd.std()/np.sqrt(len(icd))):+.1f}  (時點 {len(ic)})")
    for lo, hi in ((-99, -20), (-20, -10), (-10, -1e-9), (-1e-9, 1e-9), (1e-9, 10), (10, 20), (20, 99)):
        sub = d[(d["s"] > lo) & (d["s"] <= hi)]
        if len(sub) < 30: continue
        mu, t = cl_t(sub["ex60"].values, sub["date"].values)
        out.append(f"  ({lo:+3.0f},{hi:+3.0f}] n={len(sub):6d} 指派均 {sub['s'].mean():+6.1f} 實現超額 {mu:+6.1f} t{t:+5.1f} 原始 {sub['fwd60'].mean():+6.1f}")
    for th in (10, 15, 20):
        for side, m, sg in (("多", d["s"] >= th, 1), ("空", d["s"] <= -th, -1)):
            sub = d[m]
            if len(sub) < 30: out.append(f"  |分|≥{th} {side}: n<30"); continue
            mu, t = cl_t(sg * sub["ex60"].values, sub["date"].values)
            mud, td = cl_t(sg * sub["ex60_d"].dropna().values, sub.loc[sub["ex60_d"].notna(), "date"].values)
            raw = sg * sub["fwd60"].mean()
            first = (~(sub.index - 1).isin(sub.index)).sum()
            out.append(f"  |分|≥{th} {side}: n={len(sub):5d}(獨立{first:5d}) 超額 {mu:+6.1f} t{t:+4.1f} | 延遲1桶 {mud:+6.1f} t{td:+4.1f} | 原始 {raw:+6.1f} 扣{COST:.0f} {raw-COST:+6.1f}")
    nz = d[d["s"] != 0]
    sl = np.polyfit(nz["s"], nz["ex60"], 1)[0] if len(nz) > 100 else np.nan
    out.append(f"  校準斜率(實現~指派,非零分) {sl:.2f}")
    return "\n".join(out)


def main():
    d = build(); it = items(d); names = list(it)
    X = np.column_stack([it[k][0].fillna(False).astype(float).values for k in names])
    w22 = np.array([it[k][1] for k in names], float)
    y, dates = d["ex60"].values, d["date"].values
    is_ = (d["date"] <= "2026-06-30").values; early = (d["bucket"] < "10:00").values
    # ---- 擬合(只用 IS)兩時段 ----
    W = {}
    print("==== V2.3 權重(IS 聯合 OLS × 0.7,|cl-t|<2 歸零) ====")
    print(f"{'項':14s}{'V2.2':>7s} | {'10:00後 係數':>10s}{'t':>6s}{'→權重':>7s} | {'09:30-10 係數':>12s}{'t':>6s}{'→權重':>7s}")
    for slot, m in (("late", is_ & ~early), ("early", is_ & early)):
        b, se = ols_cluster(X[m], y[m], dates[m])
        W[slot] = np.array([round(SHRINK * bi * 2) / 2 if (abs(bi / si) >= TMIN and X[m][:, i].sum() >= 30) else 0.0
                            for i, (bi, si) in enumerate(zip(b, se))])
        W[slot + "_raw"] = (b, se)
    for i, k in enumerate(names):
        bl, sl_ = W["late_raw"]; be, se_ = W["early_raw"]
        print(f"{k:14s}{w22[i]:+7.1f} | {bl[i]:+10.1f}{bl[i]/sl_[i]:+6.1f}{W['late'][i]:+7.1f} | {be[i]:+12.1f}{be[i]/se_[i]:+6.1f}{W['early'][i]:+7.1f}")
    print("\nV23_W = {")
    for i, k in enumerate(names):
        if W["late"][i] or W["early"][i]:
            print(f'    "{k}": ({W["late"][i]:+.1f}, {W["early"][i]:+.1f}),')
    print("}  # (10:00 後, 09:30–10:00)")
    s22 = score_v22(X, names, w22, early)
    s23 = np.clip(np.where(early, X @ W["early"], X @ W["late"]), -40, 40)
    # 對照:V2.3 但不分時段(一律 late 權重、早盤 ×0.5)
    s23b = np.clip(np.where(early, (X @ W["late"]) * 0.5, X @ W["late"]), -40, 40)
    sfin = np.clip(X @ np.array([V23_FINAL.get(k, 0.0) for k in names]), -40, 40)
    for lab, m in (("IS", is_), ("OOS", ~is_)):
        print(f"\n======== {lab} ========")
        print(evaluate(d[m], s22[m], f"V2.2 {lab}"))
        print(evaluate(d[m], s23[m], f"V2.3 {lab}(逐項時段)"))
        print(evaluate(d[m], s23b[m], f"V2.3b {lab}(單一權重,早盤×0.5)"))
        print(evaluate(d[m], sfin[m], f"V2.3-final {lab}(儀表板 V23_FINAL,池化+壓縮)"))


if __name__ == "__main__":
    main()
