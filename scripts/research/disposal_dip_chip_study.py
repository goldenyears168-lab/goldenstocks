#!/usr/bin/env python3
"""處置股「跌多少 × 籌碼」進場研究(2026-10-08)。

動機:網路上的處置策略多以「第 N 天進場」為軸(延後 5 天等),jack 的假設是真正的軸
是「跌多少」與「籌碼條件」。本檔直接檢定,並對照第 N 天基準。

實驗:
  E1 做多:處置期內以『距處置前高跌幅』或『距處置首日收盤累幅』取代第 N 天;再疊法人/量縮/融資/均線
  E2 躲處置:倒數 1 的個股,開盤已達第一款門檻、收盤卻跌回門檻下的比例,是否高於對照組
  E3 出關日:出關前一日的均線/法人/量縮狀態,對出關日開盤進場報酬有無區分力
  E4 放空:只用 PIT 資訊(倒數 1 ∧ 收盤已達第一款價格門檻)的真實可執行放空規則

紀律:報酬為對 0050 的超額、每筆扣 0.44% 來回;t 值按進場日聚類(事件在同日群聚);
  同時報 MDE=2.8×SE;進場日跌停鎖死(≤-9.3%)排除;進場有『收盤價進』與『次日開盤進(延遲)』兩種;
  IS=2024 以前、OOS=2024-01 起至新制前、新制(2026-08-10 起 5 日 2 分盤)另列僅作描述。
  法人表去重 twse_t86/tpex_insti 優先於 finmind;融資表 twse_mi_margn 優先。缺值一律 NaN,不補 0。

用法:PYTHONPATH=src nice -n 10 .venv/bin/python scripts/research/disposal_dip_chip_study.py
"""
from __future__ import annotations
import importlib.util
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from stock_db import DATA_DIR, DEFAULT_DB_PATH

COST = 0.44
NEW_RULE = pd.Timestamp("2026-08-10")
SRC_PRI = {"twse_mi_index": 0, "tpex_daily": 0, "finmind": 1, "yfinance": 2}
LOCK = -9.3


def _con():
    return sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)


def load_windows():
    p = Path(__file__).with_name("disposal_window_stats.py")
    spec = importlib.util.spec_from_file_location("dws", p)
    M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
    W, _ = M.load()
    return W


def load_bars(ids, since):
    con = _con()
    q = ",".join("?" * len(ids))
    P = pd.read_sql(f"select stock_id,trade_date,open,close,volume,source from stock_daily_bars "
                    f"where stock_id in ({q}) and trade_date>=?", con, params=[*ids, since])
    P["pri"] = P["source"].map(SRC_PRI).fillna(9)
    P = P.sort_values(["stock_id", "trade_date", "pri"]).drop_duplicates(["stock_id", "trade_date"])
    P["trade_date"] = pd.to_datetime(P["trade_date"])
    P = P[(P["close"] > 0) & (P["open"] > 0)]
    assert not P.duplicated(["stock_id", "trade_date"]).any()
    return P.sort_values(["stock_id", "trade_date"]).reset_index(drop=True)


def load_inst(ids, since):
    con = _con()
    q = ",".join("?" * len(ids))
    D = pd.read_sql(f"select stock_id,trade_date,foreign_net,investment_trust_net,source from stock_institutional_daily "
                    f"where stock_id in ({q}) and trade_date>=?", con, params=[*ids, since])
    D["pri"] = D["source"].map({"twse_t86": 0, "tpex_insti": 0, "finmind": 1}).fillna(9)
    D = D.sort_values(["stock_id", "trade_date", "pri"]).drop_duplicates(["stock_id", "trade_date"])
    assert not D.duplicated(["stock_id", "trade_date"]).any()
    D["trade_date"] = pd.to_datetime(D["trade_date"])
    D["inst"] = D["foreign_net"] + D["investment_trust_net"]
    return {s: g.set_index("trade_date")["inst"] for s, g in D.groupby("stock_id")}


def load_margin(ids, since):
    con = _con()
    q = ",".join("?" * len(ids))
    D = pd.read_sql(f"select stock_id,trade_date,margin_balance,source from stock_margin_daily "
                    f"where stock_id in ({q}) and trade_date>=?", con, params=[*ids, since])
    D["pri"] = D["source"].map({"twse_mi_margn": 0, "finmind": 1}).fillna(9)
    D = D.sort_values(["stock_id", "trade_date", "pri"]).drop_duplicates(["stock_id", "trade_date"])
    assert not D.duplicated(["stock_id", "trade_date"]).any()
    D["trade_date"] = pd.to_datetime(D["trade_date"])
    return {s: g.set_index("trade_date")["margin_balance"] for s, g in D.groupby("stock_id")}


