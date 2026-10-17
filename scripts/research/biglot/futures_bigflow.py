"""biglot dashboard:現貨+個股期貨「大戶」流量加權合併欄(2026-10-06 jack 交辦)。

背景:jack 提出假說——部分大戶不賣現股、改在個股期貨開空單避險,只看現貨大戶流量會
漏接這種行為,因此平常應把現貨跟期貨的大戶流量加權合併看,背離時特別標記。

⚠ 2026-10-06 探索性研究(scripts/research/biglot_spot_futures_bigflow_divergence_explore.py,
9/3~10/6 共 24 個交易日、523 筆股票-日樣本)先查過這個假說本身:
  - 現貨大戶淨額與期貨大戶淨額跨股跨日 Pearson 相關 +0.49(同向為主,不是對沖關係)
  - 「現貨平淡∧期貨單邊」這種最直接對應避險假說的型態只佔 1.7%,且部分樣本落在期貨
    tick 才剛開始收集的頭幾天,不排除是資料不完整的假象
  - 「現貨跟期貨都有大戶單但方向相反」占 12%,但型態上是「現貨暴量同日期貨小幅反向」,
    更像價差套利/造市商對敲在收斂基差,不是「不賣股票、改期貨避險」的訊號特徵
  → 本欄位的合併與背離標記**只做觀察展示,不進淨分、不是證實的訊號**,tooltip 需誠實
    揭露以上研究結論,不能包裝成方向訊號。

口徑:
  - 現貨大戶淨額直接讀現有 `ds["big"]`(ST.day[sid]["big"],逐筆累加,trade_ingest.py 已算好)。
  - 期貨大戶淨額:逐筆讀 `{root}_trades_{today}.jsonl`(collect_stock_futures_books.py 落地),
    單筆名目金額 = 價格 × size(口) × contract_size(股/口) ≥ BIG_AMT(1000萬)才算大戶,
    主動方向用 price==ask/price==bid 判定(跟現貨 TickType 缺位時同一套 heuristic)。
  - 加權合併 = 現貨大戶淨額 + 期貨大戶淨額 × FUT_BIGFLOW_WEIGHT。權重取自上述探索性研究的
    現貨/期貨大戶單日|淨額|中位數比值(≈10.37,取整數10)——這是權宜校準,不是嚴謹估計,
    累積更多資料後應該重新估。
  - 背離標記:現貨與期貨大戶淨額方向相反,且期貨大戶淨額(原始、未加權)絕對值
    ≥ FUT_BIGFLOW_DIVERGE_MIN(3000萬,約當3筆大戶單)才標,避免雜訊觸發。

跟 biglot/mini_futures.py(已於 2026-10-05 移除)結構類似但目的不同:那支是用小型契約
1口成交當「期貨市場散戶代理」,這支是用全部42檔的一般+mini契約逐筆成交算「期貨大戶」,
服務對象不同,不要混用。

2026-10-12 jack 交辦(08:45 期貨沒有大戶/散戶數據):
  - 期貨累計從 08:45:01 起(08:45:00 為開盤集合競價撮合價,無主動方);只收今日、≤13:46 的成交,
    濾掉殭屍重送/非今日 trade_time。
  - 大戶:名目 ≥ BIG_AMT(1000萬);08:45–09:00(現貨尚未開盤)標準契約改用口數門檻 ≥ FUT_BIG_LOTS_OPEN(10 口)。
    小型契約(100 股/口)10 口名目太小,不套口數門檻。
  - 散戶:標準契約 size==1(1 口);小型契約不算(1 口名目≈標準的 1/20,不可比)。
  - 併入「全日大戶」「全日散戶」兩個既有欄(`fut_merge_day_td`),不另開欄;現貨 + 期貨×權重。
    大戶權重 10(2026-10-06);散戶權重 8(2026-10-12 校準:15 日 136 股日現貨散戶/期貨1口 |淨額| 中位數比 8.4,
    相關 0.37、同號率 57%,證據弱)。純觀察展示,不進分數。

依 docs/biglot-refactor-roadmap.md 規則:只 `import biglot_dashboard`,全域一律
`biglot_dashboard.NAME` 屬性存取(`FUT_ROOT`/`FUT_CONTRACT_SIZE`/`FUT_BIGFLOW`/
`FUT_BIGFLOW_WEIGHT`/`FUT_BIGFLOW_DIVERGE_MIN`/`BIG_AMT`)。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from stock_db import DATA_DIR

import biglot_dashboard

_TZ = timezone(timedelta(hours=8))
_FUT_OPEN_SEC = 8 * 3600 + 45 * 60
_SPOT_OPEN_SEC = 9 * 3600
_FUT_END_SEC = 13 * 3600 + 46 * 60


def _sec_of_day(trade_time_us, today: str):
    """trade_time(µs epoch,UTC+8)→當日秒數;非今日或缺值回 None。"""
    if not trade_time_us:
        return None
    t = datetime.fromtimestamp(float(trade_time_us) / 1e6, _TZ)
    if t.strftime("%Y-%m-%d") != today:
        return None
    return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6


def _ingest_fut_bigflow(today: str) -> None:
    """每輪:增量讀各 root 的 `{root}_trades_{today}.jsonl`,累加今日期貨大戶/散戶(1 口)淨額與筆數到
    `biglot_dashboard.FUT_BIGFLOW`。serial 去重(富邦 ws 收盤後會殭屍重送最後一筆成交,
    同 serial 可能被重複推播數十次,見 memory「富邦ws trades讀法陷阱」)。"""
    st = biglot_dashboard.FUT_BIGFLOW
    if st["day"] != today:
        st.update({"day": today, "off": {}, "net": {}, "n": {}, "rnet": {}, "rn": {}, "seen": {}})
    for sid, root in biglot_dashboard.FUT_ROOT.items():
        f = DATA_DIR.parent / "cache" / f"{root}_trades" / f"{root}_trades_{today}.jsonl"
        if not f.exists():
            continue
        try:
            with f.open("rb") as fh:
                off = st["off"].get(root, 0)
                fh.seek(off)
                raw = fh.read()
        except Exception:  # noqa: BLE001
            continue
        cut = raw.rfind(b"\n")      # 只吃到最後一個換行,避免讀到寫到一半的尾行而永久丟掉那筆
        if cut < 0:
            continue
        st["off"][root] = off + cut + 1
        chunk = raw[:cut + 1].decode("utf-8", errors="replace")
        csize = biglot_dashboard.FUT_CONTRACT_SIZE.get(sid, 2000.0)
        standard = csize >= 2000.0
        seen = st["seen"].setdefault(root, set())
        net, cnt = st["net"].get(sid, 0.0), st["n"].get(sid, 0)
        rnet, rcnt = st["rnet"].get(sid, 0.0), st["rn"].get(sid, 0)
        for line in chunk.splitlines():
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            key = d.get("serial")
            if key is None:
                key = (d.get("trade_time"), d.get("price"), d.get("size"))
            if key in seen:
                continue
            seen.add(key)
            sec = _sec_of_day(d.get("trade_time"), today)
            if sec is None or sec < _FUT_OPEN_SEC + 1 or sec > _FUT_END_SEC:
                continue   # 非今日/殭屍重送/開盤集合競價撮合價(08:45:00)/收盤後
            px, sz = d.get("price"), d.get("size")
            if px is None or sz is None:
                continue
            px, sz = float(px), float(sz)
            b, a = d.get("bid"), d.get("ask")
            side = 1 if (a is not None and px >= float(a)) else (
                -1 if (b is not None and px <= float(b)) else 0)
            if side == 0:
                continue   # 無法判斷主動方向,寧可漏算不要亂算(跟探索腳本同規則)
            amt = px * sz * csize
            if amt >= biglot_dashboard.BIG_AMT or (
                    standard and sec < _SPOT_OPEN_SEC and sz >= biglot_dashboard.FUT_BIG_LOTS_OPEN):
                net += side * amt
                cnt += 1
            elif standard and sz == 1:
                rnet += side * amt
                rcnt += 1
        st["net"][sid], st["n"][sid] = net, cnt
        st["rnet"][sid], st["rn"][sid] = rnet, rcnt


def fut_merge_day_td(r: dict, kind: str, spot, unm: bool = False):
    """全日大戶(kind='big')/全日散戶(kind='ret')欄:現貨 + 期貨×權重。期貨尚無該類成交回 None(呼叫端沿用原欄)。
    現貨尚未開盤(08:45–09:00)spot 為 None,只顯示期貨部分。純觀察,不進分數。"""
    if unm:
        return None
    sid = r["sid"]
    big = kind == "big"
    st = biglot_dashboard.FUT_BIGFLOW
    n = st.get("n" if big else "rn", {}).get(sid, 0)
    if n == 0:
        return None
    fut = st.get("net" if big else "rnet", {}).get(sid, 0.0)
    w = biglot_dashboard.FUT_BIGFLOW_WEIGHT if big else biglot_dashboard.FUT_RETFLOW_WEIGHT
    spot_v = spot or 0.0
    combined = spot_v + fut * w
    cls = "up" if combined > 0 else ("dn" if combined < 0 else "")
    badge = ""
    if big and spot and abs(fut) >= biglot_dashboard.FUT_BIGFLOW_DIVERGE_MIN and (spot > 0) != (fut > 0):
        badge = (" <b style='color:#f0883e' title='背離:現貨與期貨大戶方向相反。2026-10-06探索性研究(24日樣本):"
                 "這種型態多半符合現貨-期貨價差套利特徵,不是證實的避險訊號,僅供觀察'>⇄</b>")
    what = "大戶" if big else "散戶"
    rule = "≥1000萬;08:45–09:00 為≥10口" if big else "1口"
    tip = (f"現貨全日{what}淨額 {spot_v:+,.0f} + 期貨{what}({rule})淨額 {fut:+,.0f}"
           f"(×{w:g}權重、{n}筆) = {combined:+,.0f}。期貨自 08:45:01 起累計。"
           + ("權重取自2026-10-06探索性研究(現貨/期貨大戶|淨額|中位數比≈10.37,取整)。現貨-期貨大戶淨額相關+0.49,"
              "同向為主、不是對沖。" if big else
              "權重取自2026-10-12校準(15日136股日,現貨散戶/期貨1口|淨額|中位數比8.4;相關0.37、同號率57%,證據弱)。")
           + "屬權宜校準非嚴謹估計;純觀察展示,不進分數。")
    return (f"<td class='{cls}' data-sort='{combined}' title='{tip}'>{combined/1e4:+,.0f}"
            f"<sup style='font-size:8px;color:#8b949e'>期</sup>{badge}</td>")
