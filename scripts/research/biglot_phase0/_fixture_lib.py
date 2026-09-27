"""biglot_phase0 共用 helper：載入 fixture 版的 biglot_dashboard 模組 + 凍結時鐘。

背景：biglot_dashboard.py 有 27 處直接呼叫 `datetime.now(TZ)`，沒有集中包一層。
要在不改動該檔案的前提下讓重放可重現，做法是把 `biglot_dashboard.datetime`（模組
自己 `from datetime import datetime` 進去的那個名字）整個換成一個凍結子類別——
Python 的全域查找是呼叫當下才解析，所以模組內所有 `datetime.now(TZ)` 呼叫點
事後都會改用凍結值，完全不用碰原始檔案的 111 個函式。

DATA_DIR/DEFAULT_DB_PATH 則是 src/stock_db/util.py 在 import 當下讀
GOLDENSTOCKS_DATA_DIR 環境變數算出來的模組級常數——所以「指向 fixture」必須在
import biglot_dashboard 之前就把環境變數設好，且整支腳本只能 import 一次
（不要在同一個 process 裡對不同 fixture 重複呼叫 load_dashboard_module，
DATA_DIR 不會跟著環境變數重算）。
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime as _real_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = REPO_ROOT / "src"
RESEARCH_DIR = REPO_ROOT / "scripts" / "research"


def materialize_working_copy(canonical_fixture_dir: Path) -> Path:
    """把 fixture 硬連結成一份用完即丟的工作複本，canonical fixture 永遠保持乾淨。

    背景（2026-09-27 稽核發現的真事故）：biglot_dashboard.py 不只讀 DB，還會把
    holds.json/stock_notes.json/hold_events_{date}.jsonl 這類持久化狀態寫回
    DATA_DIR.parent/cache/biglot_live_watch/——GOLDENSTOCKS_DATA_DIR 指到哪裡，
    這些檔就寫到哪裡。第一次跑 smoke_test.py 對 fixture 按了 /hold /stocknote，
    寫進的狀態被下一次 run_golden_diff.py 讀回去，跑出一個看似真實、實則是
    「fixture 被上一次呼叫污染」的假 diff，跟被測的程式碼改動完全無關。

    用 os.link 硬連結而不是整份複製：stocks.db 只有 100MB+ 但 dashboard 對它
    全程唯讀，硬連結零額外硬碟成本；小的狀態檔就算被寫也是寫在 disposable
    複本上，不會反寫回 canonical fixture 的 inode（open("w") 一定先斷開硬連結
    產生新 inode，不會動到來源檔案）。
    """
    canonical_fixture_dir = Path(canonical_fixture_dir).resolve()
    work_dir = Path(tempfile.mkdtemp(prefix=f"biglot_fixture_{canonical_fixture_dir.name}_"))
    work_dir.rmdir()  # copytree 要求目的地不存在
    shutil.copytree(canonical_fixture_dir, work_dir, copy_function=os.link)
    # 工作複本用完即丟：程式正常結束或中途炸掉都要清，不然每跑一次就在系統
    # temp 目錄留一份 200MB+ 垃圾（2026-09-27 第一版忘了這段，手動清過兩份）。
    atexit.register(shutil.rmtree, work_dir, ignore_errors=True)
    return work_dir


def make_frozen_datetime(frozen_now: _real_datetime):
    """回傳一個 `datetime` 子類別，`.now(tz=...)` 一律回傳 frozen_now（忽略傳入的 tz，
    因為 biglot_dashboard 全部呼叫都固定傳自己的 TZ 常數）。其餘方法（strftime/strptime/
    運算）原封不動繼承真正的 datetime，不影響既有邏輯。"""

    class _FrozenDateTime(_real_datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 - 簽名要跟 datetime.now 對齊
            return frozen_now

    return _FrozenDateTime


def load_dashboard_module(fixture_dir: Path, frozen_now: _real_datetime | None = None,
                           use_working_copy: bool = True):
    """在指定 fixture 目錄下 import biglot_dashboard，回傳該模組物件。

    預設 use_working_copy=True：先把 fixture 硬連結成一份 disposable 工作複本
    再指過去，確保 canonical fixture 永遠不會被跑過程中寫回的狀態檔污染
    （見 materialize_working_copy docstring）。只有在你明確要檢查「跑完之後
    fixture 目錄裡多了什麼檔案」這種情境才需要 use_working_copy=False。

    呼叫前必須確認目前 process 還沒 import 過 stock_db／biglot_dashboard
    （即：這是一支剛啟動的獨立腳本，不是塞進某個已經跑過 prod 路徑的 REPL）。
    """
    fixture_dir = Path(fixture_dir).resolve()
    if not (fixture_dir / "data" / "stocks.db").exists():
        raise FileNotFoundError(
            f"{fixture_dir} 底下沒有 data/stocks.db，先跑 build_fixture.py"
        )
    if use_working_copy:
        fixture_dir = materialize_working_copy(fixture_dir)
    os.environ["GOLDENSTOCKS_DATA_DIR"] = str(fixture_dir)

    for p in (str(RESEARCH_DIR), str(SRC_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)

    for mod in ("biglot_dashboard", "stock_db", "stock_db.util", "source_dedup",
                "compute_xq_style_metrics"):
        sys.modules.pop(mod, None)

    import biglot_dashboard as bd  # noqa: PLC0415

    if frozen_now is not None:
        bd.datetime = make_frozen_datetime(frozen_now)

    return bd
