# 資料來源遷移：FinMind → 官方來源（2026-10-16 起）

**決定**：FinMind Sponsor 於 2026-10-15 到期，**不續約、不並行**。帳號降為 Free（每日 600 次），撐不起每日管線。
**防呆**：`finmind_client` 預設停用——沒有明確設 `FINMIND_ENABLED=1` 就不連線（`FinMindDisabled`），日常同步的各步驟會印一行 `SKIP … FinMind 已停用` 後結束。
**原則**：新來源寫入時用誠實的 `source`（`taifex`、`fubon`…），**不覆蓋** FinMind 歷史列；讀取端要同時認得 `finmind` 與新來源，並對同一天去重。

## 一、對照表與進度

| 項目 | 原來源（FinMind 資料集） | 新來源 | 進度 | 備註 |
|---|---|---|---|---|
| 期貨三大法人（TX 等） | `TaiwanFuturesInstitutionalInvestors` | 期交所「三大法人-區分各期貨契約」（`taifex_client`） | **已完成** | 與 FinMind TX 12 個交易日 216 個欄位逐筆相同；跑在收盤同步 screener 區塊 |
| VIXTWN 日線 | `TaiwanOptionVix` | 期交所 `getVixData` 逐日檔（`taifex_client.fetch_vix_ticks`） | **已完成** | OHLC＋筆數與 FinMind 逐日相同；跑在 20:30 `vixtwn-daily-sync`；原始檔歸檔於 `cache/taifex_vix/` |
| VIXTWN 1 分鐘 | `TaiwanOptionVix`（舊版 open/close 在分鐘內顛倒） | 同上，彙整前先依時間排序 | **已完成** | 已補回 2026-08-10～10-16 共 13,442 根；high/low/n_ticks 與 FinMind 1,430／1,430 一致 |
| TMF 引擎 VIX lag1 偏向 | `market_vix_daily` 的 `computed`（TXO 自算，**2026-10-01 起停更**） | 官方逐日收盤延伸 | **程式已改、尚未載入實彈** | 只延伸 `computed` 之後的日期；需 `scripts/order/tmf_cutover.sh` 才生效 |
| 分點買賣（專家池＋持倉監控） | `TaiwanStockTradingDailyReport` | 富邦行情站 `fubon-ebrokerdj`：個股前 15 名（精確張數）＋專家核心分點（依分點頁，張數／金額排名），`source='fubon'`（`fubon_branch_tape`） | **已完成** | 專家池配對召回 96.7%；持倉賣方口徑淨賣 ≥5,000 萬 95.4%、≥1 億 99.0%；10/16 已補 3,012 列（67 檔、434 分點）。每檔股票只有前段分點，不是全部 ~800 個 |
| 專家池/持倉監控的收盤價 | `stock_daily_bars` 的 `source='finmind'` | 官方日線（`twse_mi_index`／`tpex_daily`），補檔時 `official_bars.ensure_official_bars` 先確保當日資料 | **已完成** | 原本官方日線 22:45 才由 limitup-fade 順手回補，比 20:05／20:10 還晚 |
| 個股三大法人買賣超 | `TaiwanStockInstitutionalInvestorsBuySell` | TWSE T86＋櫃買 insti（`sync_official_chip_daily`，`source='twse_t86'`／`'tpex_insti'`） | **已完成** | 21:10 `chip-margin-catchup` 每日跑；外資／投信與 FinMind 92／94 一致。**同時修正舊回補腳本的櫃買欄位對應錯誤**（舊版把投信寫成 0），2026 年已覆蓋修復 |
| 融資融券（上市＋上櫃） | `TaiwanStockMarginPurchaseShortSale` | TWSE MI_MARGN（`twse_mi_margn`）＋櫃買 margin/balance（`tpex_margn`） | **已完成** | 與 FinMind 融資餘額 126／126 一致；儀表板波動分數已恢復（42／42） |
| 當沖（上市） | `TaiwanStockDayTrading` | TWSE TWTB4U（`twse_twtb4u`） | **已完成（僅上市）** | **上櫃當沖尚無可用端點**：儀表板 42 檔中 11 檔上櫃無當沖資料 |
| 借券餘額 | — | TWSE TWT72U | 本來就官方 | 已在收盤管線 |
| 外資持股比 | `TaiwanStockShareholding` | TWSE MI_QFIIS（`twse_qfiis`）＋櫃買 qfii（`tpex_qfiis`） | **已完成** | 儀表板 42 檔與 FinMind 40／40 一致；已補 8/20～10/16 |
| 個股市值 | `TaiwanStockMarketValue` | 日線收盤價 × 發行股數（`stock_daily_bars.shares_outstanding`） | 待做 | |
| 月營收、財報、PER、股利 | `TaiwanStockMonthRevenue` 等 | MOPS、TWSE（BWIBBU_d、TWT48U） | 待做 | 每週日跑，風險較低 |
| 鉅額交易 | `TaiwanStockBlockTrade` | TWSE BFIAUU（`twse_block`）＋櫃買 openapi（`tpex_block`） | **已完成** | 9/24 對帳 24／24 一致；已補 9/25～10/16 |
| TX gap／技術風險 | `TaiwanFuturesDaily` | 期交所期貨每日交易行情 | 待做 | `taifex_client.fetch_market_oi_total` 已能解析同一頁 |
| 早晨期貨快照 | `taiwan_futures_snapshot`（即時） | 富邦行情／自家 ws | 待做 | **快照無歷史，停用期間的缺口補不回** |
| 每日 TX 逐筆（biglot-bucket-eod） | `TaiwanFuturesTick` | 自家 ws（`cache/txf_trades`）或期交所 30 日滾動歸檔（`taifex-tick-accumulate`） | 待做 | 目前「抓不到不擋」，欄位會留空 |
| 全分點日報（chip-loud-accum-track） | `TaiwanStockTradingDailyReport`（每日約 950 檔） | **無替代** | 建議退役 | 富邦只有前 15 名，買賣家數差無法重建 |
| 隔日沖席位週更（dayflip-seat-weekly-refresh） | 全分點歷史 | **無替代** | 建議退役 | 研究線已降級 rejected |
| 分點集中度欄（8771 個股詳情頁） | 全分點 tape（要求 ≥100 個分點） | 重新定義（富邦個股頁的合計買／賣超） | 待做 | 口徑會變，需重新對帳 |

