"""biglot dashboard 重構：歷史回放(每分鐘快照+可拖曳時間軸滑桿;2026-10-04 jack 交辦，
從「每30分鐘快照」先升到「每5分鐘」，這次再升到「08:30起算、全天每1分鐘」一天301格)。

== 儲存格式(2026-10-04 第二次重寫:append-only + byte-offset 索引) ==

第一版(30分鐘/5分鐘密度)用的是「整天一個 JSON dict,每次要存新格就把整份檔案讀出來、
塞一個新 key、整份重新寫回去」。這個設計在格子數變多、檔案變大時會出事:每次存檔的成本
跟「這天已經存了多少」成正比,一天下來是 O(n²) 的總成本。5分鐘/55格/天(收盤時約38MB)
還撐得住,但全天1分鐘/301格/天(收盤時約210MB)會讓下午每一分鐘都要重新解析+寫回一份
逼近210MB的JSON——而且這個動作同步卡在跟即時頁同一個 render 迴圈裡,有拖慢整個網站的
風險。這不是「資料量變大」本身的問題,是「讀寫方式不是 append-only」的問題，所以光是
調密度沒用，先把儲存結構換掉。

新設計：
  1. 檔案格式改成 append-only 的 `timeline_{日期}.jsonl`，一行一格，格式是
     `f"{hhmm}\\t{json.dumps(frag)}\\n"`(hhmm 跟 JSON 編碼過的 frag 用一個 tab 分隔)。
     存新格=純粹在檔尾 append 一行，O(1)，不用動到檔案其餘部分。
     ⚠ 一定要用 `json.dumps(frag)` 把 frag 編碼過才能安全放進「一行一筆」的格式——
     frag 本身是多行 HTML、一定含有真的換行字元，json.dumps 會把裡面的真換行/tab
     都轉成跳脫序列(`\\n`/`\\t` 兩個字元，不是真的 control byte)，所以每一行裡唯一
     一個真的 tab byte 跟真的換行 byte，保證就是我們自己插入的分隔符號跟行尾——這是
     這個格式安全、不會被內容本身的換行破壞行界線的理由，不是巧合。
  2. 為了不要每次「有沒有存過這格」或「這格存在哪裡」都要整份掃過一遍，在記憶體裡
     維護一份 `day -> {hhmm: byte_offset}` 索引(`_DAY_INDEX`)，每次只增量掃描「上次
     掃到的位置」之後新增的 bytes(`_DAY_SCANNED_TO`)——append-only 格式讓這件事
     很便宜:新增量只會出現在檔尾，不需要從頭重算。索引掃描本身只需要找出每行的
     tab 位置取出 hhmm 標籤，完全不用 json.loads 整行(那會把動輒700KB的 frag 內容
     也解析一次，純粹浪費)，只有真的要「讀某一格的內容」時才對那一行單獨
     json.loads。這樣不管格子密度多高、檔案多大，存一格跟讀一格的成本都只跟
     「這一格自己多大」有關，不會隨著當天已經存的格數而變慢。
  3. 進程重啟後記憶體索引會清空，屬預期行為——下一次呼叫會自動從 `_DAY_SCANNED_TO`
     預設值0開始，重新做一次增量掃描補齊索引(只掃行界線、不解析內容，就算當天
     已經存了兩百多MB也是純I/O、很快)，不需要額外的持久化索引檔案。

== 密度與時間範圍(2026-10-04) ==

`_SLOT_START`/`_SLOT_END`/`_SLOT_STEP_MIN` 三個常數定義整個時間軸:08:30(含期貨/現貨
盤前試撮,`_in_market()`本來就從08:30起算)到13:30，每1分鐘一格，共301格/天。以後要再
調密度或範圍只改這三個常數，`_gen_slots()` 自動產生 `TIMELINE_SLOTS`，不手寫清單。

`_SLOT_STALE_SEC`(見下方 `save_timeline_slot_if_due`)隨密度一起調整:格距縮到60秒後，
容忍重啟延遲的空間也要跟著縮小，否則「這格內容是不是這格該有的樣子」會失去意義。

== 排序/凍結欄 JS(不變) ==

排序/凍結欄是純前端 JS，讀的是 HTML 裡本來就有的 `data-sort` 屬性，歷史快照存的 HTML
裡這些屬性早就在；跟即時頁共用同一份 `biglot_dashboard.TABLE_INTERACT_JS`，不重新發明
一套、也不用為了這次重寫而改動。

== 頁面架構(不變,2026-10-03 jack 確認:獨立頁、單一條跨日期連續滑桿) ==

`render_timeline_page(date, hhmm)` 回傳完整頁面(可拖曳滑桿橫跨所有日期＋滑桿目前位置
的表格內容)；`render_timeline_frag(date, hhmm)` 是純內容版，給滑桿拖曳放開(`change`
事件)時用 fetch 換 `#app` 內容，呼應 `/stock`、`/stockfrag` 那組既有命名慣例。

依賴規則同 biglot/tx_panel.py：ST/datetime/TZ 一律 `import biglot_dashboard` 屬性
存取，不在頂層具名匯入。
"""
from __future__ import annotations

