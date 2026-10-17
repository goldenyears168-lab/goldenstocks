#!/usr/bin/env bash
# launchd：ETF 持股收集（每天 18:00，含週末）
# 抓 5 個發行商（EZMoney/KGI/群益/野村/元大）→ holdings changes → ETF 日報 → 跟單 L1H9。
# daily-sync（週一至五 16:35）以 --skip-holdings-fetch 略過這幾步，持股只在這裡收一次。
# 僅 mini 安裝；Book 不裝 live launchd。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export ROOT
DATA_DIR="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
STAMP="$(date '+%Y%m%d')"
LAUNCHD_LOG="${DATA_DIR}/logs/launchd_etf-holdings-sync.log"
RUN_LOG="${DATA_DIR}/logs/daily_sync_${STAMP}.log"

mkdir -p "${DATA_DIR}/logs"
exec >>"${LAUNCHD_LOG}" 2>&1
echo ""
echo "=== launchd etf-holdings-sync 開始 $(date '+%Y-%m-%d %H:%M:%S') ==="

if [[ -r "${DATA_DIR}/.env" ]]; then
  set +e
  set -a
  # shellcheck disable=SC1090
  source "${DATA_DIR}/.env" 2>/dev/null
  set +a
  set -e
fi

set +e
"${ROOT}/scripts/daily_sync.sh" --quiet --etf-holdings-only
EXIT=$?
set -e

if [[ -x "${ROOT}/scripts/job_notify.sh" ]]; then
  "${ROOT}/scripts/job_notify.sh" "ETF 持股收集" "${EXIT}" "${RUN_LOG}" RUN_ETF_HOLDINGS_SYNC_EMAIL \
    "reports/daily/etf-daily/daily_brief.md" || true
fi

echo "=== launchd etf-holdings-sync 結束 exit=${EXIT} $(date '+%Y-%m-%d %H:%M:%S') ==="
exit "${EXIT}"