def load_bench():
    con = _con()
    B = pd.read_sql("select trade_date,close,source from stock_daily_bars where stock_id='0050' and trade_date>='2019-01-01'", con)
    B["pri"] = B["source"].map(SRC_PRI).fillna(9)
    B = B.sort_values(["trade_date", "pri"]).drop_duplicates("trade_date")
    B["trade_date"] = pd.to_datetime(B["trade_date"])
    return B.set_index("trade_date")["close"].sort_index()


# ───────────────────────── 統計工具 ─────────────────────────
def cstat(vals, keys, min_n=15):
    x = np.asarray(vals, float); k = np.asarray(keys)
    m = ~np.isnan(x); x = x[m]; k = k[m]
    n = len(x)
    if n < min_n:
        return {"n": n}
    mu = x.mean()
    s = pd.Series(x - mu).groupby(k).sum()
    se = float(np.sqrt((s ** 2).sum()) / n)
    return {"n": n, "mean": mu, "med": float(np.median(x)), "win": float((x > 0).mean() * 100),
            "t": mu / se if se > 0 else np.nan, "mde": 2.8 * se, "ng": len(s)}


def line(label, s, w=34):
    if s.get("mean") is None:
        return f"  {label:<{w}}{s['n']:>5}  n<15"
    return (f"  {label:<{w}}{s['n']:>5}{s['mean']:+8.2f}{s['med']:+8.2f}{s['win']:>5.0f}%{s['t']:+7.2f}  MDE{s['mde']:5.2f}")


HDR = f"  {'分組':<34}{'n':>5}{'均%':>8}{'中位':>8}{'正%':>6}{'t(聚類)':>8}  {'MDE':>7}"


