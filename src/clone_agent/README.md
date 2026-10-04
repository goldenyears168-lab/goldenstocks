# clone_agent —— 複製人操盤手代理人

把使用者已驗證有效的手動交易行為，變成可重複執行的 SOP。
研究窗 **2026-09-29 → 10-02**（4 天、89 筆手動個股期貨委託、+488,984 元）。

規格 SSOT：[`docs/trader-clone-agent-prd.md`](../../docs/trader-clone-agent-prd.md)

---

## 30 秒看懂

```
①  研究腳本                 ②  候選產生器               ③  本套件            ④  人
scripts/research/      →    trader_build_          →   clone_agent/   →   按批准/略過
trader_*.py                 approval_candidates.py      (這裡)             (可改價改量)
    ↓                            ↓                         ↓                   ↓
 找出規則                  candidates_{日}.jsonl      渲染成卡片          decisions_{日}.jsonl
 (已完成,見 PRD)            「規則說該下這張單」       「為什麼」          「人同不同意」
                                                                              ↓
                                                                    同意率 ≥80% × 20 日
                                                                        才談全自動
```

**現在是階段 A：代理人只會「建議」，送單的是人。**

---

## 這個套件不能做什麼（硬邊界）

> **不 import 富邦 SDK、不 import `src/order/`。構造上無法送出委託單。**

這不是靠自律，是靠測試掃描整個套件的原始碼釘住的：
`tests/test_clone_agent_approval.py::test_no_order_capability_anywhere_in_package`

按「批准」只是把裁決寫進帳本。真正接上送單要等 PRD §8 的下單層完成、四層鎖打開——
那會是另一次明確的交辦，不會悄悄發生。

---

## 兩個檔案在做什麼

| 檔案 | 職責 | 不碰 |
|---|---|---|
| `approval.py` | 帳本讀寫（候選、裁決、同意率統計）。純邏輯、好測。 | HTML、網路 |
| `panels.py` | 把帳本渲染成 HTML。色票沿用 biglot 儀表板（GitHub dark）。 | 寫檔、網路 |

HTTP 由 biglot 儀表板（port 8771）提供，三個路由在
`scripts/research/biglot/http_server.py`：

| 路由 | 用途 |
|---|---|
| `/agentfrag` | 首頁最上方的精簡待批准區，15 秒輪詢換內容 |
| `/agent` | 完整頁：待批准／資金水位／成交紀錄／批准紀錄／量化規格 |
| `/agentdecide` | POST，寫入裁決 |

路由用 **lazy import**：本套件壞掉只壞這兩頁，不會讓儀表板起不來。

### 為什麼掛在 8771 而不是獨立服務

本來做成獨立 port 8772，但使用者遠端一律走 Tailscale，**而 Tailscale 只通 8771**——
他根本連不到。把「架構乾淨」排在「使用者用得到」前面是錯的優先順序，已改正。

---

## 資料在哪

`${GOLDENSTOCKS_DATA_DIR}/data/clone_agent/`

| 檔案 | 誰寫 | 內容 |
|---|---|---|
| `candidates_{日}.jsonl` | 候選產生器 | 代理人的建議＋理由（✓命中 ／ +加分 ／ ⚠風險） |
| `decisions_{日}.jsonl` | 人（透過網頁） | append-only；同一 `intent_id` 後寫覆蓋先寫 |
| `capital_legs.json` | 研究腳本 | 三腿資金水位時序（畫圖用） |
| `fills.json` | 研究腳本 | 成交紀錄 |
| `spec.json` | 研究腳本 | 規格頁的數字（**頁面不硬寫數字**，免得研究更新後頁面說謊） |

`intent_id` = `(日期, 時間, 標的, 方向, 掛價)` 的雜湊前 12 碼。重複裁決只覆寫同一筆。

---

## 三個不明顯但重要的設計

**1. 帳本只記「跟建議不同」的欄位。**
他改了什麼比他按了什麼更有價值 —— 改動量就是「規則離他腦袋還差多遠」的量尺。
全部都記的話帳本會充滿雜訊，這個訊號就廢了。

**2. 同意率的分母是「已裁決」不是「候選」。**
沒看的不算不同意。否則他某天忙到沒看，同意率會無辜暴跌，而升級門檻就是靠它。

**3. 不存在的 `intent_id` 會被拒絕。**
沒有這道檢查的話任何字串都能寫進帳本，同意率的分子就能被污染。

---

## 怎麼改

```bash
# 產生候選（目前是回放他真實下單時點，不是全宇宙掃描）
GOLDENSTOCKS_DATA_DIR=~/goldenstocks-data PYTHONPATH=src:scripts/research \
  .venv/bin/python scripts/research/trader_build_approval_candidates.py

# 測試
.venv/bin/pytest tests/test_clone_agent_approval.py -q

# 改完要讓儀表板吃到新碼 = 重啟（需 ~60 秒才 bind 8771）
GOLDENSTOCKS_DATA_DIR=~/goldenstocks-data PYTHONPATH=src:scripts/research \
  nohup .venv/bin/python scripts/research/biglot_dashboard.py \
  >> ~/goldenstocks-data/logs/biglot_dashboard.log 2>&1 &
```

⚠ `panels.py` 只動 HTML 字串的話，**首頁輪詢會自己換掉**，不用重啟。
動到 `http_server.py` 或 `biglot_dashboard.py` 才需要重啟。

---

## 還沒做

- **全宇宙掃描**：現在的候選只是「他實際下單那一秒，規則會說什麼」，
  還不能回答「規則會不會找到他漏掉的標的」。要掃 42 檔 × 全部分鐘。
- **接上送單**：PRD §8 的下單層、四層鎖、八項 fail-closed 測試，都還沒開始。
