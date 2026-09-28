#!/usr/bin/env python3
"""PIT 宇宙 tick → 盤中大戶／散戶資金流 + 互動特徵面板（單次掃描）.

修正 momentum_rotation 版的三個問題：
  1. 收盤價排除 14:30 盤後定價交易
  2. 品質閘：剔除 FinMind 回傳的 5 分／20 分等間隔快照（非逐筆）
  3. 宇宙為 PIT 規則定義，非回頭挑選

互動特徵在**同一次掃描**內算完，不重讀 12,800 個檔案。
"""

from __future__ import annotations

import collections
import json
import math
import pickle

from stock_db import DATA_DIR

CACHE = DATA_DIR / "cache" / "pit_universe_tick"
OUT = CACHE / "_flow_panel.pkl"
CUTS = [(11 * 3600, "11:00"), (12 * 3600, "12:00"),
        (12 * 3600 + 1800, "12:30"), (13 * 3600, "13:00"),
        (13 * 3600 + 1200, "13:20")]
BIG = [(1e7, "big1000"), (5e6, "big500"), (1e6, "big100")]
SMALL = 5e5
# CLOSE_T 原本誤寫成 13:01（13*3600+60），讓面板的「close」不是真收盤價、「跳空」實為
# 20.5小時報酬,且13:20這個切點因此完全拿不到資料;見 pit-tick-bigflow-overnight-verdict
# 記憶檔,2026-09-28 修正回真收盤 13:30。
OPEN_T, CLOSE_T = 9 * 3600, 13 * 3600 + 1800
M0, NMIN = 9 * 3600, 270          # 09:00 起 270 分鐘（到 13:30）


def tsec(s: str) -> float:
    h, m, r = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(r)


def _corr(a, b):
    n = len(a)
    if n < 10:
        return float("nan")
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return float("nan")
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def interaction(bigm: list[float], smlm: list[float], volm: list[float]) -> dict:
    """大戶／散戶當日互動特徵。全部只用當日盤中資料。"""
    act = [i for i, v in enumerate(volm) if v > 0]
    if len(act) < 30:
        return {}
    B = [bigm[i] for i in act]
    S = [smlm[i] for i in act]
    V = [volm[i] for i in act]
    tot = sum(V) or 1.0
    # 對立度：分鐘級大戶流 vs 散戶流的相關（負 = 對作）
    opp = _corr(B, S)
    # 交戰分鐘佔比：兩邊同分鐘反向
    war = sum(1 for b, s in zip(B, S) if b * s < 0) / len(B)
    # 交戰強度：反向分鐘裡，雙方對沖掉的量佔全日量
    inten = sum(min(abs(b), abs(s)) for b, s in zip(B, S) if b * s < 0) / tot
    # 回合數：大戶流累積淨額的變號次數（來回幾趟）
    cum = 0.0
    rounds = 0
    last = 0
    for b in B:
        cum += b
        sgn = (cum > 0) - (cum < 0)
        if sgn and last and sgn != last:
            rounds += 1
        if sgn:
            last = sgn
    # 誰先動：大戶領先散戶 k 分鐘的相關（正 = 大戶先）
    lead = float("nan")
    if len(B) > 40:
        f = _corr(B[:-3], S[3:])
        r = _corr(S[:-3], B[3:])
        if not (math.isnan(f) or math.isnan(r)):
            lead = f - r
    # 時段結構：前 1/3、中 1/3、後 1/3 的大戶淨流佔比
    k = len(B) // 3
    seg = [sum(B[:k]) / tot, sum(B[k:2 * k]) / tot, sum(B[2 * k:]) / tot]
    # 散戶尾盤投降：後 1/3 散戶淨流
    sml_late = sum(S[2 * k:]) / tot
    # 大戶吸貨型態：大戶淨買 且 散戶淨賣
    b_net, s_net = sum(B) / tot, sum(S) / tot
    return {
        "ix_oppose": opp, "ix_war_min": war, "ix_war_intensity": inten,
        "ix_rounds": rounds, "ix_lead": lead,
        "ix_seg1": seg[0], "ix_seg2": seg[1], "ix_seg3": seg[2],
        "ix_sml_late": sml_late,
        "ix_big_net": b_net, "ix_sml_net": s_net,
        "ix_absorb": b_net - s_net,
        "ix_active_min": len(act),
    }


