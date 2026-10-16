"""小台(MTX = 儀表板 MXF)盤中『散戶淨部位』估計(純觀察,不進分數、不是訊號)。

背景:通行的『散戶多空比』= −(三大法人淨未平倉) ÷ 全市場未平倉,法人部位盤後才公布,盤中算不出來。
這裡用『上一個交易日實際值(期交所盤後)＋ β × 前夜開盤至今的累計主動淨口』估計當日散戶淨部位。

校準(2026-10-16 探索,34 個交易日,單一行情,不計成績):Δ散戶淨部位(口)對『前夜夜盤＋當日日盤』全部方向性成交淨口,
相關 −0.71、無截距斜率 −0.36。符號為負:主動買超時散戶淨部位反而下降(散戶偏逆勢接單方)。解釋力約一半;
多空比『水位』對下一日日盤報酬的相關只有 +0.10(n=35),所以只當描述,不能當反指標。

只在『校準窗』內給估計:上一個實際值日 L 的日盤收盤之後,且 window ⊆ {夜盤(L), 日盤(下一交易日)}。
超出(基準過期、夜盤已跨到下一日)一律回 stale,不外推。
"""
from __future__ import annotations

import csv
import os

from stock_db import DATA_DIR

BETA = -0.36                      # 口(散戶淨部位變化)／口(累計主動淨口);探索期估計,日後應滾動重估(只用過去資料)
CSV_PATH = DATA_DIR / "research" / "mtx_retail" / "mtx_retail_ratio.csv"

_CACHE: dict = {"mtime": None, "rows": []}


def load_rows(path=None):
    """讀日頻散戶多空比檔(fetch_taifex_mtx_retail_ratio.py 產出);以 mtime 快取,頁面每秒呼叫也不會重讀。"""
    path = path or CSV_PATH                      # 呼叫時才取模組常數,測試可改 CSV_PATH 隔離
    try:
        m = os.path.getmtime(path)
    except OSError:
        return []
    if _CACHE["mtime"] == (str(path), m):
        return _CACHE["rows"]
    rows = []
    try:
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    rows.append({"date": r["date"], "total_oi": float(r["total_oi"]), "inst_net": float(r["inst_net_oi"])})
                except (KeyError, ValueError):
                    continue
    except OSError:
        return []
    rows.sort(key=lambda r: r["date"])
    _CACHE.update(mtime=(str(path), m), rows=rows)
    return rows


def _ord(key):
    return (key[1], 0 if key[0] == "day" else 1)


def _net_lots(bars):
    return sum(sum(b["bl"].values()) - sum(b["sl"].values()) for b in bars.values())


def estimate(sessions, rows=None, beta=BETA):
    """sessions: {(stype, T): {minute: bar}},bar['bl'|'sl'] = {組別: 口數}。回傳 dict,status ∈ ok / nodata / stale / incomplete。"""
    rows = load_rows() if rows is None else rows
    if not rows:
        return {"status": "nodata"}
    base = rows[-1]
    L = base["date"]
    after = sorted((k for k in sessions if sessions[k] and _ord(k) > (L, 0)), key=_ord)
    night_l = ("night", L)
    days = [k for k in after if k[0] == "day"]
    allowed = {night_l} | set(days[:1])             # 校準窗:夜盤(L) + 下一個交易日日盤
    if any(k not in allowed for k in after):
        return {"status": "stale", "base_date": L}
    if days and night_l not in after:
        return {"status": "incomplete", "base_date": L}
    f = sum(_net_lots(sessions[k]) for k in after)
    base_net = -base["inst_net"]
    delta = beta * f
    est_net = base_net + delta
    tot = base["total_oi"]
    return {"status": "ok", "base_date": L, "total_oi": tot, "base_net": base_net, "base_pct": base_net / tot * 100,
            "flow": f, "delta": delta, "est_net": est_net, "est_pct": est_net / tot * 100, "windows": [f"{k[0]}|{k[1]}" for k in after]}


def render_block(est, esc=lambda s: s):
    """回傳 HTML 片段;沒有可用估計時給一行說明,不隱藏。"""
    st = est.get("status")
    head = "<div class='sect'>散戶淨部位估計(小台·純觀察)</div>"
    if st == "nodata":
        return head + "<div class='meta'>沒有期交所基準檔(mtx_retail_ratio.csv),無法估計。請執行 scripts/research/fetch_taifex_mtx_retail_ratio.py。</div>"
    if st in ("stale", "incomplete"):
        why = "基準值已過期(盤後尚未更新期交所資料,或夜盤已跨到下一個交易日)" if st == "stale" else "本機缺前夜盤資料,累計淨口不完整"
        return head + f"<div class='meta'><span class='warnv'>⚠</span> {esc(why)};最近一次實際值為 {esc(est['base_date'])}。不外推。</div>"
    sgn = lambda v: "up" if v > 0 else ("dn" if v < 0 else "dim")  # noqa: E731
    d = est["delta"]
    return (head + "<div class='wrap'><table><thead><tr>"
            "<th>基準日(實際)</th><th>散戶淨部位<br><span class='sub'>口·期交所</span></th><th>多空比<br><span class='sub'>%·實際</span></th>"
            "<th>前夜至今累計主動淨口<br><span class='sub'>買−賣·口</span></th><th>估計變化<br><span class='sub'>β×淨口·口</span></th>"
            "<th>估計淨部位<br><span class='sub'>口</span></th><th>估計多空比<br><span class='sub'>%</span></th></tr></thead><tbody><tr>"
            f"<td>{esc(est['base_date'])}</td><td>{est['base_net']:+,.0f}</td><td>{est['base_pct']:+.1f}%</td>"
            f"<td class='{sgn(est['flow'])}'>{est['flow']:+,.0f}</td><td class='{sgn(-d)}'>{d:+,.0f}</td>"
            f"<td>{est['est_net']:+,.0f}</td><td><b>{est['est_pct']:+.1f}%</b></td></tr></tbody></table></div>"
            "<div class='meta'>散戶＝全市場未平倉扣掉三大法人(期交所盤後公布);正值＝散戶淨偏多。"
            f"β={BETA:+.2f} 為 2026-10 探索期 34 日估計(相關 −0.71,只解釋約一半),負號代表主動買超時散戶淨部位反而下降。"
            "<b>多空比水位對隔日報酬無預測力(相關 +0.10),不是反指標,只是描述。</b>基準值需每日收盤後更新。</div>")
