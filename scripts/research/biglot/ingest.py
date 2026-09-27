"""biglot dashboard 重構：`ingest()`——全系統唯一的 tick 攝取＋過日重載主迴圈，
從 scripts/research/biglot_dashboard.py 逐字搬移，邏輯不改一行。

**這是這整個重構風險最高的一支搬移**，原因：
1. 這正是 docs/biglot-refactor-roadmap.md 最開頭那個過日重載 bug 的所在地
   （HIST_BIG/Y_PMLOW/UNI5/ETF981_* 曾經漏刷新）。
2. 它一次會 `global X; X = ...` 整包重新賦值 **17 個**危險全域（`DAILY_TREND`/
   `KEY_LINE`/`PE_TABLE`/`PE_PEERS`/`PE_GEN`/`PE_EPS`/`ATR_STATE`/`XQ_STYLE`/
   `VIXTWN`/`HIST_BIG`/`Y_PMLOW`/`UNI5`/`ETF981_HOLD`/`ETF981_ASOF`/
   `ETF981_PREV_ASOF`/`PREOPEN`/`FUT_PX`/`WRT`），比第四批唯一的寫入案例
   `_refresh_vol_risk_if_needed`（只寫 2 個）高一個量級。
3. `loop()` 每 1~300 秒呼叫一次，是全站台唯一處理即時 tick 的路徑——這裡任何
   錯誤都會立刻反映在正式站台上，不是研究頁面的裝飾性 bug。

因此這裡的搬移遵循 biglot/pe_and_shadow.py／biglot/reference_loaders.py（尤其
`_refresh_vol_risk_if_needed`）建立的同一套規則，但**每一個** `global X; X = ...`
都改成 `biglot_dashboard.X = ...` 屬性賦值（不再宣告 `global`），逐一核對過
17 個名字都轉換到位，沒有漏掉任何一個。搬移後另外用
scripts/research/biglot_phase0/check_daily_rebind.py 的手法擴充驗證，確認全部
17 個全域搬移後仍然會在過日時正確整包換物件、不會 stale。

`ST`、`TZ`、`PREV_CLOSE`、`TX_SER`、`WRT_MIN` 是 biglot_dashboard.py 自己的模組
層級名稱（`ST`/`TZ` 從未整包重新賦值、`PREV_CLOSE`/`TX_SER`/`WRT_MIN` 只被
`.update()` 原地修改，不在 18 個危險全域清單裡），但為了跟危險全域的存取方式
保持一致、降低未來誤判風險，一律走 `biglot_dashboard.X` 屬性存取，不特例處理。
"""
from __future__ import annotations

import json

import biglot_dashboard
from biglot.reference_loaders import (
    _refresh_vol_risk_if_needed, _load_daily_trend, _load_key_line, _load_pe_peer,
    _load_atr_state, _load_xq_style, _load_vixtwn, _load_prev_close_db, _load_hist,
    _load_etf981_holdings,
)
from biglot.trade_ingest import _ingest_trade
from biglot.iceberg import _iceberg_update
from biglot.mini_futures import _ingest_mini_fut
from stock_db import DATA_DIR


