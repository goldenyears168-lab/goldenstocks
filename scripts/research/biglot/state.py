"""biglot dashboard 重構：核心可變狀態容器 `class S`/`ST`，從
scripts/research/biglot_dashboard.py 逐字搬移，邏輯不改一行。

`ST` 是整個系統唯一的「當日解析狀態」單例，被 15+ 個已搬移模組透過
`biglot_dashboard.ST` 屬性存取讀寫（本檔案不需要、也不應該被其他模組直接
import——`ST` 這個名字繼續留在 `biglot_dashboard` 的模組命名空間裡，
`biglot_dashboard.py` 只是改成 `from biglot.state import S, ST` 而不是
自己 `class S: ...` / `ST = S()`，對外行為完全等價：`biglot_dashboard.ST`
這個屬性存取路徑不變，任何模組都不需要跟著改。

`ST` 這個名字本身從未被 `global ST; ST = ...` 整包重新賦值過（只有
`ST.__init__()` 在 `ingest()` 過日時呼叫，原地重置內部屬性），所以不需要
`import biglot_dashboard` + 屬性存取的手法——這裡就是 `ST` 的唯一定義處，
其他模組讀寫的都是同一個物件參照。
"""
from __future__ import annotations

from collections import defaultdict, deque


class S:
    """解析狀態（當日）。"""
    def __init__(self):
        self.date = None
        self.raw_off = 0
        self.book_off = 0
        self.lastvol = defaultdict(float)
        self.last_px = {}
        self.last_bid = {}; self.last_ask = {}     # 逐筆帶的買一/賣一(紙上交易掛價用)
        self.last_seen = {}
        self.first_done = set()
        self.buckets = {}                       # sid -> {bk: {...}}
        self.day = defaultdict(lambda: {"big": 0., "ret": 0., "ret2": 0., "mid": 0.,
                                        "tot": 0., "big_pm": 0., "px0": None, "hi": None, "lo": None,
                                        "ret_open": 0., "tot_open": 0., "ret_close": 0., "tot_close": 0.})   # 散戶開盤/尾盤段(SMFI 觀察欄)
        self.book = {}                          # sid -> latest snapshot
        # 每檔最近 ~60 分鐘逐筆 (ts, px, amt, sgn, is_big, is_retail):供 5分/30分 欄位**每秒滾動窗**
        # (2026-09-23 jack 要求)。標籤/旗標仍依完成的 5 分桶判定(=127 日回測定義),不走這裡。
        self.recent = defaultdict(deque)
        # 隱形大戶守價位偵測(2026-09-25 jack 交辦,見 _iceberg_update docstring):
        # sid -> {"bid":{price_key:state}, "ask":{price_key:state}, "last_breakout":{...}|None}
        self.iceberg = defaultdict(lambda: {"bid": {}, "ask": {}, "last_breakout": None})


ST = S()
