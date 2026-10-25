"""小哥原版欄位 ＋ 我們的統計欄位（2026-10-19 jack 交辦：忠實作者 / 我們的統計 分開呈現）。

分三欄（都只讀、不下單、不進分數）：
1. 小哥·連次連量（忠實）：官方口徑連次/連量、±10 亮燈、轉色＝竭盡（紅轉綠＝買盤竭盡、綠轉紅＝賣盤竭盡）、緩搓（2 分鐘漲幅 >3.5%）。
   只用小哥公開的定義；小哥沒公開的數字（連量亮燈門檻、大紅 K 幾 %、當日轉弱怎麼算）一律不自創，欄位上寫「小哥未說明」。
2. 小哥·放空三步驟（忠實）：①周轉率 >30% ②高壓區（連續兩根以上大紅 K／連兩三天漲停）③當日轉弱＝人工判斷。
   只列出客觀可算的項目；清單齊備≠進場訊號（小哥要求再等轉色）。
3. 統計·竭盡（我們的）：RVOL、爆窗×k、窗×N、段累計張數、現價差 bps、P1-v2（買盤竭盡側竭盡v2）；
   P1-v2 觸發事件寫入前向帳本（forward_p1_events.jsonl，rule=P1-v2）供前向驗證——毛報酬仍小於價差，故不是放空訊號。

SSOT：docs/exhaustion-streak-spec.md §10、docs/warrant-xiaoge/xiaoge-system-book.md §4.5／§5.2。
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from biglot.streak import EXH_MIN_NB5, EXH_RVOL  # noqa: F401
from stock_db import DEFAULT_DB_PATH

CTX: dict[str, dict] = {}
ERR = {"n": 0, "last": None}
SLOW_RISE_BPS = 350.0          # 小哥：大概 2 分鐘內漲幅超過 3.5% → 緩搓
TURN_HI_PCT = 30.0             # 小哥：周轉率超過 30% 稱高周轉率股
LIMIT_UP_PCT = 9.5             # 「漲停」的客觀近似（台股 ±10%）；小哥只說「連兩三天漲停」
P1_PREV_RET_BPS = 300.0        # P1：昨漲 >3%（我們的檢定切點，非小哥數字）
LEDGER = Path(DEFAULT_DB_PATH).parent / "biglot_forward" / "forward_p1_events.jsonl"
_SEG: dict[str, float] = {}     # sid -> 最近一次寫帳本的時間（秒）


def reset_for_day(sids, today: str) -> None:
    CTX.clear(); _SEG.clear()
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        ph = ",".join("?" * len(sids))
        rows = conn.execute(
            f"SELECT stock_id, trade_date, open, close, volume FROM stock_daily_bars "
            f"WHERE stock_id IN ({ph}) AND trade_date<? AND trade_date>=date(?,'-20 day') AND close>0 "
            f"GROUP BY stock_id, trade_date ORDER BY stock_id, trade_date DESC", (*sids, today, today)).fetchall()
        shares = {}
        try:
            for sid, mv, c in conn.execute(
                    f"SELECT m.stock_id, m.market_value_ntd, b.close FROM stock_market_value_daily m "
                    f"JOIN stock_daily_bars b ON b.stock_id=m.stock_id AND b.trade_date=m.trade_date "
                    f"WHERE m.stock_id IN ({ph}) AND m.trade_date>=date(?,'-15 day') AND b.close>0 "
                    f"ORDER BY m.trade_date", (*sids, today)):
                if mv and c:
                    shares[sid] = mv / c                      # 最新一筆覆蓋（股本估計＝市值÷收盤）
        except Exception:  # noqa: BLE001
            pass
        conn.close()
        per: dict[str, list] = {}
        for sid, td, o, c, v in rows:
            per.setdefault(sid, []).append((td, o, c, v))
        for sid, L in per.items():                            # L 由新到舊
            if len(L) < 2:
                continue
            rets = [(L[k][2] / L[k + 1][2] - 1) * 100 for k in range(len(L) - 1)]
            red_n = 0
            for k in range(len(rets)):
                if L[k][2] > L[k][1] and rets[k] > 0:        # 紅 K：收>開；且漲（小哥「大紅 K」大小未說明，不自設門檻）
                    red_n += 1
                else:
                    break
            lu_n = 0
            for r_ in rets:
                if r_ >= LIMIT_UP_PCT:
                    lu_n += 1
                else:
                    break
            turn = (L[0][3] / shares[sid] * 100) if shares.get(sid) else None
            prev5 = [x[3] for x in L[1:6] if x[3]]
            vol_x = (L[0][3] / (sum(prev5) / len(prev5))) if (prev5 and L[0][3]) else None      # 昨量÷前5日均量（R27：前日爆量漲停/長紅→今日波動大、隔日沖出貨）
            CTX[sid] = {"ret1": rets[0], "red_n": red_n, "lu_n": lu_n, "turn": turn, "d1": L[0][0], "vol_x": vol_x}
    except Exception as e:  # noqa: BLE001
        ERR["n"] += 1; ERR["last"] = repr(e)


# ---------- P1 前向帳本 ----------
RULE_P1 = "P1-v2"      # P1-v1（昨漲>3%∧今日紅K∧外攻連次≥10∧已有爆窗，2026-10-19～10-23 前向，帳本列 rule=P1-v1）已於 2026-10-25 依 jack 指示終止；v2 的定義見下，自 2026-10-26 起計


def p1_conditions(sid: str, s: dict) -> bool:
    """P1-v2（2026-10-25 起；jack 指示套用研究定案參數，取代 v1）：買盤竭盡側（紅轉綠）的竭盡v2 剛成立＝轉色 ∧ 連次≥EXH_MIN_N(4) ∧ 近30秒 RVOL≥EXH_RVOL(2×)。
    進場＝轉色窗收盤；評估＝之後 1／3／5 分鐘『放空』含零毛報酬（賣 bid 進、買 ask 出，同口徑扣價差）。v1 的『昨漲>3%、今日紅K』不再是條件。"""
    f = (s or {}).get("flip")
    return bool(f and f.get("exh") and f["d"] > 0)


def record_p1(sid: str, ev: dict, st) -> None:
    """由 streak_live 的 flip 監聽呼叫（買盤竭盡側竭盡事件）。同股 300 秒 cooldown；啟動重放的舊事件（距牆上時間 >90 秒）不寫。例外自吞。"""
    try:
        ts = ev["t"]
        _n = datetime.now()
        if (_n.hour * 3600 + _n.minute * 60 + _n.second) - ts > 90:
            return
        if ts - _SEG.get(sid, -1e9) < 300:
            return
        _SEG[sid] = ts
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        c = CTX.get(sid) or {}
        rec = {"date": _n.strftime("%Y-%m-%d"), "t": round(ts, 1), "sid": sid, "px": ev.get("px"),
               "bid": getattr(st, "bid", None), "ask": getattr(st, "ask", None), "n": ev["n"], "rvol30": round(ev["rvol30"], 2),
               "ret1_pct": round(c["ret1"], 2) if c.get("ret1") is not None else None, "day_up": ev.get("day_up"),
               "rule": RULE_P1, "logged_at": time.time()}
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        ERR["n"] += 1; ERR["last"] = repr(e)


# ---------- 欄位 ----------
def _snap(sid):
    from biglot import streak_live
    return streak_live.snapshot(sid)


_TIP1 = ("日均量<3,000張的標的灰化、不亮燈不標竭盡（作者標的限定）。小哥原版·連次/連量（出處：官方說明欄 2024-11-21／2025-07-09、V10 14:01、V14 11:24）：每5秒窗外盤量>內盤量=紅(+1)、內>外=綠(−1)；"
         "🔔＝符合竭盡v2訊號條件（轉色∧連次≥4∧RVOL≥2×，剛轉色60秒內）；連量=最近一個5秒窗的主導方向成交張數（副字『累計』為官方畫面「每一連次累積的成交量總和」解讀，兩者並列）；"
         "『轉色』=連攻段被打斷（平手只算中斷）：紅轉綠=買盤竭盡側、綠轉紅=賣盤竭盡側；『緩搓』=約2分鐘內漲幅超過3.5%。"
         "小哥未公開：連量亮燈門檻、『大量』的數字。本欄『竭盡』採本專案研究定案的參數（2026-10-25 起，規則 exh-v2；不是小哥的數字）："
         "轉色 ∧ 連次≥4 ∧ 近30秒相對成交量(RVOL)≥2倍（RVOL＝近30秒成交量÷該股過去10日整日平均每30秒成交量）。依據：33日·1,170股票日·91.9萬事件的網格（前半選／後半驗）：5秒窗最佳，每檔每天約20個（多空合計），含零毛 +5.1／+6.2／+6.6 bps（1／3／5分），仍小於價差（~20bps+），只是時機提示、不是下單訊號。"
         "順大勢逆小勢：黑K日買盤竭盡＝空方新倉方向、紅K日賣盤竭盡＝多方新倉方向；反向的竭盡讀成『平倉／回補提示』（小哥：順大勢、在小波段轉彎處動作）。規格 docs/exhaustion-streak-spec.md、研究 docs/exhaust-reversal-scale-20261025.md。")
_TIP2 = ("小哥原版·放空三步驟（出處：說明欄 2025-09-27／2025-08-07；書 §5）：①周轉率>30%（高周轉率股，確保流動性）；"
         "②高壓區：連續兩根以上大紅K、或連兩三天漲停（或空頭中的反彈衰竭，需人工判斷）；③等當日轉弱的明確訊號，行情仍強時不逆勢；"
         "不放空布林通道第一根上漲K；盤勢強時避免短空；順大勢逆小勢（預期黑K才空）。"
         "本欄只列客觀可算的①②與今日紅K/黑K；『大紅K幾%』『當日轉弱怎麼算』小哥未說明，故不自設。"
         "①②齊備≠進場訊號，小哥要求最後再等連次連量轉色。②另列昨量÷前5日均量（R27：前日爆量漲停/長紅→今日波動大、隔日沖出貨）。尚未接：③轉弱的客觀定義、布林第一根、盤勢強避免短空（R64b）、夏天不空、隔日沖買超第一名／大股東賣超（分點盤後才有）。純描述，不進分數。")
_TIP3 = ("我們的統計·竭盡（本repo自訂門檻與檢定；與小哥原版分開）：RVOL=最近6個5秒窗(30秒)成交量÷該股平均每30秒成交量（竭盡v2判準＝轉色∧連次≥4∧RVOL≥2×）；窗×N=該窗÷每5秒均量；爆窗×k=本段內單窗≥5×均量的窗數（5×為自訂）；"
         "累計=本段累計張；價差=現在 (ask−bid)/價 bps。P1-v2（我們的統計規則，不是小哥的邏輯）＝買盤竭盡側（紅轉綠）的竭盡v2剛成立（轉色∧連次≥4∧RVOL≥2×）；"
         "只作前向驗證記錄（事件寫入 forward_p1_events.jsonl，rule=P1-v2，自2026-10-26起計；v1〔昨漲>3%∧今日紅K∧外攻連次≥10∧已有爆窗，10/19～10/23〕已於 2026-10-25 依 jack 指示終止）。"
         "歷史研究（33日·1,170股票日）含零毛 +5.1／+6.2／+6.6 bps（1／3／5分）仍小於價差（~20bps+），【不是放空訊號】。"
         "另：窗均×<1 表示連次由薄量窗堆成。")

def cell_author_streak(sid: str) -> str:
    from biglot import streak_live
    s = _snap(sid)
    if sid in streak_live.LOWVOL:       # 作者標的限定『日均量 3,000 張以上』（V10／說明欄）：範圍外只顯示數字、灰化、不亮燈不標竭盡
        if not s or not s["d"]:
            return f"<td class='dim' data-sort='0' title='日均量&lt;3,000張，不在作者適用範圍'>—</td>"
        return (f"<td class='dim' data-sort='0' style='opacity:.5' title='日均量&lt;3,000張（近10日），不在作者「當沖神器」適用範圍：只顯示數字，不亮燈、不標竭盡'>"
                f"{abs(s['n'])}/{s['win_lots']:,.0f}<span style='font-size:9px'> 量薄·範圍外</span></td>")
    if not s or not s["d"]:
        return f"<td class='dim' data-sort='0' title='{_TIP1}'>—</td>"
    n, d = s["n"], s["d"]
    cls = "up" if d > 0 else "dn"
    bell = "🔔" if s["lit"] else ""
    main = f"{bell}{abs(n)}/{s['win_lots']:,.0f}"
    cum = f"<span class='dim' style='font-size:9px'> 累計{s['cum_lots']:,.0f}</span>" if s.get("cum_lots") else ""
    if s.get("cum_lots") and n:
        cum += f"<span class='dim' style='font-size:9px'> 窗均{s['cum_lots'] / abs(n):,.0f}張</span>"   # 事實：連次是靠幾張的窗堆出來的（薄量股 1 張窗也能累到±10）
    flip = ""
    f = s.get("flip")
    if f:
        side = "紅→綠" if f["d"] > 0 else "綠→紅"
        brk = f"打斷窗{f['brk_lots']:,.0f}張" if f.get("brk_lots") else "打斷窗"
        rv = f.get("rvol30")
        rvs = f"RVOL {rv:.1f}×" if rv is not None else "RVOL 無分均量"
        if f.get("exh"):
            # 順大勢＝新倉方向（黑K日的買盤竭盡＝空方新倉點、紅K日的賣盤竭盡＝多方新倉點）；反向＝平倉／回補提示（小哥：順大勢、逆小勢，在轉彎處動作）
            aligned = (f["d"] > 0) != bool(f.get("day_up"))
            role = "順大勢·新倉方向" if aligned else f"平倉/回補提示·今日{'紅' if f.get('day_up') else '黑'}K"
            flip = (f"<div style='font-size:9px;font-weight:600'>{'買盤竭盡' if f['d'] > 0 else '賣盤竭盡'}"
                    f"<span class='dim'>({f['n']}連·{rvs}·{role}·{f['age']:.0f}s前)</span></div>")
        else:
            miss = ["RVOL不足(<%g×)" % EXH_RVOL] if rv is not None else ["無分均量"]
            flip = (f"<div style='font-size:9px'>轉色 {side}<span class='dim'>({f['n']}連·{rvs}·未達竭盡:{'、'.join(miss)}·{f['age']:.0f}s前)</span></div>")
    slow = ("<div style='font-size:9px;color:#d29922'>緩搓(2分漲&gt;3.5%)</div>"
            if s.get("ret2m") is not None and s["ret2m"] >= SLOW_RISE_BPS else "")
    return f"<td class='{cls}' data-sort='{abs(n) * d}' title='{_TIP1}'>{main}{cum}{flip}{slow}</td>"


def cell_author_short(sid: str, row: dict) -> str:
    c = CTX.get(sid)
    if not c:
        return f"<td class='dim' data-sort='0' title='{_TIP2}'>—</td>"
    parts, ok = [], 0
    t = c["turn"]
    if t is None:
        parts.append("①周轉 <span class='dim'>無股本資料</span>")
    else:
        hit = t >= TURN_HI_PCT
        ok += hit
        parts.append(f"①周轉{'✓' if hit else '✗'} {t:.0f}%")
    hit2 = c["red_n"] >= 2 or c["lu_n"] >= 2
    ok += hit2
    parts.append(f"②高壓區{'✓' if hit2 else '✗'} 連紅{c['red_n']}·昨{c['ret1']:+.1f}%" + (f"·連板{c['lu_n']}" if c["lu_n"] else "")
                 + (f"·昨量×{c['vol_x']:.1f}" if c.get("vol_x") else ""))
    dr = row.get("day_ret")
    parts.append("③今日" + ("—" if dr is None else ("紅K" if dr > 0 else "黑K") + f" {dr:+.1f}%") + "<span class='dim'>·轉弱自判</span>")
    body = "<br>".join(f"<span style='font-size:9px'>{p}</span>" for p in parts)
    return f"<td data-sort='{ok}' title='{_TIP2}'>{body}</td>"


def cell_stats(sid: str, row: dict) -> str:
    s = _snap(sid)
    if not s or not s["d"]:
        return f"<td class='dim' data-sort='0' title='{_TIP3}'>—</td>"
    bits = []
    if s.get("rvol30_now") is not None:
        bits.append(f"RVOL {s['rvol30_now']:.1f}×")
    if s["win_x"] is not None:
        bits.append(f"窗×{s['win_x']:.0f}")
    if s["nb5"]:
        bits.append(f"爆窗×{s['nb5']}")
    if s.get("cum_lots"):
        bits.append(f"段{s['cum_lots']:,.0f}張")
        if s.get("win_x") is not None and s["win_x"] and s.get("win_lots"):
            ref5 = s["win_lots"] / s["win_x"]
            bits.append(f"窗均×{s['cum_lots'] / abs(s['n']) / ref5:.1f}")      # 本段平均每窗 ÷ 每5秒均量；<1 代表連次是薄量堆出來的
    ba = s.get("bid_ask") or (None, None)
    if ba[0] and ba[1] and s.get("px"):
        bits.append(f"價差{(ba[1] - ba[0]) / s['px'] * 1e4:.0f}bps")
    tags = ""
    if p1_conditions(sid, s):
        tags += ("<div style='font-size:9px;color:#d29922;font-weight:600'>P1-v2成立"
                 "<span class='dim'>(買盤竭盡·轉色·連次≥4·RVOL≥2×·前向記錄中·非訊號)</span></div>")
    if s.get("risk"):
        tags += "<div style='font-size:9px;color:#d29922'>買盤竭盡(v2:轉色·連次≥4·RVOL≥2×)</div>"
    txt = "·".join(bits)
    return f"<td class='dim' data-sort='{s['nb5']}' title='{_TIP3}'><span style='font-size:9px'>{txt}</span>{tags}</td>"
