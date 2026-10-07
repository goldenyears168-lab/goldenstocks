#!/usr/bin/env python3
"""研究用:逐分鐘全記訊號卡(不套排名濾網、不套冷卻),並附上 5 分窗流量特徵 f5,供事後重放「寬門檻加 5 分條件」。

不改 trader_signal_watch(生產 watcher);用 monkeypatch 抓 Tape 實例與包 build_card。
用法: PYTHONPATH=src .venv/bin/python scripts/research/clone_agent_wide5m_scan.py 2026-09-29 [--out-dir DIR]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import trader_signal_watch as w  # noqa: E402

day = sys.argv[1]
out_dir = (Path(sys.argv[sys.argv.index("--out-dir") + 1]) if "--out-dir" in sys.argv
           else Path.home() / "goldenstocks-data/data/scratch/clone_agent_w5_allmin")
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / f"candidates_{day}.jsonl").unlink(missing_ok=True)
w.STATE_DIR = out_dir

TAPE: list = []
_POOLS: dict = {}


class _Tape(w.Tape):
    def __init__(self, d):
        super().__init__(d)
        TAPE.clear()
        TAPE.append(self)


def _pools5(now: float):
    if now in _POOLS:
        return _POOLS[now]
    t = TAPE[0]
    fl = {s: t.flow(s, now, 300) for s in t.ev}
    share = lambda x, a: (x / a) if a > 0 else None  # noqa: E731
    big = {s: share(v[1], v[0]) for s, v in fl.items()}
    ret = {s: share(v[2], v[0]) for s, v in fl.items()}
    diff = {s: (None if big[s] is None or ret[s] is None else big[s] - ret[s]) for s in fl}
    _POOLS.clear()
    _POOLS[now] = (fl, big, ret, diff)
    return _POOLS[now]


_orig_build = w.build_card


def _build(sid, name, now, feat, root, d):
    c = _orig_build(sid, name, now, feat, root, d)
    fl, big, ret, diff = _pools5(now)
    a5, b5, r5, _ = fl.get(sid, (0.0, 0.0, 0.0, 0.0))
    c["f5"] = {
        "amt5": a5, "big5_net": b5, "ret5_net": r5,
        "big5_share": big.get(sid), "ret5_share": ret.get(sid), "diff5": diff.get(sid),
        "big5_rank": w.frac_rank(big, sid),
        "ret5_rank": w.frac_rank(ret, sid, larger=False),
        "diff5_rank": w.frac_rank(diff, sid),
        "pool5_n": sum(1 for v in big.values() if v is not None),
    }
    c["strict"] = feat.get("strict")
    c["flow_ranks"] = {k: v[1] for k, v in c.get("flow", {}).items()} if isinstance(c.get("flow"), dict) else None
    c["dist_hi"] = feat.get("dist_hi")
    c["chg_cm"] = feat.get("chg_cm")
    c["amt_pct"] = feat.get("amt_pct")
    return c


w.Tape = _Tape
w.build_card = _build
sys.argv = ["trader_signal_watch", "--day", day, "--scan", "--no-rules", "--no-rank-filter", "--recheck-min", "0"]
raise SystemExit(w.main())
