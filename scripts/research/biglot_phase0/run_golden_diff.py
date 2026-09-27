#!/usr/bin/env python3
"""biglot dashboard Phase 0：對凍結 fixture 跑一次 ingest()+全部 render_*()，
把輸出存成一份快照；有 --baseline 就跟前一份快照逐檔比對。

這是 Phase 1 起每一次「把函式搬到新模組」都要跑的安全網：搬移應該是零 diff，
一旦跑出來有差異，代表搬移途中動到了行為，不是單純换位置。

用法：
    # 第一次：建 baseline
    PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/run_golden_diff.py \\
        --fixture ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-25 \\
        --out ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-25/golden_baseline

    # 改完程式碼後：重跑並比對
    PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/run_golden_diff.py \\
        --fixture ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-25 \\
        --out ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-25/golden_after \\
        --baseline ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-25/golden_baseline

注意：這支腳本會 import biglot_dashboard 並把它的 GOLDENSTOCKS_DATA_DIR 指到 fixture，
一個 process 只能跑一個 fixture——不要在同一個互動 session 裡對兩個不同 fixture
連續呼叫，DATA_DIR 是 import 當下就算死的模組級常數。
"""
from __future__ import annotations

import argparse
import difflib
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture_lib import load_dashboard_module, load_entry_points  # noqa: E402

TZ = timezone(timedelta(hours=8))  # 對齊 biglot_dashboard.py 自己的 TZ 常數


def _safe(label: str, fn) -> str:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - 錯誤本身要進快照才看得出regression，不能吞掉
        return f"ERROR in {label}:\n{traceback.format_exc()}"


def snapshot(bd, ep: dict, date: str, out_dir: Path, stock_ids: list[str]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stock").mkdir(exist_ok=True)
    (out_dir / "stockfrag").mkdir(exist_ok=True)
    files: dict[str, Path] = {}

    def write(name: str, content: str):
        p = out_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        files[name] = p

    write("frag.html", _safe("render()/PAGE[frag]", lambda: bd.PAGE.get("frag", "")))
    for sort in ("ind", "big", "chg"):
        write(f"grid_{sort}.html", _safe(f"render_grid_frag({sort!r})",
                                          lambda s=sort: ep["render_grid_frag"](s)))
    write("history.html", _safe("render_history()", ep["render_history"]))
    write("help.html", _safe("render_help()", ep["render_help"]))
    write(f"day_{date}.html", _safe(f"render_day({date!r})", lambda: ep["render_day"](date)))

    for sid in stock_ids:
        write(f"stock/{sid}.html", _safe(f"render_stock({sid!r})",
                                          lambda s=sid: ep["render_stock"](s, date)))
        write(f"stockfrag/{sid}.html", _safe(f"render_stock_frag({sid!r})",
                                              lambda s=sid: ep["render_stock_frag"](s, date)))
    return files


def diff_against_baseline(current_dir: Path, baseline_dir: Path) -> int:
    cur_files = sorted(p.relative_to(current_dir) for p in current_dir.rglob("*") if p.is_file())
    n_diff = 0
    for rel in cur_files:
        base_f = baseline_dir / rel
        cur_f = current_dir / rel
        if not base_f.exists():
            print(f"[NEW] {rel}（baseline 沒有這個檔案）")
            n_diff += 1
            continue
        a = base_f.read_text(encoding="utf-8").splitlines(keepends=True)
        b = cur_f.read_text(encoding="utf-8").splitlines(keepends=True)
        if a != b:
            n_diff += 1
            print(f"[DIFF] {rel}")
            for line in list(difflib.unified_diff(a, b, "baseline", "current"))[:20]:
                print(f"    {line.rstrip()}")
    base_files = {p.relative_to(baseline_dir) for p in baseline_dir.rglob("*") if p.is_file()}
    for rel in sorted(base_files - set(cur_files)):
        print(f"[MISSING] {rel}（baseline 有、這次沒產出——是不是漏搬一個 render 函式？）")
        n_diff += 1
    return n_diff


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", required=True, help="build_fixture.py 產生的 fixture 根目錄")
    ap.add_argument("--out", required=True, help="這次快照要寫到哪個目錄")
    ap.add_argument("--baseline", default=None, help="要比對的前一份快照目錄（不給就只建快照不比對）")
    ap.add_argument("--date", default=None, help="不給就用 --fixture 目錄名（預期是 YYYY-MM-DD）")
    ap.add_argument("--hhmm", default="13:35", help="凍結時鐘的時分，預設收盤後 13:35")
    ap.add_argument("--stocks", default=None, help="逗號分隔股號子集，不給就跑全部觀察宇宙（較慢）")
    args = ap.parse_args()

    fixture_dir = Path(args.fixture).resolve()
    date = args.date or fixture_dir.name
    hh, mm = (int(x) for x in args.hhmm.split(":"))
    frozen_now = datetime.strptime(date, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=TZ)

    bd = load_dashboard_module(fixture_dir, frozen_now=frozen_now)
    ep = load_entry_points(bd)
    stock_ids = args.stocks.split(",") if args.stocks else list(bd.NAMES.keys())

    print(f"凍結時鐘：{frozen_now}；跑 {len(stock_ids)} 檔；ingest()+render() 中…")
    ep["ingest"]()
    ep["render"]()

    out_dir = Path(args.out).resolve()
    snapshot(bd, ep, date, out_dir, stock_ids)
    print(f"快照已寫入：{out_dir}")

    if args.baseline:
        baseline_dir = Path(args.baseline).resolve()
        n_diff = diff_against_baseline(out_dir, baseline_dir)
        if n_diff:
            print(f"\n{n_diff} 個檔案有差異——搬移不是零diff，先查是不是動到行為")
            return 1
        print("\n零 diff，搬移安全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
