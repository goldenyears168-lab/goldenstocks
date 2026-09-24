#!/usr/bin/env python3
"""抓 MOPS 重大訊息(TWSE + TPEx OpenAPI 快照)→ ${DATA_DIR}/cache/mops_today_{發言日期}.json,供儀表板成因標籤。

來源(2026-09-24 實測可用、免登入、JSON):
  上市 https://openapi.twse.com.tw/v1/opendata/t187ap04_L
  上櫃 https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O
⚠ 兩者都是**每日快照**:出表日期=今天、發言日期=前一營業日(含盤後),不是盤中即時。
  盤中當日訊息仍要看 mops.twse.com.tw(新版 API 參數未探明);儀表板對「今日」無檔會標 MOPS?。
輸出格式:{sid: [[hh:mm, 主旨], ...]},鍵=發言日期(ISO)。同日多筆依時間排序。

用法:PYTHONPATH=src .venv/bin/python scripts/research/fetch_mops_today.py
"""
from __future__ import annotations
import json, sys, urllib.request
from collections import defaultdict
from stock_db import DATA_DIR

SRC = [("https://openapi.twse.com.tw/v1/opendata/t187ap04_L", "公司代號", "主旨 "),
       ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O", "SecuritiesCompanyCode", "主旨")]


def roc_iso(s: str) -> str:
    s = s.strip(); return f"{int(s[:-4]) + 1911}-{s[-4:-2]}-{s[-2:]}"


def hhmm(s: str) -> str:
    s = s.strip().zfill(6); return f"{s[:2]}:{s[2:4]}"


def main() -> int:
    by_day: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    n = 0
    for url, kcode, ksubj in SRC:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"WARN {url}: {e}", file=sys.stderr); continue
        for r in rows:
            try:
                day = roc_iso(r["發言日期"]); sid = str(r[kcode]).strip()
                subj = (r.get(ksubj) or r.get("主旨") or "").strip()
                by_day[day][sid].append([hhmm(r["發言時間"]), subj]); n += 1
            except Exception:  # noqa: BLE001
                continue
    out_dir = DATA_DIR / "cache"; out_dir.mkdir(parents=True, exist_ok=True)
    for day, m in by_day.items():
        for v in m.values():
            v.sort()
        p = out_dir / f"mops_today_{day}.json"
        p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        print(f"{p} sids={len(m)} rows={sum(len(v) for v in m.values())}")
    print(f"total rows={n}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
