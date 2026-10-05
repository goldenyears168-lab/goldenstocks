#!/usr/bin/env python3
"""用 trader_signal_watch 的歷史全掃(--scan)重算某天代理人的訊號,寫到暫存目錄,不碰 clone_agent 的正式候選檔。

用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_scan_history.py 2026-09-29 [--out-dir DIR] [--hi-win 分鐘] [--recheck-min 分鐘] [--no-rank-filter] [--signal-start HH:MM(研究用:覆蓋 09:30)] [--flow-trace] [--end HH:MM]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import trader_signal_watch as w  # noqa: E402

day = sys.argv[1]
out_dir = Path(sys.argv[sys.argv.index("--out-dir") + 1]) if "--out-dir" in sys.argv else Path.home() / "goldenstocks-data/data/scratch/clone_agent_scan"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / f"candidates_{day}.jsonl").unlink(missing_ok=True)
w.STATE_DIR = out_dir
extra = []
for flag in ("--hi-win", "--recheck-min"):
    if flag in sys.argv:
        extra += [flag, sys.argv[sys.argv.index(flag) + 1]]
if "--no-rank-filter" in sys.argv:
    extra.append("--no-rank-filter")
if "--flow-trace" in sys.argv:     # 研究用:②⑤與避開12-13點全放寬,逐分鐘記錄每檔的流量排名(供撤單條件重放)
    w.PULLBACK_MAX, w.AMT_PCT_MIN, w.SKIP_HOURS = 1e9, -1, ()
if "--end" in sys.argv:
    extra += ["--end", sys.argv[sys.argv.index("--end") + 1]]
if "--signal-start" in sys.argv:
    w.SIGNAL_START = sys.argv[sys.argv.index("--signal-start") + 1]
sys.argv = ["trader_signal_watch", "--day", day, "--scan", "--no-rules", *extra]
raise SystemExit(w.main())