# ───────────────────────── 事件×進場日面板 ─────────────────────────
def build_panel(W, P, INST, MRG, B):
    def bret(a, b):
        x = B.asof(a); y = B.asof(b)
        return (y / x - 1) * 100 if (x == x and y == y and x > 0) else np.nan

    rows = []
    ev_rows = []
    for sid, g in P.groupby("stock_id"):
        dates = g["trade_date"].values
        dts = list(g["trade_date"])
        cl = g["close"].values.astype(float); op = g["open"].values.astype(float); vol = g["volume"].values.astype(float)
        n = len(g)
        ins_s = INST.get(sid); mg_s = MRG.get(sid)
        inst = ins_s.reindex(g["trade_date"]).values.astype(float) if ins_s is not None else np.full(n, np.nan)
        mrg = mg_s.reindex(g["trade_date"]).values.astype(float) if mg_s is not None else np.full(n, np.nan)
        for e in W[W["stock_id"] == sid].itertuples():
            i0 = int(np.searchsorted(dates, np.datetime64(e.start), "left"))
            i1 = int(np.searchsorted(dates, np.datetime64(e.end), "right")) - 1
            if i1 < i0 or i0 < 20 or i1 + 1 >= n:
                continue
            if (dts[i0] - e.start).days > 4 or (dts[i1 + 1] - e.end).days > 6:
                continue
            a, d0, L, r0 = i0 - 1, i0, i1 - i0 + 1, i1 + 1
            if L < 2:
                continue
            peak10 = cl[a - 9:a + 1].max()
            volbase = np.nanmean(vol[a - 19:a - 4])
            if not volbase or volbase <= 0:
                continue
            pre_in = inst[a - 9:a + 1]
            pre_v = vol[a - 9:a + 1]
            inst_pre = pre_in.sum() / pre_v.sum() * 100 if not np.isnan(pre_in).any() else np.nan
            # ── 事件級:空刀與出關日
            run6 = (cl[a] / cl[a - 5] - 1) * 100
            ev = {"sid": sid, "start": e.start, "year": e.start.year, "rep": bool(e.repeat_b),
                  "new": e.start >= NEW_RULE, "L": L, "run6": run6, "inst_pre": inst_pre,
                  "short": -((cl[d0] / cl[a] - 1) * 100 - bret(dts[a], dts[d0])) - COST,
                  "entry_date": dts[a]}
            k = i1  # 出關前一日(窗內最後一日)
            win_in = inst[d0:k + 1]
            ev["end_inst"] = win_in.sum() / vol[d0:k + 1].sum() * 100 if not np.isnan(win_in).any() else np.nan
            ev["end_ma5"] = cl[k] > cl[k - 4:k + 1].mean()
            ev["end_ma20"] = cl[k] > cl[k - 19:k + 1].mean()
            ev["end_cum"] = (cl[k] / cl[d0] - 1) * 100
            ev["end_dd"] = (cl[k] / peak10 - 1) * 100
            ev["end_vr"] = np.nanmean(vol[d0:k + 1]) / volbase
            ev["rel_date"] = dts[r0]
            ev["rel_o2c"] = (cl[r0] / op[r0] - 1) * 100 - bret(dts[r0], dts[r0]) * 0 - COST
            ev["rel_o2c3"] = ((cl[r0 + 2] / op[r0] - 1) * 100 - bret(dts[r0 - 1], dts[r0 + 2]) - COST) if r0 + 2 < n else np.nan
            ev["rel_o2c5"] = ((cl[r0 + 4] / op[r0] - 1) * 100 - bret(dts[r0 - 1], dts[r0 + 4]) - COST) if r0 + 4 < n else np.nan
            ev["rel_gap"] = (op[r0] / cl[k] - 1) * 100
            ev_rows.append(ev)
            # ── 進場候選日面板
            for j in range(L):
                kk = d0 + j
                win_i = inst[d0:kk + 1]
                inst_in = win_i.sum() / vol[d0:kk + 1].sum() * 100 if not np.isnan(win_i).any() else np.nan
                m0, m1 = mrg[a], mrg[kk]
                day_ret = (cl[kk] / cl[kk - 1] - 1) * 100
                r_p0 = (op[r0] / cl[kk] - 1) * 100 - bret(dts[kk], dts[r0]) - COST
                r_p1 = ((op[r0] / op[kk + 1] - 1) * 100 - bret(dts[kk], dts[r0]) - COST) if kk + 1 <= i1 else np.nan
                rows.append({
                    "sid": sid, "start": e.start, "year": e.start.year, "rep": bool(e.repeat_b), "new": e.start >= NEW_RULE,
                    "j": j, "day": j + 1, "L": L, "date": dts[kk],
                    "dd_peak": (cl[kk] / peak10 - 1) * 100, "dd_d0": (cl[kk] / cl[d0] - 1) * 100,
                    "day_ret": day_ret, "lock": day_ret <= LOCK,
                    "inst_in": inst_in, "inst_pre": inst_pre,
                    "vr": np.nanmean(vol[d0:kk + 1]) / volbase,
                    "mrg": ((m1 / m0 - 1) * 100) if (m0 == m0 and m1 == m1 and m0 > 0) else np.nan,
                    "ma5": cl[kk] > cl[max(kk - 4, 0):kk + 1].mean(), "ma20": cl[kk] > cl[max(kk - 19, 0):kk + 1].mean(),
                    "p0": r_p0, "p1": r_p1,
                    "exit_lock": (op[r0] / cl[i1] - 1) * 100 <= LOCK,
                })
    return pd.DataFrame(rows), pd.DataFrame(ev_rows)


def split3(df):
    return {"IS(≤2023)": df[(df["year"] <= 2023)], "OOS(2024~新制前)": df[(df["year"] >= 2024) & (~df["new"])],
            "新制(僅描述)": df[df["new"]]}


def report_group(title, df, col, key="date"):
    print(f"\n── {title} ──\n{HDR}")
    print(line("全期", cstat(df[col], df[key])))
    for nm, d in split3(df).items():
        print(line(nm, cstat(d[col], d[key])))


# ───────────────────────── E1 ─────────────────────────
def first_hit(Pn, X, measure="dd_peak"):
    d = Pn[(~Pn["lock"]) & (Pn[measure] <= -X)]
    return d.sort_values("j").groupby(["sid", "start"], as_index=False).first()


