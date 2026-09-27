"""biglot dashboard 重構 Phase 1：從 biglot_dashboard.py 搬出的純函式。

搬移依據：scripts/research/biglot_phase0/dep_graph.py 對 biglot_dashboard.py
做 AST 依賴分析，確認以下每個函式都是「零全域變數依賴、零呼叫其他頂層函式」的
真葉節點（見 docs/biglot-refactor-roadmap.md Phase 0 段落）。純搬移，函式本體
逐字保留，不改行為。
"""
from __future__ import annotations


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
