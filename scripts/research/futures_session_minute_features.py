#!/usr/bin/env python3
"""MXF / TMF websocket 逐筆成交 → 日盤＋夜盤「session 拼接」的一分鐘特徵表(只讀研究工具,不碰 DB 與下單層)。

輸入  <狀態根>/cache/{mxf,tmf}_trades/{root}_trades_YYYY-MM-DD.jsonl
輸出  <狀態根>/data/research/futures_session_minute/{root}_minute.csv.gz
      <狀態根>/data/research/futures_session_minute/{root}_sessions.json(每個 session 的捕獲率檢查)

規則(出處:docs 記憶 fubon-ws-trades-dual-stream-pitfalls / tmf-bigflow-size-bucket-backtest-20261008):
- 日盤 FUTURE 與夜盤 FUTURE_AH 是兩條獨立流,累計量各自計;一律以 Δcum_volume 當量,不用 size 欄。
- stale 列丟棄;(serial,cum_volume) 去重;日盤只取 08:45~13:45(開頭會混入 00:03 殭屍重送)。
- 夜盤跨日曆檔:同一個 session T 的 15:00~24:00 在 T 檔、00:00~05:00 在 T+1 檔(週五夜盤在週六檔)。
  這裡以檔案日期當 T(≥12:00)或前一日曆日當 T(<12:00),累計量跨檔延續。
- 買賣方向:成交價 ≥ 賣一 = 主動買;≤ 買一 = 主動賣;其餘不計方向。
- 「單」= 同毫秒、同方向的爆發量(不是母單、不能辨認身分)。以名目金額分五級(百萬台幣):
    S <2.5 | M1 2.5~5 | M2 5~12 | L 12~25 | XL ≥25
  名目 = 口數 × 點值(MXF 50、TMF 10) × 成交價。
"""
import argparse
import collections
import csv
import glob
import gzip
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
import futures_flow as FF  # noqa: E402
try:
    from stock_db import DATA_DIR as _DATA_DIR
    STATE_ROOT = pathlib.Path(_DATA_DIR).parent      # stock_db.DATA_DIR 指向 <狀態根>/data
except Exception:  # 沒有 src 路徑時退回環境變數/預設
    STATE_ROOT = pathlib.Path(os.environ.get("GOLDENSTOCKS_DATA_DIR", os.path.expanduser("~/goldenstocks-data")))

PV = {k.lower(): v for k, v in FF.PV.items()}
TIERS = list(FF.TIERS)
LOT_TIERS = ["1", "2_4", "5_9", "10p"]       # 口數級(跨商品同一套;2026-10-16 起新增,附加在舊欄位之後,不影響既有評估)
tier_of = FF.tier_of


def lot_tier(v):
    return "1" if v <= 1 else ("2_4" if v <= 4 else ("5_9" if v <= 9 else "10p"))


def process(root, files):
    """解析、分盤別、爆發合併全部來自共用模組 `futures_flow`;這裡只負責彙整成分鐘列與盤別捕獲率檢查。"""
    pv = PV[root]
    stream = FF.TradeStream()
    bars = {}                              # (stype, T, minute_idx) → dict
    sess = collections.defaultdict(lambda: {"first_cum": None, "last_cum": None, "sum_dv": 0, "resets": 0, "rows": 0})

    def on_burst(b):
        bk = bars[(b.stype, b.T, b.mk)]
        t = tier_of(b.lots * pv * b.last_px / 1e6)           # 與舊版相同:名目以爆發最後一筆價格計
        bk[("b_" if b.side > 0 else "s_") + t] += b.lots
        bk["nb_" + t] += 1
        bk[("bl_" if b.side > 0 else "sl_") + lot_tier(b.lots)] += b.lots

    for f in files:
        file_date = os.path.basename(f).split("_")[-1][:10]
        merger = FF.BurstMerger(pv, on_burst, streaming=False)      # 批次:同毫秒同方向整檔累加,與舊版一致
        with open(f) as fh:
            for line in fh:
                t = stream.feed(line, file_date, f)
                if t is None:
                    continue
                s = sess[(t.stype, t.T)]
                s["rows"] += 1
                if t.baseline:
                    if t.reset:
                        s["resets"] += 1
                    if s["first_cum"] is None:
                        s["first_cum"] = t.cv
                    s["last_cum"] = t.cv
                    continue                      # session 第一筆/重置:只當基準
                if s["first_cum"] is None:
                    s["first_cum"] = t.cv - t.dv
                s["last_cum"] = t.cv
                bk = bars.setdefault((t.stype, t.T, t.mk), {"o": t.px, "h": t.px, "l": t.px, "c": t.px, "vol": 0, "n": 0, "spr_sum": 0.0, "spr_n": 0,
                                                            **{f"{d}_{x}": 0 for d in ("b", "s") for x in TIERS}, **{f"nb_{x}": 0 for x in TIERS},
                                                            **{f"{d}l_{x}": 0 for d in ("b", "s") for x in LOT_TIERS}})
                bk["h"] = max(bk["h"], t.px)
                bk["l"] = min(bk["l"], t.px)
                bk["c"] = t.px
                if t.dv <= 0:
                    continue
                s["sum_dv"] += t.dv
                bk["vol"] += t.dv
                bk["n"] += 1
                if t.ask is not None and t.bid is not None and t.ask > t.bid and t.px > 0:
                    bk["spr_sum"] += (t.ask - t.bid) / t.px * 1e4
                    bk["spr_n"] += 1
                merger.add(t)
        merger.flush_all()
        stream.drop_file(f)
    return bars, sess


