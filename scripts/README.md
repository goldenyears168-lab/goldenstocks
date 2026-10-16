# scripts/ops — 每日營運入口

排程與收盤鏈會呼叫的腳本。回測／sweep 見 [`../research/README.md`](../research/README.md)。

**Pipeline SSOT**：[`config/pipeline_scripts.yaml`](../config/pipeline_scripts.yaml) · [`docs/research-script-inventory.md`](../docs/research-script-inventory.md)

## Shell / 排程

| 檔案 | 用途 |
|------|------|
| `daily_sync.sh` | ② 收盤持股雷達主鏈 |
| `weekly_sync.sh` | 週日深度補庫（**已退役排程** · 僅手動） |
| `backfill_market_data.sh` | 歷史行情補庫 |
| `install-launchd.sh` | launchd 安裝（14 支 job · 清單見下） |
| `install-etfedge-import-launchd.sh` | ETFEdge import |
| `launchd/*.command` | launchd 包裝 |
| `*notify*.sh`, `job_notify.sh` | 排程郵件通知 |

### 現行 launchd job（`install-launchd.sh` LABELS · 僅 mini live）

> **Live 排程 SSOT**：[config/job_registry.yaml](../../config/job_registry.yaml)（時間／開關以該文為準）。  
> **心智模型（2026-07-23 公告）**：三時段 — **18:30 夜盤準備** · **08:50 開盤前** · **盤中**；另加 **20:00–20:40 夜間觀測波**（當日結論／上牆）。詳見站上 Inbox／知識庫「排程重整公告」。

#### 18:30 · 夜盤準備
- `branch-tape-prewarm` — 分點 tape 補檔（POOLS∪持倉）

#### 20:00–20:40 · 夜間觀測波（當日結論）
- `winbond-expert-pool-watch` 20:00 · `expert-pool-chart-digest` 20:05 · `holdings-branch-sell-monitor` 20:10 · `second-disp-expert-pool-watch` 20:35 · `ops-console-evening-sync` **20:40**
- **郵件政策（2026-10）**：夜間觀測波各 job 各自寄信（不再收斂成 20:40 一封摘要；該摘要讀自 Supabase，已移除）。重大／Detach RED 仍即時寄。

#### 08:50–09:05 · 開盤前
- `crash-thermometer-daily` 09:00（多半昨收、**只算不寄**）· Order 窗開啟

#### 盤中 · Order／觀測
- live：`leading-dip-poll` · `songshan-copytrade-poll` · `expert-pool-staged-gate`（`rrg-c18acc-poll` 2026-08-04 退役）
- 觀測：`detach-gate`（RED 仍即時寄）· `buy/sell-signal-radar`（不下單）· `ops-live-ta-poll`
- 另有 `order-wake`（`install-order-launchd.sh` · 防睡眠）

夜間對照：`winbond-expert-pool-watch`＝20:00 買方共識；`holdings-branch-sell-monitor`＝20:10 富邦持倉×專家淨賣＋跨池面板 K/N≥5000萬；`ops-console-evening-sync`＝20:40 本地資料刷新（分點 tape／溫度計面板／個股日線／Stage 熱力圖）。

溫度計時序：09:00 多半**昨收、不寄信**；**當日讀數＋郵件以 20:40 為準**。

## Python · daily

| 檔案 | Strategy ID |
|------|-------------|
| `run_market_breadth_report.py` | Breadth zone HTML（研究 · 非 digest） |
| `run_vcp_funnel_specs_daily_brief.py` | `vcp-pivot-gate` · `vcp-coil-close` |
| `run_copytrade_l1h9_daily_brief.py` | `00981a-l1h9` · 收盤訊號篩選 |
| `backfill_vcp_funnel_screen.py` | DB backfill |
| `run_rrg_mono_daily_brief.py` | `rrg-mono-hold7` |
| `import_etfedge_holdings.py` | ETFEdge 持股 |
| `notify_job_result.py` | 通知 helper |

## macOS `.command`

`1630收盤雷達.command` · `2000週日補庫.command`

## Private ops console（已移除 · 2026-10）

好時量化 ops 後台（`haoshi-quant-ops.pages.dev` · Supabase `ops.*`）已整條移除：
`ops_console_sync` / `ops_console_snapshots` / `ops_live_ta` / `ops_holdings_stability` /
`ops_stock_heatmap`、`scripts/order/*ops*`、`send_ops_evening_summary`，以及 `notify_email`／
`notify_job_result` 的 `ops.digests` 並行寫入。`ops-live-ta-poll`、`live-ta-kbar-sync` 早已退役。
`ops-console-evening-sync` 仍每日 20:40 跑，但只剩**本地**資料刷新（by-trader 全市場分點 tape、
溫度計面板、個股日線、Stage 熱力圖），不再推任何外部服務。
