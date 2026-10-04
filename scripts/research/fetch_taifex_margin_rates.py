#!/usr/bin/env python3
"""抓台灣期交所「股票期貨保證金一覽表」，快取成 JSON。

為什麼需要：整份手動交易研究原本假設原始保證金率固定 13.5%，是錯的。
期交所按風險價格係數分級公告，實測 322 檔分布在
  級距1 13.5% ／ 級距2 16.2% ／ 級距3 20.25% ／ 另有 21.6 / 22.95 / 24.3 / 30.38 / 40.5%
使用者交易的 22 檔裡有 20 檔高於 13.5%，平均低估 1.43 倍（創意 30.38% = 2.25 倍）。
保證金率錯 1.4 倍，三分法水位、價格帶口數上限、147 萬風控上限的檢定全都會錯。

輸出：`${GOLDENSTOCKS_DATA_DIR}/data/cache/taifex_stock_futures_margin_rates.json`
  {商品英文代碼: {sid, name, tier, 結算%, 維持%, 原始%}}
查詢時用 `sid`（證券代號）接 taifex_stock_futures_specs.json 的 underlying_id，
**不要**用商品代碼接——保證金表用 3 碼商品代碼（DFF），契約規格表用 2 碼根（LY）。

櫃買/期交所的憑證鏈在 Python 預設 VERIFY_X509_STRICT 下會被擋，所以關掉該旗標。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.request
from pathlib import Path

URL = "https://www.taifex.com.tw/cht/5/stockMargining"


def fetch(url: str = URL, timeout: int = 30) -> str:
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:  # noqa: S310
        return r.read().decode("utf-8", "replace")


def parse(html: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        t = [re.sub(r"<[^>]+>", "", x).replace("\xa0", " ").strip()
             for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        # 欄序：序號, 商品代碼, 證券代號, 商品名稱, 標的公司, 級距, 結算%, 維持%, 原始%
        if len(t) < 9 or not re.fullmatch(r"[A-Z]{2,3}", t[1] or "") or "%" not in t[8]:
            continue
        try:
            out[t[1]] = {"sid": t[2], "name": t[3], "tier": t[5],
                         "結算%": float(t[6].rstrip("%")),
                         "維持%": float(t[7].rstrip("%")),
                         "原始%": float(t[8].rstrip("%"))}
        except ValueError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    root = os.environ.get("GOLDENSTOCKS_DATA_DIR")
    base = Path(root) if root else Path(__file__).resolve().parents[2]
    ap.add_argument("--out", default=str(base / "data/cache/taifex_stock_futures_margin_rates.json"))
    args = ap.parse_args()

    tbl = parse(fetch())
    if len(tbl) < 200:
        print(f"只解析到 {len(tbl)} 檔，遠低於預期（>300）—— 版面可能改了，不覆寫快取",
              file=sys.stderr)
        return 1
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tbl, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    dist = Counter(v["原始%"] for v in tbl.values())
    print(f"{len(tbl)} 檔 → {p}")
    print("原始保證金率分布:", dict(sorted(dist.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
