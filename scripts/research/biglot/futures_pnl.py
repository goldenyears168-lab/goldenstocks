"""富邦期貨未平倉損益消長圖(2026-09-29 jack 交辦):在台指期面板左邊畫出當日相關的
個股期貨(依你的要求「只看期貨就好」)損益曲線——每檔一條線 + 合計一條粗白線。

資料源分兩條,刻意分開:
1. **倉位結構**(哪些股票、方向、口數、進場/出場價)——查富邦 `close_position_record`
   (取 90 天,涵蓋任何跨日留倉)+ `get_futopt_order_results`(只覆蓋今天,補精確到秒的
   下單時間)。⚠ 2026-09-29 稍早才發生過打富邦 API 頻率過高、疑似觸發限流的事故(暫停
   tmf-channel-poll 才解除),所以這裡刻意把查詢頻率壓到 **15 分鐘一次**(jack 明確定案:
   倉位本身不常變動,常變的是金額,金額不需要再問富邦)。查詢失敗時沿用上一次快取,不讓
   整張圖因為一次網路問題就消失。
2. **價格重建**(當日損益怎麼走)——不再問富邦,改讀本地已經在收的期貨逐筆封存
   (`collect_ccf_books_websocket.py` 寫的 `cache/{root}_trades/{root}_trades_{日}.jsonl`,
   `collect_stock_futures_books.py` 08:47 起、13:45 自退)。這條路完全不消耗富邦額度。

⚠ 2026-09-29 jack 糾正(第二輪):原本只用 `query_single_position` 抓「目前留倉」,
今天平倉掉的股票(如合晶,9/24進場、今天賣掉)就完全消失,總額對不上富邦 App 的「今日
損益」。改成對 `close_position_record` 的逐筆成交做 **FIFO 配對**(同函式邏輯已在
`tmf_channel/blotter.py::pair_fills_fifo` 驗證過,這裡針對個股期貨(非TMF)重寫一份精簡版,
不直接依賴 tmf_channel 模組——那支是給自動下單引擎用的,夜盤跨日等假設不一定適用個股期貨,
自己寫一份邏輯簡單、範圍明確的版本比較安全):
- 配對後**未平倉**的口數:比照原本的「留倉」處理,用即時 tick 價格算損益,持續變動。
- 配對後**已平倉**的口數(今天出場的那些):用「出場前用即時價、出場後鎖定在出場損益」
  的方式畫線——出場那一刻之後價格再怎麼動,已經跟這筆交易無關了,這才是跟 App 一致的
  「今日已實現損益」概念。
- 進場在今天以前的口數(隔夜留倉),`entry_ts` 查不到就當作「從圖表起點就已經在場」,
  今天開盤時損益就不是 0,而是用隔夜進場價對今天開盤價算出來的既有損益——這是使用者
  明確要求的行為(合晶案例:進場價121、9/24進場,今天開盤時應該已經是負的)。

依賴規則同 biglot/tx_panel.py:`ST`/`datetime`/`TZ` 一律 `import biglot_dashboard`
屬性存取,不在頂層具名匯入。
"""
from __future__ import annotations

import bisect as _bs
import json
import sys
import time

import biglot_dashboard
from stock_db import DATA_DIR

_FILLS_CACHE: dict = {"t": 0.0, "fills": [], "entry_ts_map": {}}
_FILLS_TTL = 900.0  # 15 分鐘,jack 2026-09-29 定案
_LOOKBACK_DAYS = 90  # 抓多久以前的成交,涵蓋任何可能的跨日留倉

_ROOT_PATH_CACHE: dict = {}
_STOCK_BY_FUTCODE_CACHE: dict | None = None

PNL_COLORS = ["#e3b341", "#79c0ff", "#d2a8ff", "#3fb950", "#ff7b72", "#f0883e", "#a5d6ff", "#7ee787"]


