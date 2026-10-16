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
try:
    from stock_db import DATA_DIR as _DATA_DIR
    STATE_ROOT = pathlib.Path(_DATA_DIR).parent      # stock_db.DATA_DIR 指向 <狀態根>/data
except Exception:  # 沒有 src 路徑時退回環境變數/預設
    STATE_ROOT = pathlib.Path(os.environ.get("GOLDENSTOCKS_DATA_DIR", os.path.expanduser("~/goldenstocks-data")))

PV = {"mxf": 50, "tmf": 10}
TIERS = ["S", "M1", "M2", "L", "XL"]
EDGES_M = [0, 2.5, 5, 12, 25]
DAY0, DAY1 = 8 * 3600 + 45 * 60, 13 * 3600 + 45 * 60


def tier_of(notional_m):
    t = 0
    for i, e in enumerate(EDGES_M):
        if notional_m >= e:
            t = i
    return TIERS[t]


def parse_ts(s):
    """'2026-10-14T00:00:00.061+08:00' → (YYYY-MM-DD, 秒數)"""
    return s[:10], int(s[11:13]) * 3600 + int(s[14:16]) * 60 + int(s[17:19]) + int(s[20:23]) / 1000


def session_of(qt, file_date, sec):
    """回傳 (session_type, T)。夜盤 <12:00 屬前一日曆日的夜盤。"""
    import datetime
    if qt == "FUTURE":
        return "day", file_date
    if sec >= 12 * 3600:
        return "night", file_date
    t = datetime.date.fromisoformat(file_date) - datetime.timedelta(days=1)
    return "night", t.isoformat()


def process(root, files):
    pv = PV[root]
    prev = {}                              # quote_type → 上一筆累計量(跨檔延續)
    bars = {}                              # (stype, T, minute_idx) → dict
    sess = collections.defaultdict(lambda: {"first_cum": None, "last_cum": None, "sum_dv": 0, "resets": 0, "rows": 0})
    for f in files:
        file_date = os.path.basename(f).split("_")[-1][:10]
        seen = set()
        bursts = {}                        # (qt, trade_time, dir) → [first_sec, vol, last_px, stype, T]
        with open(f) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("stale"):
                    continue
                qt = r.get("quote_type")
                cv = r.get("cum_volume")
                if qt not in ("FUTURE", "FUTURE_AH") or cv is None or r.get("price") is None:
                    continue
                k = (qt, r.get("serial"), cv)
                if k in seen:
                    continue
                seen.add(k)
                _, sec = parse_ts(r["ts"])
                if qt == "FUTURE" and not (DAY0 <= sec < DAY1):
                    continue
                if qt == "FUTURE_AH" and 5 * 3600 <= sec < 15 * 3600:
                    continue
                stype, T = session_of(qt, file_date, sec)
                s = sess[(stype, T)]
                s["rows"] += 1
                p = prev.get(qt)
                prev[qt] = cv
                if p is None or cv < p:
                    if p is not None:
                        s["resets"] += 1
                    if s["first_cum"] is None:
                        s["first_cum"] = cv
                    s["last_cum"] = cv
                    continue                      # session 第一筆/重置:只當基準
                if s["first_cum"] is None:
                    s["first_cum"] = p
                s["last_cum"] = cv
                dv = cv - p
                px, b, a = r["price"], r.get("bid"), r.get("ask")
                m = int(sec // 60)
                bk = bars.setdefault((stype, T, m), {"o": px, "h": px, "l": px, "c": px, "vol": 0, "n": 0, "spr_sum": 0.0, "spr_n": 0,
                                                     **{f"{d}_{t}": 0 for d in ("b", "s") for t in TIERS}, **{f"nb_{t}": 0 for t in TIERS}})
                bk["h"] = max(bk["h"], px)
                bk["l"] = min(bk["l"], px)
                bk["c"] = px
                if dv <= 0:
                    continue
                s["sum_dv"] += dv
                bk["vol"] += dv
                bk["n"] += 1
                if a is not None and b is not None and a > b and px > 0:
                    bk["spr_sum"] += (a - b) / px * 1e4
                    bk["spr_n"] += 1
                d = 1 if (a is not None and px >= a) else (-1 if (b is not None and px <= b) else 0)
                if d == 0:
                    continue
                key = (qt, r.get("trade_time"), d)
                e = bursts.get(key)
                if e is None:
                    bursts[key] = [m, dv, px, stype, T]
                else:
                    e[1] += dv
                    e[2] = px
        for (qt, tt, d), (m, v, px, stype, T) in bursts.items():
            t = tier_of(v * pv * px / 1e6)
            bk = bars[(stype, T, m)]
            bk[("b_" if d > 0 else "s_") + t] += v
            bk["nb_" + t] += 1
    return bars, sess


def write(root, bars, sess, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    cols = ["root", "session", "T", "hhmm", "open", "high", "low", "close", "vol", "trades", "spread_bps"]
    cols += [f"b_{t}" for t in TIERS] + [f"s_{t}" for t in TIERS] + [f"nb_{t}" for t in TIERS]
    with gzip.open(outdir / f"{root}_minute.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for (stype, T, m) in sorted(bars):
            b = bars[(stype, T, m)]
            spr = round(b["spr_sum"] / b["spr_n"], 3) if b["spr_n"] else ""
            w.writerow([root, stype, T, "%02d:%02d" % (m // 60 % 24, m % 60), b["o"], b["h"], b["l"], b["c"], b["vol"], b["n"], spr]
                       + [b[f"b_{t}"] for t in TIERS] + [b[f"s_{t}"] for t in TIERS] + [b[f"nb_{t}"] for t in TIERS])
    rows = []
    for (stype, T), s in sorted(sess.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        span = (s["last_cum"] - s["first_cum"]) if s["first_cum"] is not None else None
        cap = round(s["sum_dv"] / span, 4) if span and span > 0 else None
        rows.append({"session": stype, "T": T, "rows": s["rows"], "sum_dv": s["sum_dv"], "cum_span": span, "capture": cap, "resets": s["resets"]})
    (outdir / f"{root}_sessions.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", choices=["mxf", "tmf"], required=True)
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
