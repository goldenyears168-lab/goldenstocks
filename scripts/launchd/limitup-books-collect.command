#!/usr/bin/env bash
# 現股漲停候選五檔收集器 · 常駐（KeepAlive）· **唯讀、無送單路徑、不碰富邦 session**。
#
# 2026-09-02 建立。動機：漲停鎖不鎖得住取決於漲停價上「買方排隊量的補進速度」，
# 而逐筆成交只看得到隊列被吃掉多少、看不到補進多少。研究已證實成交側那一半
# 判別力不足（consume AUC 0.34），缺的是委託簿側。
#
# ⚠️ 刻意**不用**富邦 websocket：mini 已有 5 條富邦 ws（tmf-channel-poll／
# momentum-rotation-poll／futopt-books-collect／futopt-books-collect-stocks／
# futopt-fill-listener），2026-09-01 09:51 第 6 條撞 "Maximum number of
# connections reached"，指數組日盤掉 52%。本收集器走 TWSE MIS 公開 API，
# 另一條路，不與生產爭連線額度。
#
# 安裝（Mac mini，一次性，不透過 scripts/install-launchd.sh 的 LABELS 陣列）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.limitup-books-collect.plist
#   sed -e "s#{{LIMITUP_BOOKS_COLLECT_LAUNCHER}}#$(pwd)/scripts/launchd/limitup-books-collect.command#g" \
#       -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.limitup-books-collect.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"
#
# 卸載：
#   launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.jackm4.goldenstocks.limitup-books-collect.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
WORKER_PY="${ROOT}/scripts/research/collect_limitup_books.py"

mkdir -p "${STATE}/logs/intraday" "${APP_SUPPORT}" "${HOME}/Library/Logs/com.jackm4.goldenstocks"
export TZ="${TZ:-Asia/Taipei}"
export PYTHONPATH="${ROOT}/src"

# --- 反重跑：PID 存活判定 ---
LOCKDIR="${APP_SUPPORT}/limitup-books-collect.lockdir"
if ! mkdir "${LOCKDIR}" 2>/dev/null; then
  holder="$(cat "${LOCKDIR}/pid" 2>/dev/null || echo "")"
  if [[ -n "${holder}" ]] && kill -0 "${holder}" 2>/dev/null; then
    echo "skip: already holding lock (pid=${holder}, alive)"; exit 0
  fi
  rm -rf "${LOCKDIR}"; mkdir "${LOCKDIR}" 2>/dev/null || { echo "skip: lock race lost"; exit 0; }
fi
echo "$$" > "${LOCKDIR}/pid"
trap 'rm -rf "${LOCKDIR}" 2>/dev/null || true' EXIT

REAP_PAT="collect_limitup_books.py"
for stale in $(pgrep -f "${REAP_PAT}" 2>/dev/null || true); do
  [[ "${stale}" == "$$" ]] && continue
  echo "reaping pre-existing pid=${stale}"; kill -TERM "${stale}" 2>/dev/null || true
done

# 掃描 +6% 以上進追蹤名單；追蹤層每 3 秒取一次五檔。禮貌節流 0.35s/請求。
# 同儕表（統計同儕，日報酬相關最高 5 檔）每 7 天重建一次；失敗不擋收集。
PEER_MAP="${STATE}/data/cache/limitup_books/_peer_map.json"
if [[ ! -f "${PEER_MAP}" ]] || [[ -n "$(find "${PEER_MAP}" -mtime +7 2>/dev/null)" ]]; then
  echo "rebuilding peer map…"
  "${ROOT}/.venv/bin/python" "${ROOT}/scripts/research/build_peer_map.py" 60 5 || true
fi

export LB_WATCH_PCT="${LB_WATCH_PCT:-0.06}"
export LB_SCAN_SEC="${LB_SCAN_SEC:-300}"
export LB_POLL_SEC="${LB_POLL_SEC:-5}"
export LB_PEER_POLL_SEC="${LB_PEER_POLL_SEC:-15}"
export LB_MAX_PEER="${LB_MAX_PEER:-250}"
export LB_MAX_WATCH="${LB_MAX_WATCH:-180}"
export LB_REQ_GAP="${LB_REQ_GAP:-0.35}"

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then echo "✗ missing venv python: ${PYTHON}"; exit 1; fi
ROTATING_TEE="${ROOT}/scripts/launchd/rotating_tee.py"
LOG_PREFIX="${STATE}/logs/intraday/limitup_books_collect"

exec "${PYTHON}" "${WORKER_PY}" 2>&1 | "${PYTHON}" "${ROTATING_TEE}" "${LOG_PREFIX}"