def e1(Pn):
    print("\n" + "=" * 100 + "\nE1  做多:『跌多少 × 籌碼』vs『第 N 天』(出場=出關日開盤;超額、扣 0.44%)\n" + "=" * 100)
    print(f"面板:{Pn.groupby(['sid','start']).ngroups} 事件 × {len(Pn)} 個進場候選日;舊制 {Pn[~Pn['new']].groupby(['sid','start']).ngroups} / 新制 {Pn[Pn['new']].groupby(['sid','start']).ngroups}")
    old = Pn[~Pn["new"]]

    print("\n[1] 對帳:既有『多刀』= 累犯 ∧ 第3天 ∧ 累幅(距首日收)>-10%(舊制;收盤價進)")
    r = old[(old["rep"]) & (old["day"] == 3) & (old["dd_d0"] > -10)]
    print(HDR); print(line("累犯∧第3天∧dd_d0>-10", cstat(r["p0"], r["date"])))
    print("  (舊腳本結果:n=369 均+5.01% 中位+3.77 t+4.77;n 不同是因 windows 檔去重/來源優先序,方向應一致)")

    print("\n[2] 第 N 天基準(舊制、收盤價進、出關日開盤出;無任何條件)")
    print(HDR)
    for nm, sub in (("全部", old), ("累犯", old[old["rep"]]), ("初犯", old[~old["rep"]])):
        for dd in (1, 2, 3, 4, 5):
            s = cstat(sub[(sub["day"] == dd) & (~sub["lock"])]["p0"], sub[(sub["day"] == dd) & (~sub["lock"])]["date"])
            print(line(f"{nm} 第{dd}天", s))

    print("\n[3] 軸對照:day × 跌幅(距處置前10日高)分桶,舊制全部事件、收盤價進;格內 均%(n)")
    bins = [(-100, -30), (-30, -20), (-20, -10), (-10, -5), (-5, 100)]
    labs = ["≤-30", "-30~-20", "-20~-10", "-10~-5", ">-5"]
    dcols = [(1, 1), (2, 2), (3, 3), (4, 5), (6, 99)]
    print(f"  {'dd_peak\\day':<10}" + "".join(f"{('第'+str(a)+('~'+str(b) if b!=a else '')+'天'):>16}" for a, b in dcols))
    for (lo, hi), lb in zip(bins, labs):
        cells = []
        for a, b in dcols:
            d = old[(old["day"] >= a) & (old["day"] <= b) & (~old["lock"]) & (old["dd_peak"] > lo) & (old["dd_peak"] <= hi)]
            s = cstat(d["p0"], d["date"], min_n=20)
            cells.append(f"{s['mean']:+6.2f}({s['n']:>4})" if s.get("mean") is not None else f"    -- ({s['n']:>4})")
        print(f"  {lb:<10}" + "".join(f"{c:>16}" for c in cells))
    print("  → 若同一欄(同一天)內由上到下單調變化、而同一列(同一跌幅)內左右不變,則『跌幅』才是軸。")

    print("\n[4] 跌幅門檻首次觸及進場(first-hit;距處置前10日高),舊制+IS/OOS,收盤價進 vs 次日開盤進(延遲)")
    for X in (10, 15, 20, 25, 30):
        H = first_hit(Pn, X)
        print(f"\n  ◇ 首次跌到 -{X}% 就進(全部事件)  平均持有 {(H['L'] - H['j']).mean():.1f} 天;進場日跌停鎖死已排除")
        print(HDR)
        for nm, sub in (("全部", H), ("累犯", H[H["rep"]]), ("初犯", H[~H["rep"]])):
            hp = sub[~sub["new"]]
            print(line(f"{nm} 收盤進(舊制)", cstat(hp["p0"], hp["date"])))
            print(line(f"{nm} 次日開盤進(舊制)", cstat(hp["p1"], hp["date"])))
        for nm, d in split3(H).items():
            print(line(f"  └ {nm} 收盤進", cstat(d["p0"], d["date"])))

    print("\n[5] 籌碼條件(固定切點,不調參):疊在『首次跌到 -15%』上;舊制;收盤進/次日開盤進")
    H = first_hit(Pn, 15)
    H = H[~H["new"]]
    conds = [("法人窗內淨買>0", H["inst_in"] > 0), ("法人窗內淨買≤0", H["inst_in"] <= 0),
             ("處置前法人淨買>0", H["inst_pre"] > 0), ("處置前法人淨買≤0", H["inst_pre"] <= 0),
             ("量縮(窗均量<處置前)", H["vr"] < 1), ("量增(窗均量≥處置前)", H["vr"] >= 1),
             ("融資餘額較公告日減少", H["mrg"] < 0), ("融資餘額較公告日增加", H["mrg"] >= 0),
             ("收盤>5日均線", H["ma5"]), ("收盤≤5日均線", ~H["ma5"]), ("收盤>20日均線", H["ma20"]), ("收盤≤20日均線", ~H["ma20"])]
    print(HDR)
    print(line("無條件", cstat(H["p0"], H["date"])))
    for nm, m in conds:
        print(line(nm + " 收盤進", cstat(H.loc[m, "p0"], H.loc[m, "date"])))
        print(line(nm + " 次日開盤進", cstat(H.loc[m, "p1"], H.loc[m, "date"])))

    print("\n[6] 格點掃描(跌幅門檻 × 籌碼條件)——IS 選 / OOS 驗;共 54 格,預期純雜訊會有數格 |t|>2")
    conds_all = [("無條件", lambda d: np.ones(len(d), bool)),
                 ("法人窗內>0", lambda d: (d["inst_in"] > 0).values), ("法人窗內≤0", lambda d: (d["inst_in"] <= 0).values),
                 ("量縮", lambda d: (d["vr"] < 1).values), ("量增", lambda d: (d["vr"] >= 1).values),
                 ("融資減", lambda d: (d["mrg"] < 0).values), ("融資增", lambda d: (d["mrg"] >= 0).values),
                 ("站上5MA", lambda d: d["ma5"].values.astype(bool)), ("跌破5MA", lambda d: ~d["ma5"].values.astype(bool))]
    out = []
    for X in (10, 15, 20, 25, 30, 40):
        H = first_hit(Pn, X)
        H = H[~H["new"]]
        for nm, f in conds_all:
            m = f(H)
            S = H[m]
            a = cstat(S[S["year"] <= 2023]["p1"], S[S["year"] <= 2023]["date"], min_n=30)
            b = cstat(S[S["year"] >= 2024]["p1"], S[S["year"] >= 2024]["date"], min_n=15)
            out.append((X, nm, a, b))
    print(f"  (進場=次日開盤『延遲』口徑,較貼近可執行;n<30(IS)/n<15(OOS) 不報)")
    print(f"  {'跌幅':>5} {'籌碼條件':<12}{'IS n':>6}{'IS均%':>8}{'IS t':>7}   {'OOS n':>6}{'OOS均%':>8}{'OOS t':>7}")
    ok = 0; both = 0
    for X, nm, a, b in sorted(out, key=lambda r: -(r[2].get("t", -99) if r[2].get("t") == r[2].get("t") else -99)):
        if a.get("mean") is None:
            continue
        bt = f"{b['n']:>6}{b['mean']:+8.2f}{b['t']:+7.2f}" if b.get("mean") is not None else f"{b['n']:>6}{'--':>8}{'--':>7}"
        print(f"  {-X:>5} {nm:<12}{a['n']:>6}{a['mean']:+8.2f}{a['t']:+7.2f}   {bt}")
        if abs(a["t"]) > 2:
            ok += 1
            if b.get("mean") is not None and b["t"] * a["t"] > 0 and abs(b["t"]) > 2:
                both += 1
    print(f"  → IS |t|>2 的格:{ok};其中 OOS 同號且 |t|>2:{both}")


