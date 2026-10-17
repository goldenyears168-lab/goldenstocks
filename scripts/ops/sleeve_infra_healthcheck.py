#!/usr/bin/env python3
"""下單層基礎設施健檢（read-only · fail-loud · 不需要 LLM）。

為什麼要有這支：2026-08-10~19 這一週的 AI 盤中監看，每天產生 100+ 個 headless
session，而它抓到的**唯一**一類真問題全都是基礎設施缺陷（log 路徑 misroute、
worker 卡死、ledger 不更新）——那類東西是布林判定，不需要語言模型，用固定規則
就抓得到，而且不會像 AI 那樣把「自己讀不到檔案」誤判成交易異常而觸發 kill switch
（2026-08-18/19 momentum-rotation 就是這樣連停兩天）。

分工原則：
  * 本腳本（deterministic）＝基礎設施是否健康。每輪固定成本、可斷言、可回歸測試。
  * AI 監看＝**只在真的有部位時**判斷「策略行為跟已知劇本吻不吻合」。那才需要判斷力。

檢查項目：
  1. log 日期輪替 —— 2026-08-19 修掉的 bug 的回歸偵測（launcher 的 STAMP 只在啟動
     算一次，長駐 worker 會把跨日輸出全寫進舊檔）。實測曾連續 6 天寫進同一檔。
  2. worker 存活 —— launchd job 有無 PID、行程 uptime 是否異常長/短
  3. ledger 新鮮度 —— 交易時窗內 ledger 是否在合理時間內被更新過
  4. fail-closed 閘門的維護新鮮度 —— futures_daily_cache.json / pit_seat_flip_latest.json
     這兩份一旦過期，dayflip 的 ADV 與 flip 閘門會**靜默排除所有候選**（設計如此），
     從外面看起來跟「今天沒訊號」一模一樣
  5. 分點 tape 覆蓋率 —— 24 席當日覆蓋的個股數（2026-07-17 起從 ~1,850 掉到 ~1,050）
  6. kill switch —— 有沒有卡住沒清掉的旗標檔

退出碼：0=OK、1=WARN、2=FAIL。email 只是加分：2026-08-14 起 Gmail SMTP 憑證被拒
（535 BadCredentials），主要告警手段是退出碼＋報告檔。

  PYTHONPATH=src .venv/bin/python scripts/ops/sleeve_infra_healthcheck.py
  PYTHONPATH=src .venv/bin/python scripts/ops/sleeve_infra_healthcheck.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import stock_db  # noqa: E402

TZ = ZoneInfo("Asia/Taipei")

# label -> (log 檔名前綴, ledger 檔名 or None, 交易時窗 HH:MM)
SLEEVES = {
    "dayflip-short-poll": ("dayflip_short", "dayflip_short_ledger.json", ("08:44", "13:41")),
    "dayflip-post-dump-long-poll": ("dayflip_post_dump_long", "dayflip_post_dump_long_ledger.json", ("09:00", "13:41")),
    "momentum-rotation-poll": ("momentum_rotation", None, ("09:00", "13:30")),
    "tmf-channel-poll": ("tmf_channel_live", None, ("08:45", "13:45")),
    "futopt-books-collect": ("futopt_books_collect", None, ("08:45", "13:45")),
    "futopt-fill-listener": ("futopt_fill_listener", None, ("08:45", "13:45")),
}

# 這兩份過期會讓 dayflip 的閘門靜默排除所有候選
FAILCLOSED_ARTIFACTS = {
    "futures_daily_cache.json": 5,     # 最新資料日期距今允許的交易日數
    "pit_seat_flip_latest.json": 14,   # 對齊 _PIT_FLIP_MAX_AGE_DAYS
}
ARTIFACT_DIR = (
    stock_db.PROJECT_ROOT
    / "reports/research/branch-footprint-screen/dayflip_gapup_short"
)

LOG_STALE_SEC = 300          # 交易時窗內 log 超過這麼久沒寫 = 異常
LEDGER_STALE_SEC = 900
TAPE_COVERAGE_FLOOR = 800    # 24 席當日覆蓋個股數低於此 = WARN


class Findings:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, level: str, check: str, subject: str, detail: str) -> None:
        self.items.append(
            {"level": level, "check": check, "subject": subject, "detail": detail}
        )

    @property
    def exit_code(self) -> int:
        if any(i["level"] == "FAIL" for i in self.items):
            return 2
        if any(i["level"] == "WARN" for i in self.items):
            return 1
        return 0


def _launchctl_pids() -> dict[str, int]:
    """label -> pid（只收有 PID 的，即真的在跑的）。"""
    out: dict[str, int] = {}
    try:
        res = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return out
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or "goldenstocks" not in parts[2]:
            continue
        if parts[0].strip() in ("-", ""):
            continue
        try:
            out[parts[2].strip().rsplit(".", 1)[-1]] = int(parts[0])
        except ValueError:
            continue
    return out


def _in_window(now: datetime, window: tuple[str, str]) -> bool:
    if now.weekday() >= 5:
        return False
    return window[0] <= now.strftime("%H:%M") <= window[1]


def check_sleeves(f: Findings, now: datetime) -> None:
    pids = _launchctl_pids()
    intraday = stock_db.LOGS_DIR / "intraday"
    stamp = now.strftime("%Y%m%d")
    for label, (prefix, ledger_name, window) in SLEEVES.items():
        running = label in pids
        in_win = _in_window(now, window)

        if in_win and not running:
            f.add("FAIL", "worker_alive", label, "交易時窗內但 launchd 沒有這個 job 的 PID")
            continue
        if not running:
            continue

        # --- 1) log 日期輪替：今天的檔案必須存在（這正是 2026-08-19 修掉的 bug）
        today_log = intraday / f"{prefix}_{stamp}.log"
        stale_logs = sorted(intraday.glob(f"{prefix}_*.log"))
        if in_win and not today_log.exists():
            newest = max(stale_logs, key=lambda p: p.stat().st_mtime, default=None)
            hint = f"；最近被寫入的是 {newest.name}" if newest else ""
            f.add(
                "FAIL", "log_rotation", label,
                f"交易時窗內找不到今日 log {today_log.name}"
                f"——launcher 的日期輪替可能又壞了{hint}",
            )
        elif today_log.exists() and in_win:
            age = time.time() - today_log.stat().st_mtime
            if age > LOG_STALE_SEC:
                f.add("FAIL", "log_fresh", label,
                      f"今日 log 已 {age/60:.1f} 分鐘沒有新輸出（門檻 {LOG_STALE_SEC/60:.0f} 分）")

        # 即使不在時窗，也偵測「有 log 檔名早於今天、但 mtime 是今天」＝ misroute 復發
        for p in stale_logs:
            if p.name == today_log.name:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if time.time() - mtime < 3600:
                f.add("FAIL", "log_rotation", label,
                      f"{p.name} 的檔名日期不是今天，但 1 小時內仍在被寫入"
                      "＝長駐 worker 的 log 又寫進舊檔（STAMP 靜態化 bug 復發）")
                break

        # --- 2) ledger 新鮮度
        if ledger_name and in_win:
            lp = stock_db.DATA_DIR / "order" / ledger_name
            if not lp.exists():
                f.add("WARN", "ledger", label, f"交易時窗內 ledger 不存在：{lp.name}")
            else:
                age = time.time() - lp.stat().st_mtime
                if age > LEDGER_STALE_SEC:
                    f.add("WARN", "ledger", label,
                          f"ledger 已 {age/60:.1f} 分鐘沒更新（門檻 {LEDGER_STALE_SEC/60:.0f} 分）")


def check_failclosed_artifacts(f: Findings, now: datetime) -> None:
    """這兩份過期時，dayflip 的閘門會靜默排除所有候選——外觀跟『今天沒訊號』一樣。"""
    p = ARTIFACT_DIR / "futures_daily_cache.json"
    if not p.exists():
        f.add("FAIL", "failclosed_artifact", p.name,
              "檔案不存在 → ADV20 閘門 fail-closed，會排除所有候選")
    else:
        try:
            m = json.loads(p.read_text())
            latest = max(d for v in m.values() for d in v)
            lag = (now.date() - datetime.fromisoformat(latest).date()).days
            if lag > FAILCLOSED_ARTIFACTS["futures_daily_cache.json"]:
                f.add("FAIL", "failclosed_artifact", p.name,
                      f"最新資料 {latest}（落後 {lag} 天）→ ADV20 閘門會排除所有候選。"
                      "刷新：scripts/research/refresh_dayflip_futures_daily_cache.py")
        except (ValueError, OSError) as exc:
            f.add("FAIL", "failclosed_artifact", p.name, f"無法解析：{exc}")

    p = ARTIFACT_DIR / "pit_seat_flip_latest.json"
    if not p.exists():
        f.add("FAIL", "failclosed_artifact", p.name,
              "檔案不存在 → flip 閘門 fail-closed，會排除所有候選")
    else:
        try:
            payload = json.loads(p.read_text())
            computed = datetime.fromisoformat(str(payload["computed_at"])).date()
            age = (now.date() - computed).days
            if age > FAILCLOSED_ARTIFACTS["pit_seat_flip_latest.json"]:
                f.add("FAIL", "failclosed_artifact", p.name,
                      f"computed_at={computed}（{age} 天前）超過 _PIT_FLIP_MAX_AGE_DAYS"
                      " → flip 閘門會排除所有候選。刷新：週五 dayflip-seat-weekly-refresh")
            vals = payload.get("values") or {}
            usable = sum(1 for v in vals.values() if isinstance(v, (int, float)) and v >= 0.40)
            if usable == 0:
                f.add("FAIL", "failclosed_artifact", p.name,
                      "沒有任何席位 flip ≥ 0.40 → 等同全部排除")
        except (ValueError, OSError, KeyError) as exc:
            f.add("FAIL", "failclosed_artifact", p.name, f"無法解析：{exc}")


def check_tape_coverage(f: Findings, now: datetime) -> None:
    """分點 tape 覆蓋率——訊號『沒出現』與『看不見』的分界線。"""
    try:
        spec = json.loads((ARTIFACT_DIR / "FROZEN_SPEC_V1.json").read_text())
        seats = list(spec["seat_flip_table_frozen"]["values"])
    except (OSError, ValueError, KeyError) as exc:
        f.add("WARN", "tape_coverage", "FROZEN_SPEC_V1.json", f"無法讀席位清單：{exc}")
        return
    try:
        con = sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        f.add("WARN", "tape_coverage", "db", f"無法連線：{exc}")
        return
    try:
        ph = ",".join("?" * len(seats))
        row = con.execute(
            f"SELECT trade_date, COUNT(DISTINCT stock_id) FROM stock_broker_branch_daily "
            f"WHERE securities_trader_id IN ({ph}) "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1",
            seats,
        ).fetchone()
    except sqlite3.Error as exc:
        f.add("WARN", "tape_coverage", "db", f"查詢失敗：{exc}")
        return
    finally:
        con.close()
    if not row:
        f.add("FAIL", "tape_coverage", "stock_broker_branch_daily", "24 席查無任何資料")
        return
    d, n = row
    lag = (now.date() - datetime.fromisoformat(str(d)).date()).days
    if lag > 4:
        f.add("WARN", "tape_coverage", "stock_broker_branch_daily",
              f"最新分點資料停在 {d}（落後 {lag} 天）")
    if n < TAPE_COVERAGE_FLOOR:
        f.add("WARN", "tape_coverage", "stock_broker_branch_daily",
              f"{d} 只覆蓋 {n} 檔（門檻 {TAPE_COVERAGE_FLOOR}）"
              "——訊號『沒出現』可能其實是『看不見』，不要當成市場沒機會")


def check_kill_switches(f: Findings, now: datetime) -> None:
    order_dir = stock_db.DATA_DIR / "order"
    if not order_dir.exists():
        return
    for p in sorted(order_dir.glob("*kill_switch*.json")):
        try:
            payload = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        d = str(payload.get("date") or "")
        reason = str(payload.get("reason") or "")[:120]
        if d == now.strftime("%Y-%m-%d"):
            f.add("WARN", "kill_switch", p.name,
                  f"今日 kill switch 生效中：{reason}")
        elif d:
            try:
                age = (now.date() - datetime.fromisoformat(d).date()).days
            except ValueError:
                continue
            if age > 3:
                f.add("WARN", "kill_switch", p.name,
                      f"{age} 天前的 kill switch 檔還在（date={d}），確認是否該清掉")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    ap.add_argument("--now", default=None, help="覆寫檢查時間（測試用，ISO 格式）")
    args = ap.parse_args()

    now = datetime.fromisoformat(args.now).replace(tzinfo=TZ) if args.now else datetime.now(TZ)
    f = Findings()
    check_sleeves(f, now)
    check_failclosed_artifacts(f, now)
    check_tape_coverage(f, now)
    check_kill_switches(f, now)

    code = f.exit_code
    if args.json:
        print(json.dumps(
            {"checked_at": now.isoformat(), "exit_code": code, "findings": f.items},
            ensure_ascii=False, indent=1,
        ))
    else:
        status = {0: "OK", 1: "WARN", 2: "FAIL"}[code]
        print(f"=== sleeve 基礎設施健檢 {now:%Y-%m-%d %H:%M:%S} → {status} ===")
        if not f.items:
            print("  全部正常")
        for i in f.items:
            print(f"  [{i['level']:4s}] {i['check']:22s} {i['subject']:32s} {i['detail']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
