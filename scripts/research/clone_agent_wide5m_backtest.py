#!/usr/bin/env python3
"""研究用:寬門檻加 5 分窗條件的回測(不改凍結規則、不改生產 watcher)。

流程: clone_agent_wide5m_scan.py 逐分鐘全記 → 本程式重放「排名濾網→冷卻→價格帶」(各變體條件不同)
→ 取各變體的聯集候選 → clone_agent_day_backtest.py 做排隊成交 → 套名單/保證金/費稅 → 依星等彙整。

用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_wide5m_backtest.py [--skip-queue]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import clone_agent_rules_backtest as rb  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

S = Path.home() / "goldenstocks-data/data/scratch"
ALLMIN = S / "clone_agent_w5_allmin"
CANDS = S / "clone_agent_w5_cands"
DAYS = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"]
OOS = {"2026-10-06"}
KEEP = 0.70 + 1e-12
COOL = 20 * 60

# 事先登記的變體(不事後增減、不調門檻)
VARIANTS = {
    "V0 現行寬門檻": (),
    "V1 +5分大戶佔比前70%": ("A",),
    "V2 +5分散戶佔比(淨買小→大≤70)": ("B",),
    "V3 +5分大戶−散戶佔比前70%": ("C",),
    "V4 +5分大戶淨額≥0∧散戶淨額<0": ("D",),
    "V5 四條全加(V1+V2+V3+V4)": ("A", "B", "C", "D"),
}


def hms(s: str) -> int:
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(x)


def old_ok(c) -> bool:
    fr = c["flow_ranks"]
    return all(fr[k] is not None and fr[k] <= KEEP for k in ("big_day", "big30", "ret30"))


def new_ok(c, tag: str) -> bool:
    f = c["f5"]
    if tag == "A":
        return f["big5_rank"] is not None and f["big5_rank"] <= KEEP
    if tag == "B":
        return f["ret5_rank"] is not None and f["ret5_rank"] <= KEEP
    if tag == "C":
        return f["diff5_rank"] is not None and f["diff5_rank"] <= KEEP
    if tag == "D":
        return f["big5_net"] >= 0 and f["ret5_net"] < 0
    raise KeyError(tag)


def replay(cards, tags, band, members=None):
    """與 watcher 同序:排名濾網(含新條件)→冷卻(通過才啟動)→價格帶。回傳觸發的卡。"""
    seen: dict[str, int] = {}
    fired = []
    for c in sorted(cards, key=lambda x: hms(x["time"])):
        if members is not None and str(c["sid"]) not in members:
            continue
        if not old_ok(c) or not all(new_ok(c, t) for t in tags):
            continue
        t = hms(c["time"])
        sid = str(c["sid"])
        if sid in seen and t - seen[sid] < COOL:
            continue
        seen[sid] = t
        if not (band[0] <= c["price"] <= band[1]):
            continue
        fired.append(c)
    return fired


def load_cards(day):
    return [json.loads(x) for x in (ALLMIN / f"candidates_{day}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def key(c):
    return (c["day"], c["time"], str(c["sid"]))


def main():
    skip_queue = "--skip-queue" in sys.argv
    R = load_rules()
    members = universe_sids(R)
    band = tuple(R["universe"]["price_band"])
    mode, cap, fee, pol = R["order"]["queue_model"], R["capital"]["margin_cap"], R["cost"]["fee_per_lot_per_side"], R["order"]["policy"]
    cards = {d: load_cards(d) for d in DAYS}

    # ① 驗證重放 = 既有掃描(225 筆,含非名單):基準變體不加新條件、不限名單
    ex = set()
    for d in DAYS[:5]:
        f = S / f"clone_agent_scan/candidates_{d}.jsonl"
        for x in f.read_text(encoding="utf-8").splitlines():
            if x.strip():
                c = json.loads(x)
                ex.add((c["day"], c["time"], str(c["sid"])))
    mine = {key(c) for d in DAYS[:5] for c in replay(cards[d], (), band)}
    print(f"[驗證] 既有掃描 {len(ex)} 筆；重放基準 {len(mine)} 筆；交集 {len(ex & mine)}；僅既有 {len(ex - mine)}；僅重放 {len(mine - ex)}")

    fired = {v: {d: replay(cards[d], tags, band, members) for d in DAYS} for v, tags in VARIANTS.items()}
    union: dict[str, dict] = {d: {} for d in DAYS}
    for v in fired:
        for d in DAYS:
            for c in fired[v][d]:
                union[d][key(c)] = c
    CANDS.mkdir(parents=True, exist_ok=True)
    for d in DAYS:
        with open(CANDS / f"candidates_{d}.jsonl", "w", encoding="utf-8") as fh:
            for c in union[d].values():
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    print("[聯集候選(名單內)]", {d: len(union[d]) for d in DAYS})

    if not skip_queue:
        for d in DAYS:
            out = S / f"w5_{d}.csv"
            subprocess.run([sys.executable, "scripts/research/clone_agent_day_backtest.py", "--day", d,
                            "--cand-dir", str(CANDS), "--out", str(out)], check=True, env={**__import__("os").environ, "PYTHONPATH": "src"})
    q = pd.concat([pd.read_csv(S / f"w5_{d}.csv") for d in DAYS], ignore_index=True)
    q["sid"] = q.sid.astype(str)
    q["k"] = list(zip(q.day, q.time, q.sid))
    fp_by_day = {d: json.loads((rb.db.CACHE / f"biglot_live_watch/futprice_{d}.json").read_text(encoding="utf-8")) for d in DAYS}

    res = {}
    for v in VARIANTS:
        ks = {key(c) for d in DAYS for c in fired[v][d]}
        sub = q[q.k.isin(ks)].copy()
        rec = {"signals": int(len(sub)), "by_star_signals": {int(s): int(n) for s, n in sub.stars.value_counts().sort_index().items()}}
        for tag, cp in (("cap", cap), ("nocap", 1e12)):
            trs, nacc, nblk = [], 0, 0
            for d, g in sub.groupby("day"):
                items, skipped, tr = rb.day_stats(g, mode, cp, fee, policy=pol, one=False)
                trs.append(tr)
                nacc += len(items)
                nblk += len(skipped)
            tr = pd.concat(trs, ignore_index=True) if trs else pd.DataFrame()
            if len(tr):
                tr["bench_bps"] = rb.benchmark(tr, members, fp_by_day)
                tr["excess_bps"] = tr.gross_bps - tr.bench_bps
            rec[tag] = {"accepted": nacc, "blocked": nblk, "fills": int(len(tr)), "tr": tr}
        res[v] = rec

    def summ(tr):
        if tr is None or not len(tr):
            return dict(n=0, win=None, net=0, net_mean=None, gross_bps=None, excess=None)
        return dict(n=int(len(tr)), win=float((tr.net > 0).mean()), net=int(tr.net.sum()), net_mean=float(tr.net.mean()),
                    gross_bps=float(tr.gross_bps.mean()), excess=float(tr.excess_bps.mean()))

    pd.set_option("display.width", 250, "display.max_columns", 40)
    print("\n===== 1. 各變體總表(保證金上限 147 萬、先到先得;樣本內=9/29–10/5、樣本外=10/6) =====")
    rows = []
    for v, r in res.items():
        tr = r["cap"]["tr"]
        s_all, s_is, s_oos = summ(tr), summ(tr[~tr.day.isin(OOS)] if len(tr) else tr), summ(tr[tr.day.isin(OOS)] if len(tr) else tr)
        ci = rb.cluster_ci(tr) if len(tr) else None
        rows.append({"變體": v, "訊號": r["signals"], "接受": r["cap"]["accepted"], "擋掉": r["cap"]["blocked"], "成交": s_all["n"],
                     "勝率": None if s_all["win"] is None else round(100 * s_all["win"]), "淨損益": s_all["net"], "每筆淨": None if s_all["net_mean"] is None else round(s_all["net_mean"]),
                     "毛bps": None if s_all["gross_bps"] is None else round(s_all["gross_bps"], 1), "超額bps": None if s_all["excess"] is None else round(s_all["excess"], 1),
                     "樣本內淨": s_is["net"], "樣本外10/6淨": s_oos["net"], "樣本外成交": s_oos["n"],
                     "每筆90%CI": None if not ci else f"{ci['lo90']:.0f}~{ci['hi90']:.0f}"})
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n===== 2. 各變體 × 星等(不設保證金上限=每張訊號都送,才能乾淨比較星等) =====")
    star_rows = []
    for v, r in res.items():
        tr = r["nocap"]["tr"]
        sig = pd.Series(r["by_star_signals"])
        for s in range(5):
            t = tr[tr.stars == s] if len(tr) else tr
            m = summ(t)
            star_rows.append({"變體": v.split(" ")[0], "星": s, "訊號": int(sig.get(s, 0)), "成交": m["n"],
                              "勝率%": None if m["win"] is None else round(100 * m["win"]), "淨損益": m["net"],
                              "每筆淨": None if m["net_mean"] is None else round(m["net_mean"]),
                              "毛bps": None if m["gross_bps"] is None else round(m["gross_bps"], 1),
                              "超額bps": None if m["excess"] is None else round(m["excess"], 1)})
    sd = pd.DataFrame(star_rows)
    print(sd.to_string(index=False))

    print("\n===== 3. 被新條件剔除 vs 保留(以 V0 的訊號為母體,不設上限;超額=扣同窗名單等權) =====")
    base_tr = res["V0 現行寬門檻"]["nocap"]["tr"]
    base_k = {key(c) for d in DAYS for c in fired["V0 現行寬門檻"][d]}
    base_tr = base_tr.copy()
    base_tr["k"] = list(zip(base_tr.day, base_tr.signal.map(lambda x: x), base_tr.sid.astype(str)))
    ex_rows = []
    for v, tags in list(VARIANTS.items())[1:]:
        vk = {key(c) for d in DAYS for c in fired[v][d]}
        kept = base_tr[base_tr.k.isin(vk)]
        drop = base_tr[base_tr.k.isin(base_k - vk)]
        a, b = summ(kept), summ(drop)
        ex_rows.append({"變體": v.split(" ")[0], "保留成交": a["n"], "保留毛bps": None if a["gross_bps"] is None else round(a["gross_bps"], 1),
                        "保留超額bps": None if a["excess"] is None else round(a["excess"], 1), "保留淨": a["net"],
                        "剔除成交": b["n"], "剔除毛bps": None if b["gross_bps"] is None else round(b["gross_bps"], 1),
                        "剔除超額bps": None if b["excess"] is None else round(b["excess"], 1), "剔除淨": b["net"]})
    print(pd.DataFrame(ex_rows).to_string(index=False))

    print("\n===== 4. 5 分窗診斷:V0 訊號裡有多少 5 分窗無成交(排名算不出來)/大戶為 0 =====")
    allv0 = [c for d in DAYS for c in fired["V0 現行寬門檻"][d]]
    f5 = pd.DataFrame([c["f5"] | {"stars": c["stars"]} for c in allv0])
    print({"V0訊號": len(f5), "5分無成交額": int((f5.amt5 <= 0).sum()), "5分大戶淨額=0": int((f5.big5_net == 0).sum()),
           "5分大戶淨額>0": int((f5.big5_net > 0).sum()), "5分散戶淨額<0": int((f5.ret5_net < 0).sum()),
           "A通過": int(f5.big5_rank.le(KEEP).sum()), "B通過": int(f5.ret5_rank.le(KEEP).sum()), "C通過": int(f5.diff5_rank.le(KEEP).sum()),
           "D通過": int(((f5.big5_net >= 0) & (f5.ret5_net < 0)).sum())})

    out = {"variants": list(VARIANTS), "summary": rows, "by_star": star_rows, "kept_vs_dropped": ex_rows}
    (S / "clone_agent_wide5m_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    for v, r in res.items():
        r["nocap"]["tr"].to_csv(S / f"clone_agent_wide5m_ledger_nocap_{v.split(' ')[0]}.csv", index=False)
    print("wrote", S / "clone_agent_wide5m_result.json")


if __name__ == "__main__":
    main()
