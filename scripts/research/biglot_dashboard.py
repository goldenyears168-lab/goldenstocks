#!/usr/bin/env python3
"""45檔大戶-散戶即時儀表板 · 唯讀 · 自帶HTTP(:8771) · 30秒增量更新。

  PYTHONPATH=src .venv/bin/python scripts/research/biglot_dashboard.py
  瀏覽 http://100.81.2.33:8771 （Tailscale 內網）

資料源：biglot-live-watch raw jsonl（逐筆）＋ watchlist_books jsonl（五檔）。
方法論同 scripts/research/biglot_5m_monitor.py（互斥三桶/競價與跳量排除/
獨漲勿追旗標/散戶參與度），詳見該檔 docstring。
不動 :8770 live UI（tmf-sim-server），完全獨立、無下單路徑。
"""
from __future__ import annotations

import html as html_mod
import json
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "src")
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

TZ = timezone(timedelta(hours=8))
PORT = 8771
BIG_AMT = 10_000_000     # 2026-09-12 使用者定案:大戶=真大戶(>=1000萬)
RETAIL_CAP = 5_000_000   # 散戶定義不動:1張且<500萬(高價股1張大單歸中實不歸散戶)
GAP_JUMP_AMT = 500_000_000
GAP_SECONDS = 90
REFRESH_SEC = 5

CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
_cal = json.load(open(CALIB))
NAMES = {r["sid"]: r["name"] for r in _cal["universe"]}
CATS = {r["sid"]: r["cat"][:4] for r in _cal["universe"]}
# 細分產業(45檔手動策展,較 cat 粗類細一層,方便看族群輪動);查無則退回 CATS
SUBCAT = {
    "2492": "被動-MLCC", "6173": "被動-陶瓷", "2327": "被動-MLCC", "3042": "石英元件",
    "6182": "矽晶圓", "6488": "矽晶圓", "3532": "矽晶圓", "5483": "矽晶圓",
    "2303": "晶圓代工", "6770": "晶圓代工",
    "2449": "封測", "6147": "封測-驅動IC", "3374": "封測-晶圓級",
    "2408": "DRAM記憶體", "2344": "記憶體-利基", "2337": "記憶體-NOR", "3006": "記憶體IC設計",
    "3443": "IC設計-ASIC",
    "6213": "CCL銅箔基板", "6274": "CCL銅箔基板", "2383": "CCL銅箔基板", "1303": "塑化-CCL",
    "8358": "PCB-銅箔", "8039": "PCB-軟板材料",
    "8046": "ABF載板", "3189": "ABF載板", "3037": "ABF載板",
    "2368": "PCB-伺服器板", "4958": "PCB-軟板",
    "3105": "砷化鎵-PA", "2455": "砷化鎵-磊晶", "3081": "光通訊",
    "3406": "光學鏡頭", "3008": "光學鏡頭",
    "3324": "散熱", "3653": "散熱-均熱片", "3017": "散熱",
    "2059": "機殼滑軌", "6669": "伺服器代工", "2357": "品牌NB",
    "2481": "二極體-功率", "6223": "半導體設備-探針卡", "2301": "光電-電源",
    "2615": "貨櫃航運", "2609": "貨櫃航運",
    # 2026-09-22 換池納入(有個股期貨·高波動)
    "8150": "封測-記憶體", "6239": "封測-記憶體", "6271": "封測-CIS", "3711": "封測-龍頭",
    "2313": "PCB-HDI", "2308": "電源供應", "1802": "玻璃基板", "3231": "伺服器代工", "3481": "面板",
}
RET_UNM = {r["sid"] for r in _cal["universe"] if r.get("px", 0) * 1000 >= RETAIL_CAP}
# 高波動分數 = 20日日均振幅%((高−低)/收) ,來自 calib;越高越適合本系統的日內波段
AMP20 = {r["sid"]: r.get("amp20") for r in _cal["universe"]}
PREOPEN: dict = {}   # 盤前試撮快照 sid->{px,bid,ask,size,t}(collector preopen_*.json,08:45~09:00)
FUT_PX: dict = {}    # 個股期貨即時價 sid->{px,t}(futprice_*.json)
# 固定產業鏈排序(避免5秒隨大戶流跳位):相近產業相鄰,半導體上游→下游→非半導體。
# 產業交界畫粗線(band)。查無的股票排最後。
_CLUSTERS = [
    ("矽晶圓", ["6182", "6488", "5483", "3532"]),
    ("晶圓代工", ["2303", "6770"]),
    ("記憶體", ["2344", "2408", "2337", "3006"]),
    ("封測", ["2449", "6147", "3374"]),
    ("化合物半導體", ["3105", "2455"]),
    ("被動元件", ["2327", "2492", "6173", "3042"]),
    ("CCL銅箔基板", ["6213", "6274"]),
    ("ABF載板", ["8046", "3189", "3037"]),
    ("PCB/載板", ["2368", "4958", "8039", "8358"]),
    ("散熱", ["3324"]),
    ("光學", ["3406"]),
    ("功率二極體", ["2481"]),
    ("電源光電", ["2301"]),
    ("系統品牌", ["2357"]),
    ("塑化", ["1303"]),
    ("航運", ["2615", "2609"]),
]
SORT_INDEX = {sid: i for i, sid in enumerate(s for _n, sids in _CLUSTERS for s in sids)}
CLUSTER_OF = {sid: name for name, sids in _CLUSTERS for sid in sids}
try:
    _rb = json.load(open(DATA_DIR.parent / "cache" / "biglot_live_watch" / "_rvol_base.json"))
    RVOL_BASE = _rb.get("base", {})
except Exception:
    RVOL_BASE = {}

PAGE = {"frag": "<div class='meta'>初始化中…</div>"}
SNAP_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch" / "eod_snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)

def _load_hist():
    """近5個快照:每檔前幾日大戶淨流/收盤、宇宙5日累積、昨日午後低。"""
    # 排除「當日」快照:對昨收/前n日大戶都該用 ≤昨日 的收盤;否則盤後重啟會抓到今收→漲跌恆0
    _today = datetime.now(TZ).strftime("%Y-%m-%d")
    files = [f for f in sorted(SNAP_DIR.glob("eod_*.json")) if _today not in f.name][-5:]
    snaps = []
    for f in files:
        try:
            snaps.append(json.load(open(f)))
        except Exception:
            pass
    hist_big = {}      # sid -> [前n日big,...最舊在前]
    prev_close = {}
    y_pmlow = {}
    for s in snaps:
        for r in s["rows"]:
            hist_big.setdefault(r["sid"], []).append(r.get("big", 0))
            prev_close[r["sid"]] = r.get("close")
            if "pm_low" in r:
                y_pmlow[r["sid"]] = r["pm_low"]
    # 宇宙近5日累積(等權,快照收盤鏈)
    u5 = None
    if len(snaps) >= 2:
        rets = []
        for i in range(1, len(snaps)):
            a = {r["sid"]: r["close"] for r in snaps[i-1]["rows"]}
            b = {r["sid"]: r["close"] for r in snaps[i]["rows"]}
            vs = [(b[k]/a[k]-1) for k in b if k in a and a[k]]
            if vs:
                rets.append(sum(vs)/len(vs))
        if rets:
            u5 = sum(rets) * 100
    return hist_big, prev_close, y_pmlow, u5

HIST_BIG, PREV_CLOSE, Y_PMLOW, UNI5 = _load_hist()


def _load_prev_close_db():
    """昨收改抓官方 stock_daily_bars 的收盤競價價(權威),取代 EOD 快照的『最後一筆 tick』。
    快照 tick 收盤與官方收盤常差 1~2 檔,會讓漲跌%失真——南電 2026-09-21 快照 1055 vs
    官方 1060,今日漲停 1165 就被算成 +10.4%(超過±10%上限,不可能)。用『<今日的最近交易日』
    避免抓到今日盤中殘影;雙來源同價,取一筆即可。"""
    out = {}
    try:
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        for sid in NAMES:
            row = conn.execute(
                "SELECT close FROM stock_daily_bars WHERE stock_id=? AND trade_date<? "
                "AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 1",
                (sid, today)).fetchone()
            if row and row[0]:
                out[sid] = float(row[0])
        conn.close()
    except Exception:
        pass
    return out


PREV_CLOSE.update(_load_prev_close_db())   # 官方收盤優先,快照昨收僅作 fallback


def _load_daily_trend():
    """每檔日線趨勢(截至最近日收盤):站上5日均線? 5日動能%。
    回測(127日隔夜候選池):壓縮∧站上5日線 +93.8bps/t5.10 vs 跌破 +30/t1.65,
    差+63.5bps;純脈絡欄+影子帳分層,不改選股規則。日線雙來源(finmind/tpex/twse)
    同價,按 trade_date 去重取一筆。"""
    out = {}
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        for sid in NAMES:
            rows = conn.execute(
                "SELECT trade_date, MAX(close) FROM stock_daily_bars "
                "WHERE stock_id=? GROUP BY trade_date ORDER BY trade_date DESC LIMIT 11",
                (sid,)).fetchall()
            closes = [c for _, c in rows if c]
            if len(closes) < 4:
                continue
            last = closes[0]
            ma5 = sum(closes[:5]) / len(closes[:5])
            ma10 = sum(closes[:10]) / len(closes[:10]) if len(closes) >= 6 else None
            ret5 = (last / closes[5] - 1) * 100 if len(closes) >= 6 and closes[5] else None
            out[sid] = {"above_ma5": last > ma5,
                        "above_ma10": (last > ma10) if ma10 else None,
                        "ret5d": ret5, "last": last, "asof": rows[0][0]}
        conn.close()
    except Exception as e:
        print(f"[daily_trend] load failed: {e}", file=sys.stderr)
    return out

DAILY_TREND = _load_daily_trend()

# ---- 融資/借券變化幅度 → 波動風險分數（非方向訊號，只預測盤中振幅，多空都適用）------
# 方法論：scripts/research/margin_lending_spike_next_day_amplitude.py（45檔高波動宇宙
# 2025-01~2026-09 回測）。演進紀錄（後面取代前面）：
#   1) 一開始只看「同時大增」(AND, 有方向)：q95 事件少(35~42次)但效果最大(+1.9~2.3pp)。
#   2) 放寬成「任一邊大增即可」(OR)：事件變多但控制當日振幅(vol clustering)後大多不顯著
#      (q90 t1.90 p0.057、q95 t1.53 p0.127)——原始t值好看是自相關撐出來的假象。
#   3) 改良為「不分方向的變化幅度」(用 |日增幅| 而非「大增」)：兩邊各自的絕對值分位數
#      取平均當 Score(0~100)，關係轉為單調遞增(十等分乾淨遞增4.9%→6.2%)，控制當日振幅後
#      仍顯著(t=3.40 p=0.0007，比原始AND設計更穩健)——這是目前採用的最終版本。
# 已驗證的邊界：只影響隔日「盤中振幅(H-L)/前收」，對隔日|收對收報酬|(t1.57 p0.117)、
# 跳空幅度(t0.01 p0.99)、隔日量能(係數反而顯著為負,t-4.33)都測不出增量——不是方向或
# 跳空訊號,量能還偏低(流動性變薄格)。換Parkinson(log range)結果不變(t3.43),但換成
# True Range(含跳空)訊號整個消失(t-0.07)——訊號本質是「盤中來回」不是「跳空」。
# ⚠ margin_balance 單位是「張」(1張=1000股)、lending_balance 單位是「股」，
# 不可共用同一個門檻——5000張門檻會系統性排掉大立光/玉晶光/華碩/緯穎等高價股
# (股價高→可融資張數天生就少，不是資料不足)。
MIN_PREV_MARGIN_LOTS = 100        # 張，只擋真正近零/停融資的退化列
MIN_PREV_LENDING_SHARES = 5_000   # 股，同一用意
VOLRISK_MIN_OBS = 60              # 兩邊都要至少60個交易日紀錄才計分位，避免新股/資料不足誤判
VOLRISK_STALE_DAYS = 7            # 融資/借券最新一筆超過這麼多天沒更新(處置股常停融資)就不計分數
# Score 分級門檻(對應回測的q90/95/98,見上方演進紀錄第3版十等分結果)
VOLRISK_TIERS = ((92, "🌊🌊"), (86, "🌊"), (80, ""))  # 第三級只上色不加圖示,由極端到寬鬆


