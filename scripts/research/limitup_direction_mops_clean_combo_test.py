#!/usr/bin/env python3
"""三因子交集：漲停鎖死 × 鎖死後大戶方向 × 排除 MOPS 重大訊息驅動的鎖死。

假說（Chen, Gao, He, Jiang & Xiong 2019, J. Econometrics，深交所帳戶級資料）：大戶在
漲停鎖死當日被迫買入（排隊排不到）、隔日賣出——鎖死日的淨買方向預測「隔日反轉」，
不是延續。我方三份先驗：
  - limitup-fade-live-prep：漲停隔日開盤放空，真 OOS 6.5 年 Sharpe 2.43，但 20 個訊號日
    檢定力不足（t=0.68）。
  - limitup-durability-opening-verdict：鎖死後逐筆結構決定耐久度，判別力排名第三是
    「漲停價大戶淨方向（淨賣易開）」——本檔重用同一個已驗證特徵（`limitup_lock_survival.
    state()` 的 `big_share`：鎖死後 T 秒內、漲停價上大單（相對當日該股正常單量的 90 分位、
    下限 30 萬）成交佔漲停價總成交量的比例；因為排隊買單看不到、只有打進來吃隊列的賣單
    規模看得到，`big_share` 高＝大戶在鎖死當下持續倒貨進隊列）。
  - mops-announcement-event-verdict：MOPS 重大訊息 96% 盤外發布、事件研究組合淨值全負。

本檔的假說延伸：文獻機制講的是「純粹買盤堆積」導致的鎖死（買不到，不是消息面）。若某次
鎖死是因為當天/前一交易日有 MOPS 重大訊息才發生的，就不是 Chen et al. 講的那種機制——
排除這批「消息驅動型」鎖死後，剩下的「純買盤堆積型」搭配鎖死後大戶方向，對隔日
跳空／反轉（耐久度）的預測力應該更乾淨（t 值更高、AUC 更高）。

── MOPS 排除的時序規則（避免製造新的前視偏誤）───────────────────────────
只用「鎖死時刻之前」的公告：
    window = [T 的前一交易日 00:00:00, T 日鎖死時刻 t0]
在這個窗內若有任一則 MOPS 公告 → 標記該次鎖死為「MOPS 污染」。鎖死之後（即使同一天）
才發布的公告一律不算——那些公告在因果上不可能是這次鎖死的成因，用來排除樣本就是
把還沒發生的事拿來篩選已發生的事。窗的下界用「前一交易日」而非「前一日曆日」，是為了
讓週一鎖死能正確涵蓋週五盤後到週一開盤前這段「隔夜新聞」，而不是被週末切斷。

── 資料來源 ─────────────────────────────────────────────────────────
- Tick 快取（`limitup_tick_lib`）：2026-03-02~2026-09-01，817 檔、580 筆「T 日漲停鎖死
  收盤」episode（含逐筆成交，可算鎖死後大戶方向）。這是唯一能算「鎖死當下大戶方向」的
  資料源（委託簿/大戶淨買都需要逐筆重建，全市場長史沒有這個粒度）。
- MOPS：`~/goldenstocks-data/data/research/mops_archive/mops_raw.db`
  （573,578 筆，2018-01-01~2026-09-03，SSOT：`fetch_mops_announcements.py`）。
- T+1 outcome：`stock_daily_bars`（source='twse_mi_index'）算開盤跳空與盤中反轉，
  不依賴 tick 快取的 `next_open`（後者只到 2026-09），這樣 T+1 close 也拿得到。

唯讀，不下單，不碰 order layer。

用法：
    PYTHONPATH=src:scripts/research .venv/bin/python \
        scripts/research/limitup_direction_mops_clean_combo_test.py
"""
from __future__ import annotations

import bisect
import datetime as dt
import sqlite3
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from limitup_lock_survival import state as lock_state  # noqa: E402
from limitup_tick_lib import episodes, assert_side_convention, auc  # noqa: E402

from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

MOPS_DB = DATA_DIR / "research" / "mops_archive" / "mops_raw.db"
T_WINDOW = 300.0          # 鎖死後觀察窗（秒）＝既有耐久度研究驗證過的「前 5 分」尺度
CAL_START, CAL_END = "2026-01-01", "2026-10-15"


# ── 交易日曆 / MOPS 前視安全查詢 ──────────────────────────────────────

def load_calendar(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily_bars "
        "WHERE source='twse_mi_index' AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (CAL_START, CAL_END),
    ).fetchall()
    return [r[0] for r in rows]


