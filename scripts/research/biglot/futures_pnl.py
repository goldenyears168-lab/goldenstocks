"""富邦期貨留倉損益消長圖(2026-09-29 jack 交辦):在台指期面板左邊畫出目前持有的
個股期貨(依你的要求「只看期貨就好」)當日損益曲線——5 檔各一條線 + 合計一條線。

資料源分兩條,刻意分開:
1. **倉位結構**(哪些股票、方向、口數、進場價)——查富邦 `query_single_position`。
   ⚠ 2026-09-29 稍早才發生過打富邦 API 頻率過高、疑似觸發限流的事故(暫停
   tmf-channel-poll 才解除,見當日記錄),所以這裡刻意把查詢頻率壓到 **15 分鐘一次**
   (jack 明確定案:倉位本身不常變動,常變的是金額,金額不需要再問富邦)。查詢失敗時
   沿用上一次快取,不讓整張圖因為一次網路問題就消失。
2. **價格重建**(當日損益怎麼走)——不再問富邦,改讀本地已經在收的期貨逐筆封存
   (`collect_ccf_books_websocket.py` 寫的 `cache/{root}_trades/{root}_trades_{日}.jsonl`,
   `collect_stock_futures_books.py` 08:47 起、13:45 自退)。這條路完全不消耗富邦額度。

進場價×口數×contract_size 重算損益,已用富邦自己回報的 `profit_or_loss` 逐筆核對
分毫不差(2026-09-29 手動驗證:FIIXF/FILUF/FIOLF×2/FIOWF 五筆全對上),確認這個
重建方法可信。

⚠ 2026-09-29 jack 糾正:第一版誤把圖形起點釘死在 08:45(期貨開盤),但 `query_single_position`
只回傳 `date`(到日期),不含進場的精確時分——實際上使用者是在盤中陸續進場(這批單
分散在 10:11~12:35,不是開盤那一刻),誤把 08:45~各自進場之間畫成平線很誤導。改用
`get_futopt_order_results` 的 `date`+`last_time`(逐筆委託結果,精確到秒)回填每一口的
真實進場時間,圖形起點改成「所有留倉裡最早那一口的進場時間」,且同一檔(如大立光)
若分兩口不同時間進場,前面那段只算已進場的那一口、後面那口進場後才併入加總——不是
兩口一開始就一起算。

依賴規則同 biglot/tx_panel.py:`ST`/`datetime`/`TZ` 一律 `import biglot_dashboard`
屬性存取,不在頂層具名匯入,避免 stale reference 與凍結時鐘 monkey-patch 被繞過。
"""
from __future__ import annotations

import bisect as _bs
import json
import sys
import time

import biglot_dashboard
from stock_db import DATA_DIR

_POSITIONS_CACHE: dict = {"t": 0.0, "rows": []}
_POSITIONS_TTL = 900.0  # 15 分鐘,jack 2026-09-29 定案

_ROOT_PATH_CACHE: dict = {}
_STOCK_BY_FUTCODE_CACHE: dict | None = None

PNL_COLORS = ["#e3b341", "#79c0ff", "#d2a8ff", "#3fb950", "#ff7b72"]  # 5 檔各自顏色,合計線另用白色


def _stock_by_futcode():
    global _STOCK_BY_FUTCODE_CACHE
    if _STOCK_BY_FUTCODE_CACHE is None:
        _STOCK_BY_FUTCODE_CACHE = {
            r["fut_code"]: (r["sid"], r["name"], r.get("contract_size") or 2000)
            for r in biglot_dashboard._cal["universe"] if r.get("fut_code")
        }
    return _STOCK_BY_FUTCODE_CACHE


def _load_fubon_positions():
    """安全唯讀查詢富邦目前期貨留倉,15分鐘快取。失敗回傳上次快取(不讓圖因暫時性
    連線問題消失)。只呼叫既有、已核可的唯讀函式(connect_fubon/query_single_position/
    get_futopt_order_results),不新開下單路徑、不呼叫 dir()/vars()。"""
    now = time.time()
    if now - _POSITIONS_CACHE["t"] < _POSITIONS_TTL and _POSITIONS_CACHE["rows"]:
        return _POSITIONS_CACHE["rows"]
    try:
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
        res = fa.query_single_position(acc)
        raw_rows = list(_result_data(res) or [])
        legs = []
        order_nos = set()
        for r in raw_rows:
            sym = str(getattr(r, "symbol", "") or "")
            if not sym.startswith("FI"):
                continue
            fut_code = sym[2:]  # "FIOLF" -> "OLF"
            side = _bs_to_side(getattr(r, "buy_sell", None))
            try:
                lots = int(getattr(r, "orig_lots", None) or 0)
                entry = float(getattr(r, "price", None))
            except (TypeError, ValueError):
                continue
            if side is None or lots <= 0 or entry is None:
                continue
            on = base_order_no(str(getattr(r, "order_no", "") or ""))
            legs.append({"fut_code": fut_code, "sign": (1 if side == "L" else -1), "lots": lots,
                         "entry": entry, "order_no": on})
            if on:
                order_nos.add(on)
        # 精確進場時間:query_single_position 只給 date(到日期),要靠 get_futopt_order_results
        # 的 date+last_time(逐筆委託結果,精確到秒)才知道「幾點幾分下的單」,同一個 session
        # 內順便查、不額外多登入一次。
        entry_ts_map: dict[str, float] = {}
        if order_nos:
            try:
                order_rows = get_futopt_order_results(session, acc=acc)
                for orow in order_rows:
                    on = base_order_no(str(getattr(orow, "order_no", "") or ""))
                    if on not in order_nos or on in entry_ts_map:
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
        for leg in legs:
            leg["entry_ts"] = entry_ts_map.get(leg["order_no"])
        if legs:
            _POSITIONS_CACHE["rows"] = legs
            _POSITIONS_CACHE["t"] = now
    except Exception as e:  # noqa: BLE001
        print(f"[futures_pnl] position query failed, using stale cache: {e}", file=sys.stderr)
    return _POSITIONS_CACHE["rows"]