def _pctile_rank(values):
    """回傳每個元素在序列中的百分位排名(0~1,含自己;越大越極端)。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for pos, i in enumerate(order):
        ranks[i] = (pos + 1) / len(values)
    return ranks


def _load_vol_risk_flags():
    """算出每檔股票「最新一筆」融資/借券變化幅度(不分方向)的歷史分位平均分數。"""
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    sids = list(NAMES)
    ph = ",".join("?" * len(sids))
    mg_by_sid = defaultdict(list)
    for sid, td, bal in conn.execute(
            f"""SELECT stock_id, trade_date, margin_balance FROM (
                    SELECT stock_id, trade_date, margin_balance,
                           ROW_NUMBER() OVER (
                               PARTITION BY stock_id, trade_date
                               ORDER BY CASE source WHEN 'twse_mi_margn' THEN 0 ELSE 1 END
                           ) AS rn
                      FROM stock_margin_daily WHERE stock_id IN ({ph})
                ) WHERE rn=1 ORDER BY stock_id, trade_date""", sids):
        mg_by_sid[sid].append((td, bal))
    ln_by_sid = defaultdict(list)
    for sid, td, prev_bal, bal in conn.execute(
            f"""SELECT stock_id, trade_date, prev_balance, lending_balance
                  FROM stock_lending_balance_daily WHERE stock_id IN ({ph})
                  ORDER BY stock_id, trade_date""", sids):
        ln_by_sid[sid].append((td, prev_bal, bal))
    conn.close()

    out = {}
    for sid in sids:
        m, l = mg_by_sid.get(sid, []), ln_by_sid.get(sid, [])
        if len(m) < VOLRISK_MIN_OBS + 1 or len(l) < VOLRISK_MIN_OBS:
            continue
        m_dates, m_pct = [], []
        for i in range(1, len(m)):
            prev, cur = m[i - 1][1], m[i][1]
            if prev and prev >= MIN_PREV_MARGIN_LOTS and cur is not None:
                m_dates.append(m[i][0])
                m_pct.append((cur - prev) / prev)
        l_dates, l_pct = [], []
        for td, prev, bal in l:
            if prev and prev >= MIN_PREV_LENDING_SHARES and bal is not None:
                l_dates.append(td)
                l_pct.append((bal - prev) / prev)
        if len(m_pct) < VOLRISK_MIN_OBS or len(l_pct) < VOLRISK_MIN_OBS:
            continue
        days_stale = max(
            (datetime.now(TZ).date() - datetime.strptime(d, "%Y-%m-%d").date()).days
            for d in (m_dates[-1], l_dates[-1])
        )
        if days_stale > VOLRISK_STALE_DAYS:
            out[sid] = {
                "score": None, "tier": None, "stale": True, "days_stale": days_stale,
                "margin_asof": m_dates[-1], "lending_asof": l_dates[-1],
                "margin_pct": m_pct[-1], "lending_pct": l_pct[-1],
                "margin_abs_pctile": None, "lending_abs_pctile": None,
            }
            continue
        m_last = _pctile_rank([abs(x) for x in m_pct])[-1]
        l_last = _pctile_rank([abs(x) for x in l_pct])[-1]
        score = (m_last + l_last) / 2 * 100
        tier = next((badge for thr, badge in VOLRISK_TIERS if score >= thr), None)
        out[sid] = {
            "score": score, "tier": tier, "stale": False, "days_stale": days_stale,
            "margin_asof": m_dates[-1], "lending_asof": l_dates[-1],
            "margin_pct": m_pct[-1], "lending_pct": l_pct[-1],
            "margin_abs_pctile": m_last, "lending_abs_pctile": l_last,
        }
    return out


VOLRISK, VOLRISK_DATE = {}, None


def _refresh_vol_risk_if_needed() -> bool:
    """依實際日曆日期(非 ST.date)刷新——T-1 籌碼資料跟有沒有開盤無關，不该被
    ingest()/render() 只在盤中才跑的邏輯卡住,否則開盤前使用者看到的都是前一個
    交易日收盤時算出的舊分數(2026-09-21 發現:盤前完全看不到當天該有的分數)。
    回傳是否真的重算了,讓呼叫端決定要不要順便重繪一次盤後定格頁面。
    """
    global VOLRISK, VOLRISK_DATE
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if VOLRISK_DATE == today:
        return False
    try:
        VOLRISK = _load_vol_risk_flags()
    except Exception:
        VOLRISK = {}
    VOLRISK_DATE = today
    return True

OOS_FILE = DATA_DIR.parent / "cache" / "biglot_live_watch" / "oos_scoreboard.json"

def _oos_load():
    try:
        return json.load(open(OOS_FILE))
    except Exception:
        return {"intraday": [], "overnight": [], "overnight_pending": [],
            "overnight_short": [], "overnight_short_pending": []}

def _oos_summary():
    o = _oos_load()
    parts = []
    it = o.get("intraday", [])
    if it:
        rets = [x["ret"] for x in it]
        parts.append(f"💎終版 {len(it)}筆 均{sum(rets)/len(rets):+.0f}bps "
                     f"勝{sum(1 for x in rets if x > 0)}/{len(rets)}")
    ov = o.get("overnight", [])
    if ov:
        rets = [x["ret"] for x in ov]
        parts.append(f"隔夜 {len(ov)}筆 均{sum(rets)/len(rets):+.0f}bps "
                     f"勝{sum(1 for x in rets if x > 0)}/{len(rets)}")
        # 影子分層:只留日線站上5日線(回測+63.5bps)——並行OOS,不改選股
        ma = [x["ret"] for x in ov if x.get("above_ma5") is True]
        if ma:
            parts.append(f"↳日線多{len(ma)}筆 均{sum(ma)/len(ma):+.0f}bps "
                         f"勝{sum(1 for x in ma if x > 0)}/{len(ma)}")
    sh = o.get("overnight_short", [])
    if sh:
        rets = [x["ret"] for x in sh]
        parts.append(f"隔夜空 {len(sh)}筆 均{sum(rets)/len(rets):+.0f}bps "
                     f"勝{sum(1 for x in rets if x > 0)}/{len(rets)}")
        ms = [x["ret"] for x in sh if x.get("above_ma5") is False]
        if ms:
            parts.append(f"↳日線空{len(ms)}筆 均{sum(ms)/len(ms):+.0f}bps "
                         f"勝{sum(1 for x in ms if x > 0)}/{len(ms)}")
    pend = len(o.get("overnight_pending", []))
    if pend:
        parts.append(f"待結算{pend}")
    return " | ".join(parts) if parts else "OOS帳本累積中"


def _oos_update_at_close():
    """收盤後:結算昨日隔夜腿、記今日盤中終版訊號、掛今日隔夜候選。冪等(按日期)。"""
    o = _oos_load()
    today = ST.date
    if any(x.get("date") == today for x in o["intraday"]) or \
       any(x.get("date") == today for x in o["overnight_pending"]):
        return
    # a) 結算pending(用今日首價)
    still = []
    for p in o["overnight_pending"]:
        px0 = ST.day.get(p["sid"], {}).get("px0")
        if px0 and p.get("close"):
            o["overnight"].append({**p, "resolve_date": today,
                                   "ret": (px0 / p["close"] - 1) * 1e4})
        else:
            still.append(p)
    o["overnight_pending"] = still
    # 做空腿結算:做空報酬=-(次開/今收-1)
    still_s = []
    for p in o.get("overnight_short_pending", []):
        px0 = ST.day.get(p["sid"], {}).get("px0")
        if px0 and p.get("close"):
            o.setdefault("overnight_short", []).append(
                {**p, "resolve_date": today, "ret": -(px0 / p["close"] - 1) * 1e4})
        else:
            still_s.append(p)
    o["overnight_short_pending"] = still_s
    # b) 今日盤中終版訊號實績(三窗<5%∧pb5<0∧pb30<=-3千萬∧買>=3千萬>10%,45分)
    for sid, m in ST.buckets.items():
        if sid in RET_UNM:
            continue
        bks = sorted(m)
        for i in range(7, len(bks)):
            a = m[bks[i]]
            if not a["tot"] or a["big"] < 3e7 or a["big"] <= 0.10 * a["tot"]:
                continue
            shs = [m[bks[j]]["ret2"] / m[bks[j]]["tot"] * 100 if m[bks[j]]["tot"] else 99
                   for j in (i, i - 1, i - 2)]
            if max(shs) >= 5:
                continue
            if m[bks[i - 1]]["big"] >= 0 or sum(m[b]["big"] for b in bks[i - 6:i]) > -3e7:
                continue
            if i + 9 >= len(bks) or not a["px"] or not m[bks[i + 9]]["px"]:
                continue
            o["intraday"].append({"date": today, "sid": sid,
                                  "bucket": bks[i].strftime("%H:%M"),
                                  "ret": (m[bks[i + 9]]["px"] / a["px"] - 1) * 1e4,
                                  "gate": bool(UNI5 is not None and UNI5 < -5)})
    # c) 今日隔夜候選:做多3檔(佔比前10∧壓縮深)+ 做空3檔(佔比最負前10∧彈開最多∧日線空)
    cand = []
    for sid in NAMES:
        ds = ST.day.get(sid)
        px = ST.last_px.get(sid)
        pc = PREV_CLOSE.get(sid)
        if not ds or not px or not ds["tot"]:
            continue
        m = ST.buckets.get(sid, {})
        bks = sorted(m)
        last12 = [m[b]["px"] for b in bks[-12:] if m[b]["px"]]
        if len(last12) < 8:
            continue
        cand.append({"sid": sid, "big": ds["big"], "close": px,
                     "cmp": px / (sum(last12) / len(last12)) - 1,
                     "locked": bool(pc and px / pc - 1 >= 0.09),
                     "above_ma5": DAILY_TREND.get(sid, {}).get("above_ma5")})
    # 做多:大戶淨買>0∧未鎖漲停,佔比前10取壓縮最深3
    lp = [c for c in cand if c["big"] > 0 and not c["locked"]]
    lp = sorted(lp, key=lambda r: -r["big"])[:10]
    for p in sorted(lp, key=lambda r: r["cmp"])[:3]:
        o["overnight_pending"].append({
            "date": today, "sid": p["sid"], "close": p["close"],
            "above_ma5": p["above_ma5"]})
    # 做空:大戶淨賣<0,淨賣量最大前10取彈開最多3,再要日線↓空(回測+74.5/t3.82)
    sp = [c for c in cand if c["big"] < 0]
    sp = sorted(sp, key=lambda r: r["big"])[:10]
    for p in sorted(sp, key=lambda r: -r["cmp"])[:3]:
        o.setdefault("overnight_short_pending", []).append({
            "date": today, "sid": p["sid"], "close": p["close"],
            "above_ma5": p["above_ma5"]})
    json.dump(o, open(OOS_FILE, "w"))

SHELL = f"""<!DOCTYPE html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=0.6">
<title>大戶45檔儀表板</title><style>
body{{background:#0d1117;color:#c9d1d9;font:12px/1.5 -apple-system,'PingFang TC',monospace;margin:8px}}
h3{{margin:4px 0;font-size:14px}}
.meta{{color:#8b949e;font-size:11px;margin-bottom:6px}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}
th,td{{padding:2px 7px;text-align:right;border-bottom:1px solid #21262d}}
th{{position:sticky;top:0;z-index:2;background:#161b22;color:#8b949e;font-weight:600;cursor:default}}
th.g5{{color:#e3b341}} th.g30{{color:#79c0ff}} th.gd{{color:#d2a8ff}}
td.nm{{position:sticky;left:0;background:#0d1117;z-index:1;text-align:left;font-weight:600;color:#e6edf3}}
th.stk{{position:sticky;left:0;z-index:3}}
tbody tr td{{border-bottom:1px solid #1c2128}}
tbody tr.band td{{border-bottom:2px solid #454d57}}
tbody tr:hover{{background:#1c2635 !important}}
tbody tr:hover td.nm{{background:#1c2635 !important}}
.cat{{color:#8b949e;font-weight:400;font-size:10px;margin-left:4px}}
.up{{color:#ff7b72}} .dn{{color:#3fb950}} .dim{{color:#484f58}}
.lup{{background:#d1242f;color:#fff;font-weight:700}}  /* 漲停:紅底白字(台股慣例) */
.ldn{{background:#1a7f37;color:#fff;font-weight:700}}  /* 跌停:綠底白字 */
.nlup{{color:#ff7b72;font-weight:700}} .nldn{{color:#3fb950;font-weight:700}}  /* 接近漲/跌停:粗紅/綠 */
.warnv{{color:#e3b341}} .wall{{color:#d2a8ff;font-weight:700}}
.vr0{{color:#58a6ff}} .vr1{{color:#e3b341;font-weight:700}} .vr2{{color:#f0883e;font-weight:700}}
.flag{{color:#e3b341;text-align:left}}
.sigdn{{color:#3fb950}} .sigup{{color:#ff7b72}}
.rk1{{color:#ffd700;font-weight:700}} .rkN{{color:#3fb950;font-weight:700}}
.flagbar{{padding:3px 8px;font-size:12px;background:#161b22;margin-bottom:4px}}
.disc{{font-size:11px;line-height:1.7;background:#161b22;border:1px solid #30363d;
border-radius:6px;padding:6px 10px;margin-bottom:6px}}
.disc b{{color:#e6edf3}} .disc .ok{{color:#3fb950}} .disc .no{{color:#ff7b72}}
.disc summary{{cursor:pointer;color:#8b949e;font-weight:600}}
</style></head><body>
<h3>大戶-散戶 45檔即時儀表板
<a href="/history" style="font-size:11px;margin-left:8px;color:#79c0ff">歷史分頁</a>
<a href="/help" style="font-size:11px;margin-left:8px;color:#79c0ff">📖 欄位說明</a>
<button id="hpBtn" style="font-size:11px;margin-left:10px;background:#21262d;color:#8b949e;
border:1px solid #30363d;border-radius:4px;padding:2px 8px;cursor:pointer"></button></h3>
<details class="disc" open><summary>📏 發言紀律（每日必看·避免盤中過度預測）</summary>
<span class="ok">✓ 可預測（有 edge，只在收盤下判斷）</span>：隔夜今收→明開階梯（大戶佔比+壓縮，IC t7.1）·
同賣勿抱（大戶賣∧散戶賣，隔夜−28/t−6）· 漲停排隊撐滿30分 · 處置20分盤大戶方向。<br>
<span class="no">✗ 死區（已證偽/硬幣，盤中禁下方向判斷）</span>：盤中30分價格方向（單窗流量轉向勝率50.3%）·
壓縮抄底（20格全滅）· 開盤累計預測09:30後（自相關假象）· 午後大戶 · streak · 跳檔動能。<br>
<b>盤中30分方向規則（可喊·但要有根據）</b>：只在<b>已驗證格觸發</b>時喊方向，且必附
數字＋基準＋t——⚠勿追（漲×參與跳升或大戶賣，下跌趨勢日−9.6bps/續跌，V轉日反向）·
跌深大戶接（跌30分×大戶買×RVOL≥0.5，+11~14bps/t3.4）·噴後（30分漲≥150後均−32）·
健康抬轎（大戶買∧散戶<45%，唯一正格）。<b>無驗證格觸發的窗＝棄權（不硬喊）</b>。
自由心證的「我覺得會漲/跌」＝禁止；喊完要標這是條件式基準率、非確定。<br>
<b>每日進步</b>：昨日自評=分析76/30分預測58（見 docs/biglot-broadcast-protocol.md）。
教訓：主升段連喊「接近高點」早1小時＝等於錯；日線滤網連兩日做多側全空倉（OOS影子驗證中）。</details>
<div id="app"><div class="meta">載入中…</div></div>
<script>
const R={REFRESH_SEC}000;
let showHP = localStorage.getItem('showHP')==='1';
function applyHP(){{
  document.querySelectorAll('tr[data-hp]').forEach(tr=>tr.style.display=showHP?'':'none');
  document.getElementById('hpBtn').textContent =
    showHP?'隱藏高價股(≥2000,散戶不可測)':'顯示高價股(已隱藏)';
}}
document.getElementById('hpBtn').onclick=()=>{{
  showHP=!showHP; localStorage.setItem('showHP',showHP?'1':'0'); applyHP();
}};
async function tick(){{
  try{{
    const r=await fetch('/frag?_='+Date.now());
    const t=await r.text();
    document.getElementById('app').innerHTML=t;   // 只換內容,不重載整頁,不閃爍
    applyHP();                                     // 換完內容重套高價股隱藏
    const c=document.getElementById('closed');
    if(c && c.dataset.closed==='1') return;        // 收盤後停止輪詢
  }}catch(e){{}}
  setTimeout(tick,R);
}}
tick();
</script>
</body></html>"""


class S:
    """解析狀態（當日）。"""
    def __init__(self):
        self.date = None
        self.raw_off = 0
        self.book_off = 0
        self.lastvol = defaultdict(float)
        self.last_px = {}
        self.last_seen = {}
        self.first_done = set()
        self.buckets = {}                       # sid -> {bk: {...}}
        self.day = defaultdict(lambda: {"big": 0., "ret": 0., "ret2": 0., "mid": 0.,
                                        "tot": 0., "big_pm": 0., "px0": None})
        self.book = {}                          # sid -> latest snapshot


ST = S()


def bucket_key(dt):
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def ingest():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if ST.date != today:
        ST.__init__()
        ST.date = today
        _refresh_vol_risk_if_needed()
        global DAILY_TREND
        DAILY_TREND = _load_daily_trend()
        PREV_CLOSE.update(_load_prev_close_db())   # 換日refresh官方昨收
    raw = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{today}.jsonl"
    if raw.exists():
        with open(raw) as f:
            f.seek(ST.raw_off)
            while True:
                line = f.readline()
                if not line or not line.endswith("\n"):
                    break                     # 尾行未寫完,下輪再讀
                ST.raw_off = f.tell()
                _ingest_trade(line)
    bookf = DATA_DIR / "cache" / "watchlist_books" / f"watchlist_books_{today}.jsonl"
    if bookf.exists():
        with open(bookf) as f:
            f.seek(ST.book_off)
            while True:
                line = f.readline()
                if not line or not line.endswith("\n"):
                    break
                ST.book_off = f.tell()
                try:
                    r = json.loads(line)
                    ST.book[r["sym"]] = r
                except Exception:
                    pass
    # 盤前試撮快照 + 個股期貨即時價(小檔,每輪重讀)
    global PREOPEN, FUT_PX
    _bd = DATA_DIR.parent / "cache" / "biglot_live_watch"
    try:
        pf = _bd / f"preopen_{today}.json"
        PREOPEN = json.loads(pf.read_text()).get("trial", {}) if pf.exists() else {}
    except Exception:
        PREOPEN = {}
    try:
        ff = _bd / f"futprice_{today}.json"
        FUT_PX = json.loads(ff.read_text()) if ff.exists() else {}
    except Exception:
        FUT_PX = {}


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
    dv = vol - ST.lastvol[sid]
    ST.lastvol[sid] = vol
    if dv <= 0 or d.get("isOpen") or d.get("isClose"):
        return
    if sid not in ST.first_done:                # 開盤競價（常無 isOpen 旗標）
        ST.first_done.add(sid)
        ST.day[sid]["px0"] = px
        return
    b, a = d.get("bid"), d.get("ask")
    side = 1 if (a is not None and px >= float(a)) else (
        -1 if (b is not None and px <= float(b)) else 0)
    if side == 0:
        p = ST.last_px.get(sid)
        side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
    ST.last_px[sid] = px
    ts = rec.get("ts")
    if not ts:
        return
    t = datetime.fromisoformat(ts)
    gap = sid in ST.last_seen and (t - ST.last_seen[sid]).total_seconds() > GAP_SECONDS
    ST.last_seen[sid] = t
    amt = px * dv * 1000
    bk = bucket_key(t)
    row = ST.buckets.setdefault(sid, {}).setdefault(
        bk, {"big": 0., "retn": 0., "ret2": 0., "midn": 0., "tot": 0.,
             "px": None, "vol": 0., "pxvol": 0.})
    row["px"] = px
    ds = ST.day[sid]
    if ds["px0"] is None:
        ds["px0"] = px
    if gap and amt >= GAP_JUMP_AMT:
        return
    row["tot"] += amt
    row["vol"] += dv
    row["pxvol"] += px * dv
    ds["tot"] += amt
    sgn = 1 if side > 0 else (-1 if side < 0 else 0)
    if amt >= BIG_AMT:
        row["big"] += sgn * amt
        ds["big"] += sgn * amt
        if t.hour >= 12:
            ds["big_pm"] += sgn * amt
    elif dv == 1 and amt < RETAIL_CAP:
        row["retn"] += sgn * amt
        row["ret2"] += amt
        ds["ret"] += sgn * amt
        ds["ret2"] += amt
    else:
        row["midn"] += sgn * amt
        ds["mid"] += sgn * amt


def _fmt(v, unit=1e4, dec=0, plus=True):
    if v is None:
        return "—"
    s = f"{v/unit:+.{dec}f}" if plus else f"{v/unit:.{dec}f}"
    return s


def _tick_sz(p):
    return (0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100
            else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0)


def _limits(pc):
    """台股漲跌停價(±10%,對齊 tick):漲停=不超過+10%的最大tick、跌停=不低於−10%的最小tick。"""
    import math
    up, dn = pc * 1.1, pc * 0.9
    return math.floor(up / _tick_sz(up)) * _tick_sz(up), math.ceil(dn / _tick_sz(dn)) * _tick_sz(dn)


def _px_class(px, pc, chg):
    """價格著色類別:漲停紅底白字/跌停綠底白字/接近漲跌停粗字/一般漲跌。"""
    if px and pc:
        up, dn = _limits(pc)
        if px >= up - 1e-6:
            return "lup"
        if px <= dn + 1e-6:
            return "ldn"
    if chg is None:
        return ""
    if chg >= 9.0:
        return "nlup"
    if chg <= -9.0:
        return "nldn"
    return "up" if chg > 0 else ("dn" if chg < 0 else "")


def render():
    now = datetime.now(TZ)
    all_bks = sorted({bk for m in ST.buckets.values() for bk in m})
    done = [bk for bk in all_bks if bk + timedelta(minutes=5, seconds=10) <= now]
    cur = done[-1] if done else None
    prev = done[-2] if len(done) >= 2 else None
    win6 = done[-6:] if done else []
    win6p = done[-12:-6] if len(done) >= 7 else []
    base_bk = max((bk for bk in all_bks if bk < win6[0]), default=None) if win6 else None

    prior6 = done[-7:-1] if len(done) >= 7 else []

    rows, rets5, rets30 = [], [], []
    _dayrets = []
    for sid in NAMES:
        m = ST.buckets.get(sid, {})
        ds = ST.day.get(sid)
        a = m.get(cur) if cur else None
        p = m.get(prev) if prev else None
        r = {"sid": sid, "name": NAMES[sid], "cat": SUBCAT.get(sid, CATS.get(sid, "")),
             "amp20": AMP20.get(sid),
             "unm": sid in RET_UNM}
        # 5分窗
        r["w_ret"] = (a["px"] / p["px"] - 1) * 10000 if (a and p and a["px"] and p["px"]) else None
        if r["w_ret"] is not None:
            rets5.append(r["w_ret"])
        r["big5"] = a["big"] if a else None
        r["big_prior6"] = (sum(m[bk]["big"] for bk in prior6 if bk in m)
                           if prior6 else None)
        r["big5p"] = p["big"] if p else None
        r["tot5"] = a["tot"] if a else None
        r["tot5p"] = p["tot"] if p else None
        r["retn5"] = a["retn"] if a else None
        rb = RVOL_BASE.get(sid, {})
        bk_lbl = cur.strftime("%H:%M") if cur else None
        r["rvol5"] = (a["tot"] / rb[bk_lbl] if (a and a["tot"] and bk_lbl in rb and rb[bk_lbl] > 0)
                      else None)
        if win6:
            b30 = sum(rb.get(bk.strftime("%H:%M"), 0) for bk in win6)
            t30v = sum(m[bk]["tot"] for bk in win6 if bk in m)
            r["rvol30"] = t30v / b30 if b30 > 0 else None
        else:
            r["rvol30"] = None
        share = a["ret2"] / a["tot"] * 100 if (a and a["tot"]) else None
        share_p = p["ret2"] / p["tot"] * 100 if (p and p["tot"]) else None
        r["share5"] = share
        if a and a["tot"]:
            r["rbuy5"] = (a["ret2"] + a["retn"]) / 2 / a["tot"] * 100
            r["rsell5"] = (a["ret2"] - a["retn"]) / 2 / a["tot"] * 100
        else:
            r["rbuy5"] = r["rsell5"] = None
        r["dshare"] = share - share_p if (share is not None and share_p is not None) else None
        # 大戶連續同向窗數
        streak = 0
        for bk in reversed(done):
            v = m.get(bk, {}).get("big", 0)
            if streak == 0:
                sgn0 = 1 if v > 0 else (-1 if v < 0 else 0)
                if sgn0 == 0:
                    break
                streak = sgn0
            else:
                if (v > 0) == (streak > 0) and v != 0:
                    streak += 1 if streak > 0 else -1
                else:
                    break
        r["streak"] = streak
        # 30分窗
        if win6:
            r["big30"] = sum(m[bk]["big"] for bk in win6 if bk in m)
            t30 = sum(m[bk]["tot"] for bk in win6 if bk in m)
            r["tot30"] = t30
            _r2 = sum(m[bk]["ret2"] for bk in win6 if bk in m)
            _rn = sum(m[bk]["retn"] for bk in win6 if bk in m)
            r["rbuy30"] = (_r2 + _rn) / 2 / t30 * 100 if t30 else None
            r["rsell30"] = (_r2 - _rn) / 2 / t30 * 100 if t30 else None
            r["share30"] = (_r2 / t30 * 100
                            if t30 else None)
            r["big30p"] = sum(m[bk]["big"] for bk in win6p if bk in m) if win6p else None
            t30p = sum(m[bk]["tot"] for bk in win6p if bk in m) if win6p else 0
            sh30p = (sum(m[bk]["ret2"] for bk in win6p if bk in m) / t30p * 100
                     if t30p else None)
            r["dsh30"] = (r["share30"] - sh30p
                          if (r["share30"] is not None and sh30p is not None) else None)
            px_end = next((m[bk]["px"] for bk in reversed(win6) if bk in m and m[bk]["px"]), None)
            px_base = (m[base_bk]["px"] if base_bk and base_bk in m and m[base_bk].get("px")
                       else next((m[bk]["px"] for bk in win6 if bk in m and m[bk]["px"]), None))
            r["r30"] = (px_end / px_base - 1) * 10000 if (px_end and px_base) else None
            if r["r30"] is not None:
                rets30.append(r["r30"])
        else:
            r["big30"] = r["share30"] = r["r30"] = None
            r["rbuy30"] = r["rsell30"] = None
            r["tot30"] = r["big30p"] = r["dsh30"] = None
        # 全日
        last_price = ST.last_px.get(sid)
        r["px"] = last_price
        _pc = PREV_CLOSE.get(sid)                       # 前一交易日收盤(專業看盤主報價基準)
        r["chg_amt"] = (last_price - _pc) if (_pc and last_price) else None
        r["chg_pct"] = ((last_price / _pc - 1) * 100) if (_pc and last_price) else None
        r["day_ret"] = ((last_price / ds["px0"] - 1) * 100
                        if (ds and ds["px0"] and last_price) else None)
        r["bigday"] = ds["big"] if ds else None
        r["retday"] = ds["ret"] if ds else None
        r["bigpm"] = ds["big_pm"] if ds else None
        # 融資/借券變化幅度波動風險分數（T-1資料，只預測盤中振幅，非方向/跳空訊號）
        vr = VOLRISK.get(sid)
        r["volrisk_score"] = vr["score"] if vr else None
        r["volrisk_tier"] = vr["tier"] if vr else None
        if not vr:
            r["volrisk_title"] = "融資/借券歷史資料不足60個交易日，無法計算分數"
        elif vr.get("stale"):
            r["volrisk_title"] = (
                f"融資或借券最新資料已 {vr['days_stale']} 天未更新(>{VOLRISK_STALE_DAYS}天門檻)"
                f"(融資asof {vr['margin_asof']}／借券asof {vr['lending_asof']})，"
                f"可能是處置股停融資或資料延遲，不計分數")
        else:
            stale_note = f"（資料{vr['days_stale']}天前,較舊）" if vr["days_stale"] > 2 else ""
            r["volrisk_title"] = (
                f"波動風險分數 {vr['score']:.0f}/100{stale_note} · "
                f"T-1 融資({vr['margin_asof']})日變動{vr['margin_pct']*100:+.1f}%"
                f"(變動幅度歷史分位{vr['margin_abs_pctile']*100:.0f}%) · "
                f"借券({vr['lending_asof']})日變動{vr['lending_pct']*100:+.1f}%"
                f"(變動幅度歷史分位{vr['lending_abs_pctile']*100:.0f}%) · "
                f"45檔宇宙回測:分數每+1分,控制當日振幅後隔日振幅仍+0.006pp(t3.40 p0.0007)。"
                f"只預測盤中來回幅度,對隔日淨報酬/跳空/量能皆無解釋力,非方向訊號")
        # 隔夜策略因子
        r["bigsh_d"] = (ds["big"] / ds["tot"] * 100) if (ds and ds["tot"]) else None
        last12 = [m[bk]["px"] for bk in done[-12:] if bk in m and m[bk]["px"]]
        r["cmp1h"] = ((last_price / (sum(last12) / len(last12)) - 1) * 100
                      if (len(last12) >= 8 and last_price) else None)
        hb = HIST_BIG.get(sid, [])
        streak_ok = (ds and ds["big"] > 0 and len(hb) >= 2 and hb[-1] > 0 and hb[-2] > 0)
        tongmai = (ds and ds["big"] < 0 and ds["ret"] < 0)
        weak_open = tongmai and (r["cmp1h"] is not None and r["cmp1h"] > 0)
        r["stamp"] = ("連3買" if streak_ok else "") + ("⚠同賣" if tongmai else "") + ("↓弱開" if weak_open else "")
        ypl = Y_PMLOW.get(sid)
        r["pmlow_warn"] = (ypl is not None and last_price is not None and last_price <= ypl * 1.002)
        # VWAP 與委託簿
        tv = sum(v["vol"] for v in m.values())
        tpv = sum(v["pxvol"] for v in m.values())
        vwap = tpv / tv if tv else None
        r["vwap_gap"] = (last_price / vwap - 1) * 10000 if (vwap and last_price) else None
        bkk = ST.book.get(sid)
        if bkk:
            try:
                v = int(bkk.get("v") or 0)
                tstr = bkk.get("t", "09:00:00")
                mins = max((int(tstr[:2]) - 9) * 60 + int(tstr[3:5]), 1)
                pm_rate = v / mins
                bq, aq = sum(bkk.get("bq", [])), sum(bkk.get("aq", []))
                r["bid_min"] = bq / pm_rate if pm_rate else None
                r["ask_min"] = aq / pm_rate if pm_rate else None
                u, px_now = bkk.get("u"), last_price or bkk.get("z")
                r["lu_dist"] = ((float(u) / px_now - 1) * 100
                                if (u and px_now) else None)
            except Exception:
                r["bid_min"] = r["ask_min"] = r["lu_dist"] = None
        else:
            r["bid_min"] = r["ask_min"] = r["lu_dist"] = None
        if r.get("day_ret") is not None:
            _dayrets.append(r["day_ret"])
        rows.append(r)

    mkt5 = sum(rets5) / len(rets5) if rets5 else 0.0
    mkt30 = sum(rets30) / len(rets30) if rets30 else 0.0

    # 排名(1=最大;注意力分流用,非訊號——個股排名持續性已檢定為不可持續)
    def _rank(key):
        order = sorted((r for r in rows if r.get(key) is not None),
                       key=lambda x: -x[key])
        return {r["sid"]: i + 1 for i, r in enumerate(order)}
    rk5, rk5p = _rank("big5"), _rank("big5p")
    rkh, rkhp = _rank("tot5"), _rank("tot5p")
    rk30, rk30p, rkd = _rank("big30"), _rank("big30p"), _rank("bigday")
    for r in rows:
        r["r5"] = rk5.get(r["sid"])
        r["d5"] = (rk5p[r["sid"]] - r["r5"]) if (r["r5"] and r["sid"] in rk5p) else None
        r["rh"] = rkh.get(r["sid"])
        r["dh"] = (rkhp[r["sid"]] - r["rh"]) if (r["rh"] and r["sid"] in rkhp) else None
        r["r30r"] = rk30.get(r["sid"])
        r["d30"] = ((rk30p[r["sid"]] - r["r30r"])
                    if (r["r30r"] and r["sid"] in rk30p) else None)
        r["rdr"] = rkd.get(r["sid"])
    # 旗標(30分尺度,127日面板驗證:勿追超額-4.9~-5.6bps cl-t≈-3;跌深大戶接+8.9bps cl-t+2.3)
    for r in rows:
        r["flag"] = ""
        big30n = (r["big30"] / r["tot30"] * 100
                  if (r.get("big30") is not None and r.get("tot30")) else None)
        if r["r30"] is not None and r["r30"] > 30:
            if ((r["dsh30"] is not None and r["dsh30"] > 10 and not r["unm"])
                    or (big30n is not None and big30n < -5)):
                r["flag"] = "⚠勿追30"
        elif (r["r30"] is not None and r["r30"] < -30
              and big30n is not None and big30n > 5):
            r["flag"] = "🟢跌深大戶接"
        # 5分早期旗標(127日驗證預測未來30分:F8超額-6.1/cl-t-4.9、F9 -9.1/-5.0;
        #  F7 +10.8/+2.8僅觀察)——與30分旗標並列顯示
        big5n = (r["big5"] / r["tot5"] * 100
                 if (r.get("big5") is not None and r.get("tot5")) else None)
        early = ""
        tot5v = r.get("tot5") or 0
        if (big5n is not None and big5n > 10 and tot5v > 0
                and r["share5"] is not None and r["share5"] < 5 and not r["unm"]
                and r["big_prior6"] is not None and r["big_prior6"] < 0
                and r["big5p"] is not None and r["big5p"] < 0):
            # 127日兩兩交互測試定案:核心=逆大戶(雙尺度)∧散戶<5%,+23.8bps/t5.18 n=1801
            # 市場方向條件是死重(只+1.3bps卻砍40%樣本),已移除;7/7月為正
            # 劑量≥3000萬:+28.7/t4.09、45分+36.1/t3.84
            early = ("💎💎逆勢強(≥3千萬)" if r["big5"] >= 3e7
                     else "💎逆勢純機構")
        elif r["w_ret"] is not None and r["w_ret"] > 20:
            if ((r["dshare"] is not None and r["dshare"] > 5 and not r["unm"])
                    or (big5n is not None and big5n < -5)):
                early = "⚠勿追5m"
                if r["rvol5"] is not None and r["rvol5"] < 1.0:
                    early = "⚠勿追5m(枯量,最強帶)"   # 127日:<1x 帶超額-7~-9bps t≈-3.3
        elif (r["w_ret"] is not None and r["w_ret"] < -20
              and big5n is not None and big5n > 5):
            if r["rvol5"] is not None and r["rvol5"] >= 0.5:
                early = "🟢跌深大戶接"               # RVOL>=0.5 條件版:127日+11~14bps cl-t 3.4-3.9
            else:
                early = "▫接刀(枯量,無效帶)"          # <0.5x 帶127日超額≈0
        if early:
            r["flag"] = (r["flag"] + " " + early).strip()

    # 漲訊/跌訊 逐條件計分(只收錄已驗證格;每格獨立打勾,分數=命中數)
    for r in rows:
        par30 = ((r["rbuy30"] or 0) + (r["rsell30"] or 0)
                 if (r.get("rbuy30") is not None and not r["unm"]) else None)
        bear = []
        if r.get("r30") is not None and r["r30"] >= 150:
            bear.append("噴後")        # 波段峰後30分均-32/中位-45
        if "⚠勿追" in r["flag"]:
            bear.append("勿追")        # 漲×參與跳升或大戶賣 -5~-9.6bps(趨勢日-32)
        if (r.get("big30") is not None and r["big30"] <= -3e7
                and (par30 is None or par30 < 15)):
            bear.append("機構賣")      # 30分大戶賣≥3千萬∧散戶缺席=機構主動調節(金居格)
        if (r.get("w_ret") is not None and r["w_ret"] > 20
                and r.get("rbuy5") is not None and r["rbuy5"] >= 5 and not r["unm"]):
            bear.append("散急拉")      # 散戶買方推的急拉留不到收盤 -6~-9bps/t-5
        if "⚠同賣" in r["stamp"]:
            bear.append("同賣")        # 大戶賣∧散戶賣 隔夜-28bps/t-6
        if r.get("pmlow_warn"):
            bear.append("破昨低")      # 觸昨日午後低點 -125bps/73%貫穿
        bull = []
        if (r.get("big30") is not None and r["big30"] >= 3e7
                and (par30 is None or par30 < 45)):
            bull.append("抬轎")        # 大戶買≥3千萬∧散戶<45%=健康抬轎(唯一正格)
        if "💎" in r["flag"]:
            bull.append("💎")          # 逆勢純機構 +24~29bps/t5.2
        if "🟢" in r["flag"]:
            bull.append("深接")        # 跌深大戶接(RVOL≥0.5) +11~14bps/t3.4+
        if (r.get("bigsh_d") is not None and r["bigsh_d"] >= 10
                and r.get("cmp1h") is not None and r["cmp1h"] < 0):
            bull.append("佔壓")        # 佔比≥10%∧收盤前壓著=隔夜雙鍵(IC t7.1)
        if "連3買" in r["stamp"]:
            bull.append("連3買")       # 持續章(確認格)
        r["bear_n"], r["bear_txt"] = len(bear), "·".join(bear)
        r["bull_n"], r["bull_txt"] = len(bull), "·".join(bull)

    # 固定產業鏈排序(不隨大戶流跳位);查無者(理論上不會有)排最後、依big30
    rows.sort(key=lambda r: (SORT_INDEX.get(r["sid"], 999), -(r["big30"] or 0)))
    # 產業交界的最後一列→畫粗線
    grpend = set()
    for i, r in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if nxt is None or CLUSTER_OF.get(r["sid"]) != CLUSTER_OF.get(nxt["sid"]):
            grpend.add(r["sid"])
    win_lbl = (f"{cur.strftime('%H:%M')}–{(cur+timedelta(minutes=5)).strftime('%H:%M')}"
               if cur else "—")
    w30_lbl = (f"{win6[0].strftime('%H:%M')}–{(win6[-1]+timedelta(minutes=5)).strftime('%H:%M')}"
               if win6 else "—")

    # 旗標速覽 + 資料延遲警示
    fl_chase = " ".join(f"{r['sid']}{r['name']}" for r in rows if r["flag"] == "⚠勿追30")
    fl_catch = " ".join(f"{r['sid']}{r['name']}" for r in rows
                        if r["flag"] == "🟢跌深大戶接")
    flag_bar = ""
    gate_txt = ""
    if UNI5 is not None:
        gate_on = UNI5 < -5
        gate_txt = (f"<span style='color:{'#ff7b72' if gate_on else '#8b949e'}'>閘門(近5日{UNI5:+.1f}%):"
                    f"{'🔴啟動-僅記帳' if gate_on else '🟢關'}</span> · ")
    on_pool = [r for r in rows if r.get("bigday") and r["bigday"] > 0 and not r["unm"]]
    cand_txt = ""
    if now.strftime("%H:%M") >= "13:00" and len(on_pool) >= 5:
        pool = sorted(on_pool, key=lambda r: -r["bigday"])[:10]
        pool = [r for r in pool if r.get("cmp1h") is not None]
        picks = sorted(pool, key=lambda r: r["cmp1h"])[:3]
        cand_txt = ("<span style='color:#ffd700'>隔夜候選(前10∧壓縮深3,13:25定案):</span> "
                    + " ".join(f"{r['sid']}{r['name']}({r['cmp1h']:+.1f}%/佔{r['bigsh_d']:.0f}%)"
                               for r in picks) + " · ")
    if fl_chase:
        flag_bar += f"<span class='warnv'>⚠勿追30(漲窗×參與跳升/大戶賣):</span> {fl_chase} "
    if fl_catch:
        flag_bar += f"<span style='color:#3fb950'>🟢跌深大戶接(唯一正EV格):</span> {fl_catch} "
    fl_e1 = " ".join(f"{r['sid']}{r['name']}" for r in rows if "⚠勿追5m" in r["flag"])
    fl_e2 = " ".join(f"{r['sid']}{r['name']}" for r in rows if "🟡接刀觀察" in r["flag"])
    fl_dia = " ".join(f"{r['sid']}{r['name']}" for r in rows if "💎" in r["flag"])
    if fl_dia:
        flag_bar = (f"<span style='color:#79c0ff'>💎逆勢純機構買單(127日+16bps/t4.4,全系統最強格):</span> "
                    f"{fl_dia} ") + flag_bar
    if fl_e1:
        flag_bar += f"<span class='warnv'>⚠勿追5m(早期):</span> {fl_e1} "
    if fl_e2:
        flag_bar += f"<span style='color:#d29922'>🟡接刀觀察(5m早期,未達門檻):</span> {fl_e2}"
    if not flag_bar:
        flag_bar = "<span class='dim'>本窗無旗標</span>"
    raw_path = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{ST.date}.jsonl"
    in_mkt = now.weekday() < 5 and "09:00" <= now.strftime("%H:%M") <= "13:32"
    stale_bar = ""
    if in_mkt:
        try:
            age = time.time() - raw_path.stat().st_mtime
            if age > 90:
                stale_bar = (f"<div style='background:#6e1a1a;color:#ffb3b3;padding:4px 8px;"
                             f"font-weight:700'>⚠ 資料延遲 {age:.0f} 秒——collector 可能斷線,"
                             f"表格為舊資料</div>")
        except FileNotFoundError:
            stale_bar = ("<div style='background:#6e1a1a;color:#ffb3b3;padding:4px 8px;"
                         "font-weight:700'>⚠ 今日 raw 檔不存在——collector 未啟動</div>")
    else:
        stale_bar = "<div style='color:#8b949e;padding:2px 8px'>盤後定格(非交易時段)</div>"

    def td(v, fmt="wan", cls_by_sign=True, unm=False):
        if unm:
            return "<td class='dim'>不可測</td>"
        if v is None:
            return "<td class='dim'>—</td>"
        cls = ""
        if cls_by_sign:
            cls = "up" if v > 0 else ("dn" if v < 0 else "")
        if fmt == "wan":
            txt = f"{v/1e4:+,.0f}"
        elif fmt == "yi":
            txt = f"{v/1e8:+.2f}"
        elif fmt == "bps":
            txt = f"{v:+.0f}"
        elif fmt == "pct":
            txt = f"{v:.0f}%"
            cls = "warnv" if v >= 35 else ""
        elif fmt == "pct2":
            txt = f"{v:+.2f}%"
        elif fmt == "min":
            txt = f"{v:.1f}分"
            cls = "wall" if v >= 10 else ("dim" if v < 3 else "")
        elif fmt == "int":
            txt = f"{v:+d}"
        else:
            txt = str(v)
        return f"<td class='{cls}'>{txt}</td>"

    def rk_td(n, d=None):
        if n is None:
            return "<td class='dim'>—</td>"
        arrow = ""
        if d is not None and d != 0:
            arrow = (f"<span class='up'>▲{d}</span>" if d > 0
                     else f"<span class='dn'>▼{-d}</span>")
        cls = "rk1" if n <= 3 else ("rkN" if n >= 43 else "")
        return f"<td class='{cls}'>{n}{arrow}</td>"

    VR_CLS = {"🌊🌊": "vr2", "🌊": "vr1", "": "vr0"}   # 對應 VOLRISK_TIERS 的分級,單一事實來源

    def vr_td(r):
        title = html_mod.escape(r.get("volrisk_title") or "")
        score = r.get("volrisk_score")
        if score is None:
            return f"<td class='dim' title=\"{title}\">—</td>"
        tier = r.get("volrisk_tier")
        cls = VR_CLS.get(tier, "") if tier is not None else ""
        icon = tier if tier else ""
        return f"<td class='{cls}' title=\"{title}\">{icon}{score:.0f}</td>"

    _mktday = sum(_dayrets) / len(_dayrets) if _dayrets else None
    for r in rows:
        r["rs_live"] = (r["day_ret"] - _mktday
                        if (r.get("day_ret") is not None and _mktday is not None) else None)
        # 順/逆市:個股30分方向 vs 市場30分方向(描述性脈絡,非訊號——市場是最強控制變數)
        if r.get("r30") is not None and abs(r["r30"]) >= 20 and abs(mkt30) >= 5:
            same = (r["r30"] > 0) == (mkt30 > 0)
            r["mkt_ctx"] = ("順漲" if (same and mkt30 > 0)
                            else "順跌" if same
                            else "逆強" if r["r30"] > 0
                            else "逆弱")
        else:
            r["mkt_ctx"] = None
        dt = DAILY_TREND.get(r["sid"])
        r["dtrend"] = dt
    trs = []
    for r in rows:
        name = html_mod.escape(f"{r['sid']} {r['name']}")
        hp = ' data-hp="1"' if (r["px"] or 0) >= 2000 else ""
        _band = " class='band'" if r["sid"] in grpend else ""     # 產業交界粗線
        _cc = r.get("chg_amt")                          # 對昨收漲跌:紅漲綠跌(台股慣例)
        _qcls = _px_class(r.get("px"), PREV_CLOSE.get(r["sid"]), r.get("chg_pct"))
        if _cc is not None:
            _arrow = "▲" if _cc > 0 else ("▼" if _cc < 0 else "")
            _chgtd = f"<td class='{_qcls}'>{_arrow}{abs(_cc):g} {r['chg_pct']:+.2f}%</td>"
        else:
            _chgtd = "<td class='dim'>—</td>"
        # 個股期貨即時價 + 基差%(期貨/現股−1)
        _fp = FUT_PX.get(r["sid"]); _futpx = _fp.get("px") if isinstance(_fp, dict) else _fp
        if _futpx and r.get("px"):
            _bas = (_futpx / r["px"] - 1) * 100
            _futtd = (f"<td class='{'up' if _bas > 0 else ('dn' if _bas < 0 else '')}'>{_futpx:g}"
                      f"<span class='dim' style='font-size:9px'> {_bas:+.1f}</span></td>")
        else:
            _futtd = "<td class='dim'>—</td>"
        # 盤前試撮:試撮價+跳空%(vs昨收) / 買一/賣一×撮合張
        _tr = PREOPEN.get(r["sid"]); _tpc = PREV_CLOSE.get(r["sid"])
        if _tr and _tr.get("px") is not None:
            _tpx = _tr["px"]
            _gap = ((_tpx / _tpc - 1) * 100) if _tpc else None
            _gtxt = (f"<span class='dim' style='font-size:9px'> {_gap:+.1f}%</span>" if _gap is not None else "")
            _trtd = f"<td class='{_px_class(_tpx, _tpc, _gap)}'>{_tpx:g}{_gtxt}</td>"
            _trbktd = (f"<td style='font-size:10px'>{_tr.get('bid')}/{_tr.get('ask')}"
                       f"<span class='dim'>×{_tr.get('size') or 0}</span></td>")
        else:
            _trtd = "<td class='dim'>—</td>"
            _trbktd = "<td class='dim'>—</td>"
        trs.append(
            f"<tr{hp}{_band}>"
            f"<td class='nm'>{name}<span class='cat'>{r['cat']}</span></td>"
            + vr_td(r)
            + (f"<td class='{'warnv' if r['amp20'] >= 7 else ('dim' if r['amp20'] < 5 else '')}'>"
               f"{r['amp20']:.1f}%</td>" if r.get("amp20") is not None else "<td class='dim'>—</td>")
            + rk_td(r["r30r"], r["d30"]) + rk_td(r["rdr"])
            + rk_td(r["r5"], r["d5"]) + rk_td(r["rh"], r["dh"])
            + f"<td class='{_qcls}'>{r['px'] if r['px'] else '—'}</td>"
            + _futtd
            + _chgtd
            + td(r["day_ret"], "pct2")
            + _trtd + _trbktd
            + td(r["r30"], "bps")
            + (f"<td class='{'up' if '逆強' in r['mkt_ctx'] or '順漲' in r['mkt_ctx'] else 'dn'}' "
               f"style='font-size:11px'>{r['mkt_ctx']}</td>"
               if r.get("mkt_ctx") else "<td class='dim'>—</td>")
            + td(r["big5"], "wan") + td(r["big30"], "wan") + td(r["bigday"], "yi")
            + (f"<td class='{'warnv' if (r['rbuy30'] or 0) >= 5 else ''}'>{r['rbuy30']:.1f}%</td>"
               if (r.get("rbuy30") is not None and not r["unm"]) else "<td class='dim'>—</td>")
            + (f"<td>{r['rsell30']:.1f}%</td>"
               if (r.get("rsell30") is not None and not r["unm"]) else "<td class='dim'>—</td>")
            + td(r["dsh30"], "bps", True, r["unm"]).replace("bps", "")
            + td(r["w_ret"], "bps") + td(r["retn5"], "wan", unm=r["unm"])
            + (f"<td class='{'warnv' if (r['rbuy5'] or 0) >= 5 else ''}'>"
               f"{r['rbuy5']:.1f}%</td>" if (r["rbuy5"] is not None and not r["unm"]) else "<td class='dim'>—</td>")
            + (f"<td>{r['rsell5']:.1f}%</td>" if (r["rsell5"] is not None and not r["unm"]) else "<td class='dim'>—</td>")
            + td(r["retday"], "yi", unm=r["unm"])
            + (f"<td class='{'up' if r['bigsh_d'] > 0 else 'dn'}'>{r['bigsh_d']:+.1f}%</td>"
               if r["bigsh_d"] is not None else "<td class='dim'>—</td>")
            + (f"<td class='{'dn' if r['cmp1h'] < 0 else ''}'>{r['cmp1h']:+.2f}%</td>"
               if r["cmp1h"] is not None else "<td class='dim'>—</td>")
            + (("<td class='up' style='font-size:11px'>↑多"
                + (f" {r['dtrend']['ret5d']:+.1f}%" if r['dtrend'].get('ret5d') is not None else "")
                + "</td>" if r['dtrend']['above_ma5']
                else "<td class='dn' style='font-size:11px'>↓空"
                + (f" {r['dtrend']['ret5d']:+.1f}%" if r['dtrend'].get('ret5d') is not None else "")
                + "</td>")
               if r.get("dtrend") else "<td class='dim'>—</td>")
            + f"<td class='flag'>{r['stamp']}{'🔻破昨低' if r.get('pmlow_warn') else ''}</td>"
            + (f"<td class='{'dn' if r['rs_live'] < 0 else ('warnv' if r['rs_live'] > 1 else '')}'>"
               f"{r['rs_live']:+.1f}</td>" if r.get("rs_live") is not None else "<td class='dim'>—</td>")
            + td(r["lu_dist"], "pct2", False)
            + (f"<td class='{'wall' if (r['rvol5'] or 0) >= 2 else ('dim' if (r['rvol5'] or 0) < 0.5 else '')}'>"
               f"{r['rvol5']:.1f}x</td>" if r["rvol5"] is not None else "<td class='dim'>—</td>")
            + td(r["bid_min"], "min", False) + td(r["ask_min"], "min", False)
            + (f"<td class='sigdn' style='text-align:left{';font-weight:700' if r['bear_n'] >= 2 else ''}'>"
               f"{r['bear_n']}·{r['bear_txt']}</td>" if r["bear_n"] else "<td class='dim'>0</td>")
            + (f"<td class='sigup' style='text-align:left{';font-weight:700' if r['bull_n'] >= 2 else ''}'>"
               f"{r['bull_n']}·{r['bull_txt']}</td>" if r["bull_n"] else "<td class='dim'>0</td>")
            + f"<td class='flag'>{r['flag']}</td>"
            "</tr>")

    upd_note = (f"每{REFRESH_SEC}s自動更新(不重載)" if in_mkt
                else "盤後定格,已停止更新")
    PAGE["frag"] = f"""<div id="closed" data-closed="{0 if in_mkt else 1}" hidden></div>
{stale_bar}
<div class="meta">更新 {now.strftime('%H:%M:%S')} · 5分窗 {win_lbl} · 30分窗 {w30_lbl} ·
市場代理 5分 <b>{mkt5:+.1f}bps</b> / 30分 <b>{mkt30:+.1f}bps</b> ·
紅=正/買 綠=負/賣 · 淨流單位:5分=萬、全日=億 · 簿深≥10分=牆(紫) <3分=真空(灰) ·
散戶參與≥35%標黃 · <b>大戶=≥1000萬</b>(127日:隔夜IC+0.13/接刀+12.7/勿追賣−9.6皆過檢) · 排名=注意力分流非訊號 · <b>主尺度=30分</b>(旗標依127日驗證:
勿追30超額−5bps/跌深大戶接+9bps/💎逆勢純機構=千萬淨買&gt;10%窗量∧前5分+前30分大戶皆淨賣∧散戶&lt;5%→+24bps cl-t5.2(兩兩交互測試定案:市場方向係死重已移除);💎💎=淨買≥3千萬→30分+29/45分+36bps;效應前5分吃69%、45分後歸零) · 5分組=執行細節 · {upd_note}</div>
<div class="flagbar">{gate_txt}<span style='color:#a5d6ff'>OOS: {_oos_summary()}</span> · {cand_txt}{flag_bar}</div>
<table><thead><tr>
<th class="stk">股票</th>
<th title="波動風險分數(0-100)＝融資日變動幅度歷史分位 與 借券日變動幅度歷史分位 的平均(不分方向,大增大減都算)。45檔宇宙回測:分數與隔日盤中振幅單調正相關,控制當日振幅(排除純波動群聚)後仍顯著(t3.40 p0.0007)。只預測盤中來回幅度——對隔日淨報酬/跳空/量能皆無解釋力,非方向訊號,量能反而偏低(流動性變薄)。🌊🌊=≥92分 🌊=≥86分 藍字=≥80分">波動分數</th>
<th title="高波動分數=20日日均振幅%((高−低)/收盤)。這是選股進本系統的門檻指標:宇宙中位約6.5%,越高日內波段越大、越適合大戶/散戶流策略。金字=≥7%(高波動)、灰=＜5%(偏低)。與左側『波動分數』不同:那是融資/借券變動的T-1振幅預測,這是實際已實現振幅。">振幅%</th>
<th title="30分大戶淨流排名(主尺度)">R30</th><th title="全日大戶淨流排名">R日</th>
<th title="5分大戶淨流排名">R5</th><th title="5分成交金額排名">R熱</th>
<th title="現價,顏色為對前一交易日收盤:紅漲綠跌(台股慣例)">價</th><th title="個股期貨即時價+基差(小字=期貨/現股−1 %,正=期貨溢價)。資料源:個股期貨ws(Phase2上線後才有值,之前顯示—)">期貨</th><th title="對前一交易日收盤的漲跌金額與%(專業看盤主報價)">對昨收</th><th title="現價/今日開盤−1(盤中相對開盤走勢,與對昨收互補)">日內%</th><th title="盤前08:45~09:00 試撮價+跳空%(小字=vs昨收);09:00開盤後凍結為最終試撮。開盤後空窗屬正常(僅盤前有值)">盤前試撮</th><th title="盤前試撮的買一/賣一價 與 撮合張數(bid/ask×張)">試撮買賣</th>
<th class="g30">30分bps</th>
<th class="g30" title="個股30分方向vs市場30分方向(描述性脈絡,非訊號):順漲/順跌=同向,逆強=市場跌它漲,逆弱=市場漲它跌。市場是個股報酬最強控制變數,讀任何訊號前先看這格">順逆市</th>
<th class="gd" title="5分大戶淨額(萬)=最短窗">5分大戶</th>
<th class="g30" title="30分大戶淨額(萬)=滾動窗">30分大戶</th>
<th class="gd" title="全日累計大戶淨額(億)=盤中一路累加,收盤即全日淨額;最重要,÷成交=佔比%(隔夜排序主鍵IC+0.097/t7.1)。三尺度並排看背離:短窗買∧全日仍賣=誘多">全日大戶</th>
<th class="g30" title="30分散戶買方參與(毒藥側,≥5%標黃)">散買30</th>
<th class="g30" title="30分散戶賣方參與(投降側,無資訊)">散賣30</th>
<th class="g30">Δ參與30</th>
<th class="g5">5分bps</th><th class="g5">5分散戶淨</th>
<th class="g5" title="散戶買方參與(毒藥側:只買不賣格-11bps/t-4.9,>=5%標黃)">散買%</th><th class="g5" title="散戶賣方參與(投降側:無資訊,less bad)">散賣%</th><th class="gd">全日散戶</th>
<th class="gd" title="當日大戶淨流÷成交=隔夜排序主鍵(IC+0.097/t7.1)">佔比%</th>
<th class="gd" title="現價距尾盤1h均線=壓縮鍵(負=壓著,隔夜挑股用;13:20後看)">壓縮1h</th>
<th class="gd" title="日線趨勢(截至最近日收盤):↑多=站上5日均線,↓空=跌破;附5日動能%。回測:壓縮∧站上5日線隔夜+93.8bps/t5.10 vs 跌破+30/t1.65(差+63.5)——壓縮回檔在日線多頭股才是買點、空頭股是接刀。短線(壓縮/即時RS)×日線(此欄)分層,並行OOS影子帳驗證中,暫不改選股規則">日線</th>
<th class="gd" title="連3買=持續章(挑股加分)/⚠同賣=今晚勿抱(-28bps/t-6)/↓弱開=明日弱開候選/🔻=跌回昨日午後低點(出場警戒)">章</th>
<th title="個股日內−宇宙日內(百分點):負(綠)=相對壓著(彈簧),>+1(黃)=已彈開;軟否決件:日線弱∧已彈=毒格−31bps">即時RS</th><th>距漲停</th><th title="5分窗成交金額/近5日同時段中位">量能x</th><th>買簿</th><th>賣簿</th>
<th title="下跌訊號計分(命中數·明細),六格皆127日/實戰驗證:噴後=30分漲≥150bps(峰後均−32) · 勿追=漲×參與跳升或大戶賣(−5~−9.6,趨勢日−32) · 機構賣=30分大戶賣≥3千萬∧散戶參與<15%(機構主動調節,流量領先價格~2h) · 散急拉=5分漲>20∧散買≥5%(留不到收盤−6~−9) · 同賣=大戶賣∧散戶賣(隔夜−28/t−6) · 破昨低=觸昨日午後低點(−125bps/73%貫穿);≥2粗體">跌訊</th>
<th title="上漲訊號計分:抬轎=30分大戶買≥3千萬∧散戶<45%(健康抬轎唯一正格) · 💎=逆勢純機構(+24~29/t5.2) · 深接=跌深大戶接RVOL≥0.5(+11~14/t3.4) · 佔壓=全日佔比≥10%∧壓縮<0(隔夜雙鍵IC t7.1,收盤導向) · 連3買=持續章(確認格);≥2粗體">漲訊</th>
<th>旗標</th>
</tr></thead><tbody>{''.join(trs)}</tbody></table>"""


ARC_CSS = """<style>body{background:#0d1117;color:#c9d1d9;font:13px/1.6 -apple-system,'PingFang TC',monospace;margin:10px}
table{border-collapse:collapse;white-space:nowrap}th,td{padding:2px 9px;text-align:right;border-bottom:1px solid #21262d}
th{background:#161b22;color:#8b949e;position:sticky;top:0;z-index:2;cursor:pointer;user-select:none}
td.nm{position:sticky;left:0;background:#0d1117;text-align:left;font-weight:600;color:#e6edf3;z-index:1}
th.stk{position:sticky;left:0;top:0;z-index:3}
.top5{color:#ffd700;font-weight:700}.bot5{color:#3fb950;font-weight:700}
.up{color:#ff7b72}.dn{color:#3fb950}.dim{color:#484f58}.nx{background:#161b22}
a{color:#79c0ff;text-decoration:none}h3{margin:4px 0}.meta{color:#8b949e;font-size:11px}</style>"""


SORT_JS = """<script>
(function(){
  const tb = document.querySelector('table'); if(!tb) return;
  const ths = tb.querySelectorAll('thead th');
  let cur = -1, asc = false;
  function val(td){
    const t = td.textContent.trim();
    if(t==='—'||t==='') return null;
    const n = parseFloat(t.replace(/[+%,]/g,'').replace('不可測',''));
    return isNaN(n) ? t : n;
  }
  ths.forEach((th,i)=>{ th.addEventListener('click',()=>{
    if(cur===i){ asc=!asc } else { cur=i; asc=false }
    ths.forEach(h=>h.textContent=h.textContent.replace(/[▲▼]$/,''));
    th.textContent += asc?'▲':'▼';
    const rows=[...tb.querySelectorAll('tbody tr')];
    rows.sort((a,b)=>{
      const x=val(a.children[i]), y=val(b.children[i]);
      if(x===null) return 1; if(y===null) return -1;
      if(typeof x==='string'||typeof y==='string')
        return asc ? String(x).localeCompare(String(y)) : String(y).localeCompare(String(x));
      return asc ? x-y : y-x;
    });
    const body=tb.querySelector('tbody');
    rows.forEach(r=>body.appendChild(r));
  })});
})();
</script>"""


def _day_sig_counts():
    """以儀表板旗標同款條件回放今日:每檔 💎 / 💎💎 觸發數。"""
    out = {}
    for sid, m in ST.buckets.items():
        if sid in RET_UNM:
            continue
        bks = sorted(m)
        c1 = c2 = 0
        for i in range(7, len(bks)):
            cur, prev, prior6 = bks[i], bks[i - 1], bks[i - 6:i]
            a, p = m[cur], m[prev]
            if not a["tot"] or a["big"] <= 0.10 * a["tot"]:
                continue
            if a["ret2"] / a["tot"] * 100 >= 5:
                continue
            if sum(m[b]["big"] for b in prior6) >= 0 or p["big"] >= 0:
                continue
            c1 += 1
            if a["big"] >= 3e7:
                c2 += 1
        out[sid] = (c1, c2)
    return out


def snapshot_day():
    """收盤後存當日 EOD 快照(冪等)。"""
    today = ST.date
    if not today:
        return
    f = SNAP_DIR / f"eod_{today}.json"
    if f.exists() or not ST.day:
        return
    sig = _day_sig_counts()
    rows = []
    for sid in NAMES:
        ds = ST.day.get(sid)
        px = ST.last_px.get(sid)
        if not ds or px is None:
            continue
        s1, s2 = sig.get(sid, (0, 0))
        m = ST.buckets.get(sid, {})
        pm_px = [m[bk]["px"] for bk in m if bk.hour >= 12 and m[bk]["px"]]
        rows.append({"sid": sid, "name": NAMES[sid], "close": px, "px0": ds["px0"],
                     "pm_low": min(pm_px) if len(pm_px) >= 3 else None,
                     "big": ds["big"], "ret2": ds["ret2"], "retn": ds["ret"], "tot": ds["tot"],
                     "sig1": s1, "sig2": s2})
    if len(rows) >= 20:                      # 資料太少不存(避免半天斷線垃圾)
        json.dump({"date": today, "rows": rows}, open(f, "w"))


def _snap_dates():
    return sorted(p.name[4:14] for p in SNAP_DIR.glob("eod_*.json"))


def _load_snap(d):
    try:
        return json.load(open(SNAP_DIR / f"eod_{d}.json"))
    except Exception:
        return None


def render_history():
    ds = _snap_dates()
    lis = "".join(f"<li><a href='/day?d={d}'>{d}</a></li>" for d in reversed(ds))
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=0.8'>"
            f"<title>歷史分頁</title>{ARC_CSS}</head><body>"
            f"<h3>每日收盤快照 <a href='/'>←即時</a></h3><ul>{lis or '<li>尚無</li>'}</ul>"
            f"<div class='meta'>每頁最後兩欄=次日漲跌/次日排名(次日收盤後自動補上)</div></body></html>")


def render_day(d):
    snap = _load_snap(d)
    if not snap:
        return f"<!DOCTYPE html><html><head>{ARC_CSS}</head><body>無 {html_mod.escape(d)} 快照 <a href='/history'>返回</a></body></html>"
    ds_all = _snap_dates()
    i = ds_all.index(d) if d in ds_all else -1
    prev = _load_snap(ds_all[i - 1]) if i > 0 else None
    nxt = _load_snap(ds_all[i + 1]) if 0 <= i < len(ds_all) - 1 else None
    pc = {r["sid"]: r["close"] for r in prev["rows"]} if prev else {}
    nc = {r["sid"]: r["close"] for r in nxt["rows"]} if nxt else {}
    no = {r["sid"]: r.get("px0") for r in nxt["rows"]} if nxt else {}
    rows = []
    for r in snap["rows"]:
        base = pc.get(r["sid"]) or r["px0"]
        r["dret"] = (r["close"] / base - 1) * 100 if base else None
        n = nc.get(r["sid"])
        r["nret"] = (n / r["close"] - 1) * 100 if (n and r["close"]) else None
        op = no.get(r["sid"])
        r["ngap"] = (op / r["close"] - 1) * 100 if (op and r["close"]) else None
        rows.append(r)
    rows.sort(key=lambda r: -(r["dret"] if r["dret"] is not None else -99))
    nrank = {r["sid"]: k + 1 for k, r in enumerate(
        sorted([r for r in rows if r["nret"] is not None], key=lambda r: -r["nret"]))}
    grank = {r["sid"]: k + 1 for k, r in enumerate(
        sorted([r for r in rows if r["ngap"] is not None], key=lambda r: -r["ngap"]))}
    trs = []
    for k, r in enumerate(rows, 1):
        def pct(v):
            if v is None:
                return "<td class='dim'>—</td>"
            return f"<td class='{'up' if v > 0 else 'dn' if v < 0 else ''}'>{v:+.2f}%</td>"
        unm = r["sid"] in RET_UNM
        share = (r["ret2"] / r["tot"] * 100) if (r["tot"] and not unm) else None
        trs.append(
            f"<tr><td>{k}</td><td class='nm'>{r['sid']} {r['name']}</td>"
            f"<td>{r['close']:g}</td>" + pct(r["dret"])
            + f"<td class='{'up' if r['big'] > 0 else 'dn' if r['big'] < 0 else ''}'>{r['big'] / 1e8:+.2f}</td>"
            + (f"<td class='{'up' if r['big'] > 0 else 'dn'}'>{r['big'] / r['tot'] * 100:+.1f}%</td>"
               if r['tot'] else "<td class='dim'>—</td>")
            + (f"<td>{share:.1f}%</td>" if share is not None else "<td class='dim'>不可測</td>")
            + f"<td>{r['tot'] / 1e8:.1f}</td>"
            + f"<td>{r['sig1'] or ''}</td>"
            + f"<td>{r['sig2'] or ''}</td>"
        )
        # 次日兩欄
        if r["ngap"] is None:
            trs[-1] += "<td class='nx dim'>—</td><td class='nx dim'>—</td>"
        else:
            gcl = 'up' if r['ngap'] > 0 else 'dn' if r['ngap'] < 0 else ''
            gk = grank.get(r['sid'])
            gk_cl = ('top5' if gk and gk <= 5 else
                     'bot5' if gk and gk > len(grank) - 5 else '')
            trs[-1] += (f"<td class='nx {gcl}'>{r['ngap']:+.2f}%</td>"
                        f"<td class='nx {gk_cl}'>{gk or '—'}</td>")
        if r["nret"] is None:
            trs[-1] += "<td class='nx dim'>—</td><td class='nx dim'>—</td></tr>"
        else:
            cl = 'up' if r['nret'] > 0 else 'dn' if r['nret'] < 0 else ''
            rk = nrank.get(r['sid'])
            rk_cl = ('top5' if rk and rk <= 5 else
                     'bot5' if rk and rk > len(nrank) - 5 else '')
            trs[-1] += (f"<td class='nx {cl}'>{r['nret']:+.2f}%</td>"
                        f"<td class='nx {rk_cl}'>{rk or '—'}</td></tr>")
    nav_p = f"<a href='/day?d={ds_all[i-1]}'>←{ds_all[i-1]}</a>" if i > 0 else ""
    nav_n = f"<a href='/day?d={ds_all[i+1]}'>{ds_all[i+1]}→</a>" if 0 <= i < len(ds_all) - 1 else ""
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=0.7'>"
            f"<title>{d} 收盤</title>{ARC_CSS}</head><body>"
            f"<h3>{d} 收盤快照 &nbsp;{nav_p} <a href='/history'>索引</a> {nav_n}</h3>"
            f"<div class='meta'>點欄位標題可排序(再點反向) · 預設=當日漲跌 · 漲跌基準=前一快照收盤(缺則用當日首價) · "
            f"💎欄=當日核心/強訊號觸發數 · 深底色兩欄=<b>次日</b>漲跌與排名(次日收盤自動補)</div>"
            f"<table><thead><tr><th>#</th><th class='stk'>股票</th><th>收盤</th><th>當日%</th>"
            f"<th>全日大戶(億)</th><th title='大戶淨流÷成交,127日驗證次日排名IC+0.043/t3.2=最佳排序鍵'>大戶佔比</th><th>散戶參與</th><th>成交(億)</th><th>💎</th><th>💎💎</th>"
            f"<th class='nx' title='次日開盤vs今收(隔夜跳空);127日:大戶佔比→開盤IC+0.097/t7.1=最可預測段'>次日開%</th><th class='nx'>開名</th><th class='nx' title='次日收對收;=開盤慣性−日內回吐的殘影'>次日%</th><th class='nx'>收名</th></tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table>"
            + SORT_JS + "</body></html>")


_HELP_GROUPS = [
    ("識別", [
        ("股票", "中文名＋細分產業標籤(灰字)。細分產業為45檔手動策展,比大類細一層(如半導體再分晶圓代工/封測/DRAM/矽晶圓;PCB再分CCL/ABF載板/軟板)。",
         "同族群連動看輪動:今天CCL(聯茂/台燿/台光電)整片被買、ABF(南電/景碩/欣興)整片被賣。高價股(≥2000元)預設隱藏,因1張即≥500萬、散戶欄不可測。"),
    ]),
    ("價格", [
        ("價", "最新成交價。顏色＝對前一交易日收盤:紅漲綠跌(台股慣例,與美股相反)。", "一眼看今日相對昨收是紅是綠。"),
        ("對昨收", "現價−昨收 的金額與%,即專業看盤軟體的主報價。▲紅=漲、▼綠=跌。", "這才是一般人講的『今天漲跌多少』。金額看跳動幅度、%看比例。"),
        ("期貨", "個股期貨即時價,小字=基差%(期貨/現股−1,正=期貨溢價)。紅=溢價、綠=逆價差。資料源:個股期貨ws(Phase2)——上線前顯示—。", "盤前期現貨背離、盤中基差都看這欄。緊接在『價』旁邊方便對照。"),
        ("日內%", "現價/今日開盤−1。盤中相對『開盤』的走勢,與對昨收互補。", "跳空開高後拉回:對昨收仍紅、日內%卻綠=開高走低。兩欄一起讀分辨跳空 vs 盤中動能。"),
        ("盤前試撮", "collector 08:45~09:00 收的試撮價,小字=跳空%(vs昨收);09:00開盤後凍結為最終試撮。", "開盤前看試撮價預判開盤;開盤後此欄空窗屬正常(只有盤前有值)。試撮價會被大單掛撤誘導,非確定開盤價。"),
        ("試撮買賣", "盤前試撮的買一價/賣一價 與 撮合張數(bid/ask×張)。", "看盤前買賣一價差(真空還牆)與會撮多少張。與『盤前試撮』同源,只在08:45~09:00有值。"),
        ("30分bps", "近30分窗價格報酬(1bps=0.01%)。主尺度。", "驗證格(噴後/跌深接/勿追)判斷的價格軸。"),
        ("順逆市", "個股30分方向 vs 市場45檔等權30分方向。順漲/順跌=同向;逆強=大盤跌它漲;逆弱=大盤漲它跌。", "市場是個股報酬最強控制變數——讀任何訊號前先看這格,逆強逆弱才有個股alpha。"),
        ("5分bps", "近5分窗價格報酬。最短尺度、雜訊最大。", "只作即時異動參考,別單獨下判斷。"),
        ("距漲停", "現價距漲停價%。", "接近0=快漲停;配合漲停排隊格(撐滿30分才買)。"),
        ("即時RS", "個股日內% − 宇宙日內%(百分點)。負(綠)=相對壓著(彈簧);＞+1(黃)=已彈開。", "隔夜挑股:壓著的彈簧優先。軟否決:日線弱∧已彈=毒格−31bps。"),
    ]),
    ("大戶三尺度(單筆≥1000萬;全系統核心)", [
        ("5分大戶", "最新完成5分窗的大戶淨買金額(萬)。最短窗、最即時。", "看『現在』誰在進出;易反覆,配30分看。"),
        ("30分大戶", "近30分滾動窗大戶淨額(萬)。主尺度。", "驗證格的大戶軸。與價格軸(30分bps)交叉:跌×大戶買=跌深接。"),
        ("全日大戶", "開盤累加至今的大戶淨額(億),收盤即全日淨額。最重要。", "÷成交=佔比%(隔夜排序主鍵)。三尺度並排看背離:短窗買∧全日仍賣=誘多/出貨(今天國巨、上週五買方今天全倒都是此型)。"),
        ("佔比%", "全日大戶淨額 ÷ 成交金額。", "隔夜今收→明開跳空最強預測(IC+0.097/t7.1)。中市值重殺看這欄不看絕對金額(旺矽絕對−5.8億進不了榜、佔比−11%才顯眼)。"),
        ("全日散戶", "全日散戶(1張∧＜500萬)淨額(億)。", "散戶大買常是出貨對手方;參與過高(黃)=毒藥側。"),
    ]),
    ("散戶(1張∧＜500萬;買賣拆兩側)", [
        ("散買30 / 散買%", "30分 / 5分窗散戶『買方』參與(散戶買額÷窗總額)。毒藥側。", "只買不賣格−11bps/t−4.9;≥5%標黃=散戶在追,留不到收盤。"),
        ("散賣30 / 散賣%", "散戶『賣方』參與。投降側,無資訊(less bad)。", "散戶賣≠訊號;真正毒的是散戶買。"),
        ("Δ參與30", "30分散戶總參與較前一窗的變化。", "跳升=散戶剛湧入(常見於急拉尾聲)。"),
        ("5分散戶淨", "5分窗散戶淨額(萬)。", "最短窗散戶方向,雜訊大。"),
    ]),
    ("隔夜挑股因子(收盤導向,13:20後看)", [
        ("壓縮1h", "現價距尾盤1小時均線%。負=壓著(彈簧)。", "隔夜挑股鍵。壓縮∧站上5日線隔夜+93.8/t5.10;噴高(正值大)=甜蜜點流失。"),
        ("日線", "日線趨勢(截最近日收盤):↑多=站上5日均線,↓空=跌破;附5日動能%。", "壓縮回檔在日線多頭股(↑)才是買點、空頭股(↓)是接刀。做空池要求↓;上週五三檔↑做空腿全被軋。"),
        ("章", "連3買=持續章(挑股加分)/⚠同賣=今晚勿抱(−28/t−6)/↓弱開=明日弱開候選/🔻=跌回昨日午後低點(出場警戒)。", "尾盤挑股與勿抱名單的快速標記。"),
    ]),
    ("委託簿(換算成分鐘量再讀,不看買賣比)", [
        ("買簿 / 賣簿", "五檔委買 / 委賣量換算成『幾分鐘的成交量』。", "＜3分=真空、＞10分=牆。絕對張數/金額當牆是錯的(欣興950誤判教訓),要除以流速。"),
    ]),
    ("風險/量能/排名", [
        ("波動分數", "0–100:融資日變動幅度歷史分位＋借券日變動幅度歷史分位的平均(不分方向)。🌊🌊≥92 🌊≥86 藍字≥80。", "只預測隔日盤中『振幅』(t3.40),對淨報酬/跳空/量能無解釋力,非方向訊號。"),
        ("振幅%", "高波動分數=20日日均振幅%((高−低)/收盤),來自 calib 選股校準。金字≥7%(高波動)、灰＜5%(偏低)。與『波動分數』不同:那是融資/借券的T-1預測,這是實際已實現振幅。", "這是股票進本系統的門檻指標——宇宙中位約6.5%,越高日內波段越大、越適合大戶/散戶流策略。用來檢視/汰換成員。"),
        ("量能x", "5分窗成交金額 ÷ 近5日同時段中位(rvol)。", "≥5=爆量。三合一吸貨窗(大戶≥3千萬∧rvol≥5∧散＜25%)的量能條件。"),
        ("R30 / R日 / R5 / R熱", "30分/全日/5分大戶淨買排名,R熱=5分成交金額排名。箭頭▲▼=較上窗名次變化。", "注意力分流用,非訊號——個股排名持續性已檢定不可持續。"),
    ]),
    ("訊號計分(只收已驗證格)", [
        ("跌訊", "命中數＋明細:噴後(30分漲≥150,峰後均−32)·勿追(漲×參與跳升或大戶賣,−5~−9.6,趨勢日−32)·機構賣(30分大戶賣≥3千萬∧散＜15%)·散急拉(5分漲＞20∧散買≥5%)·同賣(大戶賣∧散戶賣,−28/t−6)·破昨低。≥2粗體。",
         "空方計分;每格都附127日基準率,粗體=多格共振。"),
        ("漲訊", "抬轎(30分大戶買≥3千萬∧散＜45%,唯一正格)·💎逆勢純機構(+24~29/t5.2)·深接(跌深大戶接RVOL≥0.5,+11~14/t3.4)·佔壓(全日佔比≥10%∧壓縮＜0,隔夜雙鍵)·連3買。≥2粗體。",
         "多方計分;佔壓是收盤導向、盤中格是條件式基準率。"),
        ("旗標", "綜合即時旗標文字:💎逆勢純機構、🟢跌深大戶接、⚠勿追、💎💎逆勢強等。", "當窗最該注意的一句話。"),
    ]),
]


def render_help():
    css = ("body{background:#0d1117;color:#c9d1d9;font:13px/1.7 -apple-system,'PingFang TC',"
           "sans-serif;margin:0;padding:14px 16px 40px}"
           "h2{font-size:17px;margin:2px 0 4px}h3{font-size:14px;color:#79c0ff;margin:18px 0 4px;"
           "border-bottom:1px solid #30363d;padding-bottom:3px}"
           "a{color:#79c0ff}.meta{color:#8b949e;font-size:12px;margin-bottom:10px}"
           "table{border-collapse:collapse;width:100%;max-width:1000px;margin:2px 0}"
           "td{border-bottom:1px solid #21262d;padding:5px 8px;vertical-align:top}"
           "td.c{color:#e6edf3;font-weight:600;white-space:nowrap;width:96px}"
           "td.d{color:#adbac7;width:44%}td.u{color:#8b949e}"
           ".up{color:#ff7b72}.dn{color:#3fb950}.leg{background:#161b22;border:1px solid #30363d;"
           "border-radius:6px;padding:8px 12px;margin:10px 0;font-size:12px;max-width:1000px}")
    parts = [f"<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>欄位說明</title><style>{css}</style></head><body>"
             f"<h2>📖 大戶45檔儀表板 · 欄位說明</h2>"
             f"<div class='meta'><a href='/'>← 回儀表板</a>　每欄:定義(怎麼算) / 怎麼讀(用途)。"
             f"顏色慣例:<span class='up'>紅=漲/正值</span>、<span class='dn'>綠=跌/負值</span>(台股慣例)、灰=無資料或不可測。</div>"
             f"<div class='leg'><b>表頭顏色</b>=尺度分層:<span style='color:#e3b341'>黃=5分窗</span>·"
             f"<span style='color:#79c0ff'>藍=30分窗(主尺度)</span>·<span style='color:#d2a8ff'>紫=全日/隔夜</span>。"
             f"<b>列的每5列一條粗分隔線</b>,方便橫向對到同一檔;滑鼠移到列上會整列highlight。</div>"]
    for title, rows in _HELP_GROUPS:
        parts.append(f"<h3>{html_mod.escape(title)}</h3><table>")
        for col, dfn, howto in rows:
            parts.append(f"<tr><td class='c'>{html_mod.escape(col)}</td>"
                         f"<td class='d'>{html_mod.escape(dfn)}</td>"
                         f"<td class='u'>{html_mod.escape(howto)}</td></tr>")
        parts.append("</table>")
    parts.append("<div class='leg' style='margin-top:18px'><b>一句紀律</b>:盤中只在已驗證格觸發時喊方向、"
                 "且附數字＋基準率＋t;無觸發＝棄權。方向重倉判斷留到收盤(隔夜候選才進OOS記分)。"
                 "詳見 docs/biglot-broadcast-protocol.md。</div>")
    parts.append("</body></html>")
    return "".join(parts)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path == "/frag":
            body = PAGE["frag"].encode("utf-8")
        elif path == "/history":
            body = render_history().encode("utf-8")
        elif path == "/help":
            body = render_help().encode("utf-8")
        elif path == "/day":
            d = qs.split("d=")[-1][:10] if "d=" in qs else ""
            body = render_day(d).encode("utf-8")
        else:
            body = SHELL.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 手機瀏覽器(尤其 Safari over Tailscale)會積極快取整份 HTML,
        # 導致看到舊紀律條+卡在「載入中…」。強制不快取。
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _in_market():
    n = datetime.now(TZ)
    return n.weekday() < 5 and "08:55" <= n.strftime("%H:%M") <= "13:35"


def loop():
    done_close = False
    while True:
        try:
            if _in_market():
                ingest()
                render()
                done_close = False
            elif not done_close:
                ingest()          # 收盤後補跑一次定格,之後停工
                render()
                snapshot_day()
                try:
                    _oos_update_at_close()
                except Exception:
                    pass
                done_close = True
            elif _refresh_vol_risk_if_needed():
                # 盤前/盤後定格期間:tick 沒得更新,但 T-1 籌碼分數只要 DB 有新資料
                # 就該顯示,不用等開盤——重繪一次讓「盤後定格」頁面秀出當天分數
                render()
        except Exception as e:
            PAGE["frag"] = f"<div class='meta'>render error: {html_mod.escape(str(e))}</div>"
        time.sleep(REFRESH_SEC if _in_market() else 300)


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    print(f"biglot dashboard on :{PORT}")
    ThreadingHTTPServer(("", PORT), H).serve_forever()
