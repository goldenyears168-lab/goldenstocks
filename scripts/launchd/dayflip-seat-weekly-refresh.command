#!/usr/bin/env bash
# launchd：每週五 21:30 · 隔日沖席位名單週度刷新（重算24席flip + 漂移監控 + 週一候選清單）。
# 研究層（scripts/research/），非下單層：不下單、不改倉、不自動修改 FROZEN_SPEC_V1.json。
# 手動除錯：open -gj scripts/launchd/dayflip-seat-weekly-refresh.command
# 知識庫：reports/research/branch-footprint-screen/dayflip_gapup_short/GAPUP_SHORT_SIZING.md
#         reports/research/branch-footprint-screen/dayflip_gapup_short/FROZEN_SPEC_V1.json
#
# 安裝（Mac mini，一次性，不透過 scripts/install-launchd.sh 的下單層陣列）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.dayflip-seat-weekly-refresh.plist
#   sed -e "s#{{DAYFLIP_SEAT_WEEKLY_LAUNCHER}}#$(pwd)/scripts/launchd/dayflip-seat-weekly-refresh.command#g" \
#       -e "s#{{PROJECT_ROOT}}#$(pwd)#g" \
#       -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.dayflip-seat-weekly-refresh.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"
#
# 卸載：
#   launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.jackm4.goldenstocks.dayflip-seat-weekly-refresh.plist
#   rm ~/Library/LaunchAgents/com.jackm4.goldenstocks.dayflip-seat-weekly-refresh.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STAMP="$(date '+%Y%m%d')"
LOG_DIR="${HOME}/Library/Logs/etf"
RUN_LOG="${LOG_DIR}/dayflip_seat_weekly_refresh_${STAMP}.log"
mkdir -p "${LOG_DIR}"

echo "" | tee -a "${RUN_LOG}"
echo "=== launchd dayflip-seat-weekly-refresh 開始 $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "${RUN_LOG}"

export PYTHONPATH="${ROOT}/src:${ROOT}/scripts/research"
PYTHON="${ROOT}/.venv/bin/python"

if [[ -r "${GOLDENSTOCKS_DATA_DIR:-${ROOT}}/.env" ]]; then
  set +e; set -a; # shellcheck disable=SC1091
  source "${GOLDENSTOCKS_DATA_DIR:-${ROOT}}/.env" 2>/dev/null; set +a; set -e
fi

if [[ ! -x "${PYTHON}" ]]; then
  echo "✗ missing venv python: ${PYTHON}" | tee -a "${RUN_LOG}"; exit 1
fi

# 1) 補足個股期貨標的當日價格（必要集合 73 檔，供 flip 重算用）
set +e
"${PYTHON}" "${ROOT}/scripts/research/backfill_stock_futures_underlying_prices.py" \
  --start "$(date '+%Y-%m-%d')" --end "$(date '+%Y-%m-%d')" 2>&1 | tee -a "${RUN_LOG}"
PRICE_EXIT=${PIPESTATUS[0]}
set -e
[[ "${PRICE_EXIT}" -ne 0 ]] && echo "⚠ 價格補檔 exit=${PRICE_EXIT}（沿用既有資料續跑）" | tee -a "${RUN_LOG}"

# 2) 24席 flip 重算 + 漂移監控 + 候選清單
set +e
"${PYTHON}" "${ROOT}/scripts/research/run_dayflip_weekly_seat_refresh.py" 2>&1 | tee -a "${RUN_LOG}"
REFRESH_EXIT=${PIPESTATUS[0]}
set -e

echo "=== launchd dayflip-seat-weekly-refresh 結束 price=${PRICE_EXIT} refresh=${REFRESH_EXIT} $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "${RUN_LOG}"

if [[ "${REFRESH_EXIT}" -ne 0 ]]; then
  set +e
  "${PYTHON}" "${ROOT}/scripts/notify_job_result.py" \
    --subject-prefix="隔日沖席位週度刷新" \
    --exit-code="${REFRESH_EXIT}" \
    --log-path="${RUN_LOG}" \
    --extra="席位刷新失敗，詳見 log" \
    --env-flag="RUN_DAYFLIP_SEAT_WEEKLY_EMAIL" 2>/dev/null
  set -e
fi

exit "${REFRESH_EXIT}"