# ───────────────────────── E3 ─────────────────────────
def e3(Ev):
    print("\n" + "=" * 100 + "\nE3  出關日:出關前一日(窗內最後一日)的狀態 → 出關日開盤進場的報酬(超額、扣 0.44%)\n" + "=" * 100)
    E = Ev[~Ev["new"]].copy()
    E["o2c"] = Ev["rel_o2c"]
    print(f"舊制事件 {len(E)};出關日開盤進場。欄:出關日內(開→收)/ 持有到出關後第 3 日收 / 第 5 日收。聚類=出關日")
    for col, nm in (("rel_o2c", "開→當日收"), ("rel_o2c3", "開→第3日收"), ("rel_o2c5", "開→第5日收")):
        print(f"\n── {nm} ──\n{HDR}")
        print(line("無條件", cstat(E[col], E["rel_date"])))
        for lab, m in (("收>5MA", E["end_ma5"]), ("收≤5MA", ~E["end_ma5"]), ("收>20MA", E["end_ma20"]), ("收≤20MA", ~E["end_ma20"]),
                       ("窗內法人淨買>0", E["end_inst"] > 0), ("窗內法人淨買≤0", E["end_inst"] <= 0),
                       ("量縮(窗均量<處置前)", E["end_vr"] < 1), ("量增", E["end_vr"] >= 1),
                       ("窗內累幅>0", E["end_cum"] > 0), ("窗內累幅≤0", E["end_cum"] <= 0),
                       ("距處置前高≥-10%(強勢)", E["end_dd"] >= -10), ("距處置前高<-10%", E["end_dd"] < -10)):
            print(line(lab, cstat(E.loc[m, col], E.loc[m, "rel_date"])))
        s3 = ((E["end_ma5"]) & (E["end_inst"] > 0))
        print(line("『站上5MA ∧ 法人淨買>0』(專家兩指標)", cstat(E.loc[s3, col], E.loc[s3, "rel_date"])))
        s4 = ~s3
        print(line("其餘", cstat(E.loc[s4, col], E.loc[s4, "rel_date"])))
    print("\n── 出關日開盤進、持有 3 日:IS/OOS 分段(是否穩定)──")
    print(HDR)
    for nm, d in split3(E).items():
        if d.empty:
            continue
        s3 = d["end_ma5"] & (d["end_inst"] > 0)
        for lab, m in (("無條件", np.ones(len(d), bool)), ("站上5MA∧法人>0", s3), ("其餘", ~s3),
                       ("距高≥-10%", d["end_dd"] >= -10), ("距高<-10%", d["end_dd"] < -10)):
            print(line(f"{nm} {lab}", cstat(d.loc[m, "rel_o2c3"], d.loc[m, "rel_date"])))
    print("\n── 專家第三指標『基本面』:本庫無 PIT 月營收/財報面板可用於 2019~2026 逐檔對齊,不足 → 未檢定。")
    print("   補什麼才能答:月營收公告日(PIT,t+10日內公布)× 約 1,200 事件對齊;或 TWSE 財報公告日。")
    print("\n[出關跳空本身] 出關日開盤相對出關前日收的跳空(舊制):")
    print(HDR)
    print(line("全部", cstat(E["rel_gap"] - COST * 0, E["rel_date"])))


