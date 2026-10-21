#!/usr/bin/env bash
# 小哥盤點 · 每平日 22:30 · observe only（不下單、不寫正本 DB）。
# 順序：xiaoge_inventory.py（把書中戰法逐一檢查是否重現，寫 chip/xiaoge_inventory/inventory_<日>.json／.md）
#       → headless claude -p 解讀成 inventory_<日>_ai.md（prompt SSOT：scripts/launchd/xiaoge-inventory-prompt.txt，cwd=資料目錄以套用其 CLAUDE.md 護欄）。
# 22:30 排在 xiaoge-chip-daily（19:40 起，含 burst 與回補約 21:45 結束）之後。AI 步驟失敗只警告，不影響 JSON 產出。
#
# 安裝（standalone，比照 xiaoge-chip-daily）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.xiaoge-inventory.plist
#   sed -e "s#{{XIAOGE_INVENTORY_LAUNCHER}}#$(pwd)/scripts/launchd/xiaoge-inventory.command#g" -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.xiaoge-inventory.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"
# 停用：launchctl bootout "gui/$(id -u)/com.jackm4.goldenstocks.xiaoge-inventory"；或 .env 設 RUN_XIAOGE_INVENTORY=0（只關 AI 步驟：RUN_XIAOGE_INVENTORY_AI=0）。

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
mkdir -p "${STATE}/logs" "${APP_SUPPORT}"
export TZ="${TZ:-Asia/Taipei}"
export PYTHONPATH="${ROOT}/src"
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

LOCKDIR="${APP_SUPPORT}/xiaoge-inventory.lockdir"
if ! mkdir "${LOCKDIR}" 2>/dev/null; then
  holder="$(cat "${LOCKDIR}/pid" 2>/dev/null || echo "")"
  if [[ -n "${holder}" ]] && kill -0 "${holder}" 2>/dev/null; then echo "skip: already running (pid=${holder})"; exit 0; fi
  rm -rf "${LOCKDIR}"; mkdir "${LOCKDIR}" 2>/dev/null || { echo "skip: lock race"; exit 0; }
fi
echo "$$" > "${LOCKDIR}/pid"
trap 'rm -rf "${LOCKDIR}" 2>/dev/null || true' EXIT

if [[ -r "${STATE}/.env" ]]; then set +e; set -a; source "${STATE}/.env" 2>/dev/null; set +a; fi
if [[ "${RUN_XIAOGE_INVENTORY:-1}" == "0" ]]; then echo "xiaoge-inventory skipped: RUN_XIAOGE_INVENTORY=0"; exit 0; fi

PYTHON="${ROOT}/.venv/bin/python"
LOG="${STATE}/logs/xiaoge_inventory_$(date +%Y%m%d).log"
DATA_DIR="${GOLDENSTOCKS_DATA_DIR:-${HOME}/goldenstocks-data}"
{
  echo "[$(date -Iseconds)] start"
  nice -n 10 "${PYTHON}" "${ROOT}/scripts/ops/xiaoge_inventory.py" --md
  rc=$?
  echo "[$(date -Iseconds)] inventory rc=${rc}"
  nice -n 10 "${PYTHON}" "${ROOT}/scripts/ops/xiaoge_m09_followup.py" || echo "WARN: m09_followup rc=$?"
  nice -n 10 "${PYTHON}" "${ROOT}/scripts/ops/xiaoge_validation_journal.py" || echo "WARN: validation_journal rc=$?"
  nice -n 10 "${PYTHON}" "${ROOT}/scripts/ops/xiaoge_ledger.py" --live || echo "WARN: xiaoge_ledger rc=$?"
  if [[ "${rc}" -eq 0 && "${RUN_XIAOGE_INVENTORY_AI:-1}" != "0" ]]; then
    if [[ -r "${DATA_DIR}/.env.schedule" ]]; then source "${DATA_DIR}/.env.schedule"; fi
    CLAUDE="${HOME}/.local/bin/claude"
    [[ -x "${CLAUDE}" ]] || CLAUDE="$(ls -td "${HOME}/Library/Application Support/Claude/claude-code/"*/claude.app/Contents/MacOS/claude 2>/dev/null | head -1)"
    if [[ -n "${CLAUDE}" && -x "${CLAUDE}" ]]; then
      ( cd "${DATA_DIR}" && "${CLAUDE}" -p "$(cat "${ROOT}/scripts/launchd/xiaoge-inventory-prompt.txt")" --model sonnet --dangerously-skip-permissions ) \
        || echo "WARN: AI 解讀失敗 rc=$?"
    else
      echo "WARN: 找不到 claude 執行檔，略過 AI 解讀"
    fi
  fi
  echo "[$(date -Iseconds)] done"
} >> "${LOG}" 2>&1
