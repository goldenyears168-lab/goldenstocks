#!/usr/bin/env bash
# 週日深度補庫 · 每週日 20:00 · 唯讀行情/財報 API + 寫入 stock_beta/stock_consensus/
# stock_financial_history/stock_block_trade/stock_branch_daily/stock_shareholding_daily 等。
# 根因背景：scripts/weekly_sync.sh 檔頭寫「建議週日20:00排程」但從未被排程，只能靠人
# 手動雙擊 scripts/2000週日補庫.command，2026-09-27 稽核發現 stock_beta 停在
# 2026-07-06、stock_consensus 停在 2026-07-03，近12週沒人按過。本 job 根治之
# （同一類 bug 先前已在 vixtwn-daily-sync 修過一次，這是同一模式）。
#
# 安裝（Mac mini，standalone，比照 vixtwn-daily-sync/chip-lot-probe 不進
# install-launchd.sh LABELS）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.weekly-sync.plist
#   sed -e "s#{{WEEKLY_SYNC_LAUNCHER}}#$(pwd)/scripts/launchd/weekly-sync.command#g" \
#       -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.weekly-sync.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
mkdir -p "${APP_SUPPORT}"
export TZ="${TZ:-Asia/Taipei}"

LOCKDIR="${APP_SUPPORT}/weekly-sync.lockdir"
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

if [[ "${RUN_WEEKLY_SYNC:-1}" == "0" ]]; then
  echo "weekly-sync skipped: RUN_WEEKLY_SYNC=0"; exit 0
fi

# weekly_sync.sh 自己會再 source 一次 .env、自己管 logs/weekly_sync_*.log，這裡只負責
# lockdir 防重疊 + 頂層 RUN_WEEKLY_SYNC 開關，實際步驟細節(RUN_STOCK_MARKET_SYNC/
# RUN_CHIP_SYNC/RUN_SPONSOR_CHIP_SYNC/RUN_SCREENER_DATA_SYNC)仍各自的 .env 旗標決定。
"${ROOT}/scripts/weekly_sync.sh" --weekly-report
