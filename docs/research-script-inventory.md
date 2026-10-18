# Research script inventory · 腳本盤點表

> ⚠ 2026-10-09：本表停在 2026-07-09，已與現況脫節（scripts/research 已清理）；以 `config/job_registry.yaml`、`config/pipeline_scripts.yaml` 為準。

| Field | Value |
|-------|-------|
| Version | 2026-07-09 · Phase E |
| SSOT | `config/research.yaml` · `config/strategy.yaml` · `config/pipeline_scripts.yaml` |
| Graduation gates | `config/research.yaml` → `graduation_gates` |
| Archived runners | `scripts/research/archive/`（160 支 · 含 graduated 重跑） |

## 分類定義

| 分類 | 說明 | 登錄位置 |
|------|------|----------|
| **strategy** | 已採納策略 · 回測或 production screen | `config/strategy.yaml` |
| **research** | 探索 topic · sweep · 假說 | `config/research.yaml` → `topics.*` |
| **research/archive** | Phase B 封存 runner · 手動重跑 | `scripts/research/archive/` + yaml `run_scripts` |
| **pipeline** | daily_sync / launchd · 非 alpha | `config/pipeline_scripts.yaml` |
| **delete** | 無 SSOT · 無下游依賴 · 已移除 | — |

---

## Pipeline / infra（9 · 已登錄）

| Script | 分類 | 觸發 |
|--------|------|------|
| `run_rrg_universe_close.py` | pipeline | daily_sync · `RUN_RRG_UNIVERSE_CLOSE` |
| `run_vcp_funnel_close.py` | pipeline | daily_sync · `RUN_VCP_FUNNEL_CLOSE` |
| `run_vcp_funnel_intraday.py` | pipeline | launchd 13:00 |
| `run_stock_daily_lens.py` | pipeline | daily_sync · `RUN_STOCK_DAILY_LENS` |
| `run_copytrade_l1h9_daily_brief.py` | pipeline | daily_sync · `copytrade_l1h9_screen` |
| `run_rrg_mono_intraday_watch.py` | pipeline | launchd 13:00 |
| `run_mutual_fund_disclosure_watch.py` | pipeline | launchd |
| `run_sync_mutual_fund_holdings.py` | pipeline | manual |
| `run_market_breadth_report.py` | regime | manual · breadth HTML |

---

## 原 36 orphan · 處置摘要

| Script | 處置 | 理由 |
|--------|------|------|
| `run_rrg_universe_close.py` | **pipeline** | daily_close 節點 |
| `run_vcp_funnel_close.py` | **pipeline** | VCP close screen |
| `run_vcp_funnel_intraday.py` | **pipeline** | VCP intraday screen |
| `run_stock_daily_lens.py` | **pipeline** | Lens → Supabase |
| `run_copytrade_l1h9_daily_brief.py` | **pipeline** | L1H9 daily brief wrapper |
| `run_rrg_mono_intraday_watch.py` | **pipeline** | hold7 盤中預警 |
| `run_mutual_fund_disclosure_watch.py` | **pipeline** | ACDD04 披露監控 |
| `run_sync_mutual_fund_holdings.py` | **pipeline** | 主動基金持股 sync |
| `run_market_breadth_report.py` | **regime** | 廣度 HTML 工具 |
| `run_s04_freq_compare.py` | **research** | → `factor-validation-s04` |
| `run_s04_monthly_tri_compare.py` | **research** | → `factor-validation-s04` |
| `run_c18_bavg_*` · `run_c18_dl*` · `run_c18_mom` · `run_c18acc_*` (8) | **delete** | C18acc 已 graduated · 一次性 sweep |
| `run_c13_*` (2) | **delete** | score-swap 子 sweep · 已採納 C18acc |
| `run_rrg_mono_c0_vs_a_proof.py` | **delete** | C0 證明已完成 |
| `run_rrg_mono_c4_validation.py` | **delete** | 近窗驗證已完成 |
| `run_rrg_mono_empty_fresh_fallback.py` | **delete** | hold7 子研究 · 未採納 |
| `run_rrg_mono_execution_timing_backtest.py` | **delete** | 時點對照 · 未採納 |
| `run_rrg_mono_volume_tier_study.py` | **delete** | 成交量分層 · 未採納 |
| `run_vcp_breadth_zone_backtest.py` | **delete** | VCP×廣度 · 一次性 |
| `run_vcp_expert_entry_sweep.py` | **delete** | VCP 1m 進場 sweep · 重跑時重建 |
| `run_chunge_l4_calibration.py` | **delete** | L4 校準 · 已 archive |
| `run_pullback_regime_backtest.py` | **delete** |  abandoned · 無 topic |
| `run_inst_flow_backtest.py` | **delete** | abandoned · 無 topic |
| `run_acdd04_copytrade_backtest.py` | **delete** | 主動基金實驗 · 無 topic |
| `run_rrg_rotation_backtest.py` | **delete** | 月頻 rotation 演示 · `rrg_rotation` 模組保留 |

