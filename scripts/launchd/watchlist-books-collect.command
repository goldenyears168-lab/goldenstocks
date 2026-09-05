#!/usr/bin/env bash
# 固定觀察清單（高波動 45，含台達電 2308）五檔委託簿收集器 · 常駐（KeepAlive）·
# **唯讀、無送單路徑、不碰富邦 session**。2026-09-05 建立。
#
# 動機：要看「大戶的牆在哪、撐不撐得住」——成交側看得到隊列被吃多少、看不到補進多少，
# 只有委託簿看得到。limitup-books 只收漲停候選，這支收一份固定清單，每天記錄各檔五檔掛單。
#
# ⚠️ 走 TWSE MIS 公開 API，**不佔富邦 ws 連線額度**（mini 已有 5 條，第 6 條撞過上限）。
# 清單來源：WL_SYMS 覆寫 / WL_FILE / 預設 _live_calib.json 的 universe。
#
# 安裝（Mac mini，一次性，不透過 scripts/install-launchd.sh 的 LABELS 陣列）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.watchlist-books-collect.plist
#   sed -e "s#{{WATCHLIST_BOOKS_COLLECT_LAUNCHER}}#$(pwd)/scripts/launchd/watchlist-books-collect.command#g" \
#       -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.watchlist-books-collect.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"
#
# 卸載：
#   launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.jackm4.goldenstocks.watchlist-books-collect.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
WORKER_PY="${ROOT}/scripts/research/collect_watchlist_books.py"

mkdir -p "${STATE}/logs/intraday" "${APP_SUPPORT}" "${HOME}/Library/Logs/com.jackm4.goldenstocks"
export TZ="${TZ:-Asia/Taipei}"
export PYTHONPATH="${ROOT}/src"

# --- 反重跑：PID 存活判定 ---
LOCKDIR="${APP_SUPPORT}/watchlist-books-collect.lockdir"
if ! mkdir "${LOCKDIR}" 2>/dev/null; then
  holder="$(cat "${LOCKDIR}/pid" 2>/dev/null || echo "")"
  if [[ -n "${holder}" ]] && kill -0 "${holder}" 2>/dev/null; then
    echo "skip: already holding lock (pid=${holder}, alive)"; exit 0
  fi
  rm -rf "${LOCKDIR}"; mkdir "${LOCKDIR}" 2>/dev/null || { echo "skip: lock race lost"; exit 0; }
fi
echo "$$" > "${LOCKDIR}/pid"
trap 'rm -rf "${LOCKDIR}" 2>/dev/null || true' EXIT

REAP_PAT="collect_watchlist_books.py"
for stale in $(pgrep -f "${REAP_PAT}" 2>/dev/null || true); do
  [[ "${stale}" == "$$" ]] && continue
  echo "reaping pre-existing pid=${stale}"; kill -TERM "${stale}" 2>/dev/null || true
done

# 清單預設用 _live_calib.json（高波動 45）；POLL_SEC 5 秒（45 檔一請求即收完）
export WL_POLL_SEC="${WL_POLL_SEC:-5}"

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then echo "✗ missing venv python: ${PYTHON}"; exit 1; fi
ROTATING_TEE="${ROOT}/scripts/launchd/rotating_tee.py"
LOG_PREFIX="${STATE}/logs/intraday/watchlist_books_collect"

exec "${PYTHON}" "${WORKER_PY}" 2>&1 | "${PYTHON}" "${ROTATING_TEE}" "${LOG_PREFIX}"
