#!/usr/bin/env python3
"""複製人代理人實單 worker(預設 dry-run;實彈需 .env ORDER_CLONE_AGENT_DRY_RUN=0)。一次性除錯: --once"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from project_dotenv import load_project_dotenv  # noqa: E402

load_project_dotenv()

from order.clone_agent_worker_loop import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
