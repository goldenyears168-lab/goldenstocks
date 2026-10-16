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
| 分點買賣（專家池核心分點） | `TaiwanStockTradingDailyReport` | 富邦行情站 `fubon-ebrokerdj`（依分點頁，金額排名） | 驗證完成、**寫入端未做** | 專家池配對召回 96.7%；持倉賣方口徑淨賣 ≥5,000 萬 95.4%、≥1 億 99.0% |
| 個股三大法人買賣超 | `TaiwanStockInstitutionalInvestorsBuySell` | TWSE T86＋櫃買（`scripts/backfill_twse_institutional.py` 已有） | 待做 | 目前僅回補工具，需排進每日；FinMind 宇宙只有約 71～271 檔，官方可到全市場 |
| 融資融券／當沖／借券 | `TaiwanStockMarginPurchaseShortSale` 等 | TWSE MI_MARGN、TWTB4U、TWT72U；櫃買對應報表 | 待做 | 借券餘額 TWT72U 已在收盤管線 |
| 外資持股比 | `TaiwanStockShareholding` | TWSE MI_QFIIS＋櫃買 | 待做 | |
| 個股市值 | `TaiwanStockMarketValue` | 日線收盤價 × 發行股數（`stock_daily_bars.shares_outstanding`） | 待做 | |
| 月營收、財報、PER、股利 | `TaiwanStockMonthRevenue` 等 | MOPS、TWSE（BWIBBU_d、TWT48U） | 待做 | 每週日跑，風險較低 |
| 鉅額交易 | `TaiwanStockBlockTrade` | TWSE 鉅額成交資訊 | 待做 | 表已停在 2026-09-24 |
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

## 三、已知限制（誠實版）

- 富邦分點頁是網頁、不是正式 API；規則是「依排名只列前段」，約 99% 的漏列是排名以下的小部位。使用條款尚未審閱。
- 停用期間 FinMind 才有的**完整分點底稿**（每檔股票約 800 個分點）從 2026-10-16 起無法重建。
- 官方各頁面格式改版時，解析會回 `None`（視為無資料而非 0），並由測試與對帳腳本抓出。
- `computed` VIXTWN 自 10/01 停更是在這次遷移之前就已存在的問題。
