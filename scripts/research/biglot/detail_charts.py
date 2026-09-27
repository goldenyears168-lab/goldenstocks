"""biglot dashboard 重構：`_stock_series`/`_stock_series_locked`/`_svg_detail`/`_svg_mini`
四個函式一起搬移——後兩者是前兩者的輕量呼叫者/共用者，是緊耦合的一組，拆開搬會破壞可讀性。

`_stock_series` 呼叫 `_stock_series_locked` 前要先取 `biglot_dashboard.DETAIL_LOCK`
（`threading.Lock()`，2026-09-24 大戶累計 2 倍事故的修復——`loop()`（總覽/AGG）與 HTTP
（詳情頁）兩邊會同時呼叫 `_stock_series`，沒鎖會把同一段新增 bytes 解析兩次，見
`biglot_dashboard.py` 裡 `DETAIL_LOCK = threading.Lock()` 那行的行內註解）。這個鎖物件建立
一次後從不整包重新賦值，不是 docs/biglot-refactor-roadmap.md 列出的 18 個「危險全域」之一，
但為了跟其他 biglot_dashboard-native 名稱維持同一套存取規則、降低未來誤判風險，一律用
`biglot_dashboard.DETAIL_LOCK`/`biglot_dashboard.DETAIL` 屬性存取，不在檔案頂層
`from biglot_dashboard import X`。

`DATA_DIR` 不是 biglot_dashboard-native，是從 `stock_db` 直接 re-export 的常數，所以直接
`from stock_db import DATA_DIR`（跟 `biglot/pe_and_shadow.py` 做法一致）。`_limits`
（`biglot/utils.py`）、`_wrt_cum`（`biglot/scoring_support.py`）都已是搬到其他新模組的純函式，
非 biglot_dashboard-native 狀態，直接具名 import。

`_svg_detail` 唯一一處 `datetime.now(...)` 呼叫必須經過 `biglot_dashboard.datetime.now(...)`
+ `biglot_dashboard.TZ`，不能自己 `from datetime import datetime`——這正是本次重構第二批踩過的
真 bug（4 個新檔案各自匯入了自己的真時鐘，繞過測試工具凍結在 `biglot_dashboard.datetime` 上的
假時鐘，導致「排除今天」判斷用了真實牆鐘日期，42 檔昨收全算錯）。

`_stock_series_locked` 呼叫的 `_stk_trade` 是純函式，第三批已搬進 `biglot/trade_ingest.py`
（`biglot_dashboard.py` 自己也是用 `from biglot.trade_ingest import _stk_trade` 具名 import，
不是屬性存取），這裡比照同一慣例直接具名匯入。
"""
from __future__ import annotations

import html as html_mod
import json

import biglot_dashboard
from biglot.scoring_support import _wrt_cum
from biglot.trade_ingest import _stk_trade
from biglot.utils import _limits
from stock_db import DATA_DIR


def _stock_series(sid, day):
    """回傳 {mins:{"HH:MM":{px,vol,big,ret,tot}}, px0, last_px, big_day, ret_day, tot_day}。
    增量:今天的檔會一路長,只解析新增行;過去日解析一次後快取到 EOF。整段加鎖(見 DETAIL_LOCK)。"""
    with biglot_dashboard.DETAIL_LOCK:
        return _stock_series_locked(sid, day)


def _stock_series_locked(sid, day):
    key = (sid, day)
    st = biglot_dashboard.DETAIL.get(key)
    if st is None:
        st = {"off": 0, "lastvol": 0.0, "last_px": None, "last_seen": None,
              "first_done": False, "px0": None, "mins": {}}
        biglot_dashboard.DETAIL[key] = st
    raw = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{day}.jsonl"
    if raw.exists():
        # bytes 大塊讀:seek 到上次 offset,一次讀進新增區段,只保留到最後一個換行(尾行
        # 未寫完下輪再讀)。bytes 子字串預篩在 C 層跑,冷啟整天(95MB)<1s,遠快於 readline。
        sidb = sid.encode()
        with open(raw, "rb") as f:
            f.seek(st["off"])
            chunk = f.read()
        nl = chunk.rfind(b"\n")
        if nl != -1:
            st["off"] += nl + 1
            for line in chunk[:nl].split(b"\n"):
                if not line or sidb not in line:          # 便宜的子字串預篩(1/36 命中)
                    continue
                _stk_trade(sid, line.decode("utf-8", "replace"), st)
    return st


