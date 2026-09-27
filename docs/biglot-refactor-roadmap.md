# biglot dashboard 重構路線圖

`scripts/research/biglot_dashboard.py`（現況約 4300 行、111 函式，port 8771，`nohup` 手動啟動，無 supervisor，`tests/` 零覆蓋）的模組化重構計畫。

**Phase A（bug fix + DB 清理）已於 commit `e2765a0`（2026-09-27）全部完成，不在本文件範圍內**：
過日重載漏刷新 `HIST_BIG`/`Y_PMLOW`/`UNI5`/`ETF981_*` 的 bug、來源去重 SSOT（`scripts/research/source_dedup.py`）、
`stock_xq_style_daily` 拆表改名（SCHEMA_VERSION 18）、`weekly-sync` launchd 補排程。要查歷史脈絡看該 commit，
不要在後續對話裡重新驗證或重做。

本文件只涵蓋 **Phase B：`biglot_dashboard.py` 模組化重構**，尚未開始。

---

## 執行前已核實的修正（2026-09-27 稽核，勿信原始草案未經查核的敘述）

- `_fmt`（L1520）：零呼叫點，確認死碼，可獨立刪除，不需要等 Phase 1。
- `_tick_sz`（L1527）與 `_stock_tick`（L2063）：逐字比對公式完全相同，確認可安全合併，不需要等模組搬移。
- `/gridfrag?sort=big|chg` 快取缺口：確認是效能/延遲問題，不是資料正確性問題——`_stock_series()`
  內部本來就有 `DETAIL_LOCK`，2026-09-24 大戶累計兩倍那次事故的根因已經修過。這裡要修的是「非 ind
  排序每次請求都在 HTTP thread 同步重跑 36 檔 `_stock_series()`，跟背景 `loop()` 搶鎖」的延遲，不是搶救資料損毀。
- `_score_v22_legacy`/`sc_in`：目前**沒有**任何 flag 控制，是無條件計算——原始草案寫的「已經是flag控制」不成立，
  是 Phase B 要做的事，不是既有現狀。
- `score_v2_{day}.jsonl` 的 20 日 IC 研究視窗（2026-09-24 起算）：以交易日計不是日曆日，2026-09-27 稽核當下
  視窗未滿，維持原計畫不刪這兩個分數。**Phase B 開始前應重新確認一次視窗是否已滿**——不要沿用這份文件裡的日期直接假設。
- 18 模組邊界清單是讀一次程式碼歸納出來的，**沒有**實際跑過依賴圖分析驗證（`render_grid_frag` 一個函式就同時碰
  `PAGE`/`ST`/`PREV_CLOSE`/`SORT_INDEX`/`DETAIL_LOCK` 五個全域，111 個函式共用這麼多可變全域狀態時，
  邊界清單出錯的機率不低）。Phase 0 必須先產出真正的呼叫圖/全域讀寫圖，再核對這份邊界清單，不能假設它是對的。
- `tests/` 目錄零覆蓋確認為真。golden-fixture 要凍結三個非決定性來源，只凍快取目錄不夠：
  1. `datetime.now(TZ)`（`_in_market()` 等處直接讀 wall-clock）
  2. 即時 tail 的 JSONL（`cache/biglot_live_watch/raw_*.jsonl`、`cache/watchlist_books/*.jsonl`）
  3. 40GB+ 生產 DB（必須指到唯讀快照複本，不能直接指生產庫）

---

## 模組劃分（草案，Phase 0 產出依賴圖後才能定案）

`config.py` → `universe.py` → `reference_data.py` → `state.py` → `trade_classifier.py` → `ingest.py` →
`iceberg.py` → `scoring.py` → `paper_trading.py` → `holds.py` → `oos.py` → `warrant_ledger.py` →
`notes.py` → `market_panel.py` → `render_main.py` → `render_detail.py` → `render_grid.py` →
`render_history.py` → `render_help.py` → `http_server.py`（composition root）。

原檔案降級成 `from biglot.http_server import main; main()` 的薄殼，維持 `nohup python3 biglot_dashboard.py &` 操作習慣不變。

## 關鍵重構點（詳細規格見對話紀錄，這裡只列決策）

1. `_ingest_trade` vs `_stk_trade` 的 tick 解析重複——抽出 `trade_classifier.py` 共用 parse/classify，
   但兩邊各自的桶化邏輯（5分桶 vs 逐分桶）**不強行統一**，drift 是否為 bug 要另外問 jack，Phase B 不擅自改。
