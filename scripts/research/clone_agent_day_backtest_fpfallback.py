#!/usr/bin/env python3
"""研究用:包 clone_agent_day_backtest,缺 futprice_{day}.json 時借用 2026-09-22 的 sid→期貨代碼對照(只用 sym,商品代碼跨日穩定)。不寫入任何快取。"""
import pathlib
import sys

_orig = pathlib.Path.read_text
_REF = pathlib.Path.home() / "goldenstocks-data/cache/biglot_live_watch/futprice_2026-09-22.json"


def _rt(self, *a, **k):
    if self.name.startswith("futprice_") and not self.exists():
        return _orig(_REF, *a, **k)
    return _orig(self, *a, **k)


pathlib.Path.read_text = _rt
sys.path.insert(0, "scripts/research")
SRC = "scripts/research/clone_agent_day_backtest.py"
sys.argv[0] = SRC
code = open(SRC, encoding="utf-8").read()
code = code.replace("exit_last = tr[k][1]", "k = min(k, len(tr) - 1); j = min(j, k); exit_last = tr[k][1]")  # 成交落在逐筆檔尾端時不越界
exec(compile(code, SRC, "exec"), {"__name__": "__main__", "__file__": SRC})