import html as html_mod
import json
import sys

import biglot_dashboard
from stock_db import DATA_DIR

_SLOT_STEP_MIN = 1       # 2026-10-04 jack 交辦:全天改1分鐘一格
_SLOT_START, _SLOT_END = "08:30", "13:30"   # 2026-10-04 起點提前到08:30,收進期貨/現貨盤前試撮


def _gen_slots(start: str, end: str, step_min: int) -> list[str]:
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    t0, t1 = sh * 60 + sm, eh * 60 + em
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(t0, t1 + 1, step_min)]


TIMELINE_SLOTS = _gen_slots(_SLOT_START, _SLOT_END, _SLOT_STEP_MIN)
_SLOT_SET = frozenset(TIMELINE_SLOTS)   # 索引掃描時的快速合法性檢查用,避免存進格式不對的行

_TIMELINE_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch" / "timeline"


def _timeline_path(day: str):
    _TIMELINE_DIR.mkdir(parents=True, exist_ok=True)
    return _TIMELINE_DIR / f"timeline_{day}.jsonl"


# 現在時間比時段標籤晚超過這麼多秒,視為過期不存(見下方 save_timeline_slot_if_due 註記)。
# 2026-10-04 隨 _SLOT_STEP_MIN 從5分鐘(300秒)收緊到1分鐘(60秒)一起調整:門檻必須明顯
# 小於格距，否則「這格存的到底是不是這格該有的樣子」會失去意義(兩個相鄰格子的空窗
# 重疊)。25秒只能容忍很快的重啟——本次會期實測重啟到 /frag 開始回應正常內容約需
# 30~50秒，意味著大部分重啟後的第一分鐘(甚至好幾分鐘)會正確缺格，不是bug;「缺格子
# 比存錯格子好」是這整個防呆機制從一開始就定下的原則，密度變高只是讓這個取捨更常
# 發生，原則本身不變。
_SLOT_STALE_SEC = 25

_DAY_INDEX: dict[str, dict[str, int]] = {}      # day -> {hhmm: byte_offset}，記憶體快取
_DAY_SCANNED_TO: dict[str, int] = {}            # day -> 已經增量掃描到的檔案位置(byte)


def _refresh_day_index(day: str) -> dict[str, int]:
    """確保 `_DAY_INDEX[day]` 反映目前磁碟上的內容,只增量掃描上次掃到之後新增的
    bytes——只找每行的 tab 分隔位置取出 hhmm 標籤,不 json.loads 整行內容(那會把
    frag 本體也解析一次,純粹浪費;要讀內容交給 `_load_slot` 對單一行做)。遇到
    「最後一行還沒寫完」(讀到 EOF 但沒有結尾換行,代表正好撞上另一執行緒還在
    append 到一半)就停在那一行的開頭、不前進 `_DAY_SCANNED_TO`,下次呼叫會重新
    從那裡開始——保證不會永久漏掉一筆正在寫入途中被撞見的格子。"""
    path = _timeline_path(day)
    idx = _DAY_INDEX.setdefault(day, {})
    if not path.exists():
        return idx
    start = _DAY_SCANNED_TO.get(day, 0)
    try:
        with open(path, "rb") as f:
            f.seek(start)
            pos = start
            while True:
                line = f.readline()
                if not line or not line.endswith(b"\n"):
                    break
                hhmm = line.partition(b"\t")[0].decode("utf-8", "replace")
                if hhmm in _SLOT_SET:
                    idx[hhmm] = pos
                pos = f.tell()
        _DAY_SCANNED_TO[day] = pos
    except Exception as e:  # noqa: BLE001
        print(f"[timeline_snap] index scan failed: {e}", file=sys.stderr)
    return idx