---

## 已刪除模組（連帶）

| 模組 | 理由 |
|------|------|
| `src/research/backtest/pullback_regime_backtest.py` | 無 topic · 無採納 |
| `src/research/backtest/inst_flow_backtest.py` | 無 topic |
| `src/research/backtest/inst_flow_981a_overlap.py` | 僅 inst_flow 用 |
| `src/research/backtest/mutual_fund_copytrade.py` | ACDD04 實驗 |
| `src/research/backtest/rrg_mono_volume_tier.py` | 僅 volume tier study |

---

## Phase B · archived runners（2026-07-09）

| 項目 | 說明 |
|------|------|
| 目錄 | `scripts/research/archive/` |
| 觸發 | **非** daily_sync 主線 · 手動或 sweep CLI |
| yaml | `config/research.yaml` · `status: archived` 的 `run_scripts` 已指向 archive 路徑 |
| pipeline 例外 | `RUN_MARKET_PROBE=1` 時 daily_sync 仍呼叫 archive 內 `run_market_probe_radar.py` / `backfill_probe_kbar.py` |
| 保留 | `src/research/backtest/` 根目錄 **48 模組** · production import 鏈 |
| Archived engines | `src/research/backtest/archive/`（**86 模組** · sweep-only） |

Active / graduated runner 仍在 `scripts/` 根目錄（**25 支** · pipeline + strategy daily only）。

---

## Phase E · scripts 降噪（2026-07-09）

| 目錄 | 內容 | 觸發 |
|------|------|------|
| `scripts/tools/` | backfill · analyze · calibrate · site sync · 19 支 | **手動** · 見 `scripts/tools/README.md` |
| `scripts/research/archive/render_*.py` | RRG/C18acc showcase HTML · teaching deck · 7 支 | **手動** · 非 daily_sync |
| `scripts/` 根目錄保留 | `run_*`（25）· pipeline infra（`backfill_c18acc_kbar` · `sync_*` · launchd） | daily_sync / launchd |

**Strategy 配置一致性**：

| 項目 | 狀態 |
|------|------|
| `rrg-mono-hold7` | `enabled: false` · legacy baseline · C18acc 取代 live slots · `run_rrg_mono_daily_brief.py` 仍可手動重跑 |
| `abc-v3-f1-pullback` | strategy `enabled: false` · Order `ABC_V3_F1_ORDER_ENABLED=1` 預設開 · SSOT 見 `docs/order-layer-prd.md` §6.2 |

**未動**：`src/research/backtest/` 根目錄 48 模組（含 unified_comparison · finmind_* · market_probe_* · 工具型引擎 · import 面大 · 暫不切 `backtest/tools/`）。

---

## Phase D · research 靜默（2026-07-09）

| 項目 | 說明 |
|------|------|
| Active topics | **0** · dual-sleeve rejected · snapshot / finmind / audit archived |
| `scripts/run_*.py` 根目錄 | **25 支** · 僅 pipeline + strategy daily |
| `scripts/research/archive/` | +62 runner（ABC/C18acc sweep · finmind · showcase HTML） |
| Production 例外 | `run_c18acc_extension_screen.py` 仍在根目錄（launchd + sell-radar） |
| yaml version | `research-v5` |

Graduated champion 重跑：`scripts/research/archive/run_triple_wma_pullback_sweep.py` 等 · 路徑見 yaml `run_scripts`。

Archived backtest 測試：`tests/research/archive/`（56 支）· production 測試見 `tests/` 根目錄。

---

## Phase D 之後 · 首個新開 active topic（2026-07-09）