def ingest():
    today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
    if biglot_dashboard.ST.date != today:
        biglot_dashboard.ST.__init__()
        biglot_dashboard.ST.date = today
        _refresh_vol_risk_if_needed()
        biglot_dashboard.DAILY_TREND = _load_daily_trend()
        biglot_dashboard.KEY_LINE = _load_key_line()
        (biglot_dashboard.PE_TABLE, biglot_dashboard.PE_PEERS,
         biglot_dashboard.PE_GEN, biglot_dashboard.PE_EPS) = _load_pe_peer()
        biglot_dashboard.ATR_STATE = _load_atr_state()
        biglot_dashboard.XQ_STYLE = _load_xq_style()
        biglot_dashboard.VIXTWN = _load_vixtwn()
        biglot_dashboard.PREV_CLOSE.update(_load_prev_close_db())   # 換日refresh官方昨收
        # 2026-09-27 補漏(稽核發現):_load_hist()/_load_etf981_holdings() 先前只在 import 當下
        # 跑過一次,process 若連續跑超過一天不重啟,HIST_BIG(連續買賣streak)/Y_PMLOW(破昨午後低點)/
        # UNI5(宇宙近5日累積,餵閘門banner+OOS盤中gating)/ETF981_*(981A同步觀察)都會停在啟動當天,
        # 從未跟著換日更新。PREV_CLOSE 故意不從這裡的回傳值覆蓋——上面那行 update(_load_prev_close_db())
        # 已經是正確的官方昨收來源,這裡只補三個真正缺漏的全域,不要引入第二個互相打架的 PREV_CLOSE 賦值。
        biglot_dashboard.HIST_BIG, _, biglot_dashboard.Y_PMLOW, biglot_dashboard.UNI5 = _load_hist()
        (biglot_dashboard.ETF981_HOLD, biglot_dashboard.ETF981_ASOF,
         biglot_dashboard.ETF981_PREV_ASOF) = _load_etf981_holdings()
    raw = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{today}.jsonl"
    if raw.exists():
        with open(raw) as f:
            f.seek(biglot_dashboard.ST.raw_off)
            while True:
                line = f.readline()
                if not line or not line.endswith("\n"):
                    break                     # 尾行未寫完,下輪再讀
                biglot_dashboard.ST.raw_off = f.tell()
                _ingest_trade(line)
    bookf = DATA_DIR / "cache" / "watchlist_books" / f"watchlist_books_{today}.jsonl"
    if bookf.exists():
        with open(bookf) as f:
            f.seek(biglot_dashboard.ST.book_off)
            while True:
                line = f.readline()
                if not line or not line.endswith("\n"):
                    break
                biglot_dashboard.ST.book_off = f.tell()
                try:
                    r = json.loads(line)
                    biglot_dashboard.ST.book[r["sym"]] = r
                    _iceberg_update(r)
                except Exception:
                    pass
    # 盤前試撮快照 + 個股期貨即時價(小檔,每輪重讀)
    _bd = DATA_DIR.parent / "cache" / "biglot_live_watch"
    try:
        pf = _bd / f"preopen_{today}.json"
        biglot_dashboard.PREOPEN = json.loads(pf.read_text()).get("trial", {}) if pf.exists() else {}
    except Exception:
        biglot_dashboard.PREOPEN = {}
    try:
        ff = _bd / f"futprice_{today}.json"
        biglot_dashboard.FUT_PX = json.loads(ff.read_text()) if ff.exists() else {}
    except Exception:
        biglot_dashboard.FUT_PX = {}
    try:
        wf = _bd / f"warrantflow_{today}.json"
        biglot_dashboard.WRT = json.loads(wf.read_text()) if wf.exists() else {}
    except Exception:
        biglot_dashboard.WRT = {}
    # 台指近月 10 秒樣本(collect_biglot_futprice 落地),增量讀,供頂部台指校準圖
    try:
        if biglot_dashboard.TX_SER["day"] != today:
            biglot_dashboard.TX_SER.update({"day": today, "t": [], "px": [], "off": 0})
        tf = _bd / f"txf_10s_{today}.jsonl"
        if tf.exists():
            with open(tf, "rb") as f:
                f.seek(biglot_dashboard.TX_SER["off"])
                chunk = f.read()
            nl = chunk.rfind(b"\n")
            if nl != -1:
                biglot_dashboard.TX_SER["off"] += nl + 1
                for line in chunk[:nl].split(b"\n"):
                    try:
                        o = json.loads(line)
                        ts = biglot_dashboard.datetime.fromisoformat(f"{today}T{o['t']}+08:00").timestamp()
                        if o.get("px") and (not biglot_dashboard.TX_SER["t"] or ts > biglot_dashboard.TX_SER["t"][-1]):
                            biglot_dashboard.TX_SER["t"].append(ts)
                            biglot_dashboard.TX_SER["px"].append(float(o["px"]))
                    except Exception:  # noqa: BLE001
                        continue
    except Exception:  # noqa: BLE001
        pass
    # 權證逐筆(collect_warrant_ws 落地)增量聚合成每分鐘簽號淨額:購 +dirn、售 −dirn
    try:
        if biglot_dashboard.WRT_MIN["day"] != today:
            biglot_dashboard.WRT_MIN.update({"day": today, "off": 0, "data": {}})
        wtf = _bd.parent / "warrant_trades_ws" / f"warrant_trades_{today}.jsonl"
        if wtf.exists():
            with open(wtf, "rb") as f:
                f.seek(biglot_dashboard.WRT_MIN["off"])
                chunk = f.read()
            nl = chunk.rfind(b"\n")
            if nl != -1:
                biglot_dashboard.WRT_MIN["off"] += nl + 1
                for line in chunk[:nl].split(b"\n"):
                    try:
                        o = json.loads(line)
                        sgn = (o.get("dirn") or 0) * (1 if o.get("side") == "購" else -1)
                        if not sgn:
                            continue
                        hm = o["ts"][11:16]
                        dd = biglot_dashboard.WRT_MIN["data"].setdefault(str(o["sid"]), {})
                        dd[hm] = dd.get(hm, 0.0) + sgn * float(o["price"]) * float(o["size"]) * 1000
                    except Exception:  # noqa: BLE001
                        continue
    except Exception:  # noqa: BLE001
        pass
    _ingest_mini_fut(today)                        # 期散:小型契約 1 口成交(FUT_MINI 檔)
