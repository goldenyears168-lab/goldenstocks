#!/usr/bin/env python3
"""關鍵分點進場訊號——忠實小哥原做法的事件研究（唯讀）。出處：book/part_03.md §3.5、part_02.md 開布林三條件。

分點資料收盤後才有 → 訊號日 t，t+1 開盤進場（PIT：t 日旗標只用 ≤t 資料）。
變體 A「關鍵分點低檔建倉」：價格位置在 3 年區間右下角（t 收盤在過去 756 交易日的百分位 ≤0.25；敏感度 ≤0.10/≤0.40）
                           ＋ 小哥點名的關鍵分點當日買超前5且淨買≥1%量。
變體 B「開布林」：帶寬(上軌/下軌−1，20日±2σ) t-1 <5% ＋ t 帶寬放大且收盤>上軌 ＋ 關鍵分點大買（B2：以任一買超前5名且≥3%量的「主力」代替，全流動性母體）。
結果：t+1開→t+h收(h=5,20)；基準＝同樣條件但無關鍵分點事件的日子（同一批股票）；日期叢集 SE；事件同股間隔<h日者只留首筆。
另報 A 的原作者出場：該分點自進場後累計淨部位 ≤0 的次一日開盤出場（上限60日）。
"""
import json, pickle, re, sqlite3
from pathlib import Path
import numpy as np, pandas as pd, statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parents[2]
DB = Path.home() / "goldenstocks-data/data/stocks.db"
OUTD = Path.home() / "goldenstocks-data/research/xiaoge_flip_habit"
ROLES = ("關鍵分點", "地緣", "高手", "公司派")
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c.execute("pragma query_only=1")


def bars(sc):
    p = pd.read_sql_query("select trade_date d, open, high, close from stock_daily_bars where stock_id=? and source in ('twse_mi_index','tpex_daily') "
                          "and trade_date>='2021-01-01' and trade_date<='2026-07-31'", c, params=(sc,)).drop_duplicates("d").sort_values("d").reset_index(drop=True)
    if len(p) < 300: return None
    cl = p.close; ma = cl.rolling(20).mean(); sd = cl.rolling(20).std(ddof=0)
    up, lo = ma + 2 * sd, ma - 2 * sd; bw = up / lo - 1
    p["pos3y"] = cl.rolling(756, min_periods=500).apply(lambda a: (a[:-1] < a[-1]).mean(), raw=True)
    p["open_bb"] = ((bw.shift(1) < 0.05) & (bw > bw.shift(1)) & (cl > up)).astype(int)
    for h in (5, 20):
        p[f"r{h}"] = cl.shift(-h) / p.open.shift(-1) - 1
    p["g"] = (p.open.shift(-1) / cl - 1).abs()
    # 作者出場（part_02 §開了以後怎麼做）：沿上軌不賣；整根K棒離開上通道(high<上軌)→賣(短抱X1)；長抱抱到月線下彎(收盤<MA20且MA20下彎)→賣(X2)。
    # 訊號日 i、i+1 開盤進；自 i+1 起掃描，觸發日 j 隔日(j+1)開盤出；上限120日，未出場者排除。
    hi, op, mav = p.high.values, p.open.values, ma.values
    upv, clv = up.values, cl.values
    x1 = np.full(len(p), np.nan); x2 = np.full(len(p), np.nan); n = len(p)
    for i in np.flatnonzero(p.open_bb.values == 1):
        for arr, cond in ((x1, lambda j: hi[j] < upv[j]), (x2, lambda j: clv[j] < mav[j] and mav[j] < mav[j - 1])):
            for j in range(i + 1, min(i + 121, n - 1)):
                if cond(j):
                    arr[i] = op[j + 1] / op[i + 1] - 1; break
    p["rx1"], p["rx2"] = x1, x2
    return p


