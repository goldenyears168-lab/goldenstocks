#!/usr/bin/env python3
"""把儀表板存檔 HTML（timeline_*.json）解析成「時點 × 標的 × 43 欄」的面板。

為什麼不重算：這些欄位是 render_main.py 用 20+ 個參考來源算出來的，重算
一定有口徑漂移（權證/期貨五檔/隱形大戶/集保/00981A 更是完全無法從逐筆回推）。
存檔 HTML 的 `data-sort` 屬性直接帶精確數值，是零誤差的真實畫面。

限制：timeline 功能 2026-09-29 當天才建立，所以**只有 09-30 起有存檔**，
且每 30 分一格（09:00–13:30 共 10 格）。9/29 的委託要靠逐筆重放補，另標信心。
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")

TIMELINE = Path.home() / "goldenstocks-data/cache/biglot_live_watch/timeline"
# 表頭有 6 欄的 label 被 title 屬性裡的 '>' 截斷，依 title 內容人工補正
COL_FIX = {11: "5分散買參與%", 25: "均價乖離/大戶倒貨", 28: "關鍵一條線",
           34: "相對強弱", 40: "標記", 41: "淨分"}


def _text(inner: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", inner)).strip()


def parse_headers(frag: str) -> list[str]:
    th_block = re.search(r"<thead.*?</thead>", frag, re.S).group(0)
    cols = []
    for i, (_, inner) in enumerate(re.findall(r"<th([^>]*)>(.*?)</th>", th_block, re.S)):
        lab = _text(inner).replace("\n", " ")[:24] or f"col{i}"
        cols.append(COL_FIX.get(i, lab))
    return cols


def parse_slot(frag: str, cols: list[str]) -> pd.DataFrame:
    body = re.search(r"<tbody.*?</tbody>", frag, re.S)
    if not body:
        return pd.DataFrame()
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(0), re.S):
        tds = re.findall(r"<td([^>]*)>(.*?)</td>", tr, re.S)
        if len(tds) < 20:
            continue
        rec = {}
        for i, (attr, inner) in enumerate(tds):
            name = cols[i] if i < len(cols) else f"col{i}"
            txt = _text(inner).replace("\n", " ")
            ds = re.search(r"data-sort=['\"]([^'\"]*)['\"]", attr)
            rec[f"{name}#txt"] = txt
            val = None
            if ds:
                try:
                    val = float(ds.group(1))
                except ValueError:
                    val = None
            rec[f"{name}#num"] = val
            cls = re.search(r"class=['\"]([^'\"]*)['\"]", attr)
            rec[f"{name}#cls"] = cls.group(1) if cls else ""
        first = rec.get(f"{cols[0]}#txt", "")
        m = re.match(r"(\d{4})", first)
        rec["sid"] = m.group(1) if m else None
        rec["row_order"] = len(out)          # 畫面上的列序（＝當下排序後的位置）
        out.append(rec)
    return pd.DataFrame(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="+", default=["2026-09-30", "2026-10-01", "2026-10-02"])
    ap.add_argument("--out", default=str(Path.home() / "goldenstocks-data/data/scratch/html_snapshot_panel.parquet"))
    args = ap.parse_args()
    frames, cols = [], None
    for day in args.days:
        p = TIMELINE / f"timeline_{day}.json"
        if not p.exists():
            print(f"  {day}: 無存檔 — 跳過")
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        slots = [k for k in d if re.fullmatch(r"\d\d:\d\d", k)]
        for slot in sorted(slots):
            frag = d[slot]
            if not isinstance(frag, str):
                frag = frag.get("frag", "")
            if "<tbody" not in frag:
                continue
            if cols is None:
                cols = parse_headers(frag)
                print(f"欄位 {len(cols)} 個：{cols}\n")
            df = parse_slot(frag, cols)
            df["day"], df["slot"] = day, slot
            frames.append(df)
        print(f"  {day}: {len(slots)} 個時點")
    panel = pd.concat(frames, ignore_index=True)
    panel.to_parquet(args.out)
    print(f"\nwrote {args.out}")
    print(f"面板 {len(panel)} 列（時點×標的）· {panel.sid.nunique()} 檔 · "
          f"{panel.groupby(['day','slot']).ngroups} 個時點 · {len(cols)} 欄 × 3 種表示")
    num = [c for c in panel.columns if c.endswith("#num")]
    cov = panel[num].notna().mean().sort_values()
    print("\n有數值(data-sort)的欄位覆蓋率最低的 12 欄：")
    print(cov.head(12).round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
