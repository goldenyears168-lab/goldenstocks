#!/usr/bin/env python3
"""持倉期間的「排除條件」逐 30 秒格點:四個過濾條件在金額口徑與%口徑下,各標的當下是否落在淘汰區。

特徵(同儀表板過濾欄):big2d=前一交易日全日大戶淨+今日累計;bigday=全日大戶淨;big30/ret30=近30分大戶/散戶淨。
  金額口徑 = 淨額(元);%口徑 = 淨額 ÷ 同窗口總成交額。母體=儀表板全部列(排名時只計當下有值者)。
淘汰規則同儀表板(留 70%):A big2d、B bigday、D big30 → 排名較後 30% 淘汰;C ret30 → 排名較前 30% 淘汰。
輸出 scratch/exit_flowgrid.pkl:{day: {t, sids, flags_amt, flags_pct}},flags 為 [格點, 標的] 的 4-bit 位元(A=1,B=2,C=4,D=8)。
"""
from __future__ import annotations

import bisect
import pickle
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_biglot_decision_replay as rp  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"]
TZ = "Asia/Taipei"
KEEP = 0.70
STEP = 30


def dashboard_sids():
    h = urllib.request.urlopen("http://localhost:8771/frag", timeout=60).read().decode("utf-8")
    return sorted(set(re.findall(r"href='/stock\?sid=(\w+)'", h)))


def prev_trading_day(day):
    ds = sorted(p.stem[4:] for p in rp.RAW_DIR.glob("raw_*.jsonl") if p.stat().st_size > 10_000_000 and p.stem[4:] < day)
    return ds[-1]


def epoch(day, hms):
    import pandas as pd
    return pd.Timestamp(f"{day} {hms}", tz=TZ).timestamp()


def flag_matrix(F, kind):
    """F: [格點, 標的] 數值(nan=無值)。kind='low' 後30%淘汰(frac_larger>KEEP);'high' 前30%淘汰(frac_smaller>KEEP)。"""
    out = np.zeros(F.shape, dtype=bool)
    for g in range(F.shape[0]):
        row = F[g]
        ok = ~np.isnan(row)
        n = int(ok.sum())
        if n == 0:
            continue
        v = row[ok]
        larger = (v[None, :] > v[:, None]).sum(1)
        smaller = (v[None, :] < v[:, None]).sum(1)
        tie = (v[None, :] == v[:, None]).sum(1)
        frac = ((larger if kind == "low" else smaller) + tie / 2) / n
        out[g, np.where(ok)[0]] = frac > KEEP
    return out


def main():
    sids = dashboard_sids()
    print("dashboard sids", len(sids), flush=True)
    cache = {}
    res = {}
    for day in DAYS:
        pday = prev_trading_day(day)
        for d in (day, pday):
            if d not in cache:
                cache[d] = rp.replay_day(d, set(sids))
                print(d, "有資料標的", sum(1 for a in cache[d].values() if a["ts"]), flush=True)
        today, prev = cache[day], cache[pday]
        grid = np.arange(epoch(day, "09:00:30"), epoch(day, "13:30:00") + 1, STEP)
        n_g, n_s = len(grid), len(sids)
        feats = {u: {k: np.full((n_g, n_s), np.nan) for k in ("A", "B", "C", "D")} for u in ("amt", "pct")}
        for si, s in enumerate(sids):
            a, pa = today[s], prev.get(s)
            if not a["ts"]:
                continue
            ts = a["ts"]
            pbig = pa["cum_big"][-1] if pa and pa["ts"] else None
            pamt = pa["cum_amt"][-1] if pa and pa["ts"] else None
            for gi, t in enumerate(grid):
                if ts[0] > t:
                    continue
                j = bisect.bisect_right(ts, t)
                i = bisect.bisect_left(ts, t - 1800)
                big30 = a["cum_big"][j] - a["cum_big"][i]
                ret30 = a["cum_ret"][j] - a["cum_ret"][i]
                tot30 = a["cum_amt"][j] - a["cum_amt"][i]
                bigday, totday = a["cum_big"][j], a["cum_amt"][j]
                big2d = None if pbig is None else pbig + bigday
                tot2d = None if pamt is None else pamt + totday
                feats["amt"]["A"][gi, si] = np.nan if big2d is None else big2d
                feats["amt"]["B"][gi, si] = bigday
                feats["amt"]["C"][gi, si] = ret30
                feats["amt"]["D"][gi, si] = big30
                feats["pct"]["A"][gi, si] = np.nan if big2d is None or not tot2d else big2d / tot2d
                feats["pct"]["B"][gi, si] = bigday / totday if totday else np.nan
                feats["pct"]["C"][gi, si] = ret30 / tot30 if tot30 else np.nan
                feats["pct"]["D"][gi, si] = big30 / tot30 if tot30 else np.nan
        rec = {"t": grid, "sids": sids}
        for u in ("amt", "pct"):
            fl = np.zeros((n_g, n_s), dtype=np.int8)
            for bit, k, kind in ((1, "A", "low"), (2, "B", "low"), (4, "C", "high"), (8, "D", "low")):
                fl |= (flag_matrix(feats[u][k], kind).astype(np.int8) * bit).astype(np.int8)
            rec[f"flags_{u}"] = fl
            rec[f"feat_{u}"] = feats[u]
        res[day] = rec
        print(day, "格點完成", flush=True)
    pickle.dump(res, open(S / "exit_flowgrid.pkl", "wb"))
    print("wrote", S / "exit_flowgrid.pkl")


if __name__ == "__main__":
    sys.exit(main())
