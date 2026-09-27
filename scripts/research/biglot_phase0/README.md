# biglot dashboard 重構 Phase 0 工具

驗證工具，不動 `scripts/research/biglot_dashboard.py` 本體一行。細節見
`docs/biglot-refactor-roadmap.md`。

## 建 fixture（一次，每個要測的交易日各建一次）

```bash
PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/build_fixture.py --date 2026-09-17
```

輸出到 `${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/{date}/`。只從生產 DB 讀
（`mode=ro`），輸出是全新檔案，不會碰生產庫或生產 cache。已建好的第一份：
`2026-09-17`（挑選理由見下方「已建 fixture」）。

## 依賴圖（純靜態分析，可直接對正式檔案跑，不需要 fixture）

```bash
.venv/bin/python scripts/research/biglot_phase0/dep_graph.py \
    scripts/research/biglot_dashboard.py --out /tmp/dep_graph.json
```

## Golden-diff（每次 Phase 1+ 搬移程式碼前後都跑一次）

```bash
# 搬移前先建 baseline
PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/run_golden_diff.py \
    --fixture ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-17 \
    --out ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-17/golden_baseline

# 搬移後重跑並比對
PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/run_golden_diff.py \
    --fixture ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-17 \
    --out ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-17/golden_after \
    --baseline ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-17/golden_baseline
```

不加 `--stocks` 預設跑全部 42 檔觀察宇宙個股頁，實測約 90~120 秒（一次把全天
tick 檔重放完，比正式站台「5 秒增量」貴很多，這是預期成本，不是 bug）。想快速
迭代先加 `--stocks 2330,2454` 只測一兩檔。

## Smoke test（起服務打全部路由）

```bash
PYTHONPATH=src .venv/bin/python scripts/research/biglot_phase0/smoke_test.py \
    --fixture ${GOLDENSTOCKS_DATA_DIR}/scratch/biglot_fixture/2026-09-17
```

Port 固定 8772（只 bind 127.0.0.1），跟正式 8771 不衝突，可以在正式站台跑著
的同時執行。同樣約 90~120 秒（起服務前要先跑一次全天 ingest()+render()）。

## 已建 fixture：2026-09-17

從 `cache/biglot_live_watch/raw_*.jsonl` 檔案大小挑的（136MB，近期資料量排名
中高，同時 `watchlist_books_2026-09-17.jsonl`＝16MB 也在中高段）——沒有特別
核對過那天是否真的觸發過 iceberg/漲停/大單等具體事件，只是用「資料量大＝多數
程式路徑會被跑到」當代理指標。如果之後要專門測某個功能（例如 iceberg 偵測、
權證影子帳），建議另建一個那個功能確實有觸發過的日期當 fixture，不要只靠這
一天。

## 踩過的坑：fixture 會被自己跑過的結果污染

第一版工具直接把 `GOLDENSTOCKS_DATA_DIR` 指向 canonical fixture，結果 `smoke_test.py`
按過的 `/hold`／`/stocknote` 寫進 `holds.json`／`stock_notes.json`／`hold_events_*.jsonl`，
下一次 `run_golden_diff.py` 讀到這些殘留狀態，跑出一個看似真實、其實只是「fixture
被弄髒」的假 diff——跟被測的程式碼改動完全無關，第一次驗證 `_fmt`/`_tick_sz`
合併安全時就踩到了。

現在 `_fixture_lib.load_dashboard_module()` 預設會先把 canonical fixture 硬連結成
一份 disposable 工作複本（`materialize_working_copy()`），process 結束時用
`atexit` 自動砍掉，canonical fixture 永遠保持乾淨、可以放心重複拿來跑很多次。
只有明確要檢查「跑完 fixture 目錄多了什麼檔案」才需要 `use_working_copy=False`。

## 已知限制

- `dep_graph.py` 只分析 87 個「模組頂層」函式；`class S`/`class H` 底下的
  method（`__init__`/`do_GET`/`do_POST`/`log_message`）跟任何巢狀 `def` 沒有
  單獨列出——這對模組邊界決策影響不大（整個 class 會一起搬），但如果要做更細的
  分析要另外處理。
- `run_golden_diff.py`/`smoke_test.py` 用「把 `biglot_dashboard.datetime` 換成
  凍結子類別」讓 27 處 `datetime.now(TZ)` 呼叫可重現，但不會凍結任何其他非決
  定性來源（例如若未來程式碼改成呼叫 `time.time()` 或讀其他即時檔案，這裡不會
  自動攔截，需要另外擴充 `_fixture_lib.py`）。
