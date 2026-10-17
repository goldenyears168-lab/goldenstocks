#!/usr/bin/env python3
"""逐行按日期輪替的 tee —— 給長駐 KeepAlive worker 用。

背景（2026-08-19 修復）：launcher 過去寫成

    STAMP="$(date '+%Y%m%d')"
    TICK_LOG=".../dayflip_short_${STAMP}.log"
    python worker.py 2>&1 | tee -a "${TICK_LOG}"

`STAMP` 只在**行程啟動當下**算一次。對 StartInterval 冷啟動型 job 沒問題（每次執行
都重算），但對 KeepAlive 長駐 worker 是 bug：一個活了 6 天的行程會把 6 天的 log 全部
寫進啟動那天的檔名。實測 2026-08-19 當天，dayflip_short / dayflip_post_dump_long
仍在寫 `*_20260813.log`（worker 自 08-13 10:14 長駐未重啟）。

後果不只是檔名難看——任何「讀 `<name>_<今天>.log`」的下游（監看 agent、事後分析）
都會讀到空檔或舊資料。momentum-rotation 的 AI 監看就因此連兩天觸發 kill switch。

用法（取代 `tee -a`）：

    python worker.py 2>&1 | rotating_tee.py .../logs/intraday/dayflip_short

每寫一行才決定目的檔 `<prefix>_<YYYYMMDD>.log`，跨日自動換檔。同時把該行送到 stdout，
維持原本 `tee` 的語義（launchd StandardOutPath 仍收得到）。

`--rollover-hour N`：把 00:00~N:00 之間的輸出算成**前一天**。給夜盤長 session 用
（例如台指期夜盤 15:00 開到隔日 05:00，用 `--rollover-hour 5` 可讓整段夜盤留在同一檔）。
預設 0 ＝ 純日曆日，不做任何位移。

設計原則：**永遠不要讓 log 這件事殺掉 worker**。所有檔案 I/O 例外都吞掉並降級成
stderr 警告，stdout 轉發永遠繼續。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path


def _target_date(now: _dt.datetime, rollover_hour: int) -> _dt.date:
    """回傳這一行該歸屬的日期。

    rollover_hour=0 → 純日曆日。
    rollover_hour=N → 當地時間 00:00~N:00（不含 N:00）算前一天。
    """
    if rollover_hour and now.hour < rollover_hour:
        return (now - _dt.timedelta(days=1)).date()
    return now.date()


class _RotatingWriter:
    """持有一個檔案 handle，日期一變就換檔。"""

    def __init__(self, prefix: Path, rollover_hour: int) -> None:
        self._prefix = prefix
        self._rollover_hour = rollover_hour
        self._current_key: str | None = None
        self._fh = None
        self._warned_paths: set[str] = set()

    def _path_for(self, key: str) -> Path:
        return self._prefix.with_name(f"{self._prefix.name}_{key}.log")

    def _warn_once(self, path: str, exc: BaseException) -> None:
        # 同一個壞路徑只吵一次，避免每 20 秒噴一行 stderr 塞爆 launchd log
        if path in self._warned_paths:
            return
        self._warned_paths.add(path)
        print(f"rotating_tee: cannot write {path}: {exc}", file=sys.stderr, flush=True)

    def write(self, line: str, now: _dt.datetime) -> None:
        key = _target_date(now, self._rollover_hour).strftime("%Y%m%d")
        if key != self._current_key:
            self._close()
            path = self._path_for(key)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(path, "a", encoding="utf-8", errors="replace")
                self._current_key = key
            except OSError as exc:
                self._fh = None
                # current_key 仍設成 key，否則每一行都會重試 mkdir/open 拖慢 worker
                self._current_key = key
                self._warn_once(str(path), exc)
                return
        if self._fh is None:
            return
        try:
            self._fh.write(line)
            self._fh.flush()
        except OSError as exc:
            self._warn_once(str(self._path_for(key)), exc)

    def _close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def close(self) -> None:
        self._close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="逐行按日期輪替的 tee（給長駐 worker 取代 `tee -a`）",
    )
    parser.add_argument(
        "prefix",
        help="log 路徑前綴，實際寫入 <prefix>_<YYYYMMDD>.log",
    )
    parser.add_argument(
        "--rollover-hour",
        type=int,
        default=0,
        help="00:00~N:00 算前一天（夜盤用）；預設 0 ＝ 純日曆日",
    )
    args = parser.parse_args(argv)

    if not 0 <= args.rollover_hour <= 23:
        parser.error("--rollover-hour 必須在 0~23 之間")

    writer = _RotatingWriter(Path(args.prefix), args.rollover_hour)
    stdin = sys.stdin
    try:
        for line in stdin:
            # 先轉發 stdout：即使檔案端壞掉，launchd 的 StandardOutPath 仍要看得到
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except (BrokenPipeError, OSError):
                pass
            writer.write(line, _dt.datetime.now())
    except KeyboardInterrupt:
        pass
    finally:
        writer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