| 項目 | 說明 |
|------|------|
| Topic | `abc-v3-f1-entry-structure`（`config/research.yaml`） |
| 假說 | MV gap sweet-band（H-ENTRY-GAP-1 · passed）· W3 RV 谷底反彈（H-ENTRY-RV3-1 · rejected）· entry gate×TP-only 聯合（H-ENTRY-TP-1 · rejected）· 多因子疊加（H-ENTRY-MF-1 · mixed）· 個股自我標準化（H-ENTRY-NORM-1 · mixed）· W3 Improving 延伸驗證（H-ENTRY-IMP-1 · pending · RP-1） |
| Runner | `run_abc_v3_f1_entry_structure_sweep.py` · `run_abc_v3_f1_entry_gate_tp_sweep.py` · `run_abc_v3_f1_entry_multifactor_tp_sweep.py` · `run_abc_v3_f1_entry_factor_scan.py` · `run_abc_v3_f1_w3_improving_extended_window.py`（皆 `scripts/research/`，非 archive） |
| Engine | 對應 `src/research/backtest/abc_v3_f1_entry_{structure_sweep,gate_tp_sweep,multifactor_tp_sweep,factor_scan}.py` · RP-1 runner 重用 multifactor 模組 |
| Parent | `abc-v3-f1-pullback`（graduated · `triple-wma-pullback` topic） |
| Split spec | `intraday_is_oos_70_30`（`config/research_splits.yaml`） |
| Graduation | `G1_preregistered_hypothesis: passed` · 其餘 pending，validation phase（RP-1） |
| 結論 | 10% portfolio 均報酬（hold_5d）未達成；gap sweet-band 有效；rebound 假說被否證；W3 improving 待 RP-1 延伸窗口驗證（`20260710_rp1_w3_improving_extended_window.md`） |

---

## 資券籌碼日頻 null result（2026-08-20）

| 項目 | 說明 |
|------|------|
| Topic | `chip-signal-daily-horizon`（`config/research.yaml` · **status: rejected**） |
| 假說 | 十分位多空日頻預測力（H-CHIP-D1-SPREAD · rejected）· Δ融資是當日報酬代理（H-CHIP-D1-MARGIN-PROXY · **rejected —— 反而證明它是真訊號**）· 扣成本後為正（H-CHIP-D1-NET · rejected）· 訊號一致提高可靠度（H-CHIP-D1-CONSENSUS · rejected） |
| Runner | `chip_daily_horizon_null_test.py`（`scripts/research/`，非 archive）· `--test spread\|control\|score\|all` · `--cases YYYY-MM-DD` 列出反向案例 |
| 資料 | `stock_sbl_fee_daily`（本日新建 · t13sa710）· `stock_short_interest_daily`（TWT93U）· `stock_margin_daily`（含新來源 twse_mi_margn 全市場） |
| 樣本 | 155,223 個「個股-日」· 889 檔 · 234 個交易日 · 2025-09-01~2026-08-19 |
| Parent | `sbl-fee-cross-section`（active · 月頻橫斷面；本 topic 是它的護欄不是否證） |
| 結論 | 訊號傾斜 0.130%/日為真但個股離散 3.13% 是它的 **24 倍**；換手成本 1.17%/日 是毛利的 **9 倍**；一致偏空的股票隔日仍有 **44.1%** 上漲。同一個 CDM DOUT 訊號月頻文獻值 t=−3.32、日頻實測 t=+0.38——**訊號存在與否是尺度相依的** |
| 已知陷阱 | ① `stock_margin_daily` 與 `stock_daytrade_daily` 自 2026-06 起有雙 source，merge 前必須去重（未去重會讓「當日報酬隔日效果」從真值 t=0.38 變假的 t=3.88）② 評分把「當日無借券成交」算偏多，屬事後決定、偏多側數字要打折 |

---

## Phase C · pipeline 簡化（2026-07-09）

| 項目 | 變更 |
|------|------|
| `RUN_RRG_IMPROVING_WATCH` | 預設 **0**（`.env.example` · `daily_sync.sh` · `pipeline_gates.py`） |
| `RUN_MARKET_PROBE_RADAR` / `RUN_PROBE_KBAR_BACKFILL` | 預設 **0** |
| `config/buy_observation.yaml` | RRG Improving 池 `enabled: false`（定義保留 · radar 不算） |
| orphan runner | 登錄 `run_scripts_followup` / `compare_scripts` · 22 支移入 archive |
| 剩餘 orphan | 3 支 infra（`run_daily_sync` · `run_launchd_replay` · `run_signal_radar_replay`）→ `pipeline_scripts.yaml` |

手動重開 improving：`RUN_RRG_IMPROVING_WATCH=1` + 將 buy_observation 池 `enabled: true`。

---

## 驗證