def main():
    cfg = json.loads((ROOT / "config/xiaoge_named_branches.json").read_text(encoding="utf-8"))
    pairs = {}
    for e in cfg["branches"]:
        if e.get("role") in ROLES:
            for s in e["stocks"]:
                for m in re.findall(r"\((\d{4})\)", s):
                    for t in e["ids"]: pairs.setdefault(m, {})[t] = e["role"]
    rows = []
    for sc, keys in sorted(pairs.items()):
        p = bars(sc)
        if p is None: continue
        tp = pd.read_sql_query("select trade_date d, securities_trader_id br, buy, sell from stock_broker_branch_daily where stock_id=? and trade_date between '2024-07-01' and '2026-07-16'", c, params=(sc,))
        if tp.empty: continue
        tp[["buy", "sell"]] = tp[["buy", "sell"]].fillna(0); tp["net"] = tp.buy - tp.sell
        vol = tp.groupby("d")["buy"].sum(); ok = vol[vol > 0].index
        tp = tp[tp.d.isin(ok)]; tp["rk"] = tp.groupby("d")["net"].rank(ascending=False, method="first")
        ev = tp[tp.br.isin(keys) & (tp.rk <= 5) & (tp.net >= 0.01 * tp.d.map(vol))]
        key_days = set(ev.d)
        m = p[p.d.isin(ok)].copy(); m["stock"] = sc; m["key"] = m.d.isin(key_days).astype(int)
        # A 出場：事件日起該分點累計淨部位
        tpk = tp[tp.br.isin(keys)].pivot_table(index="d", columns="br", values="net", aggfunc="sum").fillna(0)
        dates = list(p.d); idx = {d: i for i, d in enumerate(dates)}
        ex = {}
        for r in ev.itertuples():
            i = idx.get(r.d); 
            if i is None or i + 2 >= len(p): continue
            cum, j = 0, i
            while j < len(p) and j - i <= 60:
                dj = dates[j]; cum += float(tpk.loc[dj, r.br]) if dj in tpk.index else 0
                if j > i and cum <= 0: break
                j += 1
            jx = min(j + 1, len(p) - 1)
            ex[(r.d)] = p.open[jx] / p.open[i + 1] - 1 if jx > i + 1 else np.nan
        m["rA"] = m.d.map(ex)
        rows.append(m); print(sc, int(m.key.sum()), flush=True)
    df = pd.concat(rows, ignore_index=True); df = df[(df.g < 0.12) & df.r5.notna() & df.pos3y.notna()]
    out = {}

    def test(sub, name):
        res = {"n_ev": int(sub.key.sum()), "n_base": int((sub.key == 0).sum())}
        for h in ("r5", "r20", "rx1", "rx2"):
            s = sub.dropna(subset=[h]).sort_values(["stock", "d"]).reset_index(drop=True)
            # 事件去重疊
            keep = []; last = {}
            for r in s[s.key == 1].itertuples():
                if r.stock not in last or (pd.Timestamp(r.d) - last[r.stock]).days >= (7 if h in ("r5", "rx1", "rx2") else 28):
                    keep.append(r.Index); last[r.stock] = pd.Timestamp(r.d)
            s1 = pd.concat([s[s.key == 0], s.loc[keep]]) if keep else s[s.key == 0]
            if s1.key.sum() < 8: res[h] = {"n": int(s1.key.sum())}; continue
            s1 = s1.replace([np.inf, -np.inf], np.nan).dropna(subset=[h]).copy()
            s1["yy"] = s1[h] - s1.groupby("stock")[h].transform("mean"); s1["kk"] = s1.key - s1.groupby("stock")["key"].transform("mean")
            r = smf.ols("yy ~ kk - 1", s1).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(s1.d)[0]})
            res[h] = {"n": int(s1.key.sum()), "ev_mean_pct": float(s1[s1.key == 1][h].mean() * 100), "base_mean_pct": float(s1[s1.key == 0][h].mean() * 100),
                      "diff_pp": float(r.params["kk"] * 100), "t": float(r.tvalues["kk"]), "ev_median_pct": float(s1[s1.key == 1][h].median() * 100), "hit": float((s1[s1.key == 1][h] > 0).mean())}
        out[name] = res
    for lim in (0.10, 0.25, 0.40):
        test(df[df.pos3y <= lim], f"A_低檔pos3y<={lim}")
    a = df[(df.pos3y <= 0.25) & (df.key == 1)].dropna(subset=["rA"])
    out["A_作者出場(關鍵分點累計淨部位≤0)"] = {"n": int(len(a)), "mean_pct": float(a.rA.mean() * 100) if len(a) else None, "median_pct": float(a.rA.median() * 100) if len(a) else None, "hit": float((a.rA > 0).mean()) if len(a) else None}
    test(df[df.open_bb == 1], "B_開布林×關鍵分點(22股)")
    # B2：全流動性母體，主力＝任一買超前5且≥3%量
    d = pickle.load(open(OUTD / "events.pkl", "rb")); ev, cal = d["events"], d["cal"]
    ev = ev[ev["in_p"] & (ev["rank"] <= 5) & (ev["share"] >= 0.03)].copy(); ev["d"] = ev["idx"].map(lambda i: cal[i])
    main = set(zip(ev.stock_id, ev.d)); sts = sorted(ev.stock_id.unique()); fr = []
    for k, sc in enumerate(sts):
        p = bars(sc)
        if p is None: continue
        p = p[(p.d >= "2024-10-01") & (p.d <= "2026-07-15")].copy(); p["stock"] = sc
        p["key"] = [int((sc, x) in main) for x in p.d]; fr.append(p[p.open_bb == 1])
    b2 = pd.concat(fr, ignore_index=True); b2 = b2[(b2.g < 0.12) & b2.r5.notna()]
    test(b2, "B2_開布林×主力大買(全母體)")
    (OUTD / "key_faithful.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))

main()
