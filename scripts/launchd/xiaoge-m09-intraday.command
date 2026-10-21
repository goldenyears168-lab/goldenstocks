#!/usr/bin/env bash
# 小哥 M09／M07 盤中氣氛記錄器 · 平日 08:25 · observe only（唯讀、不下單）。
# 對「前一交易日盤點命中」的隔日沖慣犯股，08:30–13:30 記錄試撮／開盤／高低／五檔委買賣／成交量時間序列 → ${DATA}/chip/xiaoge_intraday/<日>/m09_ticks.jsonl。
# 單一富邦 session（REST quote，≤40 檔、間隔 ≥0.1 秒），13:32 自動結束並登出。
#
# 安裝（standalone，比照 xiaoge-inventory）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.xiaoge-m09-intraday.plist
#   sed -e "s#{{XIAOGE_M09_INTRADAY_LAUNCHER}}#$(pwd)/scripts/launchd/xiaoge-m09-intraday.command#g" -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.xiaoge-m09-intraday.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"
# 停用：launchctl bootout "gui/$(id -u)/com.jackm4.goldenstocks.xiaoge-m09-intraday"；或 .env 設 RUN_XIAOGE_M09_INTRADAY=0。

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
mkdir -p "${STATE}/logs" "${APP_SUPPORT}"
export TZ="${TZ:-Asia/Taipei}"
export PYTHONPATH="${ROOT}/src"

LOCKDIR="${APP_SUPPORT}/xiaoge-m09-intraday.lockdir"
if ! mkdir "${LOCKDIR}" 2>/dev/null; then
  holder="$(cat "${LOCKDIR}/pid" 2>/dev/null || echo "")"
  if [[ -n "${holder}" ]] && kill -0 "${holder}" 2>/dev/null; then echo "skip: already running (pid=${holder})"; exit 0; fi
  rm -rf "${LOCKDIR}"; mkdir "${LOCKDIR}" 2>/dev/null || { echo "skip: lock race"; exit 0; }
fi
echo "$$" > "${LOCKDIR}/pid"
trap 'rm -rf "${LOCKDIR}" 2>/dev/null || true' EXIT

if [[ -r "${STATE}/.env" ]]; then set +e; set -a; source "${STATE}/.env" 2>/dev/null; set +a; fi
if [[ "${RUN_XIAOGE_M09_INTRADAY:-1}" == "0" ]]; then echo "xiaoge-m09-intraday skipped: RUN_XIAOGE_M09_INTRADAY=0"; exit 0; fi

LOG="${STATE}/logs/xiaoge_m09_intraday_$(date +%Y%m%d).log"
{
  echo "[$(date -Iseconds)] start"
  nice -n 10 "${ROOT}/.venv/bin/python" "${ROOT}/scripts/ops/xiaoge_m09_intraday.py"
  echo "[$(date -Iseconds)] done rc=$?"
} >> "${LOG}" 2>&1
