#!/usr/bin/env python3
"""檢定「大戶在賺的標的會延續」——橫斷面、剔除大盤共同成分。

每個 5 分鐘時點 t、每檔 i：
  signal  = 未實現 bps（= sign(淨部位) × (市價/平均成本 − 1) × 1e4）
  bet     = sign(淨部位)                        ← 跟著大戶的方向下單
  payoff  = bet × (該檔 t→t+k 報酬 − 同時點全體平均報酬)   ← 超額,已剔除大盤
若「賺錢的會延續」成立：signal 高的組,payoff 應顯著為正。
"""
from __future__ import annotations
import argparse, json, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, "scripts/research")
from stock_db import DATA_DIR
from biglot_unrealized import load, book  # noqa: E402


def build(date, thresh, step):
    df = load(date); big = df[(df.amt >= thresh) & (df.d != 0)]
    s0 = 9 * 60 + 5; e0 = 13 * 60 + 30
    G = [f"{m//60:02d}:{m%60:02d}" for m in range(s0, e0 + 1, step)]
    rows = []
    for s, g in df.groupby("sym"):
        b = big[big.sym == s]
        if len(b) < 10:
            continue
        for mk in G:
            cut = mk + ":00"
            q = g[g.t < cut]; cum = b[b.t < cut]
            if not len(q) or not len(cum):
                continue
            P = float(q.px.iloc[-1])
            _, _, pos, avg, _, _ = book(list(zip(cum.d, cum.sz, cum.px)))
            if pos == 0:
                continue
            rows.append(dict(date=date, mk=mk, sym=s, px=P, pos=pos, avg=avg,
                             sig=np.sign(pos) * (P / avg - 1) * 1e4,
                             notional=abs(pos) * 1000 * avg))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=5_000_000)
    ap.add_argument("--step", type=int, default=5)
    a = ap.parse_args()
    d = pd.concat([build(x, a.thresh, a.step) for x in ("2026-09-03", "2026-09-04")],
                  ignore_index=True)
    d["ts"] = d.date + " " + d.mk
    d = d.sort_values(["sym", "ts"])
    for k in (1, 3, 6):
        d[f"f{k}"] = d.groupby(["date", "sym"]).px.shift(-k) / d.px - 1
        d[f"x{k}"] = d[f"f{k}"] - d.groupby("ts")[f"f{k}"].transform("mean")   # 剔除大盤
        d[f"pay{k}"] = np.sign(d.pos) * d[f"x{k}"] * 1e4
    print(f"樣本 {len(d)} 個 (檔,時點) / {d.sym.nunique()} 檔 / {d.ts.nunique()} 個時點 / 2 天")
    print(f"signal 分佈 bps：p10 {d.sig.quantile(.1):.0f}  中位 {d.sig.median():.0f}  "
          f"p90 {d.sig.quantile(.9):.0f}")

    def clt(s, col):
        g = s.groupby("ts")[col].mean()
        if len(g) < 5:
            return np.nan, np.nan
        return g.mean(), g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))

    for k, lab in [(1, f"+{a.step}分"), (3, f"+{3*a.step}分"), (6, f"+{6*a.step}分")]:
        s = d.dropna(subset=[f"pay{k}"]).copy()
        s["q"] = s.groupby("ts").sig.transform(lambda x: pd.qcut(x, 5, labels=False, duplicates="drop"))
        print(f"\n### 持有 {lab}：依『未實現 bps』分五組的跟單超額（bps，時點 cluster t）")
        print(f"{'組':<6}{'n':>7}{'signal中位':>11}{'超額bps':>10}{'t':>7}")
        for i in range(5):
            g = s[s.q == i]
            m, t = clt(g, f"pay{k}")
            print(f"{'Q'+str(i+1):<6}{len(g):>7}{g.sig.median():>11.0f}{m:>10.2f}{t:>7.2f}")
        hi = s[s.q == 4].copy(); lo = s[s.q == 0].copy()
        both = pd.concat([hi.assign(v=hi[f"pay{k}"]), lo.assign(v=-lo[f"pay{k}"])])
        m, t = clt(both, "v")
        print(f"  Q5−Q1 價差 {m*2:+.2f} bps  t={t:+.2f}")
        m, t = clt(s, f"pay{k}")
        print(f"  全體跟單（不分組）{m:+.2f} bps  t={t:+.2f}")
        # 只看有賺的
        w = s[s.sig > 0]; l = s[s.sig < 0]
        mw, tw = clt(w, f"pay{k}"); ml, tl = clt(l, f"pay{k}")
        print(f"  只跟『大戶在賺』的 (sig>0, n={len(w)})  {mw:+.2f} bps  t={tw:+.2f}")
        print(f"  只跟『大戶在賠』的 (sig<0, n={len(l)})  {ml:+.2f} bps  t={tl:+.2f}")


if __name__ == "__main__":
    main()