def main() -> None:
    uni = json.loads((CACHE / "_universe.json").read_text())
    rows = []
    skipped: dict[str, int] = collections.Counter()
    files = sorted(p for p in CACHE.glob("*.json") if not p.name.startswith("_"))
    print(f"檔案 {len(files):,}", flush=True)
    for i, f in enumerate(files):
        if i % 2000 == 0:
            print(f"  {i:,}", flush=True)
        try:
            d = json.loads(f.read_text())
        except Exception:
            skipped["讀取失敗"] += 1
            continue
        if len(d) < 200:
            skipped["太少"] += 1
            continue
        d.sort(key=lambda x: x["Time"])
        sess = [t for t in d if OPEN_T <= tsec(t["Time"]) < CLOSE_T]
        if len(sess) < 200:
            skipped["盤中太少"] += 1
            continue
        ts = [tsec(t["Time"]) for t in sess]
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        mg = sum(gaps) / len(gaps)
        if mg > 0:
            sd = math.sqrt(sum((g - mg) ** 2 for g in gaps) / len(gaps))
            if sd / mg < 0.5:
                skipped["等間隔快照"] += 1
                continue
        rec = {"sid": sess[0]["stock_id"], "date": sess[0]["date"],
               "open": sess[0]["deal_price"], "close": sess[-1]["deal_price"],
               "high": max(t["deal_price"] for t in sess),
               "low": min(t["deal_price"] for t in sess),
               "vol_day": sum(t["volume"] for t in sess),
               "n_tick": len(sess)}
        acc: dict[str, float] = collections.defaultdict(float)
        bigm = [0.0] * NMIN
        smlm = [0.0] * NMIN
        volm = [0.0] * NMIN
        ci = 0
        for t, tt in zip(sess, ts):
            while ci < len(CUTS) and tt > CUTS[ci][0]:
                lab = CUTS[ci][1]
                for _, bl in BIG:
                    rec[f"{bl}_{lab}"] = acc[bl]
                rec[f"small_{lab}"] = acc["small"]
                rec[f"vol_{lab}"] = acc["vol"]
                rec[f"px_{lab}"] = acc["lastpx"]
                ci += 1
            if tt < OPEN_T + 10:
                continue
            amt = t["deal_price"] * t["volume"] * 1000
            # TickType==1 是買方主動、2是賣方主動（見 finmind-ticktype-sign-test 記憶檔，
            # 96%+ Lee-Ready 交叉驗證）；此行原本寫反，2026-09-28 由
            # quiet_accumulation_earnings_catalyst_test.py 重建時發現並修正。
            s = 1 if t["TickType"] == "1" else (-1 if t["TickType"] == "2" else 0)
            acc["vol"] += t["volume"]
            acc["lastpx"] = t["deal_price"]
            mi = min(NMIN - 1, int((tt - M0) // 60))
            volm[mi] += t["volume"]
            if not s:
                continue
            for thr, bl in BIG:
                if amt >= thr:
                    acc[bl] += s * t["volume"]
            if amt >= 5e6:
                bigm[mi] += s * t["volume"]
            if amt < SMALL:
                acc["small"] += s * t["volume"]
                smlm[mi] += s * t["volume"]
        while ci < len(CUTS):
            lab = CUTS[ci][1]
            for _, bl in BIG:
                rec[f"{bl}_{lab}"] = acc[bl]
            rec[f"small_{lab}"] = acc["small"]
            rec[f"vol_{lab}"] = acc["vol"]
            rec[f"px_{lab}"] = acc["lastpx"]
            ci += 1
        rec.update(interaction(bigm, smlm, volm))
        rec["_bigm"] = bigm
        rec["_smlm"] = smlm
        rows.append(rec)
    print(f"品質閘剔除: {dict(skipped)}")
    print(f"股日 {len(rows):,}  股票 {len({r['sid'] for r in rows})}  日 {len({r['date'] for r in rows})}")
    with OUT.open("wb") as fh:
        pickle.dump({"rows": rows, "universe": uni}, fh)
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
