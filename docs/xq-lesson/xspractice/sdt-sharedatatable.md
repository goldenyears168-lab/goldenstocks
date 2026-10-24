---
source: https://www.xq.com.tw/lesson/xspractice/sdt-sharedatatable/
title: SDT 共享資料表全攻略：用 XS 打造跨商品資料共享與策略資金控管 - XQ 全球贏家
fetched: 2026-10-23
---

# SDT 共享資料表全攻略：用 XS 打造跨商品資料共享與策略資金控管

###### 2026-08-10 16:32:45

複製連結

目前 XS 語法雖然可以用變數與陣列在單一商品的腳本內保存資料，但當您想要「讀進一份自己準備好的資料（ 例如每檔商品的停利、進場價、停損價 ）」，或想要「讓同一個策略內的多檔商品彼此看到對方目前的交易狀態」時，現行語法就無法做到。 此次更新新增了 **SDT（ ShareDataTable，共享資料表 ）**功能，使用者可以像操作一張表格一樣，用商品代碼（ 或任何自訂字串 ）當索引，讀寫多個欄位的數值或字串；並且可以選擇讓同一個策略內的所有商品共用同一份資料，藉此寫出更靈活的資料讀取與策略安控邏輯。 

## **什麼是 SDT**

一個 SDT 就是一張二維表格。第一行固定是 **Key**（ 字串 ），通常放商品代碼；Key 之後最多可以有 100 行（ Column ），每個欄位可存數值或字串。它長得像這樣：

 **Key** | **欄位 1** | **欄位 2** | **欄位 3** | **… 至多 100 欄** |

 1101.tw | 40.0 | 50.0 | 3 |  |

 1102.tw | 38.0 | 48.0 | 2 |  |

 1216.tw | 70.0 | 55.0 | 5 |  |

您可以用 Key 直接讀寫某一列某一行的欄位，也可以取得全部 Key、做統計、做排序，或把一份 CSV 檔案讀進來當成初始資料。 

### 兩種共享範圍（ Scope ）

相似的函數，靠函數名稱後面的後綴來區分共享範圍：

 **Scope** | **後綴** | **共享範圍** |

 **商品** | （ 無 ） | 每個執行商品各自擁有獨立的 SDT，彼此看不到對方的資料 |

 **策略** | _L | 同一個策略內的所有執行商品，共用同一份 SDT |

除了共享範圍不同，兩者的使用方式與結果完全相同。若您只是想在自己這檔商品的腳本裡當成好用的二維表格，用商品 Scope（ 無後綴 ）即可；若想讓同策略的其他商品也讀得到、改得到，就用策略 Scope（ 加 _L ）。 

### 容量限制

- 商品 Scope 與策略 Scope 各只有 1 個 SDT。
- 每個 SDT 最多 5000 列（ 5000 個不重複的 Key ）。
- 每個 SDT 最多 100 行（ 不含 Key 欄 ）。
- Key 與欄位名稱長度上限皆為 100 個字元，且不分大小寫。
 

## **SDT 函數語法總覽**

以下以商品 Scope（ 無後綴 ）列出。每個函數都有對應的 _L（ 策略 Scope ）版本，只要在函數名稱後加上 _L 即可，參數與用法完全相同（ 例如 SDT_GetValue 以及 SDT_GetValue_L ）。

 **分類** | **功能** | **語法** |

 **基本讀寫** | 取得數值 | SDT_GetValue(<key>, <column>, default:=0) |

 **基本讀寫** | 取得字串 | SDT_GetString(<key>, <column>, default:="") |

 **基本讀寫** | 設定數值 | SDT_SetValue(<key>, <column>, <value>) |

 **基本讀寫** | 設定字串 | SDT_SetString(<key>, <column>, <value>) |

 **條件寫入** | 在符合條件（ 目前 SDT 中的數值為 <oldvalue> ）時設定數值 | SDT_SetValueIf(<key>, <column>, <newvalue>, <oldvalue>) |

 **條件寫入** | 在符合條件（ 目前 SDT 中的字串為 <oldvalue> ）時設定字串 | SDT_SetStringIf(<key>, <column>, <newvalue>, <oldvalue>) |

 **查詢與取得 Key** | 檢查是否有此 key | SDT_HasKey(<key>) |

 **查詢與取得 Key** | 取得所有 key | SDT_GetKeys(<output_key_array>) |

 **欄位命名** | 將欄位設定名稱 | SDT_SetColumnName(<column_num>, <column_name>) |

 **統計** | 加總指定 Column | SDT_Sum(<column>) |

 **統計** | 計算指定 Column 的平均值 | SDT_Average(<column>) |

 **統計** | 取得最大值 | SDT_Max(<column>) |

 **統計** | 取得最大值與 key | SDT_Max(<column>, <output_key>) |

 **統計** | 取得最小值 | SDT_Min(<column>) |

 **統計** | 取得最小值與 key | SDT_Min(<column>, <output_key>) |

 **統計** | 取得中位數 | SDT_Median(<column>) |

 **統計** | 取得中位數與 key | SDT_Median(<column>, <output_key>) |

 **排序** | 依照 key 的順序排序 | SDT_SortKey(<sorted_key_array>, order:=-1) |

 **排序** | 依照指定 Column 的順序排序（ 數值 ） | SDT_Sort(<column>, <sorted_key_array>, order:=-1) |

 **排序** | 依照指定 Column 的順序排序（ 字串 ） | SDT_SortString(<column>, <sorted_key_array>, order:=-1) |

 **刪除** | 移除指定列的資料 | SDT_RemoveKey(<key>) |

 **刪除** | 移除整個 SDT 的資料 | SDT_RemoveAll() |

 

