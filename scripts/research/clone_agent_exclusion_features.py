#!/usr/bin/env python3
"""出卡當下,儀表板過濾欄位的「全表排名」特徵:近兩日大戶/全日大戶/30分散戶/30分大戶(淨額,元)。

排名母體=儀表板全部列(raw 內有成交的全部標的);規則逐字對齊 index 頁過濾器:
  留前 pct%:嚴格較大者數 ahead + 平手/2 > pct% × n → 淘汰;留後 pct% 反向。
輸入:scratch/cab_{day}.csv 的每張卡(全部代理人訊號)。輸出 scratch/clone_agent_exclusion_features.csv。
"""
from __future__ import annotations

import bisect
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_biglot_decision_replay as rp  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"]
TZ = "Asia/Taipei"


def dashboard_sids():
    import urllib.request
    h = urllib.request.urlopen("http://localhost:8771/frag", timeout=30).read().decode("utf-8")
    return sorted(set(re.findall(r"href='/stock\?sid=(\w+)'", h)))


def prev_trading_day(day):
    ds = sorted(p.stem[4:] for p in rp.RAW_DIR.glob("raw_*.jsonl") if p.stat().st_size > 10_000_000 and p.stem[4:] < day)
    return ds[-1]


def window_sum(cum, ts, t_end, sec):
    j = bisect.bisect_right(ts, t_end)
    i = bisect.bisect_left(ts, t_end - sec) if sec else 0
    return cum[j] - cum[i], j


def main():
    sids = set(dashboard_sids())
    print("dashboard sids", len(sids))
    rows = []
    cache = {}
    for day in DAYS:
        cands = pd.read_csv(S / f"cab_{day}.csv")
        pday = prev_trading_day(day)
        for d in (day, pday):
            if d not in cache:
                cache[d] = rp.replay_day(d, sids)
                print(d, "有資料標的", sum(1 for a in cache[d].values() if a["ts"]), flush=True)
        today, prev = cache[day], cache[pday]
        prev_full = {s: (a["cum_big"][-1] if a["ts"] else None) for s, a in prev.items()}
        for c in cands.itertuples():
            t = pd.Timestamp(f"{day} {c.time}", tz=TZ).timestamp()
            vals = {}
            for s, a in today.items():
                ts = a["ts"]
                if not ts or ts[0] > t:
                    continue
                big30, j = window_sum(a["cum_big"], ts, t, 1800)
                ret30, _ = window_sum(a["cum_ret"], ts, t, 1800)
                bigday = a["cum_big"][j]
                pf = prev_full.get(s)
                vals[s] = {"big30": big30, "ret30": ret30, "bigday": bigday,
                           "big2d": None if pf is None else pf + bigday}
            rec = {"day": day, "time": c.time, "sid": str(c.sid), "prev_day": pday, "n_pool": len(vals)}
            for k in ("big30", "ret30", "bigday", "big2d"):
                pool = [v[k] for v in vals.values() if v[k] is not None]
                x = vals.get(str(c.sid), {}).get(k)
                rec[k] = x
                n = len(pool)
                if x is None or n == 0:
                    rec[k + "_n"] = n
                    rec[k + "_frac_larger"] = None
                    rec[k + "_frac_smaller"] = None
                    continue
                larger = sum(1 for q in pool if q > x)
                smaller = sum(1 for q in pool if q < x)
                tie = sum(1 for q in pool if q == x)
                rec[k + "_n"] = n
                rec[k + "_frac_larger"] = (larger + tie / 2) / n    # 越大=排名越前面(越小的數字代表越強)
                rec[k + "_frac_smaller"] = (smaller + tie / 2) / n
            rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(S / "clone_agent_exclusion_features.csv", index=False)
    print("wrote", len(out))


if __name__ == "__main__":
    sys.exit(main())
