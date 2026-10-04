#!/usr/bin/env python3
"""族群整體強度欄位 —— 把「族群本身強不強」跟「個股在族群內排第幾」分開。

為什麼需要（2026-10-05）：jack 的故事裡有兩層 ——「族群也選對」＋「個股在族群內落後」。
但儀表板**只有第二層**（個股在族群內的排名），沒有第一層（族群自己的強弱）。
兩層分不開,就沒辦法區分這兩個故事：

  故事一「個股吸籌」：大戶在這一檔回檔時默默接,跟族群無關
  故事二「族群資金外溢」：族群先強,資金找族群內還沒動的 —— **族群強是前提**

族群定義用**股價共動 A/B 級夥伴**（comovement_broad.json）,不用 SUBCAT ——
SUBCAT 把 42 檔切成 31 群、其中 24 群只有 1 檔,而且把真正連動的拆開
（旺宏/華邦電/南亞科/晶豪科/力積電 互為 A/B 夥伴卻被分成 5 群）。

新增欄位（全部以「夥伴群中位」定義,含自己）：
  grp_chg      族群當日漲幅中位%
  grp_chg_ex   族群超額 = 族群漲幅中位 − 當分鐘全市場漲幅中位  ← 族群強不強
  grp_big_day  族群全日大戶佔比中位
  grp_big_30m  族群30分大戶佔比中位
  grp_bl_30m   族群30分大減散中位
  self_vs_grp  個股漲幅 − 族群漲幅中位                        ← 個股在族群內落後多少
  grp_n        夥伴數（含自己）

這支是 **就地加欄**（讀 panel → 算 → 寫回同一個 parquet）,因為族群聚合完全可以
從既有欄位推導,不需要重跑逐筆回放（那是最貴的一步）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

P = Path.home() / "goldenstocks-data/data"
COMOVE = Path.home() / "goldenstocks-data/cache/biglot_live_watch/comovement_broad.json"


def peer_map(universe: set[str]) -> dict[str, list[str]]:
    S = json.loads(COMOVE.read_text(encoding="utf-8"))["stocks"]
    return {s: [s] + [q["sid"] for q in (S.get(s, {}).get("partners") or [])
                      if q.get("tier") in ("A", "B") and q["sid"] in universe]
            for s in universe}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(P / "scratch/trader_feature_panel.parquet"))
    args = ap.parse_args()
    d = pd.read_parquet(args.panel)
    PEER = peer_map(set(d.sid.unique()))
    print(f"面板 {len(d):,} 列 · 夥伴群 ≥2 檔的標的 "
          f"{sum(1 for v in PEER.values() if len(v) >= 2)}/{len(PEER)}", file=sys.stderr)

    SRC = {"grp_chg": "當日漲幅%", "grp_big_day": "big_day",
           "grp_big_30m": "big_30m", "grp_bl_30m": "bigless_30m"}
    out = {k: [] for k in (*SRC, "grp_chg_ex", "self_vs_grp", "grp_n")}
    keys = []
    for (day, hm), g in d.groupby(["day", "hm"], sort=False):
        gi = g.set_index("sid")
        mkt = pd.to_numeric(gi["當日漲幅%"], errors="coerce").median()
        for sid in g.sid:
            ps = [x for x in PEER.get(sid, []) if x in gi.index]
            keys.append((day, hm, sid))
            if len(ps) < 2:
                for k in out:
                    out[k].append(len(ps) if k == "grp_n" else None)
                continue
            vals = {}
            for col, src in SRC.items():
                vals[col] = pd.to_numeric(gi.loc[ps, src], errors="coerce").median()
                out[col].append(vals[col])
            out["grp_chg_ex"].append(vals["grp_chg"] - mkt if pd.notna(mkt) else None)
            s_chg = pd.to_numeric(gi.loc[sid, "當日漲幅%"], errors="coerce")
            out["self_vs_grp"].append(s_chg - vals["grp_chg"] if pd.notna(vals["grp_chg"]) else None)
            out["grp_n"].append(len(ps))
    add = pd.DataFrame(out)
    add[["day", "hm", "sid"]] = pd.DataFrame(keys, columns=["day", "hm", "sid"])
    before = set(d.columns)
    d = d.merge(add, on=["day", "hm", "sid"], how="left", suffixes=("", "_dup"))
    d = d[[c for c in d.columns if not c.endswith("_dup")]]
    d.to_parquet(args.panel, index=False)
    new = [c for c in d.columns if c not in before]
    print(f"新增 {len(new)} 欄: {new}", file=sys.stderr)
    print(f"族群超額 可算比率 {d.grp_chg_ex.notna().mean():.1%} · "
          f"中位 {d.grp_chg_ex.median():+.3f}%", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
