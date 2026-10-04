#!/usr/bin/env python3
"""把四天的真實委託時點，套 PRD 的規則，產生待批准區的候選卡片。

這是「規則在那一秒會說什麼」的回放，**不是全宇宙掃描**。
範圍＝他 2026-09-29→10-02 實際送出的 52 張新倉委託時點。所以它能回答
「我的規則會不會同意我當時的決定」，但還不能回答「規則會不會找到我漏掉的標的」
（後者要掃 42 檔 × 全部分鐘，是下一步）。

輸出：`${GOLDENSTOCKS_DATA_DIR}/data/clone_agent/candidates_YYYY-MM-DD.jsonl`
由 scripts/order/run_clone_approval_ui.py 讀取顯示。

規則來源：docs/trader-clone-agent-prd.md §3（硬條件 H1-H5、主排序 R2/R6、
族群雙判準、加分項）、§4.1（進場掛價）、§5.2（價格帶口數上限）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

from clone_agent.approval import _data_dir, intent_id  # noqa: E402

P = Path.home() / "goldenstocks-data/data"
EQUITY = 1_748_587
VOL_FLOOR_WAN = 3_000          # §3 H4：5 分成交額門檻（萬元）；見 Q16 取捨曲線
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]


def margin_rate(root: str, specs: dict, rates: dict) -> float:
    s = specs.get(root) or {}
    sid = str(s.get("underlying_id") or s.get("stock_id") or "")
    return rates.get(sid, 20.25) / 100


def lot_cap(price: float, shares: int, rate: float) -> int:
    """§5.2 價格帶口數上限：1/3 持倉腿放得下幾口（至少 1）。"""
    per = price * shares * rate
    return max(1, int((EQUITY / 3) // per)) if per else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="+", default=DAYS)
    ap.add_argument("--vol-floor", type=float, default=VOL_FLOOR_WAN)
    args = ap.parse_args()

    specs = json.loads((P / "cache/taifex_stock_futures_specs.json").read_text())
    mr = json.loads((P / "cache/taifex_stock_futures_margin_rates.json").read_text())
    rates = {v["sid"]: v["原始%"] for v in mr.values() if v.get("原始%")}

    d = pd.read_csv(P / "scratch/trader_biglot_decision.csv", dtype={"stock": str})
    d = d[d.order_type.astype(str).str.contains("New") & d.day.isin(args.days)].copy()
    peer = pd.read_csv(P / "scratch/trader_peer_choice.csv", dtype={"stock": str})
    peer["hm"] = peer.submit.astype(str).str.slice(0, 5)
    d["hm"] = d.submit.astype(str).str.slice(0, 5)
    pk = peer.set_index(["day", "hm", "uname"])
    book = pd.read_csv(P / "scratch/trader_mae_mfe.csv")
    book["hm"] = book["送出時間"].astype(str).str.slice(0, 5)
    bk = book.set_index(["日期", "hm", "標的"])

    # 橫斷面百分位：用同一天同一批委託當分母（真正的全宇宙排名要掃 42 檔，下一步）
    for col in ("amt_5m", "big_30m", "big_5m"):
        d[col + "_pct"] = d.groupby("day")[col].rank(pct=True) * 100

    out: dict[str, list] = {}
    for r in d.itertuples():
        root = str(r.stock)
        sp = specs.get(root) or {}
        sh = sp.get("contract_shares", 2000)
        rate = margin_rate(root, specs, rates)
        px = float(r.price)
        amt5 = (r.amt_5m or 0) * 10000       # 億 → 萬
        hits, bonus, risks = [], [], []

        # ---- §3 硬條件
        blocked = []
        if not ("09:00" <= str(r.submit)[:5] <= "13:40"):
            blocked.append("H1 不在 09:00–13:40")
        if amt5 and amt5 < args.vol_floor:
            blocked.append(f"H4 5分成交額 {amt5:,.0f} 萬 < 門檻 {args.vol_floor:,.0f} 萬")
        if str(r.side) != "Buy":
            blocked.append("H2 只做多（條件反向只出平倉卡）")

        # ---- §3.2 主排序
        a5p = r.amt_5m_pct if pd.notna(r.amt_5m_pct) else None
        b30p = r.big_30m_pct if pd.notna(r.big_30m_pct) else None
        if a5p is not None and b30p is not None:
            if a5p >= 75 and b30p >= 75:
                hits.append(f"R2 高信心：5分成交額前 {100-a5p:.0f}% ∧ 大戶30分前 {100-b30p:.0f}%")
            elif a5p >= 50 and b30p >= 50:
                hits.append(f"R6 候選：5分成交額前 {100-a5p:.0f}% ∧ 大戶30分前 {100-b30p:.0f}%")
            else:
                blocked.append(f"主排序未過（成交額前 {100-a5p:.0f}%／大戶30分前 {100-b30p:.0f}%）")
        if amt5:
            hits.append(f"5分成交額 {amt5:,.0f} 萬")
        if pd.notna(r.big_5m):
            (hits if r.big_5m > 0 else risks).append(f"大戶淨額5分 {r.big_5m:+,.0f} 萬")
        if pd.notna(r.big_30m):
            (hits if r.big_30m > 0 else risks).append(f"大戶淨額30分 {r.big_30m:+,.0f} 萬")

        # ---- §3.3 族群內雙判準
        try:
            pr = pk.loc[(r.day, r.hm, r.uname)]
            if isinstance(pr, pd.DataFrame):
                pr = pr.iloc[0]
            if pd.notna(pr.get("chg_pctile")):
                msg = f"族群「{pr.get('group')}」內漲幅第 {pr.get('chg_pctile'):.0f} 百分位（{pr.get('n_peers')} 檔）"
                (hits if pr["chg_pctile"] <= 50 else risks).append(msg)
            if pd.notna(pr.get("bias_pctile")):
                msg = f"族群內 20MA 乖離第 {pr.get('bias_pctile'):.0f} 百分位"
                (hits if pr["bias_pctile"] <= 50 else risks).append(msg)
        except KeyError:
            pass

        # ---- §4.1 掛價與五檔
        pnote = f"距送出時中價 {(r.px_now - px) / px * 100:+.2f}%" if pd.notna(getattr(r, "px_now", None)) else ""
        try:
            b = bk.loc[(r.day, r.hm, r.uname)]
            if isinstance(b, pd.DataFrame):
                b = b.iloc[0]
            dist = b.get("掛價距對手價檔")
            if pd.notna(dist):
                pnote = f"距對手價 {dist:+.1f} 檔"
                if -6 <= dist <= -3:
                    risks.append(f"⚠ 死亡區：掛價距對手價 {dist:+.1f} 檔（歷史成交率 11%）")
            qa = b.get("前面排隊張數")
            if pd.notna(qa):
                (risks if qa > 20 else bonus).append(f"同價位前面排隊 {qa:.0f} 張")
            tot = b.get("送出_五檔總量")
            if pd.notna(tot):
                (risks if tot < 30 else bonus).append(f"五檔總量 {tot:.0f} 張")
        except KeyError:
            risks.append("無期貨五檔快照（無法算排隊位置）")

        cap = lot_cap(px, sh, rate)
        per = px * sh * rate
        if cap < 1:
            blocked.append("保證金不足一口")
        risks.extend(blocked)

        c = {"day": r.day, "time": str(r.submit)[:8], "symbol": f"{root}({sp.get('name', root)})",
             "uname": r.uname, "side": "Buy", "action": "新倉買進" if r.side == "Buy" else "新倉賣出",
             "price": px, "price_note": pnote, "lot": min(1, cap),
             "margin": round(per), "shares": int(sh), "rate": round(rate, 4), "lot_cap": cap,
             "equity": EQUITY,
             "leg_note": f"每口 {per:,.0f} 元 = 權益的 {100*per/EQUITY:.1f}%；價格帶上限 {cap} 口",
             "hits": hits, "bonus": bonus, "risks": risks,
             "verdict": "blocked" if blocked else "ok",
             "actual_status": int(r.status) if pd.notna(r.status) else None}
        c["intent_id"] = intent_id(c)
        out.setdefault(r.day, []).append(c)

    dd = _data_dir()
    for day, cs in out.items():
        p = dd / f"candidates_{day}.jsonl"
        p.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cs) + "\n", encoding="utf-8")
        ok = sum(1 for c in cs if c["verdict"] == "ok")
        print(f"{day}: {len(cs)} 筆候選（規則通過 {ok}、被擋 {len(cs)-ok}） → {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
