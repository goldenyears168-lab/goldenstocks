#!/usr/bin/env python3
"""CA-R1 1.2.0「故事版訊號」回測 + 分開測試(T1~T7)。事後描述性,不是樣本外。

流程:scan_v3/dense(逐分鐘全記,--no-rank-filter)→ prep 只留名單內+價格帶 → day_backtest(排隊模擬,逐日 CSV)
→ 本程式對密集卡片套各種濾網 → 先濾再冷卻(20 分)→ 同一套保證金/先到先得/凍結出場(60 分最後價)→ 逐筆帳。
用法: prep | run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_agent_rules_backtest as rb  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

S, OUT, DAYS = rb.S, rb.OUT, rb.DAYS
V3 = S / "scan_v3"
COOL = 20 * 60
KEEP = 0.70 + 1e-12
R = load_rules()
MEMBERS = universe_sids(R)
LO, HI = R["universe"]["price_band"]
CAP, FEE = R["capital"]["margin_cap"], R["cost"]["fee_per_lot_per_side"]


def read_dense(variant):
    rows = []
    for d in DAYS:
        p = V3 / variant / f"candidates_{d}.jsonl"
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                c = json.loads(ln)
                fl = c.get("flow") or {}
                rows.append({"day": c["day"], "time": c["time"], "sid": str(c["sid"]), "price": c["price"],
                             "dist30": (c.get("dist_refs") or {}).get("30"), "dist_hi": (c.get("dist_refs") or {}).get(
                                 "30"), **{f"{k}_pct": v[0] for k, v in fl.items()}, **{f"{k}_fr": v[1] for k, v in fl.items()}})
    return pd.DataFrame(rows)


def prep(variant):
    src, dst = V3 / variant, V3 / f"{variant}_m"
    dst.mkdir(exist_ok=True)
    n = 0
    for d in DAYS:
        p = src / f"candidates_{d}.jsonl"
        if not p.exists():
            continue
        keep = []
        for ln in p.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            c = json.loads(ln)
            if str(c["sid"]) in MEMBERS and LO <= c["price"] <= HI:
                keep.append(ln)
        (dst / f"candidates_{d}.jsonl").write_text("\n".join(keep) + "\n", encoding="utf-8")
        n += len(keep)
    print(variant, "名單內+價格帶卡片", n)


def cooldown(df):
    """逐檔 20 分冷卻重放(輸入已先套濾網)。"""
    keep, last = [], {}
    for r in df.sort_values(["day", "time"], kind="stable").itertuples():
        t = pd.Timestamp(f"{r.day} {r.time}").timestamp()
        k = (r.day, r.sid)
        if k in last and t - last[k] < COOL:
            continue
        last[k] = t
        keep.append(r.Index)
    return df.loc[keep]


def ledger(df, mode="qpro"):
    out, n_acc = [], 0
    for day, g in df.groupby("day"):
        items, sk, tr = rb.day_stats(g, mode, CAP, FEE, policy="fcfs", one=False)
        out.append(tr)
        n_acc += len(items)
    L = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return L, n_acc


def stat(L, n_sig):
    if L is None or len(L) == 0:
        return {"訊號": n_sig, "成交": 0, "淨": 0, "每筆": 0, "勝率": None, "去最大": 0, "最大": 0, "bps均": None}
    top = L.net.max()
    return {"訊號": n_sig, "成交": len(L), "淨": int(L.net.sum()), "每筆": int(L.net.mean()),
            "勝率": round(float((L.net > 0).mean()), 2), "去最大": int(L.net.sum() - top), "最大": int(top),
            "bps均": round(float(L.gross_bps.mean()), 1)}


def load_cab(prefix):
    fs = [S / f"{prefix}{d}.csv" for d in DAYS if (S / f"{prefix}{d}.csv").exists()]
    d = pd.concat([pd.read_csv(f) for f in fs if f.stat().st_size > 5], ignore_index=True)
    d["sid"] = d.sid.astype(str)
    return d


def run(a):
    dense = read_dense("dense2")
    cab = load_cab("cabv3_")
    key = ["day", "time", "sid"]
    M = cab.merge(dense, on=key, how="inner", suffixes=("_c", ""))
    M = M[M.sid.isin(MEMBERS) & (M.price >= LO) & (M.price <= HI)].reset_index(drop=True)
    assert len(M) == len(cab), (len(M), len(cab))
    print(f"密集卡片(名單內+價格帶,≥09:30) {len(M)} 張;逐日", M.groupby('day').size().to_dict())
    res = {}

    def shuffled(mask, k):
        return cooldown(M[mask].sample(frac=1, random_state=1000 + k))

    def variant(name, mask):
        sub = cooldown(M[mask])
        L, _ = ledger(sub)
        r = stat(L, len(sub))
        tie = np.array([(lambda LL: (int(LL.net.sum()), len(LL)))(ledger(shuffled(mask, k))[0]) for k in range(a.ties)])
        r["同分鐘隨機排序"] = {"K": a.ties, "淨_平均": int(tie[:, 0].mean()), "淨_標準差": int(tie[:, 0].std()),
                         "淨_p5": int(np.percentile(tie[:, 0], 5)), "淨_p95": int(np.percentile(tie[:, 0], 95)),
                         "成交_平均": round(float(tie[:, 1].mean()), 1)}
        res[name] = r
        t = r["同分鐘隨機排序"]
        print(f"{name:34s} 訊號{r['訊號']:>4} 成交{r['成交']:>3} 淨{r['淨']:>8} 每筆{r['每筆']:>6} 勝{r['勝率']} 去最大{r['去最大']:>8} bps{r['bps均']}"
              f" | 同分鐘隨機排序 淨均{t['淨_平均']:>8} sd{t['淨_標準差']:>7} p5/p95 {t['淨_p5']}/{t['淨_p95']} 成交均{t['成交_平均']}")
        return sub, L

    ok = lambda c: (M[f"{c}_fr"].notna()) & (M[f"{c}_fr"] <= KEEP)   # noqa: E731
    allm = pd.Series(True, index=M.index)
    newm = ok("big_day") & ok("big30") & ok("ret30")
    P, LP = variant("P 無濾網(09:30後,30分高回檔)", allm)
    N, LN = variant("NEW 1.2.0(①③④)", newm)
    # 主對照:同量隨機剔除(逐張,保留率=NEW 張數/P 張數,先剔再冷卻)
    p_keep = newm.sum() / len(M)
    rng = np.random.default_rng(11)
    nulls = []
    for _ in range(a.draws):
        sub = cooldown(M[rng.random(len(M)) < p_keep].sample(frac=1, random_state=int(rng.integers(1 << 30))))
        L, _ = ledger(sub)
        nulls.append((len(sub), len(L), int(L.net.sum()) if len(L) else 0, float(L.net.mean()) if len(L) else 0.0))
    nl = np.array(nulls)
    res["null_random_removal"] = {"draws": a.draws, "keep_prob": round(float(p_keep), 3),
                                  "net_mean": float(nl[:, 2].mean()), "net_sd": float(nl[:, 2].std()),
                                  "net_p5": float(np.percentile(nl[:, 2], 5)), "net_p50": float(np.percentile(nl[:, 2], 50)),
                                  "net_p95": float(np.percentile(nl[:, 2], 95)),
                                  "perfill_mean": float(nl[:, 3].mean()), "perfill_p95": float(np.percentile(nl[:, 3], 95)),
                                  "fills_mean": float(nl[:, 1].mean()),
                                  "new_net_percentile": float((nl[:, 2] < res["NEW 1.2.0(①③④)"]["同分鐘隨機排序"]["淨_平均"]).mean() * 100),
                                  "new_net_percentile_scanorder": float((nl[:, 2] < res["NEW 1.2.0(①③④)"]["淨"]).mean() * 100)}
    print("主對照 隨機剔除:", json.dumps(res["null_random_removal"], ensure_ascii=False))
    # T2 ① 單獨 vs 同量隨機
    one = ok("big_day")
    variant("T2 只用①全日大戶佔比", one)
    pk = one.sum() / len(M)
    nl2 = []
    for _ in range(a.draws):
        sub = cooldown(M[rng.random(len(M)) < pk].sample(frac=1, random_state=int(rng.integers(1 << 30))))
        L, _ = ledger(sub)
        nl2.append((len(L), int(L.net.sum()) if len(L) else 0, float(L.net.mean()) if len(L) else 0.0))
    nl2 = np.array(nl2)
    res["T2_null"] = {"keep_prob": round(float(pk), 3), "net_mean": float(nl2[:, 1].mean()), "net_sd": float(nl2[:, 1].std()),
                      "pct_of_actual_net": float((nl2[:, 1] < res["T2 只用①全日大戶佔比"]["同分鐘隨機排序"]["淨_平均"]).mean() * 100)}
    print("T2 隨機剔除:", json.dumps(res["T2_null"], ensure_ascii=False))
    # T3 ③④ vs 單一 30 分大戶−散戶
    variant("T3a ③④(30分大戶+30分散戶)", ok("big30") & ok("ret30"))
    variant("T3b 單一 30分大戶−散戶佔比", ok("diff30"))
    variant("T3c ①+30分大戶−散戶", ok("big_day") & ok("diff30"))
    # T4 排名 vs 排名+符號
    variant("T4a NEW+大戶淨買>0", newm & (M.big30_pct > 0))
    variant("T4b NEW+大戶>0∧散戶<0", newm & (M.big30_pct > 0) & (M.ret30_pct < 0))
    variant("T4c 只有符號(30分大戶>0∧散戶<0)", (M.big30_pct > 0) & (M.ret30_pct < 0))
    # T6 中實桶(診斷):NEW 內依 mid30 排名切半
    for nm, m in (("T6a NEW∧中實淨買排前50%", newm & (M.mid30_fr <= 0.5)), ("T6b NEW∧中實淨買排後50%", newm & (M.mid30_fr > 0.5))):
        variant(nm, m)
    # 單條件剔除(看每條濾網各自貢獻)
    for nm, m in (("D1 去①(②③④)", ok("big30") & ok("ret30")), ("D2 去③(①④)", ok("big_day") & ok("ret30")),
                  ("D3 去④(①③)", ok("big_day") & ok("big30"))):
        variant(nm, m)
    # 逐日
    byday = {}
    for nm, L in (("P", LP), ("NEW", LN)):
        byday[nm] = {d: int(g.net.sum()) for d, g in L.groupby("day")} if len(L) else {}
    res["by_day"] = byday
    print("逐日", json.dumps(byday, ensure_ascii=False))
    if len(LN):
        print(LN[["day", "name", "signal", "fill", "entry", "exit_last", "net"]].to_string(index=False))
        LN.to_csv(OUT / f"story_ledger_{R['id']}_v{R['version']}.csv", index=False)
    N.to_csv(S / "story_new_signals.csv", index=False)
    (OUT / f"story_study_{R['id']}_v{R['version']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prep", "run"])
    ap.add_argument("--draws", type=int, default=300)
    ap.add_argument("--ties", type=int, default=40)
    ap.add_argument("--variant", default="dense")
    a = ap.parse_args()
    prep(a.variant) if a.cmd == "prep" else run(a)
