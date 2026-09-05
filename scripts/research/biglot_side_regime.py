#!/usr/bin/env python3
"""大戶多空未實現「哪一邊在賺」的盤中儀表 + 換邊訊號的前瞻性檢定。

儀表（策略要的兩件事）：
  1. 每 STEP 分鐘：多方部位未實現 / 空方部位未實現（各自除以名目 → bps，尺度無關）
     side = 多方bps − 空方bps；side>0 = 多方在賺、side<0 = 空方在賺
  2. 每個時點在領先那一邊，列出可下單的「代表檔」候選（依未實現 bps 排序）

檢定（決定這條策略能不能成立）：
  未實現本質上是 mark-to-market，所以「多方開始賺」幾乎等於「價格剛剛漲了」。
  唯一有意義的問題是：side 的變化**領先**價格，還是只是價格的鏡子？
  同期相關 vs 前瞻相關，兩個都算出來比。
"""
from __future__ import annotations
import argparse, json, sys
import numpy as np, pandas as pd
sys.path.insert(0, "src"); sys.path.insert(0, "scripts/research")
from stock_db import DATA_DIR
from biglot_unrealized import load, book  # noqa: E402


def grid(step, start="09:05", end="13:30"):
    s = int(start[:2]) * 60 + int(start[3:]); e = int(end[:2]) * 60 + int(end[3:])
    out = []
    while s <= e:
        out.append(f"{s//60:02d}:{s%60:02d}"); s += step
    return out


def day(date, thresh, step):
    df = load(date)
    big = df[(df.amt >= thresh) & (df.d != 0)]
    G = grid(step)
    rows = []
    for s, g in df.groupby("sym"):
        b = big[big.sym == s]
        if len(b) < 10:
            continue
        p0 = float(g.px.iloc[0])
        for mk in G:
            cut = mk + ":00"
            q = g[g.t < cut]; cum = b[b.t < cut]
            if not len(q) or not len(cum):
                continue
            P = float(q.px.iloc[-1])
            _, _, pos, avg, _, _ = book(list(zip(cum.d, cum.sz, cum.px)))
            rows.append(dict(mk=mk, sym=s, pos=pos, avg=avg, px=P, p0=p0,
                             unreal=pos * 1000 * (P - avg) if pos else 0.0,
                             notional=abs(pos) * 1000 * avg,
                             gap_pct=(P / avg - 1) * 100 if avg else np.nan))
    r = pd.DataFrame(rows); r["date"] = date
    return r, G


def side_series(r, G):
    out = []
    for mk in G:
        s = r[r.mk == mk]
        if s.empty:
            continue
        L = s[s.pos > 0]; S = s[s.pos < 0]
        lb = L.unreal.sum() / L.notional.sum() * 1e4 if L.notional.sum() else np.nan
        sb = S.unreal.sum() / S.notional.sum() * 1e4 if S.notional.sum() else np.nan
        bask = (s.px / s.p0 - 1).mean() * 1e4          # 等權籃子相對開盤
        out.append(dict(mk=mk, long_bps=lb, short_bps=sb, side=lb - sb,
                        long_amt=L.unreal.sum() / 1e4, short_amt=S.unreal.sum() / 1e4,
                        nL=len(L), nS=len(S), basket_bps=bask))
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=5_000_000)
    ap.add_argument("--step", type=int, default=5)
    a = ap.parse_args()
    V3 = {x["sid"]: x["name"] for x in json.loads(
        (DATA_DIR / "cache/pit_universe_tick/_hivol_universe_v3.json").read_text())["universe"]}
    info = pd.read_pickle("/tmp/stock_info.pkl")
    NM = dict(zip(info.stock_id.astype(str), info.stock_name))
    nm = lambda s: V3.get(s, NM.get(s, s))

    allr, alls = [], []
    for date in ("2026-09-03", "2026-09-04"):
        r, G = day(date, a.thresh, a.step)
        s = side_series(r, G); s["date"] = date
        allr.append(r); alls.append(s)
        print(f"\n{'='*104}\n### {date} 多空未實現儀表（bps = 該邊未實現 / 該邊名目）")
        print(f"{'時點':<7}{'多方bps':>9}{'空方bps':>9}{'side':>9}{'誰在賺':>7}"
              f"{'多方(萬)':>10}{'空方(萬)':>10}{'籃子bps':>9}   空方代表 / 多方代表")
        for _, x in s.iterrows():
            g = r[r.mk == x.mk]
            SS = g[g.pos < 0].assign(bps=lambda d: d.unreal / d.notional * 1e4).nlargest(1, "bps")
            LL = g[g.pos > 0].assign(bps=lambda d: d.unreal / d.notional * 1e4).nlargest(1, "bps")
            rep = (f"{nm(SS.sym.iloc[0])} {SS.bps.iloc[0]:+.0f}" if len(SS) else "—") + " / " + \
                  (f"{nm(LL.sym.iloc[0])} {LL.bps.iloc[0]:+.0f}" if len(LL) else "—")
            print(f"{x.mk:<7}{x.long_bps:>9.1f}{x.short_bps:>9.1f}{x.side:>9.1f}"
                  f"{('多' if x.side>0 else '空'):>7}{x.long_amt:>10,.0f}{x.short_amt:>10,.0f}"
                  f"{x.basket_bps:>9.1f}   {rep}")
    S = pd.concat(alls, ignore_index=True)

    print(f"\n{'='*104}\n### 關鍵檢定：side 是領先價格，還是價格的鏡子？")
    rows = []
    for date, g in S.groupby("date"):
        g = g.sort_values("mk").reset_index(drop=True)
        g["dside"] = g.side.diff()
        g["r_now"] = g.basket_bps.diff()                    # 同期（上一格到這一格）
        for k in (1, 2, 3, 6):
            g[f"r_fwd{k}"] = g.basket_bps.shift(-k) - g.basket_bps
        rows.append(g)
    g = pd.concat(rows).dropna(subset=["dside", "r_now"])
    print(f"  樣本 {len(g)} 個 {a.step} 分鐘格（2 天）")
    print(f"  同期   corr(Δside, 同格籃子報酬)          = {g.dside.corr(g.r_now):+.3f}")
    for k in (1, 2, 3, 6):
        c = g[f"r_fwd{k}"].corr(g.dside)
        n = g[f"r_fwd{k}"].notna().sum()
        t = c * np.sqrt(max(n - 2, 1) / max(1 - c * c, 1e-9))
        print(f"  前瞻+{k*a.step:>2}分 corr(Δside, 之後籃子報酬)      = {c:+.3f}  (n={n}, t={t:+.2f})")
    print(f"  水準值 corr(side, 之後 {a.step}分報酬)         = "
          f"{g.side.corr(g['r_fwd1']):+.3f}")


if __name__ == "__main__":
    main()
