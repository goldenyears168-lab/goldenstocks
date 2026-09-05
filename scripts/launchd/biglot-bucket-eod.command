#!/usr/bin/env bash
# 收盤後：把當天「大戶未平倉損益 × 等量成交格」落成 CSV。**唯讀研究，無任何送單路徑。**
#
# 事前登記的規格（2026-09-05 鎖定，收滿 25 個交易日前不調參數）：
#   切格   等量成交值格（監測清單自己的成交值，13:25 後不進時鐘），**秒級解析度**
#   大戶   單筆成交金額 >= NT$5,000,000 且可判方向（price>=ask / price<=bid）
#   記帳   每格歸零，加權平均成本，只取未實現
#   執行   台指期（成本 2 bps）
#
# ── 已定案、不再是待驗項目（結構性證據，與樣本無關）──────────────────────────
#   切格解析度：**秒級**。分鐘解析度下早盤單一分鐘的成交值就可能超過 1/N，格子會不均甚至
#     空掉（N=60 分鐘版實測只切出 57~58 格、每格佔比 std 0.37~0.43%）；改秒級後 N=30/60/80
#     都是剛好 N 格、std 只有 0.01~0.05%。上一版分鐘解析度讓 N=30 的 A 規則從 +4.73 虛增
#     到 +7.31 bps/格，是假象。
#   執行方式：**持有版**（訊號觸發後抱到反向訊號才換），不用進出版。N=60 上進出版每日換邊
#     16.5~21.5 次、持有版只有 3~6 次，成本差 20~35 bps/日。
#
# ── 事前登記的待驗組合（2026-09-06 鎖定；25 天後只看這 6 格，其餘是探索、不進結論表。
#    Bonferroni 於 6 個檢定的門檻＝|t| > 2.6）。全部可從下面 CSV 事後重算，不必改收集：
#   N ∈ {30, 60}（N=40 只當內插檢查，不進結論表）
#   A  連續 k=3 格未實現 > 0 → 站那一邊
#   C  連續 k=3 且 **bps**[t] > bps[t-1]
#   E  連續 k=3 且 **bps**[t] > (bps[t-1]+bps[t-2])/2
#   ⚠ 越賺越多必須用 bps（單位成本報酬）判斷，不能用金額 —— 金額被部位大小主導。
#   ⚠ D（bps 單調遞增）已剔除：N=30 持有淨 +51.7、N=60 −17.3，兩個 N 上翻號。
#
#   證偽檢查 若 25 天後 A/C/E 在 N=30 與 N=60 上結論相反 = 參數過擬合，整條線收掉
#
# 安裝（一次性）：
#   PLIST=~/Library/LaunchAgents/com.jackm4.goldenstocks.biglot-bucket-eod.plist
#   sed -e "s#{{BIGLOT_BUCKET_EOD_LAUNCHER}}#$(pwd)/scripts/launchd/biglot-bucket-eod.command#g" \
#       -e "s#{{HOME}}#${HOME}#g" \
#       launchd/com.jackm4.goldenstocks.biglot-bucket-eod.plist.template > "${PLIST}"
#   launchctl bootstrap "gui/$(id -u)" "${PLIST}"

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_SUPPORT="${HOME}/Library/Application Support/com.jackm4.goldenstocks"
STATE="${GOLDENSTOCKS_DATA_DIR:-${ROOT}}"
export TZ="${TZ:-Asia/Taipei}"
export PYTHONPATH="${ROOT}/src"
mkdir -p "${STATE}/logs" "${APP_SUPPORT}"

DAY="${1:-$(date '+%Y-%m-%d')}"
DOW="$(date -j -f '%Y-%m-%d' "${DAY}" '+%u' 2>/dev/null || date '+%u')"
if [[ "${DOW}" -gt 5 ]]; then echo "skip: ${DAY} 非平日"; exit 0; fi

LOCKDIR="${APP_SUPPORT}/biglot-bucket-eod.lockdir"
if ! mkdir "${LOCKDIR}" 2>/dev/null; then echo "skip: lock held"; exit 0; fi
trap 'rm -rf "${LOCKDIR}" 2>/dev/null || true' EXIT

_src() { [[ -r "$1" ]] && { set +e; set -a; source "$1" 2>/dev/null; set +a; set -e; }; return 0; }
_src "${APP_SUPPORT}/order.env"; _src "${STATE}/.env"
if [[ "${RUN_BIGLOT_BUCKET_EOD:-1}" == "0" ]]; then echo "skipped: RUN_BIGLOT_BUCKET_EOD=0"; exit 0; fi

RAW="${STATE}/cache/biglot_live_watch/raw_${DAY}.jsonl"
if [[ ! -s "${RAW}" ]]; then echo "skip: ${RAW} 不存在或為空（當天沒收到逐筆）"; exit 0; fi

PY="${ROOT}/.venv/bin/python"
OUT="${STATE}/data/cache/biglot_bucket"

# 1) 補當天 TX 逐筆（台指期是執行標的；抓不到不擋，CSV 的 txf 欄位會留空）
"${PY}" - "${DAY}" <<'PYEOF' || echo "WARN: TX tick 補檔失敗，continue"
import json, sys
sys.path.insert(0, "src")
from pathlib import Path
from finmind_client import fetch_finmind_json
day = sys.argv[1]
f = Path.home() / "goldenstocks-data/cache/tmf_channel/finmind_tx_tick_by_day" / f"{day}.json"
if f.exists():
    print(f"TX {day} 已有"); raise SystemExit(0)
j = fetch_finmind_json({"dataset": "TaiwanFuturesTick", "data_id": "TX", "start_date": day})
d = j.get("data") or []
f.write_text(json.dumps(d)); print(f"TX {day} {len(d)} 筆")
PYEOF

# 2) 待驗 N=30 / N=60 + 內插檢查 N=40
for N in 30 40 60; do
  "${PY}" "${ROOT}/scripts/research/biglot_bucket_reset.py" \
      --date "${DAY}" --n "${N}" --quiet \
      --append-csv "${OUT}/bucket_reset_n${N}.csv" || echo "WARN: N=${N} 失敗"
done
echo "=== biglot-bucket-eod ${DAY} done $(date '+%H:%M:%S') ==="
