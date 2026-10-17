#!/usr/bin/env python3
"""momentum-rotation long-lived Order worker.

Production path: launchd ``com.jackm4.goldenstocks.momentum-rotation-poll`` →
this script (KeepAlive). One-shot debug::

  .venv/bin/python scripts/order/run_momentum_rotation_worker.py --once
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from project_dotenv import load_project_dotenv  # noqa: E402

load_project_dotenv()

from order.momentum_rotation_worker_loop import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
