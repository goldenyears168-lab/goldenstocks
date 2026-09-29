"""biglot dashboard 重構:一批「零呼叫其他頂層函式」的純參考資料 loader，從
biglot_dashboard.py 搬到這裡（見 docs/biglot-refactor-roadmap.md）。

跟 biglot/xq_style.py 用同一套規則，理由相同：這批函式讀的多個 biglot_dashboard.py
模組層級名稱（NAMES、TZ、SNAP_DIR、ATR_N、ATR_SQUEEZE_LOOKBACK、ATR_SQUEEZE_PCTL、
MIN_PREV_MARGIN_LOTS、MIN_PREV_LENDING_SHARES、VOLRISK_MIN_OBS、VOLRISK_STALE_DAYS、
VOLRISK_TIERS）本身雖然多數不在 18 個「整包重新賦值」危險全域清單裡，但為了保持
單一規則、不用一支一支去確認「這個安不安全」，一律 `import biglot_dashboard`
（模組本身，不指名字），在函式本體「呼叫當下」用 `biglot_dashboard.X` 屬性存取
——這樣不管 biglot_dashboard.py 未來有沒有把某個名稱也改成 `global X; X = ...`
整包重新賦值，這裡都不會抓到 stale reference，也不會在載入時撞到循環 import
（biglot_dashboard.py 在自己還沒執行到大多數全域賦值之前就會 import 這個新模組）。

其餘名稱（sqlite3/json/sys/datetime/defaultdict 等標準庫、stock_db.DATA_DIR／
DEFAULT_DB_PATH、source_dedup.dedup_query、compute_xq_style_metrics 的
_load_holder_tiers／_load_beta、biglot.utils 的 _pctile_rank）都不是
biglot_dashboard.py 自己定義的，直接從各自的真正來源模組匯入，不經過
biglot_dashboard。

純搬移，函式本體（含 docstring）逐字保留，只把讀 biglot_dashboard 全域的地方
從裸名改成 `biglot_dashboard.NAME` 屬性存取，不改任何邏輯。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

from compute_xq_style_metrics import _load_beta, _load_holder_tiers
from source_dedup import dedup_query
from stock_db import DATA_DIR, DEFAULT_DB_PATH

from biglot.utils import _pctile_rank

import biglot_dashboard


def _load_hist():
    """近5個快照:每檔前幾日大戶淨流/收盤、宇宙5日累積、昨日午後低。"""
    # 排除「當日」快照:對昨收/前n日大戶都該用 ≤昨日 的收盤;否則盤後重啟會抓到今收→漲跌恆0
    _today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
    files = [f for f in sorted(biglot_dashboard.SNAP_DIR.glob("eod_*.json")) if _today not in f.name][-5:]
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


def _load_prev_close_db():
    """昨收改抓官方 stock_daily_bars 的收盤競價價(權威),取代 EOD 快照的『最後一筆 tick』。
    快照 tick 收盤與官方收盤常差 1~2 檔,會讓漲跌%失真——南電 2026-09-21 快照 1055 vs
    官方 1060,今日漲停 1165 就被算成 +10.4%(超過±10%上限,不可能)。用『<今日的最近交易日』
    避免抓到今日盤中殘影;雙來源同價,取一筆即可。"""
    out = {}
    try:
        today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        sids = list(biglot_dashboard.NAMES)
        ph = ",".join("?" * len(sids))
        # 2026-09-27 DB清理Step2:改成一次撈全部42檔再Python分組,不要逐檔各開一次窗函數查詢
        # (逐檔查詢實測42次各~15秒,SQLite沒辦法對包了ROW_NUMBER()的巢狀子查詢逐檔下推索引;
        # 一次IN(...)撈完只要~1秒,跟既有_load_vol_risk_flags的寫法一致)。
        dd_sql = dedup_query("stock_daily_bars", ("stock_id", "trade_date"),
                              inner_where=f"WHERE stock_id IN ({ph}) AND trade_date<? AND close IS NOT NULL "
                                          f"AND trade_date>=date(?,'-30 day')")
        rows = conn.execute(
            f"SELECT stock_id, trade_date, close FROM ({dd_sql}) ORDER BY stock_id, trade_date DESC",
            (*sids, today, today)).fetchall()
        conn.close()
        seen = set()
        for sid, _td, c in rows:
            if sid in seen:
                continue
            seen.add(sid)
            if c:
                out[sid] = float(c)
    except Exception:
        pass
    return out


def _load_daily_trend():
    """每檔日線趨勢(截至最近日收盤):站上5日均線? 5日動能%。
    回測(127日隔夜候選池):壓縮∧站上5日線 +93.8bps/t5.10 vs 跌破 +30/t1.65,
    差+63.5bps;純脈絡欄+影子帳分層,不改選股規則。日線雙來源(finmind/tpex/twse)
    同價,按 trade_date 去重取一筆。"""
    out = {}
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        sids = list(biglot_dashboard.NAMES)
        ph = ",".join("?" * len(sids))
        today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
        # 一次撈全部42檔(45日曆天涵蓋21個交易日綽綽有餘)再Python分組,理由同 _load_prev_close_db。
        dd_sql = dedup_query("stock_daily_bars", ("stock_id", "trade_date"),
                              inner_where=f"WHERE stock_id IN ({ph}) AND trade_date>=date(?,'-45 day')")
        rows = conn.execute(
            f"SELECT stock_id, trade_date, close FROM ({dd_sql}) ORDER BY stock_id, trade_date DESC",
            (*sids, today)).fetchall()
        conn.close()
        by_sid = defaultdict(list)
        for sid, td, c in rows:
            if len(by_sid[sid]) < 21:
                by_sid[sid].append((td, c))
        for sid, sid_rows in by_sid.items():
            closes = [c for _, c in sid_rows if c]
            if len(closes) < 4:
                continue
            last = closes[0]
            ma5 = sum(closes[:5]) / len(closes[:5])
            ma10 = sum(closes[:10]) / len(closes[:10]) if len(closes) >= 6 else None
            ma20 = sum(closes[:20]) / len(closes[:20]) if len(closes) >= 20 else None   # 20MA(月線),供正乖離率欄
            ret5 = (last / closes[5] - 1) * 100 if len(closes) >= 6 and closes[5] else None
            out[sid] = {"above_ma5": last > ma5,
                        "above_ma10": (last > ma10) if ma10 else None,
                        "ma20": ma20,
                        "ret5d": ret5, "last": last, "asof": sid_rows[0][0]}
    except Exception as e:
        print(f"[daily_trend] load failed: {e}", file=sys.stderr)
    return out


KEY_LINE_RECENT_BARS = 20   # 觸發棒時效窗(交易日);2026-09-29 jack 定案,回測 N=10/20 皆過,取 20 覆蓋較高
KEY_LINE_LOOKBACK = 200     # 日線掃描窗(交易日):前高要 60 根 + 時效窗 + 緩衝


def _load_key_line(recent_bars=KEY_LINE_RECENT_BARS, lookback=KEY_LINE_LOOKBACK):
    """「關鍵一條線」(2026-09-25 jack 交辦,來源:YouTube《御錢術》楊育華分析師節目逐字稿)。

    規則(逐字稿精確化):某日 K 棒同時滿足下列三條件即為「觸發棒」,線 = 觸發棒的最低點(含影線):
      (a) 紅K(收盤>開盤) (b) 收盤漲幅>前一日收盤+4% (c) 收盤突破「前 60 個交易日最高收盤」(不含當日)。
    同一檔取**最近一次**觸發棒。

    ⚠ 2026-09-29 jack 定案改二分口徑(「能畫線才有數值」),舊版的「線永不作廢、掃 500 日」已停用。
    現在回傳四態,只有 alive 算「畫得出線」:
      base   = 掃描窗內完全沒有觸發棒
      stale  = 有觸發棒但已是 recent_bars(預設 20 個交易日)以前 → 線太舊,不算數
      broken = 觸發棒之後任一日「收盤」跌破線 → 破了就畫不出線(不可回復,要等下一根觸發棒)
      alive  = recent_bars 內有觸發棒且收盤未曾跌破 → 唯一顯示距離%的狀態
    作廢判定只看日收盤(PIT,最新收盤為止),盤中即時價跌到線下不算作廢,只會讓距離%變負。
    stale 與 broken 同時成立時歸 stale(與回測 F2 的分類順序一致)。

    2026-09-29 嚴謹回測(scripts/research/key_line_daily_rigorous.py F 段,
    scratch/key_line_daily_rigorous_2026-09-29.txt;21年史 2005~2026·IS/OOS 拆 2023·日聚類 SE·
    扣 42 檔等權籃子同期報酬,42 檔高波動宇宙):
      · 新定義(N=20 日時效 ∧ 跌破作廢)的 alive 狀態對未來相對報酬,IS/OOS 同號且三個持有期全過:
        10日 IS +29.4(t+3.95)/OOS +146.6(t+7.57);20日 IS +56.2(t+5.07)/OOS +281.0(t+8.79);
        60日 IS +227.6(t+9.70)/OOS +310.7(t+4.28)。alive 佔全樣本股-日 12.5%。
      · 舊定義(掃 500 日 ∧ 不作廢)同一支腳本重跑:60日 OOS 只剩 +55.0(t+0.37),等於熄火——
        線放到 500 日不作廢時「有線」覆蓋 94%,那個旗標幾乎恆為 1,測不出東西。
      · 合併是否損失資訊(四態同一迴歸,base 當基準,N=20):20日 OOS stale−base −87.6(t−6.55)、
        broken−base −58.3(t−2.90)、alive−base +199.3(t+8.67) —— stale/broken 跟 base 同一邊(都是負的),
        只有 alive 站在另一邊,所以把 stale/broken 併進「不能畫線」站得住,不是為了畫面好看硬併。
      · ⚠ 舊版欄位說明引用的「②無線要避開 OOS t+9.6~+15.6」**不可再引用**:那是拿 has_line(96%)對
        base(無觸發棒)比,而 base 在 OOS 期只有 n<50 列(這 42 檔近年人人都噴過),對照組形同不存在。
        新版比的是 alive vs 其餘(兩邊各萬列以上),才是有樣本的比較。
      · ⚠ 20/60 日持有期的觀測每日重疊,日聚類只處理同日橫斷面相關、沒處理序列重疊,t 值仍偏大;
        且宇宙是自選的 42 檔高波動股。此欄是**狀態顯示**,不是進場訊號,不進分數。
      · ① 節目主張「拉回線附近(±3%)買」仍是 DROP(勝率 42~43%、IS 96% 超額集中前 5 檔、扣成本轉負)。
    """
    out = {}
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        sids = list(biglot_dashboard.NAMES)
        ph = ",".join("?" * len(sids))
        today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
        # lookback 個交易日約需 lookback*1.6 個日曆天緩衝週末/假日
        cal_days = int(lookback * 1.6) + 30
        # trade_date<=today 上界:live 沒差(DB 最新就是昨收),但 fixture/回放是凍結時鐘,
        # 少了上界會讀到回放日之後的 K 棒——時效窗與「跌破作廢」都會被未來資料污染。
        dd_sql = dedup_query("stock_daily_bars", ("stock_id", "trade_date"),
                              inner_where=(f"WHERE stock_id IN ({ph}) AND trade_date<=? "
                                           f"AND trade_date>=date(?,'-{cal_days} day')"))
        all_rows = conn.execute(
            f"SELECT stock_id, trade_date, open, high, low, close FROM ({dd_sql}) "
            f"ORDER BY stock_id, trade_date DESC",
            (*sids, today, today)).fetchall()
        conn.close()
        by_sid = defaultdict(list)
        for sid, td, o, h, lo, c in all_rows:
            if len(by_sid[sid]) < lookback:
                by_sid[sid].append((td, o, h, lo, c))
        for sid, rows in by_sid.items():
            rows = [r for r in rows if all(r[1:])][::-1]   # 反轉成由舊到新,才能用 i-60:i 當「前 60 日」
            if len(rows) < 65:
                continue
            closes = [r[4] for r in rows]
            n = len(rows)
            trig_i = None
            for i in range(60, n):
                _, o, h, lo, c = rows[i]
                if c > o and c > closes[i - 1] * 1.04 and c > max(closes[i - 60:i]):
                    trig_i = i
            if trig_i is None:
                out[sid] = {"state": "base"}
                continue
            line_price, line_date = rows[trig_i][3], rows[trig_i][0]
            age = n - 1 - trig_i                                    # 觸發棒距今幾個交易日
            broken = any(closes[k] < line_price for k in range(trig_i, n))
            state = "stale" if age > recent_bars else ("broken" if broken else "alive")
            out[sid] = {"state": state, "price": line_price, "date": line_date,
                        "age": age, "broken": broken}
    except Exception as e:
        print(f"[key_line] load failed: {e}", file=sys.stderr)
    return out


def _load_pe_peer():
    """本益比同族群排名(2026-09-25 jack 交辦,依楊育華分析師《御錢術》節目邏輯:同族群比、不跨族群比)。

    讀 scripts/research/pe_peer_group_research.py 產生的靜態快照(scratch/pe_peer_group_*.json)。
    快照內存的是 TTM(近四季已公布)EPS + 用「快照當時最近收盤價」算出的本益比;EPS 每季才變,
    快照可以放著不必每次渲染重抓 FinMind——但分子(價格)不該是快照的舊收盤價,渲染時用即時價重算
    (見 _score_rows 的 r["pe_live"]),這才符合她說的「EPS慢、股價快,本益比要用即時股價每天重算」。

    ⚠ 誠實揭露:她的方法分母是「預估EPS」(法說會/營收/毛利率推算的未來EPS),我們沒有分析師預估
    EPS 的資料源,只能用「已公布 TTM EPS」——落後指標,不是預估指標。這是與原方法唯一的實質差異。
    族群清單沿用既有 SUBCAT 細分類人工擴充真實上市櫃同業,已用 TaiwanStockInfo 驗證代號存在,
    多數細分族群天生只有 3~8 檔真實同業,遠不到她說的 20~30 檔,如實呈現不硬湊。
    """
    path = DATA_DIR.parent / "scratch" / "pe_peer_group_2026-09-25.json"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        table = d.get("table", {})
        eps = {}
        for _rows in table.values():
            for _r in _rows:
                if _r.get("eps_ttm") is not None:
                    eps[_r["sid"]] = (_r["eps_ttm"], _r.get("eps_asof"))
        return table, d.get("peers", {}), d.get("generated"), eps
    except Exception as e:  # noqa: BLE001
        print(f"[pe_peer] load failed: {e}", file=sys.stderr)
        return {}, {}, None, {}


def _load_etf981_holdings():
    """00981A(中信ARK創新)持股市值 + 對前一快照的變動金額(2026-09-27 jack 交辦)。

    讀 etf_holdings(etf_code='00981A')最新兩個 snapshot_date 的 amount 欄(ezmoney 快照
    當日市值=股數×當時收盤價,非即時重算)。只在最新快照出現=新進(視為從 0 增加);
    只在前一快照出現=出清(視為降到 0)——兩者都是真實變動金額,不是資料缺漏。
    與跟單研究線 00981a-l1h9(見 copytrade_l1h9_daily.py)共用同一張表,純展示欄,
    不進分數、不影響任何評分或訊號。
    """
    out: dict[str, dict] = {}
    asof = prev_asof = None
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        dates = [row[0] for row in conn.execute(
            "SELECT DISTINCT snapshot_date FROM etf_holdings WHERE etf_code='00981A' "
            "ORDER BY snapshot_date DESC LIMIT 2").fetchall()]
        if dates:
            asof = dates[0]
            prev_asof = dates[1] if len(dates) > 1 else None
            cur = dict(conn.execute(
                "SELECT stock_id, amount FROM etf_holdings WHERE etf_code='00981A' AND snapshot_date=?",
                (asof,)).fetchall())
            prev = dict(conn.execute(
                "SELECT stock_id, amount FROM etf_holdings WHERE etf_code='00981A' AND snapshot_date=?",
                (prev_asof,)).fetchall()) if prev_asof else {}
            for sid, amt in cur.items():
                out[sid] = {"amount": amt or 0.0, "delta": (amt or 0.0) - (prev.get(sid) or 0.0)}
            for sid, amt in prev.items():
                if sid not in out:
                    out[sid] = {"amount": 0.0, "delta": -(amt or 0.0)}
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[etf981] load failed: {e}", file=sys.stderr)
    return out, asof, prev_asof


def _load_atr_state(n_bars=260):
    """ATR(平均真實區間)盤整壓縮/突破狀態(2026-09-25 jack 交辦,來源:YouTube《御錢術》楊育華分析師
    節目 ATR 段落 + Wilder《New Concepts in Technical Trading Systems》1978 原始定義)。

    TR(真實區間) = max(高−低, |高−昨收|, |低−昨收|);ATR14 = Wilder 平滑(遞迴:
    ATR_t=(ATR_{t-1}×13+TR_t)/14,種子=前14筆TR簡單平均)。「壓縮」定義=今日ATR%(=ATR14÷收盤)
    落在近120個交易日自身歷史的後30%分位(自身相對壓縮,非跨股比較——呼應本案已確立的
    「固定%門檻跨時段不可比較,正規化須用個股自身近期慣性」原則)。

    本函式只算到「昨收為止」已知的 ATR14/ATR%/壓縮旗標;「異常」(今日真實區間>1.5×此ATR14)
    需要今天的高低,由 _score_rows 用即時 ST.day 現算,避免用到未來資訊。

    ⚠ 2026-09-25 嚴謹回測(scripts/research/atr_key_line_research.py,scratch/atr_key_line_research_2026-09-25.txt,
    21年史·IS/OOS拆2023·日聚類SE·扣42檔等權籃子同期報酬·扣50bps成本·安慰劑·集中度·逐年,僅限這42檔):
      · 「壓縮→突破」事件本身(不論方向、不論是否貼近關鍵一條線):DROP。10/40/60日持有期 IS/OOS
        異號、安慰劑5組範圍完全蓋過真實事件均值(統計上與隨機日不可區分)、前5檔貢獻佔比達354%
        (比關鍵一條線已否決的96%集中度更極端,逐年正負交替無穩定方向)。不進分數,純描述性狀態顯示。
      · 突破事件『恰好貼近關鍵一條線(±1倍ATR內)』是本次唯一 IS/OOS 同號的子集(IS t+1.66、
        OOS t+1.80),方向一致但仍未過本案嚴格門檻(|t_OOS|≥2),UI 標記★近線僅供觀察、不進分數。
      · 用『距離÷ATR』取代關鍵一條線原本的『距離%』重跑橫斷面IC:OOS t 由 +1.26 小幅升至 +1.63,
        方向一致但同樣未過門檻,只當研究記錄,關鍵一條線欄位主指標仍用距離%不換。
    """
    out = {}
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        sids = list(biglot_dashboard.NAMES)
        ph = ",".join("?" * len(sids))
        today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
        cal_days = int(n_bars * 1.6) + 30
        dd_sql = dedup_query("stock_daily_bars", ("stock_id", "trade_date"),
                              inner_where=f"WHERE stock_id IN ({ph}) AND trade_date>=date(?,'-{cal_days} day')")
        all_rows = conn.execute(
            f"SELECT stock_id, trade_date, high, low, close FROM ({dd_sql}) "
            f"ORDER BY stock_id, trade_date DESC",
            (*sids, today)).fetchall()
        conn.close()
        by_sid = defaultdict(list)
        for sid, td, h_, lo_, c_ in all_rows:
            if len(by_sid[sid]) < n_bars:
                by_sid[sid].append((td, h_, lo_, c_))
        for sid, rows in by_sid.items():
            rows = [r for r in rows if all(r[1:])][::-1]
            if len(rows) < biglot_dashboard.ATR_SQUEEZE_LOOKBACK + biglot_dashboard.ATR_N + 20:
                continue
            h = [r[1] for r in rows]; lo = [r[2] for r in rows]; c = [r[3] for r in rows]
            tr = [None] * len(rows)
            for i in range(1, len(rows)):
                tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
            atr = [None] * len(rows)
            atr[biglot_dashboard.ATR_N] = sum(tr[1:biglot_dashboard.ATR_N + 1]) / biglot_dashboard.ATR_N
            for i in range(biglot_dashboard.ATR_N + 1, len(rows)):
                atr[i] = (atr[i - 1] * (biglot_dashboard.ATR_N - 1) + tr[i]) / biglot_dashboard.ATR_N
            atr_pct = [(atr[i] / c[i]) if atr[i] else None for i in range(len(rows))]
            valid_idx = [i for i in range(len(atr_pct)) if atr_pct[i] is not None]
            if len(valid_idx) < biglot_dashboard.ATR_SQUEEZE_LOOKBACK + 1:
                continue
            last_i = valid_idx[-1]
            hist = [atr_pct[i] for i in valid_idx[-biglot_dashboard.ATR_SQUEEZE_LOOKBACK - 1:-1]]
            cur = atr_pct[last_i]
            pctl = sum(1 for x in hist if x < cur) / len(hist)
            out[sid] = {"atr14": atr[last_i], "atr_pct": cur * 100, "pctl": pctl,
                        "squeeze": pctl <= biglot_dashboard.ATR_SQUEEZE_PCTL, "asof": rows[last_i][0]}
    except Exception as e:  # noqa: BLE001
        print(f"[atr_state] load failed: {e}", file=sys.stderr)
    return out


def _load_xq_style():
    """XQ全球贏家風格欄位(2026-09-27 jack 交辦):讀 compute_xq_style_metrics.py 算好寫進
    stock_xq_style_daily 的最新一列(日頻技術/籌碼欄)。純展示欄，不進分數。逐欄公式/來源見
    該腳本 docstring。

    2026-09-27 DB清理路線圖 Step 3 拆表後,800大戶/10散戶持股%與Beta不再存在這張日頻表裡
    (兩者都不是「日頻事實」,存成本表欄位只會製造過期問題),改成這裡直接呼叫
    compute_xq_style_metrics 的 _load_holder_tiers()/_load_beta() 即時查詢
    stock_holding_dispersion_weekly/stock_beta,merge 回同一個 dict。"""
    live_keys = ("holder_asof_week", "big800_holder_pct", "big800_holder_pct_chg_w",
                 "retail10_holder_pct", "retail10_holder_pct_chg_w", "beta", "beta_asof")
    out = {}
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        for sid in biglot_dashboard.NAMES:
            row = conn.execute(
                "SELECT trade_date, turnover_pct, ret_chg5d_pct, sma_20d, ema_20d, ema_sma_20d_diff, "
                "macd_dif, macd_dea, macd_hist, hist_vol_20d_pct, concentration_pct, "
                "foreign_net_pct, trust_net_pct, dealer_net_pct, sbl_sell_chg1d, sbl_sell_chg5d, "
                "daytrade_pct, foreign_holding_pct, block_volume, block_amount, block_count "
                "FROM stock_xq_style_daily WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1",
                (sid,)).fetchone()
            if not row:
                continue
            keys = ("asof", "turnover_pct", "ret_chg5d_pct", "sma_20d", "ema_20d", "ema_sma_20d_diff",
                    "macd_dif", "macd_dea", "macd_hist", "hist_vol_20d_pct", "concentration_pct",
                    "foreign_net_pct", "trust_net_pct", "dealer_net_pct", "sbl_sell_chg1d", "sbl_sell_chg5d",
                    "daytrade_pct", "foreign_holding_pct", "block_volume", "block_amount", "block_count")
            d = dict(zip(keys, row))
            d.update(dict.fromkeys(live_keys))  # 先全部補 None,下面即時查詢查得到才覆蓋,確保鍵永遠存在
            # 即時查詢(不落地):800大戶/10散戶持股%(週頻)+ Beta(非時間序列,只有最新值)
            weeks_sorted, big800_wk, retail10_wk = _load_holder_tiers(conn, sid)
            if weeks_sorted:
                wk = weeks_sorted[-1]
                d["holder_asof_week"] = wk
                d["big800_holder_pct"] = big800_wk.get(wk)
                d["retail10_holder_pct"] = retail10_wk.get(wk)
                if len(weeks_sorted) >= 2:
                    pwk = weeks_sorted[-2]
                    if d["big800_holder_pct"] is not None and big800_wk.get(pwk) is not None:
                        d["big800_holder_pct_chg_w"] = d["big800_holder_pct"] - big800_wk[pwk]
                    if d["retail10_holder_pct"] is not None and retail10_wk.get(pwk) is not None:
                        d["retail10_holder_pct_chg_w"] = d["retail10_holder_pct"] - retail10_wk[pwk]
            beta_val, beta_asof = _load_beta(conn, sid)
            if beta_val is not None:
                d["beta"] = beta_val
                d["beta_asof"] = beta_asof
            out[sid] = d
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[xq_style] load failed: {e}", file=sys.stderr)
    return out


def _load_vixtwn():
    """台灣VIX(2026-09-27 jack 交辦「稽核」後同意放進個別頁面):market_vix_daily 表,
    vixtwn-daily-sync launchd job 每日產生,市場層級(非個股),各詳情頁共用同一組數字。"""
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT date, close FROM market_vix_daily WHERE symbol='VIXTWN' "
            "ORDER BY date DESC LIMIT 2").fetchall()
        conn.close()
        if not rows:
            return {}
        cur = rows[0]
        prev = rows[1] if len(rows) > 1 else None
        chg = ((cur[1] / prev[1] - 1) * 100) if (prev and prev[1]) else None
        return {"asof": cur[0], "close": cur[1], "chg_pct": chg}
    except Exception as e:  # noqa: BLE001
        print(f"[vixtwn] load failed: {e}", file=sys.stderr)
        return {}


def _load_vol_risk_flags():
    """算出每檔股票「最新一筆」融資/借券變化幅度(不分方向)的歷史分位平均分數。
    來源去重統一走 source_dedup.dedup_query(2026-09-27 DB清理路線圖 Step 2 SSOT),
    不再各自刻 ROW_NUMBER/漏刻去重。"""
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    sids = list(biglot_dashboard.NAMES)
    ph = ",".join("?" * len(sids))
    mg_by_sid = defaultdict(list)
    mg_sql = dedup_query("stock_margin_daily", ("stock_id", "trade_date"),
                          inner_where=f"WHERE stock_id IN ({ph})")
    for sid, td, bal in conn.execute(
            f"SELECT stock_id, trade_date, margin_balance FROM ({mg_sql}) "
            f"ORDER BY stock_id, trade_date", sids):
        mg_by_sid[sid].append((td, bal))
    ln_by_sid = defaultdict(list)
    ln_sql = dedup_query("stock_lending_balance_daily", ("stock_id", "trade_date"),
                          inner_where=f"WHERE stock_id IN ({ph})")
    for sid, td, prev_bal, bal in conn.execute(
            f"SELECT stock_id, trade_date, prev_balance, lending_balance FROM ({ln_sql}) "
            f"ORDER BY stock_id, trade_date", sids):
        ln_by_sid[sid].append((td, prev_bal, bal))
    conn.close()

    out = {}
    for sid in sids:
        m, l = mg_by_sid.get(sid, []), ln_by_sid.get(sid, [])
        if len(m) < biglot_dashboard.VOLRISK_MIN_OBS + 1 or len(l) < biglot_dashboard.VOLRISK_MIN_OBS:
            continue
        m_dates, m_pct = [], []
        for i in range(1, len(m)):
            prev, cur = m[i - 1][1], m[i][1]
            if prev and prev >= biglot_dashboard.MIN_PREV_MARGIN_LOTS and cur is not None:
                m_dates.append(m[i][0])
                m_pct.append((cur - prev) / prev)
        l_dates, l_pct = [], []
        for td, prev, bal in l:
            if prev and prev >= biglot_dashboard.MIN_PREV_LENDING_SHARES and bal is not None:
                l_dates.append(td)
                l_pct.append((bal - prev) / prev)
        if len(m_pct) < biglot_dashboard.VOLRISK_MIN_OBS or len(l_pct) < biglot_dashboard.VOLRISK_MIN_OBS:
            continue
        days_stale = max(
            (biglot_dashboard.datetime.now(biglot_dashboard.TZ).date() - datetime.strptime(d, "%Y-%m-%d").date()).days
            for d in (m_dates[-1], l_dates[-1])
        )
        if days_stale > biglot_dashboard.VOLRISK_STALE_DAYS:
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
        tier = next((badge for thr, badge in biglot_dashboard.VOLRISK_TIERS if score >= thr), None)
        out[sid] = {
            "score": score, "tier": tier, "stale": False, "days_stale": days_stale,
            "margin_asof": m_dates[-1], "lending_asof": l_dates[-1],
            "margin_pct": m_pct[-1], "lending_pct": l_pct[-1],
            "margin_abs_pctile": m_last, "lending_abs_pctile": l_last,
        }
    return out


def _snap_dates():
    return sorted(p.name[4:14] for p in biglot_dashboard.SNAP_DIR.glob("eod_*.json"))


def _load_snap(d):
    try:
        return json.load(open(biglot_dashboard.SNAP_DIR / f"eod_{d}.json"))
    except Exception:
        return None


def _load_holder_big800():
    """集保戶股權分散表(2026-09-29 jack 交辦):大戶(持股≥800張)比例%,週頻(TDCC 每週五公告,
    優先 tdcc 來源,缺值退回 finmind;邏輯與 compute_xq_style_metrics._load_holder_tiers 的
    big800 定義相同,這裡是「一次撈全宇宙、啟動時載入一次」的批次版——該函式是逐檔即時查詢,
    不適合被 render() 每秒對 42 檔各呼叫一次)。

    ⚠ 已知不可交易,僅供參考顯示:
    - 文獻查證(chip-signal-literature-verdicts 記憶)對「集保戶股權分散表」這個資料源本身
      Google Scholar 零同儕審查支持,唯一像樣的實證是廠商回測 IC≈0.01(等同雜訊)。
    - 本系統自己用同一份資料做的 HS 因子(散戶持股比,hs-factor-real-but-not-tradeable 記憶)
      通過五項對抗檢定(自相關/產業中性化/PIT緩衝/開收穩健/月份集中度)、原始 t=+4.23~4.45,
      但加控週轉率後年化淨值從 +5.44% 轉為 −0.14%,再控股價水準/產業後惡化到 −3.33%/年——
      結論是「低週轉率(流動性)溢酬」的代理,不是真正的籌碼 alpha。
    純參考展示欄,不進分數、不影響任何排序邏輯。週頻資料,跟儀表板其他盤中即時欄位不同尺度。
    """
    out = {}
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        sids = list(biglot_dashboard.NAMES)
        ph = ",".join("?" * len(sids))
        rows = conn.execute(
            f"SELECT stock_id, as_of_date, level_lo, percent, source FROM stock_holding_dispersion_weekly "
            f"WHERE stock_id IN ({ph}) AND level_lo IS NOT NULL ORDER BY stock_id, as_of_date", sids).fetchall()
        conn.close()
        by_sid_week: dict = defaultdict(lambda: defaultdict(dict))
        src_used: dict = defaultdict(lambda: defaultdict(dict))
        for sid, asof, lo, pct, src in rows:
            if pct is None:
                continue
            cur_src = src_used[sid][asof].get(lo)
            if cur_src is None or (cur_src != "tdcc" and src == "tdcc"):
                by_sid_week[sid][asof][lo] = pct
                src_used[sid][asof][lo] = src
        for sid, weeks in by_sid_week.items():
            latest = max(weeks)
            tiers = weeks[latest]
            big800 = sum(v for lo, v in tiers.items() if lo >= 800001)
            out[sid] = (big800, latest)
    except Exception as e:  # noqa: BLE001
        print(f"[holder_big800] load failed: {e}", file=sys.stderr)
    return out


def _refresh_vol_risk_if_needed() -> bool:
    """依實際日曆日期(非 ST.date)刷新——T-1 籌碼資料跟有沒有開盤無關，不该被
    ingest()/render() 只在盤中才跑的邏輯卡住,否則開盤前使用者看到的都是前一個
    交易日收盤時算出的舊分數(2026-09-21 發現:盤前完全看不到當天該有的分數)。
    回傳是否真的重算了,讓呼叫端決定要不要順便重繪一次盤後定格頁面。
    """
    today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
    if biglot_dashboard.VOLRISK_DATE == today:
        return False
    try:
        biglot_dashboard.VOLRISK = _load_vol_risk_flags()
    except Exception:
        biglot_dashboard.VOLRISK = {}
    biglot_dashboard.VOLRISK_DATE = today
    return True
