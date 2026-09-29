"""每30分鐘快照(2026-09-29 jack 交辦):在既有EOD收盤快照(day_views.py::snapshot_day,
一天一份、給 _load_hist() 隔夜計算用)之外,另外做一套「當日多時段快照」,純粹給「回頭
看某個時間點畫面長怎樣」用——兩者資料格式/用途/檔案完全獨立,不共用、不互相影響。

設計刻意避開改動 render() 本身:直接存 render() 已經算好的 PAGE["frag"] 這個 HTML
字串,不重新設計一套資料結構、不用拆時鐘。這是「選項A(讓 render() 收盤後分股票/期貨
兩條時鐘繼續跑)」跟「選項B(這支檔案採用的做法)」之間,jack 明確選 B 的理由:選項A
要動 render() 核心邏輯,牽涉的函式很多、風險跟改動量都不小;選項B 完全不碰 render(),
主頁即時頁面的行為零改變,新功能的風險只集中在這支獨立新檔案裡。

⚠ 目前只收 09:00~13:30(10個時間點),不含 13:45(期貨收盤):`_in_market()` 在 13:35
之後就永久不再呼叫 render(),要在 13:45 額外補一次才能真正抓到期貨最終價,但額外呼叫
render() 會把 LIVE 的 /frag 也一併覆寫成「股票滾動窗已經退化」的版本(PAGE["frag"]
是全站共用的同一份,沒有獨立於 render() 之外的呼叫方式)——這正是選項A會踩到的同一個
問題,為了多一個時間點去冒這個險不值得,先不做。

依賴規則同 biglot/tx_panel.py:ST/datetime/TZ 一律 `import biglot_dashboard` 屬性
存取,不在頂層具名匯入。
"""
from __future__ import annotations

import html as html_mod
import json
import sys

import biglot_dashboard
from stock_db import DATA_DIR

TIMELINE_SLOTS = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
                   "12:00", "12:30", "13:00", "13:30"]

_TIMELINE_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch" / "timeline"


def _timeline_path(day: str):
    _TIMELINE_DIR.mkdir(parents=True, exist_ok=True)
    return _TIMELINE_DIR / f"timeline_{day}.json"


_SLOT_STALE_SEC = 600  # 現在時間比時段標籤晚超過這麼多秒,視為過期不存(見下方2026-09-29註記)


def save_timeline_slot_if_due(now):
    """每次 render() 算完後呼叫一次:現在時間若已經跨過 TIMELINE_SLOTS 裡最新的那個
    時間點、今天這個時間點還沒存過,就把剛算好的 PAGE["frag"] 存進當天的 timeline 檔。
    冪等(同一個時間點只存一次)、失敗不拋例外(純附加功能,不能影響既有渲染迴圈)。

    ⚠ 2026-09-29 jack 抓到的真bug:原本沒有「太晚就不存」的防呆,導致收盤後很久才發生的
    重啟(當天為了部署新功能重啟好幾輪)會把「現在時間之前最新的時段」(過了13:30後永遠
    是13:30)當成標籤,把好幾小時後的畫面誤存成「13:30快照」——標籤錯,而且內容本身也
    因為5分/30分滾動窗距離真正收盤太久沒有新tick而全部退化成空值(這正是選項A被否決的
    同一個「拆時鐘」問題,只是意外從時段標籤這裡冒出來)。現在加上:現在時間距離這個時段
    標籤超過 _SLOT_STALE_SEC 就不存,寧可那格缺著,也不要存一個標籤跟內容對不上、內容
    本身還退化的假快照。"""
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
        p = _timeline_path(day)
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = {}
        if latest_due in data:
            return
        frag = biglot_dashboard.PAGE.get("frag")
        if not frag or "初始化中" in frag:
            return
        data[latest_due] = frag
        p.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[timeline_snap] save failed: {e}", file=sys.stderr)


def _list_dates():
    if not _TIMELINE_DIR.exists():
        return []
    return sorted(f.stem[len("timeline_"):] for f in _TIMELINE_DIR.glob("timeline_*.json"))


def render_timeline_index(date: str | None = None):
    dates = _list_dates()
    if not dates:
        return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"{biglot_dashboard.ARC_CSS}</head><body><h3>每30分快照 <a href='/'>←即時</a></h3>"
                "<div class='meta'>尚無快照(今天開盤後才會開始累積)</div></body></html>")
    sel = date if date in dates else dates[-1]
    try:
        data = json.loads(_timeline_path(sel).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    date_links = "".join(
        f"<a href='/timeline?date={d}' style='margin-right:8px{';font-weight:700' if d == sel else ''}'>{d}</a>"
        for d in reversed(dates))
    slot_links = "".join(
        f"<li><a href='/timeline?date={sel}&hhmm={s}' target='_blank'>{s}</a></li>"
        for s in TIMELINE_SLOTS if s in data)
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=0.8'>"
            f"<title>每30分快照</title>{biglot_dashboard.ARC_CSS}</head><body>"
            "<h3>每30分快照 <a href='/'>←即時</a></h3>"
            f"<div class='meta' style='margin-bottom:8px'>{date_links}</div>"
            f"<ul>{slot_links or '<li>該日尚無快照</li>'}</ul>"
            "<div class='meta'>只收 09:00~13:30(10個時間點,不含13:45期貨收盤,見本檔開頭說明)。"
            "頁面是當時的完整凍結畫面,不會即時更新;點標題排序/凍結欄那些互動功能是即時頁面專用,"
            "這裡不生效(純顯示歷史畫面)。</div></body></html>")


def render_timeline_view(date: str, hhmm: str):
    try:
        data = json.loads(_timeline_path(date).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    frag = data.get(hhmm)
    if frag is None:
        return (f"<!DOCTYPE html><html><head>{biglot_dashboard.ARC_CSS}</head><body>"
                f"無 {html_mod.escape(date)} {html_mod.escape(hhmm)} 快照 "
                f"<a href='/timeline'>返回索引</a></body></html>")
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=0.6'>"
            f"<title>{date} {hhmm} 快照</title>{biglot_dashboard.ARC_CSS}</head><body>"
            f"<h3>{date} {hhmm} 快照(凍結畫面,不會更新) <a href='/timeline?date={date}'>←索引</a></h3>"
            f"<div id='app'>{frag}</div></body></html>")
