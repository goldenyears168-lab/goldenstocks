#!/usr/bin/env python3
"""組合技檢定（唯讀）：小哥的因子是組合使用，不是單因子切分。用走動式（walk-forward）樣本外評估，比較
  M0 基準（只用日期均值）、單因子、加法線性模型（無交互）、梯度提升樹（可學交互）；再用淺層 CART 找可讀的組合規則並做多重檢定校正。
樣本：T 日漲幅≥5% 且 PIT 流動性（前20日均成交值≥3千萬）的個股日，2024-10～2026-07-15（全市場分點 tape 期間）。
結果變數：T+1 開→收 oc（開盤放空到收盤的對立面）；y = oc − 當日同母體均值（扣掉「大漲隔天整體會回吐」的基準）。
特徵（全部 PIT：只用 ≤T 的日線與分點；gap 是 T+1 開盤才知道，只在「開盤後」模型使用）。
走動式：每月為一個測試窗，訓練＝測試窗起日前 5 個交易日（purge）以前的全部資料；至少 6 個月訓練。
指標：每日 rank-IC（預測 vs y）的均值與日期 t；最低預測五分位（做空候選）的 y 平均；扣 0.3% 成本後的做空淨報酬。
輸出：${DATA}/research/xiaoge_flip_habit/combo_results.json 與 combo_dataset.parquet
"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

OUT = Path.home() / "goldenstocks-data/research/xiaoge_flip_habit"
START, END = "2024-10-01", "2026-07-15"
COST = 0.3  # % 來回（當沖稅 0.15%＋手續費＋滑價約略）


def build() -> pd.DataFrame:
    d = pickle.load(open(OUT / "events.pkl", "rb")); ev, cal = d["events"], d["cal"]
    ev = ev[(ev["rank"] <= 5) & (ev["share"] >= 0.03)].copy()
    ev["date"] = ev["idx"].map(lambda i: cal[i])
    ev["rate"] = np.where(ev.n90_p > 0, ev.A70_90_p / ev.n90_p.clip(lower=1), 0.0)
    ev["is_h"] = ev.A70_90_p >= 1; ev["is_hs"] = ev.A70_90_p >= 2
    g = ev.groupby(["stock_id", "date"])
    chip = pd.DataFrame({
        "n_big": g.size(), "top1_share": g.share.max(), "sum_share": g.share.sum(),
        "n_habit": g.is_h.sum(), "n_habit_strong": g.is_hs.sum(),
        "habit_share": ev[ev.is_h].groupby(["stock_id", "date"]).share.sum(),
        "max_rate": ev[ev.n90_p >= 2].groupby(["stock_id", "date"]).rate.max(),
        "hist_n": g.n90_p.max()}).reset_index().fillna({"habit_share": 0.0, "max_rate": 0.0})
    chip["flag_of_buys"] = chip.habit_share / chip.sum_share
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    b = pd.read_sql_query("select stock_id, trade_date d, open, high, low, close, volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') "
                          "and trade_date between '2024-04-01' and '2026-07-31' and length(stock_id)=4 and stock_id glob '[1-9][0-9][0-9][0-9]'", c).drop_duplicates(["stock_id", "d"])
    sh = dict(c.execute("select stock_id,shares_issued from stock_shareholding_daily where trade_date=(select max(trade_date) from stock_shareholding_daily)").fetchall())
    b = b.sort_values(["stock_id", "d"]).reset_index(drop=True)
    g = b.groupby("stock_id")
    cl = b.close; ma = g.close.transform(lambda s: s.rolling(20).mean()); sd = g.close.transform(lambda s: s.rolling(20).std(ddof=0))
    b["ret"] = g.close.pct_change(); b["ret5"] = g.close.pct_change(5)
    b["bias20"] = cl / ma - 1; b["bw"] = (4 * sd) / (ma - 2 * sd).clip(lower=0.01)
    b["boll_pos"] = (cl - (ma - 2 * sd)) / (4 * sd).replace(0, np.nan)
    b["vr"] = b.volume / g.volume.transform(lambda s: s.shift(1).rolling(20).mean())
    b["tv20"] = (b.close * b.volume).groupby(b.stock_id).transform(lambda s: s.shift(1).rolling(20).mean())
    b["turn"] = b.volume / b.stock_id.map(sh) * 100
    b["lock"] = ((b.close >= b.high - 1e-9) & (b.ret >= 0.094)).astype(int)
    b["upper_shadow"] = (b.high - b.close) / (b.high - b.low).replace(0, np.nan)
    b["range_T"] = (b.high - b.low) / g.close.shift(1)
    b["lu5"] = g.ret.transform(lambda s: (s.shift(1) >= 0.094).rolling(5).sum())
    up = (b.ret > 0.02).astype(int)
    b["streak"] = up.groupby(b.stock_id).transform(lambda s: s.groupby((s == 0).cumsum()).cumsum())
    b["logp"] = np.log(b.close)
    b["gap"] = g.open.shift(-1) / b.close - 1
    b["oc"] = g.close.shift(-1) / g.open.shift(-1) - 1
    b["cc"] = g.close.shift(-1) / b.close - 1
    b["adv"] = g.high.shift(-1) / g.open.shift(-1) - 1           # 放空的不利excursion（最高相對開盤）
    b["N"] = g.d.shift(-1)
    s = b[(b.d >= START) & (b.d <= END) & (b.ret >= 0.05) & (b.tv20 >= 3.0e7)].dropna(subset=["oc", "gap", "boll_pos", "vr", "ret5", "bias20", "bw"]).copy()
    s = s[(s.gap.abs() < 0.12) & (s.oc.abs() < 0.12)]
    s["breadth"] = s.groupby("d").stock_id.transform("size")
    s = s.merge(chip.rename(columns={"date": "d"}), on=["stock_id", "d"], how="left")
    for k in ("n_big", "top1_share", "sum_share", "n_habit", "n_habit_strong", "habit_share", "max_rate", "flag_of_buys", "hist_n"):
        s[k] = s[k].fillna(0.0)
    s["y"] = (s.oc - s.groupby("d").oc.transform("mean")) * 100
    s["oc_pct"] = s.oc * 100; s["gap_pct"] = s.gap * 100
    return s.reset_index(drop=True)


FEATS_PRE = ["ret", "ret5", "lock", "streak", "lu5", "bias20", "boll_pos", "bw", "vr", "turn", "upper_shadow", "range_T", "logp", "breadth",
             "n_big", "top1_share", "sum_share", "n_habit", "n_habit_strong", "habit_share", "max_rate", "flag_of_buys", "hist_n"]
CHIP_ONLY = ["n_big", "top1_share", "sum_share", "n_habit", "n_habit_strong", "habit_share", "max_rate", "flag_of_buys", "hist_n"]
SINGLE = ["n_habit", "habit_share", "flag_of_buys", "turn", "lock", "boll_pos", "vr", "streak", "gap_pct"]


def rank_ic(df: pd.DataFrame, col: str) -> tuple[float, float, int]:
    ics = []
    for d, g in df.groupby("d"):
        if len(g) >= 8 and g[col].nunique() > 1:
            ics.append(g[[col, "y"]].rank().corr().iloc[0, 1])
    ics = np.array([x for x in ics if x == x])
    if len(ics) < 5:
        return float("nan"), float("nan"), len(ics)
    return float(ics.mean()), float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))), len(ics)


def walk_forward(s: pd.DataFrame, feats: list[str], model_kind: str) -> pd.Series:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    days = sorted(s.d.unique()); months = sorted({d[:7] for d in days})
    pred = pd.Series(np.nan, index=s.index)
    for m in months[6:]:
        test = s[s.d.str[:7] == m]
        if test.empty:
            continue
        cutoff_i = max(0, days.index(test.d.min()) - 5)
        train = s[s.d < days[cutoff_i]]
        if len(train) < 800:
            continue
        X, Xt = train[feats].to_numpy(float), test[feats].to_numpy(float)
        # 重尾：各特徵以訓練集 1/99 分位截尾
        med = np.nanmedian(X, axis=0)
        X = np.where(np.isnan(X), med, X); Xt = np.where(np.isnan(Xt), med, Xt)
        lo, hi = np.nanpercentile(X, 1, axis=0), np.nanpercentile(X, 99, axis=0)
        X, Xt = np.clip(X, lo, hi), np.clip(Xt, lo, hi)
        if model_kind == "ridge":
            sc = StandardScaler().fit(X); mdl = Ridge(alpha=300.0).fit(sc.transform(X), train.y)
            pred[test.index] = mdl.predict(sc.transform(Xt))
        else:
            mdl = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.04, max_iter=150, min_samples_leaf=60, l2_regularization=10.0, random_state=0).fit(X, train.y)
            pred[test.index] = mdl.predict(Xt)
    return pred


def evaluate(s: pd.DataFrame, pred: pd.Series, name: str) -> dict:
    t = s.assign(p=pred).dropna(subset=["p"])
    ic, tt, nd = rank_ic(t, "p")
    out = {"model": name, "n": int(len(t)), "days": nd, "rank_ic": ic, "ic_t": tt}
    def q_stats(sign):                      # sign=+1: 預測最低的 20%（做空候選）；-1: 預測最高
        r = []
        for d, g in t.groupby("d"):
            if len(g) < 10: continue
            k = max(2, len(g) // 5)
            gg = g.nsmallest(k, "p") if sign > 0 else g.nlargest(k, "p")
            r.append((gg.oc_pct.mean(), gg.y.mean(), len(gg)))
        a = np.array(r)
        return a
    low = q_stats(+1)
    if len(low):
        y = low[:, 1]; out["q_low_oc_pct"] = float(low[:, 0].mean()); out["q_low_excess_pct"] = float(y.mean()); out["q_low_t"] = float(y.mean() / (y.std(ddof=1) / np.sqrt(len(y))))
        net = -low[:, 0] - COST
        out["short_net_pct_per_day"] = float(net.mean()); out["short_net_t"] = float(net.mean() / (net.std(ddof=1) / np.sqrt(len(net)))); out["short_hit_days"] = float((net > 0).mean())
    return out


def cart_rules(s: pd.DataFrame, feats: list[str]) -> list[dict]:
    """淺層 CART（深度3）在『前 60% 日期』學組合規則，在『後 40%』驗證並做 BH 校正；回傳可讀規則。"""
    from sklearn.tree import DecisionTreeRegressor, export_text
    from scipy import stats
    days = sorted(s.d.unique()); cut = days[int(len(days) * 0.6)]
    tr, te = s[s.d < cut], s[s.d >= cut]
    X = tr[feats].to_numpy(float)
    tree = DecisionTreeRegressor(max_depth=3, min_samples_leaf=80, random_state=0).fit(X, tr.y)
    leaf_tr, leaf_te = tree.apply(X), tree.apply(te[feats].to_numpy(float))
    txt = export_text(tree, feature_names=feats)
    res = []
    for lf in sorted(set(leaf_tr)):
        a, b = tr[leaf_tr == lf], te[leaf_te == lf]
        if len(b) < 20: continue
        t_, p_ = stats.ttest_1samp(b.y, 0.0)
        res.append({"leaf": int(lf), "n_train": int(len(a)), "train_y": float(a.y.mean()), "n_test": int(len(b)), "test_y": float(b.y.mean()), "test_oc": float(b.oc_pct.mean()), "t": float(t_), "p": float(p_)})
    ps = sorted(r["p"] for r in res)
    for r in res:
        rank = ps.index(r["p"]) + 1; r["bh_q"] = float(min(1.0, r["p"] * len(res) / rank))
    return {"cut": cut, "tree": txt, "leaves": res}


def main():
    f = OUT / "combo_dataset.parquet"
    s = pd.read_parquet(f) if (f.exists() and "--rebuild" not in sys.argv) else build()
    s.to_parquet(f, index=False)
    print(f"樣本 {len(s)} 個股日，{s.d.nunique()} 天（{s.d.min()}～{s.d.max()}）；其中有分點大買者 {int((s.n_big > 0).sum())}；有隔日沖慣犯者 {int((s.n_habit > 0).sum())}；oc 均值 {s.oc_pct.mean():.2f}%", flush=True)
    res = {"n": int(len(s)), "days": int(s.d.nunique()), "models": [], "single": [], "cart": {}}
    zero = pd.Series(0.0, index=s.index)
    # 單因子（full-sample 秩 IC，描述用）
    for k in SINGLE:
        ic, t, nd = rank_ic(s, k); res["single"].append({"feat": k, "ic": ic, "t": t, "days": nd}); print(f"單因子 {k:<14} 全樣本 rank-IC {ic:+.3f} (t {t:+.1f}, {nd}天)", flush=True)
    for name, feats, kind in (("加法線性（價量＋籌碼，無交互）", FEATS_PRE, "ridge"), ("樹模型（價量＋籌碼，可學交互）", FEATS_PRE, "gbm"),
                              ("樹模型：只用籌碼", CHIP_ONLY, "gbm"), ("樹模型：只用價量", [x for x in FEATS_PRE if x not in CHIP_ONLY], "gbm"),
                              ("樹模型：價量＋籌碼＋開盤跳空（開盤後）", FEATS_PRE + ["gap_pct"], "gbm"), ("加法線性：＋開盤跳空", FEATS_PRE + ["gap_pct"], "ridge")):
        p = walk_forward(s, feats, kind)
        e = evaluate(s, p, name); res["models"].append(e)
        print(f"{name:<30} n={e['n']} 天={e['days']} IC {e['rank_ic']:+.3f} (t {e['ic_t']:+.1f}) | 最低20%預測 oc {e.get('q_low_oc_pct', float('nan')):+.2f}% 超額 {e.get('q_low_excess_pct', float('nan')):+.2f}% (t {e.get('q_low_t', float('nan')):+.1f}) | 做空淨(扣{COST}%) {e.get('short_net_pct_per_day', float('nan')):+.2f}%/日 t {e.get('short_net_t', float('nan')):+.1f} 勝日 {e.get('short_hit_days', float('nan')):.0%}", flush=True)
    res["cart"] = cart_rules(s, FEATS_PRE + ["gap_pct"])
    print("\nCART 組合規則（深度3；前60%日期學、後40%驗；BH 校正）：\n" + res["cart"]["tree"])
    for r in sorted(res["cart"]["leaves"], key=lambda r: r["bh_q"]):
        print(f"  葉{r['leaf']}: 訓練 n={r['n_train']} y={r['train_y']:+.2f} | 驗證 n={r['n_test']} y={r['test_y']:+.2f} oc={r['test_oc']:+.2f}% t={r['t']:+.1f} BH q={r['bh_q']:.3f}")
    (OUT / "combo_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
