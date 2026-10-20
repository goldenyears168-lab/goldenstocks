#!/usr/bin/env bash
# 撤單神器 · 常駐 worker（只撤單、不下單）。預設全關：開關檔 data/order/cancel_guard_switch.json enabled=false 時只維持心跳。
# 實彈三道鎖：開關檔 auto_cancel=true、.env ORDER_CANCEL_GUARD_DRY_RUN=0、.env ORDER_MASTER_ENABLED=1（皆預設不開）。
# 讀 futopt-fill-listener 與各 *_books 收集器已落地的檔案，不新增任何富邦 ws 連線；只有真的要撤單時才登入 REST。
#
# 安裝（Mac mini，一次性，不透過 install-launchd.sh 的 LABELS 陣列；2026-10-21 尚未安裝）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.cancel-guard.plist
#   sed -e "s#{{CANCEL_GUARD_LAUNCHER}}#$(pwd)/scripts/launchd/cancel-guard.command#g" -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.cancel-guard.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"
# 卸載： launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.jackm4.goldenstocks.cancel-guard.plist
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
set +e; set -a; [[ -r "${STATE}/.env" ]] && source "${STATE}/.env" 2>/dev/null; set +a; set -e
export ORDER_CANCEL_GUARD_DRY_RUN="${ORDER_CANCEL_GUARD_DRY_RUN:-1}"
export TZ="${TZ:-Asia/Taipei}" PYTHONPATH="${ROOT}/src"
exec "${ROOT}/.venv-fubon/bin/python" -u "${ROOT}/scripts/order/run_cancel_guard.py"
