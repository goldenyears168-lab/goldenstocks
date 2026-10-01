#!/usr/bin/env python3
"""處置風險看板:從注意紀錄算出「還差幾次就被關」(2026-09-30)。

這**不是預測**,是計數 —— 處置的三大觸發條件完全由已公告的注意紀錄決定:
  連續條款A 連續 3 個營業日達第一款
  連續條款B 連續 5 個營業日達第一~第八款任一
  頻率條款   最近 10 個營業日內有 6 天,或最近 30 個營業日內有 12 天

所以只要把注意清單收齊,就能在「當天開盤前」知道誰是「再中一次就進處置」。
2026-09-22 的 2455 全新在開盤時已連 4 日注意 —— 那天盤中追高的風險本來就能事先看到。

輸出 ${GOLDENSTOCKS_DATA_DIR}/data/disposal/disposal_risk.json,給儀表板欄位用:
  {stock_id: {level, label, streak, in10, in30, need, clauses, last_date, in_disposal, release_date}}
  level: 3=🔴倒數1(再中一次就關) / 2=🟠接近 / 1=🟡有注意紀錄 / 0=無 / -1=⬛處置中

用法:PYTHONPATH=src .venv/bin/python scripts/research/disposal_risk_watch.py [--top 30]
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
from collections import defaultdict

import pandas as pd

from stock_db import DATA_DIR

OUT_DIR = DATA_DIR / "disposal"
NOTICE_CSV = OUT_DIR / "notice_history.csv"
WINDOWS_CSV = OUT_DIR / "disposal_windows.csv"
RISK_JSON = OUT_DIR / "disposal_risk.json"

# 觸發門檻(2026-08-10 新制)
STREAK_C1, STREAK_ANY, IN10, IN30 = 3, 5, 6, 12


def _calendar(notices: pd.DataFrame) -> list[str]:
    """真實交易日曆。

    ⚠ 不可以拿「注意公告日」當代理:沒有任何個股被列注意的交易日不會出現在公告裡,
    視窗就會往前多吃幾天,把「最近 30 個營業日」算成更長的區間 → n30 被高估。
    改從 stock_daily_bars 取 distinct trade_date;取不到才退回公告日並印警告。
    """
    try:
        import sqlite3
        from stock_db import DEFAULT_DB_PATH
        con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        lo = min(notices["date"])
        rows = con.execute(
            "select distinct trade_date from stock_daily_bars where trade_date>=? order by trade_date",
            (lo,)).fetchall()
        cal = [r[0] for r in rows]
        if len(cal) >= 30:
            return cal
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ 交易日曆取用失敗，退回公告日代理: {exc}", file=sys.stderr)
    print("⚠ 使用注意公告日當日曆代理，最近 N 個營業日的視窗可能偏長", file=sys.stderr)
    return sorted(notices["date"].unique())


def build(asof: str | None = None) -> dict:
    N = pd.read_csv(NOTICE_CSV, dtype=str)
    if N.empty:
        return {}
    cal = _calendar(N)
    # asof 預設取「今天」而非最後一個注意公告日:注意名單是收盤後才公告,盤前跑的時候
    # 最後公告日是昨天,若拿它當 asof,今天才生效的處置會被漏判成「已達標待公告」。
    # 注意計數本來就只會用到 date <= asof 的紀錄,所以往後推不會提前使用未來資訊。
    asof = asof or max(cal[-1], dt.date.today().isoformat())
    cal = [d for d in cal if d <= asof] or cal
    pos = {d: i for i, d in enumerate(cal)}
    last10 = set(cal[-10:]); last30 = set(cal[-30:])

    W = pd.read_csv(WINDOWS_CSV, dtype=str)
    in_disp, last_end = {}, {}
    for r in W.itertuples():
        sid = str(r.stock_id)
        if str(r.start) <= asof <= str(r.end):
            in_disp[sid] = {"start": str(r.start), "end": str(r.end), "measure": str(r.measure)}
        # ⚠ 計數起點:上一次處置結束日。處置期滿後前面的注意次數不再累積,
        #   否則剛出關的股票會被永遠誤判成「已達標」——2455 在 09-15 出關,
        #   09-16 起重新累積,到 09-22 才滿連續 5 日,這才對得上實際公告日。
        if str(r.end) < asof:
            last_end[sid] = max(last_end.get(sid, ""), str(r.end))

    by_sid: dict[str, list[dict]] = defaultdict(list)
    for r in N[N["date"] <= asof].itertuples():
        by_sid[str(r.stock_id)].append({"date": str(r.date), "clauses": str(r.clauses or ""),
                                        "cum": str(r.cum or ""), "market": str(r.market),
                                        "name": str(getattr(r, "name", "") or "")})
    out = {}
    for sid, recs in by_sid.items():
        # ⚠ 只有第一~第八款會被計入處置的連續/頻率條款;第 9~13 款(當沖比、本益比等)
        #   會被公布注意但**不累積成處置次數**,全部算進去會高估(例如只中第十款的日子)。
        def _valid(rec: dict) -> bool:
            cs = [c for c in rec["clauses"].split("|") if c]
            return any(1 <= int(c) <= 8 for c in cs)
        floor = last_end.get(sid, "")
        vrecs = [r for r in recs if _valid(r) and r["date"] > floor]
        days = sorted({r["date"] for r in vrecs})
        c1days = sorted({r["date"] for r in vrecs if "1" in r["clauses"].split("|")})
        if not days:
            continue
        # 連續天數:從最後一個交易日往回數,每一天都要有注意
        def streak(dayset: set[str]) -> int:
            n = 0
            for i in range(len(cal) - 1, -1, -1):
                if cal[i] <= floor:
                    break
                if cal[i] in dayset:
                    n += 1
                else:
                    break
            return n
        st_any = streak(set(days)); st_c1 = streak(set(c1days))
        # 視窗同樣不回溯到上次處置結束之前
        w10 = {d for d in last10 if d > floor}; w30 = {d for d in last30 if d > floor}
        n10 = len(set(days) & w10); n30 = len(set(days) & w30)
        # 距離各條款還差幾次(再中幾天就觸發)
        need = min(STREAK_ANY - st_any, STREAK_C1 - st_c1 if st_c1 or c1days else 99,
                   IN10 - n10, IN30 - n30)
        need = max(need, 0)
        last = days[-1]
        fresh = pos.get(last, -99) >= len(cal) - 1          # 最後一次注意就是最近交易日
        if sid in in_disp:
            level, label = -1, f"處置中 ~{in_disp[sid]['end']}"
        elif need <= 0:
            level, label = 3, f"已達標待公告 (連{st_any}日/10中{n10}/30中{n30})"
        elif need == 1 and fresh:
            level, label = 3, f"倒數1 (連{st_any}日/10中{n10}/30中{n30})"
        elif need <= 2:
            level, label = 2, f"接近 (連{st_any}日/10中{n10}/30中{n30})"
        elif days:
            level, label = 1, f"注意 {len(days)} 次 (30日內 {n30})"
        else:
            level, label = 0, ""
        out[sid] = {"level": level, "label": label, "streak": st_any, "streak_c1": st_c1,
                    "in10": n10, "in30": n30, "need": need, "last_date": last,
                    "clauses": vrecs[-1]["clauses"], "cum": vrecs[-1]["cum"],
                    "market": vrecs[-1]["market"], "name": vrecs[-1].get("name", ""),
                    "all_clauses": recs[-1]["clauses"],
                    "count_since": floor or None,
                    "in_disposal": sid in in_disp,
                    "release_date": in_disp.get(sid, {}).get("end")}
    return {"asof": asof, "stocks": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--asof", default=None, help="回溯到某日(YYYY-MM-DD),預設最新")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    data = build(args.asof)
    if not data:
        print("無注意紀錄,先跑 fetch_disposal_list.py", file=sys.stderr)
        return 1
    if not args.no_write:
        RISK_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    rows = sorted(data["stocks"].items(), key=lambda kv: (-kv[1]["level"], kv[1]["need"], -kv[1]["in30"]))
    hot = [r for r in rows if r[1]["level"] >= 2]
    print(f"asof {data['asof']}  追蹤 {len(rows)} 檔;🔴倒數1 {sum(1 for _, v in rows if v['level'] == 3)} 檔、"
          f"🟠接近 {sum(1 for _, v in rows if v['level'] == 2)} 檔、⬛處置中 {sum(1 for _, v in rows if v['level'] == -1)} 檔\n")
    print(f"  {'代號':<7}{'名稱':<8}{'市場':<6}{'燈':<4}{'連續':>4}{'10中':>5}{'30中':>5}{'差':>4}  {'最後注意':<12}{'款別':<10}說明")
    for sid, v in (hot or rows)[:args.top]:
        icon = {3: "🔴", 2: "🟠", 1: "🟡", 0: "  ", -1: "⬛"}[v["level"]]
        print(f"  {sid:<7}{v.get('name', ''):<8}{v['market']:<6}{icon:<3}{v['streak']:>4}{v['in10']:>5}{v['in30']:>5}{v['need']:>4}  "
              f"{v['last_date']:<12}{v['clauses'] or '-':<10}{v['label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