## **參數說明**

 **參數** | **說明** |

 <key> | 字串，用來指定表格的某一列，通常是商品代碼，也可以是任何自訂字串。不分大小寫，長度上限 100 個字元。 |

 <column> | 指定欄位，可以是數字（ 序號 1～100 ）或字串（ 欄位名稱 ）。用字串時不分大小寫。 |

 <value> ／ <newvalue> ／ <oldvalue> | 要寫入或比對的值。SDT_SetValue 系列傳數值，SDT_SetString 系列傳字串。 |

 default | 選填。當 Key 或欄位不存在時的回傳值。若沒有設定的話，SDT_GetValue 預設回傳 0、SDT_GetString 預設回傳空字串。 |

 <output_key> | 選填。SDT_Max ／ SDT_Min ／ SDT_Median 若帶入這個變數，會一併取回符合條件那一筆的 Key。 |

 <output_key_array> ／ <sorted_key_array> | 必填的一維字串空陣列。呼叫後函數會把 Key 依結果填入這個陣列，供後續逐一取用。 |

 order | 選填。排序方向，-1 由小到大（ 預設 ），1 由大到小。 |

 

## **函數的運作方式**

### Key（ 列 ）的規則

- Key 不分大小寫，"1101.TW" 與 "1101.tw" 視為同一列。
- 用 Key 可以讀也可以寫；若您改用順序（ index，從 1 開始 ）存取，則只能讀不能寫。
- SDT 儲存資料時沒有順序保證。先存 "1102.tw" 再存 "1101.tw"，之後取回的順序不一定照存入順序。若需要固定順序，請用排序函數。

### Column（ 欄位 ）的規則

<column> 用數字或名稱指定，行為如下：

- - **用名稱寫入**：系統會找第一個符合的欄位（ 不含 Key 欄、不分大小寫 ）；找不到就自動新增一個欄位，排在目前最後一欄之後。
- **用數字寫入**：序號在現有範圍內就直接寫；若超過目前欄數，中間的欄位會被自動補上（ 沒有名稱 ）。
- 欄位最多 100 個，超過會回傳執行錯誤。
**需注意**：建議同一份腳本盡量不要混用「數字」與「名稱」兩種指定方式，以免欄位位置不如預期。若第一次寫入時沒有設定欄位名稱，之後可用 SDT_SetColumnName 補上或修改名稱。

### 數值與字串的自動轉換

數值欄位請用 SDT_GetValue ／ SDT_SetValue，字串欄位請用 SDT_GetString ／ SDT_SetString。若讀取時的型態與存入時不一致，系統會自動轉換：

- **數值轉字串**：例如 12 轉成 "12"。
- **字串轉數值**：類似 parseFloat，"12.3" 轉成 12.3；轉不出來則回傳 0（ 例如 "xyz" 轉成 0 ）。
**需注意**：自動轉換只是為了降低出錯機會，不建議在同一欄位混用不同型態。

### 統計與排序的轉換行為

- SDT_Sum ／ SDT_Average：無法轉成數值的列，當作 0 計算。
- SDT_Max ／ SDT_Min ／ SDT_Median：無法轉成數值的列會被跳過；若有多筆並列，回傳字串排序較小的 Key。
- 排序時無法轉換型態的列，會被排到結果的最後面。

### 資料只有一份，與 bar 無關

SDT 的內容與執行到第幾根 bar 無關，每份 SDT 只保留一份資料，記錄的永遠是目前最新的狀態。無法透過 [n] 的方式取得前期值。

