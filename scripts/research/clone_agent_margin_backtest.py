#!/usr/bin/env python3
"""保證金上限下的單日回測:clone_agent_day_backtest.py 的第二階段,讀它輸出的 CSV。

規則:
  · 佔用保證金 = 持倉 + 未成交的新倉委託(平倉不佔);每口 = 候選卡片的 margin(限價 × 2000 × 保證金率)。
  · 訊號依時間先後處理,送出當下 已占用 + 本筆 > 上限 就整筆放棄。
  · 委託預設掛到成交或收盤;--ttl 模擬 N 分鐘沒成交就撤單。成交後持有 hold 分鐘(CSV 已算好)出場並釋放保證金。
  · 委託順序 policy:fcfs=先到先得;replace=額度不夠時,撤掉「尚未成交且星數較低」的委託讓位(只用當下已知資訊)。
  · --one-per-stock:同標的已有持倉或掛單就不再送。
  · 損益由 CSV 的進出價重算:手續費每口每邊 --fee 元、期交稅每邊 0.002% × 名目金額。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

CSV = Path.home() / "goldenstocks-data/data/scratch/clone_agent_day_backtest.csv"
CLOSE = 13 * 3600 + 45 * 60
TAX = 0.00002
HM = lambda s: f"{int(s)//3600:02d}:{int(s)%3600//60:02d}:{int(s)%60:02d}"


def sec(hms):
    h, m, s = (int(x) for x in str(hms).split(":"))
    return h * 3600 + m * 60 + s


def net(r, mode, kind, fee):
    e, x = getattr(r, f"{mode}_entry"), getattr(r, f"{mode}_{kind}")
    return (x - e) * r.shares - 2 * fee - TAX * (e + x) * r.shares


def run(df, mode, cap, ttl_min, policy, one_per_stock):
    items, skipped = [], []
    for r in df.sort_values("time").itertuples():
        t0 = sec(r.time) + int(getattr(r, "lag", 5))
        f = getattr(r, f"{mode}_fill")
        tf = sec(f) if isinstance(f, str) else None
        if tf is not None and ttl_min is not None and tf - t0 > ttl_min * 60:
            tf = None
        if tf is not None:
            res_end = tf + float(getattr(r, f"{mode}_held_min")) * 60
        else:
            res_end = t0 + ttl_min * 60 if ttl_min is not None else CLOSE
        x = {"r": r, "t0": t0, "tf": tf, "res_end": res_end, "cancelled": False, "would_fill": tf is not None}
        act = [k for k in items if not k["cancelled"] and k["t0"] <= t0 < k["res_end"]]
        if one_per_stock and any(k["r"].sid == r.sid for k in act):
            skipped.append((x, "同標的已有"))
            continue
        used = sum(k["r"].margin for k in act)
        if used + r.margin > cap and policy == "replace":
            pend = sorted((k for k in act if (k["tf"] is None or k["tf"] > t0) and k["r"].stars < r.stars),
                          key=lambda k: (k["r"].stars, -k["t0"]))
            freed, drop = 0, []
            for k in pend:
                if used - freed + r.margin <= cap:
                    break
                freed += k["r"].margin
                drop.append(k)
            if used - freed + r.margin <= cap:
                for k in drop:
                    k["cancelled"], k["tf"], k["res_end"] = True, None, t0
                used -= freed
        if used + r.margin <= cap:
            items.append(x)
        else:
            skipped.append((x, f"保證金(已占{used/1e4:.0f}萬)"))
    return items, skipped


def summarize(df, mode, cap, ttl_min, policy, one_per_stock, fee):
    items, skipped = run(df, mode, cap, ttl_min, policy, one_per_stock)
    fl = [k for k in items if k["tf"] is not None]
    nl = [net(k["r"], mode, "last", fee) for k in fl]
    nb = [net(k["r"], mode, "bid", fee) for k in fl]
    peak = 0
    for t in sorted({k["t0"] for k in items}):
        peak = max(peak, sum(k["r"].margin for k in items if k["t0"] <= t < k["res_end"]))
    return {"口徑": mode, "順序": policy + ("+單檔" if one_per_stock else ""), "手續費": fee,
            "TTL": ttl_min or "收盤", "接受": len(items), "擋掉": len(skipped), "成交": len(fl),
            "損益(末筆價)": int(sum(nl)), "損益(買一價出場)": int(sum(nb)),
            "含費損益/筆": int(sum(nl) / len(fl)) if fl else 0,
            "費稅合計": int(sum(2 * fee + TAX * (getattr(k["r"], f"{mode}_entry") + getattr(k["r"], f"{mode}_last")) * k["r"].shares for k in fl)),
            "峰值萬": round(peak / 1e4)}, (items, skipped)


def summarize_days(d, mode, cap, ttl_min, policy, one_per_stock, fee):
    rows, tot = [], None
    for day, g in d.groupby("day"):
        r, _ = summarize(g, mode, cap, ttl_min, policy, one_per_stock, fee)
        rows.append({"day": day, **r})
    keys = ("接受", "擋掉", "成交", "損益(末筆價)", "損益(買一價出場)", "費稅合計")
    tot = {k: sum(r[k] for r in rows) for k in keys}
    tot.update({"口徑": mode, "順序": rows[0]["順序"], "手續費": fee, "峰值萬": max(r["峰值萬"] for r in rows),
                "含費損益/筆": int(tot["損益(末筆價)"] / tot["成交"]) if tot["成交"] else 0,
                "賺日數": sum(1 for r in rows if r["損益(末筆價)"] > 0), "天數": len(rows)})
    return tot, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=1_470_000)
    ap.add_argument("--csv", nargs="+", default=[str(CSV)])
    ap.add_argument("--fee", type=float, default=176)
    ap.add_argument("--detail", default="qpro")
    a = ap.parse_args()
    d = pd.concat([pd.read_csv(p) for p in a.csv], ignore_index=True)
    d = d[d.in_univ].copy()
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 200)
    print(f"訊號(在可交易名單內)共 {len(d)} 筆,{d.day.nunique()} 天;依日:", d.groupby("day").size().to_dict())
    tab = []
    for mode in ("all", "opt", "qpro", "qcons", "strict"):
        for pol, ops in (("fcfs", False), ("replace", False), ("fcfs", True)):
            tab.append(summarize_days(d, mode, a.cap, None, pol, ops, a.fee)[0])
    print(pd.DataFrame(tab)[["口徑", "順序", "天數", "接受", "擋掉", "成交", "損益(末筆價)", "損益(買一價出場)", "含費損益/筆", "費稅合計", "賺日數", "峰值萬"]].to_string(index=False))
    print(f"\n逐日 口徑={a.detail} 先到先得 手續費={a.fee}")
    print(pd.DataFrame(summarize_days(d, a.detail, a.cap, None, "fcfs", False, a.fee)[1]).drop(columns=["口徑", "手續費", "TTL"]).to_string(index=False))
    print("\n不設保證金上限(cap=1e12)同口徑:")
    print(pd.DataFrame([summarize_days(d, m, 1e12, None, "fcfs", False, a.fee)[0] for m in ("all", "opt", "qpro", "qcons", "strict")])[["口徑", "接受", "成交", "損益(末筆價)", "損益(買一價出場)", "含費損益/筆", "賺日數", "峰值萬"]].to_string(index=False))


if __name__ == "__main__":
    main()
