#!/usr/bin/env python3
"""抓 TAIFEX 官方「股票期貨/股票選擇權交易標的」表 → 商品代號 → 契約規模對照。

為什麼需要：同一檔標的可能同時有標準型（2,000 股）與**小型（100 股）**兩種
契約，代號不同（大立光 3008：IJ=2,000 股、OL=100 股）。把全部當 2,000 股會
把小型契約的損益放大 20 倍——2026-10-02 診斷手動期貨單時就先踩了這個坑，
一筆大立光單被算成 −78 萬，實際是 −3.9 萬。
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")

from stock_db import DATA_DIR  # noqa: E402

URL = "https://www.taifex.com.tw/cht/2/stockLists"
OUT = DATA_DIR / "cache" / "taifex_stock_futures_specs.json"


def main() -> int:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8", errors="replace")
    specs: dict[str, dict] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) < 12:
            continue
        code = cells[0]
        if not re.fullmatch(r"[A-Z]{2,3}", code):
            continue
        shares = cells[-3].replace(",", "")
        specs[code] = {
            "stock_id": cells[2],
            "name": cells[3],
            "contract_shares": int(shares) if shares.isdigit() else None,
            "night_session": cells[-1],
        }
    if len(specs) < 200:
        print(f"abort: only parsed {len(specs)} rows — 版面可能改了", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(specs, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"wrote {OUT}  n={len(specs)}")
    print("契約規模分布:", dict(Counter(v["contract_shares"] for v in specs.values())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
