"""biglot dashboard 重構：逐筆成交解析——即時全域累加版（`_ingest_trade`，逐筆餵
`biglot_dashboard.ST`，供盤中總覽表/標籤引擎/分數引擎使用）與個股詳情頁回放版
（`_stk_trade`，逐筆餵 `_stock_series_locked` 傳進來的 per-(sid,日) 狀態 dict，
只服務單一個股的分時圖）。

依 docs/biglot-refactor-roadmap.md「關鍵重構點」第 1 條交辦：這兩支函式做幾乎一樣
的 tick 解析工作，但彼此有**刻意保留、尚未裁決**的 drift（分桶粒度 5 分 vs 逐分、
是否追蹤買一/賣一給隱形大戶判定用等，見路線圖與本檔案 docstring 對照）——這批搬移
不做任何統一/去重/調解，兩支函式本體逐字照抄現狀。

跟 biglot/xq_style.py 同一條規則：模組頂層只 `import biglot_dashboard`（不
`from biglot_dashboard import X`），函式本體內用 `biglot_dashboard.X` 屬性存取
`biglot_dashboard.py` 自己定義的全域（`ST`/`BIG_AMT`/`RETAIL_CAP`/`GAP_JUMP_AMT`/
`GAP_SECONDS`/`datetime`）——`datetime.now`/`datetime.fromisoformat` 等都要走
`biglot_dashboard.datetime`，不能 `from datetime import datetime` 直接匯入，否則
golden-fixture 凍結時鐘的測試會被繞過（`_ingest_trade`/`_stk_trade` 這裡用的是
`datetime.fromisoformat(ts)`，不是 `.now()`，理論上跟 wall-clock 無關，但仍統一
走 `biglot_dashboard.datetime` 屬性存取，避免特例判斷、也避免將來有人在這支模組裡
加 `.now()` 呼叫時漏掉這條規則）。`bucket_key` 是 Phase 1 已確認的零依賴葉節點，
在 `biglot/utils.py`，直接 `from biglot.utils import bucket_key` 匯入。`json` 是
標準庫，直接匯入。

函式本體與 docstring 逐字複製，不改一行邏輯、不調解兩者的 drift。
"""
from __future__ import annotations

import json

import biglot_dashboard
from biglot import streak_live as _streak_live
from biglot.utils import bucket_key


def _ingest_trade(line):
    try:
        rec = json.loads(line)
    except Exception:
        return
    if rec.get("kind") != "message":
        return
    msg = rec["payload"]
    if msg.get("channel") != "trades" or msg.get("event") not in (None, "data"):
        return
    d = msg.get("data") or {}
    sid, px, vol = str(d.get("symbol", "")), d.get("price"), d.get("volume")
    if not sid or px is None or vol is None or d.get("isTrial"):
        return
    px, vol = float(px), float(vol)
    if d.get("isClose"):
        biglot_dashboard.ST.close_px[sid] = px
    dv = vol - biglot_dashboard.ST.lastvol[sid]
    biglot_dashboard.ST.lastvol[sid] = vol
    if dv <= 0 or d.get("isOpen") or d.get("isClose"):
        return
    if sid not in biglot_dashboard.ST.first_done:                # 開盤競價（常無 isOpen 旗標）
        biglot_dashboard.ST.first_done.add(sid)
        biglot_dashboard.ST.day[sid]["px0"] = px
        return
    b, a = d.get("bid"), d.get("ask")
    if b is not None: biglot_dashboard.ST.last_bid[sid] = float(b)
    if a is not None: biglot_dashboard.ST.last_ask[sid] = float(a)
    side = 1 if (a is not None and px >= float(a)) else (
        -1 if (b is not None and px <= float(b)) else 0)
    if side == 0:
        p = biglot_dashboard.ST.last_px.get(sid)
        side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
    biglot_dashboard.ST.last_px[sid] = px
    ts = rec.get("ts")
    if not ts:
        return
    t = biglot_dashboard.datetime.fromisoformat(ts)
    gap = sid in biglot_dashboard.ST.last_seen and (t - biglot_dashboard.ST.last_seen[sid]).total_seconds() > biglot_dashboard.GAP_SECONDS
    biglot_dashboard.ST.last_seen[sid] = t
    _t_ex = t                                         # 連次窗用交易所成交時間（ws `time`，µs）；缺值/時鐘異常退回收到時間
    try:
        _x = d.get("time")
        if _x is not None:
            _tx = biglot_dashboard.datetime.fromtimestamp(float(_x) / 1e6, biglot_dashboard.TZ)
            if abs((_tx - t).total_seconds()) <= 10.0:
                _t_ex = _tx
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    _streak_live.on_trade(sid, _t_ex, px, dv, b, a)   # 連次/連量(只讀描述欄;例外自吞,不影響本函式)
    amt = px * dv * 1000
    bk = bucket_key(t)
    row = biglot_dashboard.ST.buckets.setdefault(sid, {}).setdefault(
        bk, {"big": 0., "retn": 0., "ret2": 0., "midn": 0., "tot": 0.,
             "px": None, "vol": 0., "pxvol": 0.})
    row["px"] = px
    ds = biglot_dashboard.ST.day[sid]
    ds["hi"] = px if ds.get("hi") is None else max(ds["hi"], px)   # 今日高低(振幅倍數用)
    ds["lo"] = px if ds.get("lo") is None else min(ds["lo"], px)
    if ds["px0"] is None:
        ds["px0"] = px
    if gap and amt >= biglot_dashboard.GAP_JUMP_AMT:
        return
    row["tot"] += amt
    _q = biglot_dashboard.ST.recent[sid]
    _sgn = 1 if side > 0 else (-1 if side < 0 else 0)
    _q.append((t.timestamp(), px, amt, _sgn, amt >= biglot_dashboard.BIG_AMT, (dv == 1 and amt < biglot_dashboard.RETAIL_CAP)))
    _cut = t.timestamp() - 3700
    while _q and _q[0][0] < _cut:
        _q.popleft()
    row["vol"] += dv
    row["pxvol"] += px * dv
    ds["tot"] += amt
    sgn = 1 if side > 0 else (-1 if side < 0 else 0)
    if amt >= biglot_dashboard.BIG_AMT:
        row["big"] += sgn * amt
        ds["big"] += sgn * amt
        if t.hour >= 12:
            ds["big_pm"] += sgn * amt
    elif dv == 1 and amt < biglot_dashboard.RETAIL_CAP:
        row["retn"] += sgn * amt
        row["ret2"] += amt
        ds["ret"] += sgn * amt
        ds["ret2"] += amt
        _hm = t.strftime("%H:%M")                                # 散戶背離(SMFI,2026-09-25採納)的
        if "09:00" <= _hm < "09:25":                              # 固定基準窗口:開盤(永遠固定)+尾盤
            ds["ret_open"] += sgn * amt; ds["tot_open"] += amt    # (12:55-13:20,收盤後的「最終定格值」,
        elif "12:55" <= _hm < "13:20":                            # 供day_views.py/historical回顧與
            ds["ret_close"] += sgn * amt; ds["tot_close"] += amt  # reference_loaders.py的PREV2D回退用)。
            # ⚠ 2026-10-04起,render_main.py 盤中即時顯示/計分的「現在」這一側已改用近30分
            # 滾動窗(_r30n,SMFI_ACTIVE_FROM起算),不再讀這裡的 ds["ret_close"]/["tot_close"]
            # ——這兩個累加器仍然保留計算,只是變成「這天收盤後的定格值」專用,見
            # render_main.py 與 reference_loaders.py::_load_prev2d_totals 的對應說明。
    else:
        row["midn"] += sgn * amt
        ds["mid"] += sgn * amt


