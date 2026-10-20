#!/usr/bin/env bash
# 小哥籌碼每日抓取＋筆記本 · 每平日 19:40 · observe only（不下單、不寫正本 DB）。
# 順序：xiaoge_chip_daily.py（富邦：流動性前 579 檔個股頁＋全部活躍分點頁，約 35 分鐘）→ xiaoge_notebook.py（8771 /xiaoge 讀的 JSON）。
# 富邦當日資料未就緒（退出碼 3）時每 10 分鐘重試，最多 6 次。與 branch-tape-prewarm（16:30／18:30）錯開，避免同時打富邦。
#
# 安裝（Mac mini，standalone，比照 chip-margin-catchup 不進 install-launchd.sh LABELS）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.xiaoge-chip-daily.plist
#   sed -e "s#{{XIAOGE_CHIP_DAILY_LAUNCHER}}#$(pwd)/scripts/launchd/xiaoge-chip-daily.command#g" \
#       -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.xiaoge-chip-daily.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"
# 停用：launchctl bootout "gui/$(id -u)/com.jackm4.goldenstocks.xiaoge-chip-daily"；或 .env 設 RUN_XIAOGE_CHIP_DAILY=0。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
mkdir -p "${STATE}/logs" "${APP_SUPPORT}"
export TZ="${TZ:-Asia/Taipei}"
export PYTHONPATH="${ROOT}/src"

LOCKDIR="${APP_SUPPORT}/xiaoge-chip-daily.lockdir"
if ! mkdir "${LOCKDIR}" 2>/dev/null; then
  holder="$(cat "${LOCKDIR}/pid" 2>/dev/null || echo "")"
  if [[ -n "${holder}" ]] && kill -0 "${holder}" 2>/dev/null; then
    echo "skip: already running (pid=${holder})"; exit 0
  fi
  rm -rf "${LOCKDIR}"; mkdir "${LOCKDIR}" 2>/dev/null || { echo "skip: lock race"; exit 0; }
fi
echo "$$" > "${LOCKDIR}/pid"
trap 'rm -rf "${LOCKDIR}" 2>/dev/null || true' EXIT

_load_env_file() {
  [[ -r "$1" ]] || return 0
  set +e; set -a; source "$1" 2>/dev/null; local rc=$?; set +a; set -e
  [[ "${rc}" -ne 0 ]] && echo "WARN: cannot source $2 rc=${rc}"
  return 0
}
_load_env_file "${STATE}/.env" "project .env"

if [[ "${RUN_XIAOGE_CHIP_DAILY:-1}" == "0" ]]; then
  echo "xiaoge-chip-daily skipped: RUN_XIAOGE_CHIP_DAILY=0"; exit 0
fi
# 週末與非交易日：xiaoge_chip_daily.py 以最新日線日期為準；若該日資料已抓過（續跑）會直接略過已完成的股票。

PYTHON="${ROOT}/.venv/bin/python"
LOG="${STATE}/logs/xiaoge_chip_daily_$(date +%Y%m%d).log"
{
  echo "[$(date -Iseconds)] start"
  rc=3
  for attempt in 1 2 3 4 5 6; do
    set +e
    nice -n 10 "${PYTHON}" "${ROOT}/scripts/ops/xiaoge_chip_daily.py"
    rc=$?
    set -e
    [[ "${rc}" -ne 3 ]] && break
    echo "富邦尚未就緒，10 分鐘後重試（${attempt}/6）"; sleep 600
  done
  echo "[$(date -Iseconds)] chip_daily rc=${rc}"
  if [[ "${rc}" -eq 0 ]]; then
    nice -n 10 "${PYTHON}" "${ROOT}/scripts/ops/xiaoge_notebook.py" || echo "WARN: xiaoge_notebook failed rc=$?"
  else
    echo "WARN: 抓取未成功（rc=${rc}），不產生筆記本"
  fi
  # 歷史回補：慣犯判讀需要近 90 日的個股頁；每晚在主流程之後再回補一天（流動性前 250 檔，最多 45 分鐘），補齊後自動不再執行
  if [[ "${rc}" -eq 0 ]]; then
    nice -n 10 "${PYTHON}" "${ROOT}/scripts/ops/xiaoge_chip_daily.py" --backfill --top 250 --max-minutes 45 || echo "WARN: backfill rc=$?"
    nice -n 10 "${PYTHON}" "${ROOT}/scripts/ops/xiaoge_notebook.py" || echo "WARN: xiaoge_notebook(after backfill) failed rc=$?"
  fi
  echo "[$(date -Iseconds)] done"
} >> "${LOG}" 2>&1
