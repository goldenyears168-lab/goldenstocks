#!/usr/bin/env python3
"""研究用:用 TAIFEX 官方 tick 歷史檔(只有成交、無買賣一)補 HOLD 期貨逐筆/委託簿缺口,比較 H0 與現行 ★4 的每筆報酬率(bps)。

三個階段(argv):
  IS | HOLD  建立 raw:重放各變體取訊號 → archive 代理進/出場 mid + (若有)真實簿/逐筆的 E1(吃賣一進、買一出)與 E2(限價掛當下成交價,TTL 5 分) 結果
             → 寫 OUT/taifex_backfill_raw.csv  (OUT=scratch/w5h_is 或 scratch/w5h)
  REPORT     讀兩份 raw → 以 IS 真簿校準(archive mid → 吃賣一進/買一出)→ 驗證 → 寫兩份 taifex_backfill_trades.csv → 印統計表

口徑:進場=卡片+5 秒(t0)之後第一個有成交的那一秒的 VWAP;出場=min(t0+持有, 13:30) 之前最後一個有成交的那一秒的 VWAP。持有 60/90 分(上限 13:30,未滿者剔除)。
成本=手續費 2×176 + 期貨稅 2e-5/邊。近月:<=9/16 取 202609,之後 202610。日盤 08:45–13:45。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_tri_taifex_backfill.py IS|HOLD|REPORT
"""
from __future__ import annotations

import bisect
import json
import sys
import multiprocessing as mp
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import clone_agent_day_backtest as bt  # noqa: E402
import clone_agent_tri_holdout as th  # noqa: E402  (import 時依 argv 決定 MODE/DAYS/SRC/OUT;REPORT 時預設 HOLD,不使用)
import clone_agent_wide5m_backtest as m  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

FEE, TAX, LAG, CLOSE = 176.0, 2e-5, 5, "13:30:00"
HOLDS = (60, 90)
TTL = 300
QM = ("qpro", "qcons", "qopt")
ROOT = {"2492": "HBF", "2327": "LXF", "3105": "NAF", "6488": "OWF", "3532": "QDF", "8046": "LYF", "2455": "GUF", "3324": "PTF",
        "6213": "KBF", "2408": "CYF", "3189": "IXF", "3037": "IRF", "6274": "OVF"}
ARCH = Path.home() / "goldenstocks-data/cache/momentum_rotation/taifex_tick_daily_broad"
S = th.S
DIRS = {"IS": S / "w5h_is", "HOLD": S / "w5h"}
SOD0, SOD1, SODC = 8 * 3600 + 45 * 60, 13 * 3600 + 45 * 60, 13 * 3600 + 30 * 60
NEAR = lambda day: "202609" if day <= "2026-09-16" else "202610"  # noqa: E731
V0N, H0N = "V0 現行寬門檻", "H0 三尺度∧距高∧額≥30"
ARC: dict = {}


def tick(p):
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0


def tier(p):
    return "<500" if p < 500 else "500-1000" if p < 1000 else ">=1000"


def cost_bps(e, x, shares):
    return 1e4 * (2 * FEE / (e * shares) + TAX * (1 + x / e))


def load_archive():
    """ARC[root][day] = (secs, vwap, vol) 日盤、近月、按秒彙總的 VWAP。"""
    for sid, root in ROOT.items():
        df = pd.read_csv(ARCH / f"{root}.csv", dtype={"contract_date": str}, low_memory=False)
        df["contract_date"] = df["contract_date"].str.strip()
        df["day"] = df["date"].str[:10]
        df["sod"] = df["date"].str[11:13].astype(int) * 3600 + df["date"].str[14:16].astype(int) * 60 + df["date"].str[17:19].astype(int)
        df = df[(df.sod >= SOD0) & (df.sod <= SOD1) & (df.contract_date == df.day.map(NEAR))].copy()
        df["pv"] = df.price * df.volume
        g = df.groupby(["day", "sod"]).agg(pv=("pv", "sum"), v=("volume", "sum"), pm=("price", "mean")).reset_index()
        g["vw"] = np.where(g.v > 0, g.pv / g.v.where(g.v > 0, 1), g.pm)
        ARC[root] = {d: (x.sod.values, x.vw.values, x.v.values) for d, x in g.groupby("day")}