def write(root, bars, sess, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    cols = ["root", "session", "T", "hhmm", "open", "high", "low", "close", "vol", "trades", "spread_bps"]
    cols += [f"b_{t}" for t in TIERS] + [f"s_{t}" for t in TIERS] + [f"nb_{t}" for t in TIERS]
    cols += [f"bl_{t}" for t in LOT_TIERS] + [f"sl_{t}" for t in LOT_TIERS]
    with gzip.open(outdir / f"{root}_minute.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for (stype, T, m) in sorted(bars):
            b = bars[(stype, T, m)]
            spr = round(b["spr_sum"] / b["spr_n"], 3) if b["spr_n"] else ""
            w.writerow([root, stype, T, "%02d:%02d" % (m // 60 % 24, m % 60), b["o"], b["h"], b["l"], b["c"], b["vol"], b["n"], spr]
                       + [b[f"b_{t}"] for t in TIERS] + [b[f"s_{t}"] for t in TIERS] + [b[f"nb_{t}"] for t in TIERS]
                       + [b[f"bl_{t}"] for t in LOT_TIERS] + [b[f"sl_{t}"] for t in LOT_TIERS])
    rows = []
    for (stype, T), s in sorted(sess.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        span = (s["last_cum"] - s["first_cum"]) if s["first_cum"] is not None else None
        cap = round(s["sum_dv"] / span, 4) if span and span > 0 else None
        rows.append({"session": stype, "T": T, "rows": s["rows"], "sum_dv": s["sum_dv"], "cum_span": span, "capture": cap, "resets": s["resets"]})
    (outdir / f"{root}_sessions.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", choices=["txf", "mxf", "tmf"], required=True)
    ap.add_argument("--since", default="2026-08-20", help="只處理此日期(含)之後的檔;8/20 之前收集器有連線洩漏,資料不可靠")
    a = ap.parse_args()
    files = [f for f in sorted(glob.glob(str(STATE_ROOT / "cache" / f"{a.root}_trades" / f"{a.root}_trades_*.jsonl")))
             if os.path.basename(f).split("_")[-1][:10] >= a.since]
    bars, sess = process(a.root, files)
    rows = write(a.root, bars, sess, STATE_ROOT / "data" / "research" / "futures_session_minute")
    real = [r for r in rows if r["rows"] >= 100]      # <100 列是週末檔裡殘留的單筆重送,不算 session
    bad = [r for r in real if r["capture"] is None or r["capture"] < 0.95 or r["capture"] > 1.05]
    print(f"{a.root}: {len(files)} 檔 → {len(real)} 個有效 session(殘留 {len(rows) - len(real)} 個略過)、{len(bars)} 根分鐘列;捕獲率不在 95~105% 的 session {len(bad)} 個")
    for r in bad[:12]:
        print("  ", r)


if __name__ == "__main__":
    main()