def _svg_detail(sid, day, st, pc):
    """單圖疊合(2026-09-24):分時價(左軸)+ 成交量(底部淡色柱)+ 累計大戶/散戶淨(右軸,0 線置中)。x 依實際時鐘 09:00–13:30。"""
    mins = st["mins"]
    if not mins:
        return "<div class='meta'>今日尚無成交(或該檔今日無資料)</div>"
    order = sorted(mins.keys())

    def _mod(hm):
        h, mm = hm.split(":")
        return int(h) * 60 + int(mm)
    x0, x1 = 540, 810                          # 09:00–13:30
    W, H, PADL, PADR, PADT, PADB = 940, 380, 52, 64, 10, 22
    plotW, plotH = W - PADL - PADR, H - PADT - PADB

    def X(hm):
        return PADL + max(0.0, min(1.0, (_mod(hm) - x0) / (x1 - x0))) * plotW
    pxs = [mins[k]["px"] for k in order if mins[k]["px"]]
    lo, hi = min(pxs), max(pxs)
    refs = [pc] if pc else []
    up = dn = None
    if pc:
        up, dn = _limits(pc)
        if up <= hi * 1.02:
            refs.append(up)
        if dn >= lo * 0.98:
            refs.append(dn)
    ylo, yhi = min([lo] + refs), max([hi] + refs)
    if yhi - ylo < 1e-9:
        yhi = ylo + 1
    pad = (yhi - ylo) * 0.06
    ylo -= pad
    yhi += pad

    def Y(v):                                  # 價格:左軸,佔整個繪圖區
        return PADT + (yhi - v) / (yhi - ylo) * plotH
    parts = [f"<svg viewBox='0 0 {W} {H}' style='width:100%;max-width:{W}px;height:auto;background:#0d1117' data-vw='{W}' data-pts='__PTS__'>"]
    # 成交量:底部 22% 高度的淡色柱(先畫,壓在最底層)
    VH = plotH * 0.22
    vmax = max((mins[k]["vol"] for k in order), default=1) or 1
    for k in order:
        v = mins[k]["vol"]
        if v <= 0:
            continue
        h = v / vmax * VH
        parts.append(f"<rect x='{X(k)-1:.1f}' y='{PADT+plotH-h:.1f}' width='2' height='{h:.1f}' fill='#3b5170' opacity='0.55'/>")
    # 累計大戶/散戶淨:右軸,0 線置中,單位萬
    cb = cr = 0.0
    cum = []
    for k in order:
        cb += mins[k]["big"]
        cr += mins[k]["ret"]
        cum.append((k, cb / 1e4, cr / 1e4))
    amax = max((max(abs(b), abs(r)) for _, b, r in cum), default=1) or 1
    fmid = PADT + plotH / 2

    def FY(wan):
        return fmid - (wan / amax) * (plotH / 2 - 8)
    parts.append(f"<line x1='{PADL}' y1='{fmid:.1f}' x2='{W-PADR}' y2='{fmid:.1f}' stroke='#30363d' stroke-width='1'/>")
    bpts = " ".join(f"{X(k):.1f},{FY(b):.1f}" for k, b, _ in cum)
    rpts = " ".join(f"{X(k):.1f},{FY(r):.1f}" for k, _, r in cum)
    parts.append(f"<polyline points='{bpts}' fill='none' stroke='#ff7b72' stroke-width='1.8' opacity='0.85'/>")
    parts.append(f"<polyline points='{rpts}' fill='none' stroke='#58a6ff' stroke-width='1.3' opacity='0.85'/>")
    wc = _wrt_cum(sid, order); wmax = 0.0
    if wc and any(abs(v) > 0 for v in wc):
        wmax = max(abs(v) for v in wc) or 1.0
        wpts = " ".join(f"{X(k):.1f},{fmid - (v / wmax) * (plotH / 2 - 8):.1f}" for k, v in zip(order, wc))
        parts.append(f"<polyline points='{wpts}' fill='none' stroke='#d2a8ff' stroke-width='1.2' opacity='0.9'/>")
        parts.append(f"<text x='{W-PADR+4}' y='{PADT+plotH-2:.1f}' fill='#d2a8ff' font-size='9'>權±{wmax/1e4:,.0f}萬</text>")
    for wan, y in ((amax, FY(amax)), (0, fmid), (-amax, FY(-amax))):
        parts.append(f"<text x='{W-PADR+4}' y='{y+3:.1f}' fill='#8b949e' font-size='10'>{wan:+,.0f}萬</text>")
    # 參考線:昨收(灰)/漲停(紅)/跌停(綠)
    for val, col, lab in [(pc, "#8b949e", "昨收" if day == biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime('%Y-%m-%d') else "基準"),
                          (up, "#d1242f", "漲停"), (dn, "#1a7f37", "跌停")]:
        if val and ylo <= val <= yhi:
            y = Y(val)
            parts.append(f"<line x1='{PADL}' y1='{y:.1f}' x2='{W-PADR}' y2='{y:.1f}' stroke='{col}' "
                         f"stroke-dasharray='4 3' stroke-width='1' opacity='0.7'/>")
            parts.append(f"<text x='{W-PADR-2}' y='{y-2:.1f}' fill='{col}' font-size='10' text-anchor='end'>{lab} {val:g}</text>")
    # 分時價格線(最上層)
    pts = " ".join(f"{X(k):.1f},{Y(mins[k]['px']):.1f}" for k in order if mins[k]["px"])
    parts.append(f"<polyline points='{pts}' fill='none' stroke='#e3b341' stroke-width='1.6'/>")
    for v in (yhi, (yhi + ylo) / 2, ylo):
        parts.append(f"<text x='2' y='{Y(v)+3:.1f}' fill='#e3b341' font-size='10'>{v:.1f}</text>")
    # 圖例
    parts.append(f"<text x='{PADL+4}' y='{PADT+11}' fill='#e3b341' font-size='10'>— 價(左軸)</text>")
    parts.append(f"<text x='{PADL+80}' y='{PADT+11}' fill='#ff7b72' font-size='10'>— 累計大戶淨(右軸·萬)</text>")
    parts.append(f"<text x='{PADL+210}' y='{PADT+11}' fill='#58a6ff' font-size='10'>— 累計散戶淨</text>")
    parts.append(f"<text x='{PADL+300}' y='{PADT+11}' fill='#3b5170' font-size='10'>▮ 量(底部)</text>")
    parts.append(f"<text x='{PADL+370}' y='{PADT+11}' fill='#d2a8ff' font-size='10'>— 累計權證簽號淨(自訂尺)</text>")
    for hm in ("09:00", "10:00", "11:00", "12:00", "13:00", "13:30"):
        parts.append(f"<text x='{X(hm):.1f}' y='{H-6}' fill='#8b949e' font-size='10' text-anchor='middle'>{hm}</text>")
    parts.append("</svg>")
    # hover 資料:每分鐘 [x, y(價), 時間, 價, 累計大戶萬, 累計散戶萬, 量張]
    cumd = {k: (b, r) for k, b, r in cum}
    wcd = dict(zip(order, wc)) if wc else {}
    hov = json.dumps([[round(X(k), 1), round(Y(mins[k]["px"]), 1), k, mins[k]["px"], round(cumd[k][0]), round(cumd[k][1]), int(mins[k]["vol"]), round(wcd.get(k, 0) / 1e4)]
                      for k in order if mins[k]["px"]])
    return "".join(parts).replace("__PTS__", html_mod.escape(hov, quote=True), 1)


