#!/usr/bin/env bash
# 大戶盤中佈局監看 · 一次性（09:00 啟動、13:32 自動退出）· **唯讀，無任何送單路徑**。
# 09:00 起累積逐筆，12:00 / 13:00 / 13:30 各寄一封前十名，收盤後再寄一封。
# 判準與參數見 scripts/research/biglot_live_watch.py 檔頭；宇宙與門檻在
# ${GOLDENSTOCKS_DATA_DIR}/data/cache/pit_universe_tick/_live_calib_v3.json
# （產生器 scripts/research/build_hivol_futures_universe.py，可重現）。
# 必須從 09:00 起算：全場 IC +0.151，只取 12:00-13:00 會掉到 +0.068 且對全場零增量。
# 獨立 Fubon 行情 websocket，訂閱 45×1 頻道（遠低於 108 撞牆教訓）。
#
# 安裝（Mac mini，一次性，不透過 scripts/install-launchd.sh 的下單層陣列）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.biglot-live-watch.plist
#   sed -e "s#{{BIGLOT_LIVE_WATCH_LAUNCHER}}#$(pwd)/scripts/launchd/biglot-live-watch.command#g" \
#       -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.biglot-live-watch.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"
#
# 卸載：
#   launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.jackm4.goldenstocks.biglot-live-watch.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
WORKER_PY="${ROOT}/scripts/research/biglot_live_watch.py"

mkdir -p "${STATE}/logs/intraday" "${APP_SUPPORT}" "${HOME}/Library/Logs/com.jackm4.goldenstocks"
export TZ="${TZ:-Asia/Taipei}"
export PYTHONPATH="${ROOT}/src"

LOCKDIR="${APP_SUPPORT}/biglot-live-watch.lockdir"
if ! mkdir "${LOCKDIR}" 2>/dev/null; then
  holder="$(cat "${LOCKDIR}/pid" 2>/dev/null || echo "")"
  if [[ -n "${holder}" ]] && kill -0 "${holder}" 2>/dev/null; then
    echo "skip: already holding lock (pid=${holder}, alive)"; exit 0
  fi
  rm -rf "${LOCKDIR}"; mkdir "${LOCKDIR}" 2>/dev/null || { echo "skip: lock race lost"; exit 0; }
fi
echo "$$" > "${LOCKDIR}/pid"
trap 'rm -rf "${LOCKDIR}" 2>/dev/null || true' EXIT

_load_env_file() {
  [[ -r "$1" ]] || return 0
  set +e; set -a; source "$1" 2>/dev/null; local rc=$?; set +a; set -e
  [[ "${rc}" -ne 0 ]] && echo "WARN: cannot source $2 rc=${rc}"
  return 0
}
_load_env_file "${APP_SUPPORT}/order.env" "order.env"
_load_env_file "${STATE}/.env" "project .env"

if [[ "${RUN_BIGLOT_LIVE_WATCH:-1}" == "0" ]]; then
  echo "biglot-live-watch skipped: RUN_BIGLOT_LIVE_WATCH=0"; exit 0
fi

# 2026-09-05：訂閱宇宙改用 v3（期交所官方契約對照 + 還原後波動排序）。
# 舊 _live_calib.json 的 vol20 混了兩種量、且對 8 檔標的用到已萎縮的舊期貨契約。
# 檔案不存在時 biglot_live_watch.py 會自動回退舊檔（fail-safe，不擋收集）。
export BIGLOT_CALIB="${STATE}/data/cache/pit_universe_tick/_live_calib_v3.json"

PYTHON="${ROOT}/.venv-fubon/bin/python"
if [[ ! -x "${PYTHON}" ]]; then echo "✗ missing .venv-fubon python: ${PYTHON}"; exit 1; fi
ROTATING_TEE="${ROOT}/scripts/launchd/rotating_tee.py"
LOG_PREFIX="${STATE}/logs/intraday/biglot_live_watch"

# 個股期貨即時價 poller(背景,與現股 collector 同一 job):寫 futprice_{date}.json 供
# 儀表板「期貨」欄。REST 輪詢、唯讀、獨立錯誤處理;13:45 自退。失敗不影響現股收集。
FUTPRICE_PY="${ROOT}/scripts/research/collect_biglot_futprice.py"
if [[ -f "${FUTPRICE_PY}" ]]; then
  ( PYTHONPATH="${ROOT}/src" "${PYTHON}" "${FUTPRICE_PY}" \
      >> "${STATE}/logs/intraday/biglot_futprice_$(date +%Y%m%d).log" 2>&1 ) &
  echo "futprice poller started (pid $!)"
fi

# 權證多空 poller(背景,同一 job):TWSE MIS 批次輪詢該宇宙底下全部權證,寫 warrantflow_{date}.json
# 供儀表板「權證多空」欄。SDK 只在啟動時列權證清單一次即 logout,不佔盤中富邦額度;13:35 自退。
# 2026-09-23 v2:改走富邦 REST 活躍子集(前一日成交額前 1000 檔,8 req/s),**不再碰 TWSE MIS**
# (v1 全掃 7.5k 檔曾把 MIS IP 封鎖一小時、打掛生產 collect_watchlist_books)。RUN_WARRANT_FLOW=0 可停。
WARRANT_PY="${ROOT}/scripts/research/collect_warrant_flow.py"
if [[ "${RUN_WARRANT_FLOW:-1}" == "1" && -f "${WARRANT_PY}" ]]; then
  ( PYTHONPATH="${ROOT}/src" "${PYTHON}" "${WARRANT_PY}" \
      >> "${STATE}/logs/intraday/biglot_warrant_$(date +%Y%m%d).log" 2>&1 ) &
  echo "warrant flow poller started (pid $!)"
fi

# 現股五檔 ws 收集(36 檔 books,一條連線)+ z 急殺回彈影子帳(讀 raw 逐筆,不顯示不送單)。
# 兩者唯讀、各自錯誤處理、13:35 自退;供「被動掛單」回測與 20 日 OOS 累積(2026-09-23)。
STOCK_BOOKS_PY="${ROOT}/scripts/research/collect_stock_books_ws.py"
if [[ -f "${STOCK_BOOKS_PY}" ]]; then
  ( PYTHONPATH="${ROOT}/src" "${PYTHON}" "${STOCK_BOOKS_PY}" \
      >> "${STATE}/logs/intraday/stock_books_ws_$(date +%Y%m%d).log" 2>&1 ) &
  echo "stock books ws started (pid $!)"
fi
ZSHADOW_PY="${ROOT}/scripts/research/zcrash_shadow.py"
if [[ -f "${ZSHADOW_PY}" ]]; then
  ( PYTHONPATH="${ROOT}/src" "${ROOT}/.venv/bin/python" "${ZSHADOW_PY}" \
      >> "${STATE}/logs/intraday/zcrash_shadow_$(date +%Y%m%d).log" 2>&1 ) &
  echo "zcrash shadow started (pid $!)"
fi

exec "${PYTHON}" "${WORKER_PY}" 2>&1 | "${PYTHON}" "${ROTATING_TEE}" "${LOG_PREFIX}"
