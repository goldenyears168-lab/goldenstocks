#!/usr/bin/env python3
"""抓期交所官方「股票期貨/選擇權標的證券」對照表 → config/taifex_stock_futures_map.json。

取代先前用「收盤價序列比對」猜期貨↔現貨對映的做法（會在一檔標的有新舊兩個契約時出錯，
例：台光電 2383 同時有 PJF(舊,日量~100口) 與 SFF(新,日量~2000口)）。
欄位 root 是 2 碼契約代碼，FinMind 的 futures_id = root + "F"。
"""
from __future__ import annotations
import json, re, sys, urllib.request
from pathlib import Path

URL = "https://www.taifex.com.tw/cht/2/stockLists"
# .gitignore 有 *.csv（資料檔一律不進版控），所以走 json —— config/ 底下 json 是版控的
OUT = Path(__file__).resolve().parents[2] / "config/taifex_stock_futures_map.json"


def main() -> int:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (goldenstocks research)"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        td = [re.sub(r"<[^>]+>", "", c).replace("\r", " ").replace("\n", " ").strip()
              for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        td = [re.sub(r"\s+", " ", c) for c in td]
        if len(td) < 5 or not re.fullmatch(r"[A-Z0-9]{2,3}", td[0]):
            continue
        rows.append(dict(root=td[0], futures_id=td[0] + "F", stock_id=td[2], name=td[3],
                         company=td[1],
                         is_future="是股票期貨標的" in td[4],
                         market="上櫃" if any("上櫃" in c for c in td[7:9]) else "上市",
                         contract_size=td[11] if len(td) > 11 else "",
                         hours=td[12] if len(td) > 12 else ""))
    if not rows:
        print("解析失敗（頁面結構可能改了）", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": URL, "n": len(rows), "rows": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    fut = [r for r in rows if r["is_future"]]
    dup = {}
    for r in fut:
        dup.setdefault(r["stock_id"], []).append(r["futures_id"])
    multi = {k: v for k, v in dup.items() if len(v) > 1}
    print(f"寫出 {OUT}：{len(rows)} 列，其中股票期貨標的 {len(fut)} 檔、"
          f"{len(dup)} 個 stock_id")
    print(f"一檔標的有多個契約的 {len(multi)} 檔：{multi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
