#!/usr/bin/env python3
"""大戶-散戶即時儀表板 · 唯讀 · 自帶HTTP(:8771) · 5秒增量更新。

  PYTHONPATH=src .venv/bin/python scripts/research/biglot_dashboard.py
  瀏覽 http://100.81.2.33:8771 （Tailscale 內網）

資料源：biglot-live-watch raw jsonl（逐筆）＋ watchlist_books jsonl（五檔）。
方法論同 scripts/research/biglot_5m_monitor.py（互斥三桶/競價與跳量排除/
獨漲勿追旗標/散戶參與度），詳見該檔 docstring。
不動 :8770 live UI（tmf-sim-server），完全獨立、無下單路徑。
"""
from __future__ import annotations

import sys

# 2026-09-27：直接執行(python3 biglot_dashboard.py)時，這支檔案在 sys.modules 裡叫
# "__main__"，不叫 "biglot_dashboard"。底下 biglot/*.py 子模組全部 `import biglot_dashboard`
# ——如果沒有這一行，Python 找不到 sys.modules["biglot_dashboard"]，會把這支檔案「當成
# 另一個模組」重新執行一次，在還沒執行完的 import 敘述式上撞出循環 import
# ImportError(2026-09-27 批次二上線時真的炸過一次，正式站台因此中斷)。這裡手動把
# 「正在執行的這個模組」也註冊成 "biglot_dashboard"，兩個名字指向同一個模組物件，
# 之後任何 `import biglot_dashboard` 都會直接命中、不會觸發第二次執行。
# 用 setdefault：正常當套件匯入時 __name__ 已經是 "biglot_dashboard"，這行是無害的 no-op。
sys.modules.setdefault("biglot_dashboard", sys.modules[__name__])

import json
import threading
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")
from stock_db import DATA_DIR  # noqa: E402
# 2026-09-27 重構全部五批完成(見 docs/biglot-refactor-roadmap.md)：原本 70+ 個函式
# /類別逐批搬進 biglot/ package，這支檔案現在只剩「模組層級常數/設定＋一次性初始化
# 呼叫」，函式本體全部不在這裡了。以下這些匯入雖然這支檔案自己的程式碼不再直接
# 呼叫，但仍然要留著，因為它們是「一次性初始化呼叫」的結果會存進模組層級變數
# （ATR_STATE/DAILY_TREND/PE_TABLE 等），供 ingest() 之後的 `global X; X = ...`
# 覆寫，或是其他模組透過 `biglot_dashboard.X` 屬性存取讀寫（不是
# `from biglot_dashboard import X` 那種一次性具名匯入，見 biglot/state.py 檔頭
# 說明的 stale-reference 原理）：
#   - ST：核心可變狀態單例（15+ 個已搬移模組共用同一個物件）
#   - datetime：這支檔案自己的 `datetime` 名字會被 Phase 0 測試工具凍結時鐘置換，
#     其他模組故意透過 `biglot_dashboard.datetime.now(...)` 存取而不是自己
#     `from datetime import datetime`，才能吃到同一份凍結時鐘
from biglot.state import ST  # noqa: E402
from biglot.utils import _par30, _b30n, _b5n  # noqa: E402
from biglot.reference_loaders import (  # noqa: E402
    _load_daily_trend, _load_key_line, _load_atr_state, _load_prev_close_db,
    _load_hist, _load_etf981_holdings, _load_pe_peer, _load_vixtwn, _load_xq_style,
    _load_holder_big800,
)
from biglot.paper_trading import _paper_blank  # noqa: E402

TZ = timezone(timedelta(hours=8))
PORT = 8771
BIG_AMT = 10_000_000     # 2026-09-12 使用者定案:大戶=真大戶(>=1000萬)
RETAIL_CAP = 5_000_000   # 散戶定義不動:1張且<500萬(高價股1張大單歸中實不歸散戶)
GAP_JUMP_AMT = 500_000_000
GAP_SECONDS = 90
REFRESH_SEC = 1   # 背景 ingest(~2ms)+render(~23ms) 每秒約 2.5% 單核,client 抓 ~66KB/s,mini 綽綽有餘

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
FUT_MINI = {r["sid"] for r in _cal["universe"] if r.get("is_mini")}   # 期貨為小型契約(100 股):期貨買/賣欄標「小」、1 口名目=價×100
# 公司描述/看盤標籤(純靜態;主表名稱 hover 提示 + 詳情頁區塊)。查無則不顯示。
try:
    from biglot_stock_info import INFO as STOCK_INFO, BLOCKS as STOCK_BLOCKS, tooltip as _stock_tip
except Exception:  # noqa: BLE001 -- 資料檔缺失不影響儀表板
    STOCK_INFO, STOCK_BLOCKS = {}, {}
    def _stock_tip(_sid):
        return ""
# 員工平均營業額(2026-09-27 jack 交辦):暫時沿用 XQ 截圖數字,FinMind查無員工人數對應dataset。
try:
    from xq_manual_metrics import EMPLOYEE_REVENUE_XQ, EMPLOYEE_REVENUE_ASOF
except Exception:  # noqa: BLE001
    EMPLOYEE_REVENUE_XQ, EMPLOYEE_REVENUE_ASOF = {}, ""
# 高波動分數 = 20日日均振幅%((高−低)/收) ,來自 calib;越高越適合本系統的日內波段
AMP20 = {r["sid"]: r.get("amp20") for r in _cal["universe"]}
PREOPEN: dict = {}   # 盤前試撮快照 sid->{px,bid,ask,size,t}(collector preopen_*.json,08:30~09:00)
FUT_PX: dict = {}    # 個股期貨即時 sid->{px,sym,t,bid,bidsz,ask,asksz,bt}(futprice_*.json;bid/ask 來自 ws books)
WRT: dict = {}       # 權證多空 sid->{call_30,put_30,call_day,put_day,...}(warrantflow_*.json;MIS 輪詢,描述性未回測)
TX_SER: dict = {"day": None, "t": [], "px": [], "off": 0}
TX_LAST: dict = {"z": None}   # 台指 1 分 z 最近值(_tx_panel 每秒更新,淨分欄用)
WRT_MIN: dict = {"day": None, "off": 0, "data": {}}   # 權證逐筆 → sid -> {HH:MM: 簽號淨額(元,購+/售−×主動方)},供各圖紫線
AGG: dict = {}                                        # 36 檔分鐘加總累計(大戶/散戶/權證),供台指面板;render_grid_frag 每 5 秒更新


#: 頁首可編輯筆記(2026-09-24):存在資料目錄,不進 git;沒有檔案時顯示預設紀律條
NOTES_PATH = DATA_DIR.parent / "cache" / "biglot_live_watch" / "dashboard_notes.html"
DEFAULT_NOTES = "（自由書寫的筆記區：點這裡開始輸入；Enter 換行，停止輸入 1.5 秒自動儲存，Ctrl/Cmd+S 立即儲存。紀律條全文見 📖 欄位說明。）"


