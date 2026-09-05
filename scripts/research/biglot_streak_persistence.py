#!/usr/bin/env python3
"""檢定「連續賺錢的,下一段也會賺」——用連勝格數(streak)分組,不是用浮盈大小分組。

每 5 分鐘、每檔：
  step   = sign(前一格淨部位) × (該格報酬 − 同時點全體平均報酬)   ← 大戶方向的超額,剔除大盤
  streak = 到 t 為止連續 step>0 的格數（連勝）
  payoff = sign(淨部位) × (t→t+k 超額報酬)
問題：streak 越長,payoff 是否越大？
"""
from __future__ import annotations
import argparse, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, "scripts/research")
from biglot_winner_persistence import build  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=5_000_000)
    ap.add_argument("--step", type=int, default=5)
    a = ap.parse_args()
    d = pd.concat([build(x, a.thresh, a.step) for x in ("2026-09-03", "2026-09-04")],
                  ignore_index=True)
    d["ts"] = d.date + " " + d.mk
    d = d.sort_values(["date", "sym", "mk"]).reset_index(drop=True)
    g = d.groupby(["date", "sym"])
    d["r"] = g.px.pct_change()
    d["prev_pos"] = g.pos.shift(1)
    d["xr"] = d.r - d.groupby("ts").r.transform("mean")          # 剔除大盤
    d["step_pl"] = np.sign(d.prev_pos) * d.xr * 1e4              # 這一格大戶方向的超額
    # 連勝計數
    streak = []
    cur = {}
    for _, row in d.iterrows():
        key = (row.date, row.sym)
        c = cur.get(key, 0)
        streak.append(c)
        if row.step_pl == row.step_pl:
            cur[key] = c + 1 if row.step_pl > 0 else 0
    d["streak"] = streak
    for k in (1, 3, 6):
        d[f"f{k}"] = g.px.shift(-k) / d.px - 1
        d[f"x{k}"] = d[f"f{k}"] - d.groupby("ts")[f"f{k}"].transform("mean")
        d[f"pay{k}"] = np.sign(d.pos) * d[f"x{k}"] * 1e4
    d = d.dropna(subset=["step_pl"])
    print(f"樣本 {len(d)} 個 (檔,時點)；連勝分佈：" +
          "  ".join(f"{i}格={int((d.streak==i).sum())}" for i in range(0, 6)) +
          f"  ≥6格={int((d.streak>=6).sum())}")

    def clt(s, col):
        gg = s.groupby("ts")[col].mean().dropna()
        if len(gg) < 5:
            return np.nan, np.nan, 0
        return gg.mean(), gg.mean() / (gg.std(ddof=1) / np.sqrt(len(gg))), len(gg)

    for k, lab in [(1, f"+{a.step}分"), (3, f"+{3*a.step}分"), (6, f"+{6*a.step}分")]:
        print(f"\n### 持有 {lab}：依『已連勝幾格』分組的跟單超額（bps）")
        print(f"{'連勝':<8}{'n':>7}{'超額bps':>10}{'t':>7}   逐日")
        for s_ in [0, 1, 2, 3, 4, 5]:
            sub = d[d.streak == s_] if s_ < 5 else d[d.streak >= 5]
            if len(sub) < 40:
                continue
            m, t, n = clt(sub, f"pay{k}")
            per = []
            for date, gg in sub.groupby("date"):
                m2, t2, _ = clt(gg, f"pay{k}")
                per.append(f"{date[5:]} {m2:+.1f}(t={t2:+.1f})")
            lab_s = f"{s_} 格" if s_ < 5 else "≥5 格"
            print(f"{lab_s:<8}{len(sub):>7}{m:>10.2f}{t:>7.2f}   " + "  ".join(per))
        hi = d[d.streak >= 3]; lo = d[d.streak == 0]
        both = pd.concat([hi.assign(v=hi[f"pay{k}"]), lo.assign(v=-lo[f"pay{k}"])])
        m, t, _ = clt(both, "v")
        print(f"  (連勝≥3) − (連勝0) 價差 {m*2:+.2f} bps  t={t:+.2f}")
        # 連勝是否比隨機更常出現（tape 有沒有趨勢性）
    obs = d.groupby("streak").size()
    p = (d.step_pl > 0).mean()
    print(f"\n### 連勝本身是否比隨機更常見（step_pl>0 的機率 {p:.3f}）")
    for i in range(1, 6):
        exp = len(d) * (p ** i) * (1 - p)
        print(f"  連勝 {i} 格：實測 {int(obs.get(i,0))}  隨機期望 {exp:.0f}  "
              f"比值 {obs.get(i,0)/exp if exp else float('nan'):.2f}")


if __name__ == "__main__":
    main()
