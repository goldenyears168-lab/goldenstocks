#!/usr/bin/env python3
"""籌碼選股判斷工具（XQ 全球贏家教學 × 權證小哥）：給一份標的清單與日期，逐檔算籌碼狀態，
輸出「哪幾檔符合哪些偏多／偏空／特別留意條件」與每條命中的數字理由。

  PYTHONPATH=src .venv/bin/python scripts/research/chip_screen.py [--asof YYYY-MM-DD] [--stocks 2330,2303 | --watchlist default] [--write]

定位（務必讀）：
  * 描述盤後籌碼狀態，不是預測。各條規則尚未單獨驗證（多因子搭配判斷，之後再配驗證）；
    repo 既有檢定：籌碼 SOP 對隔日收盤無獨立預測力、外資分點跟單無 edge。
  * 三組方向訊號各自獨立成組：法人資券（XQ）／分點（小哥，沿用 chip_sop.py 口徑）／集保（XQ）。
    組內 bull 命中數減 bear 命中數＝該組淨向；組間同向才算「偏多／偏空」，有正有負＝「分歧」。
    價量與處置、爆量、當沖等只當「特別留意」旗標，不計入方向。
  * 資料缺就 NA、不補、不拿替代資料硬推；方向組可用少於 2 組＝「資料不足」。
  * 全部門檻只定義在 TH；其中標「操作化」者是我們自己的數字，教學／書沒給。
  * 唯讀：DB 以 mode=ro 開啟；預設只印出，--write 才寫 ${DATA_DIR}/chip/screen/。
規格與規則出處：docs/chip-screen-spec.md
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "research"))

from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

CONFIG = ROOT / "config" / "chip_screen.yaml"

# ── 門檻（SSOT）。「操作化」＝教學／書沒給數字，我們自己定 ─────────────────────────────
TH = {
    "foreign_streak_days": 5,        # 外資連買／連賣天數（操作化）
    "lead_pct_of_vol": 3.0,          # XQ 分級領先者 5 日淨買賣佔 5 日量 ≥ 此% 才算（操作化）
    "fh_delta_pp": 0.5,              # 外資持股 5 日變化 ≥ ±此 pp（操作化）
    "margin_surge_pct": 20.0,        # 融資 5 日增幅 ≥ 此% 列為追價過熱（操作化）
    "short_margin_ratio_pct": 60.0,  # XQ：券資比 ≥60% 且價不跌＝可能軋空
    "sbl_chg_pct": 20.0,             # 借券賣出餘額 5 日變化 ≥ ±此%（操作化）
    "daytrade_z": 2.0,               # 當沖率較 20 日均值高出 ≥ 此個標準差（操作化）
    "vol_burst": 2.0,                # 量比 ≥2＝爆量（小哥地板股第一道條件）
    "vol_shrink": 0.5,               # 量比 <0.5＝量縮
    "thin_lots": 3000,               # 20 日均量 <3000 張＝量小（小哥當沖門檻）
    "limit_pct": 9.5,                # 漲跌停近似（未用官方漲跌停價）
    "bias_year_hi_pct": 40.0,        # 乖離年線 >40%（小哥）
    "disp_pp": 0.5,                  # 集保 4 週變化 ≥ ±此 pp（操作化）
    "disp_mid_pp": 1.5,              # 400~800 張級距往少移動 ≥ 此 pp（XQ 提醒，門檻操作化）
    "notice_30d": 3,                 # 近 30 日列注意 ≥ 此次數＝處置邊緣留意
    "disp_streak_weeks": 4,          # 集保 1000 張以上比例連增／連減週數（操作化；XQ「連增股」門檻未公開）
    "disp_jump_pp": 1.0,             # 單週變化 ≥ ±此 pp 視為暴增／驟減事件（操作化；XQ 門檻未公開）
    "foreign_small_pct": 10.0,       # 中小型股外資 5 日買超佔 5 日量 ≥ 此%（操作化；XQ 影音只說「大買超」）
    "liquid_min_lots": 1000,         # I8 流動性護欄：20 日均量 ≥ 此張才算（量太小佔比會失真；操作化）
    "forces_min": 3,                 # 多方勢力共振：外資／投信／自營／主力 5 日同向買超 ≥ 此數（操作化）
    "forces_tri_pct": 3.0,           # 且三大法人 5 日合計佔量 ≥ 此%（操作化）
    "main_buy_pct": 5.0,             # 主力買超佔量 ≥ 此%（XQ 籌碼集中度範例用 5%）
    "quiet_r10_pct": 10.0,           # 近 10 日漲幅 < 此%（XQ「主力買超但漲幅不大」；範例用 10%）
    "it_red_pct": 4.0,               # 投信佈局：當日收紅 ≥ 此%（XQ 影音：4%）
    "it_min_vol_lots": 500,          # 投信佈局：近 5 日每日量 > 此張（XQ 影音：500）
    "cap_large_100m": 2000,          # 市值分級（億）：>2000 大、300~2000 中、<300 小（操作化）
    "cap_mid_100m": 300,
}
LEAD_BY_TIER = {"大": "f", "中": "t", "小": "d"}  # XQ：大型看外資、中型看投信、小型看自營
LEAD_NAME = {"f": "外資", "t": "投信", "d": "自營"}
BULL_ROLES = {"關鍵分點", "公司派", "地緣", "高手"}

GROUPS = {"inst": "法人資券", "branch": "分點", "disp": "集保"}


# ── 小工具 ─────────────────────────────────────────────────────────────────────
def streak(vals: list[float]) -> int:
    """從最後一日往前連續同號天數（買為正、賣為負；最後一日為 0 則 0）。"""
    if not vals or vals[-1] == 0:
        return 0
    s = 1 if vals[-1] > 0 else -1
    n = 0
    for x in reversed(vals):
        if x * s > 0:
            n += 1
        else:
            break
    return n * s


def pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else a / b * 100


# ── 資料載入（唯讀）─────────────────────────────────────────────────────────────
def load_features(conn: sqlite3.Connection, sid: str, asof: str, branch_row: dict | None, disposal: dict) -> dict:
    """逐檔特徵。缺資料的欄位為 None／缺 key；不補值。單位：法人／成交量庫內為股，這裡換成張。"""
    q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731
    f: dict = {"sid": sid, "asof": asof, "warn": []}

    bars = q("SELECT trade_date,open,high,low,close,volume FROM stock_daily_bars WHERE stock_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 70", sid, asof)[::-1]
    if not bars or bars[-1][0] != asof:
        f["no_bar"] = True
        return f
    close = [b[4] for b in bars]
    vol = [b[5] for b in bars]
    vm20 = sum(vol[-21:-1]) / 20 if len(vol) >= 21 else None
    chg = (close[-1] / close[-2] - 1) * 100 if len(close) >= 2 else None
    f["px"] = {
        "close": close[-1], "chg": chg, "vol_lots": vol[-1] / 1000, "vm20_lots": None if vm20 is None else vm20 / 1000,
        "ratio": None if not vm20 else vol[-1] / vm20,
        "r5": (close[-1] / close[-6] - 1) * 100 if len(close) > 5 else None,
        "r20": (close[-1] / close[-21] - 1) * 100 if len(close) > 20 else None,
        "r10": (close[-1] / close[-11] - 1) * 100 if len(close) > 10 else None,
        "min_vol5_lots": min(vol[-5:]) / 1000 if len(vol) >= 5 else None,
    }
    vols = {b[0]: b[5] for b in bars}

    # 法人
    inst = q("SELECT trade_date,foreign_net,investment_trust_net,dealer_self_net,three_institution_net FROM stock_institutional_daily WHERE stock_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 25", sid, asof)[::-1]
    sh = q("SELECT trade_date,foreign_remaining_ratio,shares_issued FROM stock_shareholding_daily WHERE stock_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 25", sid, asof)[::-1]
    si = sh[-1][2] if sh and sh[-1][2] else None
    mcap = si * close[-1] / 1e8 if si else None
    f["mcap_100m"] = mcap
    f["tier"] = None if mcap is None else ("大" if mcap > TH["cap_large_100m"] else "中" if mcap >= TH["cap_mid_100m"] else "小")
    if inst and inst[-1][0] == asof:
        def win(n: int, col: int) -> float:
            return sum(x[col] or 0 for x in inst[-n:]) / 1000

        def volw(n: int) -> float:
            return sum(vols.get(x[0], 0) for x in inst[-n:]) / 1000

        i: dict = {}
        for n in (1, 5, 20):
            i[f"tri{n}"] = win(n, 4)
            i[f"tri{n}_pct"] = pct(win(n, 4), volw(n))
            for k, col in (("f", 1), ("t", 2), ("d", 3)):
                i[f"{k}{n}"] = win(n, col)
                i[f"{k}{n}_pct"] = pct(win(n, col), volw(n))
        for k, col in (("f", 1), ("t", 2), ("d", 3)):
            i[f"{k}_streak"] = streak([x[col] or 0 for x in inst])
        sg = [(1 if (x or 0) > 0 else -1 if (x or 0) < 0 else 0) for x in inst[-1][1:4]]
        i["t_dir"] = sg[0] if len(set(sg)) == 1 else 0   # T 日三者同向才非 0
        f["inst"] = i
    # 外資持股（剩餘比率；ratio=0 視為資料斷層，不算變化）
    if len(sh) > 5 and sh[-1][1] and sh[-6][1]:
        f["fh_d5"] = -(sh[-1][1] - sh[-6][1])
    elif sh and not sh[-1][1]:
        f["warn"].append("外資持股比率為 0，疑資料斷層")
    if len(sh) > 20 and sh[-1][1] and sh[-21][1]:
        f["fh_d20"] = -(sh[-1][1] - sh[-21][1])

    # 融資融券（餘額單位＝張）
    mg = q("SELECT trade_date,margin_balance,short_balance FROM stock_margin_daily WHERE stock_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 25", sid, asof)[::-1]
    if len(mg) > 5 and mg[-1][0] == asof:
        mb, sb = mg[-1][1], mg[-1][2]
        m: dict = {"mb": mb, "sb": sb, "ratio": pct(sb, mb), "mb_d5": mb - mg[-6][1], "sb_d5": sb - mg[-6][2],
                   "sb_high20": len(mg) >= 20 and bool(sb) and sb >= max((x[2] or 0) for x in mg[-20:]),
                   "mb_d5_pct": pct(mb - mg[-6][1], mg[-6][1]),
                   "mb_d20_pct": pct(mb - mg[-21][1], mg[-21][1]) if len(mg) > 20 else None}
        f["margin"] = m
    # 借券賣出餘額（股）
    sbl = q("SELECT trade_date,sbl_balance FROM stock_short_interest_daily WHERE stock_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 25", sid, asof)[::-1]
    if len(sbl) > 5 and sbl[-1][0] == asof and sbl[-6][1] is not None and sbl[-1][1] is not None:
        f["sbl"] = {"bal_lots": sbl[-1][1] / 1000, "d5_lots": (sbl[-1][1] - sbl[-6][1]) / 1000, "d5_pct": pct(sbl[-1][1] - sbl[-6][1], sbl[-6][1])}
    # 當沖率
    dt = q("SELECT trade_date,daytrade_ratio_pct FROM stock_daytrade_daily WHERE stock_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 21", sid, asof)[::-1]
    if dt and dt[-1][0] == asof and dt[-1][1] is not None:
        prev = [x[1] for x in dt[:-1] if x[1] is not None]
        d: dict = {"t": dt[-1][1]}
        if len(prev) >= 5:
            mu, sd = st.mean(prev), st.pstdev(prev)
            d["mean20"], d["z"] = mu, ((dt[-1][1] - mu) / sd if sd else None)
        f["daytrade"] = d
    elif dt:
        f["warn"].append(f"當沖率停在 {dt[-1][0]}")

    # 集保（週）
    rows = q("SELECT as_of_date,level,people,percent FROM stock_holding_dispersion_weekly WHERE stock_id=? AND as_of_date<=? AND as_of_date>=date(?, '-60 day')", sid, asof, asof)
    by: dict = {}
    for d_, lv, pp, pc in rows:
        by.setdefault(d_, {})[int(lv)] = (pp or 0, pc or 0)
    ser = []
    for d_ in sorted(by):
        if 17 not in by[d_]:
            continue
        g = lambda lvs, i: sum(by[d_][l][i] for l in lvs if l in by[d_])  # noqa: E731
        ser.append({"d": d_, "b1000": g([15], 1), "b400": g([12, 13, 14, 15], 1), "b400_800": g([12, 13], 1), "r100": g(range(1, 10), 1), "tot": by[d_][17][0]})
    if len(ser) >= 5:
        a, b = ser[-1], ser[-5]
        w8 = [s["b1000"] for s in ser[-8:]]
        if a["tot"] <= 5:
            f["warn"].append(f"集保 {a['d']} 總股東數僅 {a['tot']}，疑異常")
        elif len(w8) >= 6 and len(set(w8)) == 1:
            f["warn"].append("集保大戶比例多週完全不變，疑資料未更新")
        else:
            f["disp"] = {"asof": a["d"], "base": b["d"], "b1000": a["b1000"], "d_b1000": a["b1000"] - b["b1000"], "d_b400": a["b400"] - b["b400"],
                         "d_b400_800": a["b400_800"] - b["b400_800"], "d_r100": a["r100"] - b["r100"], "d_tot": a["tot"] - b["tot"],
                         "series": [s_["b1000"] for s_ in ser[-(TH["disp_streak_weeks"] + 1):]]}

    # 分點（沿用 chip_sop 的 analyse 結果）
    if branch_row and (branch_row.get("win") or {}).get("1", {}).get("conc_pct") is not None and branch_row.get("status") == "ok":
        f["branch"] = branch_row
    elif branch_row:
        f["branch_pos"] = {"boll": branch_row.get("boll"), "bias_year": branch_row.get("bias_year"), "thin": branch_row.get("thin"), "it": branch_row.get("it")}

    # 處置／注意
    f["disposal"] = {
        "windows": [(w["start"], w["end"], w.get("measure")) for w in disposal["windows"] if w["stock_id"] == sid and w["end"] >= asof],
        "notice_today": [(n.get("cum"), n.get("clauses")) for n in disposal["notices"] if n["stock_id"] == sid and n["date"] == asof],
        "notices_30d": sum(1 for n in disposal["notices"] if n["stock_id"] == sid and asof >= n["date"] >= disposal["since30"]),
    }
    return f


def load_disposal(asof: str) -> dict:
    base = DATA_DIR / "disposal"
    out = {"windows": [], "notices": [], "since30": ""}
    try:
        out["windows"] = list(csv.DictReader((base / "disposal_windows.csv").open(encoding="utf-8")))
        out["notices"] = list(csv.DictReader((base / "notice_history.csv").open(encoding="utf-8")))
    except OSError:
        pass
    d = datetime.strptime(asof, "%Y-%m-%d")
    out["since30"] = datetime.fromordinal(d.toordinal() - 30).strftime("%Y-%m-%d")
    return out


# ── 規則（純函式，輸入特徵 dict，輸出命中清單）─────────────────────────────────────
def _hit(rid: str, group: str, side: str, name: str, src: str, detail: str) -> dict:
    return {"id": rid, "group": group, "side": side, "name": name, "src": src, "detail": detail}


def evaluate(f: dict) -> dict:
    """回傳 {hits, coverage, group_net, label, strength, notes}。side：bull／bear／watch。"""
    hits: list[dict] = []
    cov = {"inst": False, "branch": False, "disp": False}
    inst, mg, sbl, dtd = f.get("inst"), f.get("margin"), f.get("sbl"), f.get("daytrade")
    px = f.get("px") or {}
    tier = f.get("tier")

    # —— 組一：法人資券（XQ）——
    if inst:
        cov["inst"] = True
        fs = inst["f_streak"]
        if fs >= TH["foreign_streak_days"]:
            hits.append(_hit("I1", "inst", "bull", "外資連買", "XQ", f"外資連買 {fs} 日，20 日 {inst['f20']:+,.0f} 張"))
        if fs <= -TH["foreign_streak_days"]:
            hits.append(_hit("I1", "inst", "bear", "外資連賣", "XQ", f"外資連賣 {-fs} 日，20 日 {inst['f20']:+,.0f} 張"))
        if tier:
            k = LEAD_BY_TIER[tier]
            v5, p5 = inst[f"{k}5"], inst[f"{k}5_pct"]
            if p5 is not None and abs(p5) >= TH["lead_pct_of_vol"]:
                side = "bull" if p5 > 0 else "bear"
                hits.append(_hit("I2", "inst", side, f"{tier}型股領先者{'買超' if p5 > 0 else '賣超'}", "XQ",
                                 f"{tier}型股看{LEAD_NAME[k]}：5 日 {v5:+,.0f} 張，佔 5 日量 {p5:+.1f}%"))
        if inst["t_dir"] != 0:
            hits.append(_hit("I3", "inst", "bull" if inst["t_dir"] > 0 else "bear", "三大法人 T 日一致" + ("買" if inst["t_dir"] > 0 else "賣"), "XQ",
                             f"T 日合計 {inst['tri1']:+,.0f} 張（佔量 {inst['tri1_pct'] or 0:+.1f}%）"))
        for key, nm in (("fh_d5", "5 日"),):
            v = f.get(key)
            if v is not None and abs(v) >= TH["fh_delta_pp"]:
                hits.append(_hit("I4", "inst", "bull" if v > 0 else "bear", "外資持股" + ("增加" if v > 0 else "減少"), "XQ", f"外資持股 {nm} {v:+.2f}pp"))
    if mg:
        cov["inst"] = True
        p5 = px.get("r5")
        if p5 is not None and mg["mb_d5"] < 0 and mg["sb_d5"] > 0 and p5 > 0:
            hits.append(_hit("I5", "inst", "bull", "資券反向：融資降融券升價漲", "XQ", f"融資 5 日 {mg['mb_d5']:+,} 張、融券 {mg['sb_d5']:+,} 張、價 5 日 {p5:+.1f}%（可能有主力撐）"))
        if p5 is not None and mg["mb_d5"] > 0 and mg["sb_d5"] < 0 and p5 < 0:
            hits.append(_hit("I5", "inst", "bear", "資券反向：融資升融券降價跌", "XQ", f"融資 5 日 {mg['mb_d5']:+,} 張、融券 {mg['sb_d5']:+,} 張、價 5 日 {p5:+.1f}%（散戶看好但價跌，可能有未知利空）"))
        if mg["ratio"] is not None and mg["ratio"] >= TH["short_margin_ratio_pct"] and (p5 or 0) >= 0:
            hits.append(_hit("I6", "inst", "bull", "高券資比且價不跌（軋空）", "XQ", f"券資比 {mg['ratio']:.1f}%，價 5 日 {p5 or 0:+.1f}%"))
    if sbl and sbl["d5_pct"] is not None:
        cov["inst"] = True
        if sbl["d5_pct"] <= -TH["sbl_chg_pct"]:
            hits.append(_hit("I7", "inst", "bull", "借券賣出餘額下降", "XQ", f"借券賣出餘額 5 日 {sbl['d5_pct']:+.0f}%（{sbl['d5_lots']:+,.0f} 張）"))
        if sbl["d5_pct"] >= TH["sbl_chg_pct"]:
            hits.append(_hit("I7", "inst", "bear", "借券賣出餘額上升", "XQ", f"借券賣出餘額 5 日 {sbl['d5_pct']:+.0f}%（{sbl['d5_lots']:+,.0f} 張）"))

    # —— 組二：分點（小哥，沿用 chip_sop）——
    br = f.get("branch")
    pos = f.get("branch_pos")
    boll = (br or {}).get("boll") or (pos or {}).get("boll")
    if br:
        cov["branch"] = True
        for r in br.get("rules") or []:
            side = {"buy": "bull", "sell": "bear"}.get(r.get("side"), None)
            if side is None:
                continue
            if r["id"] in ("R13", "R16a"):  # 逃命點／短線空頭與出貨池高度重疊，併入明細但仍計一次
                pass
            hits.append(_hit("B-" + r["id"], "branch", side, r["label"], "小哥", f"主力日集中度 {br['win']['1']['conc_pct']:+.1f}%"))
        for sgn, nm in ((1, "買"), (-1, "賣")):  # 同方向多個點名分點只計一條，避免組內灌水
            ns = [n for n in br.get("named") or [] if n.get("role") in BULL_ROLES and (n.get("lots") or 0) * sgn > 0]
            if ns:
                hits.append(_hit("B-N", "branch", "bull" if sgn > 0 else "bear", f"點名分點{nm}", "小哥",
                                 "、".join(f"{n['name']}（{n['role']}）T 日 {n['lots']:+,} 張" for n in ns)))
    # —— 組三：集保（XQ）——
    d = f.get("disp")
    if d:
        cov["disp"] = True
        sr = d.get("series") or []
        if len(sr) >= TH["disp_streak_weeks"] + 1:
            diffs = [b - a for a, b in zip(sr, sr[1:])]
            if all(x > 0 for x in diffs):
                hits.append(_hit("D2", "disp", "bull", "大戶持股比例連增", "XQ", f"1000 張以上連續 {len(diffs)} 週上升：{' → '.join(f'{x:.2f}' for x in sr)}%"))
            if all(x < 0 for x in diffs):
                hits.append(_hit("D2", "disp", "bear", "大戶持股比例連減", "XQ", f"1000 張以上連續 {len(diffs)} 週下降：{' → '.join(f'{x:.2f}' for x in sr)}%"))
            if abs(diffs[-1]) >= TH["disp_jump_pp"]:
                hits.append(_hit("W15", "watch", "watch", "大戶持股比例單週暴增" if diffs[-1] > 0 else "大戶持股比例單週驟減", "XQ",
                                 f"單週 {diffs[-1]:+.2f}pp（可能是增減資、轉換、ETF 申購等事件，或資料異常，先核對）"))
        if (d["d_b1000"] >= TH["disp_pp"] and d["d_r100"] <= 0) or (d["d_b400"] >= TH["disp_pp"] and d["d_tot"] < 0):
            hits.append(_hit("D1", "disp", "bull", "集保集中", "XQ", f"1000 張以上 {d['d_b1000']:+.2f}pp、400 張以上 {d['d_b400']:+.2f}pp、散戶≤100張 {d['d_r100']:+.2f}pp、股東數 {d['d_tot']:+,}（{d['base']}→{d['asof']}）"))
        if (d["d_b1000"] <= -TH["disp_pp"] and d["d_r100"] >= 0) or (d["d_b400"] <= -TH["disp_pp"] and d["d_tot"] > 0):
            hits.append(_hit("D1", "disp", "bear", "集保鬆動", "XQ", f"1000 張以上 {d['d_b1000']:+.2f}pp、400 張以上 {d['d_b400']:+.2f}pp、散戶≤100張 {d['d_r100']:+.2f}pp、股東數 {d['d_tot']:+,}（{d['base']}→{d['asof']}）"))

    # —— XQ 專家策略（lesson／videoteach；作者回測宣稱未驗證，這裡只描述是否符合條件）——
    if inst and tier in ("中", "小") and (inst["f5_pct"] or 0) >= TH["foreign_small_pct"] and (px.get("vm20_lots") or 0) >= TH["liquid_min_lots"]:
        hits.append(_hit("I8", "inst", "bull", "中小型股外資大買", "XQ 專家", f"{tier}型股外資 5 日 {inst['f5']:+,.0f} 張，佔 5 日量 {inst['f5_pct']:.1f}%（外資通常不替小股寫報告，一旦大買值得研究）"))
    if inst:
        br_main = ((f.get("branch") or {}).get("win") or {}).get("5") or {}
        mc = br_main.get("conc_pct")
        forces = [LEAD_NAME[k] for k in ("f", "t", "d") if inst[f"{k}5"] > 0] + (["主力"] if mc is not None and mc > 0 else [])
        if len(forces) >= TH["forces_min"] and (inst["tri5_pct"] or 0) >= TH["forces_tri_pct"]:
            hits.append(_hit("I9", "inst", "bull", "多方勢力共振", "XQ 專家", f"{'、'.join(forces)} 5 日同向買超；三大法人 5 日佔量 {inst['tri5_pct']:.1f}%（主力" + ("有" if mc is not None else "缺") + "資料）"))
        it5, it_pct = inst["t5"], inst["t5_pct"]
        chg_ = px.get("chg")
        if it5 > 0 and (it_pct or 0) > 0 and inst["t_streak"] >= 2 and chg_ is not None and chg_ >= TH["it_red_pct"] and (px.get("min_vol5_lots") or 0) > TH["it_min_vol_lots"]:
            hits.append(_hit("I10", "inst", "bull", "投信佈局（部分條件）", "XQ 專家", f"投信連買 {inst['t_streak']} 日、5 日佔量 {it_pct:.1f}%、當日收紅 {chg_:+.1f}%、近 5 日每日量 >{TH['it_min_vol_lots']} 張；缺「投信持股比例 <1.5%」資料，不能宣稱完整符合"))
    if mg and mg.get("sb_high20") and br is not None:
        mc1 = (br.get("win") or {}).get("5", {}).get("conc_pct")
        if mc1 is not None and mc1 >= TH["main_buy_pct"]:
            hits.append(_hit("B-X1", "branch", "bull", "主力買超且融券餘額創 20 日新高", "XQ 專家", f"主力 5 日集中度 {mc1:+.1f}%、融券餘額 {mg['sb']:,} 張創 20 日新高（缺「距融券最後回補日」資料）"))
    if br is not None:
        mc5 = (br.get("win") or {}).get("5", {}).get("conc_pct")
        r10 = px.get("r10")
        if mc5 is not None and mc5 >= TH["main_buy_pct"] and r10 is not None and r10 < TH["quiet_r10_pct"]:
            hits.append(_hit("B-X2", "branch", "bull", "主力買超但近 10 日漲幅不大", "XQ 專家", f"主力 5 日集中度 {mc5:+.1f}%、近 10 日 {r10:+.1f}%（<{TH['quiet_r10_pct']:g}%）"))
    # —— 特別留意（不計方向）——
    dis = f.get("disposal") or {}
    if dis.get("windows"):
        hits.append(_hit("W1", "watch", "watch", "處置中或已排定處置", "公告", f"處置區間 {dis['windows']}"))
    if dis.get("notice_today"):
        hits.append(_hit("W2", "watch", "watch", "T 日列注意股", "公告", f"累計 {dis['notice_today'][0][0]}、款 {dis['notice_today'][0][1]}；近 30 日 {dis['notices_30d']} 次"))
    elif dis.get("notices_30d", 0) >= TH["notice_30d"]:
        hits.append(_hit("W2", "watch", "watch", "近 30 日多次列注意", "公告", f"近 30 日 {dis['notices_30d']} 次"))
    ratio, chg = px.get("ratio"), px.get("chg")
    if ratio is not None and ratio >= TH["vol_burst"]:
        nm = "爆量黑K" if (chg or 0) < 0 else "爆量紅K"
        hits.append(_hit("W3", "watch", "watch", nm, "小哥", f"量比 {ratio:.2f}、當日 {chg:+.1f}%、20 日均量 {px['vm20_lots']:,.0f} 張（描述，不判好壞）"))
    if ratio is not None and ratio < TH["vol_shrink"]:
        hits.append(_hit("W4", "watch", "watch", "量縮", "—", f"量比 {ratio:.2f}"))
    if chg is not None and chg >= TH["limit_pct"]:
        hits.append(_hit("W5", "watch", "watch", "漲停(近似)", "—", f"當日 {chg:+.1f}%"))
    if chg is not None and chg <= -TH["limit_pct"]:
        hits.append(_hit("W5", "watch", "watch", "跌停(近似)", "—", f"當日 {chg:+.1f}%"))
    if dtd and dtd.get("z") is not None and dtd["z"] >= TH["daytrade_z"]:
        hits.append(_hit("W6", "watch", "watch", "當沖率過熱", "XQ", f"當沖率 {dtd['t']:.1f}%，較 20 日均 {dtd['mean20']:.1f}% 高 {dtd['z']:.1f} 個標準差"))
    if mg and mg["mb_d5_pct"] is not None and mg["mb_d5_pct"] >= TH["margin_surge_pct"]:
        hits.append(_hit("W7", "watch", "watch", "融資暴增（追價）", "XQ", f"融資 5 日 {mg['mb_d5_pct']:+.1f}%（{mg['mb_d5']:+,} 張）"))
    if mg and mg["ratio"] is not None and mg["ratio"] >= TH["short_margin_ratio_pct"]:
        hits.append(_hit("W8", "watch", "watch", "券資比≥60%", "XQ", f"券資比 {mg['ratio']:.1f}%（融券 {mg['sb']:,}／融資 {mg['mb']:,} 張）"))
    if sbl and sbl["d5_pct"] is not None and sbl["d5_pct"] >= TH["sbl_chg_pct"] and mg and (mg["mb_d5_pct"] or 0) >= TH["margin_surge_pct"]:
        hits.append(_hit("W9", "watch", "watch", "融資與借券同步增加", "XQ", f"融資 5 日 {mg['mb_d5_pct']:+.0f}%、借券 5 日 {sbl['d5_pct']:+.0f}%（多空力量同增）"))
    by = (boll or {}).get("bw_level")
    bias = (br or {}).get("bias_year") if br else (pos or {}).get("bias_year")
    if bias is not None and bias > TH["bias_year_hi_pct"]:
        hits.append(_hit("W10", "watch", "watch", "乖離年線偏高", "小哥", f"乖離年線 {bias:+.1f}%（>{TH['bias_year_hi_pct']:g}%）"))
    if px.get("vm20_lots") is not None and px["vm20_lots"] < TH["thin_lots"]:
        hits.append(_hit("W11", "watch", "watch", "量小", "小哥", f"20 日均量 {px.get('vm20_lots', 0):,.0f} 張（<{TH['thin_lots']:,}）"))
    if d and d["d_b1000"] >= TH["disp_pp"] and d["d_b400_800"] <= -TH["disp_mid_pp"]:
        hits.append(_hit("W12", "watch", "watch", "大戶增但 400~800 張級距減", "XQ", f"1000 張以上 {d['d_b1000']:+.2f}pp、400~800 張 {d['d_b400_800']:+.2f}pp（誰賣給誰，集保看不出）"))
    for w in f.get("warn") or []:
        hits.append(_hit("W13", "watch", "watch", "資料可疑", "—", w))
    for g in GROUPS:
        nb = sum(h["group"] == g and h["side"] == "bull" for h in hits)
        ns = sum(h["group"] == g and h["side"] == "bear" for h in hits)
        if nb and ns:
            hits.append(_hit("W14", "watch", "watch", "組內多空互斥", "—", f"{GROUPS[g]}組內偏多 {nb} 條、偏空 {ns} 條，淨向不代表單邊"))

    # —— 組淨向與結論 ——
    gnet = {g: sum((h["side"] == "bull") - (h["side"] == "bear") for h in hits if h["group"] == g) for g in GROUPS}
    avail = [g for g in GROUPS if cov[g]]
    signs = {g: (1 if gnet[g] > 0 else -1 if gnet[g] < 0 else 0) for g in avail}
    pos_n, neg_n = sum(v > 0 for v in signs.values()), sum(v < 0 for v in signs.values())
    if len(avail) < 2:
        label, strength = "資料不足", ""
    elif pos_n and neg_n:
        label, strength = "分歧", ""
    elif pos_n:
        label, strength = "偏多", "強" if pos_n >= 2 else "弱"
    elif neg_n:
        label, strength = "偏空", "強" if neg_n >= 2 else "弱"
    else:
        label, strength = "中性", ""
    return {"hits": hits, "coverage": cov, "group_net": gnet, "label": label, "strength": strength}


# ── 主程式 ─────────────────────────────────────────────────────────────────────
def load_watchlist(name: str) -> list[tuple[str, str]]:
    import yaml  # 延後 import：evaluate 單測不需要
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    out = []
    for item in cfg["watchlists"][name]:
        sid, _, nm = str(item).partition(" ")
        out.append((sid, nm or sid))
    return out


def run(conn: sqlite3.Connection, uni: list[tuple[str, str]], asof: str) -> list[dict]:
    import chip_sop as cs
    branch = {r["sid"]: r for r in cs.compute_rows(conn, [asof], uni)[asof]}
    disposal = load_disposal(asof)
    rows = []
    for sid, nm in uni:
        f = load_features(conn, sid, asof, branch.get(sid), disposal)
        if f.get("no_bar"):
            rows.append({"sid": sid, "name": nm, "label": "無當日行情", "hits": [], "coverage": {}, "group_net": {}, "features": {}})
            continue
        ev = evaluate(f)
        rows.append({"sid": sid, "name": nm, "tier": f.get("tier"), "close": f["px"]["close"], "chg": f["px"]["chg"], **ev})
    return rows


def _tag(r: dict) -> str:
    """標的名稱後面附「留意」旗標（處置注意／爆量／漲跌停／融資暴增／資料可疑）。"""
    keys = {"W1": "處置", "W2": "注意股", "W3": "爆量", "W5": "漲跌停", "W7": "融資暴增", "W13": "資料可疑", "W14": "組內互斥", "W15": "大戶單週跳動"}
    ms = sorted({keys[h["id"]] for h in r["hits"] if h["id"] in keys}, key=list(keys.values()).index)
    return f"{r['sid']} {r['name']}" + (f"⚠{'/'.join(ms)}" if ms else "")


def render(rows: list[dict], asof: str) -> str:
    L: list[str] = []
    L.append(f"# 籌碼選股判斷 {asof}\n")
    L.append("> 描述盤後籌碼狀態，不是預測；各規則尚未單獨驗證。方向＝三組（法人資券／分點／集保）組內淨向，組間同向才算偏多偏空，有正有負＝分歧；價量、處置、爆量、當沖只列「特別留意」。\n")
    by = {}
    for r in rows:
        key = r["label"] + (r.get("strength") or "")
        by.setdefault(key, []).append(r)
    order = ["偏多強", "偏多弱", "偏空強", "偏空弱", "分歧", "中性", "資料不足", "無當日行情"]
    L.append("## 一、結論分組\n")
    L.append("| 結論 | 標的 |\n|---|---|")
    for k in order:
        if k in by:
            L.append(f"| {k.replace('強', '（強）').replace('弱', '（弱）')} | " + "、".join(_tag(r) for r in by[k]) + " |")
    L.append("\n## 二、規則命中對照（哪幾檔符合哪些條件）\n")
    rules: dict = {}
    for r in rows:
        for h in r["hits"]:
            lst_ = rules.setdefault((h["side"], h["id"], h["name"]), [])
            if f"{r['sid']} {r['name']}" not in lst_:
                lst_.append(f"{r['sid']} {r['name']}")
    side_name = {"bull": "偏多", "bear": "偏空", "watch": "特別留意"}
    for side in ("bull", "bear", "watch"):
        L.append(f"### {side_name[side]}\n")
        L.append("| 條件 | 命中標的 |\n|---|---|")
        for (s, rid, nm), lst in sorted(rules.items(), key=lambda x: (x[0][1])):
            if s == side:
                L.append(f"| {rid} {nm} | " + "、".join(lst) + " |")
        L.append("")
    L.append("## 三、逐檔明細（理由）\n")
    for r in rows:
        gn = r.get("group_net") or {}
        cov = r.get("coverage") or {}
        g = "／".join(f"{GROUPS[k]}{'NA' if not cov.get(k) else f'{gn.get(k, 0):+d}'}" for k in GROUPS)
        L.append(f"### {r['sid']} {r['name']} — {r['label']}{r.get('strength') or ''}"
                 + (f"（{r.get('tier') or '?'}型・收 {r['close']:g}・{r['chg']:+.1f}%）" if r.get("close") is not None and r.get("chg") is not None else ""))
        L.append(f"組別淨向：{g}")
        for side in ("bull", "bear", "watch"):
            for h in r["hits"]:
                if h["side"] == side:
                    L.append(f"- [{side_name[side]}・{h['src']}] {h['id']} {h['name']}：{h['detail']}")
        if not r["hits"]:
            L.append("- 無命中")
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--asof")
    ap.add_argument("--stocks", help="逗號分隔代碼，例 2330,2303（名稱用代碼代替）")
    ap.add_argument("--watchlist", default="default", help="config/chip_screen.yaml 的清單名")
    ap.add_argument("--write", action="store_true", help="另存 json／md 到 ${DATA_DIR}/chip/screen/")
    ap.add_argument("--json", action="store_true", help="stdout 改印 json")
    a = ap.parse_args()
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True, timeout=20)
    asof = a.asof or conn.execute("SELECT MAX(trade_date) FROM stock_daily_bars").fetchone()[0]
    uni = [(s.strip(), s.strip()) for s in a.stocks.split(",") if s.strip()] if a.stocks else load_watchlist(a.watchlist)
    rows = run(conn, uni, asof)
    md = render(rows, asof)
    print(json.dumps(rows, ensure_ascii=False, indent=1) if a.json else md)
    if a.write:
        out = DATA_DIR / "chip" / "screen"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"chip_screen_{asof}.json").write_text(json.dumps({"asof": asof, "th": TH, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"chip_screen_{asof}.md").write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
