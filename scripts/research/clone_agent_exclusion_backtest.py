#!/usr/bin/env python3
"""把儀表板「排除型過濾器」四條件套在 CA-R1 v1.0.0 回測上,看濾掉之後有沒有改善(出卡時間判斷)。

排除條件(jack 2026-10-05,全部用金額淨額、母體=儀表板全表,同過濾器的 70% 保留規則):
  A 近兩日大戶淨額 倒數30%   B 全日大戶淨額 倒數30%   C 30分散戶淨額 前30%   D 30分大戶淨額 倒數30%
被排除的卡片不送單(不佔保證金);之後重跑保證金/排隊/費稅模擬。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_agent_rules_backtest as crb  # noqa: E402
from clone_agent.rules_version import label, load_rules, universe_sids  # noqa: E402

S, OUT, DAYS, OOS = crb.S, crb.OUT, crb.DAYS, crb.OOS_DAYS
KEEP = 0.70
FLAGS = {"A": ("近兩日大戶 倒數30%", "big2d", "frac_larger"), "B": ("全日大戶 倒數30%", "bigday", "frac_larger"),
         "C": ("30分散戶 前30%", "ret30", "frac_smaller"), "D": ("30分大戶 倒數30%", "big30", "frac_larger")}


def load():
    raw = crb.load_csvs("cab_")
    raw["sid"] = raw.sid.astype(str)
    f = pd.read_csv(S / "clone_agent_exclusion_features.csv", dtype={"sid": str})
    m = raw.merge(f, on=["day", "time", "sid"], how="left", validate="m:1")
    for k, (_, col, fr) in FLAGS.items():
        v = m[f"{col}_{fr}"]
        m[k] = (v > KEEP + 1e-12) & v.notna()
    m["any"] = m[list(FLAGS)].any(axis=1)
    return m


def run(df, mode, cap, fee, pol):
    tr_all, items_n, blocked = [], 0, 0
    for day, g in df.groupby("day"):
        items, skipped, tr = crb.day_stats(g, mode, cap, fee, policy=pol)
        tr_all.append(tr)
        items_n += len(items)
        blocked += len(skipped)
    tr = pd.concat([t for t in tr_all if len(t)], ignore_index=True) if any(len(t) for t in tr_all) else pd.DataFrame(
        columns=["day", "net", "gross_bps"])
    return tr, items_n, blocked


def summ(tr, items_n, blocked, n_cards):
    ist = tr[~tr.day.isin(OOS)]
    oos = tr[tr.day.isin(OOS)]
    return {"送單卡": n_cards, "接受": items_n, "擋掉": blocked, "成交": len(tr),
            "淨損益": int(tr.net.sum()) if len(tr) else 0, "樣本內4日": int(ist.net.sum()) if len(ist) else 0,
            "樣本外10-05": int(oos.net.sum()) if len(oos) else 0,
            "平均毛bps": round(float(tr.gross_bps.mean()), 1) if len(tr) else None,
            "勝率": round(float((tr.net > 0).mean()), 2) if len(tr) else None,
            "每筆淨": int(tr.net.mean()) if len(tr) else 0,
            "賺日數": f"{int((tr.groupby('day').net.sum() > 0).sum()) if len(tr) else 0}/{len(DAYS)}"}


def null_p(df, mode, cap, fee, pol, n_drop, observed, n=300, seed=11):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(df))
    res = []
    for _ in range(n):
        keep = np.ones(len(df), bool)
        keep[rng.choice(idx, size=n_drop, replace=False)] = False
        tr, *_ = run(df[keep], mode, cap, fee, pol)
        res.append(int(tr.net.sum()) if len(tr) else 0)
    res = np.array(res)
    return {"隨機同量剔除_中位": int(np.median(res)), "p25": int(np.percentile(res, 25)), "p75": int(np.percentile(res, 75)),
            "p(隨機>=觀察)": round(float((res >= observed).mean()), 3)}


def main():
    R = load_rules()
    mode, cap, fee, pol = R["order"]["queue_model"], R["capital"]["margin_cap"], R["cost"]["fee_per_lot_per_side"], R["order"]["policy"]
    members = universe_sids(R)
    m = load()
    miss = m[list(f"{c}_{fr}" for _, c, fr in FLAGS.values())].isna().any(axis=1).sum()
    print(f"{label(R)} 全部訊號 {len(m)} 筆;特徵缺值 {miss};名單內 {m.sid.isin(members).sum()}")
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 200)
    out = {"label": label(R), "flags": {k: v[0] for k, v in FLAGS.items()}, "pool_n_median": float(m.big30_n.median())}

    for tag, df0 in (("名單13檔", m[m.sid.isin(members)].copy()), ("全部代理人訊號(不套名單)", m.copy())):
        print(f"\n=========== {tag}:{len(df0)} 張卡")
        hit = {k: int(df0[k].sum()) for k in list(FLAGS) + ["any"]}
        print("命中卡數(出卡當下):", hit, "  依日 any:", df0.groupby("day")["any"].sum().to_dict())
        combos = [("基準(不過濾)", df0)]
        combos += [(f"排除 {k} {FLAGS[k][0]}", df0[~df0[k]]) for k in FLAGS]
        combos += [("四條件任一中就排除(全開)", df0[~df0["any"]])]
        combos += [(f"只排除 {a}+{b}", df0[~(df0[a] | df0[b])]) for a, b in (("A", "B"), ("C", "D"), ("B", "D"))]
        rows, nul = [], {}
        for name, d in combos:
            tr, a_, b_ = run(d, mode, cap, fee, pol)
            r = {"情境": name, **summ(tr, a_, b_, len(d))}
            tru, au, bu = run(d, mode, 1e12, fee, pol)
            r["無上限淨"] = int(tru.net.sum()) if len(tru) else 0
            r["無上限成交"] = len(tru)
            rows.append(r)
            if name != "基準(不過濾)":
                nul[name] = null_p(df0, mode, cap, fee, pol, len(df0) - len(d), r["淨損益"])
        tab = pd.DataFrame(rows)
        print(tab.to_string(index=False))
        print("\n隨機剔除同量卡片的對照(淨損益分布):")
        print(pd.DataFrame(nul).T.to_string())

        # 卡片層級:不受保證金影響(無上限成交)的「中標 vs 沒中標」
        tru, *_ = run(df0, mode, 1e12, fee, pol)
        tru = tru.merge(df0[["day", "time", "sid"] + list(FLAGS) + ["any"]].rename(columns={"time": "signal"}), on=["day", "signal", "sid"], how="left")
        rows2 = []
        for k in list(FLAGS) + ["any"]:
            for v, lab in ((True, "中"), (False, "沒中")):
                g = tru[tru[k] == v]
                rows2.append({"條件": k if k == "any" else f"{k} {FLAGS[k][0]}", "": lab, "成交": len(g),
                              "平均毛bps": round(float(g.gross_bps.mean()), 1) if len(g) else None,
                              "中位bps": round(float(g.gross_bps.median()), 1) if len(g) else None,
                              "勝率": round(float((g.net > 0).mean()), 2) if len(g) else None,
                              "淨損益": int(g.net.sum()), "每筆淨": int(g.net.mean()) if len(g) else 0})
        t2 = pd.DataFrame(rows2)
        print("\n不受保證金影響(無上限)的成交,中標 vs 沒中標:")
        print(t2.to_string(index=False))

        # 基準帳上的 39 筆各自中了哪些條件
        trb, *_ = run(df0, mode, cap, fee, pol)
        trb = trb.merge(df0[["day", "time", "sid"] + list(FLAGS) + ["any"]].rename(columns={"time": "signal"}), on=["day", "signal", "sid"], how="left")
        out[tag] = {"hit_cards": hit, "scenarios": rows, "null": nul, "uncapped_hit_vs_not": rows2,
                    "ledger_hits": trb[["day", "name", "signal", "gross_bps", "net"] + list(FLAGS) + ["any"]].assign(
                        **{k: trb[k].astype(bool) for k in list(FLAGS) + ["any"]}).to_dict("records")}
        cols = ["day", "name", "signal", "gross_bps", "net"] + list(FLAGS)
        print("\n基準帳逐筆(★=中該排除條件):")
        print(trb[cols].assign(**{k: trb[k].map({True: "★", False: ""}) for k in FLAGS}).to_string(index=False))

    (OUT / f"exclusion_filter_{R['id']}_v{R['version']}.json").write_text(json.dumps(out, ensure_ascii=False, default=str, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
