#!/usr/bin/env python3
"""委託管理拆解(掛價固定=買一下1檔=現行):TTL / 流量撤單 / 重新武裝各自貢獻。保證金上限147萬,同分鐘隨機排序K次、同種子配對,對現行A。預先登記,事後描述性。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_agent_entry2_backtest as E  # noqa: E402

PX = dict(px="ticks", ticks=1)
SPECS = {
    "A 現行(不撤)": dict(ttl=None, flow_cancel=False, rearm=False),
    "TTL3+重武裝": dict(ttl=3, flow_cancel=False, rearm=True),
    "TTL5+重武裝": dict(ttl=5, flow_cancel=False, rearm=True),
    "TTL10+重武裝": dict(ttl=10, flow_cancel=False, rearm=True),
    "TTL15+重武裝": dict(ttl=15, flow_cancel=False, rearm=True),
    "TTL5 不重武裝": dict(ttl=5, flow_cancel=False, rearm=False),
    "僅流量撤+重武裝": dict(ttl=None, flow_cancel=True, rearm=True),
    "僅流量撤 不重武裝": dict(ttl=None, flow_cancel=True, rearm=False),
    "B TTL5+流量+重武裝": dict(ttl=5, flow_cancel=True, rearm=True),
    "TTL5+流量 不重武裝": dict(ttl=5, flow_cancel=True, rearm=False),
    "TTL10+流量+重武裝": dict(ttl=10, flow_cancel=True, rearm=True),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ties", type=int, default=100)
    a = ap.parse_args()
    days = [E.Day(d) for d in E.DAYS]
    res, nets, byday = {}, {}, {}
    for nm, mc in SPECS.items():
        cfg = {**PX, **mc, "lag": 5}
        r, _ = E.evaluate(days, cfg, a.ties)
        t = r["同分鐘隨機排序"]
        nets[nm] = np.array(t["每次淨損益"])
        byday[nm] = t["逐日_平均"]
        res[nm] = {k: v for k, v in t.items() if k != "每次淨損益"}
        nc = E.run_all(days, {**PX, **mc, "lag": 5, "cap": 1e12}, None)
        res[nm]["無上限"] = E.digest(nc)
        print(f"[{nm}] 淨均{t['淨_平均']} sd{t['淨_標準差']} 送{t['送出_平均']} 成{t['成交_平均']} 率{t['成交率_平均']} ttl撤{t['撤單ttl_平均']} 流量撤{t['撤單流量_平均']} "
              f"每筆{t['每筆_平均']} 逐日{t['逐日_平均']} | 無上限 淨{res[nm]['無上限']['淨']} 成{res[nm]['無上限']['成交']}", flush=True)
    base = nets["A 現行(不撤)"]
    for nm in SPECS:
        d = nets[nm] - base
        res[nm]["對A配對差"] = {"平均": int(d.mean()), "se": int(d.std(ddof=1) / np.sqrt(len(d))), "優於A比例": round(float((d > 0).mean()), 2),
                              "逐日差": {k: byday[nm][k] - byday["A 現行(不撤)"][k] for k in E.DAYS}}
        print(f"配對差 vs A [{nm}]: {d.mean():+.0f} (se {d.std(ddof=1)/np.sqrt(len(d)):.0f}) 優於 {(d>0).mean():.0%} 逐日差 {res[nm]['對A配對差']['逐日差']}")
    (E.OUT / "mgmt_study_CA-R1_v1.2.0.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
