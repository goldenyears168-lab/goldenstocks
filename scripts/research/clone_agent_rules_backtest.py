#!/usr/bin/env python3
"""依「規則版本」(config/clone_agent_rules.json) 做端到端回測:全部代理人選股 → 名單 → 保證金/委託簿排隊/手續費稅 → 逐筆帳。

輸入是 clone_agent_day_backtest.py 逐日輸出的 CSV(排隊模擬已在裡面);本程式負責套名單、保證金、順序、成本、
基準對照、集中度與敏感度,輸出 JSON(給 /agentrules 頁讀)+ 逐筆 CSV。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_agent_day_backtest as db  # noqa: E402
import clone_agent_margin_backtest as mb  # noqa: E402
from clone_agent.rules_version import label, load_rules, universe_sids  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
OUT = Path.home() / "goldenstocks-data/data/clone_agent"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"]
OOS_DAYS = {"2026-10-05"}


def load_csvs(prefix: str) -> pd.DataFrame:
    fs = [S / f"{prefix}{d}.csv" for d in DAYS]
    dfs = [pd.read_csv(f) for f in fs if f.exists()]
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def simulate(df, mode, cap, fee, policy="fcfs", one=False, close=None):
    if close is not None:
        mb.CLOSE = close
    items, skipped = mb.run(df, mode, cap, None, policy, one)
    mb.CLOSE = 13 * 3600 + 45 * 60
    return items, skipped


def trade_rows(items, mode, fee):
    rows = []
    for k in items:
        r = k["r"]
        if k["tf"] is None:
            continue
        e, xl, xb = getattr(r, f"{mode}_entry"), getattr(r, f"{mode}_last"), getattr(r, f"{mode}_bid")
        cost = 2 * fee + mb.TAX * (e + xl) * r.shares
        rows.append({"day": r.day, "sid": str(r.sid), "name": r.name, "group": r.group, "stars": int(r.stars),
                     "signal": r.time, "submit": mb.HM(k["t0"]), "fill": mb.HM(k["tf"]),
                     "wait_min": round((k["tf"] - k["t0"]) / 60, 1), "exit": mb.HM(k["res_end"]),
                     "held_min": float(getattr(r, f"{mode}_held_min")), "trunc": bool(getattr(r, f"{mode}_trunc")),
                     "entry": e, "exit_last": xl, "exit_bid": xb, "margin": float(r.margin),
                     "gross_bps": round(1e4 * (xl / e - 1), 1), "gross": round((xl - e) * r.shares),
                     "cost": round(cost), "net": round((xl - e) * r.shares - cost),
                     "net_bid": round((xb - e) * r.shares - (2 * fee + mb.TAX * (e + xb) * r.shares))})
    return pd.DataFrame(rows)


def peak_margin(items):
    pk = 0.0
    for t in sorted({k["t0"] for k in items}):
        pk = max(pk, sum(k["r"].margin for k in items if k["t0"] <= t < k["res_end"]))
    return pk


def day_stats(df, mode, cap, fee, **kw):
    items, skipped = simulate(df, mode, cap, fee, **kw)
    tr = trade_rows(items, mode, fee)
    return items, skipped, tr


def scenario(label_, df, mode, cap, fee, exit_kind="net", **kw):
    """回傳一列敏感度:總損益、成交數、樣本內/外損益、賺日數。"""
    tot_is = tot_oos = n = 0
    pk = 0.0
    wins = 0
    accepted = blocked = 0
    for day, g in df.groupby("day"):
        items, skipped, tr = day_stats(g, mode, cap, fee, **kw)
        v = int(tr[exit_kind].sum()) if len(tr) else 0
        if day in OOS_DAYS:
            tot_oos += v
        else:
            tot_is += v
        n += len(tr)
        wins += v > 0
        accepted += len(items)
        blocked += len(skipped)
        pk = max(pk, peak_margin(items))
    return {"情境": label_, "損益": tot_is + tot_oos, "樣本內4日": tot_is, "樣本外10-05": tot_oos, "成交": n,
            "接受": accepted, "擋掉": blocked, "賺日數": f"{wins}/{df.day.nunique()}", "峰值萬": round(pk / 1e4)}


def benchmark(tr, members, fp_by_day):
    """每筆成交的同窗口:名單內其他標的『等權、最後成交價』報酬(扣除大盤/族群多頭的 beta)。"""
    out, tcache = [], {}
    for r in tr.itertuples():
        day = r.day
        t_f = db.epoch(day, r.fill)
        t_x = db.epoch(day, r.exit)
        rets = []
        for sid in members:
            if sid == r.sid:
                continue
            root = db.resolve_root((fp_by_day[day].get(sid) or {}).get("sym", ""), day)
            if not root:
                continue
            ser = tcache.get((root, day))
            if ser is None:
                rows = db.load_trades(root, day) or []
                ser = tcache[(root, day)] = (np.array([x[0] for x in rows]), np.array([x[1] for x in rows]))
            ts, px = ser
            if len(ts) == 0:
                continue
            i0, i1 = np.searchsorted(ts, t_f, "right") - 1, np.searchsorted(ts, t_x, "right") - 1
            if i0 < 0 or i1 <= i0:
                continue
            rets.append(1e4 * (px[i1] / px[i0] - 1))
        out.append(float(np.mean(rets)) if rets else np.nan)
    return np.array(out)



def risk_profile(L, fp_by_day):
    """逐分鐘持倉未實現損益:同時持倉數、同族群最大持倉、名目、盤中最差浮虧(對帳戶淨值)。"""
    eq = load_rules()["capital"]["equity"]
    out = {}
    for day, g in L.groupby("day"):
        series = {}
        for sid in g.sid.unique():
            root = db.resolve_root((fp_by_day[day].get(sid) or {}).get("sym", ""), day)
            rows = db.load_trades(root, day) or []
            series[sid] = (np.array([x[0] for x in rows]), np.array([x[1] for x in rows]))
        pos = [(r.sid, r.group, db.epoch(day, r.fill), db.epoch(day, r.exit), r.entry, r.margin) for r in g.itertuples()]
        t_grid = np.arange(min(p_[2] for p_ in pos), max(p_[3] for p_ in pos) + 1, 30.0)
        worst, worst_t, max_conc, max_grp, max_not = 0.0, None, 0, 0, 0.0
        for t in t_grid:
            live = [p_ for p_ in pos if p_[2] <= t < p_[3]]
            if not live:
                continue
            pnl, notional, grp = 0.0, 0.0, {}
            for sid, gr, tf, tx, e, m in live:
                ts, px = series[sid]
                i = np.searchsorted(ts, t, "right") - 1
                last = px[i] if i >= 0 else e
                pnl += (last - e) * 2000
                notional += last * 2000
                grp[gr] = grp.get(gr, 0) + 1
            max_conc, max_grp, max_not = max(max_conc, len(live)), max(max_grp, max(grp.values())), max(max_not, notional)
            if pnl < worst:
                worst, worst_t = pnl, db.pd.Timestamp(t, unit="s", tz="UTC").tz_convert(db.TZ).strftime("%H:%M")
        out[day] = {"max_concurrent": max_conc, "max_same_group": max_grp, "peak_notional_萬": round(max_not / 1e4),
                    "peak_notional_x_equity": round(max_not / eq, 1), "worst_mtm": int(worst),
                    "worst_mtm_pct_equity": round(100 * worst / eq, 2), "worst_at": worst_t}
    return out


def cluster_ci(tr, col="net", n_boot=4000, seed=7):
    """依 (日,標的) 叢集重抽,看每筆平均損益的 90% 區間;叢集數很少,僅供量級。"""
    if tr.empty:
        return None
    g = tr.groupby(["day", "sid"])[col].agg(["sum", "count"]).reset_index()
    rng = np.random.default_rng(seed)
    m = []
    for _ in range(n_boot):
        s = g.iloc[rng.integers(0, len(g), len(g))]
        m.append(s["sum"].sum() / s["count"].sum())
    return {"clusters": int(len(g)), "mean": float(tr[col].mean()),
            "lo90": float(np.percentile(m, 5)), "hi90": float(np.percentile(m, 95))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=None)
    a = ap.parse_args()
    R = load_rules()
    mode = a.mode or R["order"]["queue_model"]
    cap = R["capital"]["margin_cap"]
    fee = R["cost"]["fee_per_lot_per_side"]
    pol = R["order"]["policy"]
    members = universe_sids(R)
    raw = load_csvs("cab_")
    inl = raw[raw.sid.astype(str).isin(members)].copy()
    print(f"{label(R)}  全部代理人訊號 {len(raw)} 筆 → 名單內 {len(inl)} 筆;依日", inl.groupby('day').size().to_dict())

    # 基準情境逐筆帳
    items_all, skipped_all, ledger = [], [], []
    daily, skip_rows = [], []
    for day, g in inl.groupby("day"):
        items, skipped, tr = day_stats(g, mode, cap, fee, policy=pol, one=R["order"]["one_per_stock"])
        items_all += items
        ledger.append(tr)
        for x, why in skipped:
            skip_rows.append({"day": day, "name": x["r"].name, "sid": str(x["r"].sid), "signal": x["r"].time,
                              "stars": int(x["r"].stars), "margin": float(x["r"].margin), "why": why,
                              "would_fill": bool(x["would_fill"])})
        n_unfilled = sum(1 for k in items if k["tf"] is None)
        daily.append({"day": day, "訊號": len(g), "接受": len(items), "擋掉": len(skipped), "成交": len(tr),
                      "掛到收盤未成交": n_unfilled, "毛損益": int(tr.gross.sum()) if len(tr) else 0,
                      "費稅": int(tr.cost.sum()) if len(tr) else 0, "淨損益": int(tr.net.sum()) if len(tr) else 0,
                      "買一價出場淨損益": int(tr.net_bid.sum()) if len(tr) else 0,
                      "峰值保證金萬": round(peak_margin(items) / 1e4),
                      "勝率": round(float((tr.net > 0).mean()), 2) if len(tr) else None})
    L = pd.concat(ledger, ignore_index=True)
    skips = pd.DataFrame(skip_rows)
    fp_by_day = {d: json.loads((db.CACHE / f"biglot_live_watch/futprice_{d}.json").read_text(encoding="utf-8")) for d in DAYS}
    L["bench_bps"] = benchmark(L, members, fp_by_day)
    L["excess_bps"] = L.gross_bps - L.bench_bps
    L.to_csv(OUT / f"backtest_{R['id']}_v{R['version']}_ledger.csv", index=False)

    tot = int(L.net.sum())
    top3 = int(L.net.sort_values(ascending=False).head(3).sum())
    by_stock = L.groupby("name").net.agg(["sum", "count"]).sort_values("sum", ascending=False)
    by_star = L.groupby("stars").net.agg(["sum", "count", "mean"])
    ist, oos = L[~L.day.isin(OOS_DAYS)], L[L.day.isin(OOS_DAYS)]
    summ = {
        "label": label(R), "rules": {k: R[k] for k in ("id", "version", "frozen_on")},
        "signals_all": int(len(raw)), "signals_in_list": int(len(inl)), "mode": mode, "cap": cap, "fee": fee,
        "headline": {"net": tot, "gross": int(L.gross.sum()), "cost": int(L.cost.sum()), "fills": int(len(L)),
                     "net_bid_exit": int(L.net_bid.sum()), "win": float((L.net > 0).mean()),
                     "mean_net": float(L.net.mean()), "median_net": float(L.net.median()),
                     "mean_gross_bps": float(L.gross_bps.mean()), "median_gross_bps": float(L.gross_bps.median()),
                     "wait_median_min": float(L.wait_min.median()), "skipped_by_margin": int(len(skips)),
                     "skipped_would_fill": int(skips.would_fill.sum()) if len(skips) else 0,
                     "skipped_would_fill_lost_mean_bps": None},
        "is_oos": {"is_net": int(ist.net.sum()), "is_n": int(len(ist)), "is_win": float((ist.net > 0).mean()),
                   "is_mean_bps": float(ist.gross_bps.mean()), "is_median_bps": float(ist.gross_bps.median()),
                   "oos_net": int(oos.net.sum()), "oos_n": int(len(oos)),
                   "oos_win": float((oos.net > 0).mean()) if len(oos) else None,
                   "oos_mean_bps": float(oos.gross_bps.mean()) if len(oos) else None,
                   "oos_median_bps": float(oos.gross_bps.median()) if len(oos) else None,
                   "oos_net_bid": int(oos.net_bid.sum())},
        "concentration": {"top3_net": top3, "ex_top3_net": tot - top3,
                          "top1_net": int(L.net.max()), "top1": f"{L.loc[L.net.idxmax(), 'name']} {L.loc[L.net.idxmax(), 'day']}",
                          "by_stock": {k: {"net": int(v['sum']), "n": int(v['count'])} for k, v in by_stock.iterrows()}},
        "stars": {int(k): {"net": int(v["sum"]), "n": int(v["count"]), "mean": float(v["mean"])} for k, v in by_star.iterrows()},
        "benchmark": {"mean_bench_bps": float(L.bench_bps.mean()), "mean_excess_bps": float(L.excess_bps.mean()),
                      "median_excess_bps": float(L.excess_bps.median()),
                      "is_excess_bps": float(ist.index.map(lambda i: L.excess_bps[i]).to_series().mean()) if len(ist) else None,
                      "oos_excess_bps": float(L[L.day.isin(OOS_DAYS)].excess_bps.mean()) if len(oos) else None,
                      "by_day": {d: {"own": float(g.gross_bps.mean()), "bench": float(g.bench_bps.mean())} for d, g in L.groupby("day")}},
        "cluster_ci": cluster_ci(L),
        "risk": risk_profile(L, fp_by_day),
        "daily": daily,
        "signal_profile": {"all_by_day": raw.groupby("day").size().to_dict(), "list_by_day": inl.groupby("day").size().to_dict(),
                           "stars": {int(k): int(v) for k, v in inl.stars.value_counts().sort_index().items()},
                           "groups": inl.group.value_counts().to_dict(), "names": inl.name.value_counts().to_dict(),
                           "filled_unconstrained_rate": float(inl.qpro_fill.notna().mean())},
    }
    # 擋掉的單若成交會怎樣(保證金上限的機會成本)
    if len(skips):
        blk = inl.copy()
        full_items, _, full_tr = day_stats(blk, mode, 1e12, fee)
        summ["headline"]["unconstrained_net"] = int(full_tr.net.sum()) if len(full_tr) else 0
        summ["headline"]["unconstrained_fills"] = int(len(full_tr))

    # 敏感度
    sens = []
    add = lambda *a_, **k_: sens.append(scenario(*a_, **k_))  # noqa: E731
    add("基準 " + label(R), inl, mode, cap, fee, policy=pol)
    for m, nm in (("qcons", "排隊保守 qcons"), ("qopt", "排隊樂觀 qopt"), ("strict", "只算價格穿越才成交 strict"),
                  ("opt", "觸價即成交 opt"), ("all", "送出即成交 all(理論上界)")):
        add(nm, inl, m, cap, fee, policy=pol)
    for f_, nm in ((0, "手續費 0"), (40, "手續費 40"), (100, "手續費 100"), (300, "手續費 300")):
        add(nm, inl, mode, cap, f_, policy=pol)
    add("出場改買一價", inl, mode, cap, fee, exit_kind="net_bid", policy=pol)
    for cap_, nm in ((1_000_000, "保證金上限 100 萬"), (2_000_000, "保證金上限 200 萬"), (1e12, "不設保證金上限")):
        add(nm, inl, mode, cap_, fee, policy=pol)
    add("保證金不足時撤低星單 replace", inl, mode, cap, fee, policy="replace")
    add("每檔限 1 口", inl, mode, cap, fee, policy=pol, one=True)
    for s_ in (2, 3):
        add(f"只做 ★≥{s_}（事後挑選,樣本內調參,僅供參考）", inl[inl.stars >= s_], mode, cap, fee, policy=pol)
    add("名單改用逐筆價格帶(in_univ)", raw[raw.in_univ], mode, cap, fee, policy=pol)
    add("不套名單：225 筆全送", raw, mode, cap, fee, policy=pol)
    for lag in (30, 60):
        d_ = load_csvs(f"cab_lag{lag}_")
        if len(d_):
            d_ = d_[d_.sid.astype(str).isin(members)]
            add(f"人工批准延遲 {lag} 秒", d_, mode, cap, fee, policy=pol)
    c_ = load_csvs("cab_close1325_")
    if len(c_):
        c_ = c_[c_.sid.astype(str).isin(members)]
        add("13:25 強制平倉/停止排隊(PRD)", c_, mode, cap, fee, policy=pol, close=13 * 3600 + 25 * 60)
    summ["sensitivity"] = sens
    pd.DataFrame(sens).to_csv(S / "clone_agent_rules_sensitivity.csv", index=False)

    # 逐日逐筆時間線
    summ["ledger"] = L[["day", "name", "stars", "signal", "submit", "fill", "wait_min", "exit", "held_min", "entry", "exit_last",
                        "margin", "gross_bps", "bench_bps", "net"]].round(1).to_dict("records")
    summ["skipped"] = skips.to_dict("records") if len(skips) else []
    out = OUT / f"backtest_{R['id']}_v{R['version']}.json"
    out.write_text(json.dumps(summ, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 300)
    print(pd.DataFrame(daily).to_string(index=False))
    print(json.dumps({k: summ[k] for k in ("headline", "is_oos", "concentration", "stars", "benchmark", "cluster_ci", "risk")}, ensure_ascii=False, indent=1, default=float))
    print(pd.DataFrame(sens).to_string(index=False))
    print("wrote", out)


if __name__ == "__main__":
    main()
