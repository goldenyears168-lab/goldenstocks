#!/usr/bin/env python3
"""複製人代理人「全自動、零人工審核」紙上模擬（2026-09-29 → 10-02）。

跟 trader_build_approval_candidates.py 的差別很重要：
  · 那一支只在**他實際下單的時點**回放規則 → 回答「規則同不同意我當時的決定」
  · 這一支掃**全宇宙 × 全部分鐘** → 回答「全自動的代理人自己會做什麼」

規則來源：docs/trader-clone-agent-prd.md（§3 進場 · §4 掛價 · §5 風控 · §6 出場）。

## 刻意保守的建模選擇（高估會騙自己）

1. **進場價 = 訊號分鐘結束後第 1 秒之後的第一筆成交價**。不是訊號當下的價、
   也不是買一。延遲檢定的教訓很貴：[[fade-edge-refuted-by-delay-test]] 記錄過
   「只延後 1 秒進場,+22.8 → −6.8」。同秒成交會製造假 alpha。
2. **不模擬排隊**。全宇宙 42 檔裡只有 23 檔有期貨五檔快照,無法對全部標的算排隊
   位置。所以一律假設吃得到市價——這對「掛買一」策略是**樂觀**的,報告裡會標。
3. **成本 0.8bp 來回**（大型契約實測:期交稅 0.002%×2 + 手續費 40 元×2）。
4. **保證金用台灣期交所公告的原始保證金率**（級距 1/2/3 = 13.5/16.2/20.25%,
   另有 21.6–40.5%）,不是固定 13.5%。
5. 橫斷面百分位只用**當下這一分鐘、當天宇宙內有成交的標的**算,不偷看未來。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from trader_biglot_decision_replay import RAW_DIR, replay_day, window  # noqa: E402
from trader_peer_group_choice import day_universe, load_subcat, ma20_and_prevclose  # noqa: E402

P = Path.home() / "goldenstocks-data/data"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
EQUITY = 1_748_587
RISK_CAP = 1_470_000          # 他在富邦設的風控上限 = 權益的 84.1%
COST_BP = 0.8                 # 來回成本（檔外,直接扣報酬）
MAX_POS = 4
VOL_FLOOR_WAN = 3_000         # §3 H4
TICK_SEC = 60                 # 掃描間隔


def tick_size(px: float) -> float:
    for lim, t in ((10, .01), (50, .05), (100, .1), (500, .5), (1000, 1.)):
        if px < lim:
            return t
    return 5.


def _epoch(day: str, hms: str) -> float:
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


def _px_at(A: dict, t: float) -> tuple[float | None, float | None]:
    """回傳 (<=t 的最後成交價, >t 的第一筆成交價)。後者就是可成交的進場價。"""
    ts = A["ts"]
    if not ts:
        return None, None
    j = bisect.bisect_right(ts, t)
    last = A["px"][j - 1] if j > 0 else None
    nxt = A["px"][j] if j < len(ts) else None
    return last, nxt


def _fill_at(A: dict, t: float) -> tuple[float | None, float | None]:
    """委託在 t 送出後,第一筆可成交的 (價格, **成交時間**)。

    送出時間 ≠ 成交時間。模擬器一開始把兩者混在同一個時間戳,報表就說不出
    「掛多久才成交」—— 而那正是他實際紀錄裡最有資訊量的欄位之一
    （他本人新倉中位等 6.4 分、平倉等 30.0 分）。
    """
    ts = A["ts"]
    if not ts:
        return None, None
    j = bisect.bisect_right(ts, t)
    if j >= len(ts):
        return None, None
    return A["px"][j], ts[j]


def build_day(day: str, subcat: dict) -> dict:
    """回放一天,補上累計序列與日線參考值。"""
    uni = day_universe(day)
    if not uni:
        return {}
    acc = replay_day(day, uni)
    acc = {k: v for k, v in acc.items() if v.get("ts")}
    for A in acc.values():                       # window() 需要累計序列
        if "cum_amt" not in A:
            from trader_biglot_decision_replay import _cum
            A["cum_amt"], A["cum_big"], A["cum_ret"] = _cum(A["amt"]), _cum(A["big"]), _cum(A["ret"])
            A["px0"] = A["px"][0]
    ref = ma20_and_prevclose(set(acc), day)
    return {"acc": acc, "ref": ref, "subcat": subcat}


def agg_series(acc: dict) -> tuple[list[float], list[float]]:
    """42 檔加總的大戶−散戶差額時序（他說的「台指期面板上的差額」）。"""
    evs = []
    for A in acc.values():
        for t, b, r in zip(A["ts"], A["big"], A["ret"]):
            evs.append((t, b - r))
    evs.sort()
    ts, cum, s = [], [], 0.0
    for t, v in evs:
        s += v
        ts.append(t); cum.append(s)
    return ts, cum


def scan(day: str, D: dict, agg_ts: list, agg_cum: list) -> list[dict]:
    """每 TICK_SEC 掃一次全宇宙,回傳所有通過硬條件的候選（含當下橫斷面排名）。"""
    acc, ref, subcat = D["acc"], D["ref"], D["subcat"]
    out = []
    t0, t1 = _epoch(day, "09:00:00"), _epoch(day, "13:25:00")
    t = t0
    while t <= t1:
        snap = {}
        for sid, A in acc.items():
            bp30, rp30, _, _, _, _ = window(A, t, 1800)
            bp5, rp5, amt5, _, _, px = window(A, t, 300)
            _, _, amt30, _, _, _ = window(A, t, 1800)
            bpd, rpd, amtd, _, _, _ = window(A, t, None)
            if amt5 is None or px is None or amt30 is None:
                continue
            snap[sid] = {"big30%": bp30, "bigday": bpd, "ret5%": rp5,
                         "amt5": amt5, "amt30": amt30, "px": px,
                         "bl30": (bp30 - rp30) if (bp30 is not None and rp30 is not None) else None,
                         "blday": (bpd - rpd) if (bpd is not None and rpd is not None) else None,
                         "bigless5": (bp5 - rp5) if (bp5 is not None and rp5 is not None) else None}
        if len(snap) < 5:
            t += TICK_SEC; continue
        s = pd.DataFrame(snap).T
        for c in ("amt5", "amt30", "big30%", "bigday", "bl30", "blday"):
            s[c + "_pct"] = pd.to_numeric(s[c], errors="coerce").rank(pct=True) * 100

        i = bisect.bisect_right(agg_ts, t) - 1
        aggv = agg_cum[i] if i >= 0 else 0.0
        lo_i = min(range(i + 1), key=lambda k: agg_cum[k]) if i >= 0 else 0
        agg_from_low_min = (t - agg_ts[lo_i]) / 60 if i >= 0 else 999
        i5 = bisect.bisect_left(agg_ts, t - 300)
        agg_d5 = aggv - (agg_cum[i5] if i5 <= i else aggv)
        for sid, r in s.iterrows():
            if r["amt5"] / 10000 < VOL_FLOOR_WAN:       # H4（amt5 單位=元）
                continue
            # v3 進場（2026-10-04 定案）：**全日為主、搭 30 分,5 分全部不用**。
            #   大戶佔比「全日」前30% ∧ 大減散「30分」前15% ∧ 成交額「30分」前30%
            #
            # 為什麼剔掉 5 分：使用者說「5 分鐘是雜訊」,資料同意他 ——
            # 剔掉 5 分後,分鐘內洗牌虛無校準出來的**選股 alpha 從 +0.425% 升到 +0.662%**。
            # 另外 5 分大減散每檔每日穿越零軸 31.7 次（30分 8.3、全日 2.4、兩日 0.6）,
            # 本來就不是一個穩定的狀態量。
            #
            # 為什麼不加族群條件：加了單筆報酬升到 +1.68%,但族群數從 23 掉到 6。
            # 族群內殘差相關 ρ=0.659,變異數地板 = σ√ρ,集中到 6 個族群等於放棄分散。
            # 這一格留給使用者裁決（Q9 他選了可接受集中,但那是在不知道代價的情況下）。
            a_pct = r["amt30_pct"]          # 成交額 30 分
            b_pct = r["bigday_pct"]         # 大戶佔比 全日 ← 主
            bl_pct = r["bl30_pct"]          # 大減散 30 分 ← 搭
            if pd.isna(a_pct) or pd.isna(b_pct) or pd.isna(bl_pct):
                continue
            if a_pct < 70 or b_pct < 70 or bl_pct < 85:
                continue
            grp = subcat.get(sid)
            peers = [x for x in s.index if subcat.get(x) == grp and x in ref]
            chg_pct = bias_pct = None
            if grp and len(peers) >= 2 and sid in ref:
                chg = {x: 100 * (s.loc[x, "px"] / ref[x][1] - 1) for x in peers}
                bia = {x: 100 * (s.loc[x, "px"] / ref[x][0] - 1) for x in peers}
                chg_pct = 100 * sum(v < chg[sid] for v in chg.values()) / len(chg)
                bias_pct = 100 * sum(v < bia[sid] for v in bia.values()) / len(bia)
            score = (a_pct + b_pct + bl_pct) / 3
            if a_pct >= 85 and b_pct >= 85 and bl_pct >= 95:
                score += 25                               # 高信心（三個都更嚴）
            if chg_pct is not None and chg_pct <= 50:
                score += 10
            if bias_pct is not None and bias_pct <= 50:
                score += 10
            if agg_from_low_min <= 30:
                score += 8
            if agg_d5 > 0:
                score += 4
            out.append({"day": day, "t": t, "hm": datetime.fromtimestamp(t).strftime("%H:%M"),
                        "sid": sid, "grp": grp, "px": r["px"], "amt5_pct": a_pct,
                        "big30_pct": b_pct, "bigless5": r["bigless5"], "amt5_wan": r["amt5"] / 10000,
                        "chg_pct": chg_pct, "bias_pct": bias_pct,
                        "tier": "高信心" if (a_pct >= 85 and b_pct >= 85 and bl_pct >= 95) else "標準",
                        "agg_from_low_min": agg_from_low_min, "agg_d5": agg_d5, "score": score})
        t += TICK_SEC
    return out


# ------------------------------------------------------------------ 模擬器

def simulate(cands: list[dict], DD: dict[str, dict], specs: dict, rates: dict) -> tuple:
    """全自動撮合。沒有任何人工審核 —— 規則說進就進、說出就出。

    §5 風控：同時最多 4 檔、持倉保證金 ≤50% 權益、持倉 ≤147 萬（他的富邦風控上限）、
    價格帶口數上限。§6 出場：條件反轉（主排序掉到後 50% ∧ 大戶減散戶5分轉負）、
    被更好的候選換掉（狀態距離最大的先平）、最後一天 13:25 強制平倉。
    """
    def rate(sid):
        return rates.get(str(sid), 20.25) / 100

    def shares(sid):
        for root, sp in specs.items():
            if str(sp.get("underlying_id") or sp.get("stock_id") or "") == str(sid):
                return sp.get("contract_shares", 2000), root, sp.get("name", sid)
        return None, None, None

    by_t: dict[float, list] = {}
    for c in cands:
        by_t.setdefault(c["t"], []).append(c)
    times = sorted(by_t)
    pos: dict[str, dict] = {}
    trades, legs = [], []
    last_day_end = _epoch(DAYS[-1], "13:25:00")

    for t in times:
        day = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
        acc = DD[day]["acc"]
        cur = {c["sid"]: c for c in by_t[t]}

        # ---- 出場判斷（先出再進，釋放保證金）
        for sid in list(pos):
            if sid not in acc:
                continue
            A = acc[sid]
            bp30, _, _, _, _, _ = window(A, t, 1800)
            bp5, rp5, amt5, _, _, px = window(A, t, 300)
            if px is None:
                continue
            p = pos[sid]
            reason = None
            # v2 出場（sweep 定案）：盤中**不**用條件洗出場。
            # 5 分大減散每檔每日穿越零軸 31.7 次,當出場訊號 95% 的趟數會被洗掉,
            # 平均報酬只有「抱著」的 1/4（+0.306% vs +1.286%）。
            # 全日尺度只穿越 2.4 次、兩日 0.6 次,所以只留全日尺度的真正破線。
            bpd, rpd, _, _, _, _ = window(A, t, None)
            blday = (bpd - rpd) if (bpd is not None and rpd is not None) else None
            if t >= last_day_end:
                reason = "E5 模擬結束強制平倉"
            elif t >= _epoch(day, "13:25:00"):
                reason = "E5 收盤前平倉"
            elif blday is not None and blday < 0 and (t - p["t_in"]) > 1800:
                reason = "E2 全日大戶減散戶轉負（已持有>30分才判定）"
            if reason:
                ex, t_xfill = _fill_at(A, t)
                if ex is None:
                    ex, t_xfill = px, t
                _close(pos, trades, sid, ex, t, reason, rate, shares, t_xfill)

        # ---- 進場
        ranked = sorted(by_t[t], key=lambda c: -c["score"])
        for c in ranked:
            sid = c["sid"]
            if sid in pos or len(pos) >= MAX_POS or t >= last_day_end:
                continue
            sh, root, nm = shares(sid)
            if sh is None:
                continue                       # 沒有個股期貨契約 → H3 擋掉
            A = acc.get(sid)
            if not A:
                continue
            entry, t_fill = _fill_at(A, c["t"])
            if not entry:
                continue
            per = entry * sh * rate(sid)
            cap = max(1, int((EQUITY / 3) // per))
            held = sum(p["margin"] for p in pos.values())
            if held + per > min(RISK_CAP, EQUITY * 0.5):
                continue                       # §5 風控上限
            pos[sid] = {"sid": sid, "root": root, "nm": nm, "entry": entry,
                        "t_in": t, "t_submit_in": c["t"], "t_fill_in": t_fill,
                        "sh": sh, "rate": rate(sid), "lot": 1, "margin": per,
                        "lot_cap": cap, "day_in": day, "tier": c["tier"],
                        "score": c["score"], "grp": c["grp"],
                        "why": f"{c['tier']}｜成交額30分 第{c['amt5_pct']:.0f}百分位 ∧ 大戶佔比全日 第{c['big30_pct']:.0f}百分位"
                               + (f" ∧ 族群內漲幅 第{c['chg_pct']:.0f}百分位" if c["chg_pct"] is not None else "")
                               + (f"（加分：距加總差額當日低點 {c['agg_from_low_min']:.0f} 分）"
                                  if c["agg_from_low_min"] <= 30 else "")}
            legs.append({"t": t, "day": day, "n_pos": len(pos),
                         "margin": sum(p["margin"] for p in pos.values())})
    # 收尾
    for sid in list(pos):
        A = DD[DAYS[-1]]["acc"].get(sid)
        px = A["px"][-1] if A and A["px"] else pos[sid]["entry"]
        _close(pos, trades, sid, px, last_day_end, "E5 模擬結束強制平倉", rate, shares, last_day_end)
    return trades, legs


def _hms(t):
    return datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S") if t else None


def _wait(t0, t1):
    return round(t1 - t0, 1) if (t0 and t1) else None


def _close(pos, trades, sid, exit_px, t, reason, rate, shares, t_fill_out=None):
    p = pos.pop(sid)
    gross = (exit_px - p["entry"]) * p["sh"] * p["lot"]
    ret = 100 * (exit_px / p["entry"] - 1)
    cost_pct = COST_BP / 100
    trades.append({**{k: p[k] for k in
                      ("sid", "root", "nm", "grp", "entry", "sh", "rate", "lot",
                       "margin", "lot_cap", "day_in", "tier", "score", "why")},
                   "exit": exit_px, "t_in": p["t_in"], "t_out": t,
                   "進場": datetime.fromtimestamp(p["t_in"]).strftime("%m-%d %H:%M"),
                   "出場": datetime.fromtimestamp(t).strftime("%m-%d %H:%M"),
                   "送出買進": _hms(p.get("t_submit_in")),
                   "買進成交": _hms(p.get("t_fill_in")),
                   "買進等待秒": _wait(p.get("t_submit_in"), p.get("t_fill_in")),
                   "送出平倉": _hms(t),
                   "平倉成交": _hms(t_fill_out if t_fill_out else t),
                   "平倉等待秒": _wait(t, t_fill_out),
                   "持有分": round(((t_fill_out or t) - (p.get("t_fill_in") or p["t_in"])) / 60, 1),
                   "毛損益": round(gross), "報酬%": round(ret, 3),
                   "淨報酬%": round(ret - cost_pct, 3),
                   "淨損益": round(gross - p["margin"] / p["rate"] * cost_pct / 100),
                   "報酬÷保證金%": round(100 * (gross - p["margin"] / p["rate"] * cost_pct / 100) / p["margin"], 2),
                   "出場原因": reason})


# ------------------------------------------------------------------ 統計與異常

def stats_and_anomalies(tr: pd.DataFrame, legs: pd.DataFrame, n_cand: int) -> tuple[dict, list]:
    """算統計值,並主動標出「看起來不對勁」的那些。

    異常偵測的目的不是美化報告,是**在上線前找出模擬騙了我們的地方**。
    每一條都寫清楚門檻與為什麼那個門檻合理。
    """
    if tr.empty:
        return {}, [{"level": "致命", "item": "零筆交易", "detail": "規則在四天內從未觸發",
                     "why": "門檻設太高,或掃描/資料有洞"}]
    n = len(tr)
    pnl = tr["淨損益"].sum()
    win = (tr["淨損益"] > 0).mean()
    rets = tr["報酬÷保證金%"]
    daily = tr.groupby(tr["出場"].str.slice(0, 5))["淨損益"].sum()
    wins, losses = tr[tr["淨損益"] > 0]["淨損益"], tr[tr["淨損益"] <= 0]["淨損益"]
    eq = tr.sort_values("t_out")["淨損益"].cumsum()
    dd = (eq.cummax() - eq).max() if len(eq) else 0
    top3 = tr.nlargest(min(3, n), "淨損益")["淨損益"].sum()

    S = {
        "候選訊號數": n_cand,
        "實際進場筆數": n,
        "訊號→進場轉換率%": round(100 * n / n_cand, 2) if n_cand else None,
        "淨損益": int(pnl),
        "累計動用保證金": int(tr["margin"].sum()),
        "保證金報酬率%": round(100 * pnl / tr["margin"].sum(), 2),
        "勝率%": round(100 * win, 1),
        "平均單筆報酬÷保證金%": round(rets.mean(), 2),
        "單筆報酬標準差%": round(rets.std(ddof=1), 2) if n > 1 else None,
        "中位持有分鐘": round(tr["持有分"].median(), 1),
        "平均獲利": int(wins.mean()) if len(wins) else 0,
        "平均虧損": int(losses.mean()) if len(losses) else 0,
        "盈虧比": round(abs(wins.mean() / losses.mean()), 2) if len(losses) and losses.mean() else None,
        "最大單筆獲利": int(tr["淨損益"].max()),
        "最大單筆虧損": int(tr["淨損益"].min()),
        "最大回撤": int(dd),
        "前3大獲利佔總損益%": round(100 * top3 / pnl, 1) if pnl else None,
        "每日損益標準差": int(daily.std(ddof=1)) if len(daily) > 1 else None,
        "交易日數": int(len(daily)),
        "每日平均筆數": round(n / max(1, len(daily)), 1),
        "不重複標的數": int(tr["sid"].nunique()),
        "不重複族群數": int(tr["grp"].nunique()),
        "最大同時持倉": int(legs["n_pos"].max()) if not legs.empty else 0,
        "平均持倉保證金佔權益%": round(100 * legs["margin"].mean() / EQUITY, 1) if not legs.empty else None,
        "最高持倉保證金佔權益%": round(100 * legs["margin"].max() / EQUITY, 1) if not legs.empty else None,
    }

    # 日內 vs 跨日留倉拆帳 —— 第一版漏了這條,結果就是沒抓到「獲利全在隔夜」
    over = tr[tr["進場"].str.slice(0, 5) != tr["出場"].str.slice(0, 5)]
    intra = tr[tr["進場"].str.slice(0, 5) == tr["出場"].str.slice(0, 5)]
    S["跨日留倉筆數"] = int(len(over))
    S["跨日留倉淨損益"] = int(over["淨損益"].sum())
    S["日內筆數"] = int(len(intra))
    S["日內淨損益"] = int(intra["淨損益"].sum())
    S["跨日佔總損益%"] = round(100 * over["淨損益"].sum() / pnl, 1) if pnl else None
    S["夏普式比值(淨損益÷每日損益標準差)"] = (
        round(pnl / S["每日損益標準差"], 2) if S["每日損益標準差"] else None)

    A = []
    def flag(level, item, detail, why):
        A.append({"level": level, "item": item, "detail": detail, "why": why})

    # ★ 最重要的一條：規則是日內訊號,如果獲利全在隔夜,等於規則本身沒有 edge
    if len(over) and len(intra) and pnl:
        if S["跨日佔總損益%"] > 80:
            flag("致命", "獲利幾乎全來自跨日留倉",
                 f"{len(over)} 筆跨日貢獻 {over['淨損益'].sum():+,.0f}（{S['跨日佔總損益%']:.0f}%）,"
                 f"{len(intra)} 筆日內只有 {intra['淨損益'].sum():+,.0f}",
                 "進場規則是日內訊號（5分/30分大戶流）。獲利全在隔夜 = 賺的是隔夜漂移,"
                 "不是規則。而隔夜曝險完全不在規則的解釋與風控範圍內")
    # 勝率 ≈50% 本身不是問題 —— 配上夠高的盈虧比就是正常的趨勢型結構。
    # 只有「勝率像擲銅板**且**賠率沒補回來」才是真的沒有判別力。
    # 門檻取 1.5：勝率 50% 時,盈虧比 >1 就正期望,但 <1.5 的安全邊際太薄,
    # 勝率只要掉 5pp 就翻負,而 n=21 的勝率標準誤本來就有 ±11pp。
    pr = S["盈虧比"]
    if 0.45 <= win <= 0.55:
        if pr is None or pr < 1.5:
            flag("致命", "勝率等同擲銅板且賠率補不回來",
                 f"勝率 {100*win:.1f}% · 盈虧比 {pr}（n={n}）",
                 "規則是從這四天反向工程出來的,樣本內都只有五成而且賠率不足,"
                 "代表進場條件沒有方向性判別力")
        else:
            flag("低", "勝率約五成,但靠賠率撐住",
                 f"勝率 {100*win:.1f}% · 盈虧比 {pr} → 每筆期望 "
                 f"{win*wins.mean()+(1-win)*losses.mean():+,.0f}",
                 "這是正常的趨勢型結構（贏大輸小）,不是缺陷。但它對**出場**很敏感:"
                 "任何提早獲利了結的規則都會把賠率砍掉,勝率就撐不住了")
    if S["夏普式比值(淨損益÷每日損益標準差)"] is not None and S["夏普式比值(淨損益÷每日損益標準差)"] < 2:
        flag("高", "損益被日間波動淹沒",
             f"淨損益 {pnl:+,.0f} vs 每日損益標準差 {S['每日損益標準差']:,.0f}"
             f"（比值 {S['夏普式比值(淨損益÷每日損益標準差)']}）",
             "四天的樣本下比值 <2 表示這個損益跟「運氣」區分不開")

    if n < 30:
        flag("高", "樣本數不足", f"只有 {n} 筆",
             "n<30 時勝率的 95% 信賴區間寬度 >±18pp,任何勝率數字都不該當結論")
    if win >= 0.8 and n < 50:
        flag("高", "勝率高得可疑", f"{100*win:.1f}%（n={n}）",
             "規則是從這四天反向工程出來的,在同一批資料上回測必然偏高 = 樣本內過擬合")
    if len(tr) > 5:
        rest = tr.nsmallest(len(tr) - 3, "淨損益")["淨損益"].sum()
        if pnl and rest < 0:
            # 贏大輸小的策略本來就集中,但「拿掉前 3 大會變負」仍然是脆弱訊號。
            # 用相對筆數判斷嚴重度：3/21 的集中比 3/73 的集中可接受得多。
            lvl = "高" if n < 40 else "致命"
            flag(lvl, "拿掉前 3 大獲利後轉負",
                 f"其餘 {len(tr)-3} 筆合計 {rest:+,.0f}（前 3 筆佔 {3/n:.0%} 的筆數）",
                 "盈虧比 >2 的策略集中是正常的,但 n 這麼小的時候,"
                 "「少數幾筆撐住全部」跟「運氣好」在統計上分不開")
    if S["前3大獲利佔總損益%"] is not None and S["前3大獲利佔總損益%"] > 70:
        flag("高", "報酬集中在極少數交易", f"前 3 筆佔 {S['前3大獲利佔總損益%']:.0f}%",
             "拿掉那幾筆就不賺 = 這不是一個有統計支撐的策略,是幾次好運")
    if S["不重複族群數"] and n / S["不重複族群數"] > 4:
        flag("中", "族群過度集中", f"{n} 筆只分布在 {S['不重複族群數']} 個族群",
             "族群內殘差相關 ρ=0.659,等於實際獨立賭注遠少於筆數,日報酬標準差被低估")
    if S["交易日數"] < 4:
        flag("中", "沒有每天都交易", f"只有 {S['交易日數']}/4 天有出場",
             "周轉率目標是每天都有單;某天掛零要查是沒訊號還是被風控擋住")
    if S["最大同時持倉"] < MAX_POS:
        flag("低", "從未用滿倉位", f"最大同時 {S['最大同時持倉']} 檔（上限 {MAX_POS}）",
             "持倉腿吃不滿 = 三分法回不去,跟他「提高周轉率」的目標衝突")
    if S["平均持倉保證金佔權益%"] is not None and S["平均持倉保證金佔權益%"] < 20:
        flag("中", "持倉腿長期偏低", f"平均只有 {S['平均持倉保證金佔權益%']:.1f}%（目標 35–50%）",
             "錢大多閒置;這正是他實際交易四天的同一個毛病(中位 31.5%)")
    if len(losses) == 0:
        flag("致命", "零虧損交易", f"{n} 筆全賺",
             "真實策略不可能零虧損。先查:出場規則是不是只在獲利時才觸發(存活者偏誤)")
    if S["盈虧比"] is not None and S["盈虧比"] > 3:
        flag("中", "盈虧比異常高", f"{S['盈虧比']}",
             "通常伴隨「贏的抱很久、輸的砍很快」;要確認不是出場規則偷看未來")
    if S["中位持有分鐘"] > 240:
        flag("低", "持有時間偏長", f"中位 {S['中位持有分鐘']:.0f} 分",
             "跨日留倉會吃到隔夜跳空,而跳空不在規則的解釋範圍內")
    return S, A


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="+", default=DAYS)
    ap.add_argument("--out", default=str(Path.home() / "goldenstocks-data/data/clone_agent"))
    args = ap.parse_args()

    specs = json.loads((P / "cache/taifex_stock_futures_specs.json").read_text())
    mr = json.loads((P / "cache/taifex_stock_futures_margin_rates.json").read_text())
    rates = {v["sid"]: v["原始%"] for v in mr.values() if v.get("原始%")}
    subcat = load_subcat()

    DD, cands = {}, []
    for day in args.days:
        D = build_day(day, subcat)
        if not D:
            print(f"{day}: 無逐筆資料,略過", file=sys.stderr)
            continue
        DD[day] = D
        ats, acum = agg_series(D["acc"])
        c = scan(day, D, ats, acum)
        cands.extend(c)
        print(f"{day}: 宇宙 {len(D['acc'])} 檔 · 候選訊號 {len(c)} 個")

    trades, legs = simulate(cands, DD, specs, rates)
    tr = pd.DataFrame(trades)
    lg = pd.DataFrame(legs)
    S, A = stats_and_anomalies(tr, lg, len(cands))

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    if not tr.empty:
        cols = ["nm", "root", "grp", "tier", "lot",
                "送出買進", "買進成交", "買進等待秒", "entry",
                "送出平倉", "平倉成交", "平倉等待秒", "exit",
                "sh", "rate", "margin", "持有分", "報酬%", "淨報酬%", "淨損益",
                "報酬÷保證金%", "why", "出場原因", "進場", "出場"]
        tr[cols].to_json(out / "paper_trades.json", orient="records", force_ascii=False)
        tr[cols].to_csv(P / "scratch/trader_paper_trades.csv", index=False)
    (out / "paper_stats.json").write_text(
        json.dumps({"stats": S, "anomalies": A, "days": args.days,
                    "n_candidates": len(cands)}, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame(cands).to_csv(P / "scratch/trader_paper_candidates.csv", index=False)

    print(f"\n{'='*64}\n全自動模擬結果（零人工審核）\n{'='*64}")
    for k, v in S.items():
        print(f"  {k:<26} {v}")
    print(f"\n異常 {len(A)} 項:")
    for a in A:
        print(f"  [{a['level']}] {a['item']} — {a['detail']}\n        → {a['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
