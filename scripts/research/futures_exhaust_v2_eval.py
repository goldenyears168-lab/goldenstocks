#!/usr/bin/env python3
"""期貨（大台 TXF／小台 MXF／微台 TMF，日盤）套用『竭盡 v2』參數的探索檢定（唯讀；不屬 futures-streak-prereg-20261019 的凍結檢定）。
竭盡 v2 ＝ 轉色（連攻後下一個有成交窗由反方主導，平手不算）∧ 連次≥N ∧ 近 30 秒 RVOL≥X（近 6 個 5 秒窗〔含轉色窗〕買賣口數 ÷ 該商品過去 ≤10 個日盤『每 30 秒平均口數』）。
窗／方向／爆發單合併同 src/futures_flow.py（與 /futflow 共用程式）；口數＝可判定方向的主動成交口數（無法判定方向者兩邊都不含，分子分母同口徑）。
結果：反轉方向（紅串後做空、綠串後做多）毛報酬 bps（含零、取事件窗末的最近成交價與之後 H 分鐘的最近成交價；期貨一檔≈0.3～1 bps，故不另用中價），日期叢集 t。
"""
import glob, os, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]
import futures_flow as FF  # noqa: E402
from stock_db import DATA_DIR  # noqa: E402
CACHE = DATA_DIR.parent / "cache"
HS = (1, 3, 5, 15)

class Rec(FF.WindowStreak):
    def __init__(self):
        super().__init__(); self.log = []
    def _settle(self, key, wid, w):
        self.log.append((key, wid, float(w[0]), float(w[1])))
        super()._settle(key, wid, w)

def load(root):
    files = sorted(glob.glob(str(CACHE / f"{root.lower()}_trades" / f"{root.lower()}_trades_*.jsonl")))
    stream = FF.TradeStream(); ws = Rec(); sess = {}
    def on_burst(b):
        if b.stype != "day" or b.trade_time is None: return
        ws.add(b); sess.setdefault(b.T, ([], [])); sess[b.T][0].append(b.trade_time / 1e6); sess[b.T][1].append(b.last_px)
    merger = FF.BurstMerger(FF.PV[root], on_burst, streaming=True); n = 0
    for f in files:
        fd = os.path.basename(f).split("_")[-1][:10]
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                t = stream.feed(line, fd, f)
                if t is None: continue
                if t.dv > 0: merger.add(t)
                n += 1
                if n % 2000 == 0: merger.flush_old()
    merger.flush_all(); ws.flush_all()
    wins = {}
    for (stype, T), wid, buy, sell in ws.log:
        if stype == "day": wins.setdefault(T, []).append((wid, buy, sell))
    px = {}
    for T, (tt, p) in sess.items():
        tt, p = np.array(tt), np.array(p); o = np.argsort(tt); px[T] = (tt[o], p[o])
    return wins, px

def events(root):
    wins, px = load(root); days = sorted(wins)
    tot = {T: sum(b + s for _, b, s in wins[T]) for T in days}
    rows = []
    for i, T in enumerate(days):
        prev = [tot[d] for d in days[max(0, i - 10):i]]
        if len(prev) < 3 or T not in px: continue
        ref30 = np.mean(prev) / 600.0                                   # 每 30 秒平均口數（日盤 300 分＝600 個 30 秒）
        W = sorted(wins[T]); tt, p = px[T]
        wid = np.array([w[0] for w in W]); buy = np.array([w[1] for w in W]); sell = np.array([w[2] for w in W])
        dr = np.sign(buy - sell).astype(int); tot_w = buy + sell; nb = len(W); k = 0
        while k < nb:
            if dr[k] == 0: k += 1; continue
            j = k
            while j + 1 < nb and dr[j + 1] == dr[k]: j += 1
            n = j - k + 1; d = int(dr[k])
            if n >= 3 and j + 1 < nb and dr[j + 1] == -d:
                w_end = (wid[j + 1] + 1) * FF.WINDOW_SEC
                lo = wid[j + 1] - 5
                v30 = sum(tot_w[m] for m in range(len(W)) if lo <= wid[m] <= wid[j + 1])
                r = {"T": T, "n": n, "d": d, "t": w_end, "S1": v30 / ref30 if ref30 > 0 else np.nan, "cum": (buy if d > 0 else sell)[k:j + 1].sum() / (ref30 * 2)}
                p0i = np.searchsorted(tt, w_end, side="right") - 1
                for h in HS:
                    t1 = w_end + h * 60; i1 = np.searchsorted(tt, t1, side="right") - 1
                    r[f"r{h}"] = (-d * (p[i1] / p[p0i] - 1) * 1e4) if (p0i >= 0 and t1 <= tt[-1] and i1 > p0i) else np.nan
                rows.append(r)
            k = j + 1
    return pd.DataFrame(rows), len(days)

def main():
    pd.set_option("display.width", 200)
    for root in ("TXF", "MXF", "TMF"):
        E, nd = events(root)
        if E.empty: print(root, "無資料"); continue
        print(f"\n===== {root}：日盤 {E['T'].nunique()} 日；轉色事件(連次≥3) {len(E)}；每日 {len(E) / E['T'].nunique():.1f} 個")
        base = {h: E.groupby("T")[f"r{h}"].mean() for h in HS}
        def line(x, label):
            out = [f"{label:<30} 每日 {len(x) / E['T'].nunique():5.1f}"]
            for h in HS:
                y = x.dropna(subset=[f"r{h}"]); g = y.groupby("T")[f"r{h}"].agg(["mean", "size"]); g = g[g["size"] >= 2]
                if len(g) < 8: out.append("—"); continue
                dd = g["mean"] - base[h].reindex(g.index)
                t = y[f"r{h}"].mean() / (g["mean"].std(ddof=1) / np.sqrt(len(g)))
                out.append(f"{y[f'r{h}'].mean():+5.2f}(t{t:+4.1f})")
            return "  ".join(out)
        print("格式：每日事件數｜反轉方向含零毛 bps（日期叢集 t，對零），持有 1／3／5／15 分")
        print(line(E, "連次≥3 ∧ 轉色（無量條件）"))
        for N in (3, 4, 5, 10):
            for X in (0, 1.5, 2, 3):
                x = E[(E.n >= N) & (E.S1 >= X)]
                print(line(x, f"連次≥{N} ∧ RVOL≥{X}×"))

if __name__ == "__main__":
    main()
