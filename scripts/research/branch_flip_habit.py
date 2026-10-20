#!/usr/bin/env python3
"""隔日沖慣性（小哥口徑的量化版）：某檔個股、某日買超前 N 名分點，近 90 日在該檔是否「至少兩次大買後隔日賣出」。

定義（可調參數）
- 大買事件 (t, b)：分點 b 在 t 日為該股買超（淨買）前 TOPN 名，且淨買 ≥ MINSH × 當日該股總買進量。
- 隔日沖旗標 flip(t, b)：
    A（賣出量口徑）次日賣出量 ≥ ALPHA × 淨買；
    B（淨額口徑）  次日淨額（買−賣）≤ −ALPHA × 淨買（真的把部位轉成賣超；排除「買賣都大」的做市／當沖大戶）。
- 慣犯：評估日前 WIN_DAYS 個日曆日內，該分點在該股的大買事件中 flip 次數 ≥ MINF（只用評估日前已知的事件，次日結果在 t+1 才知，故僅用 t ≤ 評估日前第 2 個交易日）。
唯讀 stocks.db 的 stock_broker_branch_daily。用法：
  PYTHONPATH=src python3 scripts/research/branch_flip_habit.py 8039 2026-10-16 [2026-10-19 ...]
"""
from __future__ import annotations
import collections, sqlite3, sys
from datetime import date, timedelta
from pathlib import Path

DB = Path.home() / "goldenstocks-data/data/stocks.db"
TOPN, ALPHA, MINSH, WIN_DAYS, MINF = 5, 0.7, 0.03, 90, 2


def load(c, sid, since):
    D, name = collections.defaultdict(dict), {}
    for d, b, n, bu, se in c.execute(
            "select trade_date,securities_trader_id,securities_trader,buy,sell from stock_broker_branch_daily "
            "where stock_id=? and trade_date>=? order by trade_date", (sid, since)):
        D[d][b] = (bu or 0, se or 0); name[b] = n
    return D, name


def events(D, dates, topn=TOPN, minsh=MINSH):
    vol = {d: sum(v[0] for v in D[d].values()) for d in dates}
    ev = {}
    for i, t in enumerate(dates[:-1]):
        nxt = dates[i + 1]
        for net, b in sorted(((v[0] - v[1], b) for b, v in D[t].items()), reverse=True)[:topn]:
            if net <= 0 or net < minsh * vol[t]:
                continue
            bu2, se2 = D[nxt].get(b, (0, 0))
            ev[(t, b)] = dict(net=net, sell_next=se2, net_next=bu2 - se2)
    return ev


def flip(e, mode, alpha=ALPHA):
    return e["sell_next"] >= alpha * e["net"] if mode == "A" else e["net_next"] <= -alpha * e["net"]


def habit(ev, b, t, dates, mode):
    idx = {d: i for i, d in enumerate(dates)}
    lo = (date.fromisoformat(t) - timedelta(days=WIN_DAYS)).isoformat()
    h = [e for (tt, bb), e in ev.items() if bb == b and lo <= tt and idx[tt] <= idx[t] - 2]
    return len(h), sum(flip(e, mode) for e in h)


def main():
    sid, targets = sys.argv[1], sys.argv[2:]
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    since = (date.fromisoformat(targets[0]) - timedelta(days=200)).isoformat()
    D, name = load(c, sid, since)
    dates = sorted(D)
    ev = events(D, dates)
    print(f"# {sid} 隔日沖慣性（TOPN={TOPN} ALPHA={ALPHA} MINSH={MINSH:.0%} 視窗{WIN_DAYS}日 慣犯≥{MINF}次）\n")
    for t in targets:
        i = dates.index(t)
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        print(f"## {t} 買超前 {TOPN} 名 → 次日 {nxt}")
        print("| 分點 | 當日淨買(張) | 近90日大買次數 | A隔日賣出≥70% | B次日淨賣≥70% | 慣犯(A) | 慣犯(B) | 次日實際買／賣(張) | 實際A | 實際B |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for net, b in sorted(((v[0] - v[1], b) for b, v in D[t].items()), reverse=True)[:TOPN]:
            nA = habit(ev, b, t, dates, "A"); nB = habit(ev, b, t, dates, "B")
            if nxt:
                bu2, se2 = D[nxt].get(b, (0, 0)); e = dict(net=net, sell_next=se2, net_next=bu2 - se2)
                act = f"{bu2/1000:,.0f}／{se2/1000:,.0f}", "是" if flip(e, "A") else "否", "是" if flip(e, "B") else "否"
            else:
                act = ("—", "—", "—")
            print(f"| {name[b]} | {net/1000:,.0f} | {nA[0]} | {nA[1]} | {nB[1]} | {'是' if nA[1] >= MINF else '否'} | "
                  f"{'是' if nB[1] >= MINF else '否'} | {act[0]} | {act[1]} | {act[2]} |")
        print()
    # 整體：以慣犯預測隔日沖的命中率（本檔、全期）
    for mode in ("A", "B"):
        tp = fp = fn = tn = 0
        for (t, b), e in ev.items():
            n, f = habit(ev, b, t, dates, mode)
            pred, act = f >= MINF, flip(e, mode)
            tp += pred and act; fp += pred and not act; fn += (not pred) and act; tn += (not pred) and not act
        base = (tp + fn) / max(1, tp + fp + fn + tn)
        print(f"整體（口徑{mode}）事件 {tp+fp+fn+tn}：基準隔日沖率 {base:.1%}｜判為慣犯 {tp+fp} 次、命中 {tp/max(1,tp+fp):.1%}｜"
              f"非慣犯的隔日沖率 {fn/max(1,fn+tn):.1%}")


if __name__ == "__main__":
    main()
