#!/usr/bin/env python3
"""大台『連次達 10 亮燈後反向』預先登記評估(唯讀;規則見 docs/futures-streak-prereg-20261019.md,凍結後不得改)。

事件定義與 /futflow 的連次欄位**共用同一份程式**(src/futures_flow.py 的 WindowStreak):
  5 秒窗、外盤量>內盤量=+1、內>外=−1、平手中斷、無成交窗跳過;|連次|=10 亮燈;同盤別 300 秒內不重複。
進場價=亮燈窗結算時刻(窗末)的最近成交價;出場價=進場 H 分鐘後的最近成交價;方向=連攻的反方向(反向做)。
只取日盤(FUTURE 流)。探索期 T≤2026-10-16 只印出、標『不計成績』;2026-10-19 在凍結前已被看過,排除;確認期 T≥2026-10-20 才計入判定。

  PYTHONPATH=src .venv/bin/python scripts/research/futures_streak_prereg_eval.py            # 只跑大台(約 1.5 分鐘)
  PYTHONPATH=src .venv/bin/python scripts/research/futures_streak_prereg_eval.py --controls # 加跑小台/微台負對照(約 5 分鐘)
唯讀研究檔,收盤後手動重跑;不新增 launchd、不寫 DB。判定只在『30 日期中、60 日最終』兩個時點有效,中間重跑只是看資料。
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]

import futures_flow as FF  # noqa: E402
from stock_db import DATA_DIR  # noqa: E402

CACHE = DATA_DIR.parent / "cache"
EXPLORE_END = "2026-10-16"       # 探索期:T ≤ 此日,只印出不計成績
EXCLUDED = ("2026-10-17", "2026-10-18", "2026-10-19")   # 10-19 在凍結前已被看過(週末無日盤),不計入任何一邊
CONFIRM_START = "2026-10-20"     # 確認期:T ≥ 此日才計入
PRIMARY = ("TXF", 15)            # 唯一主要檢定:大台、持有 15 分鐘
CONTROLS = ("MXF", "TMF")        # 負對照:預期無效,只報告
SECONDARY_H = (5, 10)            # 次要視窗:只描述,不下結論
COST_BPS = 1.5                   # 來回成本假設(手續費+稅+約 1 點滑價,保守)
INTERIM_DAYS = 30                # 期中檢視:只允許『無效性提前終止』,不得宣告成立
FINAL_DAYS = 60                  # 最終判定:確認期日盤日數達此值才下成立/邊際/不成立的結論
MIN_EVENTS = 100
FUTILITY_T, FUTILITY_MEAN = 0.5, 1.5   # 期中無效性:t ≤ 0.5 且 毛平均 < 1.5 bps
T_THRESH = 3.0                   # |t| 門檻(日群集;探索期挑自約 150 個組合,所以比 2 嚴格)
GROSS_MIN = COST_BPS + 1.0       # 實用門檻:毛報酬至少 2.5 bps(成本 + 1.0 bps 邊際)
WIN_MIN = 0.55                   # 勝率(毛>0)至少 55%


def load_events(root: str):
    """回傳 {T: {'ev': [(t_end, dir, n)], 'tt': 成交時間陣列, 'px': 價格陣列}}(只含日盤)。"""
    files = sorted(glob.glob(str(CACHE / f"{root.lower()}_trades" / f"{root.lower()}_trades_*.jsonl")))
    stream = FF.TradeStream()
    ws = FF.WindowStreak()
    sess: dict = {}

    def on_burst(b):
        if b.stype != "day" or b.trade_time is None:
            return
        ws.add(b)
        sess.setdefault(b.T, ([], []))
        sess[b.T][0].append(b.trade_time / 1e6)
        sess[b.T][1].append(b.last_px)

    merger = FF.BurstMerger(FF.PV[root], on_burst, streaming=True)
    n = 0
    for f in files:
        fd = os.path.basename(f).split("_")[-1][:10]
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                t = stream.feed(line, fd, f)
                if t is None:
                    continue
                if t.dv > 0:
                    merger.add(t)
                n += 1
                if n % 2000 == 0:
                    merger.flush_old()
    merger.flush_all()
    ws.flush_all()
    out = {}
    for (stype, T), st in ws.st.items():
        if stype != "day" or T not in sess:
            continue
        tt, px = np.array(sess[T][0]), np.array(sess[T][1])
        o = np.argsort(tt)
        out[T] = {"ev": [tuple(x) for x in st["lits"]], "tt": tt[o], "px": px[o]}
    return out


def price_at(tt, px, when):
    i = np.searchsorted(tt, when, side="right") - 1
    return px[i] if i >= 0 else np.nan


def fade_returns(data: dict, h_min: int, days):
    """反向做的毛報酬(bps)。回傳 [(T, bps)]。出場時刻超過該盤最後一筆成交就略過。"""
    out = []
    for T in days:
        d = data.get(T)
        if not d:
            continue
        for t_end, direction, _n in d["ev"]:
            t1 = t_end + h_min * 60
            if t1 > d["tt"][-1]:
                continue
            p0, p1 = price_at(d["tt"], d["px"], t_end), price_at(d["tt"], d["px"], t1)
            if p0 == p0 and p1 == p1 and p0:
                out.append((T, -direction * (p1 / p0 - 1) * 1e4))
    return out


def stats(rows):
    """事件平均、以日為群集的 t、勝率、日數。"""
    if len(rows) < 2:
        return None
    r = np.array([x[1] for x in rows])
    days = np.array([x[0] for x in rows])
    m = r.mean()
    g: dict = {}
    for x, d in zip(r - m, days):
        g[d] = g.get(d, 0.0) + x
    se = np.sqrt(sum(v * v for v in g.values())) / len(r)
    dm = np.array([r[days == d].mean() for d in sorted(set(days))])
    return {"n": len(r), "days": len(set(days)), "mean": m, "t": m / se if se > 0 else float("nan"),
            "win": float((r > 0).mean()), "sd": float(r.std()), "day_sd": float(dm.std(ddof=1)) if len(dm) > 1 else float("nan"),
            "day_pos": float((dm > 0).mean())}


def fmt(s):
    if s is None:
        return "—"
    return (f"n={s['n']} 日={s['days']} 毛{s['mean']:+.2f}bps t{s['t']:+.2f} 勝率{s['win']:.0%} "
            f"(事件SD {s['sd']:.1f}、日均SD {s['day_sd']:.1f}、日為正 {s['day_pos']:.0%})")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--controls", action="store_true", help="加跑小台/微台負對照(較慢)")
    args = ap.parse_args()
    roots = (PRIMARY[0],) + (CONTROLS if args.controls else ())
    print("大台『連次=10 亮燈後反向』預先登記評估 · 規則:docs/futures-streak-prereg-20261019.md")
    print(f"探索期 T≤{EXPLORE_END}(不計成績)；{', '.join(EXCLUDED)} 排除；確認期 T≥{CONFIRM_START}(計入判定)；成本假設 {COST_BPS} bps 來回\n")
    verdict = None
    for root in roots:
        data = load_events(root)
        days = sorted(data)
        expl = [d for d in days if d <= EXPLORE_END]
        conf = [d for d in days if d >= CONFIRM_START]
        print(f"== {root}  日盤 {len(days)} 日(探索 {len(expl)}、確認 {len(conf)})")
        for h in (PRIMARY[1],) + SECONDARY_H:
            tag = "【主要】" if (root == PRIMARY[0] and h == PRIMARY[1]) else ("【負對照】" if root in CONTROLS else "【次要·只描述】")
            print(f"  持有{h:>2}分 {tag}")
            print(f"     探索(不計成績): {fmt(stats(fade_returns(data, h, expl)))}")
            sc = stats(fade_returns(data, h, conf))
            print(f"     確認(計入)    : {fmt(sc)}")
            if root == PRIMARY[0] and h == PRIMARY[1]:
                verdict = sc
                n_conf_days = len(conf)
    print("\n== 判定(只看 大台 15 分 確認期)")
    nd = n_conf_days
    nev = 0 if verdict is None else verdict["n"]
    if nd < INTERIM_DAYS:
        print(f"  不足:確認期 {nd} 日 / {nev} 事件。期中檢視要等到 {INTERIM_DAYS} 日、最終判定要等到 {FINAL_DAYS} 日;中間重跑只是看資料,不得據此改規則或下結論。")
        return 0
    if nd < FINAL_DAYS:
        if verdict and verdict["t"] <= FUTILITY_T and verdict["mean"] < FUTILITY_MEAN:
            print(f"  期中·無效性提前終止:{nd} 日 t={verdict['t']:+.2f}、毛平均 {verdict['mean']:+.2f} bps(≤t {FUTILITY_T} 且 <{FUTILITY_MEAN} bps)→ 判定『不成立』,欄位說明改標『確認期未重現』。")
        else:
            print(f"  期中檢視({nd} 日):未達提前終止條件 → 繼續累積到 {FINAL_DAYS} 日。期中不得宣告成立,即使 t 很大。")
        return 0
    if verdict is None or verdict["n"] < MIN_EVENTS:
        print(f"  不足:{nd} 日但事件僅 {nev}(門檻 ≥{MIN_EVENTS})。")
        return 0
    ok_t = verdict["t"] >= T_THRESH
    ok_g = verdict["mean"] >= GROSS_MIN
    ok_w = verdict["win"] >= WIN_MIN
    print(f"  最終({nd} 日): t≥{T_THRESH}: {'過' if ok_t else '未過'}({verdict['t']:+.2f}) | 毛≥{GROSS_MIN} bps: {'過' if ok_g else '未過'}({verdict['mean']:+.2f}) | 勝率≥{WIN_MIN:.0%}: {'過' if ok_w else '未過'}({verdict['win']:.0%})")
    if ok_t and ok_g and ok_w:
        print("  結論:成立——大台連次亮燈後反向在確認期成立。仍只是描述性/研究結論:不等於可實單,實單另需成本實測與風控登記。")
    elif verdict["t"] >= 2.0 and verdict["mean"] > 0:
        print("  結論:邊際——方向一致但未達門檻;不得放寬門檻,可另開登記延長樣本。")
    else:
        print("  結論:不成立——探索期結果未在確認期重現,欄位維持純描述並應在說明欄標註『確認期未重現』。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