# ───────────────────────── E4 + E2 ─────────────────────────
def thr_up(base):
    up32, up25 = base * 1.32, base * 1.25
    return up25 if (up25 - base) >= 50 else up32


def e2_e4(B):
    print("\n" + "=" * 100 + "\nE2/E4  躲處置檢定 + 可執行放空規則(僅 2026 注意公告史;上櫃只有快照→計數可能偏低)\n" + "=" * 100)
    N = pd.read_csv(DATA_DIR / "disposal" / "notice_history.csv", dtype=str)
    Wn = pd.read_csv(DATA_DIR / "disposal" / "disposal_windows.csv", dtype=str)
    sids = sorted(N["stock_id"].unique())
    P = load_bars(sids, "2025-10-01")
    cal = sorted(B[B.index >= "2025-10-01"].index.strftime("%Y-%m-%d"))
    posn = {d: i for i, d in enumerate(cal)}
    first_notice = N["date"].min()
    print(f"注意公告史 {first_notice} ~ {N['date'].max()};追蹤 {len(sids)} 檔;交易日曆 {len(cal)} 日")

    def valid(c):
        cs = [x for x in str(c or "").split("|") if x]
        return any(1 <= int(x) <= 8 for x in cs)
    N["v"] = N["clauses"].map(valid)
    byS = {s: g for s, g in N.groupby("stock_id")}
    wins = {s: [(r.start, r.end) for r in g.itertuples()] for s, g in Wn.groupby("stock_id")}
    pb = {s: g.reset_index(drop=True) for s, g in P.groupby("stock_id")}

    rows = []
    t_lo = max(posn.get("2026-02-02", 0), 30)
    for sid, g in byS.items():
        if sid not in pb:
            continue
        px = pb[sid]; pdates = px["trade_date"].dt.strftime("%Y-%m-%d").tolist()
        pidx = {d: i for i, d in enumerate(pdates)}
        recs = g[g["v"].astype(bool)]
        if recs.empty:
            continue
        all_days = sorted(set(recs["date"]))
        c1_days = sorted(set(recs[recs["clauses"].map(lambda c: "1" in str(c).split("|"))]["date"]))
        for ti in range(t_lo, len(cal)):
            t = cal[ti]; p = cal[ti - 1]
            if t not in pidx or pidx[t] < 6:
                continue
            in_disp = any(s <= p <= e for s, e in wins.get(sid, []))
            if in_disp:
                continue
            floor = max([e for s, e in wins.get(sid, []) if e < p], default="")
            days = [d for d in all_days if floor < d <= p]
            c1 = [d for d in c1_days if floor < d <= p]
            if not days:
                continue
            ds, c1s = set(days), set(c1)

            def streak(S):
                n = 0
                for i in range(ti - 1, -1, -1):
                    if cal[i] <= floor:
                        break
                    if cal[i] in S:
                        n += 1
                    else:
                        break
                return n
            sa, sc = streak(ds), streak(c1s)
            w10 = {d for d in cal[max(ti - 10, 0):ti] if d > floor}; w30 = {d for d in cal[max(ti - 30, 0):ti] if d > floor}
            need = max(min(5 - sa, 3 - sc if sc or c1 else 99, 6 - len(ds & w10), 12 - len(ds & w30)), 0)
            if need == 0:
                continue
            i = pidx[t]
            base = float(px["close"].iloc[i - 5]); up = thr_up(base)
            op_t = float(px["open"].iloc[i]); cl_t = float(px["close"].iloc[i])
            noticed = t in set(recs["date"])
            disposed = any(t < s <= (pd.Timestamp(t) + pd.Timedelta(days=6)).strftime("%Y-%m-%d") for s, e in wins.get(sid, []))
            nxt = float(px["close"].iloc[i + 1]) if i + 1 < len(px) else np.nan
            rows.append({"sid": sid, "t": t, "need": need, "open_hit": op_t >= up, "close_hit": cl_t >= up,
                         "noticed": noticed, "disposed": disposed, "cl": cl_t, "nxt": nxt,
                         "f1": (float(px["close"].iloc[i + 1]) / cl_t - 1) * 100 if i + 1 < len(px) else np.nan,
                         "f3": (float(px["close"].iloc[i + 3]) / cl_t - 1) * 100 if i + 3 < len(px) else np.nan,
                         "f5": (float(px["close"].iloc[i + 5]) / cl_t - 1) * 100 if i + 5 < len(px) else np.nan})
    R = pd.DataFrame(rows)
    if R.empty:
        print("無樣本"); return
    n1 = R[R["need"] == 1]
    print(f"(股,日) 候選 {len(R)};其中『倒數1(再中一次就關)』{len(n1)};其中當日確實被列注意 {int(n1['noticed'].sum())}、隨後進處置 {int(n1['disposed'].sum())}")

    print("\n[E2-a] 躲處置 DiD:開盤價已達第一款門檻(漲幅側)的 (股,日),收盤『跌回門檻下』的比例")
    ctrl = R[(R["need"] >= 2) & R["open_hit"]]; trt = R[(R["need"] == 1) & R["open_hit"]]
    fb = lambda d: (~d["close_hit"]).mean() * 100 if len(d) else np.nan
    print(f"  倒數1  : 開盤已達標 n={len(trt):>4}  收盤跌回門檻下 {fb(trt):5.1f}%")
    print(f"  對照(差≥2): 開盤已達標 n={len(ctrl):>4}  收盤跌回門檻下 {fb(ctrl):5.1f}%")
    try:
        from scipy.stats import fisher_exact
        tab = [[int((~trt['close_hit']).sum()), int(trt['close_hit'].sum())], [int((~ctrl['close_hit']).sum()), int(ctrl['close_hit'].sum())]]
        print(f"  Fisher exact p = {fisher_exact(tab)[1]:.3f}(倒數1 若刻意壓價,『跌回』比例應顯著較高)")
    except Exception as exc:  # noqa: BLE001
        print(f"  (scipy 不可用:{exc})")
    print("  ⚠ 倒數1 組樣本很小,且『跌回』也可能只是日內均值回歸;DiD 以對照組扣除一般回落率。")

    print("\n[E2-b] 躲過去之後(倒數1 ∧ 當日沒被列注意)的前瞻報酬(收盤→+k 日收;未扣超額,僅描述;聚類=日)")
    esc = n1[~n1["noticed"]]
    print(HDR)
    for c in ("f1", "f3", "f5"):
        print(line(f"躲過去 {c}", cstat(esc[c], esc["t"], min_n=10)))
    print(line("倒數1 且被列注意 f1(對照)", cstat(n1[n1['noticed']]['f1'], n1[n1['noticed']]['t'], min_n=10)))

    print("\n[E4] 可執行放空規則(PIT):倒數1 ∧ 收盤已達第一款價格門檻(漲幅側,未檢差幅條件)→ 當日收盤放空、隔日收盤回補")
    z = n1[n1["close_hit"]].copy()
    z["ret"] = -z["f1"] - COST          # 未扣 0050 超額(樣本小,僅描述);扣成本 0.44%
    print(f"  觸發 n={len(z)};其中實際進處置 {int(z['disposed'].sum())}({z['disposed'].mean() * 100:.0f}%)")
    print(HDR)
    print(line("全部觸發(含誤報)", cstat(z["ret"], z["t"], min_n=10)))
    print(line("  └ 實際進處置", cstat(z[z['disposed']]['ret'], z[z['disposed']]['t'], min_n=10)))
    print(line("  └ 未進處置(誤報)", cstat(z[~z['disposed']]['ret'], z[~z['disposed']]['t'], min_n=10)))
    z["post"] = z["t"] >= "2026-08-07"
    print(line("  └ 新制前(2026-02~08-06)", cstat(z[~z['post']]['ret'], z[~z['post']]['t'], min_n=10)))
    print(line("  └ 新制後(08-07起)", cstat(z[z['post']]['ret'], z[z['post']]['t'], min_n=10)))
    print("  ⚠ 隔日收盤回補;此為『處置首日』(若確實被處置);未扣融券費/借券費;放空受平盤下限制與券源影響。")


