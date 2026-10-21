"""分點「隔日沖」慣性 —— 逐股看紀錄（權證小哥 V14／V10、knowledge-book 2.10、R21b）。純函式，不連 DB、不讀檔。

小哥原則：隔日沖是「每檔個股各自看紀錄」，不是整個分點一概而論（「同分點在不同股不通用」）。
因此慣性只以（股票, 分點）為單位：該分點在這檔股票的前 N 名買超、且淨買 ≥ 該股當日量的 minsh 時算一次「大買事件」，
隔日在這檔股票賣出量 ≥ alpha × 淨買 算一次「隔日沖」。參數沿用預先註冊檢定
（docs/warrant-xiaoge/flip-habit-study-protocol.md：前 5 名、3%、α=0.7）。PIT：只用結果在 day 之前已知的事件（d1 ≤ day）。

呼叫端各自把資料整理成同一結構（單位須一致，函式不關心是股或張）：
  D[(sid, 日期)] = {分點代號: (買進量, 賣出量)}      vol[(sid, 日期)] = 該股當日成交量
  days = 升冪交易日清單（已排除不完整的日子由呼叫端決定）
共用者：scripts/ops/xiaoge_inventory.py（M07／M09）、scripts/research/chip_sop.py（/chips）。
"""
from __future__ import annotations

from typing import Any

MINSH = 0.03       # 大買事件：淨買 ≥ 該股當日量 3%
ALPHA = 0.7        # 隔日沖：隔日賣出 ≥ 0.7 × 淨買
TOPN = 5           # 大買事件：該股當日淨買前 5 名


def top_buyers(day_rows: dict[str, tuple[float, float]], vol: float | None, minsh: float = MINSH, topn: int = TOPN) -> list[tuple[str, float]]:
    """當日該股淨買前 topn 名且淨買 ≥ minsh×量的分點，回 [(分點, 淨買)]（大到小）。"""
    if not day_rows or not vol:
        return []
    net = {b: x[0] - x[1] for b, x in day_rows.items()}
    out = [(b, net[b]) for b in sorted(net, key=lambda k: -net[k])[:topn] if net[b] > 0 and net[b] >= minsh * vol]
    return out


def habit_of(D: dict, vol: dict, days: list[str], sid: str, branch: str, day: str,
             minsh: float = MINSH, alpha: float = ALPHA, topn: int = TOPN,
             skip_unknown_next: bool = False) -> tuple[int, int, list[tuple[str, str, float, float]]]:
    """回 (大買事件數, 其中隔日沖次數, 例子[(買日, 賣日, 淨買, 隔日賣出)])；只算 d1 ≤ day 的事件。
    skip_unknown_next=True：隔日該股完全沒有分點資料（不是只缺這個分點）時，這個事件結果未知，不計入事件數
    （/chips 用；/inventory 維持原行為 False）。"""
    ev = fl = 0
    ex: list[tuple[str, str, float, float]] = []
    for i in range(len(days) - 1):
        d0, d1 = days[i], days[i + 1]
        if d1 > day:
            break
        rows = D.get((sid, d0))
        v = vol.get((sid, d0))
        hit = dict(top_buyers(rows or {}, v, minsh, topn))
        if branch not in hit:
            continue
        if skip_unknown_next and (sid, d1) not in D:
            continue
        ev += 1
        nxt = D.get((sid, d1), {}).get(branch)
        if nxt and nxt[1] >= alpha * hit[branch]:
            fl += 1
            ex.append((d0, d1, hit[branch], nxt[1]))
    return ev, fl, ex


def habit_summary(D: dict, vol: dict, days: list[str], sid: str, day: str, buyers: list[tuple[str, float]],
                  names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """對今日（股票 sid）的大買分點逐一查該股的隔日沖紀錄。回 [{branch, name, net, events, flips, strong}]。"""
    out = []
    for b, net in buyers:
        ev, fl, _ = habit_of(D, vol, days, sid, b, day, skip_unknown_next=True)
        out.append({"branch": b, "name": (names or {}).get(b, b), "net": net, "events": ev, "flips": fl, "strong": fl >= 2})
    return out
