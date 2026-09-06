#!/usr/bin/env bash
# 45 檔個股期貨五檔深度（期交所 MIS 輪詢）· 平日 08:45–13:45 · **唯讀，無送單路徑**。
#
# 為什麼走 MIS 而不是 Fubon ws books：補齊 45 檔要 34 root × 4 訂閱 = 136 個訂閱、
# 得再開 3 條 ws 連線。mini 已掛 11 個 job，含唯一 live order-capable 的 tmf-channel-poll；
# futopt-books-collect-stocks.command 檔頭記著 2026-08-21「108 訂閱擠一條連線 → 日盤掉
# 157/300 分鐘」的事故。MIS 是純 HTTP、不佔連線額度，一次請求就回 45 檔完整五檔。
#
# 安裝（一次性）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.futopt-mis-depth-collect.plist
#   sed -e "s#{{FUTOPT_MIS_DEPTH_LAUNCHER}}#$(pwd)/scripts/launchd/futopt-mis-depth-collect.command#g" \
#       -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.futopt-mis-depth-collect.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
export TZ="${TZ:-Asia/Taipei}"
export PYTHONPATH="${ROOT}/src"
mkdir -p "${STATE}/logs/intraday" "${APP_SUPPORT}"

DOW="$(date '+%u')"; HHMM="$(date '+%H:%M')"
[[ "${DOW}" -gt 5 ]] && { echo "skip: 非平日"; exit 0; }
[[ "${HHMM}" > "13:40" ]] && { echo "skip: ${HHMM} 已過收集窗"; exit 0; }

LOCKDIR="${APP_SUPPORT}/futopt-mis-depth.lockdir"
if ! mkdir "${LOCKDIR}" 2>/dev/null; then
  holder="$(cat "${LOCKDIR}/pid" 2>/dev/null || echo "")"
  if [[ -n "${holder}" ]] && kill -0 "${holder}" 2>/dev/null; then
    echo "skip: already running (pid=${holder})"; exit 0
  fi
  rm -rf "${LOCKDIR}"; mkdir "${LOCKDIR}" 2>/dev/null || { echo "skip: lock race"; exit 0; }
fi
echo "$$" > "${LOCKDIR}/pid"
trap 'rm -rf "${LOCKDIR}" 2>/dev/null || true' EXIT

_src() { [[ -r "$1" ]] && { set +e; set -a; source "$1" 2>/dev/null; set +a; set -e; }; return 0; }
_src "${APP_SUPPORT}/order.env"; _src "${STATE}/.env"
[[ "${RUN_FUTOPT_MIS_DEPTH:-1}" == "0" ]] && { echo "skipped: RUN_FUTOPT_MIS_DEPTH=0"; exit 0; }

PY="${ROOT}/.venv/bin/python"
LOG="${STATE}/logs/intraday/futopt_mis_depth_$(date '+%Y%m%d').log"
exec "${PY}" "${ROOT}/scripts/research/collect_futopt_mis_depth.py" \
     --interval "${MIS_DEPTH_INTERVAL:-60}" --until "${MIS_DEPTH_UNTIL:-13:45}" >>"${LOG}" 2>&1
