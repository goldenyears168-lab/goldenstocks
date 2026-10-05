#!/usr/bin/env python3
"""進場掛價三法比較(買一 / 買一下1檔 / 條件式外盤) × 無保證金上限+時段分析 × 加保證金再測。預先登記,事後描述性。

價格:b1=買一;t1=買一下1檔(現行);X-b1/X-t1=條件式外盤(90%);X60=同條件但門檻放寬到 60%(診斷,因 90% 幾乎不觸發)(決策前5分鐘期貨外盤量>=90% 且 賣一-買一<=1檔 → 掛賣一,否則退回 b1/t1);ASK=一律掛賣一(上界參考)。
管理:A=現行(掛到成交或收盤、不撤單);B=5分未成交撤單+①③④不成立撤單+撤單即重新武裝。
無上限:cap=1e12,順序不影響結果(單次);有上限:147萬、同分鐘隨機排序 K 次、同種子配對。
時段(以訊號分鐘分桶):對每筆成交比較淨損益/毛 bps/同窗名單等權基準,並做標籤置換檢定。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_agent_day_backtest as db  # noqa: E402
import clone_agent_entry2_backtest as E  # noqa: E402
import clone_agent_rules_backtest as rb  # noqa: E402

PRICES = {"b1": dict(px="ticks", ticks=0), "t1(現行)": dict(px="ticks", ticks=1),
          "X-b1": dict(px="cond", fb=0), "X-t1": dict(px="cond", fb=1), "ASK": dict(px="ask"),
          "X60-b1(診斷)": dict(px="cond", fb=0, thr=0.6), "X60-t1(診斷)": dict(px="cond", fb=1, thr=0.6)}
MGMT = {"A現行": dict(ttl=None, flow_cancel=False, rearm=False), "B撤單重武裝": dict(ttl=5, flow_cancel=True, rearm=True)}
BUCKETS = [("09:30", "09:30:00", "09:31:00"), ("09:31-10:00", "09:31:00", "10:00:00"), ("10:00-10:30", "10:00:00", "10:30:00"),
           ("10:30-11:00", "10:30:00", "11:00:00"), ("11:00-12:00", "11:00:00", "12:00:00")]


def bucket(t):
    for nm, a, b in BUCKETS:
        if a <= t < b:
            return nm
    return "其他"


def hms(e):
    return datetime.fromtimestamp(e, pd.Timestamp(0, tz=db.TZ).tz).strftime("%H:%M:%S")


def perm_between(vals, labels, n=20000, seed=7):
    rng = np.random.default_rng(seed)
    labs = pd.Series(labels).to_numpy()
    cats = sorted(set(labs))
    def ss(l):
        m = vals.mean()
        return sum(((l == c).sum()) * (vals[l == c].mean() - m) ** 2 for c in cats)
    obs = ss(labs)
    cnt = sum(ss(rng.permutation(labs)) >= obs for _ in range(n))
    return obs, (cnt + 1) / (n + 1)


def perm_vs_rest(vals, labels, c, n=20000, seed=11):
    rng = np.random.default_rng(seed)
    m = np.array(labels) == c
    obs = vals[m].mean() - vals[~m].mean()
    k = m.sum()
    cnt = 0
    for _ in range(n):
        i = rng.permutation(len(vals))
        cnt += abs(vals[i[:k]].mean() - vals[i[k:]].mean()) >= abs(obs)
    return obs, (cnt + 1) / (n + 1)


def tod_table(F, days, fp_by_day, label):
    """F: 成交 DataFrame(含 time, net, bps, dist30, spread_bps, day, sid, tf, exit_t)。"""
    F = F.copy()
    F["bucket"] = F.time.map(bucket)
    tmp = pd.DataFrame({"day": F.day, "sid": F.sid, "fill": F.tf.map(hms), "exit": F.exit_t.map(hms)})
    F["bench"] = rb.benchmark(tmp, E.MEMBERS, fp_by_day)
    F["excess"] = F.bps - F.bench
    rows = []
    for nm, g in F.groupby("bucket"):
        byd = g.groupby("day").net.sum()
        rows.append({"時段": nm, "筆數": len(g), "淨損益": int(g.net.sum()), "每筆淨": int(g.net.mean()), "勝率": round(float((g.net > 0).mean()), 2),
                     "毛bps均": round(float(g.bps.mean()), 1), "基準bps均": round(float(g.bench.mean()), 1),
                     "超額bps均": round(float(g.excess.mean()), 1), "價差bps均": round(float(g.spread_bps.mean()), 1),
                     "距30分高%均": round(float(g.dist30.mean()), 2), "賺的天數": f"{int((byd > 0).sum())}/{len(byd)}"})
    T = pd.DataFrame(rows)
    order = [b[0] for b in BUCKETS]
    T["_o"] = T.時段.map(lambda x: order.index(x) if x in order else 99)
    T = T.sort_values("_o").drop(columns="_o")
    out = {"表": T.to_dict("records")}
    if len(F) >= 10:
        for key in ("net", "bps", "excess"):
            obs, p = perm_between(F[key].to_numpy(float), F.bucket.to_numpy())
            out[f"全時段置換_{key}"] = {"between_SS": float(obs), "p": round(p, 4)}
        out["各時段對其餘(淨損益/筆)"] = {c: {"差": int(d), "p": round(p, 4)} for c in F.bucket.unique()
                                   for d, p in [perm_vs_rest(F.net.to_numpy(float), F.bucket.to_numpy(), c)]}
        out["各時段對其餘(超額bps)"] = {c: {"差": round(float(d), 1), "p": round(p, 4)} for c in F.bucket.unique()
                                   for d, p in [perm_vs_rest(F.excess.to_numpy(float), F.bucket.to_numpy(), c)]}
    print(f"\n[{label}] 成交 {len(F)} 筆")
    print(T.to_string(index=False))
    for k, v in out.items():
        if k != "表":
            print(k, json.dumps(v, ensure_ascii=False))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ties", type=int, default=200)
    a = ap.parse_args()
    days = [E.Day(d) for d in E.DAYS]
    fp_by_day = {D.day: D.fp for D in days}
    pd.set_option("display.width", 250, "display.max_columns", 40)
    res = {"無上限": {}, "有上限": {}, "時段": {}}
    nocap_fills = {}
    # ① 無上限(順序無關,單次)
    for pn, pc in PRICES.items():
        for mn, mc in MGMT.items():
            cfg = {**pc, **mc, "lag": 5, "cap": 1e12}
            o = E.run_all(days, cfg, None)
            d = E.digest(o)
            d["條件外盤觸發張數"] = int(o.ask_trig.sum()) if "ask_trig" in o else 0
            f = o[o.status == "filled"]
            d["外盤觸發且成交"] = int(f.ask_trig.sum())
            res["無上限"][f"{pn}|{mn}"] = d
            nocap_fills[(pn, mn)] = f
            print(f"[無上限] {pn:8s} {mn:8s} 送{d['送出']:>4} 成{d['成交']:>4} 率{d['成交率']:.2f} ttl撤{d['撤單_ttl']:>3} 流量撤{d['撤單_流量']:>3} "
                  f"淨{d['淨']:>8} 每筆{d['每筆']:>6} 勝{d['勝率']} bps{d['bps均']} 等{d['平均等待分']}分 外盤觸發{d['條件外盤觸發張數']}/成交{d['外盤觸發且成交']} 逐日{d['逐日']}", flush=True)
    # ② 時段(無上限)
    for pn in PRICES:
        res["時段"][f"{pn}|A現行"] = tod_table(nocap_fills[(pn, "A現行")], days, fp_by_day, f"無上限 {pn} 管理A")
    res["時段"]["t1(現行)|B撤單重武裝"] = tod_table(nocap_fills[("t1(現行)", "B撤單重武裝")], days, fp_by_day, "無上限 t1 管理B")
    # ③ 有上限,同種子配對
    base = None
    for mn, mc in MGMT.items():
        for pn, pc in PRICES.items():
            cfg = {**pc, **mc, "lag": 5}
            r, _ = E.evaluate(days, cfg, a.ties)
            res["有上限"][f"{pn}|{mn}"] = r
            if pn == "t1(現行)" and mn == "A現行":
                base = np.array(r["同分鐘隨機排序"]["每次淨損益"])
            t = r["同分鐘隨機排序"]
            res["有上限"][f"{pn}|{mn}"]["_net"] = np.array(t["每次淨損益"])
            print(f"[有上限] {pn:8s} {mn:8s} 淨均{t['淨_平均']:>8} sd{t['淨_標準差']:>6} p5/p95 {t['淨_p5']}/{t['淨_p95']} 送{t['送出_平均']} 成{t['成交_平均']} 率{t['成交率_平均']} "
                  f"ttl撤{t['撤單ttl_平均']} 流量撤{t['撤單流量_平均']} 每筆{t['每筆_平均']} 勝{t['勝率_平均']} 逐日{t['逐日_平均']}", flush=True)
    for k, r in res["有上限"].items():
        d = r.pop("_net") - base
        r["對現行A配對差"] = {"平均": int(d.mean()), "標準誤": int(d.std(ddof=1) / np.sqrt(len(d))), "優於現行比例": round(float((d > 0).mean()), 2)}
        print(f"配對差 vs t1|A [{k}]: {d.mean():+.0f} (se {d.std(ddof=1) / np.sqrt(len(d)):.0f}) 優於 {(d > 0).mean():.0%}")
    (E.OUT / "entry3_study_CA-R1_v1.2.0.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
