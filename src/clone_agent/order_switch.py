"""複製人代理人 —— 實單開關／帳本／心跳的檔案契約(純讀寫 JSON,不碰任何券商 API)。

下單層(src/order/clone_agent_*)與儀表板之間唯一的介面就是這三個檔,雙方互不 import:
  · switch    `data/order/clone_agent_switch.json`        儀表板寫、下單層讀。預設開;檔案不存在=開,檔案壞掉=關(fail-closed)。
  · ledger    `data/order/clone_agent_ledger_{day}.json`  下單層寫、儀表板讀。
  · heartbeat `data/order/clone_agent_heartbeat.json`     下單層寫、儀表板讀,用來判斷下單層是否活著。
另有 cancel_requests_{day}.jsonl:AI 監督員(只能 append)寫撤單請求、下單層讀並驗證後才撤(AI 本身沒有券商 API)。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from clone_agent.approval import _data_dir


def _dir() -> Path:
    d = _data_dir().parent / "order"
    d.mkdir(parents=True, exist_ok=True)
    return d


def switch_path() -> Path:
    return _dir() / "clone_agent_switch.json"


def ledger_path(day: str) -> Path:
    return _dir() / f"clone_agent_ledger_{day}.json"


def heartbeat_path() -> Path:
    return _dir() / "clone_agent_heartbeat.json"


def cancel_requests_path(day: str) -> Path:
    return _dir() / f"clone_agent_cancel_requests_{day}.jsonl"


def append_cancel_request(day: str, req: dict[str, Any]) -> None:
    with open(cancel_requests_path(day), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(req, ensure_ascii=False) + "\n")


def read_cancel_requests(day: str) -> list[dict[str, Any]]:
    p = cancel_requests_path(day)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("request_id"):
            out.append(r)
    return out


def atomic_write(path: Path, obj: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        finally:
            raise


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_switch() -> dict[str, Any]:
    """{'enabled': bool, 'updated_at': str|None, 'by': str|None, 'state': 'default'|'set'|'corrupt'}"""
    p = switch_path()
    if not p.exists():
        return {"enabled": True, "updated_at": None, "by": None, "state": "default"}
    try:
        j = _load(p)
        if not isinstance(j, dict) or not isinstance(j.get("enabled"), bool):
            raise ValueError("bad shape")
        return {"enabled": j["enabled"], "updated_at": j.get("updated_at"), "by": j.get("by"), "state": "set"}
    except (OSError, ValueError):
        return {"enabled": False, "updated_at": None, "by": None, "state": "corrupt"}


def set_switch(enabled: bool, by: str = "dashboard") -> dict[str, Any]:
    atomic_write(switch_path(), {"enabled": bool(enabled), "updated_at": datetime.now().isoformat(timespec="seconds"), "by": by})
    return read_switch()


class LedgerUnreadable(RuntimeError):
    """帳本檔案存在但讀不出來(IO 錯誤或壞掉的 JSON)——2026-10-06 修正:這跟『今天還
    沒有帳本』(檔案不存在,合法的新的一天)是完全不同的情況,之前兩者都回傳 `{}`,
    呼叫端(`clone_agent_order.py::load_ledger`)分不出來,會把讀取失敗誤判成新的一天,
    連帶把熔斷旗標/當日已送單數/同標的防重複這些狀態全部重置掉,在壞資料上繼續送單。
    現在檔案存在但讀不出來時丟出這個例外,呼叫端要 fail-closed(直接熔斷等人工介入),
    不能假裝什麼事都沒發生。"""


def read_ledger(day: str) -> dict[str, Any]:
    p = ledger_path(day)
    if not p.exists():
        return {}
    try:
        j = _load(p)
    except (OSError, ValueError) as exc:
        raise LedgerUnreadable(f"{p}: {type(exc).__name__}: {exc}") from exc
    return j if isinstance(j, dict) else {}


def read_ledger_display(day: str) -> dict[str, Any]:
    """給唯讀顯示端(儀表板面板/CLI狀態/AI監督員)用:讀不出來就當空帳本,不丟例外。
    下單層(`clone_agent_order.py::load_ledger`)不走這支——它要分得出『讀不出來』跟
    『今天還沒有帳本』,fail-closed 熔斷,不能在這裡被吞掉。"""
    try:
        return read_ledger(day)
    except LedgerUnreadable:
        return {}


def read_heartbeat() -> dict[str, Any]:
    p = heartbeat_path()
    if not p.exists():
        return {}
    try:
        j = _load(p)
        return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}
