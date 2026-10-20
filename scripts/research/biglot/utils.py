"""biglot dashboard 重構 Phase 1：從 biglot_dashboard.py 搬出的純函式。

搬移依據：scripts/research/biglot_phase0/dep_graph.py 對 biglot_dashboard.py
做 AST 依賴分析，確認以下每個函式都是「零全域變數依賴、零呼叫其他頂層函式」的
真葉節點（見 docs/biglot-refactor-roadmap.md Phase 0 段落）。純搬移，函式本體
逐字保留，不改行為。
"""
from __future__ import annotations

import time

import biglot_dashboard


def _tick_sz(p):
    return (0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100
            else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0)


def bucket_key(dt):
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def _pctile_rank(values):
    """回傳每個元素在序列中的百分位排名(0~1,含自己;越大越極端)。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for pos, i in enumerate(order):
        ranks[i] = (pos + 1) / len(values)
    return ranks


#: (tag, 方向, 時距秒, 條件(r)->bool|None, 反向失效(r)->bool, 顯示名(r)->str) —— 供
#: biglot_dashboard.py 的 TAG_DEFS 使用，三個函式本身跟 TAG_DEFS 資料結構無耦合。
def _par30(r):
    return None if (r.get("rbuy30_r") is None or r["unm"]) else (r["rbuy30_r"] + r["rsell30_r"])


def _b30n(r):
    return (r["big30_r"] / r["tot30_r"] * 100) if (r.get("big30_r") is not None and r.get("tot30_r")) else None


def _b5n(r):
    return (r["big5_r"] / r["tot5_r"] * 100) if (r.get("big5_r") is not None and r.get("tot5_r")) else None


def _r30n(r):
    """近30分散戶淨額÷近30分成交額(%),跟 _b30n 同一個算法、retail 版。2026-10-04新增,
    供散戶背離(SMFI)欄位的滾動窗版本使用(見 biglot_dashboard.py::SMFI_ACTIVE_FROM)。"""
    return (r["retn30_r"] / r["tot30_r"] * 100) if (r.get("retn30_r") is not None and r.get("tot30_r")) else None


def _limits(pc):
    """台股漲跌停價(±10%,對齊 tick):漲停=不超過+10%的最大tick、跌停=不低於−10%的最小tick。"""
    import math
    up, dn = pc * 1.1, pc * 0.9
    return math.floor(up / _tick_sz(up)) * _tick_sz(up), math.ceil(dn / _tick_sz(dn)) * _tick_sz(dn)


def _limit_down(y: float) -> float:
    """跌停價 = 前收 ×0.9 無條件進位到升降單位(TWSE 規則)。"""
    import math
    raw = y * 0.9; t = _tick_sz(raw)  # 原本重複定義成 _stock_tick,2026-09-27 稽核後合併
    return round(math.ceil(raw / t - 1e-9) * t, 2)


def _in_market():
    n = biglot_dashboard.datetime.now(biglot_dashboard.TZ)
    # 08:30 起 = 期貨/現貨盤前試撮(2026-09-24:launchd 也提前到 08:30),試撮價要即時跳動
    return n.weekday() < 5 and "08:30" <= n.strftime("%H:%M") <= "13:35"


def _rolling_now(sid):
    """5分/30分這類「往回N秒」滾動窗該用哪個時間點當「現在」。

    2026-10-03 jack 糾正:原本所有呼叫處一律傳真實 `time.time()`,在盤中這是對的,
    但收盤後(含:當天已收盤但伺服器還開著、或任何時間重啟伺服器、或假日完全沒有
    開盤)`time.time()` 會一路前進,往回N秒的滾動窗會算到「現在」跟「最後一筆真實
    成交」之間那段完全沒有成交的空窗,導致5分/30分大戶/散戶/期散等欄位全部塌陷成
    空值——使用者看到的現象是「收盤幾小時後重開伺服器,現價/5分/30分全部變成
    —」,根因就是這裡。

    修法:盤中(`_in_market()`為真)才用真實時間;收盤後一律改用「該檔最後一筆
    已知成交時間」(`ST.last_seen[sid]`)當基準點,讓滾動窗凍結在收盤那一刻該有的
    樣子,不會因為伺服器重啟的真實時間點不同而產生不一致的結果——不論是原本
    收盤當下那次定格 render,還是任何時候重啟後的 render,算出來都該是同一個答案。
    查無該檔任何成交紀錄(當天完全沒開盤、或該檔今天從未成交)時退回 `time.time()`,
    這種情況下滾動窗本來就該算出空值,不是這支函式要處理的範圍。
    """
    if _in_market():
        return time.time()
    last = biglot_dashboard.ST.last_seen.get(sid)
    return last.timestamp() if last else time.time()


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
