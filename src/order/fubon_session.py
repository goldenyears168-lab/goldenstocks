"""Fubon Neo API session · login / realtime."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import account_block, load_order_config
from project_dotenv import load_project_dotenv
from stock_db import PROJECT_ROOT

_FUBON_PY_MAX = (3, 13)

# CAFubon/ lives under GOLDENSTOCKS_DATA_DIR (state root, e.g. ~/goldenstocks-data)
# when set, not under the code checkout — same pattern as stock_db.util._STATE_ROOT
# and project_dotenv._default_env_path().
_STATE_ROOT = (
    Path(os.environ["GOLDENSTOCKS_DATA_DIR"])
    if os.environ.get("GOLDENSTOCKS_DATA_DIR")
    else PROJECT_ROOT
)


def check_python_version() -> None:
    if sys.version_info[:2] > _FUBON_PY_MAX:
        major, minor = sys.version_info[:2]
        raise RuntimeError(
            f"Python {major}.{minor} 不受富邦 Neo SDK 支援（官方：3.8–3.13）。"
            " 請用 .venv-fubon/bin/python 執行 order 腳本。"
        )


def _resolve_cert(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else _STATE_ROOT / p


def _login_is_success(res: Any) -> bool:
    ok = getattr(res, "is_success", None)
    if ok is None:
        ok = getattr(res, "isSuccess", False)
    return bool(ok)


@dataclass
class FubonSession:
    sdk: Any
    accounts: list[Any]

    @property
    def primary(self) -> Any:
        if not self.accounts:
            raise RuntimeError("No Fubon accounts returned after login")
        return self.accounts[0]

    def init_realtime(self) -> None:
        self.sdk.init_realtime()


def safe_logout(session: "FubonSession | None") -> bool:
    """明確登出舊 session，釋放券商端連線額度。

    背景（2026-09-03）：三個 session pool 換手時只把舊物件丟給 GC，等券商端逾時
    才釋放額度——換手瞬間帳號同時持有兩條連線，多 worker 撞在一起就打滿
    ACCOUNT CONNECTION LIMIT（2026-09-01 曾迫使 collector 退讓 600s×18 次）。
    任何失敗都吞掉：logout 失敗最壞就是回到「等 GC」的舊行為，絕不能影響交易主流程。
    """
    if session is None:
        return False
    try:
        session.sdk.logout()
        return True
    except Exception:  # noqa: BLE001 -- never let logout break the caller
        return False


# ---------------------------------------------------------------- 連線斷路器
#
# 2026-09-28 13:14 → 09-29 10:10,富邦端連續 21 小時回 503 Service Unavailable
# （`FubonSDK()` 建構式就丟,在登入之前,所以不是我們的連線數造成的）。但我們的
# 反應把一次被動故障放大成主動轟炸:
#
#   · collect_ccf_books_websocket 的退避上限只有 120s,第 6 次失敗就 `return 1`
#     「exiting so launchd restarts clean」→ launchd 2 秒後重啟 → **計數器歸零**。
#     實測 156 秒一輪,9/29 重啟 231 次 × 6 = 1,386 次連線嘗試。
#   · biglot_live_watch 連退避都沒有,raw traceback 直接退出重啟（當天 12 次）。
#   · 全機四支收集器各自獨立重試,彼此不知道對方也在撞同一道牆。
#   · 總計約 6,700 次嘗試,而且 **9/29 開盤完全沒有資料**,逐筆錄製到 10:12:44
#     才開始——那正是交易研究裡那個「錄製缺口」。
#
# 既有的 `Maximum number of connections reached` 長退讓（600s / 容忍 30 次）方向
# 是對的,但只寫在 3 支收集器裡、而且只認那一個字串,503 完全不適用。
#
# 所以斷路器放在這裡,理由有三:
#   1. **40+ 個呼叫點自動繼承**,沒有人能繞過,包含之後的代理人下單層
#   2. **狀態存檔案 → 跨行程共用**,一支退讓了其他支也看得到
#   3. **跨重啟存活** —— 這是 9/29 最關鍵的那個洞:行程退出重啟會讓記憶體裡的
#      失敗計數歸零,所以「退出讓 launchd 重啟」反而讓退避完全失效
#
# 刻意**不**自動重試:這裡只負責「快速失敗 + 告訴你還要等多久」,重試節奏由呼叫端
# 決定。一個會自己重試的 connect() 會讓呼叫端的退避邏輯再疊一層,更難推理。
_BREAKER_PATH = _STATE_ROOT / "data" / "fubon_breaker.json"
#: 連續失敗 n 次後的冷卻秒數;超過表尾一律用最後一格。刻意讓最後一格夠長
#: （30 分鐘),因為 9/29 的故障持續 21 小時,短退避只是浪費請求。
_BREAKER_COOLDOWN = (5, 15, 60, 300, 900, 1800)
#: 這些字樣代表「連線層故障」,才記進斷路器。帳密錯、憑證過期之類的設定問題
#: 重試再多次也不會好,但那是人要修的,不該讓斷路器把它藏起來——所以只收這幾種。
_BREAKER_MARKS = (
    "503", "502", "504", "Service Unavailable", "Bad Gateway", "Gateway Time",
    "Maximum number of connections reached", "Connection refused",
    "Temporary failure in name resolution", "timed out", "timeout",
    # 2026-10-04 實測盤後的樣子：`URL error: Unable to connect to
    # wss://neoapi.fbs.com.tw/TASP/XCPXWS`。第一版漏了這個字樣,斷路器沒跳。
    "Unable to connect", "URL error", "Connection reset", "Broken pipe",
    "Network is unreachable", "No route to host",
)


class FubonBreakerOpen(RuntimeError):
    """斷路器開啟中,這次連線請求沒有真的送出去。

    呼叫端看到這個例外應該**安靜退讓**到 `retry_after` 秒之後,不要當成一般錯誤
    去累加自己的失敗計數,更不要退出行程讓 launchd 立刻重啟。
    """

    def __init__(self, retry_after: float, fails: int, last_error: str):
        self.retry_after = retry_after
        self.fails = fails
        self.last_error = last_error
        super().__init__(
            f"Fubon 連線斷路器開啟中（連續失敗 {fails} 次,還要等 {retry_after:.0f}s）"
            f"；最後錯誤: {last_error}"
        )


def _breaker_read() -> dict:
    try:
        import json
        return json.loads(_BREAKER_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- 壞檔或不存在都當成「沒問題」
        return {}


def _breaker_write(d: dict) -> None:
    try:
        import json
        _BREAKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _BREAKER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_BREAKER_PATH)          # 原子替換,避免別的行程讀到半截
    except Exception:  # noqa: BLE001 -- 斷路器寫不進去絕不能擋住交易主流程
        pass


def breaker_state() -> dict:
    """給健檢／儀表板看的唯讀狀態。"""
    import time
    d = _breaker_read()
    nxt = float(d.get("next_try", 0) or 0)
    return {"fails": int(d.get("fails", 0) or 0),
            "open": nxt > time.time(),
            "retry_after": max(0.0, nxt - time.time()),
            "last_error": str(d.get("last_error", "")),
            "first_fail_at": d.get("first_fail_at")}


def breaker_reset() -> None:
    """人工清除（例如確認富邦已恢復、不想等冷卻跑完）。"""
    _breaker_write({})


def _breaker_check() -> None:
    import time
    if os.environ.get("FUBON_BREAKER_DISABLE") == "1":
        return
    d = _breaker_read()
    nxt = float(d.get("next_try", 0) or 0)
    now = time.time()
    if nxt > now:
        raise FubonBreakerOpen(nxt - now, int(d.get("fails", 0) or 0),
                               str(d.get("last_error", "")))


def _breaker_record(exc: BaseException) -> None:
    import time
    msg = f"{type(exc).__name__}: {exc}"
    if not any(m.lower() in msg.lower() for m in _BREAKER_MARKS):
        return                               # 設定類錯誤不進斷路器,讓它照常爆出來
    d = _breaker_read()
    fails = int(d.get("fails", 0) or 0) + 1
    cd = _BREAKER_COOLDOWN[min(fails - 1, len(_BREAKER_COOLDOWN) - 1)]
    now = time.time()
    _breaker_write({"fails": fails, "next_try": now + cd, "last_error": msg[:300],
                    "first_fail_at": d.get("first_fail_at") or now,
                    "last_fail_at": now})
    print(f"[fubon-breaker] 連續失敗 {fails} 次,冷卻 {cd}s — {msg[:160]}", file=sys.stderr)


def _breaker_clear() -> None:
    if _breaker_read():
        _breaker_write({})
        print("[fubon-breaker] 連線恢復,已重置", file=sys.stderr)


def connect_fubon(*, realtime: bool = False, load_env: bool = True) -> FubonSession:
    """Login via .env + config/order.yaml; optional init_realtime()."""
    check_python_version()
    if load_env:
        load_project_dotenv()

    cfg = load_order_config()
    acct_cfg = account_block(cfg)

    user_id = os.environ.get(acct_cfg.get("user_id_env", "FUBON_USER_ID"), "").strip()
    if not user_id:
        user_id = "N125801238"

    password = os.environ.get(acct_cfg.get("password_env", "FUBON_PASSWORD"), "").strip()
    cert_env = acct_cfg.get("cert_path_env", "FUBON_CERT_PATH")
    cert_default = acct_cfg.get("default_cert_path", f"CAFubon/{user_id}/{user_id}.pfx")
    cert_path = _resolve_cert(os.environ.get(cert_env, cert_default))
    cert_pass = os.environ.get(
        acct_cfg.get("cert_password_env", "FUBON_CERT_PASSWORD"), user_id
    ).strip()
    api_key = os.environ.get(acct_cfg.get("api_key_env", "FUBON_API_KEY"), "").strip()

    if not cert_path.is_file():
        raise FileNotFoundError(f"Fubon cert not found: {cert_path}")

    from fubon_neo.sdk import FubonSDK

    _breaker_check()        # 冷卻中就直接丟 FubonBreakerOpen,連線請求不送出去
    cert_str = str(cert_path)
    try:
        sdk = FubonSDK()    # ← 2026-09-28/29 的 503 就是這一行丟出來的（登入之前）
        if api_key:
            res = sdk.apikey_login(user_id, api_key, cert_str, cert_pass)
        else:
            if not password:
                raise ValueError(
                    "FUBON_PASSWORD 未設定；或改用 FUBON_API_KEY（見 .env.example）"
                )
            res = sdk.login(user_id, password, cert_str, cert_pass)
        if not _login_is_success(res):
            msg = getattr(res, "message", "") or "login failed"
            raise RuntimeError(f"Fubon login failed: {msg}")
    except FubonBreakerOpen:
        raise
    except BaseException as exc:
        _breaker_record(exc)
        raise
    _breaker_clear()

    accounts = list(getattr(res, "data", []) or [])
    session = FubonSession(sdk=sdk, accounts=accounts)
    if realtime:
        session.init_realtime()
    return session


def account_label(acc: Any) -> str:
    branch = getattr(acc, "branch_no", getattr(acc, "branchNo", "?"))
    number = getattr(acc, "account", "?")
    name = getattr(acc, "name", "?")
    kind = getattr(acc, "account_type", getattr(acc, "accountType", "?"))
    return f"{name} · {branch}-{number} · {kind}"
