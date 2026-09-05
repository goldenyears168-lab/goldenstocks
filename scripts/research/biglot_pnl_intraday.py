#!/usr/bin/env python3
"""昨日盤中「大戶」逐筆損益模擬（不看張數，看賺不賺錢）。

資料：cache/biglot_live_watch/raw_{date}.jsonl（富邦 ws trades channel，45 檔訂閱）
方向：ws trades 沒有 TickType，用報價規則（price>=ask → 買方主動；price<=bid → 賣方主動；
      其餘視為中價、不計入）。
大戶：單筆成交金額 >= THRESH（預設 500 萬）。多空皆計。
損益：mark-to-market —— 在時點 T 的損益 = Σ d_i · v_i · 1000 · (P_T − p_i)
      （d=+1 買方主動 / −1 賣方主動；v 張；P_T = 該檔在 T 的最後成交價）
去重：富邦 ws 的殭屍重送**用 serial 去重不掉**（實測仍有 3.4 倍膨脹）。改用累計量
      `volume` 單調遞增當閘門：只有 cum 比該檔目前最大值大的才是新成交，成交張數取
      cum 差。這樣算出來的 cum/官方張 = 0.99，捕獲率驗證通過。
"""
from __future__ import annotations
import argparse, json, os, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "src")
import stock_db
from stock_db import DATA_DIR

TPE = timezone(timedelta(hours=8))
RAW = Path("/Users/jackm4/goldenstocks-data/cache/biglot_live_watch")
MARKS = ["10:00", "11:00", "12:00", "13:00", "13:30"]