def _load_slot(day: str, hhmm: str) -> str | None:
    """讀單一格的 frag 內容:索引查 byte offset,seek 過去只讀那一行,不碰檔案其餘部分。"""
    idx = _refresh_day_index(day)
    offset = idx.get(hhmm)
    if offset is None:
        return None
    try:
        with open(_timeline_path(day), "rb") as f:
            f.seek(offset)
            line = f.readline()
        return json.loads(line.partition(b"\t")[2])
    except Exception:  # noqa: BLE001
        return None


def save_timeline_slot_if_due(now):
    """每次 render() 算完後呼叫一次:現在時間若已經跨過 TIMELINE_SLOTS 裡最新的那個
    時間點、今天這個時間點還沒存過,就把剛算好的 PAGE["frag"] append 進當天的 timeline
    檔。冪等(同一個時間點只存一次)、失敗不拋例外(純附加功能,不能影響既有渲染迴圈)。

    ⚠ 2026-09-29 jack 抓到的真bug(密度調整後原理不變,保留記錄):原本沒有「太晚就不
    存」的防呆,導致收盤後很久才發生的重啟會把「現在時間之前最新的時段」當成標籤,
    把好幾小時後的畫面誤存成「13:30快照」——標籤錯,而且內容本身也因為滾動窗距離
    真正收盤太久沒有新tick而全部退化成空值。現在加上:現在時間距離這個時段標籤超過
    `_SLOT_STALE_SEC` 就不存,寧可那格缺著,也不要存一個標籤跟內容對不上、內容本身
    還退化的假快照。"""
    try:
        day = biglot_dashboard.ST.date
        if not day:
            return
        hm = now.strftime("%H:%M")
        due = [s for s in TIMELINE_SLOTS if s <= hm]
        if not due:
            return
        latest_due = due[-1]
        h, m = latest_due.split(":")
        slot_dt = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        if (now - slot_dt).total_seconds() > _SLOT_STALE_SEC:
            return
        idx = _refresh_day_index(day)
        if latest_due in idx:
            return
        frag = biglot_dashboard.PAGE.get("frag")
        if not frag or "初始化中" in frag:
            return
        path = _timeline_path(day)
        offset = path.stat().st_size if path.exists() else 0
        line = f"{latest_due}\t{json.dumps(frag)}\n".encode("utf-8")
        with open(path, "ab") as f:
            f.write(line)
        idx[latest_due] = offset                      # 直接更新記憶體索引,不用等下次掃描才看到剛存的這格
        _DAY_SCANNED_TO[day] = offset + len(line)
    except Exception as e:  # noqa: BLE001
        print(f"[timeline_snap] save failed: {e}", file=sys.stderr)


def _list_dates() -> list[str]:
    if not _TIMELINE_DIR.exists():
        return []
    return sorted(f.stem[len("timeline_"):] for f in _TIMELINE_DIR.glob("timeline_*.jsonl"))


def _available_slots(day: str) -> list[str]:
    """該日實際存到的時間點,照 TIMELINE_SLOTS 的時間順序回傳(不是索引字典的插入順序)
    ——中途重啟漏接的格子會缺，不假裝補滿，滑桿格數就是實際有的格數。"""
    idx = _refresh_day_index(day)
    return [s for s in TIMELINE_SLOTS if s in idx]


def render_timeline_frag(date: str, hhmm: str) -> str:
    """純內容版:只回傳該時間點存的 frag 本體,給滑桿 JS 用 fetch 換 #app 內容
    ——呼應 /stock 與 /stockfrag 那組「整頁版／純內容版」的既有命名慣例。"""
    frag = _load_slot(date, hhmm)
    if frag is None:
        return f"<div class='meta'>無 {html_mod.escape(date)} {html_mod.escape(hhmm)} 快照(可能被 _SLOT_STALE_SEC 擋下或尚未到這個時間點)</div>"
    return frag


