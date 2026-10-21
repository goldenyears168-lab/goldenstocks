#!/usr/bin/env python3
"""小哥盤點 · 預註冊假說計分（先多次前瞻驗證、累積經驗，再談更大回測）。唯讀。

做法：假說在 2026-10-21 由 10/20 訊號日的觀察形成（形成樣本，不計入證據）；從 T ≥ 2026-10-21 起的訊號日才是樣本外（OOS）。
每晚 xiaoge_m09_followup.py 產生 (T, T+1) 追蹤資料後，本程式對每個假說算「當日結果」與「累積 OOS」，寫：
  ${DATA}/chip/xiaoge_inventory/validation_tally.json   （供 /inventory 與 AI 步驟讀）
  ${DATA}/chip/xiaoge_inventory/validation_journal.md    （逐日機械式紀錄；AI 另寫心得到 inventory_<日>_ai.md）
裁決門檻（避免早下結論）：OOS 訊號日 ≥10 天 且 樣本 ≥60 才標「支持／未支持」，否則一律「累積中」。多重比較：同時追蹤 6 個假說，|t|≥2.5 才算強。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from stock_db import DATA_DIR  # noqa: E402

INV = DATA_DIR / "chip" / "xiaoge_inventory"
OOS_FROM = "2026-10-21"
MIN_DAYS, MIN_N = 10, 60

HYP = {
    "H1": "【機械性成立，不計證據，由 H7 取代】M09 在 T+1 開盤跳空 ≥4% 者，收→收報酬高於跳空 <4% 者（收→收含跳空本身，事後發現的設計缺陷）",
    "H2": "T 日漲停（≥9.5%）且 T+1 跳空 <3% 者，T+1 開→收為負的比例 >60%（開盤沒溢價就被倒貨）",
    "H3": "flagged 隔日沖分點占 T 日買盤 ≥50% 且 T+1 跳空 <3% 者，收→收平均為負",
    "H4": "T 日周轉率 ≥30% 的 M09，T+1 開→收低於其他 M09（小哥：高周轉＋隔日沖＝很弱；預期差為負）",
    "H5": "M09 整體 T+1 開→收平均 < −0.3%（開盤放空到收盤，扣掉約 0.3% 成本後仍為正；預期為負）",
    "H6": "flagged 分點照慣性在 T+1 賣出（賣出量 ≥0.7×T 日淨買）的比例 ≥60%",
    "H7": "M09 在 T+1 開盤跳空 ≥4% 者，開→收高於跳空 <4% 者（預期差為正；2026-10-21 回放後補入，回放樣本屬事後，前瞻從 10/21 起計）",
    "H8": "M09 的 T+1 開→收，低於『同日同流動性、T 日漲≥5% 但不看分點』基準的開→收（日均差 <0；回放發現基準本身就是 −0.54%，故這才是隔日沖標籤的增量檢定）",
}


def tstat(y: np.ndarray, groups: np.ndarray) -> tuple[float, float]:
    """日期叢集穩健 t（對 y 的均值）。叢集數太少時回 nan。"""
    n = len(y)
    if n < 3 or len(set(groups)) < 3:
        return float(np.mean(y)) if n else float("nan"), float("nan")
    mu = y.mean(); df = pd.DataFrame({"e": y - mu, "g": groups}).groupby("g").e.sum()
    se = np.sqrt((df ** 2).sum()) / n
    return float(mu), float(mu / se) if se > 0 else float("nan")


def diff_t(a: np.ndarray, ga: np.ndarray, b: np.ndarray, gb: np.ndarray) -> tuple[float, float]:
    if len(a) < 3 or len(b) < 3:
        return float("nan"), float("nan")
    ma, mb = a.mean(), b.mean()
    ea = pd.DataFrame({"e": a - ma, "g": ga}).groupby("g").e.sum() / len(a)
    eb = pd.DataFrame({"e": b - mb, "g": gb}).groupby("g").e.sum() / len(b)
    allg = ea.index.union(eb.index)
    v = ((ea.reindex(allg, fill_value=0) - eb.reindex(allg, fill_value=0)) ** 2).sum()
    se = np.sqrt(v)
    return float(ma - mb), float((ma - mb) / se) if se > 0 else float("nan")


def evaluate(d: pd.DataFrame, base: dict | None = None) -> dict:
    out = {}
    m9 = d[d.method == "M09"].drop_duplicates(["T", "stock"])
    if m9.empty:
        return {k: {"n": 0} for k in HYP}
    g = m9["T"].to_numpy()
    hi, lo = m9[m9.gap_pct >= 4], m9[m9.gap_pct < 4]
    diff, t = diff_t(hi.cc_pct.to_numpy(), hi["T"].to_numpy(), lo.cc_pct.to_numpy(), lo["T"].to_numpy())
    out["H1"] = {"n": len(m9), "n_hi": len(hi), "n_lo": len(lo), "stat_pp": diff, "t": t, "expect": "+"}
    s2 = m9[(m9.chg_T >= 9.5) & (m9.gap_pct < 3)]
    out["H2"] = {"n": len(s2), "share": float((s2.oc_pct < 0).mean()) if len(s2) else float("nan"), "expect": ">0.6"}
    s3 = m9[(m9.flag_of_buys_T_pct >= 50) & (m9.gap_pct < 3)] if "flag_of_buys_T_pct" in m9 else m9.iloc[0:0]
    mu, t3 = tstat(s3.cc_pct.to_numpy(), s3["T"].to_numpy()) if len(s3) else (float("nan"), float("nan"))
    out["H3"] = {"n": len(s3), "stat_pp": mu, "t": t3, "expect": "<0"}
    a, b = m9[m9.turnover_T >= 30], m9[m9.turnover_T < 30]
    d4, t4 = diff_t(a.oc_pct.to_numpy(), a["T"].to_numpy(), b.oc_pct.to_numpy(), b["T"].to_numpy()) if len(a) else (float("nan"), float("nan"))
    out["H4"] = {"n": len(m9), "n_hi": len(a), "stat_pp": d4, "t": t4, "expect": "<0"}
    mu5, t5 = tstat(m9.oc_pct.to_numpy(), g)
    out["H5"] = {"n": len(m9), "stat_pp": mu5, "t": t5, "expect": "<-0.3"}
    fr = m9.flip_ratio.dropna()
    out["H6"] = {"n": len(fr), "share": float((fr >= 0.7).mean()) if len(fr) else float("nan"), "expect": ">=0.6"}
    hi7, lo7 = m9[m9.gap_pct >= 4], m9[m9.gap_pct < 4]
    d7, t7 = diff_t(hi7.oc_pct.to_numpy(), hi7["T"].to_numpy(), lo7.oc_pct.to_numpy(), lo7["T"].to_numpy())
    out["H7"] = {"n": len(m9), "n_hi": len(hi7), "n_lo": len(lo7), "stat_pp": d7, "t": t7, "expect": "+"}
    if base:
        diffs = np.array([gm.oc_pct.mean() - base[T] for T, gm in m9.groupby("T") if T in base])
        if len(diffs) >= 3:
            out["H8"] = {"n": len(m9), "days": len(diffs), "stat_pp": float(diffs.mean()), "t": float(diffs.mean() / (diffs.std(ddof=1) / np.sqrt(len(diffs)))), "expect": "<0"}
    out.setdefault("H8", {"n": len(m9), "expect": "<0"})
    return out


def verdict(k: str, r: dict, days: int) -> str:
    if k == "H1":
        return "機械性，不計證據"
    if days < MIN_DAYS or r.get("n", 0) < MIN_N:
        return f"累積中（{days}/{MIN_DAYS} 天，n={r.get('n', 0)}/{MIN_N}）"
    t = r.get("t"); sh = r.get("share")
    if k in ("H2", "H6"):
        ok = sh > (0.6)
        return "支持" if ok else "未支持"
    if t is None or t != t:
        return "資料不足以判斷"
    exp = r["expect"]
    ok = (exp == "+" and t >= 2.5) or (exp == "<0" and t <= -2.5) or (exp == "<-0.3" and r["stat_pp"] < -0.3 and t <= -2.5)
    return "強支持" if ok else ("方向相同未達門檻" if ((exp == "+" and t > 0) or (exp != "+" and t < 0)) else "未支持")


def base_oc(Ts: list[str]) -> dict:
    """每個訊號日 T：同流動性（PIT 前20日均成交值≥3千萬）、T 日漲≥5% 的所有股票（不看分點）T+1 開→收平均（%）。"""
    import sqlite3
    from stock_db import DEFAULT_DB_PATH
    c = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    b = pd.read_sql_query("select stock_id,trade_date d,open,close,volume from stock_daily_bars where source in ('twse_mi_index','tpex_daily') and trade_date>=? and length(stock_id)=4 and stock_id glob '[1-9][0-9][0-9][0-9]'",
                          c, params=((pd.Timestamp(min(Ts)) - pd.Timedelta(days=45)).strftime("%Y-%m-%d"),)).drop_duplicates(["stock_id", "d"]).sort_values(["stock_id", "d"])
    g = b.groupby("stock_id")
    b["ret"] = g.close.pct_change(); b["tv20"] = (b.close * b.volume).groupby(b.stock_id).transform(lambda s: s.shift(1).rolling(20).mean())
    b["oc"] = g.close.shift(-1) / g.open.shift(-1) - 1; b["gap"] = g.open.shift(-1) / b.close - 1
    x = b[b.d.isin(Ts) & (b.ret >= 0.05) & (b.tv20 >= 3e7)].dropna(subset=["oc"])
    x = x[(x.gap.abs() < 0.12) & (x.oc.abs() < 0.12)]
    return {T: float(gg.oc.mean() * 100) for T, gg in x.groupby("d") if len(gg) >= 3}


def main():
    pqp = INV / "m09_followup.parquet"
    if not pqp.exists():
        print("尚無 m09_followup.parquet"); return
    d = pd.read_parquet(pqp)
    oos = d[d["T"] >= OOS_FROM]; form = d[d["T"] < OOS_FROM]
    days = int(oos["T"].nunique())
    base = base_oc(sorted(d["T"].unique()))
    tally = {"generated_at": datetime.now().isoformat(timespec="seconds"), "oos_from": OOS_FROM, "oos_days": days, "formation_rows": int(len(form)),
             "hypotheses": {}, "formation": evaluate(form, base) if len(form) else {}}
    ev = evaluate(oos, base) if len(oos) else {k: {"n": 0} for k in HYP}
    for k, text in HYP.items():
        tally["hypotheses"][k] = {"text": text, **ev.get(k, {}), "verdict": verdict(k, ev.get(k, {"n": 0}), days)}
    (INV / "validation_tally.json").write_text(json.dumps(tally, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    lastT = d["T"].max()
    day_ev = evaluate(d[d["T"] == lastT], base)
    lines = [f"\n## 訊號日 {lastT} → T+1 {d[d['T'] == lastT].N.iloc[0]}（{'OOS' if lastT >= OOS_FROM else '形成樣本，不計證據'}）", f"累積 OOS {days} 天"]
    for k in HYP:
        r = day_ev.get(k, {})
        bits = ", ".join(f"{a}={b:.2f}" if isinstance(b, float) else f"{a}={b}" for a, b in r.items() if a != "expect")
        lines.append(f"- {k}（預期 {r.get('expect', '')}）當日：{bits}｜累積：{tally['hypotheses'][k]['verdict']}")
    jp = INV / "validation_journal.md"
    if not jp.exists():
        jp.write_text("# 小哥盤點 · 前瞻驗證日誌（機械紀錄）\n\n假說見 scripts/ops/xiaoge_validation_journal.py 的 HYP；形成樣本＝T 日 2026-10-20 之前，OOS 從 2026-10-21 起。\n", encoding="utf-8")
    txt = jp.read_text(encoding="utf-8")
    if f"## 訊號日 {lastT} " not in txt:
        jp.write_text(txt + "\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
