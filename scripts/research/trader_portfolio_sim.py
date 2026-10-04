#!/usr/bin/env python3
"""組合層級模擬器 —— 4 檔上限 ＋ 挑戰式換股 ＋ 真實期貨價。

為什麼要重寫：前一版 `trader_exit_sweep2.run_path` 是**逐部位**的,沒有倉位上限,
所以它測的「被更好訊號挑戰」其實是「只要有更好的就換」。jack 描述的機制是
**滿倉時才換掉條件最差的** —— 那是組合層級的約束,逐部位模擬根本測不到。
前一版那個「≥20 分門檻才不傷」的結果不能採信,這一版才是對的建模。

跟前幾版的三個差異（都是 jack 指正的）：
  1. **戰場 23 檔**（config/biglot_roles.json 推導),不是 42 檔。橫斷面排名仍用
     全 42 檔算 —— 他畫面上就是這樣排的。
  2. **報酬用個股期貨價**：進場付賣一、出場收買一。期貨買賣價差中位 **0.276%**,
     是手續費+稅(0.008%)的 34 倍 —— 先前全部忽略它是嚴重低估成本。
  3. **進場是「散戶賣給大戶」的連乘**（大戶淨買>0 ∧ 散戶淨賣<0）,跨 30分∧全日 兩尺度,
     不是「大戶%−散戶%」相減。相減會把「大戶大買但散戶也買」跟「大戶小買但散戶大賣」
     混成同一個數字,而後者才是他要的。

保證金用台灣期交所公告的原始費率（級距 1/2/3 = 13.5/16.2/20.25%,另有 21.6–40.5%）。
"""
from __future__ import annotations

import argparse
import bisect
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

P = Path.home() / "goldenstocks-data/data"
EQUITY = 1_748_587
RISK_CAP = 1_470_000        # 他在富邦設的上限 = 權益的 84.1%
FEE_PCT = 0.008             # 手續費+期交稅 來回（價差另計,已內含在 ask/bid）
CLOSE_HM = "13:25"


def load_all() -> pd.DataFrame:
    d = pd.read_parquet(P / "scratch/trader_feature_panel.parquet")
    f = pd.read_parquet(P / "scratch/trader_futures_panel.parquet")
    m = d.merge(f, on=["day", "sid", "t"], how="inner")
    specs = json.loads((P / "cache/taifex_stock_futures_specs.json").read_text())
    mr = json.loads((P / "cache/taifex_stock_futures_margin_rates.json").read_text())
    rates = {v["sid"]: v["原始%"] for v in mr.values() if v.get("原始%")}
    sh = {}
    for root, s in specs.items():
        sid = str(s.get("underlying_id") or s.get("stock_id") or "")
        n = s.get("contract_shares") or 0
        if sid and n and n > sh.get(sid, 0):
            sh[sid] = n
    # 欄名含 % 不能當 itertuples 屬性,改安全名
    m = m.rename(columns={"距當日高%": "dist_hi", "距當日低%": "dist_lo",
                          "當日漲幅%": "chg_today", "日內振幅%": "range_today"})
    m["shares"] = m.sid.map(sh).fillna(2000)
    m["mrate"] = m.sid.map(lambda s: rates.get(s, 20.25) / 100)
    return m


def entry_mask(m: pd.DataFrame, *, pullback: float | None = -1.0) -> pd.Series:
    """v5：散戶賣給大戶（30分 ∧ 全日 都成立）＋ 量能 ＋ 共動族群 ＋ 避開午盤。"""
    e = ((m.big_30m > 0) & (m.ret_30m < 0)
         & (m.big_day > 0) & (m.ret_day < 0)
         & (m.amt_30m_pct >= 70)
         & (m.chg_cm <= 50)
         & (~m.時段.isin([12, 13])))
    if pullback is not None:
        e &= m["dist_hi"] <= pullback       # 不追高:要等回檔
    return e


def tick_size(px: float) -> float:
    """TAIFEX 股票期貨最小升降單位（與現股同級距）。"""
    for lim, t in ((10, .01), (50, .05), (100, .1), (500, .5), (1000, 1.)):
        if px < lim:
            return t
    return 5.


def score(m: pd.DataFrame) -> pd.Series:
    """候選強度 0–100。挑戰式換股靠它比大小,所以尺度要可比。"""
    return (m.big_day_pct.fillna(0) * 0.4
            + m.bigless_30m_pct.fillna(0) * 0.4
            + m.amt_30m_pct.fillna(0) * 0.2)


