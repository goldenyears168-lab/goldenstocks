#!/usr/bin/env python3
"""biglot dashboard Phase 1→2 之間的安全網：動態證明「18 個每日整包重新賦值的
全域」真的會在過日時換成新物件，並且具體示範一個天真的 `from biglot_dashboard
import X` 搬移手法會在下一次過日時產生 stale reference。

背景：2026-09-27 對整份檔案做 AST 掃描，找出哪些全域是被
`global X; X = ...`（含 tuple unpacking）整包重新賦值、不只是原地修改
（見 docs/biglot-refactor-roadmap.md「Phase 1 到此為止」段落）。這支工具把
那個靜態分析結果拿到動態執行上驗證：連續模擬兩個交易日的 ingest()，用
`id()` 檢查這些全域是不是真的換了物件——如果換了（預期行為），代表任何
「搬移時用簡單 import 抓一份參照」的手法都會在第二天壞掉；如果沒換，
代表這個名字其實不在真正會重載的清單裡，可以放心用今天 Phase 1 那套
簡單 import 手法搬移。

用法：
    PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/check_daily_rebind.py \\
        --fixture ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-17

不需要第二天的真實資料——我們要驗證的是「重新賦值機制本身」，不是「內容算得對不對」，
第二天的 raw tick 檔案不存在也沒關係（ingest() 找不到檔案會跳過那一段，但過日
重載的全域重新賦值邏輯發生在檔案讀取「之前」，一樣會觸發）。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture_lib import load_dashboard_module, make_frozen_datetime  # noqa: E402

TZ = timezone(timedelta(hours=8))

# 2026-09-27 AST 掃描找出的「被 global X; X = ... 整包重新賦值」全域，
# 見 docs/biglot-refactor-roadmap.md。這份清單是本工具的輸入，不是本工具自己推算的
# ——兩邊要保持同步，改動時兩處一起改。
DANGEROUS_REBIND_GLOBALS = (
    "ATR_STATE", "DAILY_TREND", "ETF981_ASOF", "ETF981_HOLD", "ETF981_PREV_ASOF",
    "FUT_PX", "HIST_BIG", "KEY_LINE", "PE_EPS", "PE_GEN", "PE_PEERS", "PE_TABLE",
    "PREOPEN", "UNI5", "VIXTWN", "VOLRISK", "VOLRISK_DATE", "WRT", "XQ_STYLE",
    "Y_PMLOW",
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--date", default=None, help="不給就用 fixture 目錄名")
    ap.add_argument("--hhmm", default="13:35")
    args = ap.parse_args()

    fixture_dir = Path(args.fixture).resolve()
    day1 = args.date or fixture_dir.name
    hh, mm = (int(x) for x in args.hhmm.split(":"))
    day1_dt = datetime.strptime(day1, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=TZ)
    day2_dt = day1_dt + timedelta(days=1)  # 不需要是真的交易日，見檔頭說明

    bd = load_dashboard_module(fixture_dir, frozen_now=day1_dt)

    def is_identity_unsafe(v) -> bool:
        # None/bool/小整數/空 tuple 在 CPython 是單例或被 intern，id() 相同不代表
        # 「沒有重新賦值」，只代表「兩次算出來的值剛好都是這個單例」——這裡改用
        # 「內容也相同」當額外判準，而不是直接判定成沒換（2026-09-27 UNI5 在只有
        # 一份 fixture、快照不足 2 份時就踩到這個：兩次都算出 None，id() 一樣，
        # 但不是真的沒有重新呼叫 _load_hist()）。
        return v is None or isinstance(v, (bool, int)) or v == ()

    print(f"第一天 {day1_dt}：呼叫 ingest()…")
    bd.ingest()
    before = {name: getattr(bd, name, None) for name in DANGEROUS_REBIND_GLOBALS}
    # 模擬「Phase 2 某支新模組在這個時間點做了 from biglot_dashboard import X」
    stale_refs = dict(before)

    print(f"第二天 {day2_dt}：換日、再呼叫一次 ingest()…")
    bd.datetime = make_frozen_datetime(day2_dt)
    bd.ingest()
    after = {name: getattr(bd, name, None) for name in DANGEROUS_REBIND_GLOBALS}

    print(f"\n{'全域':<18}{'判定':<14}{'天真 import 搬移會不會壞掉':<18}")
    n_rebound = n_na = 0
    for name in DANGEROUS_REBIND_GLOBALS:
        b, a = before[name], after[name]
        if is_identity_unsafe(b) and is_identity_unsafe(a):
            verdict, would_break, n_na = "N/A(單例值,id()判不出)", "無法判斷", n_na + 1
        elif id(b) != id(a):
            verdict, would_break, n_rebound = "是", "會 stale", n_rebound + 1
        else:
            verdict, would_break = "否 (意外!)", "不會"
        print(f"{name:<18}{verdict:<14}{would_break:<18}")

    checked = len(DANGEROUS_REBIND_GLOBALS) - n_na
    print(f"\n{n_rebound}/{checked} 個可判斷的全域證實會在過日時整包換物件"
          f"（另有 {n_na} 個因為兩次都算出單例值，id() 判不出來，非異常）。")
    if n_rebound < checked:
        print("有全域沒有換——去查 docs/biglot-refactor-roadmap.md 的清單是不是要更新，"
              "或這次 fixture 沒有觸發到某個 reload 路徑。")
        return 1
    print("結論：這 18 個全域在 state.py 化之前，任何要移動的函式若讀到它們，"
          "禁止用 `from biglot_dashboard import X` 這種簡單 import 手法搬移。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
