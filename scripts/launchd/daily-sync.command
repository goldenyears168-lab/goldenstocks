#!/usr/bin/env bash
# launchd：完整收盤同步（daily_sync.sh 全量，非 --holdings-only）
# 週一至五 16:35 · 收盤後 FinMind 應已有當日日線/籌碼資料
# 僅 mini 安裝；Book 不裝 live launchd。
#
# 背景：2026-08-10 發現 stock_daily_bars（個股收盤價，dayflip-short/-long
# build_candidates() 依賴）長期只靠 ops-console-evening-sync 裡一個只查5檔
# watchlist的sync_stock_market_daily.py呼叫在補，完整版daily_sync.sh
# （RUN_STOCK_MARKET_SYNC=1才會跑的成分股全量同步）從未掛過launchd自動排程，
# 是CLAUDE.md列的「常用指令」但一直要手動觸發——某天沒人手動跑，個股期貨
# 宇宙251檔裡就有192檔(76%)當天完全沒有收盤價，導致隔日候選誤判成0。
# ETF 持股抓取/changes/ETF 日報/跟單 L1H9 已移到 etf-holdings-sync（每天 18:00），
# 本 job 以 --skip-holdings-fetch 略過（2026-10-08）。
#
# 這支job把它排程化，不再依賴人工記得跑。
#
# 2026-08-10（同日發現第二個缺口）：build_candidates()的ADV流動性濾網讀的
# futures_daily_cache.json只靠週五的dayflip-seat-weekly-refresh更新——對每天
# 都要判斷的策略來說週更新太慢，某天最新資料停在上週五，fail-closed設計會把
# 當天所有候選當成「查無流動性資料」直接排除，即使分點買超訊號明明存在（親眼
# 驗證過：手動補這個快取後，原本顯示0候選的一天實際有12檔真實候選）。
# 一併排進這支daily job，跟stock_daily_bars用同一個16:35時間點更新，不再只靠
# 週五那次。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export ROOT
DATA_DIR="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
STAMP="$(date '+%Y%m%d')"
LAUNCHD_LOG="${DATA_DIR}/logs/launchd_daily-sync.log"
RUN_LOG="${DATA_DIR}/logs/daily_sync_${STAMP}.log"

mkdir -p "${DATA_DIR}/logs"
exec >>"${LAUNCHD_LOG}" 2>&1
echo ""
echo "=== launchd daily-sync 開始 $(date '+%Y-%m-%d %H:%M:%S') ==="

if [[ -r "${DATA_DIR}/.env" ]]; then
  set +e
  set -a
  # shellcheck disable=SC1090
  source "${DATA_DIR}/.env" 2>/dev/null
  set +a
  set -e
fi

set +e
"${ROOT}/scripts/daily_sync.sh" --holdings-report --skip-holdings-fetch
EXIT=$?
set -e

set +e
PYTHONPATH="${ROOT}/src" "${ROOT}/.venv/bin/python" \
  "${ROOT}/scripts/research/refresh_dayflip_futures_daily_cache.py"
REFRESH_EXIT=$?
set -e
if [[ "${REFRESH_EXIT}" -ne 0 ]]; then
  echo "✗ futures_daily_cache refresh exit=${REFRESH_EXIT}"
  EXIT=1
fi

if [[ -x "${ROOT}/scripts/job_notify.sh" ]]; then
  "${ROOT}/scripts/job_notify.sh" "收盤完整同步" "${EXIT}" "${RUN_LOG}" RUN_DAILY_SYNC_EMAIL \
    "reports/daily/regime/daily_brief.md" || true
fi

echo "=== launchd daily-sync 結束 exit=${EXIT} $(date '+%Y-%m-%d %H:%M:%S') ==="
exit "${EXIT}"