def simulate(m: pd.DataFrame, *, max_pos: int = 4, challenge: float | None = None,
             tp: float | None = None, pullback: float | None = -1.0,
             entry_ticks: int = 1, exit_ticks: int = 4, optimistic: bool = False,
             margin_cap: float = RISK_CAP) -> tuple[pd.DataFrame, pd.DataFrame]:
    """限價掛單模擬。

    ## 為什麼不能用「吃對手價立刻成交」
    jack 2026-10-04 指正：「我基本上都掛低幾檔的,等回檔的,不會太快成交,
    你掛單的價格都太高了。」他說的對,而且先前的逐單研究早就量過了——
    掛價距送出時中價 IQR **4.7 檔**、新倉成交率只有 **48%**、中位等 **6.4 分**。
    讓代理人每筆都吃對手價 = 每筆多付 0.276% 的期貨價差、拿不到等回檔的價、
    而且少掉那 52% 掛不到的單（那些通常是跑掉的贏家,屬於逆選擇,不能白送）。

    ## 進場
    訊號觸發時掛**買一往下 `entry_ticks` 檔**的限價買單,掛著等。
    ## 出場
    成交後掛**買一往上 `exit_ticks` 檔**的限價賣單（他實測平倉掛在對手價上方
    中位 +4 檔、賣出平倉 16 筆無一為負）。13:25 沒成交就以買一市價出。

    ## 成交口徑（兩種都算,因為真實落在中間）
    · 嚴格（預設）：賣一跌到 ≤ 掛價才算買到（有人真的賣到你的價）
    · 樂觀（`optimistic=True`）：買一碰到 ≤ 掛價就算買到（只要價格摸到）
    這是 [[paper-trading-sim-due-20260929]] 當初就約定的「嚴格與樂觀並記」。
    """
    m = m.sort_values(["day", "t"]).copy()
    m["sig"] = entry_mask(m, pullback=pullback)
    m["sc"] = score(m)
    trades, legs, pending_log = [], [], []
    for day, dg in m.groupby("day", sort=True):
        pos: dict[str, dict] = {}
        pend: dict[str, dict] = {}        # 掛著還沒成交的新倉單
        close_t = dg[dg.hm <= CLOSE_HM].t.max()
        for t, g in dg.groupby("t", sort=True):
            now = {r.sid: r for r in g.itertuples()}
            forced = t >= close_t

            def close_pos(sid, why, row, px=None):
                p = pos.pop(sid)
                ex = px if px is not None else row.fut_bid
                gross = (ex - p["entry"]) * p["sh"] * p["lot"]
                fee = p["entry"] * p["sh"] * p["lot"] * FEE_PCT / 100
                trades.append({**{k: p[k] for k in ("sid", "grp", "entry", "sh", "lot",
                                                    "margin", "mrate", "score", "why")},
                               "day": day, "exit": ex,
                               "送出買進": p["submit_hm"], "買進成交": p["hm"],
                               "掛單等待分": p["wait"], "出場時間": row.hm,
                               "持有分": round((t - p["t"]) / 60, 1),
                               "報酬%": round(100 * (ex / p["entry"] - 1), 3),
                               "淨損益": round(gross - fee),
                               "報酬÷保證金%": round(100 * (gross - fee) / p["margin"], 2),
                               "出場原因": why})

            # ---- 1) 既有持倉的出場（限價賣單掛在買一上方 exit_ticks 檔）
            for sid in list(pos):
                r = now.get(sid)
                if r is None:
                    continue
                p = pos[sid]
                if forced:
                    close_pos(sid, "13:25 收盤前以買一出場", r); continue
                lim = p["exit_limit"]
                touched = (r.fut_ask >= lim) if not optimistic else (r.fut_bid >= lim)
                if touched:
                    close_pos(sid, f"限價賣單成交（掛買一上方{exit_ticks}檔）", r, px=lim)
                elif tp is not None and 100 * (r.fut_bid / p["entry"] - 1) >= tp:
                    close_pos(sid, f"停利 +{tp}%", r)

            # ---- 2) 掛著的新倉單：看有沒有被打到
            for sid in list(pend):
                r = now.get(sid)
                if r is None:
                    continue
                q = pend[sid]
                if forced or t >= q["expire"]:
                    pending_log.append({**q, "day": day, "結果": "未成交取消"})
                    pend.pop(sid); continue
                hit = (r.fut_ask <= q["limit"]) if not optimistic else (r.fut_bid <= q["limit"])
                if not hit or len(pos) >= max_pos:
                    continue
                per = q["limit"] * q["sh"] * q["mrate"]
                if sum(p["margin"] for p in pos.values()) + per > min(margin_cap, EQUITY * 0.5):
                    continue
                pos[sid] = {"sid": sid, "grp": q["grp"], "entry": q["limit"], "t": t,
                            "hm": r.hm, "submit_hm": q["submit_hm"],
                            "wait": round((t - q["t0"]) / 60, 1),
                            "sh": q["sh"], "lot": 1, "margin": per, "mrate": q["mrate"],
                            "score": q["score"], "why": q["why"],
                            "exit_limit": q["limit"] + exit_ticks * tick_size(q["limit"])}
                pending_log.append({**q, "day": day, "結果": "成交"})
                pend.pop(sid)
                legs.append({"day": day, "t": t, "n": len(pos),
                             "margin": sum(p["margin"] for p in pos.values())})

            if forced:
                continue

            # ---- 3) 新訊號：掛買一往下 entry_ticks 檔
            cands = sorted((r for r in g.itertuples()
                            if r.sig and r.sid not in pos and r.sid not in pend),
                           key=lambda r: -r.sc)
            for c in cands:
                if len(pos) + len(pend) >= max_pos:
                    if challenge is None or not pos:
                        break
                    cur = {s: (now[s].sc if s in now else -1e9) for s in pos}
                    worst = min(cur, key=cur.get)
                    if c.sc < cur[worst] + challenge:
                        break
                    close_pos(worst, f"被更好訊號挑戰（分數差≥{challenge}）", now[worst])
                lim = c.fut_bid - entry_ticks * tick_size(c.fut_bid)
                pend[c.sid] = {"sid": c.sid, "grp": c.grp, "limit": lim, "t0": t,
                               "submit_hm": c.hm, "expire": t + 3600,
                               "sh": int(c.shares), "mrate": round(c.mrate, 4),
                               "score": round(c.sc, 1),
                               "why": (f"大戶買∧散戶賣(30分∧全日) · 量能30分第{c.amt_30m_pct:.0f}百分位"
                                       f" · 共動族群漲幅第{c.chg_cm:.0f}百分位"
                                       f" · 距當日高{c.dist_hi:+.1f}%"
                                       f" · 掛買一下{entry_ticks}檔 {lim}")}
        for sid in list(pos):
            last = dg[dg.sid == sid].iloc[-1]
            close_pos(sid, "13:25 收盤前以買一出場", last)
    pl = pd.DataFrame(pending_log)
    fill_rate = (pl.結果 == "成交").mean() if len(pl) else np.nan
    return pd.DataFrame(trades), pd.DataFrame(legs), fill_rate, pl