2. 刪 `_fmt`；合併 `_tick_sz`/`_stock_tick`（已確認安全，可提前於 Phase 1 之前單獨做）。
3. `_score_v22_legacy`/`sc_in` 改成 flag 控制、預設仍開啟——前提是 IC 研究視窗已滿（見上方稽核備註）。
4. `/gridfrag` 快取擴展到 `sort=big|chg`，比照 `PAGE["frag"]` 預算模式。
5. `scripts/research/biglot_restart.sh` 包裝重啟流程——零風險，可隨時做，不用等模組搬移。

## 執行順序（每階段獨立可驗證、可回退）

- **Phase 0（已完成 2026-09-27）**：工具在 `scripts/research/biglot_phase0/`（用法見該目錄 README.md）——
  `build_fixture.py`（凍結 DB 唯讀子集 45→42 檔宇宙 + 當天 cache 檔）、`run_golden_diff.py`（凍結時鐘後跑
  ingest()+全部 render_*()，逐檔比對）、`smoke_test.py`（起服務打全部路由確認 200，port 8772 不撞正式 8771）、
  `dep_graph.py`（純 AST 靜態分析，不執行程式碼）。已建第一份 fixture：`2026-09-17`（挑選理由＝tick 檔案量中高，
  代理「多數程式路徑會被跑到」，未針對特定事件驗證，見 README 限制說明）。三支動態工具（build/diff/smoke）都已
  對這份 fixture 實測跑過一次，14/14 smoke test 通過、golden-diff 產出非空輸出——工具本身是可信的，不是紙上設計。

  **依賴圖跑出來的結果修正了原始草案的猜測**（`dep_graph.py --out /tmp/dep_graph.json`，87 個頂層函式）：
  - 全域變數 fan-out 排行（真正的 `state.py` 內容，不是猜的）：`TZ`(25個函式)、`ST`(17)、`NAMES`(12)、
    `WRT`(5)、`SNAP_DIR`/`RET_UNM`/`SUBCAT`/`PAPER_BOOKS`(各4)……完整清單見 dep_graph.json。
  - 真正零全域依賴、零呼叫其他頂層函式的 15 個函式（Phase 1 的真候選，取代下面這行原本用讀一次程式碼猜的清單）：
    `_b30n`/`_b5n`/`_book_table`/`_fmt`/`_load_etf981_holdings`/`_load_pe_peer`/`_load_vixtwn`/`_mops_load`/
    `_par30`/`_pctile_rank`/`_stock_info_block`/`_stock_tick`/`_tick_sz`/`_wrt_td`/`bucket_key`。
  - 原草案猜的 `render_help`/`_load_notes` **不是**零依賴（前者讀 `_HELP_GROUPS`、後者讀
    `DEFAULT_NOTES`/`NOTES_PATH`，都是模組常數依賴）——不是錯得離譜，但 Phase 1 排序應該以上面 dep_graph 產出
    的清單為準，不要沿用原始草案那份手動列的名單。
  - `dep_graph.py` 只涵蓋 87 個頂層函式，`class S`/`class H` 的 method 沒單獨列（整個 class 會一起搬，見 README）。

- **Phase 1 第一批（已完成 2026-09-27）**：`dep_graph.py` 原本漏抓「模組層級 try/except 裡的
  `from x import y as Y`」這種 import 綁定，導致 `_stock_info_block`/`_load_pe_peer`/`_load_vixtwn`/
  `_load_etf981_holdings`/`_mops_load` 被誤判成零依賴——已修好工具（`_module_level_stmts` 遞迴
  展開模組層級 if/try/with），重新產出的真葉節點清單只剩 8 個：`_tick_sz`/`bucket_key`/
  `_pctile_rank`/`_par30`/`_b30n`/`_b5n`/`_book_table`/`_wrt_td`。
  已搬進新建的 `scripts/research/biglot/` package：`biglot/utils.py`（前 6 個純數值/時間 helper）、
  `biglot/html_fragments.py`（`_book_table`/`_wrt_td` 兩個葉節點 HTML 片段產生器）。
  `biglot_dashboard.py` 原地只留 `from biglot.utils import ...`/`from biglot.html_fragments import ...`
  兩行 import，函式本體逐字搬移不改一行邏輯。
  驗證：`git stash` 切出搬移前後兩版，對 `2026-09-17` fixture 各跑一次 golden-diff（全表 frag +
  4 檔詳情頁）與 smoke test（14 條路由），前後 byte 數完全一致，零 diff。
  `biglot_dashboard.py` 4326→4250 行。

