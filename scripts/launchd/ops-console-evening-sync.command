#!/usr/bin/env bash
# launchd / 手動：夜間本地資料刷新（by-trader 全市場分點 tape · 溫度計面板 · 個股日線 · Stage 熱力圖）
# 週一至五 20:40（晚於 20:35 second-disp）。2026-10 起不再推 Supabase ops console。
# 僅 mini 安裝；Book 不裝 live launchd。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STAMP="$(date '+%Y%m%d')"
LAUNCHD_LOG="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}/logs/launchd_ops-console-evening-sync.log"
RUN_LOG="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}/logs/ops_console_evening_sync_${STAMP}.log"

mkdir -p "${GOLDENSTOCKS_DATA_DIR:-${ROOT}}/logs"
exec >>"${LAUNCHD_LOG}" 2>&1
echo ""
echo "=== launchd ops-console-evening-sync 開始 $(date '+%Y-%m-%d %H:%M:%S') ==="

export ROOT="${ROOT}"
export PYTHONPATH="${ROOT}/src"

if [[ -r "${GOLDENSTOCKS_DATA_DIR:-${ROOT}}/.env" ]]; then
  set +e
  set -a
  # shellcheck disable=SC1090
  source "${GOLDENSTOCKS_DATA_DIR:-${ROOT}}/.env" 2>/dev/null
  set +a
  set -e
fi


PY="${ROOT}/.venv/bin/python"
EXIT=0

if [[ ! -x "${PY}" ]]; then
  echo "✗ missing venv python: ${PY}"
  EXIT=1
else
  # 09:00 溫度計常用「昨收」asof。收盤後重算今日讀數（不上個別信；併入夜間一封摘要）。
  set +e
  "${PY}" "${ROOT}/scripts/research/backfill_broker_branch_tape.py" \
    --universe-json "${ROOT}/reports/research/branch-footprint-screen/crash_thermometer_panel_universe.json" \
    --start "$(date -v-5d '+%Y-%m-%d' 2>/dev/null || date -d '5 days ago' '+%Y-%m-%d')" \
    --end "$(date '+%Y-%m-%d')" \
    --force --fetch-workers 2 --delay 0.6 \
    2>&1 | tee -a "${RUN_LOG}"
  "${PY}" "${ROOT}/scripts/research/run_market_crash_thermometer_dashboard.py" \
    2>&1 | tee -a "${RUN_LOG}"
  rc_thermo=${PIPESTATUS[0]}
  set -e
  if [[ "${rc_thermo}" -ne 0 ]]; then
    echo "✗ crash thermometer evening refresh exit=${rc_thermo}"
    EXIT=1
  fi

  # 2026-07-30 新增：個股日線補齊（stock_heatmap watchlist），讓熱力圖讀到當日 EOD。
  # 20:40 已收盤 → FinMind 有當日日線。非致命：失敗只記錄、不中斷後續步驟。
  set +e
  "${PY}" "${ROOT}/src/sync_stock_market_daily.py" \
    --stock-ids 3189,6147,2492,3653,6505 --sync-db --quiet \
    2>&1 | tee -a "${RUN_LOG}"
  rc_daily_bars=${PIPESTATUS[0]}
  set -e
  if [[ "${rc_daily_bars}" -ne 0 ]]; then
    echo "✗ watchlist daily-bars sync exit=${rc_daily_bars}"
    EXIT=1
  fi

  # 2026-07-27 新增：Weinstein Stage 熱力圖（30週當量·日更2日確認引擎），跑完才有
  # 本地產物供研究使用。
  set +e
  "${PY}" "${ROOT}/scripts/research/build_stage_heatmap_1y.py" \
    2>&1 | tee -a "${RUN_LOG}"
  rc_stage_heatmap=${PIPESTATUS[0]}
  set -e
  if [[ "${rc_stage_heatmap}" -ne 0 ]]; then
    echo "✗ stage heatmap evening refresh exit=${rc_stage_heatmap}"
    EXIT=1
  fi

  # 2026-10：已移除「推 Supabase ops console」三步（write_ops_console_snapshot／
  # write_ops_sleeve_status／run_ops_holdings_sync）與夜間摘要信（send_ops_evening_summary，
  # 其內容讀自 Supabase）。**前面各步不可關**——本 job 是目前唯一還在跑的 by-trader 全市場
  # 分點 tape 寫入者（8 席：1650/1560/1480/1360/984K/1261/5850/585c），見 job_registry。
fi

echo "=== launchd ops-console-evening-sync 結束 exit=${EXIT} $(date '+%Y-%m-%d %H:%M:%S') ==="

if [[ "${EXIT}" -ne 0 ]]; then
  export JOB_NOTIFY_EXTRA="${JOB_NOTIFY_EXTRA:-ops console evening sync 失敗，詳見 log}"
  /bin/bash "${ROOT}/scripts/job_notify.sh" \
    "ops console evening sync" "${EXIT}" "${RUN_LOG}" RUN_OPS_CONSOLE_EVENING_EMAIL || true
fi

if [[ "${TERM_PROGRAM:-}" == "Apple_Terminal" ]]; then
  /usr/bin/osascript -e 'tell application "Terminal" to close front window' >/dev/null 2>&1 || true
fi

exit "${EXIT}"