def summarise(tr: pd.DataFrame, lg: pd.DataFrame, label: str, fill_rate=np.nan) -> dict:
    if len(tr) < 5:
        return {"設定": label, "趟數": len(tr), "成交率%": round(100 * fill_rate, 1)}
    dm = tr.groupby("day")["淨損益"].sum()
    rm = tr.groupby("day")["報酬%"].mean()
    se = rm.std(ddof=1) / np.sqrt(len(rm)) if len(rm) > 1 else np.nan
    return {"設定": label, "趟數": len(tr), "成交率%": round(100 * fill_rate, 1),
            "淨損益": int(tr.淨損益.sum()),
            "勝率%": round(100 * (tr.淨損益 > 0).mean(), 1),
            "平均報酬%": round(tr["報酬%"].mean(), 3),
            "標準差%": round(tr["報酬%"].std(ddof=1), 3),
            "保證金報酬%": round(100 * tr.淨損益.sum() / tr.margin.sum(), 2),
            "日一致": f"{int((dm>0).sum())}/{len(dm)}",
            "t": round(tr["報酬%"].mean() / se, 2) if se and se > 0 else None,
            "中位持有分": round(tr.持有分.median(), 1),
            "中位掛單等待分": round(tr.掛單等待分.median(), 1),
            "每日損益標準差": int(dm.std(ddof=1)) if len(dm) > 1 else None,
            "換股筆數": int(tr.出場原因.str.startswith("被更好").sum()),
            "族群": tr.grp.nunique(), "標的": tr.sid.nunique(),
            "最大同時持倉": int(lg.n.max()) if len(lg) else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(P / "scratch/trader_portfolio_sim.csv"))
    args = ap.parse_args()
    m = load_all()
    print(f"面板 {len(m):,} 列 · {m.sid.nunique()} 檔戰場 · 期貨價差中位 "
          f"{m.fut_spread_pct.median():.3f}%", file=sys.stderr)
    rows = []
    # 先掃「掛幾檔」——這是他說我掛太高的那一格
    for k in (0, 1, 2, 3, 5):
        for opt in (False, True):
            tr, lg, fr, pl = simulate(m, entry_ticks=k, optimistic=opt)
            rows.append(summarise(tr, lg, f"掛買一下{k}檔 / {'樂觀' if opt else '嚴格'}口徑", fr))
    # 再掃挑戰式換股（固定掛買一下1檔、嚴格口徑）
    for ch in (None, 5, 10, 15, 20, 30):
        tr, lg, fr, pl = simulate(m, entry_ticks=1, challenge=ch)
        rows.append(summarise(tr, lg, f"挑戰{'關' if ch is None else f'≥{ch}分'}（掛買一下1檔）", fr))
    t = pd.DataFrame(rows)
    t.to_csv(args.out, index=False)
    print(t.to_string(index=False))
    tr, lg, fr, pl = simulate(m, entry_ticks=1, challenge=15)
    if len(tr):
        tr.to_csv(P / "scratch/trader_portfolio_trades.csv", index=False)
        print(f"\n逐筆 → {P}/scratch/trader_portfolio_trades.csv  ({len(tr)} 筆)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
