#!/usr/bin/env python3
"""研究用:以「5分∧30分∧全日 同時成立」的組合為單位,重放寬門檻並回測(不改凍結規則/生產 watcher)。

故事(預註冊):散戶在三個尺度同時淨賣、大戶同時淨買=散戶持續把貨倒給大戶,價格下跌是散戶賣壓造成。
借 clone_agent_wide5m_backtest 的重放/排隊/彙整流程,只換變體定義與輸出目錄。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_wide_combo_backtest.py [--skip-queue]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import clone_agent_wide5m_backtest as m  # noqa: E402

OUT = m.S / "w5c"
OUT.mkdir(exist_ok=True)
link = OUT / "clone_agent_scan"
if not link.exists():
    link.symlink_to(m.S / "clone_agent_scan")
m.S = OUT
m.CANDS = OUT / "cands"

_DAY = re.compile(r"全日 大戶([+-]?\d+)萬／散戶([+-]?\d+)萬")


def nets(c):
    mm = _DAY.search(" ".join(c["hits"]))
    bd, rd = int(mm.group(1)), int(mm.group(2))
    return {"5": (c["f5"]["big5_net"], c["f5"]["ret5_net"]),
            "30": (c["flow"]["big30"][0], c["flow"]["ret30"][0]),
            "d": (bd, rd)}


def sell_buy(b, r, strict=False):  # 大戶淨買、散戶淨賣
    return (b > 0 if strict else b >= 0) and r < 0


def new_ok(c, tag):
    n = nets(c)
    sc = ("5", "30", "d")
    if tag == "O":
        return m_old(c)
    if tag == "T":
        return all(sell_buy(*n[s]) for s in sc)
    if tag == "P":
        return all(sell_buy(*n[s], strict=True) for s in sc)
    if tag == "R":
        return all(n[s][1] < 0 for s in sc)
    if tag == "B":
        return all(n[s][0] >= 0 for s in sc)
    if tag == "M":
        return all(n[s][0] < 0 and n[s][1] >= 0 for s in sc)
    if tag == "L":
        return all(sell_buy(*n[s]) for s in ("30", "d"))
    if tag == "S":
        return all(sell_buy(*n[s]) for s in ("5", "30"))
    raise KeyError(tag)


m_old = m.old_ok
m.old_ok = lambda c: True
m.new_ok = new_ok
m.VARIANTS = {
    "V0 現行寬門檻": ("O",),
    "T1 現行+三尺度大買散賣": ("O", "T"),
    "T2 現行+三尺度大戶>0且散賣": ("O", "P"),
    "T3 現行+三尺度散戶賣(大戶不限)": ("O", "R"),
    "T4 現行+三尺度大戶買(散戶不限)": ("O", "B"),
    "T5 現行+30分∧全日大買散賣": ("O", "L"),
    "T6 現行+5分∧30分大買散賣": ("O", "S"),
    "T7 現行+三尺度鏡像(對照:大賣散買)": ("O", "M"),
    "U1 取代排名濾網:三尺度大買散賣": ("T",),
    "U3 取代排名濾網:三尺度散戶賣": ("R",),
    "U7 取代排名濾網:三尺度鏡像(對照)": ("M",),
}
raise SystemExit(m.main())