#: 每檔筆記(2026-09-24):sid -> {"txt": 純文字, "t": "HH:MM:SS", "d": "YYYY-MM-DD"};存資料目錄,不進 git
STOCK_NOTES_PATH = DATA_DIR.parent / "cache" / "biglot_live_watch" / "stock_notes.json"
STOCK_NOTES: dict = {}
try:
    STOCK_NOTES = json.loads(STOCK_NOTES_PATH.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    STOCK_NOTES = {}


# ---- 持倉監控(jack 2026-09-25):手動標記持倉,持有中每輪重算 V2.5 當「持倉分」,出場提示依 127 日面板對照
#      (scratch/exit_rules_2026-09-25.txt):分數≤0 出 +23.1/+23.2(t5.2/6.2,均持 11 分,SD 94)、壞標籤出 +24.5/+26.0、
#      到期 60 分;移動停利/硬停損/破昨低出場皆較差,不做。純提示,不送單。
HOLDS_PATH = DATA_DIR.parent / "cache" / "biglot_live_watch" / "holds.json"
try:
    HOLDS: dict = json.loads(HOLDS_PATH.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    HOLDS = {}
HOLD_BAD = ("散戶虛拉", "噴後過熱", "急跌·竭盡∧散戶接")


# 固定產業鏈排序(避免5秒隨大戶流跳位):相近產業相鄰,半導體上游→下游→非半導體。
# 產業交界畫粗線(band)。查無的股票排最後。
_CLUSTERS = [
    ("矽晶圓", ["6182", "6488", "5483", "3532"]),
    ("晶圓代工", ["2303", "6770"]),
    ("記憶體", ["2344", "2408", "2337", "3006"]),
    ("封測", ["2449", "6147", "3374"]),
    ("半導體測試設備/探針卡", ["6223"]),
    ("化合物半導體", ["3105", "2455"]),
    ("被動元件", ["2327", "2492", "6173", "3042"]),
    ("CCL銅箔基板", ["6213", "6274", "2383"]),
    ("ABF載板", ["8046", "3189", "3037"]),
    ("PCB/載板", ["2368", "4958", "8039", "8358"]),
    ("散熱", ["3324", "3653", "3017"]),
    ("光學", ["3406", "3008"]),
    ("光通訊/光模組", ["3081"]),
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


HIST_BIG, PREV_CLOSE, Y_PMLOW, UNI5 = _load_hist()


PREV_CLOSE.update(_load_prev_close_db())   # 官方收盤優先,快照昨收僅作 fallback


DAILY_TREND = _load_daily_trend()


KEY_LINE = _load_key_line()


HOLDER_BIG800 = _load_holder_big800()   # sid -> (大戶≥800張比例%, as_of_date);集保週頻,啟動載一次即可


PE_TABLE, PE_PEERS, PE_GEN, PE_EPS = _load_pe_peer()


ETF981_HOLD, ETF981_ASOF, ETF981_PREV_ASOF = _load_etf981_holdings()

ATR_N = 14            # Wilder(1978)慣例期數,節目原話「5或20皆可」,14是業界折衷慣例
ATR_SQUEEZE_LOOKBACK = 120
ATR_SQUEEZE_PCTL = 0.30
ATR_BREAKOUT_K = 1.5  # 節目原話:「這個慣性超過1.5倍,我覺得不合理,你要立刻出場,因為方向改變了」


ATR_STATE = _load_atr_state()


XQ_STYLE = _load_xq_style()
VIXTWN = _load_vixtwn()

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


VOLRISK, VOLRISK_DATE = {}, None


OOS_FILE = DATA_DIR.parent / "cache" / "biglot_live_watch" / "oos_scoreboard.json"


SHELL = f"""<!DOCTYPE html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=0.6">
<title>大戶{len(NAMES)}檔儀表板</title><style>
body{{background:#0d1117;color:#c9d1d9;font:12px/1.5 -apple-system,'PingFang TC',monospace;margin:8px}}
h3{{margin:4px 0;font-size:14px}}
.meta{{color:#8b949e;font-size:11px;margin-bottom:6px}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}
th,td{{padding:2px 7px;text-align:right;border-bottom:1px solid #21262d}}
th{{position:sticky;top:0;z-index:2;background:#161b22;color:#8b949e;font-weight:600;cursor:pointer}}
th[data-nosort]{{cursor:default}}
th.g5{{color:#e3b341}} th.g30{{color:#79c0ff}} th.gd{{color:#d2a8ff}}
td.nm{{position:sticky;left:0;background:#0d1117;z-index:1;text-align:left;font-weight:600;color:#e6edf3}}
th.stk{{position:sticky;left:0;z-index:3}}
/* 凍結欄(2026-09-29 jack 交辦):現價/期貨買/期貨賣跟股票欄一起固定,橫向捲動不跟著跑。
   left 是動態的(每欄實際渲染寬度不同),交給 JS 量測後寫進 style.left,這裡只定 position/z-index/背景。 */
th.frz{{position:sticky;z-index:3}}
td.frz{{position:sticky;z-index:1;background:#0d1117}}
tbody tr td{{border-bottom:1px solid #1c2128}}
tbody tr.band td{{border-bottom:2px solid #454d57}}
table.sorted tbody tr.band td{{border-bottom:1px solid #1c2128}}  /* 手動排序時產業交界粗線失去意義,隱藏 */
.sortind{{color:#58a6ff}}
tbody tr:hover{{background:#1c2635 !important}}
tbody tr:hover td.nm{{background:#1c2635 !important}}
tbody tr:hover td.frz{{background:#1c2635 !important}}
.cat{{color:#8b949e;font-weight:400;font-size:10px;margin-left:4px}}
th .sub{{display:block;font-size:9px;font-weight:400;color:#8b949e;margin-top:1px}}
.up{{color:#ff7b72}} .dn{{color:#3fb950}} .dim{{color:#484f58}}
.lup{{background:#d1242f;color:#fff;font-weight:700}}  /* 漲停:紅底白字(台股慣例) */
.ldn{{background:#1a7f37;color:#fff;font-weight:700}}  /* 跌停:綠底白字 */
.nlup{{color:#ff7b72;font-weight:700}} .nldn{{color:#3fb950;font-weight:700}}  /* 接近漲/跌停:粗紅/綠 */
.hit{{text-decoration:underline;text-decoration-color:#f2cc60;text-decoration-thickness:2px;text-underline-offset:3px}}  /* 期貨成交價落在的那一邊:黃底線 */
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
.txp{{flex:0 0 700px;width:700px;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 10px;margin-bottom:6px;font-size:12px;line-height:1.6;position:relative}}
#txtip{{position:absolute;display:none;background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:1px 6px;font-size:11px;color:#e6edf3;pointer-events:none;z-index:5;white-space:nowrap}}
#txline{{position:absolute;display:none;width:1px;background:#8b949e;pointer-events:none;z-index:4}}
#pnltip{{position:absolute;display:none;background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:1px 6px;font-size:11px;color:#e6edf3;pointer-events:none;z-index:5;white-space:nowrap}}
#pnlline{{position:absolute;display:none;width:1px;background:#8b949e;pointer-events:none;z-index:4}}
#notes{{outline:none;min-height:60px;padding:2px 4px;border-radius:4px}} #notes:focus{{background:#0d1117;box-shadow:0 0 0 1px #388bfd}}
#nstat{{color:#8b949e;font-size:10px;text-align:right}}
td.snote{{text-align:left;min-width:170px;white-space:nowrap;font-weight:400}}
.ne{{display:inline-block;min-width:130px;max-width:320px;overflow:hidden;text-overflow:ellipsis;vertical-align:bottom;white-space:nowrap;outline:none;padding:0 3px;border-radius:3px;color:#e6edf3}} .ne:empty::before{{content:'…';color:#484f58}}
.ne:focus{{max-width:none;white-space:normal;overflow:visible}}
.ne:focus{{background:#0d1117;box-shadow:0 0 0 1px #388bfd}} .nt{{margin-left:4px;font-size:9px;white-space:nowrap}}
</style></head><body>
<h3>大戶-散戶 {len(NAMES)}檔即時儀表板
<span id="clk" style="font-size:14px;color:#e3b341;margin-left:10px;font-variant-numeric:tabular-nums">--:--:--</span>
<a href="/history" style="font-size:11px;margin-left:8px;color:#79c0ff">歷史分頁</a>
<a href="/help" style="font-size:11px;margin-left:8px;color:#79c0ff">📖 欄位說明</a>
<a href="/grid" target="_blank" style="font-size:11px;margin-left:8px;color:#79c0ff">▦ 36檔圖形總覽</a>
<a href="/timeline" target="_blank" style="font-size:11px;margin-left:8px;color:#79c0ff">⏱ 每30分快照</a></h3>
<div style="display:flex;gap:14px;align-items:stretch">
<details class="disc" open style="flex:1 1 auto;margin-bottom:6px"><summary>📝 筆記（自由書寫 · 自動儲存）</summary>
<div id="notes" contenteditable="true" spellcheck="false">{{NOTES}}</div>
<div id="nstat">未編輯</div></details>
<div id="pnlp" class="txp"><div id="pnlbody"><span class="dim">期貨留倉損益 載入中…</span></div><div id="pnltip"></div><div id="pnlline"></div></div>
<div id="txp" class="txp"><div id="txbody"><span class="dim">台指近月 載入中…</span></div><div id="txtip"></div><div id="txline"></div></div>
</div>
<div id="app"><div class="meta">載入中…</div></div>
<script>
const R={REFRESH_SEC}000;
function clk(){{const d=new Date();const p=n=>String(n).padStart(2,'0');
  document.getElementById('clk').textContent=p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());}}
clk(); setInterval(clk,1000);   // 每秒跳動的即時時鐘(到秒)
async function tick(){{
  try{{
    const r=await fetch('/frag?_='+Date.now());
    const t=await r.text();
    const ae=document.activeElement;
    if(window.__noteEditing || (ae && ae.classList && ae.classList.contains('ne'))){{setTimeout(tick,R);return;}}   // 正在編輯個股筆記:暫停換表,離開格子後恢復
    document.getElementById('app').innerHTML=t;   // 只換內容,不重載整頁,不閃爍
    if(window.__applySort){{window.__applySort();}}   // 表格每秒被整包換掉,排序狀態要在換完後重套用(見下方排序 IIFE)
    if(window.__applyFreeze){{window.__applyFreeze();}}   // 同理,凍結欄的 left 偏移也要在換完內容後重新量測套用
    const s=document.getElementById('txsrc'); if(s){{document.getElementById('txbody').innerHTML=s.innerHTML;}}   // 台指面板搬到右上(tip/line 元素保留)
    const ps=document.getElementById('pnlsrc'); if(ps){{document.getElementById('pnlbody').innerHTML=ps.innerHTML;}}   // 期貨損益面板同理搬到左上
    const c=document.getElementById('closed');
    if(c && c.dataset.closed==='1'){{setTimeout(tick,30000);return;}}   // 非交易時段改 30s 慢輪詢,08:30 自動恢復(不必重載頁面)
  }}catch(e){{}}
  setTimeout(tick,R);
}}
tick();
// 頁首筆記:contenteditable,停止輸入 1.5 秒或 Ctrl/Cmd+S 自動 POST /notes 存檔
(function(){{
  const n=document.getElementById('notes'), st=document.getElementById('nstat'); if(!n) return; let tm=null;
  const save=async()=>{{try{{const r=await fetch('/notes',{{method:'POST',body:n.innerHTML}});
    st.textContent=(r.ok?'已儲存 ':'儲存失敗 ')+new Date().toTimeString().slice(0,8);}}catch(e){{st.textContent='儲存失敗';}}}};
  n.addEventListener('input',()=>{{st.textContent='編輯中…';clearTimeout(tm);tm=setTimeout(save,1500);}});
  n.addEventListener('keydown',e=>{{if((e.metaKey||e.ctrlKey)&&e.key==='s'){{e.preventDefault();clearTimeout(tm);save();}}}});
}})();
// 個股筆記:事件委派到 #app(表格每秒重繪);input 去抖 1.5s / blur / Ctrl+S → POST /stocknote,回傳最後編輯時間寫進同格小字
(function(){{
  const app=document.getElementById('app'); const tm={{}}; window.__noteEditing=false;
  // 按下格子的瞬間就鎖住重繪(避免 fetch 回應剛好在 mousedown 與 focus 之間把格子換掉);focusout 解鎖
  app.addEventListener('mousedown',e=>{{if(e.target.closest&&e.target.closest('td.snote')){{window.__noteEditing=true;}}}});
  app.addEventListener('focusin',e=>{{const el=e.target.closest&&e.target.closest('.ne'); if(el){{window.__noteEditing=true; if(el.dataset.orig===undefined){{el.dataset.orig=el.innerText;}}}}}});
  app.addEventListener('click',e=>{{const td=e.target.closest&&e.target.closest('td.snote'); if(!td) return;
    const el=td.querySelector('.ne'); if(el && document.activeElement!==el){{el.focus();}}}});   // 點到格子空白處也進入編輯
  const save=async (el,force)=>{{const sid=el.dataset.sid; const nt=el.nextElementSibling;
    if(!force && el.innerText===(el.dataset.orig||'')){{return;}}   // 沒改動就不存(避免只是點進去也蓋掉編輯時間)
    el.dataset.orig=el.innerText;
    try{{const r=await fetch('/stocknote',{{method:'POST',body:JSON.stringify({{sid:sid,txt:el.innerText}})}});
      const j=await r.json(); if(nt){{nt.textContent=j.t;}}}}catch(e){{if(nt){{nt.textContent='儲存失敗';}}}}}};
  app.addEventListener('input',e=>{{const el=e.target.closest('.ne'); if(!el) return; const sid=el.dataset.sid;
    const nt=el.nextElementSibling; if(nt){{nt.textContent='編輯中…';}} clearTimeout(tm[sid]); tm[sid]=setTimeout(()=>save(el,true),1500);}});
  app.addEventListener('focusout',e=>{{const el=e.target.closest&&e.target.closest('.ne'); if(!el) return; clearTimeout(tm[el.dataset.sid]); save(el); setTimeout(()=>{{window.__noteEditing=false;}},200);}});
  app.addEventListener('keydown',e=>{{const el=e.target.closest&&e.target.closest('.ne'); if(!el) return;
    if((e.metaKey||e.ctrlKey)&&e.key==='s'){{e.preventDefault();clearTimeout(tm[el.dataset.sid]);save(el,true);}}
    if(e.key==='Escape'){{el.blur();}}}});
}})();
// 持倉按鈕:持/出 → POST /hold(純提示,不送單)
(function(){{const app=document.getElementById('app');
  app.addEventListener('click',async e=>{{const b=e.target.closest&&e.target.closest('.hbtn'); if(!b) return; e.preventDefault(); e.stopPropagation();
    try{{await fetch('/hold',{{method:'POST',body:JSON.stringify({{sid:b.dataset.sid,action:b.dataset.action}})}});}}catch(_){{}}}});
}})();
// 表格點標題排序(仿 XQ 全球贏家;2026-09-29 jack 交辦)。#app 每秒被整包 innerHTML 換掉,
// 不能像靜態頁那樣原地重排 <tr> 就結束——排序「狀態」記在這個閉包的變數裡(不受換頁影響),
// 事件也代理到 #app(不會被砍掉)而不是綁在每次都重生的 <th> 上。每次 tick() 換完內容後
// 呼叫 window.__applySort() 用同一份狀態重新排一次,體感上是「持續排序」。
// 排序鍵一律讀 <td data-sort> 屬性(渲染時後端已經把原始數值塞進去,不猜畫面文字格式)。
// 第一欄「股票」不是排序鍵——現行順序本來就是固定產業鏈排序,點它=清空排序狀態、回到這個預設順序。
(function(){{
  const app=document.getElementById('app');
  let sortIdx=null, sortDir=1;   // sortIdx=null → 預設順序(產業鏈);sortDir 1=大到小 −1=小到大
  function applySort(){{
    const table=app.querySelector('table'); if(!table) return;
    const theadRow=table.querySelector('thead tr'); const tbody=table.querySelector('tbody');
    if(!theadRow||!tbody) return;
    table.classList.toggle('sorted', sortIdx!==null);
    const ths=[...theadRow.children];
    if(sortIdx!==null && ths[sortIdx]){{
      ths[sortIdx].insertAdjacentHTML('beforeend', ' <span class="sortind">'+(sortDir>0?'▼':'▲')+'</span>');
    }}
    if(sortIdx===null) return;   // 預設順序:吃伺服器算好的原始列順序,不重排
    const rows=[...tbody.children];
    rows.sort((a,b)=>{{
      const av=a.children[sortIdx]&&a.children[sortIdx].dataset.sort, bv=b.children[sortIdx]&&b.children[sortIdx].dataset.sort;
      const an=(av===undefined||av==='')?null:parseFloat(av), bn=(bv===undefined||bv==='')?null:parseFloat(bv);
      if(an===null&&bn===null) return 0;
      if(an===null) return 1; if(bn===null) return -1;   // 缺值一律排最後,不受方向影響
      return sortDir*(bn-an);
    }});
    rows.forEach(r=>tbody.appendChild(r));
  }}
  app.addEventListener('click', e=>{{
    const th=e.target.closest&&e.target.closest('thead th'); if(!th) return;
    const theadRow=th.closest('tr'); const ths=[...theadRow.children]; const i=ths.indexOf(th);
    if(i===0){{sortIdx=null; sortDir=1; applySort(); return;}}   // 股票欄=回到預設順序
    if(th.dataset.nosort!==undefined) return;   // 不可排序欄(期貨買/賣·順逆大盤·訊號·隱形大戶·筆記)
    if(sortIdx===i){{sortDir=-sortDir;}} else {{sortIdx=i; sortDir=1;}}   // 同欄再點=反向,首次點=大到小
    applySort();
  }});
  window.__applySort=applySort;
}})();
// 凍結欄(2026-09-29 jack 交辦):股票/現價/期貨買/期貨賣共四欄橫向捲動時固定不動。股票欄(th.stk/td.nm)
// 本來就是 left:0 寫死不用量;後三欄(class=frz)的實際寬度會隨內容變動(價格位數/委託量字數),
// 沒辦法在 CSS 寫死 left,每次 #app 換完內容後量測一次目前的真實寬度、疊加計算 left 寫進 style。
(function(){{
  const app=document.getElementById('app');
  function applyFreeze(){{
    const table=app.querySelector('table'); if(!table) return;
    const stkTh=table.querySelector('th.stk'); if(!stkTh) return;
    let left=stkTh.getBoundingClientRect().width;
    const offsets=[];
    table.querySelectorAll('thead th.frz').forEach(th=>{{
      th.style.left=left+'px'; offsets.push(left); left+=th.getBoundingClientRect().width;
    }});
    table.querySelectorAll('tbody tr').forEach(tr=>{{
      const tds=tr.querySelectorAll('td.frz');
      tds.forEach((td,i)=>{{ if(offsets[i]!==undefined) td.style.left=offsets[i]+'px'; }});
    }});
  }}
  window.__applyFreeze=applyFreeze;
}})();
// 台指圖 hover:找最近取樣點,顯示 時間/價 + 垂直線(事件掛在容器上,svg 每秒被換掉也不用重綁)
(function(){{
  const box=document.getElementById('txp'), tip=document.getElementById('txtip'), ln=document.getElementById('txline');
  box.addEventListener('mousemove',e=>{{
    const svg=box.querySelector('svg'); if(!svg){{tip.style.display='none';ln.style.display='none';return;}}
    if(!svg._pts){{try{{svg._pts=JSON.parse(svg.dataset.pts);}}catch(_){{return;}}}}
    const r=svg.getBoundingClientRect(), b=box.getBoundingClientRect(), x=e.clientX-r.left;
    if(x<0||x>r.width||e.clientY<r.top||e.clientY>r.bottom){{tip.style.display='none';ln.style.display='none';return;}}
    let best=null,bd=1e9; for(const p of svg._pts){{const d=Math.abs(p[0]-x); if(d<bd){{bd=d;best=p;}}}}
    if(!best||bd>12){{tip.style.display='none';ln.style.display='none';return;}}
    let _th='台指 '+best[2]+'  <b>'+best[3].toLocaleString()+'</b>';
    if(best[4]!=null){{_th+='<br>36檔大戶 <span style="color:#ff7b72">'+best[4].toLocaleString()+'萬</span>'
      +' · 散戶 <span style="color:#58a6ff">'+best[5].toLocaleString()+'萬</span>'
      +' · 差 '+best[6].toLocaleString()+'萬';}}
    tip.innerHTML=_th; tip.style.display='block';
    const lx=r.left-b.left+best[0]; ln.style.left=lx+'px'; ln.style.top=(r.top-b.top)+'px'; ln.style.height=r.height+'px'; ln.style.display='block';
    tip.style.left=Math.min(lx+8,b.width-190)+'px'; tip.style.top=(r.top-b.top+best[1]-38)+'px';
  }});
  box.addEventListener('mouseleave',()=>{{tip.style.display='none';ln.style.display='none';}});
}})();
// 期貨留倉損益圖 hover(2026-09-29 jack 交辦):找最近取樣點,顯示時間+5檔各自損益+合計。
// data-pts 每點=[x, 時間, 合計, 檔1損益, 檔2損益, ...],data-names=5檔名稱陣列(跟 data-pts 順序對應)。
(function(){{
  const box=document.getElementById('pnlp'), tip=document.getElementById('pnltip'), ln=document.getElementById('pnlline');
  box.addEventListener('mousemove',e=>{{
    const svg=box.querySelector('svg'); if(!svg){{tip.style.display='none';ln.style.display='none';return;}}
    if(!svg._pts){{try{{svg._pts=JSON.parse(svg.dataset.pts); svg._names=JSON.parse(svg.dataset.names);}}catch(_){{return;}}}}
    const r=svg.getBoundingClientRect(), b=box.getBoundingClientRect(), x=e.clientX-r.left;
    if(x<0||x>r.width||e.clientY<r.top||e.clientY>r.bottom){{tip.style.display='none';ln.style.display='none';return;}}
    let best=null,bd=1e9; for(const p of svg._pts){{const d=Math.abs(p[0]-x); if(d<bd){{bd=d;best=p;}}}}
    if(!best||bd>12){{tip.style.display='none';ln.style.display='none';return;}}
    let _th=best[1]+' 合計 <b>'+(best[2]!=null?best[2].toLocaleString():'—')+'</b>';
    const names=svg._names||[];
    for(let k=0;k<names.length;k++){{
      const v=best[3+k];
      _th+='<br>'+names[k]+' '+(v!=null?v.toLocaleString():'—');
    }}
    tip.innerHTML=_th; tip.style.display='block';
    const lx=r.left-b.left+best[0]; ln.style.left=lx+'px'; ln.style.top=(r.top-b.top)+'px'; ln.style.height=r.height+'px'; ln.style.display='block';
    tip.style.left=Math.min(lx+8,b.width-190)+'px'; tip.style.top=Math.max(0,e.clientY-b.top-70)+'px';
  }});
  box.addEventListener('mouseleave',()=>{{tip.style.display='none';ln.style.display='none';}});
}})();
</script>
</body></html>"""


ICEBERG_EXHAUST_FRAC = 0.15    # 量降到≤原量15%(或≤5張)才算「耗盡」(Frey&Sandås:trade exhausts all displayed depth)
ICEBERG_EXHAUST_MIN_ABS = 5.0
ICEBERG_REPLENISH_FRAC = 0.5   # 補回到耗盡前≥50%,第一次補回=「偵測到」(原文:detected after the first replenishment)
ICEBERG_GRACE_SEC = 20 * 60    # 價位暫時滑出五檔的寬限期(原文:keeps state until expected replenishment has not occurred)
ICEBERG_TRADE_TOL = 0.003      # 成交價須在守價位±0.3%內才算confirm(交叉比對真實逐筆成交)
ICEBERG_TRADE_LOOKBACK = 30    # 秒,confirm用的成交回看窗


SHADOW = {"date": None, "events": []}


#: 即時標籤引擎(2026-09-24):盤中 7 格改吃每秒滾動窗、條件連續成立 TAG_HOLD_SEC 秒才「首次觸發」,
#: 觸發後顯示經過分鐘、到基準率時距自動熄(30 分格 1800s / 5 分格 300s),滾動數反向加 ✗,13:00 後觸發加「尾」。
#: 每次觸發落地 tag_events_{date}.jsonl 供事後對照 127 日(完成桶版)基準率。日級格(蓄勢隔夜/同賣/連3買/破昨/弱開)不變。
TAG_HOLD_SEC = 10
TAG_STATE: dict = {}          # (sid, tag) -> {"pend": ts|None, "t0": ts|None, "hz": sec, "dead": bool, "tail": bool, "txt": str}
TAG_LOG_DAY = {"d": None}
#: (tag, 方向, 時距秒, 條件(r)->bool|None, 反向失效(r)->bool, 顯示名(r)->str)
TAG_DEFS = [
    ("主力點火", "bull", 1800,
     lambda r: (r.get("big30_r") or 0) >= 3e7 and (_par30(r) is None or _par30(r) < 45),
     lambda r: (r.get("big30_r") or 0) < 0, lambda r: "主力點火"),
    ("機構暗退", "bear", 1800,
     lambda r: (r.get("big30_r") or 0) <= -3e7 and (_par30(r) is None or _par30(r) < 15),
     lambda r: (r.get("big30_r") or 0) > 0, lambda r: "機構暗退"),
    ("噴後過熱", "bear", 1800,
     lambda r: (r.get("r30_r") or 0) >= 150,
     lambda r: (r.get("r30_r") or 0) < 50, lambda r: "噴後過熱"),
    ("勿追30", "bear", 1800,
     lambda r: (r.get("r30_r") or 0) > 30 and (((r.get("dsh30_r") or 0) > 10 and not r["unm"]) or (_b30n(r) is not None and _b30n(r) < -5)),
     lambda r: (r.get("r30_r") or 0) < 0, lambda r: "勿追"),
    ("勿追5m", "bear", 300,
     lambda r: (r.get("w_ret_r") or 0) > 20 and (((r.get("dshare5_r") or 0) > 5 and not r["unm"]) or (_b5n(r) is not None and _b5n(r) < -5)),
     lambda r: (r.get("w_ret_r") or 0) < 0,
     lambda r: "勿追5m(枯量)" if (r.get("rvol5_r") is not None and r["rvol5_r"] < 1.0) else "勿追5m"),
    ("深接30", "bull", 1800,
     lambda r: (r.get("r30_r") or 0) < -30 and _b30n(r) is not None and _b30n(r) > 5,
     lambda r: (r.get("big30_r") or 0) < 0, lambda r: "深接"),
    ("深接5m", "bull", 300,
     lambda r: (r.get("w_ret_r") or 0) < -20 and _b5n(r) is not None and _b5n(r) > 5 and (r.get("rvol5_r") or 0) >= 0.5,
     lambda r: (r.get("big5_r") or 0) < 0, lambda r: "深接5m"),
    ("虛胖接刀", "ex", 300,
     lambda r: (r.get("w_ret_r") or 0) < -20 and _b5n(r) is not None and _b5n(r) > 5 and r.get("rvol5_r") is not None and r["rvol5_r"] < 0.5,
     lambda r: False, lambda r: "▫虛胖接刀(枯量)"),
    ("散戶虛拉", "bear", 300,
     lambda r: (r.get("w_ret_r") or 0) > 20 and (r.get("rbuy5_r") or 0) >= 5 and not r["unm"],
     lambda r: (r.get("w_ret_r") or 0) < 0, lambda r: "散戶虛拉"),
    ("純機構", "bull", 1800,
     lambda r: (_b5n(r) or 0) > 10 and (r.get("tot5_r") or 0) > 0 and r.get("share5_r") is not None and r["share5_r"] < 5 and not r["unm"]
     and (r.get("bigp30_r") if r.get("bigp30_r") is not None else 0) < 0 and (r.get("big5p_r") if r.get("big5p_r") is not None else 0) < 0,
     lambda r: (r.get("big5_r") or 0) < 0,
     lambda r: "巨資機構" if (r.get("big5_r") or 0) >= 3e7 else "純機構"),
]


#: 各訊號的時間價值曲線 (平台秒數, 歸零秒數):經過 < 平台 → 1.0;之後線性降到 歸零秒 = 0。依各格驗證時距設定(2026-09-24):
#:   主力點火/深接/勿追/噴後過熱:基準率=首次觸發後 30 分 → 0 平台、30 分歸零
#:   純機構:30 分 +24~29、45 分 +36 仍成長 → 5 分平台、45 分歸零
#:   機構暗退:流量領先價 ~2h → 60 分平台、120 分歸零(標籤顯示仍 30 分熄,計分延續)
#:   散戶虛拉:留不到收盤 → 10 分平台、60 分歸零
TAG_DECAY = {"主力點火": (0, 1800), "純機構": (300, 2700), "深接30": (0, 1800), "深接5m": (0, 1800),
             "機構暗退": (3600, 7200), "噴後過熱": (0, 1800), "散戶虛拉": (600, 3600),
             "勿追30": (0, 1800), "勿追5m": (0, 1800), "虛胖接刀": (0, 300)}


SC_HIST: dict = {}   # sid -> deque[(ts, score)] 近 60 分盤中分歷史(V2.2「近60分極端分」用)


#: 盤中分 V2.3 權重(2026-09-24;scripts/research/biglot_score_v23_fit.py):pit100×127 日 5 分桶面板,只用 IS(≤06-30)做
#: 「區間指標」聯合 OLS(y=未來 60 分對宇宙等權超額 bps,按日聚類 SE),權重 = 0.7×係數、|cl-t|<2 歸零、四捨五入到 0.5;
#: 過熱 400–600 與逆弱 50–100 依單調先驗沿用前一格(擬合值 t−2.0/+1.9 剛好落門檻外)。OOS(07-01~)不參與擬合。
#: 歸零項(聯合後無增量):過熱 150–200、急跌 200–600、順漲/順跌/逆強、對開盤 ±3/±5%(被過熱/散戶側吸收)、主力點火/深接/暗退/破昨。
#: 2026-09-28 追加歸零:過熱 200–300(獨立事件重驗,見 biglot-cross-sectional-lookahead-bug-and-fix
#: 記憶檔——原始 t−2.12 用的是「同一串連續觸發全部當獨立樣本」,拆成獨立事件(每串只留第一筆,
#: 觸發10,872次/獨立事件僅7,512次,重疊倍數1.46x)後 t 掉到 −1.34,低於生產門檻|t|≥2)。
#: 2026-09-28 v26(scripts/research/biglot_score_v26_fit.py,完整聯合重擬+分組先驗收縮,取代原本
#: v23→v24→v25 序貫凍結流程)發現過熱300-400/400-600、逆弱20-50/50-100、蓄勢深是「序貫凍結」下
#: 僥倖存活的 5 項,完整聯合重擬後全部失去顯著性(|t|<1.7),方向不翻轉、純粹雜訊;用延伸至
#: 2026-09-24 的 144 日面板重驗(biglot_score_v26_fit_extended.py)結論不變、且證據更紮實(9 月新
#: 增 OOS 子窗最大 |t| 僅 1.67),故追加歸零。急跌≤−600 OOS 全期仍顯著(t+2.44)方向不變,人工鎖定
#: +40 維持不動。
V23_W = {   # 名稱沿用;內容為 V2.5(2026-09-24 晚,2026-09-28 依 v26 驗證追加歸零 5 項)
    "散戶虛拉": -4.5, "勿追5m": -3.0,
    "急跌≤−600": +40.0,                       # 擬合 +45~49,受 |分|≤40 上限
    "逆弱≤−100": +5.5,
    "純機構": +9.0,
    "蓄勢": +4.5, "倒貨": -2.5,  # V2.4 分級蓄勢
    # V2.5 竭盡狀態格(biglot_score_v25_fit.py;近5分 ≤−0.2% 為「急跌」、≥+0.2% 為「急拉」;30秒主動賣占比 ≤40%=竭盡、≥60%=未竭):
    #   單獨的「賣盤竭盡」是負的(−5.5/−5.1):賣壓退了=反彈已在桶內發生;反轉指紋是「賣壓還在」或「沒人賣價卻掉(真空)」
    "急跌·賣壓未竭": +3.0, "急跌·真空": +12.5, "急跌·竭盡散戶接": -5.5, "急跌·大戶接∧未竭": +6.0, "急跌·末30秒續跌": +2.0,
    "急拉·買壓竭盡": +4.5, "急拉·末30秒續漲": -3.5,
    "權證": 3.0,                               # tick 項,面板測不到,沿用暫定 ±3(散戶接跌 +2.5 已被狀態格吸收 → 0)
}


SC_LOGGED: set = set()   # (日, 5分桶, sid) 已落地


# ---- 成因標籤(jack 2026-09-24:極值分數進場前要知道「為什麼」——處置/跌停/族群/MOPS,每項標來源與時間)----
_DISP_CACHE: dict = {"date": None, "sids": {}, "mtime": None}


# ---- 期散(小型契約 1 口成交 = 期貨市場散戶代理;jack 2026-09-25):只對 FUT_MINI 檔顯示,描述性、不進分數 ----
from collections import deque as _deque_mini
MINI_ROOT = {r["sid"]: (r["fut_code"][:-1] if len(r.get("fut_code", "")) == 3 and r["fut_code"].endswith("F") else r.get("fut_code", "")).lower()
             for r in _cal["universe"] if r.get("is_mini")}
MINI_ST: dict = {"day": None, "off": {}, "q": {}}       # off: root -> 檔案讀取位移;q: sid -> deque[(ts, px, size, sgn, amt)]


# ---- 紙上交易(paper trading;jack 2026-09-25 交辦,2026-09-29 起累 20 日)--------------------------------------------
#: 目的:量「訊號當下掛買一等 30 秒」的真實成交率——tick 重放顯示嚴格(穿越才成交)27% vs 樂觀(觸價即成交)89% 決定損益兩平的哪一邊。
#: 規則(定案,見記憶 biglot-v25-tick-replay-verdict):進場 V2.5≥15、成因無 處置/跌停鎖/跟盤殺、同時 ≤3 口、掛買一 30 秒不追;
#: 出場 持倉分≤0 連續 30 秒 / 壞標籤 / 60 分 → 掛賣一 60 秒、沒成交打買一;13:20 後強制出。兩本帳:bucket=只在 5 分桶邊界取樣
#: (回測口徑)、sec=每秒首次穿越(反應式,預期較差)。成交口徑 strict(價穿越)與 opt(觸價)並記。不接下單層、不送單。
PAPER_PATH = DATA_DIR.parent / "cache" / "biglot_live_watch" / "paper_state.json"
PAPER_DAILY = DATA_DIR.parent / "cache" / "biglot_live_watch" / "paper_daily.json"
PAPER_COST, PAPER_K, PAPER_TH = 22.0, 3, 15.0
PAPER_BUY_WAIT, PAPER_SELL_WAIT, PAPER_MAX_HOLD = 30, 60, 3600
PAPER_SKIP = ("處置", "跌停鎖", "跟盤殺")
PAPER_BOOKS = ("bucket", "sec")


try:
    PAPER: dict = json.loads(PAPER_PATH.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    PAPER = _paper_blank(None)


ARC_CSS = """<style>body{background:#0d1117;color:#c9d1d9;font:13px/1.6 -apple-system,'PingFang TC',monospace;margin:10px}
tr.hold td{box-shadow:inset 3px 0 #58a6ff}tr.hexit td{background:#3a3300 !important}tr.hbad td{background:#4a1a1a !important}tr.hexp td{background:#2a2a2a !important}
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


_HELP_GROUPS = [
    ("識別", [
        ("股票", "中文名＋細分產業標籤(灰字)。細分產業為宇宙標的手動策展,比大類細一層(如半導體再分晶圓代工/封測/DRAM/矽晶圓;PCB再分CCL/ABF載板/軟板)。",
         "同族群連動看輪動:CCL(聯茂/台燿)整片被買、ABF(南電/景碩/欣興)整片被賣一目了然。目前宇宙全為散戶可測標的(股價<2000),無隱藏。"),
    ]),
    ("價格", [
        ("價", "最新成交價。顏色＝對前一交易日收盤:紅漲綠跌(台股慣例,與美股相反)。", "一眼看今日相對昨收是紅是綠。"),
        ("對昨收", "現價−昨收 的金額與%,即專業看盤軟體的主報價。▲紅=漲、▼綠=跌。", "這才是一般人講的『今天漲跌多少』。金額看跳動幅度、%看比例。"),
        ("期貨買 / 期貨賣", "個股期貨近月買一/賣一,拆成兩欄:委託價＋委託量(小字×N張)。著色比照『價』欄以期貨自身昨結為基準:紅漲綠跌,期貨漲停=紅底白字、跌停=綠底白字(漲停時賣方常空→期貨賣顯示—、買一鎖在漲停價;跌停反之)。**黃底線**標在成交價落在的那一邊(成交價==賣一→主動買、==買一→主動賣),一眼看主動方在哪側。滑鼠移上 tooltip 顯示期貨成交價與基差%(期貨/現股−1,正=溢價)。資料源:個股期貨 ws books channel(五檔即時推播取第一檔);整條 ws 斷線逾30秒才剔除,鎖死檔簿不動仍保留(不再閃爍消失)。", "買一/賣一價差=期貨即時流動性(價差窄=好成交);委託量=該價位掛單張數(對照『幾分鐘成交量』判牆/真空,勿看買賣比)。黃底線那側=最近成交的主動方向。期貨先漲停/跌停常領先現股,是搶帽方向的即時線索;基差看盤前期現貨背離與盤中溢價/逆價差。"),
        ("日內%", "現價/今日開盤−1。盤中相對『開盤』的走勢,與對昨收互補。", "跳空開高後拉回:對昨收仍紅、日內%卻綠=開高走低。兩欄一起讀分辨跳空 vs 盤中動能。"),
        ("盤前試撮(共用欄)", "不另立欄:08:30~09:00 無成交時,試撮價塞進『價』欄、試撮跳空%塞『對昨收』,皆帶『試』上標;09:00開盤後自動轉為成交資料。(試撮買一/賣一改到個股詳情頁的五檔看)", "開盤前看試撮預判開盤;試撮價會被大單掛撤誘導,非確定開盤價。"),
        ("30分bps", "近30分窗價格報酬(1bps=0.01%)。主尺度。", "驗證格(噴後過熱/跌深接/勿追)判斷的價格軸。"),
        ("順逆市", "個股30分方向 vs 市場方向(=全宇宙36檔等權30分報酬,即頂端『市場代理30分』)。順漲/順跌=同向;逆強=大盤跌它漲;逆弱=大盤漲它跌。門檻:個股|30分|≥20bps ∧ 市場|30分|≥5bps 兩邊都夠力才標,否則顯示—。", "市場是個股報酬最強控制變數——先看這格:順漲/順跌多是隨大盤,逆強/逆弱才有個股獨立alpha。"),
        ("5分bps", "近5分窗價格報酬。最短尺度、雜訊最大。", "只作即時異動參考,別單獨下判斷。"),
        ("即時RS", "個股日內% − 宇宙日內%(百分點)。負(綠)=相對壓著(彈簧);＞+1(黃)=已彈開。", "隔夜挑股:壓著的彈簧優先。軟否決:日線弱∧已彈=毒格−31bps。"),
    ]),
    ("大戶三尺度(單筆≥1000萬;全系統核心)", [
        ("大戶淨額三尺度", "大戶=單筆成交≥1000萬;每筆按主動方向(價≥賣一→買、≤買一→賣)計正負,累加成淨額。單位一律=萬(5分/30分/全日)。5分/30分為每秒滾動窗;訊號標籤用完成5分桶版。", "三尺度=不同記憶長度的同一件事。"),
        ("5分大戶", "最新完成5分窗的大戶淨額(單位萬)。最短窗、最即時。", "看『現在』誰在進出;易反覆,配30分看。"),
        ("30分大戶", "近30分滾動窗大戶淨額(單位萬)。主尺度。", "驗證格的大戶軸。與價格軸(30分bps)交叉:跌×大戶買=跌深接。"),
        ("全日大戶", "開盤一路累加至今的大戶淨額,收盤即全日最終值。單位=萬元(NT$),與 5分/30分 同尺。最重要。", "紅=整天淨買、綠=淨賣。÷成交金額=佔比%(隔夜排序主鍵)。三尺度並排看背離:短窗買∧全日仍賣=誘多/出貨。"),
        ("權證5分 / 權證30分", "該標的底下的**活躍權證**(前一交易日成交額前 600 檔、對映到 36 檔標的;認購為主、認售稀少)近5分 / 近30分滾動窗。數字=認購/認售**成交額(活動量)**,單位萬——只代表多方/空方商品有多熱,**散戶倒認購也算認購成交**,不帶方向。小字=**簽號後多方占比**:每筆成交用當下買一/賣一判主動方(≥賣一=主動買、≤買一=主動賣、中間用tick rule),多方=主動買認購+主動賣認售、空方=主動賣認購+主動買認售,占比=多方÷(多方+空方)。**判斷字與顏色同一規則:≥60% 偏多(紅)、≤40% 偏空(綠)、40~60% 中性(灰);主動額 <10 萬標『量小』不判斷**(權證單價低,一筆 1 張常只有幾百元,<10 萬顯示一位小數)。資料源:富邦 ws 逐筆(2 連線×300 檔,單線上限 300),不用 MIS、不吃 REST 配額。", "⚠純描述性、**尚未回測**,不進訊號欄。**『權證做多』看小字占比與顏色,不看購/售活動量。**機制假說:散戶主動買認購→造市商賣認購並買現股避險(機械性買盤),可能與中實桶有交集;反面它也可能只是散戶追價放大鏡——方向待影子帳驗(『大戶5分≥3千萬∧散買%<5%』對照組每窗自動記錄權證欄位與5/30分/收盤結果)。"),
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
        ("壓縮1h", "(現價 ÷ 最近12個5分桶均價 − 1)×100%,即現價相對『近1小時均價』的位置。需≥8桶才算,13:20後最有意義。負(綠)=壓在均價下=彈簧;正=已彈高。", "隔夜挑股鍵。壓縮∧站上5日線隔夜+93.8/t5.10;噴高(正值大)=甜蜜點流失。是『蓄勢隔夜』紅籤條件之一。"),
        ("日線", "取最近11個交易日收盤:↑多=最新收盤>5日均線、↓空=跌破;後面%=5日動能(最新收盤÷5交易日前−1)。截至最近日收盤,盤中不變。", "壓縮回檔在日線多頭股(↑)才是買點、空頭股(↓)是接刀。做空池要求↓;上週五三檔↑做空腿全被軋。"),
    ]),
    ("五檔委託簿(移到個股詳情頁)", [
        ("買簿 / 賣簿", "已從總表移除,改到個股詳情頁看完整五檔。判讀原則不變:委買/委賣量要換算成『幾分鐘的成交量』——＜3分=真空、＞10分=牆,絕對張數/金額當牆是錯的(欣興950誤判教訓),要除以流速,不看買賣比。", "總表擠,五檔屬於描述性、可被掛撤誘導,放詳情頁逐檔細看更合適。"),
    ]),
    ("風險/量能", [
        ("波動分數", "0–100:融資日變動幅度歷史分位＋借券日變動幅度歷史分位的平均(不分方向)。🌊🌊≥92 🌊≥86 藍字≥80。", "只預測隔日盤中『振幅』(t3.40),對淨報酬/跳空/量能無解釋力,非方向訊號。"),
        ("振幅%", "高波動分數=20日日均振幅%((高−低)/收盤),來自 calib 選股校準。金字≥7%(高波動)、灰＜5%(偏低)。與『波動分數』不同:那是融資/借券的T-1預測,這是實際已實現振幅。", "這是股票進本系統的門檻指標——宇宙中位約6.5%,越高日內波段越大、越適合大戶/散戶流策略。用來檢視/汰換成員。"),
        ("量能x", "5分窗成交金額 ÷ 近5日同時段中位(rvol)。", "≥5=爆量。三合一吸貨窗(大戶≥3千萬∧rvol≥5∧散＜25%)的量能條件。"),
    ]),
    ("訊號(合併欄:原章/跌訊/漲訊/旗標整成一格,去重,顏色分方向)", [
        ("紅=看多", "主力點火(30分大戶買≥3千萬∧散＜45%,健康首發、唯一正格;台股『抬轎』易誤解為散戶故改名)·純機構/巨資機構(逆勢純機構買單+24~29/t5.2,巨資=≥3千萬)·深接(跌深大戶接RVOL≥0.5,正EV,+11~14/t3.4)·蓄勢隔夜(全日佔比≥10%∧壓縮＜0,隔夜雙鍵IC t7.1)·連3買(持續)。", "多方計分;蓄勢隔夜是收盤導向、盤中格是條件式基準率。"),
        ("綠=看空", "噴後過熱(30分漲≥150,峰後均−32)·勿追(漲×參與跳升或大戶賣,−5~−9.6,趨勢日−32)·機構暗退(30分大戶賣≥3千萬∧散＜15%,流量領先價~2h)·散戶虛拉(5分漲＞20∧散買≥5%,留不到收盤)·同賣(大戶賣∧散戶賣,隔夜−28/t−6)·破昨防線@價(觸昨日午後低,−125bps/73%貫穿)。", "空方計分;每格都附127日基準率。"),
        ("黃=注記", "↓弱開=明日弱開候選 · ▫虛胖接刀=枯量RVOL＜0.5、超額≈0的無效帶。", "⚠『深接』(正EV,紅)與『虛胖接刀』(無效,黃)語意近但一好一壞,別搞混。"),
        ("整格粗體", "同一列命中的多方+空方訊號合計 ≥3 個時整格加粗。", "多格共振,最該優先看的列。原本四欄拆開看容易漏掉『同一檔同時有多空』的背離,合併後一眼看到。"),
    ]),
]


# ============================ 個股詳情頁（點名稱進入） ============================
# 逐筆分時圖 + 累計大戶/散戶淨流 + 五檔委託簿。沿用主表同一套判定
# (BIG_AMT=1000萬 / 散戶=1張<500萬 / side=主動買賣 / 空窗跳量剔除),
# 資料源=raw_{日}.jsonl(逐筆) + watchlist_books_{日}.jsonl(五檔),兩者皆有歷史,
# 故同一頁 today=即時、d=過去日=回放,完全共用。per-(sid,日) 增量快取,重繪只讀新增 bytes。
DETAIL: dict = {}
DETAIL_LOCK = threading.Lock()   # _stock_series 增量讀非執行緒安全:loop(總覽/AGG)與 HTTP(詳情頁)同時呼叫會把同一段 bytes 解析兩次(2026-09-24 大戶累計 2 倍事故)


if __name__ == "__main__":
    # class H(HTTP 處理器)/loop()(背景排程迴圈)/main()(組合根)都搬到
    # biglot/http_server.py 了——這是 docs/biglot-refactor-roadmap.md 從一開始
    # 就設想的「原檔案降級成薄殼」最終型態，見該檔案檔頭說明。
    from biglot.http_server import main
    main()