## 二、不每天收就補不回來的來源（必須持續歸檔）

| 來源 | 保留期 | 現況 |
|---|---|---|
| 期交所期貨逐筆（官方） | 最近 30 個交易日 | `taifex-tick-accumulate` 每日歸檔 |
| 期交所 VIX 逐日檔／月壓縮檔 | 當月逐日檔＋前 3 個月壓縮檔（約 4 個月） | 下載時一律歸檔到 `cache/taifex_vix/{daily,zip}`；7、8、9 月壓縮檔已存 |
| 富邦分點頁 | 至少 2 年（實測到 2024-06），上限不明 | 尚未歸檔，建議批量保存 |
| 自家 ws 逐筆／五檔／權證／MIS 深度 | 無歷史 API | 各收集器每日 |
| 前瞻帳本、OOS 事件帳本 | 事後無法重現 | 各 job |

## 三、尚未處理的系統性依賴：`stock_daily_bars` 的 `source='finmind'`

2026-10-17 盤點：**25 個檔案**讀日線時寫死 `source='finmind'`（FinMind 日線只涵蓋約 520 檔 ETF 成分股，且帶還原價 `adj_close`；官方日線有約 7,900 檔但**沒有** `adj_close`）。FinMind 停用後，這些讀取端看不到 10/16 之後的新日期。

| 類別 | 檔案 |
|---|---|
| 收盤管線／儀表板 | `rrg_universe_snapshot`、`rrg_rotation`、`rrg_mono_daily_brief`、`vcp_funnel_specs_daily`、`stock_context`、`report_summary`、`sync_stock_technical_daily` |
| 資金流與績效歸因 | `flow_returns`、`flow_attribution`、`build_signal_outcomes` |
| 下單層（均為停用／dry-run 的袖） | `order/dayflip_short_*`、`order/dayflip_post_dump_long_order`、`order/songshan_copytrade_order`、`copytrade/branch_signals`、`copytrade/dual_branch_signals` |
| 研究／回測 | `research/backtest/*`、`research/chip_relative/panel`、`research/tw100_data_quality` |

處理選項（尚待決定）：(A) 橋接——用官方日線為既有 FinMind 宇宙續寫帶 `adj_close` 的列；(B) 逐一改讀取端同時認官方來源並自行處理還原價；(C) 接受這些功能停在 10/15。

