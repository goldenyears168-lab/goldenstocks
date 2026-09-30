"""biglot dashboard 重構：紙上交易(paper trading)帳本的載入/儲存/紀錄/彙總 helper。

跟 `biglot/xq_style.py` 同一個 stale-reference 理由：`PAPER`/`PAPER_PATH`/`PAPER_DAILY`/
`PAPER_BOOKS`/`ST`/`TZ` 都是 `biglot_dashboard.py` 自己定義的模組層級名字（`PAPER` 雖然
目前只被 `.clear()`/`.update()` 原地修改、沒有 `global PAPER; PAPER = ...` 整包重新賦值，
但 `ST` 這種 class 實例／`TZ` 這種模組常數都是同一份物件被許多函式共用），一律不能用
`from biglot_dashboard import X` 在檔案頂層抓快照——只 `import biglot_dashboard`，函式本體
內用 `biglot_dashboard.X` 屬性存取，確保任何時候讀到的都是 `biglot_dashboard` 模組當下最新的物件。

`DATA_DIR`來自 `stock_db`（不是 `biglot_dashboard.py` 自己定義的名字），直接從 `stock_db` import。

這批函式（`_paper_blank`/`_paper_log`/`_paper_save`/`_paper_summary`/`_paper_fills`）是純
load/save/log/彙總 bookkeeping，經依賴分析確認零呼叫其他頂層函式。`_paper_close`（收單結算，
呼叫本檔案內的 `_paper_log`）也已搬進本檔案。`_paper_update`（掛單/成交狀態機，逐檔驅動）與
`_paper_settle`（收盤強制平倉＋寫日統計）兩支真正驅動狀態機的函式後續一併搬進本檔案——兩者都呼叫
本檔案內的 `_paper_blank`/`_paper_close`/`_paper_fills`/`_paper_log`/`_paper_save`（bare name，同檔
不需 import/prefix），並讀取 `biglot_dashboard.py` 的 `HOLD_BAD`/`PAPER`/`PAPER_BOOKS`/`PAPER_DAILY`/
`PAPER_SKIP`/`PAPER_TH`/`PAPER_K`/`PAPER_BUY_WAIT`/`PAPER_SELL_WAIT`/`PAPER_MAX_HOLD`/`ST`/`TZ`/
`datetime`——一律 `biglot_dashboard.X` 屬性存取於呼叫當下，不在檔案頂層 `from biglot_dashboard import X`。
"""
from __future__ import annotations

import json
import time

from stock_db import DATA_DIR

import biglot_dashboard


def _paper_blank(day):
    return {"day": day, "orders": {b: {} for b in biglot_dashboard.PAPER_BOOKS}, "pos": {b: {} for b in biglot_dashboard.PAPER_BOOKS},
            "closed": {b: [] for b in biglot_dashboard.PAPER_BOOKS}, "last_bkey": {}, "n_sig": {b: 0 for b in biglot_dashboard.PAPER_BOOKS},
            "cool": {b: {} for b in biglot_dashboard.PAPER_BOOKS}, "nent": {b: {} for b in biglot_dashboard.PAPER_BOOKS}}


def _paper_ensure():
    """舊 paper_state.json 缺 cool/nent 時補上(同日重啟不重置帳本)。"""
    P = biglot_dashboard.PAPER
    for k in ("cool", "nent"):
        P.setdefault(k, {})
        for b in biglot_dashboard.PAPER_BOOKS:
            P[k].setdefault(b, {})


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


def _paper_close(book, sid, pos, exit_px, how, now):
    g = (exit_px / pos["entry"] - 1) * 1e4; net = g - biglot_dashboard.PAPER_COST
    rec = {"ev": "close", "book": book, "sid": sid, "entry": pos["entry"], "exit": exit_px, "how": how, "reason": (pos.get("sell") or {}).get("reason"),
           "gross_bps": g, "net_bps": net, "ntd_net": net / 1e4 * pos["entry"] * 2000, "hold_min": (now - pos["t_fill"]) / 60,
           "strict_entry": pos["strict"], "strict_exit": how in ("買一", "收盤") or bool(pos.get("sell_strict")), "sig": pos["sig"]}
    biglot_dashboard.PAPER["closed"][book].append(rec); _paper_log(rec); biglot_dashboard.PAPER["pos"][book].pop(sid, None)
    nent = biglot_dashboard.PAPER.get("nent", {}).get(book)
    if nent is not None:
        nent[sid] = nent.get(sid, 0) + 1
    cool = biglot_dashboard.PAPER.get("cool", {}).get(book)
    if cool is not None:
        cool[sid] = now + biglot_dashboard.PAPER_COOL["closed"]   # 平倉後冷卻,避免立刻回頭追同一檔


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


