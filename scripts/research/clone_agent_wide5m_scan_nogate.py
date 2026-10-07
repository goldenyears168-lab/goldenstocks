#!/usr/bin/env python3
"""研究用:同 clone_agent_wide5m_scan,但拿掉寬門檻的『距近30分高≤−0.5%』與『30分成交額≥30百分位』,
讓事後能檢定這兩條在三尺度組合下是否還需要。用法同原腳本,輸出請指定 --out-dir。"""
import runpy
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import trader_signal_watch as w  # noqa: E402

w.PULLBACK_MAX = 99.0
w.AMT_PCT_MIN = -1
runpy.run_path("scripts/research/clone_agent_wide5m_scan.py", run_name="__main__")