## 三之二、2026-10-17 決定：RRG／VCP／Lens 不再維護，接受停在 10/15（選項 C）；之後只處理儀表板內容

- 上表第三節的 25 個檔案中，RRG 宇宙、VCP 漏斗、Lens 相關**不再處理**。
- 已處理的儀表板內容：主表「波動分數」（融資餘額＋借券餘額）、頁首 VIXTWN、個股詳情頁的法人／融資／借券。
- **2026-10-17 已處理**：
  - `/chips`：`chip_sop.py` 新增「前段分點」口徑（買超前 15＋賣超前 15 ＋ 專家／席位分點）。每日主力張數與全分點版相同（8 檔 10/15 對照差距 ≤5 張，為頁面四捨五入），集中度改除以成交量，期間版改逐日主力加總，家數差不提供。41／41 檔恢復 ok。
  - 個股詳情頁：**分點集中度**（同口徑，分母改官方成交量；3711 對財報狗 8.50% vs 8.55%）、**外資持股**（TWSE MI_QFIIS＋櫃買 qfii，與 FinMind 40／40 一致）、**鉅額**（TWSE BFIAUU＋櫃買 openapi，9/24 對帳 24／24）、**Beta**（價格本來就是 Yahoo，只換股票清單來源）。
  - `capitulation-oos-accumulate` 已退役（launchd 卸載、登錄表 retired）。
- **仍缺**：上櫃當沖（櫃買只提供市場彙總，無逐檔端點）→ 儀表板 11 檔上櫃無當沖資料。

## 四、已知限制（誠實版）

- 富邦分點頁是網頁、不是正式 API；規則是「依排名只列前段」，約 99% 的漏列是排名以下的小部位。使用條款尚未審閱。
- 停用期間 FinMind 才有的**完整分點底稿**（每檔股票約 800 個分點）從 2026-10-16 起無法重建。
- 官方各頁面格式改版時，解析會回 `None`（視為無資料而非 0），並由測試與對帳腳本抓出。
- `computed` VIXTWN 自 10/01 停更是在這次遷移之前就已存在的問題。

## 五、2026-10-17 最終稽核：啟用中的 job 都不再依賴 FinMind

**防線（三層）**：(1) `finmind_client` 預設停用（`FINMIND_ENABLED` 未設為 1 就不連線）；(2) `.env` 的 `FINMIND_TOKEN` 已註解（備份 `.env.bak-20261017-pre-token`），連直接呼叫 API 的腳本也拿不到 token；(3) 依賴 FinMind 的 job 已停用。

**已改官方／富邦**：期貨法人、VIXTWN（日線＋分鐘）、TX／TE 缺口（tech_risk）、早盤期貨快照（`sync_morning_futures`）、台指期夜盤缺口（`morning_gate_brief`）、融資券／當沖／法人、外資持股、鉅額、Beta 股票清單、`chip-daily-brief` 產業分類、分點（專家池＋持倉＋`/chips`＋詳情頁集中度）、日線覆蓋率斷言。

**已停用（launchctl bootout + disable，登錄表標記）**：`capitulation-oos-accumulate`（retired）、`tape-bars-backfill`、`chip-loud-accum-track`、`ops-console-evening-sync`（disabled）、`finmind-branch-probe-onetime`（retired，plist 已刪）。復原：`launchctl enable gui/$(id -u)/com.jackm4.goldenstocks.<label>` 後 `launchctl bootstrap`。

**程式碼仍含 FinMind 呼叫、但在啟用中的 job 裡走不到**：`order/dayflip_short_signal.resolve_futures_symbol`（僅已退役的 dayflip 使用，且包 `try/except`）、`refresh_dayflip_futures_daily_cache`（僅已退役的席位週更）、`stock_universe`（僅 `sync_stock_market_daily` 使用，入口已 SKIP）、`build_hivol_futures_universe`（手動產生器）、`run_expert_pool_watch.refresh_stock_tape` 的 FinMind 分支（停用時走富邦）。

**已知缺口**：上櫃當沖；詳情頁「本益比同族群」的 EPS 快照（2026-09-25，原由 FinMind 財報產生，無法自動更新）；RRG／VCP／Lens 與 25 個讀 `source='finmind'` 日線的檔案停在 10/15；每檔完整約 800 個分點的底稿。