- **Phase 1 到此為止（2026-09-27 拍板，暫停於此，等下次對話再評估要不要繼續）**：對整份檔案掃過
  「哪些全域是被 `global X; X = ...` 整包重新賦值，不只是原地修改」，抓到 18 個：`ATR_STATE`/
  `DAILY_TREND`/`KEY_LINE`/`PE_TABLE`/`PE_PEERS`/`PE_GEN`/`PE_EPS`/`XQ_STYLE`/`VIXTWN`/`HIST_BIG`/
  `Y_PMLOW`/`UNI5`/`ETF981_HOLD`/`ETF981_ASOF`/`ETF981_PREV_ASOF`/`FUT_PX`/`PREOPEN`/`WRT`/`VOLRISK`/
  `VOLRISK_DATE`（跟本文件開頭那個過日重載 bug 是同一組變數）。剩下 50 個「次順位候選」裡，
  任何讀到這 18 個全域之一的函式（例如 `_pe_peer_block`/`_xq_style_block`），如果現在用
  `from biglot_dashboard import X` 這種 Phase 1 用過的手法搬到新模組，**會在下一次過日時產生
  stale reference**（新模組 import 進來的名字還指著昨天的舊物件，`biglot_dashboard.py` 自己那份
  已經換成今天的）——這正是本文件開頭那個 bug 的同一個機制，換了個地方重演。

  更關鍵的是：`run_golden_diff.py`/`smoke_test.py` 目前的驗證方式（凍結時鐘後在單一 process 裡只
  呼叫一次 `ingest()`）**抓不到這類 bug**，因為只會經歷一次過日重載，不會經歷「連續兩個交易日」
  去暴露 stale reference——搬錯了也會顯示零 diff，等於騙過驗證工具自己。

  **決定（jack 拍板）**：不現在硬推。今天的 Phase 1（8 個純函式）視為完成並收尾，正式站台已重啟
  生效（PID 見部署紀錄）。真正要繼續往下搬碰到這 18 個全域的函式之前，必須先做兩件事，留給下次
  對話評估要不要啟動：
  1. 幫這 18 個全域建共用的 `state.py` 基礎模組（`ingest()` 的過日重載要從 `global X; X = ...`
     改成 `state.X = ...` 屬性賦值，`biglot_dashboard.py` 跟未來搬出去的模組都指向同一個容器）。
  2. 補強 `run_golden_diff.py`，讓它能模擬「連續兩個交易日」而不是只驗證一天，否則沒有工具能
     擋住這類 stale-reference bug。
  這兩件事本質上是原始草案 Phase 3 的範圍，跟今天的「純函式剪貼」是不同量級的改動。

**Phase 9 的兩個小 patch 已提前做掉並驗證過（2026-09-27）**：刪除死碼 `_fmt`（零呼叫點）、
合併 `_stock_tick`/`_tick_sz` 重複公式（`_limit_down` 改呼叫 `_tick_sz`）。用
`git stash` 隔出 patch 前後兩個版本、各自對 fixture 跑一次 `run_golden_diff.py`，
確認零 diff 才算數——第一次驗證時其實踩到 Phase 0 工具自己的 bug（fixture 目錄
會被前一次 smoke_test 的 `/hold`/`/stocknote` 寫入污染，見
`scripts/research/biglot_phase0/README.md`「踩過的坑」），修好 `_fixture_lib.py`
用 disposable 工作複本後才拿到乾淨的零 diff 結果。教訓：golden-diff 工具本身
也需要驗證過沒有偽陰性/偽陽性，不能第一次跑出「有 diff」就直接懷疑是程式碼改動。
- Phase 2：`trade_classifier.py` 抽離（風險較高，需人工 side-by-side 比對一支重倉股的詳情頁）。
- Phase 3：`config`/`reference_data`/`state`（純搬移，預期零 diff）。
- Phase 4：`ingest`/`scoring`（建議跟正式 8771 並行跑一個 scratch port 比對）。
- Phase 5：`paper_trading`/`holds`（額外驗證 JSON 持久化檔案位元級一致）。
- Phase 6：`market_panel`/`render_grid`/`render_history`/`render_detail`。
- Phase 7：`render_main`（`render()` 拆分成獨立子步驟，先搬移再拆分，不同一步做）。
- Phase 8：`http_server` 組合根，舊檔案改殼，正式機上完整跑一週後才視為完成，期間保留 `.pre_refactor` 備份供秒退。
- Phase 9：三個已確認安全的小 patch（`_fmt`/`_tick_sz`/`gridfrag` 快取）——可以提前單獨做，不強制排在最後。

每個 Phase 開始前，先讀本文件開頭的「執行前已核實的修正」區塊，不要重新從頭稽核已經查過的項目；
但 IC 研究視窗、sc_v22 flag 狀態這類會隨時間變化的事實，每次開始前仍要重新確認一次現況再動手。
