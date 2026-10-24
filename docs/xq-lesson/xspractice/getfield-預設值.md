---
source: https://www.xq.com.tw/lesson/xspractice/getfield-預設值/
title: GetField 預設值 - XQ 全球贏家
fetched: 2026-10-23
---

# GetField 預設值

###### 2025-06-09 18:41:13

複製連結

使用者在取得資料欄位時，有時會遇到資料尚未更新或欄位缺失的情況，這可能導致回測或執行時發生錯誤。 為避免此類問題，GetField 欄位新增了預設值參數，當資料缺失時，可回傳指定的預設值，確保策略順利執行。 除了預設值以外，還有其他函數可用來判斷欄位是否可用以及欄位名稱等參數是否正確。 

### **語法說明**

#### **default**

Value1 = GetField("大戶持股比例", "W", default := 0); Value2 = GetSymbolField("2330.TW", "大戶持股比例", "W", default := Value2[1]);GetField / GetSymbolField 在傳入參數時，現在可額外加上 default 參數。 此參數可以是固定數值 （ 如上方範例 0 ） 或是變數 （ 如上方範例 Value2[1] ）。 當欄位資訊輸入正確時，以下情境支援預設值：

- 資料尚未更新。
- 資料中間有空值。
- 資料讀取範圍內沒有資料。
- 資料序列為空。 例如： 從未發生不定期欄位事件的商品。
以下情境則不支援預設值：

- 取未來值。
- 回測頻率為 1 分鐘逐筆洗價取 Tick 或是分鐘頻率資料欄位，因無法對位故會發生錯誤。
- 不支援的商品 ／ 頻率。
- 回測範圍內沒有發生過不定期欄位的事件。 （ 會回最新一筆 ）
 

#### **CheckField**

CheckField("外盤量", "D"); CheckSymbolField("TSE.TW", "外盤量"); CheckField / CheckSymbolField 會依據傳入的商品代碼、欄位和頻率來判斷該資料是否能夠取用，回傳 True / False。 

#### **IsSupportField**

IsSupportField("月營收", "M"); IsSupportSymbolField("TSE.TW", "月營收"); IsSupportField / IsSupportSymbolField 可根據傳入的商品代碼、欄位和頻率，判斷指定的欄位是否存在，回傳 True / False。 需注意此函數並不會判斷對應 K 棒上是否有資料，只會判斷欄位是否存在。 因此，可能會發生函數回傳 True ，但該根 K 棒上沒有資料的情況。[##### 前一篇

XS美股新增欄位列表](https://www.xq.com.tw/lesson/xspractice/xs美股新增欄位列表/)[##### 下一篇

商品清單功能](https://www.xq.com.tw/lesson/xspractice/商品清單功能/)