def _all_slots() -> list[tuple[str, str]]:
    """跨所有日期攤平成一條連續時間軸:日期由舊到新、同一天內照 TIMELINE_SLOTS 順序。
    2026-10-03 jack 交辦:不要分日期切換,滑桿拖過一天的最後一格要能直接接上
    下一個交易日的第一格,體感是一條連續的軸,不是「選日期→再選時間」兩層選擇。"""
    return [(d, hm) for d in _list_dates() for hm in _available_slots(d)]


def render_timeline_page(date: str | None, hhmm: str | None) -> str:
    """整頁版:單一條跨日期的連續滑桿(放開才打 /timelinefrag 換內容)＋目前位置的表格
    內容(進頁第一時間就有,不用再打一次 API)。滑桿共用即時頁同一套排序/凍結欄 JS
    (biglot_dashboard.TABLE_INTERACT_JS)。沒給 date/hhmm 時預設停在整條軸最新的一格
    (= 最近一個交易日的最後一筆快照)。"""
    all_slots = _all_slots()
    if not all_slots:
        return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"{biglot_dashboard.ARC_CSS}</head><body><h3>歷史回放 <a href='/'>←即時</a></h3>"
                "<div class='meta'>尚無快照(今天開盤後才會開始累積)</div></body></html>")
    if date and hhmm and (date, hhmm) in all_slots:
        init_idx = all_slots.index((date, hhmm))
    elif date:
        day_idxs = [i for i, (d, _) in enumerate(all_slots) if d == date]
        init_idx = day_idxs[0] if day_idxs else len(all_slots) - 1   # 該日無資料就退回最新一格
    else:
        init_idx = len(all_slots) - 1
    sel_date, sel_hhmm = all_slots[init_idx]
    init_frag = _load_slot(sel_date, sel_hhmm) or ""
    first_date, last_date = all_slots[0][0], all_slots[-1][0]
    span = first_date if first_date == last_date else f"{first_date}~{last_date}"
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=0.8'>"
            f"<title>歷史回放 {sel_date} {sel_hhmm}</title>{biglot_dashboard.ARC_CSS}</head><body>"
            # 時間滑桿凍結在螢幕固定位置(2026-10-04 jack 交辦「時間的滾輪也要跟股票名稱一樣
            # 凍結在螢幕固定的位置」):跟 th(表頭)用同一招 position:sticky;top:0,縱向捲動看
            # 下面的列時留在畫面上。z-index 給比表頭(th z-index 2,th.stk/th.frz z-index 3)
            # 更高的 10,確保往下捲動時滑桿列蓋在表頭上面、不會被蓋住。
            # ⚠ 2026-10-04 jack 交辦「橫向滑桿只要影響表格,不要影響時間/置頂按鈕/筆記」:
            # 這裡原本還加了 left:0 去擋橫向捲動把整個 bar 帶走的問題,但那是在治標——真正
            # 的根因是 #app(放表格的地方)本身沒有自己的橫向捲動範圍,表格變寬才會把整個
            # body 撐寬、害 bar 跟著被捲走。改成 `#app{overflow-x:auto}`(見 TABLE_CSS)讓
            # 表格變寬只在 #app 內部出現捲軸之後,body 本身再也不會橫向捲動,這個 bar(在
            # #app 外面)天生就不會被橫向捲動影響,不再需要 left:0 這行了。
            "<div id='tlbar' style='position:sticky;top:0;z-index:10;background:#0d1117;padding:8px 0;margin-bottom:4px'>"
            "<h3 style='margin:0 0 6px'>歷史回放 <a href='/'>←即時</a></h3>"
            f"<input type='range' id='tlslider' min='0' max='{len(all_slots) - 1}' value='{init_idx}' "
            "style='width:480px;vertical-align:middle'>"
            f"<span id='tllabel' style='font-weight:700;margin-left:10px'>{sel_date} {sel_hhmm}</span>"
            f"<span class='meta' style='margin-left:10px'>每{_SLOT_STEP_MIN}分鐘一格,{span}共{len(all_slots)}格"
            "(中途重啟漏接的格子會缺,不補假資料;拖到一天的最後一格會自動接上下一個交易日的第一格)</span>"
            "</div>"
            # 台指近月/期貨留倉損益面板(2026-10-04 jack 交辦「恢復台指期的圖形」「整個操盤
            # 體驗才會一致」):跟即時頁共用同一份面板標記+hover JS,資料來源是 render() 已經
            # 存進每格快照裡的 #txsrc/#pnlsrc 隱藏 div(回放時原封不動重放,圖表本身逐分鐘正確;
            # ⚠ 面板最上面那行「現在價」文字取材自 futprice_*.json,這個來源每5秒整檔覆寫、
            # 沒有歷史軌跡,回放畫面會固定顯示收盤那一刻的價格,不會跟著滑桿變動——這跟權證
            # 欄位同一種「來源本身不是時間序列」的限制,圖表線本身不受影響,只有這行文字如此)。
            f"{biglot_dashboard.TX_PNL_PANELS_HTML}"
            f"<div id='app'>{init_frag}</div>"
            # TABLE_INTERACT_JS/CHART_HOVER_JS 要排在滑桿自己的 script 之前:滑桿 IIFE 進頁就
            # 立刻呼叫一次 __applyFreeze() 讓初始畫面的凍結欄也量測到正確寬度(不是只有拖過
            # 一次滑桿之後才生效),這個呼叫發生在腳本解析當下、不是事件回呼裡,所以先後順序
            # 是真的有差——定義要先於第一次呼叫。
            f"<script>{biglot_dashboard.TABLE_INTERACT_JS}</script>"
            f"<script>{biglot_dashboard.CHART_HOVER_JS}</script>"
            f"<script>const TL_SLOTS={json.dumps(all_slots)};"
            "(function(){"
            "const slider=document.getElementById('tlslider'),label=document.getElementById('tllabel'),"
            "app=document.getElementById('app');let loading=false,pending=null;"
            # 把這一格快照裡埋的 #txsrc/#pnlsrc 隱藏 div 搬到可見的面板裡——跟即時頁 tick()
            # 的搬運邏輯一樣(見 biglot_dashboard.py 的 SHELL script),只是這裡是「換格」觸發,
            # 不是「每秒」觸發。
            "function syncPanels(){"
            "const s=document.getElementById('txsrc'); if(s){document.getElementById('txbody').innerHTML=s.innerHTML;}"
            "const ps=document.getElementById('pnlsrc'); if(ps){document.getElementById('pnlbody').innerHTML=ps.innerHTML;}}"
            # 滑桿列跟表頭都用 position:sticky;top:0,縱向捲動時會疊在同一個位置——表頭的
            # sticky top 要讓出滑桿列實際量到的高度,道理跟 applyFreeze() 量橫向寬度一樣,
            # 只是這裡量的是縱向高度。bar 本身高度不固定(字型/裝置縮放都會影響),用量測
            # 而不是寫死數字。
            "function adjustStickyTop(){"
            "const bar=document.getElementById('tlbar'); if(!bar) return;"
            "const h=bar.getBoundingClientRect().height;"
            "app.querySelectorAll('thead th').forEach(th=>{th.style.top=h+'px';});}"
            "async function loadSlot(idx){const[date,hhmm]=TL_SLOTS[idx];label.textContent=date+' '+hhmm;"
            "if(loading){pending=idx;return;}loading=true;"
            "try{const r=await fetch('/timelinefrag?date='+encodeURIComponent(date)+'&hhmm='+encodeURIComponent(hhmm));"
            "app.innerHTML=await r.text();"
            "if(window.__applySort){window.__applySort();}if(window.__applyFreeze){window.__applyFreeze();}syncPanels();adjustStickyTop();}catch(e){}"
            "loading=false;if(pending!==null){const p=pending;pending=null;loadSlot(p);}}"
            "slider.addEventListener('input',()=>{const[date,hhmm]=TL_SLOTS[slider.value];label.textContent=date+' '+hhmm;});"
            "slider.addEventListener('change',()=>{loadSlot(parseInt(slider.value,10));});"
            # 進頁時的初始內容(init_frag)也要套一次凍結欄+面板搬運+表頭偏移,不等使用者
            # 第一次拖曳滑桿才生效
            "if(window.__applyFreeze){window.__applyFreeze();}syncPanels();adjustStickyTop();"
            "})();</script>"
            "</body></html>")
