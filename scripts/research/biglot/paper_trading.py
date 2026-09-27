"""biglot dashboard 重構：紙上交易(paper trading)帳本的載入/儲存/紀錄/彙總 helper。

跟 `biglot/xq_style.py` 同一個 stale-reference 理由：`PAPER`/`PAPER_PATH`/`PAPER_DAILY`/
`PAPER_BOOKS`/`ST`/`TZ` 都是 `biglot_dashboard.py` 自己定義的模組層級名字（`PAPER` 雖然
目前只被 `.clear()`/`.update()` 原地修改、沒有 `global PAPER; PAPER = ...` 整包重新賦值，
但 `ST` 這種 class 實例／`TZ` 這種模組常數都是同一份物件被許多函式共用），一律不能用
`from biglot_dashboard import X` 在檔案頂層抓快照——只 `import biglot_dashboard`，函式本體
內用 `biglot_dashboard.X` 屬性存取，確保任何時候讀到的都是 `biglot_dashboard` 模組當下最新的物件。

`DATA_DIR`來自 `stock_db`（不是 `biglot_dashboard.py` 自己定義的名字），直接從 `stock_db` import。

這批函式（`_paper_blank`/`_paper_log`/`_paper_save`/`_paper_summary`/`_paper_fills`）是純
load/save/log/彙總 bookkeeping，經依賴分析確認零呼叫其他頂層函式——不含 `_paper_update`/
`_paper_close`/`_paper_settle` 那幾支真正驅動掛單/成交/收盤結算狀態機的函式（那些還留在
`biglot_dashboard.py`，本次搬移範圍不含）。
"""
from __future__ import annotations

import json

from stock_db import DATA_DIR

import biglot_dashboard


def _paper_blank(day):
    return {"day": day, "seen": {b: [] for b in biglot_dashboard.PAPER_BOOKS}, "orders": {b: {} for b in biglot_dashboard.PAPER_BOOKS}, "pos": {b: {} for b in biglot_dashboard.PAPER_BOOKS},
            "closed": {b: [] for b in biglot_dashboard.PAPER_BOOKS}, "last_bkey": {}, "n_sig": {b: 0 for b in biglot_dashboard.PAPER_BOOKS}}


def _paper_save():
    try:
        biglot_dashboard.PAPER_PATH.parent.mkdir(parents=True, exist_ok=True); biglot_dashboard.PAPER_PATH.write_text(json.dumps(biglot_dashboard.PAPER, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _paper_log(rec):
    try:
        f = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"paper_trades_{biglot_dashboard.PAPER['day']}.jsonl"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%H:%M:%S"), **rec}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _paper_fills(sid, t_post, limit, side):
    """掃 t_post 之後的逐筆:買單=賣方主動成交 <limit(strict)/≤limit(opt);賣單對稱。回傳 (t_strict, t_opt)。"""
    ts_s = ts_o = None
    for ts, px, amt, sgn, _b, _r in biglot_dashboard.ST.recent.get(sid, ()):
        if ts <= t_post: continue
        if side == "buy" and sgn < 0:
            if px <= limit and ts_o is None: ts_o = ts
            if px < limit and ts_s is None: ts_s = ts
        elif side == "sell" and sgn > 0:
            if px >= limit and ts_o is None: ts_o = ts
            if px > limit and ts_s is None: ts_s = ts
        if ts_s is not None and ts_o is not None: break
    return ts_s, ts_o


def _paper_summary():
    """頁首一行:今日兩本帳 + 累計(paper_daily)。"""
    parts = []
    for book in biglot_dashboard.PAPER_BOOKS:
        cl = [c for c in biglot_dashboard.PAPER.get("closed", {}).get(book, []) if c.get("ev") == "close"]; un = [c for c in biglot_dashboard.PAPER.get("closed", {}).get(book, []) if c.get("ev") == "unfilled"]
        st = [c for c in cl if c["strict_entry"]]
        net = (sum(c["net_bps"] for c in cl) / len(cl)) if cl else None
        parts.append(f"{book}: 訊號 {biglot_dashboard.PAPER.get('n_sig', {}).get(book, 0)} 掛 {len(cl)+len(un)} 成交 {len(st)}嚴/{len(cl)}樂 持 {len(biglot_dashboard.PAPER.get('pos', {}).get(book, {}))}"
                     + (f" 淨均 {net:+.0f}bps" if net is not None else ""))
    try:
        daily = json.loads(biglot_dashboard.PAPER_DAILY.read_text(encoding="utf-8")) if biglot_dashboard.PAPER_DAILY.exists() else {}
        if daily:
            ds = sorted(daily); b = "bucket"; nets = [daily[d][b]["net_opt"] for d in ds if daily[d][b].get("net_opt") is not None]
            nf = sum(daily[d][b]["n_fill_opt"] for d in ds); ns = sum(daily[d][b]["n_fill_strict"] for d in ds)
            parts.append(f"累計 {len(ds)} 日 bucket 成交 {ns}嚴/{nf}樂" + (f" 日均淨 {sum(nets)/len(nets):+.0f}bps" if nets else ""))
    except Exception:  # noqa: BLE001
        pass
    return " · ".join(parts)