def _paper_update(rows, now):
    day = biglot_dashboard.ST.date
    if biglot_dashboard.PAPER.get("day") != day:
        biglot_dashboard.PAPER.clear(); biglot_dashboard.PAPER.update(_paper_blank(day)); _paper_save()
    _paper_ensure()
    hm = biglot_dashboard.datetime.fromtimestamp(now, biglot_dashboard.TZ).strftime("%H:%M:%S")
    if hm < "09:30:00" or hm > "13:25:00":
        return
    bkey = hm[:4] + str(int(hm[4]) // 5 * 5)                      # 5 分桶鍵(HH:M0/M5)
    at_boundary = hm[3:5] in ("00", "05", "10", "15", "20", "25", "30", "35", "40", "45", "50", "55") and hm[6:8] <= "03"
    changed = False
    for r in rows:
        sid = r["sid"]; sc = r.get("sc_v2"); px = r.get("px")
        tags = [t for t, _ in (r.get("cause") or [])]; items = [k for k, _ in (r.get("sc_v2_items") or [])]
        bid = biglot_dashboard.ST.last_bid.get(sid); ask = biglot_dashboard.ST.last_ask.get(sid)
        bk = biglot_dashboard.ST.book.get(sid) or {}
        if bid is None and bk.get("bp"): bid = bk["bp"][0]
        if ask is None and bk.get("ap"): ask = bk["ap"][0]
        for book in biglot_dashboard.PAPER_BOOKS:
            cool = biglot_dashboard.PAPER["cool"][book]; nent = biglot_dashboard.PAPER["nent"][book]
            # --- 訊號 → 掛買一(冷卻制:在途/持倉中不重掛;冷卻未到則靜默跳過,不寫 log 也不計 n_sig) ---
            if (sc is not None and sc >= biglot_dashboard.PAPER_TH and hm <= "13:20:00"
                    and sid not in biglot_dashboard.PAPER["orders"][book] and sid not in biglot_dashboard.PAPER["pos"][book]
                    and now >= cool.get(sid, 0.0)):
                fire = False
                if book == "bucket":
                    if at_boundary and biglot_dashboard.PAPER["last_bkey"].get(sid) != bkey:
                        biglot_dashboard.PAPER["last_bkey"][sid] = bkey; fire = True
                else:
                    fire = True
                if fire:
                    biglot_dashboard.PAPER["n_sig"][book] += 1; changed = True
                    skip = next((t for t in tags if t.startswith(biglot_dashboard.PAPER_SKIP)), None)
                    busy = len(biglot_dashboard.PAPER["orders"][book]) + len(biglot_dashboard.PAPER["pos"][book])
                    why = cd = None
                    if biglot_dashboard.PAPER_MAX_ENTRY and nent.get(sid, 0) >= biglot_dashboard.PAPER_MAX_ENTRY:
                        why, cd = "當日額度用盡", 86400.0
                    elif skip:
                        why, cd = skip, biglot_dashboard.PAPER_COOL["skip"]
                    elif busy >= biglot_dashboard.PAPER_K:
                        why, cd = "容量", biglot_dashboard.PAPER_COOL["cap"]
                    elif bid is None or not px:
                        why, cd = "無買一", biglot_dashboard.PAPER_COOL["nobid"]
                    if why:
                        cool[sid] = now + cd
                        _paper_log({"ev": "signal_skip", "book": book, "sid": sid, "score": sc, "why": why, "cool_s": cd, "n_ent": nent.get(sid, 0), "tags": tags})
                    else:
                        biglot_dashboard.PAPER["orders"][book][sid] = {"limit": bid, "t_post": now, "sig": {"hm": hm, "score": sc, "px": px, "bid": bid, "ask": ask, "tags": tags, "items": items}}
                        _paper_log({"ev": "signal", "book": book, "sid": sid, "score": sc, "px": px, "bid": bid, "ask": ask, "tags": tags, "items": items, "n_ent": nent.get(sid, 0)})
            # --- 買單管理 ---
            o = biglot_dashboard.PAPER["orders"][book].get(sid)
            if o:
                ts_s, ts_o = _paper_fills(sid, o["t_post"], o["limit"], "buy")
                if ts_o is not None:
                    biglot_dashboard.PAPER["pos"][book][sid] = {"entry": o["limit"], "t_fill": ts_o, "strict": ts_s is not None, "low_since": None, "sell": None, "sig": o["sig"]}
                    biglot_dashboard.PAPER["orders"][book].pop(sid); changed = True
                    _paper_log({"ev": "fill", "book": book, "sid": sid, "px": o["limit"], "strict": ts_s is not None, "wait_s": ts_o - o["t_post"]})
                elif now - o["t_post"] > biglot_dashboard.PAPER_BUY_WAIT:
                    biglot_dashboard.PAPER["orders"][book].pop(sid); changed = True
                    cool[sid] = now + biglot_dashboard.PAPER_COOL["unfilled"]   # 排不到隊不算訊號失效,冷卻後可重試
                    biglot_dashboard.PAPER["closed"][book].append({"ev": "unfilled", "book": book, "sid": sid, "limit": o["limit"], "px_now": px, "sig": o["sig"]})
                    _paper_log({"ev": "unfilled", "book": book, "sid": sid, "limit": o["limit"], "px_now": px, "run_bps": ((px / o["limit"] - 1) * 1e4) if px else None,
                                "cool_s": biglot_dashboard.PAPER_COOL["unfilled"]})
            # --- 持倉管理 ---
            pos = biglot_dashboard.PAPER["pos"][book].get(sid)
            if not pos: continue
            if pos.get("sell") is None:
                if sc is not None and sc <= biglot_dashboard.PAPER_EXIT_TH:
                    if pos.get("low_since") is None: pos["low_since"] = now
                else:
                    pos["low_since"] = None
                reason = None
                if pos.get("low_since") is not None and now - pos["low_since"] >= 30:
                    reason = f"分數≤{biglot_dashboard.PAPER_EXIT_TH:g}·30秒"
                elif any(k.startswith(biglot_dashboard.HOLD_BAD) for k in items): reason = "壞標籤"
                elif now - pos["t_fill"] >= biglot_dashboard.PAPER_MAX_HOLD: reason = "到期60分"
                elif hm >= "13:20:00": reason = "收盤前"
                if reason and ask:
                    pos["sell"] = {"limit": ask, "t_post": now, "reason": reason}; changed = True
                    _paper_log({"ev": "sell_post", "book": book, "sid": sid, "limit": ask, "reason": reason, "score": sc, "hold_min": (now - pos["t_fill"]) / 60})
            else:
                so = pos["sell"]; ts_s, ts_o = _paper_fills(sid, so["t_post"], so["limit"], "sell")
                if ts_o is not None:
                    pos["sell_strict"] = ts_s is not None; _paper_close(book, sid, pos, so["limit"], "賣一", now); changed = True
                elif now - so["t_post"] > biglot_dashboard.PAPER_SELL_WAIT or hm >= "13:24:00":
                    _paper_close(book, sid, pos, bid or px or so["limit"], "買一", now); changed = True
            if sid in biglot_dashboard.PAPER["pos"][book]:
                pos = biglot_dashboard.PAPER["pos"][book][sid]
                r.setdefault("paper", {})[book] = {"entry": pos["entry"], "min": (now - pos["t_fill"]) / 60, "pnl": ((px / pos["entry"] - 1) * 1e4) if px else None,
                                                    "sell": bool(pos.get("sell")), "strict": pos["strict"]}
    if changed: _paper_save()


def _paper_settle(day):
    """收盤:強制平掉殘餘部位、寫當日統計到 paper_daily.json(冪等)。"""
    if biglot_dashboard.PAPER.get("day") != day: return
    now = time.time()
    for book in biglot_dashboard.PAPER_BOOKS:
        for sid, pos in list(biglot_dashboard.PAPER["pos"][book].items()):
            px = biglot_dashboard.ST.last_px.get(sid) or pos["entry"]; pos["sell"] = pos.get("sell") or {"reason": "收盤強制"}; _paper_close(book, sid, pos, px, "收盤", now)
        biglot_dashboard.PAPER["orders"][book].clear()
    try:
        daily = json.loads(biglot_dashboard.PAPER_DAILY.read_text(encoding="utf-8")) if biglot_dashboard.PAPER_DAILY.exists() else {}
    except Exception:  # noqa: BLE001
        daily = {}
    out = {}
    for book in biglot_dashboard.PAPER_BOOKS:
        cl = [c for c in biglot_dashboard.PAPER["closed"][book] if c.get("ev") == "close"]; un = [c for c in biglot_dashboard.PAPER["closed"][book] if c.get("ev") == "unfilled"]
        st = [c for c in cl if c["strict_entry"]]
        def _m(xs, k): return (sum(x[k] for x in xs) / len(xs)) if xs else None
        out[book] = {"n_sig": biglot_dashboard.PAPER["n_sig"][book], "n_post": len(cl) + len(un), "n_fill_opt": len(cl), "n_fill_strict": len(st),
                     "gross_opt": _m(cl, "gross_bps"), "net_opt": _m(cl, "net_bps"), "net_strict": _m(st, "net_bps"),
                     "hit_opt": (sum(1 for c in cl if c["net_bps"] > 0) / len(cl)) if cl else None, "ntd_opt": sum(c["ntd_net"] for c in cl), "ntd_strict": sum(c["ntd_net"] for c in st),
                     "reasons": {str(k): sum(1 for c in cl if c.get("reason") == k) for k in set(c.get("reason") for c in cl)}}
    daily[day] = out; biglot_dashboard.PAPER_DAILY.parent.mkdir(parents=True, exist_ok=True); biglot_dashboard.PAPER_DAILY.write_text(json.dumps(daily, ensure_ascii=False, indent=1), encoding="utf-8")
    _paper_log({"ev": "settle", **{b: {k: v for k, v in out[b].items() if k != "reasons"} for b in biglot_dashboard.PAPER_BOOKS}}); _paper_save()