### 策略共享時的執行順序（ 重要 ）

當 SDT 被策略共享（ _L ）時，由於策略內的多檔商品是同時運作、不保證先後順序，系統只保證「每一次呼叫 SDT 函數本身」是安全的；但在您連續呼叫多個 SDT 函數之間，其他商品有可能同時改動同一份資料。 因此，凡是「先讀取、再依讀到的值決定要不要寫入」這種跨多個函數的邏輯，可以使用 SDT_SetValueIf ／ SDT_SetStringIf，把「檢查＋寫入」合併成一個不可分割的動作。

## **使用範例**

### 範例 1：跨商品分享函數運算值

SetTotalBar(100); array: _keyName[](""); Value1 = Stochastic(9, 3, 3, Value2, Value3, Value4); if CurrentBar > 31 then begin SDT_SetValue_L(Symbol, "K", Value3); SDT_SetValue_L(Symbol, "D", Value4); SDT_SetValue_L(Symbol, "Date", Date); SDT_SetValue_L(Symbol, "Time", Time); SDT_GetKeys_L(_keyName); Value10 = Array_GetMaxIndex(_keyName); for Value11 = 1 to Value10 begin Print( Value10, Value11, _keyName[Value11], "Date: ", SDT_GetValue_L(_keyName[Value11], "Date", default := 0), "Time: ", SDT_GetValue_L(_keyName[Value11], "Time", default := 0), " K = ", SDT_GetValue_L(_keyName[Value11], "K", default := 0), " D = ", SDT_GetValue_L(_keyName[Value11], "D", default := 0) ); end; Print( SDT_GetValue_L(_keyName[1], "Time", default := 0), SDT_GetValue_L(_keyName[1], "Time", default := 0)[1] ); Print("======================="); end;此範例展示了如何透過 SDT 分享不同商品的函數（ Stochastic ）運算結果並將其印出。 系統內建的 Stochastic 函數只能夠運算在執行商品上，無法跨商品運算。現在除了自行撰寫修改的 Stochastic 函數外，亦可透過使用 SDT 的功能來分享不同商品運算出來的結果。 除此之外，若多數商品都需要取某個代表性商品（ 如 TSE.TW ）運算出來的結果，也可以透過 SDT 分享來節省效能，避免每個商品都要重新算一次。

### 範例 2：多商品共用投入金額上限

input: _AVAILABLE_FUNDS(1000000, "資金上限"), _TRADE_NUM(5, "下單張數"); vars: _usedFund(0); array: _keyName[](""); once begin SDT_SetValue_L(Symbol, "實際庫存", 0); SDT_SetValue_L(Symbol, "占用資金", 0); end; if Filled <> SDT_GetValue_L(Symbol, "實際庫存") then begin SDT_SetValue_L(Symbol, "實際庫存", Filled); if Position > Filled then Value1 = (Filled * FilledAvgPrice + (Position - Filled) * GetField("漲停價", "D")) * 1000 else Value1 = Filled * FilledAvgPrice * 1000; SDT_SetValue_L(Symbol, "占用資金", Value1); end; SDT_GetKeys_L(_keyName); Value1 = Array_GetMaxIndex(_keyName); _usedFund = SDT_SUM_L("占用資金"); Condition1 = Close cross over Average(Close, 10); Condition2 = Close cross under Average(Close, 10); Condition3 = _availableFunds >= (_usedFund + (GetField("漲停價", "D") * 1000 * _tradeNum)); if GetInfo("IsRealTime") = 1 and Condition1 and Q_BestAskSize1 > _tradeNum and Condition3 and Position = 0 and Filled = 0 and Close < 100 then begin SetPosition(_tradeNum, Market); SDT_SetValue_L(Symbol, "占用資金", GetField("漲停價", "D") * 1000 * _tradeNum); end; if GetInfo("IsRealTime") = 1 and Condition2 and Q_BestBidSize1 > _tradeNum and Position = _tradeNum and Filled = _tradeNum then begin SetPosition(0, Market); end;此範例展示了如何透過 SDT 分享不同商品的投入金額，並控制整個策略的資金總和上限。 每檔商品把自己的投入金額寫入共用的 SDT（ 由於是市價，為了保險故先用漲停價來估算 ），進場前先用 SDT_Sum_L 算出全策略已投入的金額，未超過上限才進場。 實際成交後再將庫存成本更新至 SDT，取代送出委託時紀錄的漲停價。 

## **匯入 CSV 作為初始資料**