def e4_history(Ev):
    print("\n" + "=" * 100 + "\nE4b  空刀(公告日收盤放空→處置首日收盤回補;事後已知被處置)× 公告前漲幅/法人\n" + "=" * 100)
    print("⚠ 這是『已知會被處置』的條件報酬,不是可執行規則(實際得先預測);僅用來看跌多少/籌碼是否區分空刀大小")
    E = Ev[~Ev["new"]]
    print(HDR)
    print(line("全部", cstat(E["short"], E["entry_date"])))
    for lab, m in (("累犯", E["rep"]), ("初犯", ~E["rep"]),
                   ("公告前6日漲幅≥25%", E["run6"] >= 25), ("公告前6日漲幅<25%", E["run6"] < 25),
                   ("公告前10日法人淨買>0", E["inst_pre"] > 0), ("公告前10日法人淨買≤0", E["inst_pre"] <= 0)):
        print(line(lab, cstat(E.loc[m, "short"], E.loc[m, "entry_date"])))
    En = Ev[Ev["new"]]
    print(line("新制全部(僅描述)", cstat(En["short"], En["entry_date"], min_n=10)))
    print(line("新制累犯(僅描述)", cstat(En[En["rep"]]["short"], En[En["rep"]]["entry_date"], min_n=10)))
    for nm, d in split3(E).items():
        print(line("累犯 " + nm, cstat(d[d["rep"]]["short"], d[d["rep"]]["entry_date"])))
    for lab, m in (("累犯∧漲幅≥25%", E["rep"] & (E["run6"] >= 25)), ("累犯∧法人淨買>0", E["rep"] & (E["inst_pre"] > 0)),
                   ("累犯∧漲幅≥25%∧法人淨買>0", E["rep"] & (E["run6"] >= 25) & (E["inst_pre"] > 0))):
        print(line(lab, cstat(E.loc[m, "short"], E.loc[m, "entry_date"])))


