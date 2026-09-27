#!/usr/bin/env python3
"""biglot dashboard Phase 0：驗證「正式站台實際的啟動方式」不會炸。

背景（2026-09-27 真的發生過一次事故）：`_fixture_lib.load_dashboard_module()`
一律用 `import biglot_dashboard as bd` 載入，golden-diff/smoke-test 全部通過；
但正式站台實際指令是 `python3 biglot_dashboard.py` 直接執行——這時檔案在
`sys.modules` 裡叫 `"__main__"` 不叫 `"biglot_dashboard"`，如果任何 `biglot/*.py`
子模組 `import biglot_dashboard`，Python 找不到既有物件會把整支檔案當成
「另一個模組」重新執行一次，在自己還沒執行完的 import 上撞出循環 import
ImportError——這個崩潰模式 golden-diff/smoke-test 兩支工具都測不到，只有真的
用 `python3 biglot_dashboard.py` 這個確切指令啟動才會暴露。已修好（檔案開頭
`sys.modules.setdefault("biglot_dashboard", sys.modules[__name__])`），這支工具
是往後每次改動後都要重跑一次的回歸測試,不能只信 golden-diff/smoke-test 過關。

用法：
    PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/check_prod_launch.py \\
        --fixture ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-17

只檢查「有沒有在印出啟動 banner 之前就死掉」，不檢查 port 有沒有真的綁定成功
（如果正式站台剛好也在跑，這裡會因為 port 衝突印出 OSError，那是預期中的
「兩個 process 搶同一個 port」，不是這支工具要抓的 bug；抓的是 import 階段崩潰）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--timeout", type=float, default=20.0,
                     help="等啟動 banner 或崩潰的秒數上限")
    args = ap.parse_args()

    fixture_dir = str(Path(args.fixture).resolve())
    env = {"GOLDENSTOCKS_DATA_DIR": fixture_dir, "PYTHONPATH": "src",
           "PATH": "/usr/bin:/bin"}

    proc = subprocess.Popen(
        [str(REPO_ROOT / ".venv" / "bin" / "python"), "scripts/research/biglot_dashboard.py"],
        cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.time() + args.timeout
    output_lines: list[str] = []
    saw_banner = False
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            line = proc.stdout.readline()
            if line:
                output_lines.append(line)
                if line.startswith("biglot dashboard on :"):
                    saw_banner = True
                    break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if saw_banner:
        print("PASS：`python3 biglot_dashboard.py` 直接執行成功印出啟動 banner，"
              "沒有在 import 階段崩潰。")
        return 0

    exit_code = proc.poll()
    print(f"FAIL：直接執行 `python3 biglot_dashboard.py` 沒看到啟動 banner "
          f"(process exit code={exit_code})，輸出如下：\n")
    print("".join(output_lines) or (proc.stdout.read() if proc.stdout else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
