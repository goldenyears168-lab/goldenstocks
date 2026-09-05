#!/usr/bin/env python3
"""建立統計同儕對照表：近 N 個交易日日報酬相關係數最高的 K 檔。

比 56 個產業碼精準得多（平均最高相關 0.69 vs 產業碼內任意配對），
而且能抓到跨產業碼的上下游／競爭對手關係。
供 collect_limitup_books.py 決定要不要順便追蹤同儕的委託簿。

輸出：${GOLDENSTOCKS_DATA_DIR}/cache/limitup_books/_peer_map.json
"""
from __future__ import annotations
import collections, json, sqlite3, statistics as st, sys
from datetime import datetime, timedelta, timezone
import numpy as np
from stock_db import DATA_DIR, DEFAULT_DB_PATH

OUT = DATA_DIR / "cache" / "limitup_books" / "_peer_map.json"
NDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
K = int(sys.argv[2]) if len(sys.argv) > 2 else 5
TPE = timezone(timedelta(hours=8))


def main() -> None:
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    days = [d for (d,) in con.execute(
        "SELECT DISTINCT trade_date FROM stock_daily_bars WHERE source='finmind' "
        "ORDER BY trade_date DESC LIMIT ?", (NDAYS,))]
    lo, hi = min(days), max(days)
    rows = con.execute("""SELECT stock_id, trade_date, close FROM stock_daily_bars
        WHERE trade_date BETWEEN ? AND ? AND source='finmind'
          AND LENGTH(stock_id)=4 AND stock_id NOT LIKE '00%'""", (lo, hi)).fetchall()
    by = collections.defaultdict(list)
    for s, d, c in rows:
        by[s].append((d, c))
    di = {d: i for i, d in enumerate(sorted(days))}
    syms, M = [], []
    # 覆蓋門檻放寬到 70% 並前向填補：repo 的價格宇宙有已知斷檔（非 ETF 成分股
    # 靠手動 backfill、會退潮），要求 90% 只剩 564/2329 檔建得出同儕。
    for s, l in by.items():
        if len(l) < len(days) * 0.7:
            continue
        v = [np.nan] * len(days)
        for d, c in l:
            if d in di:
                v[di[d]] = c
        a = np.array(v, dtype=float)
        last = np.nan
        for i in range(len(a)):                       # 前向填補（無成交日沿用前價）
            if np.isnan(a[i]) or a[i] <= 0:
                a[i] = last
            else:
                last = a[i]
        if np.isnan(a).any() or (a <= 0).any():       # 開頭仍缺就放棄這檔
            continue
        r = np.diff(np.log(a))
        if not np.isfinite(r).all() or r.std() < 1e-9:
            continue
        syms.append(s); M.append((r - r.mean()) / r.std())
    X = np.array(M)
    C = X @ X.T / X.shape[1]
    assert np.isfinite(C).all(), "相關矩陣有 NaN/inf"
    np.fill_diagonal(C, -9)
    peers = {}
    for i, s in enumerate(syms):
        idx = np.argsort(-C[i])[:K]
        peers[s] = [syms[j] for j in idx if C[i, j] > 0.3]     # 相關太低的不算同儕
    top = [C[i, np.argsort(-C[i])[0]] for i in range(len(syms))]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "built_at": datetime.now(TPE).isoformat(timespec="seconds"),
        "formation": [lo, hi], "n_days": len(days), "k": K,
        "median_top_corr": round(float(st.median(top)), 3),
        "peers": peers}, ensure_ascii=False))
    print(f"同儕表：{len(syms)} 檔 × {X.shape[1]} 日（{lo}~{hi}）"
          f"　最高相關中位 {st.median(top):.2f}　平均同儕數 "
          f"{st.mean(len(v) for v in peers.values()):.1f}")
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
