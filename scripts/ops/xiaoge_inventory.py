#!/usr/bin/env python3
"""小哥盤點：把小哥書中「能用日線＋分點 top15 檢查」的戰法，每天收盤後跑一次，列出當日是否重現訊號。唯讀。

規格 SSOT：docs/warrant-xiaoge/daily-inventory-spec.md（每個戰法的書中出處、量化定義、資料缺口）。
輸出：${DATA}/chip/xiaoge_inventory/inventory_<日期>.json（8771 或 AI 步驟讀）；加 --md 同時寫 .md。
PIT：訊號日 T 只用 date ≤ T 的日線與 T 日收盤後的分點 top15（富邦，xiaoge_chip_daily 產出）。
本程式只做「條件是否成立」的標示，不判斷漲跌、不下單。價格/籌碼條件各自標明資料覆蓋。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

CHIP = DATA_DIR / "chip" / "xiaoge"
OUT = DATA_DIR / "chip" / "xiaoge_inventory"
NOTE = DATA_DIR / "chip" / "xiaoge_notebook"
MIN_TURNOVER = 3.0e7          # 前 20 日平均成交值（流動性母體；同 flip-habit 研究）
HIST_DAYS = 330               # 日線視窗（MA240 + 緩衝）
LOOKBACK = 20                 # 「重現頻率」回看交易日數
KEY_ROLES = ("關鍵分點", "地緣", "高手", "公司派")

PRICE_KEY = {"M01": "open_bb", "M02": "narrow", "M03": "range", "M04": "high_out", "M05": "floor"}
METHODS = {
    "M01": ("開布林沿上軌", "part_03 §3.2；part_02 2.1", "前一日帶寬<5%、今日帶寬放大且收盤>上軌（量化為「那一根」）；籌碼：買超前5名任一≥3%量"),
    "M02": ("窄帶寬名單（等突破）", "part_03 §3.1；手冊 §1 第4點", "帶寬<5% 的股票，上軌斜率排序（≥3%/日為飆股）；不是進場訊號，是待命清單"),
    "M03": ("多頭區間來回做", "part_03 §3.3", "月線向上、上下軌同升、帶寬≥10%，收盤回到月線±1.5%內且量縮（<20日均量×0.8）"),
    "M04": ("高檔出貨股", "part_02 2.4；part_04 §3", "月線下彎且乖離年線>40%；籌碼面：主力集中度<0（僅收集池有分點）"),
    "M05": ("地板股候選（前一晚名單）", "part_04 §2；手冊 §2 第6點", "今日黑K、量≥20日均量2倍、月線下彎、乖離月線≤該股近500日乖離的5%分位"),
    "M06": ("關鍵分點低檔建倉", "part_03 §3.5", "小哥點名的關鍵分點/地緣/高手/公司派 今日買超前5且≥1%量，且收盤位於3年區間最低25%"),
    "M07": ("隔日沖短空池", "part_04 §4；part_02 2.13", "近5日曾列入M04、今日收紅K、且買超前5名含小哥點名的隔日沖分點（大股東賣：缺資料，未判）"),
    "M08": ("純空頭（月線下彎＋主力賣超）", "part_04 §1.1", "月線下彎且主力集中度<0（僅收集池有分點）"),
}


def key_pairs(by_id: dict) -> set:
    """(分點代號, 股票代號)：小哥點名該分點是「這檔」的關鍵分點才算；富邦、美林等大型分點不能套到任意股票。"""
    out = set()
    for tid, e in by_id.items():
        for s in e.get("stocks", []):
            for m in re.findall(r"\((\d{4})\)", s):
                out.add((tid, m))
    return out


def load_named() -> dict:
    cfg = json.loads((ROOT / "config/xiaoge_named_branches.json").read_text(encoding="utf-8"))
    by_id = {}
    for e in cfg["branches"]:
        for tid in e["ids"]:
            by_id[tid] = e
    return by_id


def anchor_and_days(c) -> list[str]:
    r = c.execute("select distinct trade_date from stock_daily_bars where source in ('twse_mi_index','tpex_daily') "
                  "group by trade_date having count(*)>1500 order by trade_date desc limit ?", (HIST_DAYS,)).fetchall()
    return sorted(x[0] for x in r)


def load_panel(c, days) -> pd.DataFrame:
    df = pd.read_sql_query(
        "select stock_id, trade_date d, open, high, close, volume from stock_daily_bars "
        "where source in ('twse_mi_index','tpex_daily') and trade_date between ? and ? and length(stock_id)=4 "
        "and stock_id glob '[1-9][0-9][0-9][0-9]'", c, params=(days[0], days[-1]))
    return df.drop_duplicates(["stock_id", "d"])


def features(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("d").reset_index(drop=True)
    cl, vol = g.close, g.volume.astype(float)
    ma = cl.rolling(20).mean(); sd = cl.rolling(20).std(ddof=0)
    up, lo = ma + 2 * sd, ma - 2 * sd
    g["ma20"], g["up"], g["lo"] = ma, up, lo
    g["bw"] = up / lo - 1
    g["up_slope"] = up.pct_change()
    g["ma_dn"] = ma < ma.shift(1)
    g["ma_up5"] = ma > ma.shift(5)
    g["bias20"] = cl / ma - 1
    g["bias240"] = cl / cl.rolling(240, min_periods=200).mean() - 1
    g["vr"] = vol / vol.shift(1).rolling(20).mean()
    g["turn20"] = (cl * vol).shift(1).rolling(20).mean()
    b5 = g["bias20"].shift(1).rolling(500, min_periods=200).quantile(0.05)
    g["floor_ref"] = b5
    g["open_bb"] = (g.bw.shift(1) < 0.05) & (g.bw > g.bw.shift(1)) & (cl > up)
    g["narrow"] = g.bw < 0.05
    g["range"] = g.ma_up5 & (up > up.shift(1)) & (lo > lo.shift(1)) & (g.bw >= 0.10) & (g.bias20.abs() <= 0.015) & (g.vr < 0.8)
    g["high_out"] = g.ma_dn & (g.bias240 > 0.40)
    g["floor"] = (cl < g.open) & (g.vr >= 2) & g.ma_dn & (g.bias20 <= g.floor_ref)
    g["red"] = cl > g.open
    return g


def chip_today(day: str, vol_lots: dict) -> dict:
    pq = CHIP / day / "stock_top15.parquet"
    if not pq.exists():
        return {}
    p = pd.read_parquet(pq)
    p = p[p["trade_date"] == day] if "trade_date" in p else p
    res = {}
    for sid, g in p.groupby("stock_id"):
        v = vol_lots.get(sid)
        if not v:
            continue
        net = (g["buy_lots"] - g["sell_lots"]).to_numpy()
        order = np.argsort(-net)[:5]
        res[sid] = {"conc": float(net.sum() / v), "main_net_lots": int(net.sum()),
                    "top5": [{"id": str(g["branch_id"].iloc[j]), "name": str(g["branch"].iloc[j]), "net_lots": int(net[j]), "share": float(net[j] / v)}
                             for j in order if net[j] > 0]}
    return res


def market_env(c, day) -> dict:
    d = pd.read_sql_query("select date d, close from daily_bars where code='IX0001' and date<=? order by date desc limit 60", c, params=(day,))
    d = d.sort_values("d").reset_index(drop=True)
    if len(d) < 25:
        return {"note": "加權指數資料不足"}
    cl = d.close; ma = cl.rolling(20).mean(); sd = cl.rolling(20).std(ddof=0)
    bw = (ma + 2 * sd) / (ma - 2 * sd) - 1
    bwn = float(bw.iloc[-1])
    level = "極窄(<5%)" if bwn < 0.05 else ("普通窄(5~10%)" if bwn < 0.10 else ("普通(10~20%)" if bwn < 0.20 else "寬(≥20%)"))
    return {"date": d.d.iloc[-1], "close": float(cl.iloc[-1]), "ma20_dir": "向上" if ma.iloc[-1] > ma.iloc[-2] else "下彎",
            "bandwidth_pct": round(bwn * 100, 2), "bandwidth_level": level,
            "pos_in_band": round(float((cl.iloc[-1] - (ma - 2 * sd).iloc[-1]) / (4 * sd.iloc[-1])), 2) if sd.iloc[-1] else None,
            "book": "小哥：先分多空（月線方向）再選招；寬帶寬=來回做，窄帶寬=等突破（part_02 2.1）"}


def pos3y(c, sid, day) -> float | None:
    p = pd.read_sql_query("select close from stock_daily_bars where stock_id=? and source in ('twse_mi_index','tpex_daily') and trade_date<=? "
                          "order by trade_date desc limit 756", c, params=(sid, day))
    if len(p) < 500:
        return None
    cl = p.close.to_numpy()
    return float((cl[1:] < cl[0]).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="預設最新完整交易日")
    ap.add_argument("--md", action="store_true")
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True); c.execute("pragma query_only=1")
    days = anchor_and_days(c)
    day = a.date or days[-1]
    days = [x for x in days if x <= day]
    panel = load_panel(c, days)
    named = load_named(); pairs = key_pairs(named)
    names = dict(c.execute("select stock_id,name from stock_beta").fetchall()) if c.execute("select name from sqlite_master where name='stock_beta'").fetchone() else {}
    hist = {}
    for sid, g in panel.groupby("stock_id"):
        if len(g) < 60:
            continue
        f = features(g)
        if f.d.iloc[-1] != day or not (f.turn20.iloc[-1] >= MIN_TURNOVER):
            continue
        hist[sid] = f
    vol_lots = {s: float(f.volume.iloc[-1]) / 1000.0 for s, f in hist.items()}
    chip = chip_today(day, vol_lots)
    # 近 LOOKBACK 日價格型訊號出現檔數（重現頻率）
    freq = {k: [] for k in ("open_bb", "narrow", "range", "high_out", "floor")}
    for f in hist.values():
        tail = f.tail(LOOKBACK + 1)
        for k in freq:
            freq[k].append(tail[k].fillna(False).astype(bool).reset_index(drop=True))
    freq_series = {k: pd.concat(v, axis=1).sum(axis=1).tolist() for k, v in freq.items()}

    def row(sid, f, extra=None):
        r = f.iloc[-1]
        o = {"stock": sid, "name": names.get(sid, ""), "close": float(r.close), "bw_pct": round(float(r.bw) * 100, 2), "bias20_pct": round(float(r.bias20) * 100, 2),
             "vr": None if pd.isna(r.vr) else round(float(r.vr), 2), "chip": chip.get(sid)}
        if extra:
            o.update(extra)
        return o

    hits = {k: [] for k in METHODS}
    high_out_recent = set(); m7 = {}
    for sid, f in hist.items():
        r = f.iloc[-1]; ch = chip.get(sid)
        if f.high_out.tail(5).any():
            high_out_recent.add(sid)
        if r.open_bb:
            ok = None if ch is None else any(x["share"] >= 0.03 for x in ch["top5"])
            hits["M01"].append(row(sid, f, {"chip_confirm": ok, "up_slope_pct": round(float(r.up_slope) * 100, 2)}))
        if r.narrow:
            hits["M02"].append(row(sid, f, {"up_slope_pct": round(float(r.up_slope) * 100, 2)}))
        if r["range"]:
            hits["M03"].append(row(sid, f))
        if r.high_out:
            hits["M04"].append(row(sid, f, {"chip_bearish": None if ch is None else ch["conc"] < 0}))
        if r.floor:
            hits["M05"].append(row(sid, f, {"floor_ref_pct": round(float(r.floor_ref) * 100, 2)}))
        if ch is not None:
            if r.ma_dn and ch["conc"] < 0:
                hits["M08"].append(row(sid, f))
            for x in ch["top5"]:
                e = named.get(x["id"])
                if e and e["role"] in KEY_ROLES and (x["id"], sid) in pairs and x["share"] >= 0.01:
                    p3 = pos3y(c, sid, day)
                    if p3 is not None and p3 <= 0.25:
                        hits["M06"].append(row(sid, f, {"branch": e["name"], "role": e["role"], "net_lots": x["net_lots"], "pos3y": round(p3, 2), "says": e.get("says", "")}))
                if e and e["role"] == "隔日沖" and sid in high_out_recent and r.red:
                    m7.setdefault(sid, []).append(f'{e["name"]}({x["net_lots"]}張)')
    for sid, bl in m7.items():
        hits["M07"].append(row(sid, hist[sid], {"branch": "、".join(bl), "major_holder_sell": "缺資料"}))
    # 具名分點今日動作（沿用每日筆記本的故事重放）
    stories = []
    nb = NOTE / f"notebook_{day}.json"
    if nb.exists():
        stories = json.loads(nb.read_text(encoding="utf-8")).get("stories", [])
    for k in hits:
        hits[k].sort(key=lambda x: -abs(x["bias20_pct"]))
    result = {
        "date": day, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe": {"liquid_stocks": len(hist), "with_chip": sum(1 for s in hist if s in chip), "min_turnover20": MIN_TURNOVER},
        "market_env": market_env(c, day),
        "methods": {k: {"name": v[0], "book": v[1], "rule": v[2], "hits_today": len(hits[k]),
                        "typical_daily_hits_20d": (float(np.median(freq_series[PRICE_KEY[k]][:-1])) if k in PRICE_KEY and len(freq_series[PRICE_KEY[k]]) > 1 else None),
                        "hits": hits[k]} for k, v in METHODS.items()},
        "price_signal_20d_counts": {k: [int(x) for x in v] for k, v in freq_series.items()},
        "stories_today": stories[:30],
        "gaps": ["權證買超排名（小哥選股主表）：缺", "大股東賣超/申報轉讓：缺", "集保戶數、營收、殖利率、庫藏股：缺", "分點籌碼條件僅限收集池（流動性前579檔），其餘標 chip=null",
                 "地板價用該股近500日乖離5%分位近似（書中為軟體逐檔統計）", "「開布林那一根」以收盤>上軌量化（書中無數字）"],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"inventory_{day}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.md:
        lines = [f"# 小哥盤點 {day}", "", f"流動性母體 {len(hist)} 檔；有分點資料 {result['universe']['with_chip']} 檔。", "",
                 f"**大盤環境**：{json.dumps(result['market_env'], ensure_ascii=False)}", ""]
        for k, m in result["methods"].items():
            lines.append(f"## {k} {m['name']}（{m['book']}）— 今日 {m['hits_today']} 檔")
            lines.append(f"規則：{m['rule']}")
            for h in m["hits"][:15]:
                lines.append(f"- {h['stock']} {h['name']} 收{h['close']:g} 帶寬{h['bw_pct']}% 乖離{h['bias20_pct']}%" + (f" {h.get('branch','')}" if h.get("branch") else ""))
            lines.append("")
        (OUT / f"inventory_{day}.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"date": day, "universe": result["universe"], "hits": {k: len(v) for k, v in hits.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