def _stk_trade(sid, line, st):
    try:
        rec = json.loads(line)
    except Exception:
        return
    if rec.get("kind") != "message":
        return
    msg = rec["payload"]
    if msg.get("channel") != "trades" or msg.get("event") not in (None, "data"):
        return
    d = msg.get("data") or {}
    if str(d.get("symbol", "")) != sid:
        return
    px, vol = d.get("price"), d.get("volume")
    if px is None or vol is None or d.get("isTrial"):
        return
    px, vol = float(px), float(vol)
    dv = vol - st["lastvol"]
    st["lastvol"] = vol
    if dv <= 0 or d.get("isOpen") or d.get("isClose"):
        return
    if not st["first_done"]:
        st["first_done"] = True
        st["px0"] = px
        return
    b, a = d.get("bid"), d.get("ask")
    side = 1 if (a is not None and px >= float(a)) else (
        -1 if (b is not None and px <= float(b)) else 0)
    if side == 0:
        p = st["last_px"]
        side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
    st["last_px"] = px
    ts = rec.get("ts")
    if not ts:
        return
    t = biglot_dashboard.datetime.fromisoformat(ts)
    gap = st["last_seen"] is not None and (t - st["last_seen"]).total_seconds() > biglot_dashboard.GAP_SECONDS
    st["last_seen"] = t
    amt = px * dv * 1000
    m = st["mins"].setdefault(t.strftime("%H:%M"),
                              {"px": None, "vol": 0.0, "big": 0.0, "ret": 0.0, "tot": 0.0})
    m["px"] = px
    try:                                                              # 每分鐘最後一筆的買一/賣一（現貨中價用；基差見 biglot/fut_basis.py）
        if b is not None and a is not None and float(b) > 0 and float(a) > 0:
            m["bid"], m["ask"] = float(b), float(a)
    except (TypeError, ValueError):
        pass
    if st["px0"] is None:
        st["px0"] = px
    if gap and amt >= biglot_dashboard.GAP_JUMP_AMT:                       # 空窗跳量:更新價、不計流量
        return
    m["tot"] += amt
    m["vol"] += dv
    sgn = 1 if side > 0 else (-1 if side < 0 else 0)
    if amt >= biglot_dashboard.BIG_AMT:
        m["big"] += sgn * amt
    elif dv == 1 and amt < biglot_dashboard.RETAIL_CAP:
        m["ret"] += sgn * amt