def main():
    W = load_windows()
    ids = list(W["stock_id"].unique())
    print(f"處置窗 {len(W)} 筆 / {len(ids)} 檔")
    P = load_bars(ids, "2019-06-01")
    INST = load_inst(ids, "2019-09-01"); MRG = load_margin(ids, "2019-09-01"); B = load_bench()
    Pn, Ev = build_panel(W, P, INST, MRG, B)
    print(f"可用事件 {len(Ev)}(舊制 {int((~Ev['new']).sum())} / 新制 {int(Ev['new'].sum())});"
          f"法人覆蓋(窗內)舊制 {Pn[~Pn['new']]['inst_in'].notna().mean() * 100:.0f}% / 新制 {Pn[Pn['new']]['inst_in'].notna().mean() * 100 if Pn['new'].any() else float('nan'):.0f}%;"
          f"融資覆蓋舊制 {Pn[~Pn['new']]['mrg'].notna().mean() * 100:.0f}% / 新制 {Pn[Pn['new']]['mrg'].notna().mean() * 100 if Pn['new'].any() else float('nan'):.0f}%")
    e1(Pn); e3(Ev); e4_history(Ev); e2_e4(B)
    print("\n⚠ 未驗證的執行面:處置期分盤撮合排隊成交率、預收款券資金佔用、放空平盤下限制與券源、出關日跳空鎖死。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
