#!/usr/bin/env bash
# 融資/借券/當沖補追同步 · 每平日 21:10 · 2026-10-17 起改官方來源(TWSE/櫃買),不再用 FinMind;寫入 stock_margin_daily 等。
# 根因背景：主線 daily-sync 排在 weekdays 16:35，比 TWSE 當日融資餘額發布時間還早
# （實測 2026-09-17／09-18 兩天都是：16:35 那次抓不到當天，隔天早上查 FinMind 源頭
# 已經有資料，本機 DB 卻卡在 T-1 沒追上，得手動補），造成資料永遠慢一天。
# 本 job 在收盤+6h 左右再补跑一次同一支同步腳本（增量 upsert 冪等，不會重複算 diff），
# 順便帶 --extra-stock-ids-file 把高波動宇宙（不一定是任何 ETF 成分股，主線的 ETF
# 持股聯集本來就抓不到）一起補上。
#
# 安裝（Mac mini，standalone，比照 vixtwn-daily-sync 不進 install-launchd.sh LABELS）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.chip-margin-catchup.plist
#   sed -e "s#{{CHIP_MARGIN_CATCHUP_LAUNCHER}}#$(pwd)/scripts/launchd/chip-margin-catchup.command#g" \
#       -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.chip-margin-catchup.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
mkdir -p "${STATE}/logs" "${APP_SUPPORT}"
export TZ="${TZ:-Asia/Taipei}"
export PYTHONPATH="${ROOT}/src"

LOCKDIR="${APP_SUPPORT}/chip-margin-catchup.lockdir"
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

if [[ "${RUN_CHIP_MARGIN_CATCHUP:-1}" == "0" ]]; then
  echo "chip-margin-catchup skipped: RUN_CHIP_MARGIN_CATCHUP=0"; exit 0
fi

PYTHON="${ROOT}/.venv/bin/python"
HIVOL_UNIVERSE_FILE="${STATE}/data/cache/pit_universe_tick/_hivol_universe_v3.json"
LOG="${STATE}/logs/chip_margin_catchup_$(date +%Y%m%d).log"
{
  echo "[$(date -Iseconds)] start"
  # 2026-10-17:FinMind 不續約。改用官方來源每日籌碼同步(TWSE/櫃買 融資券、當沖、三大法人;一天一次請求涵蓋全市場,
  # 所以不再需要 --extra-stock-ids-file 補高波動宇宙)。依來源判斷已抓,冪等。
  "${PYTHON}" "${ROOT}/src/sync_official_chip_daily.py" --lookback-days "${CHIP_MARGIN_CATCHUP_LOOKBACK_DAYS:-5}" \
    || echo "WARN: sync_official_chip_daily failed rc=$?"
  # 2026-10-16：/chips 頁的 chip_sop_latest.json 改在此產生。原掛在 branch-tape-prewarm 16:30/18:30 尾端，
  # 比當日官方日 K 入庫早，asof 連兩天（10/14、10/15）落後一天。唯讀 DB · 冪等 · 失敗不影響本 job。
  "${PYTHON}" "${ROOT}/scripts/research/chip_sop.py" \
    || echo "WARN: chip_sop failed rc=$?"
  echo "[$(date -Iseconds)] done"
} >> "${LOG}" 2>&1
