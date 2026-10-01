#!/usr/bin/env python3
"""抓取處置有價證券名單（TWSE 上市 + TPEx 上櫃），供 limit-up fade 選股使用。

處置期間該股改為人工分盤撮合（5 或 20 分鐘一盤）並暫停現股當日沖銷，
是 limit-up fade 規則的必要排除條件 —— 實測該子集放空報酬 -1.67%（反指標）。

資料來源與限制：
- TWSE `announcement/punish`：可指定日期區間回溯查詢；連續請求過快會被擋
  （HTTP 307 →「因為安全性考量」），故預設請求間隔 1.5 秒。
- TPEx `bulletin/disposal`：**只提供當日快照**，沒有歷史區間查詢，
  因此上櫃名單只能靠每日累積。第一次執行只會有今天一天。
- TWSE `announcement/notice` / TPEx `bulletin/attention`：**注意**有價證券名單。
  處置的觸發條件是「注意次數累積」（連續 3/5 日、10 日內 6 日、30 日內 12 日），
  所以要提前一天知道誰會被關，關鍵資料是注意清單而不是處置清單。
  TWSE 可回溯查詢區間；TPEx 同樣只有當日快照。

輸出（都在 ${GOLDENSTOCKS_DATA_DIR}/data/disposal/，不寫生產 SQLite）：
- twse_punish_raw.json     TWSE 原始處置公告列（累積、依公告去重）
- tpex_snapshots/<date>.json  TPEx 每日處置快照原檔
- disposal_windows.csv     正規化後的處置區間 (market,stock_id,start,end,measure)
- twse_notice_raw.json     TWSE 原始注意公告列（累積、依 代號+日期 去重）
- tpex_notice_snapshots/<date>.json  TPEx 每日注意快照原檔
- notice_history.csv       正規化後的注意紀錄 (market,stock_id,date,cum,clauses,reason)

用法：
    # 每日例行：抓當月 TWSE + 今日 TPEx 快照
    PYTHONPATH=src .venv/bin/python scripts/research/fetch_disposal_list.py

    # 回補 TWSE 歷史（TPEx 無法回補）
    PYTHONPATH=src .venv/bin/python scripts/research/fetch_disposal_list.py \
        --twse-start 2026-01 --twse-end 2026-08 --skip-tpex
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from stock_db.util import DATA_DIR

TWSE_URL = "https://www.twse.com.tw/rwd/zh/announcement/punish"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal"
TWSE_NOTICE_URL = "https://www.twse.com.tw/rwd/zh/announcement/notice"
TPEX_NOTICE_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/attention"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

OUT_DIR = DATA_DIR / "disposal"
TWSE_RAW = OUT_DIR / "twse_punish_raw.json"
TPEX_DIR = OUT_DIR / "tpex_snapshots"
WINDOWS_CSV = OUT_DIR / "disposal_windows.csv"
NOTICE_RAW = OUT_DIR / "twse_notice_raw.json"
TPEX_NOTICE_DIR = OUT_DIR / "tpex_notice_snapshots"
NOTICE_CSV = OUT_DIR / "notice_history.csv"

# 上櫃「不再處置」的判定靠每日快照消失，故快照本身要保留原檔以便重建。
_STOCK_RE = re.compile(r"^\d{4}$")
_ROC_RE = re.compile(r"(\d{2,3})/(\d{1,2})/(\d{1,2})")


def _ctx() -> ssl.SSLContext:
    # 櫃買的憑證鏈過不了 Python 3.13 預設的 VERIFY_X509_STRICT（見 memory: tpex-ssl-x509-strict）。
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def _get_json(url: str, *, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp:
        return json.load(resp)


def _roc_to_date(text: str) -> dt.date | None:
    m = _ROC_RE.search(text or "")
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return dt.date(y + 1911, mo, d)
    except ValueError:
        return None


def fetch_twse(start: str, end: str, *, sleep: float) -> list[list]:
    """抓 TWSE 處置公告；start/end 格式 YYYY-MM（含頭含尾）。"""
    y0, m0 = (int(x) for x in start.split("-"))
    y1, m1 = (int(x) for x in end.split("-"))
    rows: list[list] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        last = calendar.monthrange(y, m)[1]
        url = f"{TWSE_URL}?response=json&startDate={y}{m:02d}01&endDate={y}{m:02d}{last}"
        try:
            payload = _get_json(url)
            got = payload.get("data") or []
            rows.extend(got)
            print(f"  TWSE {y}-{m:02d}: {len(got)} 筆", file=sys.stderr)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            # 307 = 被擋（請求太密）。不中斷，讓呼叫端知道哪幾個月缺。
            print(f"  TWSE {y}-{m:02d}: 失敗 {exc}", file=sys.stderr)
        m += 1
        if m > 12:
            m, y = 1, y + 1
        time.sleep(sleep)
    return rows


def fetch_tpex_snapshot() -> tuple[str, list[list]]:
    """抓 TPEx 當日處置快照，回傳 (快照日期, 資料列)。"""
    payload = _get_json(TPEX_URL)
    table = (payload.get("tables") or [{}])[0]
    snap = _roc_to_date(table.get("title2", "")) or dt.date.today()
    return snap.isoformat(), table.get("data") or []


def _merge_twse_raw(new_rows: list[list]) -> list[list]:
    old = json.loads(TWSE_RAW.read_text()) if TWSE_RAW.exists() else []
    seen, merged = set(), []
    for row in old + new_rows:
        # (公布日期, 證券代號, 處置起迄) 足以唯一識別一則公告
        key = (str(row[1]), str(row[2]), str(row[6]))
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def _twse_windows(rows: list[list]) -> list[dict]:
    out = []
    for row in rows:
        code = str(row[2]).strip()
        if not _STOCK_RE.match(code):  # 排除權證／ETN（6 碼）
            continue
        parts = re.split(r"[～~]", str(row[6]))
        if len(parts) != 2:
            continue
        start, end = _roc_to_date(parts[0]), _roc_to_date(parts[1])
        if not start or not end:
            continue
        out.append({"market": "TWSE", "stock_id": code, "start": start.isoformat(),
                    "end": end.isoformat(), "measure": str(row[7]).strip()})
    return out


def _tpex_windows() -> list[dict]:
    """由累積的 TPEx 每日快照重建處置區間。"""
    out = []
    for path in sorted(TPEX_DIR.glob("*.json")):
        for row in json.loads(path.read_text()):
            code = str(row[2]).strip()
            if not _STOCK_RE.match(code):
                continue
            parts = re.split(r"[～~]", str(row[5]))
            if len(parts) != 2:
                continue
            start, end = _roc_to_date(parts[0]), _roc_to_date(parts[1])
            if not start or not end:
                continue
            out.append({"market": "TPEX", "stock_id": code, "start": start.isoformat(),
                        "end": end.isoformat(), "measure": str(row[6]).strip()})
    return out


# ── 注意有價證券 ───────────────────────────────────────────────────────────
# 款別文字兩個市場寫法不同：TWSE「﹝第一款﹞」、TPEx「(第一款)」。
_CLAUSE_RE = re.compile(r"[﹝(（]第([一二三四五六七八九十]+)款[﹞)）]")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13}
# 注意清單的日期兩種寫法：TWSE「115.09.22」、TPEx「115/09/30」。
_ROC_DOT_RE = re.compile(r"(\d{2,3})[./](\d{1,2})[./](\d{1,2})")


def _roc_flexible(text: str) -> dt.date | None:
    m = _ROC_DOT_RE.search(str(text or ""))
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return dt.date(y + 1911, mo, d)
    except ValueError:
        return None


def _parse_clauses(reason: str) -> str:
    """把注意原因文字裡的款別抽成排序後的字串，例如 "1|4|6"。"""
    nums = {_CN_NUM.get(g) for g in _CLAUSE_RE.findall(str(reason or ""))}
    return "|".join(str(n) for n in sorted(x for x in nums if x))


def fetch_twse_notice(start: str, end: str, *, sleep: float) -> list[list]:
    """抓 TWSE 注意公告；start/end 格式 YYYY-MM（含頭含尾）。"""
    y0, m0 = (int(x) for x in start.split("-"))
    y1, m1 = (int(x) for x in end.split("-"))
    rows: list[list] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        last = calendar.monthrange(y, m)[1]
        url = f"{TWSE_NOTICE_URL}?response=json&startDate={y}{m:02d}01&endDate={y}{m:02d}{last}"
        try:
            got = _get_json(url).get("data") or []
            rows.extend(got)
            print(f"  TWSE 注意 {y}-{m:02d}: {len(got)} 筆", file=sys.stderr)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            print(f"  TWSE 注意 {y}-{m:02d}: 失敗 {exc}", file=sys.stderr)
        m += 1
        if m > 12:
            m, y = 1, y + 1
        time.sleep(sleep)
    return rows


def fetch_tpex_notice_snapshot() -> tuple[str, list[list]]:
    """抓 TPEx 當日注意快照，回傳 (快照日期, 資料列)。"""
    table = (_get_json(TPEX_NOTICE_URL).get("tables") or [{}])[0]
    # title2 例：「公布注意期間為 115/09/29 至 115/09/30」──取迄日當快照日
    dates = _ROC_DOT_RE.findall(table.get("title2", ""))
    snap = dt.date.today()
    if dates:
        y, mo, d = (int(x) for x in dates[-1])
        try:
            snap = dt.date(y + 1911, mo, d)
        except ValueError:
            pass
    return snap.isoformat(), table.get("data") or []


def _merge_notice_raw(new_rows: list[list]) -> list[list]:
    old = json.loads(NOTICE_RAW.read_text()) if NOTICE_RAW.exists() else []
    seen, merged = set(), []
    for row in old + new_rows:
        # 同一檔同一天可能有多列（不同款），連原因一起當 key 才不會誤刪
        key = (str(row[1]), str(row[5]) if len(row) > 5 else "", str(row[4])[:60])
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def _notice_records() -> list[dict]:
    """把 TWSE 累積檔 + TPEx 快照正規化成一列一筆 (market, stock_id, date, cum, clauses, reason)。"""
    out: list[dict] = []
    for row in (json.loads(NOTICE_RAW.read_text()) if NOTICE_RAW.exists() else []):
        if len(row) < 6:
            continue
        code = str(row[1]).strip()
        if not _STOCK_RE.match(code):        # 排除權證／ETN
            continue
        day = _roc_flexible(row[5])
        if not day:
            continue
        reason = str(row[4]).replace("\n", " ")
        out.append({"market": "TWSE", "stock_id": code, "name": str(row[2]).strip(), "date": day.isoformat(),
                    "cum": str(row[3]).strip(), "clauses": _parse_clauses(reason), "reason": reason})
    for path in sorted(TPEX_NOTICE_DIR.glob("*.json")):
        for row in json.loads(path.read_text()):
            if len(row) < 6:
                continue
            code = str(row[1]).strip()
            if not _STOCK_RE.match(code):
                continue
            day = _roc_flexible(row[5])
            if not day:
                continue
            reason = str(row[4]).replace("<br>", " ")
            out.append({"market": "TPEX", "stock_id": code, "name": str(row[2]).strip(), "date": day.isoformat(),
                        "cum": str(row[3]).strip(), "clauses": _parse_clauses(reason), "reason": reason})
    seen, dedup = set(), []
    for r in out:
        key = (r["market"], r["stock_id"], r["date"], r["clauses"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    dedup.sort(key=lambda r: (r["date"], r["stock_id"]))
    return dedup


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    today = dt.date.today()
    ap.add_argument("--twse-start", default=today.strftime("%Y-%m"), help="YYYY-MM，預設當月")
    ap.add_argument("--twse-end", default=today.strftime("%Y-%m"), help="YYYY-MM，預設當月")
    ap.add_argument("--skip-twse", action="store_true")
    ap.add_argument("--skip-tpex", action="store_true")
    ap.add_argument("--skip-notice", action="store_true", help="跳過注意有價證券名單")
    ap.add_argument("--sleep", type=float, default=1.5, help="TWSE 請求間隔秒數（太快會被擋）")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TPEX_DIR.mkdir(parents=True, exist_ok=True)
    TPEX_NOTICE_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_twse:
        print(f"抓 TWSE {args.twse_start} ~ {args.twse_end}", file=sys.stderr)
        merged = _merge_twse_raw(fetch_twse(args.twse_start, args.twse_end, sleep=args.sleep))
        TWSE_RAW.write_text(json.dumps(merged, ensure_ascii=False))
        print(f"TWSE 原始公告累計 {len(merged)} 則 → {TWSE_RAW}", file=sys.stderr)

    if not args.skip_tpex:
        try:
            snap_date, rows = fetch_tpex_snapshot()
            (TPEX_DIR / f"{snap_date}.json").write_text(json.dumps(rows, ensure_ascii=False))
            print(f"TPEx 快照 {snap_date}: {len(rows)} 檔", file=sys.stderr)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError) as exc:
            print(f"TPEx 快照失敗（不影響上市名單）: {exc}", file=sys.stderr)

    if not args.skip_notice:
        print(f"抓注意名單 {args.twse_start} ~ {args.twse_end}", file=sys.stderr)
        merged_n = _merge_notice_raw(fetch_twse_notice(args.twse_start, args.twse_end, sleep=args.sleep))
        NOTICE_RAW.write_text(json.dumps(merged_n, ensure_ascii=False))
        print(f"TWSE 注意公告累計 {len(merged_n)} 列 → {NOTICE_RAW}", file=sys.stderr)
        try:
            snap_date, rows = fetch_tpex_notice_snapshot()
            (TPEX_NOTICE_DIR / f"{snap_date}.json").write_text(json.dumps(rows, ensure_ascii=False))
            print(f"TPEx 注意快照 {snap_date}: {len(rows)} 檔", file=sys.stderr)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError) as exc:
            print(f"TPEx 注意快照失敗（不影響上市名單）: {exc}", file=sys.stderr)
        recs = _notice_records()
        with NOTICE_CSV.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["market", "stock_id", "name", "date", "cum", "clauses", "reason"])
            w.writeheader()
            w.writerows(recs)
        print(f"注意紀錄 {len(recs)} 列 → {NOTICE_CSV}", file=sys.stderr)

    twse_rows = json.loads(TWSE_RAW.read_text()) if TWSE_RAW.exists() else []
    windows = _twse_windows(twse_rows) + _tpex_windows()
    seen, dedup = set(), []
    for w in windows:
        key = (w["market"], w["stock_id"], w["start"], w["end"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(w)
    dedup.sort(key=lambda w: (w["start"], w["stock_id"]))
    with WINDOWS_CSV.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["market", "stock_id", "start", "end", "measure"])
        writer.writeheader()
        writer.writerows(dedup)

    n_twse = sum(1 for w in dedup if w["market"] == "TWSE")
    print(f"處置區間 {len(dedup)} 筆（TWSE {n_twse} / TPEX {len(dedup) - n_twse}）→ {WINDOWS_CSV}")
    if dedup:
        print(f"涵蓋 {dedup[0]['start']} ~ {max(w['end'] for w in dedup)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