def load(date: str):
    f = RAW / f"raw_{date}.jsonl"
    maxcum = defaultdict(float); rows = []; n_raw = 0
    for line in f.open():
        if '"trades"' not in line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        pl = m.get("payload") or {}
        if pl.get("event") != "data" or pl.get("channel") != "trades":
            continue
        d = pl.get("data") or {}
        sym = str(d.get("symbol")); cum = d.get("volume")
        if sym == "None" or cum is None:
            continue
        n_raw += 1
        cum = float(cum)
        if cum <= maxcum[sym]:           # 殭屍重送：累計量沒往前
            continue
        sz = cum - maxcum[sym] if maxcum[sym] else float(d["size"])
        maxcum[sym] = cum
        t = datetime.fromtimestamp(d["time"] / 1e6, TPE).strftime("%H:%M:%S")
        rows.append((sym, t, float(d["price"]), sz,
                     float(d.get("bid") or np.nan), float(d.get("ask") or np.nan), cum))
    df = pd.DataFrame(rows, columns=["sym", "t", "px", "sz", "bid", "ask", "cum"])
    print(f"  原始 trades 事件 {n_raw} → 累計量遞增過濾後 {len(df)}"
          f"（殭屍重送佔 {(1-len(df)/max(n_raw,1))*100:.0f}%）")
    return df.sort_values(["sym", "t"]).reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-04")
    ap.add_argument("--thresh", type=float, default=5_000_000, help="大戶單筆金額門檻(元)")
    ap.add_argument("--topn", type=int, default=5)
    ap.add_argument("--mode", default="amt", choices=["amt", "pct"])
    ap.add_argument("--pct", type=float, default=0.01, help="mode=pct 時取每檔前 x 大單")
    a = ap.parse_args()
    df = load(a.date)
    print(f"{a.date}：去重後 {len(df)} 筆成交 / {df.sym.nunique()} 檔 / "
          f"{df.t.min()}~{df.t.max()}")

    # ---- 資料完整性：ws 累計量 vs 官方日成交量
    conn = sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
    off = pd.read_sql("SELECT stock_id,volume FROM stock_daily_bars WHERE trade_date=?",
                      conn, params=(a.date,)).drop_duplicates(subset=["stock_id"])
    OFF = dict(zip(off.stock_id.astype(str), off.volume))
    cap = []
    for s, g in df.groupby("sym"):
        ws_cum = g.cum.max(); ws_sum = g.sz.sum()
        o = OFF.get(s)
        o_lot = o / 1000 if o else None      # stock_daily_bars.volume 單位是股
        cap.append((s, ws_cum, ws_sum, o_lot,
                    (ws_sum / o_lot) if o_lot else np.nan))
    capd = pd.DataFrame(cap, columns=["sym", "ws_cum", "ws_sum", "官方張", "捕獲率"])
    print(f"[捕獲率] 中位 {capd.捕獲率.median():.3f}、"
          f"<0.9 的 {int((capd.捕獲率 < 0.9).sum())} 檔、>1.1 的 {int((capd.捕獲率 > 1.1).sum())} 檔")
    bad = set(capd[(capd.捕獲率 < 0.9) | (capd.捕獲率 > 1.1)].sym)
    if bad:
        print("  ⚠️ 捕獲率異常（不納入排名）:", sorted(bad))
    df = df[~df.sym.isin(bad)]

    # ---- 方向 + 大戶
    df["amt"] = df.px * df.sz * 1000
    df["d"] = np.where(df.px >= df.ask, 1.0, np.where(df.px <= df.bid, -1.0, 0.0))
    if a.mode == "amt":
        big = df[(df.amt >= a.thresh) & (df.d != 0)].copy()
    else:                                   # 每檔前 pct% 大單
        q = df.groupby("sym").sz.transform(lambda x: x.quantile(1 - a.pct))
        big = df[(df.sz >= q) & (df.d != 0)].copy()
    lab = f"單筆>={a.thresh/1e4:.0f}萬" if a.mode == "amt" else f"每檔前{a.pct*100:.0f}%大單"
    print(f"[大戶·{lab}] {len(big)} 筆 / "
          f"{big.sym.nunique()} 檔；佔全部成交金額 {big.amt.sum()/df.amt.sum()*100:.1f}%")
    print(f"  方向可判比例（全體）{(df.d!=0).mean()*100:.1f}%；大戶買方主動佔 "
          f"{(big.d>0).mean()*100:.1f}%")

    # ---- 每個整點的 mark price 與 mark-to-market 損益
    marks = {}
    for s, g in df.groupby("sym"):
        for mk in MARKS:
            gg = g[g.t <= mk + ":59"]
            marks[(s, mk)] = float(gg.px.iloc[-1]) if len(gg) else np.nan
    out = []
    for s, g in big.groupby("sym"):
        for mk in MARKS:
            P = marks.get((s, mk), np.nan)
            gg = g[g.t <= mk + ":59"]
            if not len(gg) or P != P:
                continue
            pnl = float((gg.d * gg.sz * 1000 * (P - gg.px)).sum())
            notional = float((gg.sz * 1000 * gg.px).sum())
            net = float((gg.d * gg.sz).sum())
            out.append(dict(sym=s, mark=mk, pnl=pnl, n=len(gg), notional=notional,
                            net_lots=net, ret_bps=pnl / notional * 1e4 if notional else np.nan,
                            px=P))
    r = pd.DataFrame(out)
    uni = json.loads((DATA_DIR / "cache/pit_universe_tick/_hivol_universe_v3.json").read_text())
    V3 = {x["sid"]: x["name"] for x in uni["universe"]}
    r["v3"] = r.sym.map(lambda s: "★" if s in V3 else "")
    r["name"] = r.sym.map(lambda s: V3.get(s, ""))
    for mk in MARKS:
        s = r[r.mark == mk].sort_values("pnl", ascending=False)
        if s.empty:
            continue
        print(f"\n=== {mk} 大戶累計 mark-to-market 損益（{lab}） ===")
        print(f"{'':<3}{'代號':<7}{'名稱':<8}{'損益(萬元)':>11}{'投入(億)':>9}{'報酬bps':>9}"
              f"{'筆數':>6}{'淨張':>8}")
        for tag, part in [("賺最多", s.head(a.topn)), ("賠最多", s.tail(a.topn).iloc[::-1])]:
            print(f"  -- {tag}")
            for _, x in part.iterrows():
                print(f"{x.v3:<3}{x.sym:<7}{x['name']:<8}{x.pnl/1e4:>11.0f}"
                      f"{x.notional/1e8:>9.2f}{x.ret_bps:>9.1f}{x.n:>6.0f}{x.net_lots:>8.0f}")
        print(f"  全體合計 {s.pnl.sum()/1e4:>.0f} 萬元；賺錢檔數 {int((s.pnl>0).sum())}/{len(s)}")
    r.to_pickle(f"/tmp/biglot_pnl_{a.date}.pkl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