def _positions_by_symbol():
    """同一檔期貨多口合併成一組(供合併成一條線),查無對映股票的略過。
    legs = [(sign, lots, entry, entry_ts_or_None), ...];entry_ts 缺值(查不到)時
    在 _pnl_series 裡視為「從圖形起點就已經在場」,不會憑空消失。"""
    groups: dict[str, dict] = {}
    for r in _load_fubon_positions():
        g = groups.setdefault(r["fut_code"], {"fut_code": r["fut_code"], "legs": []})
        g["legs"].append((r["sign"], r["lots"], r["entry"], r.get("entry_ts")))
    fc_map = _stock_by_futcode()
    out = []
    for fc, g in groups.items():
        sid, name, csize = fc_map.get(fc, (None, None, None))
        if sid is None:
            continue
        g["sid"], g["name"], g["contract_size"] = sid, name, csize
        ts_list = [t for *_, t in g["legs"] if t is not None]
        g["first_entry_ts"] = min(ts_list) if ts_list else None
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


def _pnl_series(group, grid_ts):
    """回傳跟 grid_ts 等長的損益陣列(NTD)。該時刻之前還沒有任何成交、或這檔所有口
    都還沒進場回 None;同一檔分批進場(如大立光兩口不同時間)時,先進場那口先算,
    後進場那口到了它自己的 entry_ts 才併入加總——不是兩口從一開始就一起算。"""
    ticks = _load_trade_series(group["fut_code"], biglot_dashboard.ST.date)
    if not ticks:
        return [None] * len(grid_ts)
    times = [t for t, _ in ticks]
    prices = [p for _, p in ticks]
    out = []
    for gt in grid_ts:
        i = _bs.bisect_right(times, gt) - 1
        if i < 0:
            out.append(None)
            continue
        px = prices[i]
        active = [(sign, lots, entry) for sign, lots, entry, ets in group["legs"] if ets is None or ets <= gt]
        if not active:
            out.append(None)
            continue
        out.append(sum(sign * lots * group["contract_size"] * (px - entry) for sign, lots, entry in active))
    return out


def _current_pnl(group, asof_ts):
    """該檔「現在」損益(NTD),用**最新一筆實際成交**算,不是從圖表的取樣網格反推。

    2026-09-29 jack 抓到環球晶算錯:圖表線是每 ~15~60 秒取一個網格點畫線(360點,
    效能考量),但「現在損益」這個headline數字如果直接拿網格最後一點的價格,會撿到
    兩筆真實成交中間的舊值——當天環球晶收盤前最後成交在 13:44:37(934),但網格最後
    一點卡在 13:44:24(兩筆成交中間),用的是 13:44:15 那筆的 936,少算了 2 點×2000=4000。
    這裡繞過網格、直接對逐筆封存做 bisect,拿到 asof_ts 之前最新一筆真實成交價。"""
    ticks = _load_trade_series(group["fut_code"], biglot_dashboard.ST.date)
    if not ticks:
        return None
    times = [t for t, _ in ticks]
    i = _bs.bisect_right(times, asof_ts) - 1
    if i < 0:
        return None
    px = ticks[i][1]
    active = [(sign, lots, entry) for sign, lots, entry, ets in group["legs"] if ets is None or ets <= asof_ts]
    if not active:
        return None
    return sum(sign * lots * group["contract_size"] * (px - entry) for sign, lots, entry in active)


def _pnl_panel(now):
    """頂部左側:目前富邦期貨留倉當日損益消長圖,5 檔各一條線 + 合計一條粗白線。
    無持倉或查無資料回空字串。"""
    groups = _positions_by_symbol()
    if not groups:
        return ""
    day = biglot_dashboard.ST.date
    mkt_open = biglot_dashboard.datetime.fromisoformat(f"{day}T08:45:00+08:00").timestamp()
    mkt_close = mkt_open + 5 * 3600  # 期貨盤中窗 08:45-13:45
    entry_times = [g["first_entry_ts"] for g in groups if g.get("first_entry_ts") is not None]
    # 圖形起點=所有留倉裡最早那一口的實際進場時間,不是開盤那一刻(查不到任何進場時間時
    # 才退回開盤當保底,不讓圖整個消失)。
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
    # 最後一點(=t1)改用最新一筆真實成交重算,不吃網格 bisect 的結果(見 _current_pnl docstring:
    # 網格點之間可能卡在兩筆真實成交中間,取到舊價)——這樣線的視覺終點才會跟下面的headline數字一致。
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
        legend.append(f"<span style='color:{col}'>{g['name']} {cur:+,.0f}</span>")
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
    left = (f"<div><b>期貨留倉損益</b></div>"
            f"<div><span class='{cls}' style='font-size:22px;font-weight:700'>{cur_total:+,.0f}</span>"
            f"<span class='dim' style='font-size:10px'> NTD</span></div>"
            f"<div class='dim' style='font-size:10px;line-height:1.5'>" + "<br>".join(legend) + "</div>"
            f"<div class='dim' style='font-size:9px'>倉位每15分查富邦一次,價格用本地tick即時算</div>")
    return (f"<div id='pnlsrc' hidden><div style='display:flex;gap:10px;align-items:stretch'>"
            f"<div style='flex:0 0 150px'>{left}</div><div style='flex:1 1 auto'>{svg}</div></div></div>")