如果您希望用一份 CSV 檔案的內容當作 SDT 的初始值（ 例如集中管理每檔商品的進場價、停利、停損 ），可以透過介面設定，不需寫在腳本裡。 **需注意**：目前只有「自動交易中心」提供 CSV 功能，指標與策略雷達不支援；其共享範圍為策略共享（ 即對應策略 Scope ）。

### 操作方式

在策略的設定介面「策略內共享 CSV」選擇要匯入的 CSV 檔案，右側有預覽 CSV 內容的按鈕，並可勾選相關選項。需注意若要調整 CSV 檔案的編碼，要在預覽畫面中作設定。 在 CSV 檔案編碼為 UTF-8 的情況下，若使用 Excel 開啟，可能會遇到顯示亂碼的情況，可透過以下兩種方式解決：

- 使用者在 Excel 啟動的情況下，透過「資料」索引標籤 => 取得外部資料 ／ 從文字檔 => 選擇 CSV 檔案 => 在預覽視窗將「檔案原始格式」選擇為 UTF-8 來開啟。
- 使用 Google Sheet 或文字編輯器來開啟檔案。
**策略內共享 CSV 設定介面** [![](../images/377266c8c1.png)](https://www.xq.com.tw/wp-content/uploads/2026/08/01.png)**CSV 內容預覽畫面（ 含編碼設定 ）** [![](../images/f7c91ab79c.png)](https://www.xq.com.tw/wp-content/uploads/2026/08/02.png)可勾選的選項說明如下：

- **是否包含標題列**

- 勾選：第一列會被讀成 SDT 的欄位名稱（ Column Name ）。即使第一列的 Key 位置留空，仍可讀取（ 此時請用數字指定欄位 ）。
- 不勾選：第一列一樣當成資料讀入。若第一列的 Key 位置（ 第一行 ）為空值，則會出錯。
- **是否自動儲存共享資料表異動內容**

- 勾選後，策略啟動時會在 XQ 資料夾 <XQ>\XS\CSV\<策略名稱>\ 下產生一個 <CSV檔名>_<啟動時間>.csv 的備份。
- 每個策略最多保留 10 個備份，更舊的會自動刪除。
- 勾選標題列時，存檔會把欄位名稱放在第一列、資料從第二列開始；未勾選時資料從第一列開始，欄位名稱會被捨棄。

### 讀取時機與儲存機制

- 策略啟動時，若有設定 CSV，會先讀入 CSV 內容，才開始啟動各個商品。
- CSV 只在啟動時讀取一次，啟動後不會再讀，也不會偵測檔案異動後重讀。
- 勾選自動儲存時，系統會每隔約 10 秒檢查一次，發現有異動的 SDT 才寫回 CSV，因此不保證即時儲存。
- 若自動交易程式意外關閉（ 閃退 ），無法保證 CSV 已寫到 SDT 的最新狀態，也無法保證檔案完整。
 

## **支援平台**

本次上線後，各平台可用的 Scope 與功能如下：

 **執行平台** | **商品 Scope** | **策略 Scope（ _L ）** | **CSV 匯入** |

 **指標** | 支援 | 不支援 | 不支援 |

 **策略雷達** | 支援 | 支援 | 不支援 |

 **策略雷達回測** | 支援 | 不支援 | 不支援 |

 **自動交易** | 支援 | 支援 | 支援 |

 **自動交易回測** | 支援 | 不支援 | 不支援 |

 **選股** | 支援 | 不支援 | 不支援 |

 **選股回測** | 支援 | 不支援 | 不支援 |

**需注意**：在 server 執行的環境（ 選股、各種回測 ）中，同策略的商品是依序執行的，就算資料能共享也會因執行順序而產生非預期結果，因此策略共享功能會關閉。此時若使用策略 Scope（ _L ）的 SDT 功能會回傳錯誤。 

## **結語**

SDT 共享資料表讓 XS 腳本首度具備「表格式資料管理」與「跨商品資料共享」的能力：您可以在單一商品內把它當成好用的二維表格，也可以用策略 Scope（ _L ）讓同策略的多檔商品互通交易狀態，搭配 CSV 匯入集中管理每檔商品的進場價與停利停損設定。撰寫跨多個函數的「先讀後寫」邏輯時，記得善用 SDT_SetValueIf ／ SDT_SetStringIf 確保資料安全，並留意各執行平台支援的 Scope 範圍，即可寫出更靈活、更穩健的策略安控邏輯。[##### 前一篇

XS語法應用](https://www.xq.com.tw/lesson/xspractice)[##### 下一篇

營收領先指標：合約負債與營收的搭配實務——用 XS 腳本判讀訂單蓄水池動能](https://www.xq.com.tw/lesson/xspractice/advanced-applications-of-contractual-liabilities/)