def _stock_by_futcode():
    global _STOCK_BY_FUTCODE_CACHE
    if _STOCK_BY_FUTCODE_CACHE is None:
        _STOCK_BY_FUTCODE_CACHE = {
            r["fut_code"]: (r["sid"], r["name"], r.get("contract_size") or 2000)
            for r in biglot_dashboard._cal["universe"] if r.get("fut_code")
        }
    return _STOCK_BY_FUTCODE_CACHE


def _load_fubon_fills():
    """安全唯讀查詢富邦成交紀錄,15分鐘快取。失敗回傳上次快取(不讓圖因暫時性連線
    問題消失)。只呼叫既有、已核可的唯讀函式(connect_fubon/close_position_record/
    get_futopt_order_results),不新開下單路徑、不呼叫 dir()/vars()。

    回傳 {"fills": [{"fut_code","sign","lots","px","date","order_no"}...] 依date排序,
          "entry_ts_map": {order_no: epoch_ts} 只覆蓋今天的單(get_futopt_order_results範圍)}。
    """
    now = time.time()
    if now - _FILLS_CACHE["t"] < _FILLS_TTL and _FILLS_CACHE["fills"]:
        return _FILLS_CACHE["fills"], _FILLS_CACHE["entry_ts_map"]
    try:
        from datetime import date, timedelta

        from order.fubon_futopt_orders import (
            _bs_to_side,
            _result_data,
            base_order_no,
            get_futopt_order_results,
            pick_futopt_account,
        )
        from order.fubon_session import connect_fubon

        session = connect_fubon(realtime=False)
        acc = pick_futopt_account(session)
        fa = session.sdk.futopt_accounting
        end = date.today().strftime("%Y/%m/%d")
        start = (date.today() - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y/%m/%d")
        res = fa.close_position_record(acc, start, end)
        raw_rows = list(_result_data(res) or [])
        fills = []
        for idx, r in enumerate(raw_rows):
            sym = str(getattr(r, "symbol", "") or "")
            if not sym.startswith("FI"):
                continue
            fut_code = sym[2:]
            side = _bs_to_side(getattr(r, "buy_sell", None))
            try:
                lots = int(getattr(r, "orig_lots", None) or 0)
                px = float(getattr(r, "price", None))
            except (TypeError, ValueError):
                continue
            if side is None or lots <= 0 or px is None:
                continue
            d = str(getattr(r, "date", "") or "").replace("/", "-")[:10]
            on = base_order_no(str(getattr(r, "order_no", "") or ""))
            fills.append({"fut_code": fut_code, "sign": (1 if side == "L" else -1),
                          "lots": lots, "px": px, "date": d, "order_no": on, "seq": idx})
        # 精確下單時間:close_position_record 只給 date,get_futopt_order_results 只覆蓋今天
        # 但精確到秒——今天的成交用這個補時間,今天以前的成交沒有精確時間也沒關係(見檔頭說明)。
        entry_ts_map: dict[str, float] = {}
        try:
            order_rows = get_futopt_order_results(session, acc=acc)
            for orow in order_rows:
                on = base_order_no(str(getattr(orow, "order_no", "") or ""))
                if not on or on in entry_ts_map:
                    continue
                d, t = getattr(orow, "date", None), getattr(orow, "last_time", None)
                if not d or not t:
                    continue
                try:
                    iso = f"{d.replace('/', '-')}T{t[:8]}+08:00"
                    entry_ts_map[on] = biglot_dashboard.datetime.fromisoformat(iso).timestamp()
                except Exception:  # noqa: BLE001
                    continue
        except Exception as e:  # noqa: BLE001
            print(f"[futures_pnl] order_results lookup failed: {e}", file=sys.stderr)
        if fills:
            _FILLS_CACHE["fills"] = fills
            _FILLS_CACHE["entry_ts_map"] = entry_ts_map
            _FILLS_CACHE["t"] = now
    except Exception as e:  # noqa: BLE001
        print(f"[futures_pnl] fills query failed, using stale cache: {e}", file=sys.stderr)
    return _FILLS_CACHE["fills"], _FILLS_CACHE["entry_ts_map"]


def _fifo_match(fills):
    """單一fut_code的成交序列(依date,同日內依seq=原始清單順序排序)做FIFO配對。
    回傳 (closed, open_legs):
      closed = [{"sign","lots","entry_px","entry_order_no","exit_px","exit_order_no"}]
      open_legs = [{"sign","lots","entry_px","entry_order_no"}]
    不含contract_size/entry_ts,那些在呼叫端補。"""
    ordered = sorted(fills, key=lambda f: (f["date"], f["seq"]))
    queue: list[dict] = []
    closed = []
    for f in ordered:
        remain = f["lots"]
        while remain > 0 and queue and queue[0]["sign"] != f["sign"]:
            o = queue[0]
            take = min(remain, o["lots"])
            closed.append({"sign": o["sign"], "lots": take, "entry_px": o["entry_px"],
                           "entry_order_no": o["order_no"], "exit_px": f["px"], "exit_order_no": f["order_no"]})
            o["lots"] -= take
            remain -= take
            if o["lots"] <= 0:
                queue.pop(0)
        if remain > 0:
            queue.append({"sign": f["sign"], "lots": remain, "entry_px": f["px"], "order_no": f["order_no"]})
    open_legs = [{"sign": o["sign"], "lots": o["lots"], "entry_px": o["entry_px"],
                  "entry_order_no": o["order_no"]} for o in queue]
    return closed, open_legs


def _positions_by_symbol():
    """依fut_code分組,FIFO配對後統一成同一種leg格式:
    (sign, lots, entry_px, entry_ts_or_None, exit_ts_or_None, exit_px_or_None)。
    exit_ts/exit_px 皆為 None 代表這口還沒平倉(用即時tick價算,持續變動);
    有值代表今天平倉了(出場前用即時價、出場後鎖定,見檔頭說明)。
    只保留「今天還有事(留倉中,或今天平倉)」的fut_code,不然每天都要重算90天前的舊倉位。"""
    fills, entry_ts_map = _load_fubon_fills()
    today = biglot_dashboard.ST.date
    by_fc: dict[str, list] = {}
    for f in fills:
        by_fc.setdefault(f["fut_code"], []).append(f)
    fc_map = _stock_by_futcode()
    out = []
    for fc, fc_fills in by_fc.items():
        sid, name, csize = fc_map.get(fc, (None, None, None))
        if sid is None:
            continue
        closed, open_legs = _fifo_match(fc_fills)
        on_to_date = {f["order_no"]: f["date"] for f in fc_fills}
        closed_today = [c for c in closed if on_to_date.get(c["exit_order_no"]) == today]
        if not open_legs and not closed_today:
            continue  # 今天沒事的舊倉位,略過
        legs = []
        for o in open_legs:
            legs.append((o["sign"], o["lots"], o["entry_px"], entry_ts_map.get(o["entry_order_no"]), None, None))
        for c in closed_today:
            legs.append((c["sign"], c["lots"], c["entry_px"], entry_ts_map.get(c["entry_order_no"]),
                        entry_ts_map.get(c["exit_order_no"]), c["exit_px"]))
        g = {"fut_code": fc, "sid": sid, "name": name, "contract_size": csize, "legs": legs}
        entry_ts_list = [leg[3] for leg in legs if leg[3] is not None]
        g["first_entry_ts"] = min(entry_ts_list) if entry_ts_list else None
        out.append(g)
    out.sort(key=lambda g: g["sid"])
    return out


def _resolve_root_path(fut_code: str, day: str):
    """{root}_trades_{day}.jsonl 目錄名多數是 fut_code 去掉尾碼(OLF->ol),但至少一檔
    (LUF->luf)例外用整個 fut_code——兩種都探,查到就快取,避免每次重掃檔案系統。"""
    key = (fut_code, day)
    if key in _ROOT_PATH_CACHE:
        return _ROOT_PATH_CACHE[key]
    base = DATA_DIR.parent / "cache"
    for cand in (fut_code[:-1].lower(), fut_code.lower()):
        p = base / f"{cand}_trades" / f"{cand}_trades_{day}.jsonl"
        if p.exists():
            _ROOT_PATH_CACHE[key] = p
            return p
    _ROOT_PATH_CACHE[key] = None
    return None


def _load_trade_series(fut_code: str, day: str):
    """回傳 [(epoch_ts, price), ...] 由早到晚。查無回 []。"""
    p = _resolve_root_path(fut_code, day)
    if not p:
        return []
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    px, ts = d.get("price"), d.get("ts")
                    if px is None or not ts:
                        continue
                    out.append((biglot_dashboard.datetime.fromisoformat(ts).timestamp(), float(px)))
                except Exception:  # noqa: BLE001
                    continue
    except Exception as e:  # noqa: BLE001
        print(f"[futures_pnl] read {p} failed: {e}", file=sys.stderr)
    return out


def _leg_pnl(leg, csize, gt, live_px):
    """單一口在時刻gt的損益。leg=(sign,lots,entry_px,entry_ts,exit_ts,exit_px)。
    還沒進場回None;已平倉且gt在出場之後,用出場價鎖定(不再跟著即時價動);
    否則用live_px(當下tick價,查不到也回None)。"""
    sign, lots, entry_px, entry_ts, exit_ts, exit_px = leg
    if entry_ts is not None and entry_ts > gt:
        return None
    if exit_ts is not None and gt >= exit_ts:
        px = exit_px
    else:
        if live_px is None:
            return None
        px = live_px
    return sign * lots * csize * (px - entry_px)


def _pnl_series(group, grid_ts):
    """回傳跟 grid_ts 等長的損益陣列(NTD)。"""
    ticks = _load_trade_series(group["fut_code"], biglot_dashboard.ST.date)
    times = [t for t, _ in ticks]
    prices = [p for _, p in ticks]
    out = []
    for gt in grid_ts:
        i = _bs.bisect_right(times, gt) - 1
        live_px = prices[i] if i >= 0 else None
        vals = [_leg_pnl(leg, group["contract_size"], gt, live_px) for leg in group["legs"]]
        vals = [v for v in vals if v is not None]
        out.append(sum(vals) if vals else None)
    return out


def _current_pnl(group, asof_ts):
    """該檔「現在」損益(NTD),用**最新一筆實際成交**算,不是從圖表的取樣網格反推
    (2026-09-29 jack 抓到環球晶算錯:網格點之間可能卡在兩筆真實成交中間拿到舊價,
    詳見 commit 說明)。"""
    ticks = _load_trade_series(group["fut_code"], biglot_dashboard.ST.date)
    times = [t for t, _ in ticks]
    i = _bs.bisect_right(times, asof_ts) - 1
    live_px = ticks[i][1] if i >= 0 else None
    vals = [_leg_pnl(leg, group["contract_size"], asof_ts, live_px) for leg in group["legs"]]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def _pnl_panel(now):
    """頂部左側:當日相關個股期貨損益消長圖(含今天平倉的),各檔一條線 + 合計一條粗白線。
    無相關部位或查無資料回空字串。"""
    groups = _positions_by_symbol()
    if not groups:
        return ""
    day = biglot_dashboard.ST.date
    mkt_open = biglot_dashboard.datetime.fromisoformat(f"{day}T08:45:00+08:00").timestamp()
    mkt_close = mkt_open + 5 * 3600  # 期貨盤中窗 08:45-13:45
    entry_times = [g["first_entry_ts"] for g in groups if g.get("first_entry_ts") is not None]
    # 圖形起點=所有相關部位裡最早那一口「今天查得到」的進場時間,不是開盤那一刻
    # (隔夜留倉的進場時間查不到,None,視為從t0就已經在場——見檔頭合晶案例說明)。
    t0 = min(entry_times) if entry_times else mkt_open
    t0 = max(t0, mkt_open)
    t1 = min(now.timestamp(), mkt_close)
    if t1 - t0 < 60:
        return ""
    n_grid = 360
    step_sec = max(15.0, (t1 - t0) / n_grid)
    grid_ts = []
    gt = t0
    while gt <= t1:
        grid_ts.append(gt)
        gt += step_sec
    if grid_ts[-1] != t1:
        grid_ts.append(t1)  # 確保最後一個網格點就是 t1 本身,線的終點才能對上下面的精確損益
    series = {g["fut_code"]: _pnl_series(g, grid_ts) for g in groups}
    # 最後一點(=t1)改用最新一筆真實成交重算,不吃網格 bisect 的結果(見 _current_pnl docstring)。
    cur_by_fc = {g["fut_code"]: _current_pnl(g, t1) for g in groups}
    for g in groups:
        series[g["fut_code"]][-1] = cur_by_fc.get(g["fut_code"])
    total = [sum(v for v in (series[g["fut_code"]][i] for g in groups) if v is not None) for i in range(len(grid_ts))]
    cur_total = sum(v for v in cur_by_fc.values() if v is not None)

    W, H, L, R = 470, 250, 4, 4
    all_vals = [v for vals in series.values() for v in vals if v is not None] + [v for v in total if v is not None]
    if not all_vals:
        return ""
    vmax = max(1.0, max(abs(v) for v in all_vals))

    def X(ts):
        return L + (ts - t0) / (t1 - t0) * (W - L - R)

    mid = H / 2

    def Y(v):
        return mid - (v / vmax) * (mid - 10)

    parts = [f"<line x1='{L}' y1='{mid:.1f}' x2='{W-R}' y2='{mid:.1f}' stroke='#30363d' stroke-width='1'/>"]

    def _line(vals, color, width, opacity):
        pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in zip(grid_ts, vals) if v is not None)
        if pts:
            parts.append(f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='{width}' opacity='{opacity}'/>")

    legend = []
    for i, g in enumerate(groups):
        col = PNL_COLORS[i % len(PNL_COLORS)]
        _line(series[g["fut_code"]], col, 1.2, 0.85)
        cur = cur_by_fc.get(g["fut_code"]) or 0.0
        closed_mark = " ✓平倉" if all(leg[4] is not None for leg in g["legs"]) else ""
        legend.append(f"<span style='color:{col}'>{g['name']} {cur:+,.0f}{closed_mark}</span>")
    _line(total, "#e6edf3", 2.0, 0.95)

    for v, y in ((vmax, Y(vmax)), (0, mid), (-vmax, Y(-vmax))):
        parts.append(f"<text x='{W-R+4}' y='{y+3:.1f}' fill='#8b949e' font-size='9'>{v:+,.0f}</text>")

    hov_rows = []
    for i, (gt, tv) in enumerate(zip(grid_ts, total)):
        row = [round(X(gt), 1), biglot_dashboard.datetime.fromtimestamp(gt, biglot_dashboard.TZ).strftime("%H:%M:%S"),
               round(tv) if tv is not None else None]
        for g in groups:
            v = series[g["fut_code"]][i]
            row.append(round(v) if v is not None else None)
        hov_rows.append(row)
    hov = json.dumps(hov_rows)
    names_json = json.dumps([g["name"] for g in groups])

    svg = (f"<svg width='{W}' height='{H}' style='display:block' data-pts='{hov}' data-names='{names_json}'>"
           + "".join(parts)
           + f"</svg>")
    cls = "up" if cur_total > 0 else ("dn" if cur_total < 0 else "")
    left = (f"<div><b>期貨未平倉損益</b></div>"
            f"<div><span class='{cls}' style='font-size:22px;font-weight:700'>{cur_total:+,.0f}</span>"
            f"<span class='dim' style='font-size:10px'> NTD</span></div>"
            f"<div class='dim' style='font-size:10px;line-height:1.5'>" + "<br>".join(legend) + "</div>"
            f"<div class='dim' style='font-size:9px'>含當日已平倉;倉位每15分查富邦一次,價格用本地tick即時算</div>")
    return (f"<div id='pnlsrc' hidden><div style='display:flex;gap:10px;align-items:stretch'>"
            f"<div style='flex:0 0 150px'>{left}</div><div style='flex:1 1 auto'>{svg}</div></div></div>")
