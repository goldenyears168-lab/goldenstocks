#!/usr/bin/env python3
"""用 trader_signal_watch 的歷史全掃(--scan)重算某天代理人的訊號,寫到暫存目錄,不碰 clone_agent 的正式候選檔。

用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_scan_history.py 2026-09-29 [--out-dir DIR]
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
sys.argv = ["trader_signal_watch", "--day", day, "--scan", "--no-rules"]
raise SystemExit(w.main())