def prev_trading_day(cal: list[str], date: str) -> str | None:
    i = bisect.bisect_left(cal, date)
    return cal[i - 1] if i > 0 else None


def next_trading_day(cal: list[str], date: str) -> str | None:
    i = bisect.bisect_left(cal, date)
    if i < len(cal) and cal[i] == date and i + 1 < len(cal):
        return cal[i + 1]
    return None


def load_mops_by_stock(mops_db: Path) -> dict[str, list[tuple[str, str]]]:
    con = sqlite3.connect(f"file:{mops_db}?mode=ro", uri=True)
    rows = con.execute("SELECT stock_id, ann_date, ann_time FROM mops").fetchall()
    con.close()
    by_stock: dict[str, list[tuple[str, str]]] = {}
    for sid, d, t in rows:
        by_stock.setdefault(sid, []).append((d, t or "00:00:00"))
    for sid, lst in by_stock.items():
        lst.sort()
    return by_stock


def mops_tainted(by_stock, stock_id: str, prev_day: str | None, lock_dt: dt.datetime) -> bool:
    """鎖死時刻之前（含前一交易日）是否已有 MOPS 重大訊息公告。"""
    anns = by_stock.get(stock_id)
    if not anns or prev_day is None:
        return False
    lock_date = lock_dt.date().isoformat()
    for d, t in anns:
        if d < prev_day:
            continue
        if d > lock_date:
            break  # 已排序；超過鎖死當天的公告不可能是成因，之後的也不用看了
        try:
            ts = dt.datetime.fromisoformat(f"{d} {t}")
        except ValueError:
            continue
        if ts <= lock_dt:
            return True
    return False


# ── 統計小工具 ────────────────────────────────────────────────────────

def cluster_t(vals: list[float], dates: list[str]) -> float:
    g: dict[str, list[float]] = {}
    for v, d in zip(vals, dates):
        g.setdefault(d, []).append(v)
    day_means = [sum(vs) / len(vs) for vs in g.values()]
    if len(day_means) < 3:
        return float("nan")
    m, sd = st.mean(day_means), st.pstdev(day_means)
    return m / (sd / len(day_means) ** 0.5) if sd else float("nan")


def mde(vals: list[float], dates: list[str], target_t: float = 2.0) -> tuple[float, int]:
    """回傳（達到 target_t 所需的日聚類數估計, 目前日聚類數）。"""
    g: dict[str, list[float]] = {}
    for v, d in zip(vals, dates):
        g.setdefault(d, []).append(v)
    day_means = [sum(vs) / len(vs) for vs in g.values()]
    n = len(day_means)
    if n < 3:
        return float("nan"), n
    m, sd = st.mean(day_means), st.pstdev(day_means)
    if m == 0:
        return float("nan"), n
    n_needed = (target_t * sd / m) ** 2
    return n_needed, n


def summarize(name: str, rows: list[dict]) -> None:
    if not rows:
        print(f"  {name}: n=0")
        return
    dates = [r["date"] for r in rows]
    n_days = len(set(dates))
    print(f"  {name}: n={len(rows)}  獨立日={n_days}")
    for key, label in (("gap", "T+1跳空"), ("fade", "T+1開→收(反轉)"), ("netcc", "T收→T+1收")):
        vals = [r[key] for r in rows]
        m = st.mean(vals)
        need, have = mde(vals, dates)
        print(f"    {label:16s} mean={m*100:+6.2f}%  cluster-t={cluster_t(vals, dates):6.2f}"
              f"  (MDE: 需要約 {need:.0f} 個獨立日才夠 t=2，現有 {have})")