```bash
cd "<project-root>"
PYTHONPATH=src python3 -m pytest tests/test_research_config.py -q
PYTHONPATH=src python3 <<'PY'
import glob, yaml
from pathlib import Path
with open("config/research.yaml") as f: r = yaml.safe_load(f)
with open("config/strategy.yaml") as f: s = yaml.safe_load(f)
with open("config/pipeline_scripts.yaml") as f: p = yaml.safe_load(f)
reg = set()
for t in r["topics"].values():
    for x in t.get("run_scripts") or []: reg.add(str(x))
    for x in t.get("run_scripts_followup") or []: reg.add(str(x))
for st in s["strategies"].values():
    for k in ("run_script","screen_script"):
        if st.get(k): reg.add(st[k])
for sc in p["scripts"].values(): reg.add(sc["path"])
# archived topic scripts live under archive/
archived_paths = {
    x for t in r["topics"].values() if t.get("status") == "archived"
    for x in (t.get("run_scripts") or [])
}
missing = sorted(p for p in archived_paths if not Path(p).is_file())
print("missing archived scripts:", missing or "none")
root_runs = set(glob.glob("scripts/run_*.py"))
reg_names = {Path(x).name for x in reg if not x.startswith("scripts/research/archive/")}
orphans = sorted(Path(x).name for x in root_runs if Path(x).name not in reg_names)
print("root orphans:", len(orphans), orphans[:20] if orphans else "none")
PY
```

---

## Sweep runner（trial registry）

| 模組 | 用途 |
|------|------|
| `src/research/sweep_runner.py` | topic → family → trial → run |
| `scripts/run_research_sweep.py` | CLI 模板 |
| `config/sweep_trial_registry.example.yaml` | JSON schema |

```bash
PYTHONPATH=src .venv/bin/python scripts/run_research_sweep.py \\
  --topic c18acc-snapshot-1300 --family alpha-sweep --dry-run
```

---

## 統一回測比較層

| 模組 | 用途 |
|------|------|
| `config/backtest_standard.yaml` | 五軌 adapter · 窗口 · 成本 |
| `scripts/run_unified_backtest_comparison.py` | league table |

```bash
PYTHONPATH=src .venv/bin/python scripts/run_unified_backtest_comparison.py
```

---

## 2026-10-16 證偽封存批次（12 支 → `scripts/research/archive/`）

**判準**（三條皆須成立；N = 4 週，截止 2026-09-18）：
1. `config/research.yaml` topic `status`/`phase` 為 rejected，且其**所有** hypotheses 皆 `rejected`（含 `pending`／`passed`／`mixed` 的 topic 不動）。
2. 除同批腳本與 `config/research.yaml` 外，無其他 code／launchd／tests／docs 引用；被保留腳本 import 者不動。
3. 截止日前無修改（淺層 clone 補抓至 2026-08-14，未見修改者視為 ≥ 8 週未動）。

搬移時同步：`parents[2]` → `parents[3]`（archive 慣例）、`config/research.yaml` 的 `run_scripts` 路徑改指 archive。腳本**未刪**，重跑方式見 `archive/README.md`。
被證偽的是**訊號本身**——看多與看空皆不得援引。

| Topic | 搬入 archive 的腳本 | 一行結論 |
|-------|--------------------|----------|
| `tdcc-big-holder-forward` | `tdcc_big_holder_forward_test` · `cache_holding_shares_futures_universe` | 集保大戶比週變化預測前瞻報酬：H-TDCC-1／2 皆 rejected |
| `chip-street-canon` | `street_canon_a_conc_momentum` · `_b_cost_line` · `_c_midtier` | 三線 14 格全滅；先驗最高的 C 線（中實戶）t 最高僅 0.546 |
| `foreign-branch-copytrade-full-sweep` | `run_foreign_branch_copytrade_compare` · `_robustness` · `run_foreign_branch_2330_decomposition` | 外資分點「α」＝權值股 beta；條件式優勢 < 45bps 往返成本，非可成交性問題 |
| `umc-branch-overnight-decision` | `umc_branch_overnight_signal_sweep` · `umc_branch_market_calibrated_sweep` · `branch_overnight_crosssection_sweep` · `branch_netdisp_multihorizon_validation` | H1–H6 全否；分點聚合訊號隔夜幅度 0.1~0.3% ≈ 成本同量級 |

**刻意留在原處**（符合 rejected 但有依賴）：`umc_night_forward_recorder.py`（launchd 常駐）；`scan_quiet_accumulators.py`／`study_whale_9A81_forward_tradeable.py`（被 `whale-branch-position-momentum-2m` 的 9217／9661 腳本 import，該 topic 含 `passed`）。
