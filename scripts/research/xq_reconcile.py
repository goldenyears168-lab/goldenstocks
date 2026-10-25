#!/usr/bin/env python3
"""XQ 選股結果匯出檔 × 我們 DB 對帳（唯讀）。

  PYTHONPATH=src .venv/bin/python scripts/research/xq_reconcile.py FILE --asof 2026-10-22 [--watch 5245,8086]

FILE＝XQ 選股中心「匯出整份股票清單成 excel」的檔（csv／xlsx／html 偽 xls 皆可；真 xls 請另存 csv）。
預期欄名（outputfield 名稱）：主力買賣超、主力集中度%、地緣券商買賣超、關鍵券商買賣超、關聯券商買賣超、
外資買賣超、投信買賣超、融資使用率%、融資維持率%、融券餘額、借券賣出餘額、投信持股比例%。
做的事：①逐欄與 DB 同日數字比對（外資、投信、融券餘額、借券賣出餘額、主力）②列出地緣／關鍵／關聯券商買賣超非 0 的標的
③列出融資使用率≥60%、投信持股比例<1.5% 的標的。不寫 DB、不寫檔，只印報告。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "research"))

from stock_db import DEFAULT_DB_PATH  # noqa: E402

COLS = {  # key -> 欄名關鍵字（取第一個含關鍵字的欄；順序重要，先比長的）
    "conc": "主力集中度", "main": "主力買賣超", "local": "地緣", "key": "關鍵券商", "rel": "關聯券商",
    "foreign": "外資買賣超", "trust": "投信買賣超", "mu": "融資使用率", "mm": "融資維持率",
    "short": "融券餘額", "sbl": "借券賣出餘額", "trust_pct": "投信持股比例",
}
ID_RE = re.compile(r"^\s*(\d{4,6}[A-Z]?)\b")


def num(x) -> float | None:
    s = str(x).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "--", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_table(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    if raw[:2] == b"PK":  # xlsx
        with zipfile.ZipFile(path) as z:
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            sst = []
            if "xl/sharedStrings.xml" in z.namelist():
                for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                    sst.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
            sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
            rows = []
            for r in ET.fromstring(z.read(sheet)).iter("{%s}row" % ns["m"]):
                cells = {}
                for c in r.findall("m:c", ns):
                    col = re.match(r"[A-Z]+", c.get("r")).group(0)
                    idx = 0
                    for ch in col:
                        idx = idx * 26 + ord(ch) - 64
                    v = c.find("m:v", ns)
                    val = "" if v is None else v.text or ""
                    if c.get("t") == "s" and val:
                        val = sst[int(val)]
                    cells[idx - 1] = val
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
            return rows
    for enc in ("utf-8-sig", "big5", "cp950"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit("無法解碼檔案；請另存為 csv（UTF-8）")
    if "<table" in text.lower():  # html 偽 xls
        from bs4 import BeautifulSoup
        return [[c.get_text(strip=True) for c in tr.find_all(["td", "th"])] for tr in BeautifulSoup(text, "html.parser").find_all("tr")]
    if raw[:4] == b"\xd0\xcf\x11\xe0":
        raise SystemExit("這是舊版 xls（二進位）；請在 Excel 另存 csv 或 xlsx")
    import csv
    import io
    sep = "\t" if text.count("\t") > text.count(",") else ","
    return list(csv.reader(io.StringIO(text), delimiter=sep))


def parse(rows: list[list[str]]) -> tuple[dict[str, dict], dict[str, str]]:
    hi = next(i for i, r in enumerate(rows) if sum(any(k in c for k in COLS.values()) for c in r) >= 3)
    head = rows[hi]
    colidx: dict[str, int] = {}
    for key, kw in COLS.items():  # conc 先於 main，避免「主力集中度」被「主力買賣超」抓走（兩者關鍵字不同，但保險）
        for i, c in enumerate(head):
            if kw in c and i not in colidx.values():
                colidx[key] = i
                break
    id_col = next((i for i in range(len(head)) if sum(bool(ID_RE.match(r[i])) for r in rows[hi + 1:] if len(r) > i) > 0.5 * max(1, len(rows) - hi - 1)), None)
    if id_col is None:
        raise SystemExit("找不到代碼欄（需要像 2330 或 2330 台積電 的欄）")
    out = {}
    names = {}
    for r in rows[hi + 1:]:
        if len(r) <= id_col or not ID_RE.match(r[id_col]):
            continue
        sid = ID_RE.match(r[id_col]).group(1)
        out[sid] = {k: num(r[i]) if i < len(r) else None for k, i in colidx.items()}
        names[sid] = r[id_col]
    return out, {k: head[i] for k, i in colidx.items()}


def db_side(conn: sqlite3.Connection, sids: list[str], asof: str) -> dict[str, dict]:
    q = ",".join("?" * len(sids))
    out: dict[str, dict] = {s: {} for s in sids}
    for sid, f, t in conn.execute(f"SELECT stock_id,foreign_net,investment_trust_net FROM stock_institutional_daily WHERE trade_date=? AND stock_id IN ({q})", [asof, *sids]):
        out[sid]["foreign"] = None if f is None else f / 1000
        out[sid]["trust"] = None if t is None else t / 1000
    for sid, sb in conn.execute(f"SELECT stock_id,short_balance FROM stock_margin_daily WHERE trade_date=? AND stock_id IN ({q})", [asof, *sids]):
        out[sid]["short"] = sb
    for sid, sb in conn.execute(f"SELECT stock_id,sbl_balance FROM stock_short_interest_daily WHERE trade_date=? AND stock_id IN ({q})", [asof, *sids]):
        out[sid]["sbl"] = None if sb is None else sb / 1000
    have = [r[0] for r in conn.execute(f"SELECT DISTINCT stock_id FROM stock_broker_branch_daily WHERE trade_date=? AND stock_id IN ({q})", [asof, *sids])]
    if have:
        import chip_sop as cs
        for r in cs.compute_rows(conn, [asof], [(s, s) for s in have])[asof]:
            d = (r.get("day") or {})
            if r.get("status") == "ok" and d.get("main_lots") is not None:
                out[r["sid"]]["main"] = d["main_lots"]
                out[r["sid"]]["conc"] = d.get("conc_pct")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--asof", required=True)
    ap.add_argument("--watch", default="5245,8086", help="逗號分隔，特別列出地緣／關鍵券商買賣超")
    ap.add_argument("--tol", type=float, default=1.0, help="張數容差")
    a = ap.parse_args()
    xq, colnames = parse(read_table(Path(a.file)))
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True, timeout=20)
    db = db_side(conn, list(xq), a.asof)
    L = [f"# XQ × DB 對帳 {a.asof}\n", f"XQ 檔 {len(xq)} 檔；辨識到的欄：{colnames}\n", "## 一、逐欄對帳（容差 ±%g 張；主力集中度容差 ±0.2pp）\n" % a.tol,
         "| 欄位 | 可比檔數 | 一致 | 一致率 | 最大差 |\n|---|---|---|---|---|"]
    detail = []
    for key, label in (("foreign", "外資買賣超"), ("trust", "投信買賣超"), ("short", "融券餘額"), ("sbl", "借券賣出餘額"), ("main", "主力買賣超"), ("conc", "主力集中度%")):
        tol = 0.2 if key == "conc" else a.tol
        pairs = [(s, xq[s].get(key), db[s].get(key)) for s in xq if xq[s].get(key) is not None and db[s].get(key) is not None]
        ok = [p for p in pairs if abs(p[1] - p[2]) <= tol]
        mx = max((abs(p[1] - p[2]) for p in pairs), default=0)
        L.append(f"| {label} | {len(pairs)} | {len(ok)} | {(len(ok) / len(pairs) * 100 if pairs else 0):.1f}% | {mx:,.1f} |")
        bad = sorted((p for p in pairs if abs(p[1] - p[2]) > tol), key=lambda p: -abs(p[1] - p[2]))[:8]
        for s, x, d in bad:
            detail.append(f"- {label} {s}：XQ {x:,.1f}／DB {d:,.1f}（差 {x - d:+,.1f}）")
    L.append("\n### 不一致明細（每欄最大 8 筆）\n" + ("\n".join(detail) if detail else "無"))
    L.append("\n## 二、地緣／關鍵／關聯券商買賣超（非 0，依絕對值前 25）\n")
    L.append("| 代碼 | 地緣 | 關鍵 | 關聯 | 主力買賣超 |\n|---|---|---|---|---|")
    rows = [(s, v) for s, v in xq.items() if any(v.get(k) for k in ("local", "key", "rel"))]
    rows.sort(key=lambda sv: -max(abs(sv[1].get(k) or 0) for k in ("local", "key", "rel")))
    fmt = lambda x: "NA" if x is None else f"{x:,.0f}"  # noqa: E731
    for s, v in rows[:25]:
        L.append(f"| {s} | {fmt(v.get('local'))} | {fmt(v.get('key'))} | {fmt(v.get('rel'))} | {fmt(v.get('main'))} |")
    L.append(f"\n（非 0 共 {len(rows)} 檔）\n\n### 指定標的\n")
    for s in [x.strip() for x in a.watch.split(",") if x.strip()]:
        v = xq.get(s)
        L.append(f"- {s}：" + ("不在檔內" if v is None else f"地緣 {fmt(v.get('local'))}／關鍵 {fmt(v.get('key'))}／關聯 {fmt(v.get('rel'))}／主力 {fmt(v.get('main'))}（集中度 {v.get('conc')}%）"))
    L.append("\n## 三、融資使用率與投信持股比例\n")
    hi = sorted(((s, v["mu"]) for s, v in xq.items() if v.get("mu") is not None and v["mu"] >= 60), key=lambda x: -x[1])
    L.append(f"- 融資使用率 ≥60%：{len(hi)} 檔；前 15：" + "、".join(f"{s}({u:.0f}%)" for s, u in hi[:15]))
    lo = [s for s, v in xq.items() if v.get("trust_pct") is not None and 0 < v["trust_pct"] < 1.5]
    L.append(f"- 投信持股比例 <1.5%（>0）：{len(lo)} 檔")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