def main() -> int:
    conn_ro = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    cal = load_calendar(conn_ro)
    print(f"[calendar] {len(cal)} 個交易日 {cal[0]}~{cal[-1]}")

    by_stock = load_mops_by_stock(MOPS_DB)
    print(f"[mops] {sum(len(v) for v in by_stock.values()):,} 筆公告，{len(by_stock)} 檔")

    # 一次撈完 tick 快取窗內全部個股日 K，避免逐檔查詢
    px_rows = conn_ro.execute(
        "SELECT stock_id, trade_date, open, close FROM stock_daily_bars "
        "WHERE source='twse_mi_index' AND trade_date BETWEEN '2026-02-15' AND '2026-09-20'"
    ).fetchall()
    px = {(sid, d): (o, c) for sid, d, o, c in px_rows}

    eps = episodes(split=None)
    assert_side_convention(eps)
    locked = [e for e in eps if e.y_close]
    print(f"[episodes] tick 快取共 {len(eps)} 筆觸停 episode，其中 T 日鎖死收盤 {len(locked)} 筆")

    rows: list[dict] = []
    skipped_no_state = skipped_no_bars = skipped_no_next = 0
    for e in locked:
        s = lock_state(e, T_WINDOW)
        if s is None:
            skipped_no_state += 1
            continue
        date = e.date
        nxt = next_trading_day(cal, date)
        if nxt is None:
            skipped_no_next += 1
            continue
        if (e.stock_id, date) not in px or (e.stock_id, nxt) not in px:
            skipped_no_bars += 1
            continue
        _, close_T = px[(e.stock_id, date)]
        open_T1, close_T1 = px[(e.stock_id, nxt)]
        if not close_T or not open_T1 or not close_T1:
            skipped_no_bars += 1
            continue

        prev_day = prev_trading_day(cal, date)
        lock_dt = dt.datetime.fromisoformat(date) + dt.timedelta(seconds=s["t0"])
        tainted = mops_tainted(by_stock, e.stock_id, prev_day, lock_dt)

        rows.append(dict(
            stock_id=e.stock_id, date=date,
            big_share=s["big_share"], consume=s["consume"], solid=int(s["solid"]),
            broke=int(s["broke_in_win"]), tainted=tainted,
            gap=open_T1 / close_T - 1, fade=close_T1 / open_T1 - 1,
            netcc=close_T1 / close_T - 1,
        ))

    print(f"[merge] 可用樣本 {len(rows)}（缺 state={skipped_no_state}，缺次一交易日={skipped_no_next}，"
          f"缺日K={skipped_no_bars}）")

    n_tainted = sum(r["tainted"] for r in rows)
    print(f"[mops] 污染（鎖死時刻前已有重大訊息）{n_tainted} / {len(rows)} "
          f"= {n_tainted/len(rows)*100:.1f}%")

    all_rows = rows
    clean_rows = [r for r in rows if not r["tainted"]]
    tainted_rows = [r for r in rows if r["tainted"]]

    print(f"\n{'='*78}\n【基準線：未排除 MOPS】")
    summarize("全樣本", all_rows)

    print(f"\n{'='*78}\n【排除 MOPS 污染後：純買盤堆積型鎖死】")
    summarize("MOPS-clean", clean_rows)
    summarize("MOPS-tainted（對照組，樣本小僅供參考）", tainted_rows)

    # big_share（鎖死後大戶淨方向代理，越高＝大單賣壓佔比越高）與 T+1 outcome 的關係
    print(f"\n{'='*78}\n【搭配大戶方向（big_share 中位數分組）】")
    for label, pool in (("全樣本", all_rows), ("MOPS-clean", clean_rows)):
        valid = [r for r in pool if r["big_share"] == r["big_share"]]  # drop NaN
        if len(valid) < 20:
            print(f"  {label}: 樣本不足（n={len(valid)}），略過分組")
            continue
        med = st.median(r["big_share"] for r in valid)
        hi = [r for r in valid if r["big_share"] > med]
        lo = [r for r in valid if r["big_share"] <= med]
        print(f"  -- {label}（大戶方向中位數={med:.3f}）--")
        summarize("大戶賣壓佔比 高於中位（更可能有大戶倒貨）", hi)
        summarize("大戶賣壓佔比 低於中位", lo)

        # AUC：big_share 對「T+1 反轉」（netcc<0）與「T 日同日打開（不耐久）」的判別力
        y_rev = [1 if r["netcc"] < 0 else 0 for r in valid]
        y_brk = [r["broke"] for r in valid]
        score = [-r["big_share"] for r in valid]  # 分數＝−big_share（大戶越賣→越不耐久/預期越可能反轉）
        print(f"    big_share 對『T+1 收盤反轉』AUC={auc(y_rev, score):.3f}  "
              f"對『T 日同日打開(耐久度)』AUC={auc(y_brk, score):.3f}  (n={len(valid)})")

    print(f"\n{'='*78}\n【三級標記摘要（供回報用，非結論本身）】")
    print("  [事實] 以上 mean / cluster-t / AUC 皆可由本腳本重算。")
    print("  [推論] MOPS 污染窗＝[前一交易日00:00, 鎖死時刻t0]，若窗定義改變數字會變；")
    print("         big_share 沿用 limitup_lock_survival.state() 既有定義（PIT，只用鎖死後"
          f"{T_WINDOW:.0f}秒內的 tick）。")
    print("  [猜測] 樣本量是否足以區分『MOPS-clean 比未過濾更乾淨』——見上方 MDE 對照現有獨立日數。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
