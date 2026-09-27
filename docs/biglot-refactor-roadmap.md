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

  **決定（jack 拍板）**：不現在硬推 46 個候選全部搬完。已補強 `check_daily_rebind.py`
  （模擬連續兩個交易日，動態證明 19/19 個可判斷的全域確實會整包換物件）。

  **更新（2026-09-27 稍晚）：不需要 state.py，找到更簡單的正確做法**——一開始設想的
  「建 state.py 容器 + 把 `ingest()` 的 `global X; X = ...` 改成 `state.X = ...`」
  是過度工程；真正的修法只需要一條規則：**任何新模組要讀 `biglot_dashboard.py` 自己定義的
  全域（不管是不是這 18 個危險全域），一律 `import biglot_dashboard`（整個模組），在函式本體
  「呼叫當下」用 `biglot_dashboard.X` 屬性存取，不要在檔案頂層 `from biglot_dashboard import X`**。
  原因：
  - 屬性存取是每次呼叫都重新查一次 `biglot_dashboard` 模組目前的狀態，不管 `ingest()`
    换過幾次日都不會過期——不需要改 `ingest()` 一行程式碼。
  - 同時解決了「新模組 import biglot_dashboard、biglot_dashboard 又 import 新模組」的循環
    import 問題：`import biglot_dashboard`（模組本身）只需要 `sys.modules` 裡有這個模組物件
    就會成功，不需要它已經執行到某個特定屬性；`from biglot_dashboard import X` 才會在
    biglot_dashboard 還沒執行到 `X = ...` 那行時直接炸掉。

  已用 `_xq_style_block`（讀 `XQ_STYLE`/`VIXTWN`，兩個都在 18 個危險全域裡）做端到端證明：
  搬進 `biglot/xq_style.py`，模組頂層只有 `import biglot_dashboard`，函式內用
  `biglot_dashboard.XQ_STYLE`/`biglot_dashboard.VIXTWN`。驗證：golden-diff 零 diff；
  額外手動測試模擬連續兩天 `ingest()`，確認① `XQ_STYLE` 物件真的換了、② `biglot.xq_style`
  模組本身零頂層快取狀態、③ 搬移後的函式 `__globals__['biglot_dashboard']` 就是同一個
  正在跑的模組實例——三者合起來構成不可能 stale 的結構性證明,不只是「這次剛好沒事」。

  **第二批（已完成 2026-09-27，多agent分工）**：用 7 個 general-purpose agent 平行處理，
  每個只負責「讀 biglot_dashboard.py、寫一個新檔案」，不碰共用檔案，避免並行寫入衝突；
  移除舊定義＋接 import＋跑驗證這三步集中由主線程序列執行。搬完 42 個函式到 7 個新檔案：
  `reference_loaders.py`(12)、`scoring_support.py`(12)、`user_state.py`(6)、
  `paper_trading.py`(5)、`mini_futures.py`(3)、`stock_meta.py`(2)、`pe_and_shadow.py`(2，
  含 `_pe_peer_block`/`_shadow_triple`，兩者都碰危險全域，用同一套屬性存取規則搬移)。
  `_score_v2`（核心 V2.5 計分引擎）刻意不列入這批，留待專門一輪處理。

  過程中 golden-diff 抓到一個真的 bug（不是誤報）：4 個新檔案各自 `from datetime import
  datetime` 後呼叫 `datetime.now(biglot_dashboard.TZ)`——這個 `datetime` 是它們自己匯入的
  真正時鐘，不是測試工具凍結掛在 `biglot_dashboard.datetime` 上的假時鐘，導致
  `_load_prev_close_db` 等函式的「排除今天」判斷用了真實牆鐘日期，抓錯昨收，
  42 檔全部 %漲跌算錯——golden-diff 老實抓到 88 個檔案差異。修法：這幾個檔案的
  `datetime.now(...)` 全部改成 `biglot_dashboard.datetime.now(biglot_dashboard.TZ)`，
  `datetime.strptime`/`timedelta` 等不依賴「現在」的用法不受影響。詳見
  `scripts/research/biglot_phase0/README.md`「踩過的坑」。修好後：golden-diff 全 42 檔
  零 diff、smoke test 14/14、`_pe_peer_block`/`_shadow_triple` 額外過連續兩天過日驗證。

  **第三批（已完成 2026-09-27，2 個 agent 平行處理）**：`_score_v2`（核心 V2.5 計分引擎，
  搬進 `biglot/score_v2.py`）+ `_ingest_trade`/`_stk_trade`（搬進同一個 `biglot/trade_ingest.py`，
  刻意不合併）。驗證：golden-diff 全 42 檔零 diff、smoke-test 14/14、`check_prod_launch` 通過
  （正式站台真實指令啟動無崩潰——這是上一個事故後補的第三道關卡，這次確實有跑）。
  `biglot_dashboard.py` 2862 行。

  `_ingest_trade`/`_stk_trade` 搬移時 agent 逐項列出兩者的 drift（只報告不決定要不要統一）：
  1. 狀態物件：前者寫共用的 `ST`（跨42檔）、後者寫傳入的單檔 `st` dict
  2. 分桶粒度：前者5分桶、後者逐分桶
  3. 只有前者追蹤 bid/ask（餵給 iceberg 偵測）
  4. `first_done`：前者是 set（跨檔）、後者是單一 bool
  5. 前者處理全宇宙 feed、後者用 sid 參數過濾成單檔
  6. 只有前者追蹤當日高低點(`ds["hi"]/["lo"]`)
  7. 前者有明確的「中實戶」第三桶、後者中實單直接丟棄不累計
  8. 前者散戶欄拆「淨額」與「無方向成交額」兩個獨立欄位、後者只有淨額一個
  9. 只有前者追蹤午盤後大單(`ds["big_pm"]`)
  10. 只有前者追蹤開盤(09:00-09:25)/尾盤(12:55-13:20)兩個 SMFI 觀察窗
  11. 只有前者維護餵給 `_rolling`(scoring_support.py)的 3700 秒滾動 deque(`ST.recent`)
  12. `side` fallback 寫法不同(`.get()` vs 直接索引)，語意等價

  這 12 點目前**維持現狀不動**——是否該統一是產品/研究決策，不是重構該擅自決定的事，
  已完整記錄在這裡等 jack 之後決定。

  **第四批（已完成 2026-09-27，8 agent 平行處理）**：重新跑 dep_graph 發現前三批之後，
  「零呼叫其他頂層函式」候選其實還有 18 個（不是原本以為的 0 個——這些函式呼叫的是
  「已經搬走的函式」，不是還留在 biglot_dashboard.py 的函式，dep_graph 的呼叫圖只認
  當下檔案裡的頂層定義，這批 agent 呼叫的都是前幾批搬走的函式，所以仍然安全）。
  8 個 agent 平行處理，4 個擴充既有檔案（`utils.py`+3、`user_state.py`+4、
  `paper_trading.py`+1、`reference_loaders.py`+1）、4 個建新檔案（`iceberg.py`、
  `tx_panel.py`、`day_views.py`+5、`score_rows.py`）。

  這批第一次出現「寫」危險全域的函式：`_refresh_vol_risk_if_needed` 原本
  `global VOLRISK, VOLRISK_DATE` 後直接賦值，搬移後改成
  `biglot_dashboard.VOLRISK = ...` 屬性賦值（不再宣告 `global`）——agent 正確做對，
  額外手動驗證：模擬「需要刷新」狀態呼叫一次，確認寫入真的反映回
  `biglot_dashboard` 自己的命名空間（`id()` 有換、值有更新、42 筆資料寫入正確），
  第二次呼叫正確判斷「已是當天不用再刷新」而回傳 False。

  驗證：golden-diff 全42檔零diff、smoke-test 14/14、check_prod_launch 通過，
  額外針對 `_oos_update_at_close`（讀 `DAILY_TREND`/`UNI5`）跑連續兩天過日驗證確認
  不會 stale。`biglot_dashboard.py` 2282 行。

  **第五批（已完成 2026-09-27，4 agent 平行處理）**：重新跑 dep_graph 又發現 8 個「零呼叫
  其他仍留在檔案裡的頂層函式」候選（原因同第四批：呼叫的是已搬走的函式）。
  `_paper_update`/`_paper_settle` 併入既有 `paper_trading.py`、`_px_class` 併入既有
  `utils.py`、新建 `cause_tags.py`（`_cause_tags`）、新建 `detail_charts.py`
  （`_stock_series`/`_stock_series_locked`/`_svg_detail`/`_svg_mini` 四個一起搬，因為
  `_stock_series` 只是 `_stock_series_locked` 外面包一層 `DETAIL_LOCK` 鎖，兩者必須
  留在同一個檔案——agent 有特別確認這個鎖的用途（防止 2026-09-24 那次大戶累計算兩次
  的事故重演），沒有把鎖跟被鎖的函式拆開）。

  驗證：golden-diff 全42檔零diff、smoke-test 14/14、check_prod_launch 通過。
  `biglot_dashboard.py` **1930 行**（原始 4326 行，減少 55%）。

  **後續要做的（下次對話）**：重新列出目前仍留在 `biglot_dashboard.py` 的 8 個定義：
  `class S`、`ingest()`、`render()`（約 963 行，全檔案最大單一函式）、
  `render_stock_frag`、`render_grid_frag`、`render_stock`、`class H`
  （HTTP composition root）、`loop()`。dep_graph 再也找不出「零呼叫其他仍留在檔案裡
  的頂層函式」的候選了——這 8 個是真正**互相呼叫**的核心（`render()` 呼叫
  `render_stock_frag` 等；`H` 呼叫 `render()`/`render_day` 等；`loop()` 呼叫
  `ingest()`+`render()`），前五批「零呼叫、可獨立剪貼」的簡單手法在這裡完全用不上了。

  其中 `ingest()` 本身雖然技術上仍是「零呼叫其他仍留在檔案裡的頂層函式」（它呼叫的
  `_ingest_trade`/`_iceberg_update`/所有 `_load_*` 都已搬走），但它是**這整個重構最早
  修的那個過日重載 bug 的所在地**，而且會一次寫入全部 17 個危險全域（`global X; X = ...`
  × 17）——這跟第四批「只寫 2 個危險全域」的 `_refresh_vol_risk_if_needed` 不是同一個
  量級的風險。`render()` 900+ 行則需要先做設計（怎麼拆成 `_compute_rows()`/
  `_assemble_html()` 兩段），是全新的、不同性質的工作，對應 Phase 7。

  **狀態＋ingest搬移（已完成 2026-09-27，單線程手動處理，不交給agent）**：
  1. `class S`/`ST` 搬到 `biglot/state.py`——純位置搬移，`biglot_dashboard.ST`
     這個屬性存取路徑對其他 15+ 個已搬移模組完全不變。順手清掉變死碼的
     `from collections import defaultdict, deque` 頂層 import。
  2. `ingest()` 搬到 `biglot/ingest.py`——這整個重構風險最高的一次搬移，逐一把
     17 個 `global X; X = ...` 轉成 `biglot_dashboard.X = ...` 屬性賦值，搬移前
     先用「去除 `biglot_dashboard.` 前綴後逐行比對原始碼」的程式化驗證，確認
     零邏輯差異（只有兩處因為行長度換行格式改變，語意不變）才動手替換。

  驗證（比照對待這個等級的風險，全部跑過）：golden-diff 全42檔零diff、
  smoke-test 14/14、check_prod_launch 通過，**外加重跑 `check_daily_rebind.py`
  的完整18全域清單**（不是只挑一兩個抽查）——19/19 個可判斷的全域確認過日後
  依然正確整包換物件，跟搬移前行為一致。

  `biglot_dashboard.py` 1794 行。剩 6 個定義：`render()`（約 963 行,全檔案最大單一
  函式）、`render_stock_frag`、`render_grid_frag`、`render_stock`、`class H`
  （HTTP composition root）、`loop()`——這些是最後、也是耦合度最高的一批，
  對應原始草案 Phase 7-8。

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