def _svg_mini(st, pc):
    """6×6 總覽用迷你疊圖:價(黃)+昨收虛線 + 累計大戶(紅)/散戶(藍)右軸 + 量(底部面積)。無文字、座標取整、preserveAspectRatio=none 拉滿格子。"""
    mins = st["mins"]
    if not mins:
        return "<svg viewBox='0 0 320 170' preserveAspectRatio='none' style='width:100%;height:100%'></svg>", 1, 0
    order = sorted(mins.keys())
    W, H = 320, 170

    def _mod(hm):
        h, mm = hm.split(":")
        return int(h) * 60 + int(mm)

    def X(hm):
        return max(0.0, min(1.0, (_mod(hm) - 540) / 270)) * W
    pxs = [mins[k]["px"] for k in order if mins[k]["px"]]
    lo, hi = min(pxs), max(pxs)
    if pc:
        lo, hi = min(lo, pc), max(hi, pc)
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = (hi - lo) * 0.06
    lo -= pad; hi += pad

    def Y(v):
        return 4 + (hi - v) / (hi - lo) * (H - 8)
    hov = json.dumps([[_mod(k) - 540, mins[k]["px"]] for k in order if mins[k]["px"]], separators=(",", ":"))
    parts = [f"<svg viewBox='0 0 {W} {H}' preserveAspectRatio='none' style='width:100%;height:100%;display:block' data-pts='{hov}'>"]
    vmax = max((mins[k]["vol"] for k in order), default=1) or 1
    VH = H * 0.22
    area = " ".join(f"{X(k):.0f},{H - mins[k]['vol'] / vmax * VH:.0f}" for k in order)
    if area:
        parts.append(f"<polygon points='{X(order[0]):.0f},{H} {area} {X(order[-1]):.0f},{H}' fill='#3b5170' opacity='0.45'/>")
    cb = cr = 0.0; cum = []
    for k in order:
        cb += mins[k]["big"]; cr += mins[k]["ret"]; cum.append((k, cb, cr))
    amax = max((max(abs(b), abs(r)) for _, b, r in cum), default=1) or 1
    mid = H / 2
    FY = lambda v: mid - (v / amax) * (H / 2 - 6)  # noqa: E731
    parts.append(f"<line x1='0' y1='{mid:.0f}' x2='{W}' y2='{mid:.0f}' stroke='#30363d' stroke-width='1'/>")
    parts.append("<polyline points='" + " ".join(f"{X(k):.0f},{FY(b):.0f}" for k, b, _ in cum) + "' fill='none' stroke='#ff7b72' stroke-width='1.4' opacity='0.85'/>")
    parts.append("<polyline points='" + " ".join(f"{X(k):.0f},{FY(r):.0f}" for k, _, r in cum) + "' fill='none' stroke='#58a6ff' stroke-width='1.1' opacity='0.85'/>")
    wc = _wrt_cum(st.get("sid") or "", order) if st.get("sid") else None
    wmax = 0.0
    if wc and any(abs(v) > 0 for v in wc):
        wmax = max(abs(v) for v in wc) or 1.0
        parts.append("<polyline points='" + " ".join(f"{X(k):.0f},{mid - (v / wmax) * (H / 2 - 6):.0f}" for k, v in zip(order, wc)) + "' fill='none' stroke='#d2a8ff' stroke-width='1.1' opacity='0.9'/>")
    if pc:
        parts.append(f"<line x1='0' y1='{Y(pc):.0f}' x2='{W}' y2='{Y(pc):.0f}' stroke='#8b949e' stroke-dasharray='3 3' opacity='0.7'/>")
    parts.append("<polyline points='" + " ".join(f"{X(k):.0f},{Y(mins[k]['px']):.0f}" for k in order if mins[k]["px"]) + "' fill='none' stroke='#e3b341' stroke-width='1.5'/>")
    parts.append("</svg>")
    return "".join(parts), amax, wmax