def day_rows(d):
    R = load_rules()
    members, band = universe_sids(R), tuple(R["universe"]["price_band"])
    cards = [json.loads(x) for x in (th.SRC / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    fired = {}
    for v in (V0N, H0N):
        pr = th.PRED[v]
        m.old_ok = lambda c: True
        m.new_ok = lambda c, tag, pr=pr: pr(c)
        fired[v] = m.replay(cards, ("X",), band, members)
    v0 = {(c["time"], str(c["sid"])): c for c in fired[V0N]}
    h0 = {(c["time"], str(c["sid"])): c for c in fired[H0N]}
    rows, tc, bc = [], {}, {}
    tclose = bt.epoch(d, CLOSE)
    for key in sorted(set(v0) | set(h0)):
        c = v0.get(key) or h0[key]
        sid = str(c["sid"])
        shares, px = c["shares"], c["price"]
        rec = dict(day=d, time=c["time"], sid=sid, name=c["uname"], stars=c["stars"], V0=key in v0, H0=key in h0,
                   S4=(key in v0 and v0[key]["stars"] == 4), card_px=px, shares=shares, tier=tier(px))
        rows.append(rec)
        root = ROOT.get(sid)
        if root is None:
            rec["arc_status"] = "無root"
            continue
        t0 = m.hms(c["time"]) + LAG
        # ---------- archive 代理 ----------
        a = ARC[root].get(d)
        if a is None:
            rec["arc_status"] = "當日archive無成交"
        else:
            secs, vw, _ = a
            i = bisect.bisect_left(secs, t0)
            if i >= len(secs) or secs[i] >= SODC:
                rec["arc_status"] = "進場後無成交"
            else:
                rec["arc_status"] = "ok"
                rec["entry_mid"], rec["entry_sec"] = float(vw[i]), int(secs[i])
                rec["entry_wait_s"] = int(secs[i] - t0)
                rec["px_dev_bps"] = 1e4 * (vw[i] / px - 1)
                for h in HOLDS:
                    t1 = t0 + h * 60
                    rec[f"trunc{h}"] = t1 > SODC
                    k = bisect.bisect_right(secs, min(t1, SODC)) - 1
                    if k >= i:
                        rec[f"exit_mid{h}"], rec[f"exit_sec{h}"] = float(vw[k]), int(secs[k])
                        rec[f"exit_lag{h}"] = int(min(t1, SODC) - secs[k])
        # ---------- 真實簿/逐筆 ----------
        rr = bt.resolve_root(root, d)
        tr = tc.setdefault(rr, bt.load_trades(rr, d)) if rr else None
        if not tr:
            continue
        bk = bc.setdefault(rr, bt.load_books(rr, d))
        if not bk:
            continue
        bts, ts = [b[0] for b in bk], [x[0] for x in tr]
        te = bt.epoch(d, c["time"]) + LAG
        i = bisect.bisect_right(bts, te) - 1
        if i < 0 or not np.isfinite(bk[i][3]):
            continue
        ask = bk[i][3]
        bids = bk[i][1]
        rec["real_ask0"] = ask
        rec["real_bid0"] = max(bids) if bids else np.nan
        rec["spread_ticks"] = round((ask - rec["real_bid0"]) / tick(ask), 1) if bids else np.nan
        rec["spread_ok"] = bool(bids) and (ask - rec["real_bid0"] <= tick(ask) + 1e-9)

        def exits(tf, h):
            t1 = tf + h * 60
            j = max(bisect.bisect_left(ts, tf), 0)
            k = max(bisect.bisect_right(ts, min(t1, tclose)) - 1, 0)
            last = tr[k][1]
            bb = [x[2] for x in tr[j:k + 1] if x[2]]
            return t1 > tclose, (bb[-1] if bb else last), last

        for h in HOLDS:
            trc, bidx, last = exits(te, h)
            rec[f"real_trunc{h}"] = trc
            rec[f"real_exit_bid{h}"], rec[f"real_exit_last{h}"] = bidx, last
        # E2:限價=當下成交價夾在買一~賣一之間;限價掛單 TTL 5 分(自 t0)。僅價差≤1檔者(同 entry2 口徑)
        if rec["spread_ok"]:
            ki = bisect.bisect_right(ts, te) - 1
            last0 = tr[ki][1] if ki >= 0 else rec["real_bid0"]
            L = min(max(last0, rec["real_bid0"]), ask)
            rec["E2_limit"], rec["E2_side"] = L, ("外盤吃賣一" if L >= ask else "內盤排買一")
            for mode in QM:
                tf, p0, *_ = bt.simulate_queue(tr, bk, L, te, mode, min(tclose, te + TTL))
                rec[f"E2{mode}_filled"] = tf is not None
                if tf is None:
                    continue
                rec[f"E2{mode}_entry"], rec[f"E2{mode}_wait_s"] = p0, round(tf - te)
                for h in HOLDS:
                    trc, bidx, last = exits(tf, h)
                    rec[f"E2{mode}_trunc{h}"], rec[f"E2{mode}_exit_bid{h}"] = trc, bidx
    return rows


def build():
    load_archive()
    with mp.get_context("fork").Pool(5) as p:
        out = p.map(day_rows, th.DAYS)
    df = pd.DataFrame([r for rs in out for r in rs])
    df["set"] = th.MODE
    f = th.OUT / "taifex_backfill_raw.csv"
    df.to_csv(f, index=False)
    print(f"{th.MODE}: 寫出 {f} {len(df)} 列;archive ok={int((df.arc_status == 'ok').sum())};有真簿={int(df.real_ask0.notna().sum())}")
    print("px_dev_bps(archive entry vs 卡片價) |>500bps| 筆數:", int((df.px_dev_bps.abs() > 500).sum()), "中位", round(df.px_dev_bps.median(), 1))


# =============================== REPORT ===============================
rng = np.random.default_rng(23)
pd.set_option("display.width", 260, "display.max_columns", 60, "display.max_rows", 200)


def dayboot(x, col, n=5000):
    g = x.groupby("day")[col].mean().values
    if len(g) == 0:
        return np.nan, np.nan, np.nan
    bs = rng.choice(g, (n, len(g))).mean(1)
    return g.mean(), np.percentile(bs, 5), np.percentile(bs, 95)


def stat_row(x, h, gross, net):
    x = x[x[gross].notna() & x[net].notna()]
    if not len(x):
        return dict(持有=h, 筆=0)
    mu, lo, hi = dayboot(x, net)
    gmu, glo, ghi = dayboot(x, gross)
    return dict(持有=h, 筆=len(x), 天=x.day.nunique(), 毛均=round(x[gross].mean(), 1), 毛中位=round(x[gross].median(), 1),
                毛日均=round(gmu, 1), 毛日CI=f"{glo:.0f}~{ghi:.0f}", 淨均=round(x[net].mean(), 1), 淨日均=round(mu, 1),
                淨日CI=f"{lo:.0f}~{hi:.0f}", 勝率=round(100 * (x[net] > 0).mean()))


def diff_row(name, a, b, net, h):
    """H0 − ★4:逐日配對(兩者皆有成交的日)與全日 bootstrap(各自日均、缺者略過)。"""
    ga, gb = a.groupby("day")[net].mean(), b.groupby("day")[net].mean()
    both = ga.index.intersection(gb.index)
    out = dict(集=name, 持有=h, 配對天=len(both))
    if len(both) >= 2:
        dd = (ga[both] - gb[both]).values
        bs = rng.choice(dd, (5000, len(dd))).mean(1)
        out.update(配對差=round(dd.mean(), 1), 配對CI=f"{np.percentile(bs, 5):.0f}~{np.percentile(bs, 95):.0f}")
    days = np.array(sorted(set(ga.index) | set(gb.index)))
    if len(days) >= 2:
        sa, sb = ga.reindex(days).values, gb.reindex(days).values
        idx = rng.integers(0, len(days), (5000, len(days)))
        with np.errstate(all="ignore"):
            bs = np.nanmean(np.where(np.isnan(sa[idx]), np.nan, sa[idx]), 1) - np.nanmean(sb[idx], 1)
        out.update(全日差=round(np.nanmean(sa) - np.nanmean(sb), 1), 全日CI=f"{np.nanpercentile(bs, 5):.0f}~{np.nanpercentile(bs, 95):.0f}")
    # 筆池化差:重抽「日」(日簇),每次以抽到的日內全部筆求 H0 平均 − ★4 平均
    sa_, na_ = a.groupby("day")[net].agg(["sum", "count"]).reindex(days).fillna(0).values.T if len(days) else (None, None)
    sb_, nb_ = b.groupby("day")[net].agg(["sum", "count"]).reindex(days).fillna(0).values.T if len(days) else (None, None)
    if len(days) >= 2 and na_.sum() and nb_.sum():
        idx = rng.integers(0, len(days), (5000, len(days)))
        with np.errstate(all="ignore"):
            bs = sa_[idx].sum(1) / na_[idx].sum(1) - sb_[idx].sum(1) / nb_[idx].sum(1)
        out.update(筆池化差=round(sa_.sum() / na_.sum() - sb_.sum() / nb_.sum(), 1), 池化CI=f"{np.nanpercentile(bs, 5):.0f}~{np.nanpercentile(bs, 95):.0f}")
    return out


def report():
    import warnings
    warnings.filterwarnings("ignore")
    raw = pd.concat([pd.read_csv(DIRS[k] / "taifex_backfill_raw.csv", dtype={"sid": str}) for k in ("IS", "HOLD")], ignore_index=True)
    for c in ("V0", "H0", "S4"):
        raw[c] = raw[c].astype(bool)
    raw["arc"] = raw.arc_status.eq("ok")
    raw["real"] = raw.real_ask0.notna()
    for h in HOLDS:
        raw[f"trunc{h}"] = raw[f"trunc{h}"].fillna(True).astype(bool)
        raw[f"real_trunc{h}"] = raw[f"real_trunc{h}"].fillna(True).astype(bool)
    raw["spread_ok"] = raw.spread_ok.fillna(False).astype(bool)
    IS, HO = raw[raw.set == "IS"], raw[raw.set == "HOLD"]
    print("=" * 30, "0. 資料覆蓋與單位檢查", "=" * 30)
    print(raw.groupby("set").agg(訊號=("sid", "size"), archive有成交=("arc", "sum"), 有真簿=("real", "sum")))
    for k, g in raw.groupby("set"):
        print(f"[{k}] archive 進場價 vs 卡片價偏差 bps:中位 {g.px_dev_bps.median():.1f}、|>500bps|筆數 {int((g.px_dev_bps.abs() > 500).sum())}、最大|偏差| {g.px_dev_bps.abs().max():.0f}")
    print("進場等待秒(entry_wait_s)分位:", raw.entry_wait_s.quantile([.5, .75, .9, .99]).round(0).to_dict())
    print("無 archive 成交原因:", raw[~raw.arc].groupby(["set", "arc_status"]).size().to_dict())
    print("HOLD 真簿(筆/訊號):", {s: f"{int(x.real.sum())}/{len(x)}" for s, x in HO.groupby("sid")})

    # ---------------- 1. 校準 (用 IS 真簿) ----------------
    print("\n" + "=" * 30, "1. 校準:IS 真簿 vs archive 代理(同一批訊號)", "=" * 30)
    cal = IS[IS.arc & IS.real].copy()
    cal["gap_e"] = 1e4 * (cal.real_ask0 / cal.entry_mid - 1)
    cal["gap_e_tick"] = (cal.real_ask0 - cal.entry_mid) / cal.real_ask0.map(tick)
    for h in HOLDS:
        cal[f"gap_x{h}"] = np.where(cal[f"real_trunc{h}"], np.nan, 1e4 * (cal[f"real_exit_bid{h}"] / cal[f"exit_mid{h}"] - 1))
        cal[f"gapl_x{h}"] = np.where(cal[f"real_trunc{h}"], np.nan, 1e4 * (cal[f"real_exit_last{h}"] / cal[f"exit_mid{h}"] - 1))

    def q(s):
        return pd.Series({"n": s.notna().sum(), "均": s.mean(), "中位": s.median(), "p10": s.quantile(.1), "p25": s.quantile(.25),
                          "p75": s.quantile(.75), "p90": s.quantile(.9), "SD": s.std()})

    rows = []
    for nm, col in (("進場 ask0−entry_mid (bps)", "gap_e"), ("進場 ask0−entry_mid (檔)", "gap_e_tick"), ("出場60 買一−exit_mid (bps)", "gap_x60"),
                    ("出場90 買一−exit_mid (bps)", "gap_x90"), ("出場60 最後成交−exit_mid", "gapl_x60"), ("出場90 最後成交−exit_mid", "gapl_x90")):
        rows.append(pd.concat([pd.Series({"量": nm}), q(cal[col])]))
    print(pd.DataFrame(rows).round(1).to_string(index=False))

    def tier_tab(c):
        tt = []
        for t, g in c.groupby("tier"):
            r = dict(檔位=t, n=len(g), 進場中位=round(g.gap_e.median(), 1), 進場均=round(g.gap_e.mean(), 1), 進場SD=round(g.gap_e.std(), 1))
            for h in HOLDS:
                r.update({f"出{h}中位": round(g[f"gap_x{h}"].median(), 1), f"出{h}均": round(g[f"gap_x{h}"].mean(), 1), f"出{h}SD": round(g[f"gap_x{h}"].std(), 1)})
            tt.append(r)
        return pd.DataFrame(tt).to_string(index=False)

    print("\n依價格檔位分層(IS,bps):")
    print(tier_tab(cal))
    hc = HO[HO.arc & HO.real].copy()
    hc["gap_e"] = 1e4 * (hc.real_ask0 / hc.entry_mid - 1)
    for h in HOLDS:
        hc[f"gap_x{h}"] = np.where(hc[f"real_trunc{h}"], np.nan, 1e4 * (hc[f"real_exit_bid{h}"] / hc[f"exit_mid{h}"] - 1))
    print("\n同樣的量在 HOLD 真簿子集(樣本外穩定性):")
    print(tier_tab(hc))
    def make_cal(key, stat, minn):
        out = {"e": {}, "x60": {}, "x90": {}}
        for k, g in cal.groupby(key):
            if len(g) < minn:
                continue
            out["e"][k] = getattr(g.gap_e, stat)()
            for h in HOLDS:
                out[f"x{h}"][k] = getattr(g[f"gap_x{h}"], stat)()
        return out

    CAL_SID = make_cal("sid", "mean", 5)     # 主校準:各 sid 的 IS 均值(spread 是合約特性;QD 約 5 檔、其他約 1 檔)
    CAL_TIER_MEAN = make_cal("tier", "mean", 1)
    CAL_TIER_MED = make_cal("tier", "median", 1)  # 原規格:依價格檔位中位數

    def corr_of(df, CALS, keys):
        """keys=(主鍵欄, 後備鍵欄);回傳 (進場 gap, 出場60 gap, 出場90 gap) 的 Series(bps)。"""
        res = []
        for nm in ("e", "x60", "x90"):
            v = df[keys[0]].map(CALS[0][nm])
            if len(keys) > 1:
                v = v.fillna(df[keys[1]].map(CALS[1][nm]))
            res.append(v)
        return res

    print("\n各 sid 的 IS 校準量(bps;均值):")
    print(pd.DataFrame({"n": cal.groupby("sid").size(), "進場gap": pd.Series(CAL_SID["e"]), "出60gap": pd.Series(CAL_SID["x60"]),
                        "出90gap": pd.Series(CAL_SID["x90"]), "進場gap(檔)": cal.groupby("sid").gap_e_tick.mean()}).round(1).T.to_string())
    print("採用:各 sid IS 均值(n>=5;不足退回檔位均值)。檔位中位數版本僅作敏感度。")

    # ---------------- 2. 套用校準 → 代理報酬 ----------------
    def apply(df, CALS=(CAL_SID, CAL_TIER_MEAN), keys=("sid", "tier")):
        df = df.copy()
        ge, gx60, gx90 = corr_of(df, CALS, keys)
        gx = {60: gx60, 90: gx90}
        df["ask_corr"] = df.entry_mid * (1 + ge / 1e4)
        for h in HOLDS:
            df[f"exit_corr{h}"] = df[f"exit_mid{h}"] * (1 + gx[h] / 1e4)
            ok = df.arc & ~df[f"trunc{h}"] & df[f"exit_mid{h}"].notna()
            e, x = df.ask_corr, df[f"exit_corr{h}"]
            df[f"proxy_bps{h}"] = np.where(ok, 1e4 * (x / e - 1), np.nan)
            df[f"proxy_net{h}"] = np.where(ok, 1e4 * (x / e - 1) - cost_bps(e, x, df.shares), np.nan)
            df[f"mid_bps{h}"] = np.where(ok, 1e4 * (df[f"exit_mid{h}"] / df.entry_mid - 1), np.nan)
            df[f"mid_net{h}"] = np.where(ok, df[f"mid_bps{h}"] - cost_bps(df.entry_mid, df[f"exit_mid{h}"], df.shares), np.nan)
            okr = df.real & ~df[f"real_trunc{h}"]
            xb = df[f"real_exit_bid{h}"]
            df[f"real_bps{h}"] = np.where(okr, 1e4 * (xb / df.real_ask0 - 1), np.nan)
            df[f"real_net{h}"] = np.where(okr, df[f"real_bps{h}"] - cost_bps(df.real_ask0, xb, df.shares), np.nan)
            df[f"bps{h}"] = np.where(df[f"real_bps{h}"].notna(), df[f"real_bps{h}"], df[f"proxy_bps{h}"])
            df[f"net{h}"] = np.where(df[f"real_net{h}"].notna(), df[f"real_net{h}"], df[f"proxy_net{h}"])
            df[f"realsp_net{h}"] = np.where(df.spread_ok, df[f"real_net{h}"], np.nan)
            df[f"realsp_bps{h}"] = np.where(df.spread_ok, df[f"real_bps{h}"], np.nan)
        df["source"] = np.where(df.real, "real_book", np.where(df.arc, "archive_proxy", "none"))
        return df

    allr = apply(raw)
    IS, HO = allr[allr.set == "IS"], allr[allr.set == "HOLD"]
    keep = ["day", "time", "sid", "name", "stars", "H0", "V0", "S4", "card_px", "tier", "entry_mid", "ask_corr", "real_ask0", "spread_ticks", "spread_ok",
            "exit_mid60", "exit_corr60", "exit_mid90", "exit_corr90", "real_exit_bid60", "real_exit_bid90",
            "proxy_bps60", "proxy_bps90", "proxy_net60", "proxy_net90", "real_bps60", "real_bps90", "real_net60", "real_net90",
            "bps60", "bps90", "net60", "net90", "source", "arc_status", "entry_wait_s", "px_dev_bps"]
    for k, d in (("IS", IS), ("HOLD", HO)):
        out = d[keep].rename(columns={"net60": "淨bps60", "net90": "淨bps90"})
        f = DIRS[k] / "taifex_backfill_trades.csv"
        out.to_csv(f, index=False)
        print("寫出", f, len(out))

    # ---------------- 3. 驗證 ----------------
    print("\n" + "=" * 30, "2. 驗證:同批訊號 archive 代理(校準後) vs 真簿 E1(吃賣一/買一出),逐筆 bps", "=" * 30)
    vr = []
    for nm, d in (("IS(校準樣本內)", IS), ("HOLD 真簿子集(樣本外)", HO)):
        for h in HOLDS:
            x = d[d[f"proxy_bps{h}"].notna() & d[f"real_bps{h}"].notna()].copy()
            if len(x) < 3:
                continue
            x["dd"] = x[f"proxy_bps{h}"] - x[f"real_bps{h}"]
            gm = 1e4 * (x[f"exit_mid{h}"] / x.entry_mid - 1)
            _, lo, hi = dayboot(x, "dd")
            vr.append(dict(集=nm, 持有=h, n=len(x), 相關=round(x[f"proxy_bps{h}"].corr(x[f"real_bps{h}"]), 3), 代理均=round(x[f"proxy_bps{h}"].mean(), 1),
                           真簿均=round(x[f"real_bps{h}"].mean(), 1), 差均=round(x.dd.mean(), 1), 差中位=round(x.dd.median(), 1), 差SD=round(x.dd.std(), 1),
                           差SE=round(x.dd.std() / np.sqrt(len(x)), 1), 未校準mid均=round(gm.mean(), 1), 日簇差CI=f"{lo:.0f}~{hi:.0f}"))
    print(pd.DataFrame(vr).to_string(index=False))
    BIAS = {h: float(next((r["差均"] for r in vr if r["集"].startswith("HOLD") and r["持有"] == h), 0.0)) for h in HOLDS}
    print("驗證殘差(代理−真簿,HOLD 真簿子集,bps;>0=代理高估報酬):", BIAS)

    # ---------------- 4. 各變體表 ----------------
    def vsets(d):
        return {"★4": d[d.S4], "H0": d[d.H0], "V0全星級": d[d.V0]}

    def table(title, sets, g, n):
        print("\n" + "=" * 30, title, "=" * 30)
        rows = []
        for sn, d in sets.items():
            for vn, x in vsets(d).items():
                for h in HOLDS:
                    rows.append(dict(集=sn, 變體=vn, **stat_row(x, h, g.format(h=h), n.format(h=h))))
        print(pd.DataFrame(rows).fillna("").to_string(index=False))
        drows = []
        for sn, d in sets.items():
            for h in HOLDS:
                nn = n.format(h=h)
                a, b = d[d.H0], d[d.S4]
                drows.append(diff_row(sn, a[a[nn].notna()], b[b[nn].notna()], nn, h))
        print("H0 − ★4 淨bps 差(日為單位 bootstrap 90% CI):")
        print(pd.DataFrame(drows).fillna("").to_string(index=False))

    both = pd.concat([IS, HO])
    sets3 = {"IS": IS, "HOLD": HO, "合併": both}
    table("3. archive 代理(校準後) 全 13 檔:IS / HOLD / 合併(無價差濾網、吃賣一)", sets3, "proxy_bps{h}", "proxy_net{h}")
    table("4. 混合(有真簿者用真簿 E1,其餘 archive 代理)", sets3, "bps{h}", "net{h}")
    table("5. 僅真簿子集 E1(吃賣一,無價差濾網)  <- 原『僅有真簿』口徑", sets3, "real_bps{h}", "real_net{h}")
    table("6. 僅真簿子集 E1 + 價差≤1檔濾網", sets3, "realsp_bps{h}", "realsp_net{h}")
    table("7. 對照:HOLD 真簿子集上,archive 代理(校準後)同一批筆", {"HOLD真簿子集": HO[HO.real]}, "proxy_bps{h}", "proxy_net{h}")
    table("8. 對照:未校準 mid→mid (archive 全 13 檔)", sets3, "mid_bps{h}", "mid_net{h}")

    # ---------------- 5. 樣本數漏斗 ----------------
    print("\n" + "=" * 30, "9. 樣本數漏斗(訊號→有 archive 成交→可算 60/90 分)", "=" * 30)
    fr = []
    for sn, d in (("IS", IS), ("HOLD", HO)):
        for vn, x in vsets(d).items():
            fr.append({"集": sn, "變體": vn, "訊號": len(x), "有archive成交": int(x.arc.sum()), "可算60": int(x.proxy_bps60.notna().sum()),
                       "可算90": int(x.proxy_bps90.notna().sum()), "有真簿": int(x.real.sum()), "真簿可算60": int(x.real_bps60.notna().sum()),
                       "真簿價差<=1檔": int(x.spread_ok.sum())})
    print(pd.DataFrame(fr).to_string(index=False))

    # ---------------- 6. E2 排隊(TTL 5分) ----------------
    print("\n" + "=" * 30, "10. 真簿子集 E2:限價=當下成交價,排隊 TTL=5 分(自 t0),價差≤1檔者", "=" * 30)
    er = []
    for sn, d in sets3.items():
        for vn, x in vsets(d).items():
            x = x[x.spread_ok]
            if not len(x):
                continue
            inside = x.E2_side == "內盤排買一"
            for mode in QM:
                fl = x[f"E2{mode}_filled"].fillna(False).astype(bool)
                r = {"集": sn, "變體": vn, "模式": mode, "價差ok訊號": len(x), "成交": int(fl.sum()), "TTL5成交率%": round(100 * fl.mean()),
                     "外盤即成": int((~inside).sum()), "內盤排隊數": int(inside.sum()),
                     "內盤TTL5成交率%": round(100 * fl[inside].mean()) if inside.any() else None}
                for h in HOLDS:
                    e, ex = x[f"E2{mode}_entry"], x[f"E2{mode}_exit_bid{h}"]
                    okm = fl & ~x[f"E2{mode}_trunc{h}"].fillna(True).astype(bool)
                    b = (1e4 * (ex / e - 1))[okm]
                    n_ = (1e4 * (ex / e - 1) - cost_bps(e, ex, x.shares))[okm]
                    r[f"n{h}"], r[f"毛{h}"], r[f"淨{h}"] = len(b), (round(b.mean(), 1) if len(b) else None), (round(n_.mean(), 1) if len(b) else None)
                er.append(r)
    print(pd.DataFrame(er).fillna("").to_string(index=False))
    print("\n對照:同價差≤1檔母體的 E1(吃賣一)")
    cr = []
    for sn, d in sets3.items():
        for vn, x in vsets(d).items():
            for h in HOLDS:
                z = x[x[f"realsp_net{h}"].notna()]
                if len(z):
                    cr.append(dict(集=sn, 變體=vn, 持有=h, n=len(z), 毛=round(z[f"realsp_bps{h}"].mean(), 1), 淨=round(z[f"realsp_net{h}"].mean(), 1)))
    print(pd.DataFrame(cr).to_string(index=False))

    # ---------------- 7. 敏感度 ----------------
    print("\n" + "=" * 30, "11. 敏感度:校準方式(合併 / HOLD;淨bps均;H0−★4)", "=" * 30)
    alt = apply(raw, (CAL_TIER_MED,), ("tier",))
    srows = []
    for nm, d0 in (("合併", allr), ("HOLD", allr[allr.set == "HOLD"])):
        alt0 = alt.loc[d0.index]
        for h in HOLDS:
            r = {"集": nm, "持有": h}
            for vn, col in (("★4", "S4"), ("H0", "H0")):
                mk = d0[col]
                bias = BIAS[h]
                r[f"{vn}_主(sid均值)"] = round(d0.loc[mk, f"proxy_net{h}"].mean(), 1)
                r[f"{vn}_檔位中位(原規格)"] = round(alt0.loc[mk, f"proxy_net{h}"].mean(), 1)
                r[f"{vn}_主−驗證殘差"] = round(d0.loc[mk, f"proxy_net{h}"].mean() - bias, 1)
                r[f"{vn}_未校準mid"] = round(d0.loc[mk, f"mid_net{h}"].mean(), 1)
            r["H0−★4_主"] = round(r["H0_主(sid均值)"] - r["★4_主(sid均值)"], 1)
            r["H0−★4_檔位中位"] = round(r["H0_檔位中位(原規格)"] - r["★4_檔位中位(原規格)"], 1)
            r["H0−★4_未校準"] = round(r["H0_未校準mid"] - r["★4_未校準mid"], 1)
            srows.append(r)
    print(pd.DataFrame(srows).to_string(index=False))


if __name__ == "__main__":
    if "REPORT" in sys.argv:
        report()
    else:
        build()
