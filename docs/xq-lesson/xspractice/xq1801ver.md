---
source: https://www.xq.com.tw/lesson/xspractice/xq1801ver/
title: XQ 18.01 版本 XS 重點功能：美股盤前盤後、函數彈性與編輯器優化 - XQ 全球贏家
fetched: 2026-10-23
---

# XQ 18.01 版本 XS 重點功能：美股盤前盤後、函數彈性與編輯器優化

###### 2025-12-30 17:00:29

複製連結

在 XQ 全球贏家 .18.01 版本中，XS 語法與功能迎來了多項實用更新。本次更新重點包含「美股盤前盤後交易時段支援」、「函數腳本傳遞商品代碼的靈活運用」，以及「編輯器編譯警告功能」。

本文將帶您快速掌握這些新功能的應用方式與注意事項。

## 1. 支援美股盤前與盤後交易時段

美國股市的正常交易時間是在美東時間的 09:30 ~ 16:00 之間，但除此之外，還有所謂的 **盤前交易 （ 04:00 ~ 09:30 ）** 與 **盤後交易 （ 16:00 ~ 20:00 ）** 。 在 .18.01 版本中，XQ 提供了 **「美股支援盤前 / 盤後時段」** 的選項，讓使用者可以取得並運用這段時間的資訊。目前支援此功能的模組包括：技術線圖、策略雷達以及自動交易策略。

### 設定方式

- **技術線圖**： 使用者可以點選線圖上方頻率旁的 **時鐘圖示** 選項，來切換「正常交易區段」以及「包含盤前盤後的交易區段」。
[![](../images/b922442443.png)](https://www.xq.com.tw/wp-content/uploads/2025/12/01.png)[![](../images/3cf36bd95b.png)](https://www.xq.com.tw/wp-content/uploads/2025/12/02.png)

- **策略雷達 / 自動交易策略 / 回測**： 在設定介面中，使用者可以在 **設定頻率** 處，勾選是否要啟動「美股支援盤前盤後時段」。
**策略雷達** [![](../images/8e9e9ed01d.png)](https://www.xq.com.tw/wp-content/uploads/2025/12/03.png)**自動交易策略**[![](../images/08681984b6.png)](https://www.xq.com.tw/wp-content/uploads/2025/12/04.png)**回測** [![](../images/c1745f9f10.png)](https://www.xq.com.tw/wp-content/uploads/2025/12/05.png)

### 注意事項

- **委託限制**：美股盤前盤後時段的交易規則和一般交易時段不太相同，特別需要注意的是，此時段 **僅能使用限價單** 進行委託。
- **頻率限制**：美股盤前盤後的資訊 **只提供在分鐘頻率上** 。日頻率的資料不包含盤前盤後時段的資訊，因此若執行頻率設定為「日」，將無法勾選此選項。

## 2. 商品清單與函數的運用

在 .18.01 版本中，函數腳本的彈性大幅提升，現在可以透過 input 的方式來取得商品代碼。這意味著：

- 可以在執行腳本中使用 Group 函式取得一組商品清單，然後將此清單中的每個商品單獨傳入函數中，以取得該商品所需的資訊。
- 可以直接在執行腳本中傳入「固定」或透過 Input 輸入的商品代碼進函數，然後在函數中取得與該代碼相關的詳細清單及資訊。

### 應用情境 A ：函數中抓取傳入商品的資訊

舉例來說，我們撰寫一個函數 _getIdOpen ，用來回傳傳入商品的當日開盤價： input: _id(string); retval = GetSymbolField(_id, "Open", "D"); **支援的使用方式：**

- **固定商品**
 value1 = _getIdOpen("2330.TW"); 

- **執行商品**
value1 = _getIdOpen(symbol);

- **input 傳入的商品**
input: _id("2330.TW"); value1 = _getIdOpen(_id);

- **input 清單中的商品**
input: myGroup(Group); value1 = _getIdOpen(myGroup[1]);

- **group 清單中的商品**
group: myGroup(); myGroup = GetSymbolGroup("TSE23.TW", "成分股"); value1 = _getIdOpen(myGroup[1]);

### 應用情境 B ：函數中抓取被傳入商品的清單

舉例來說，我們撰寫一個函數 _getGroupVolumeSum ，用來回傳「傳入商品」其成分股的成交量加總： input: _id(string); group: myGroup(); var: _sum(0); myGroup = GetSymbolGroup(_id, "成分股"); _sum = 0; for value1 = 1 to GroupSize(myGroup) begin _sum += GetSymbolField(myGroup[value1], "Volume", "D", default := 0); end; retval = _sum; 在以下的情況是可以使用的: 固定商品 value1 = _getGroupVolumeSum("TSE23.TW"); 執行商品 value1 = _getGroupVolumeSum(symbol); input 傳入的商品 input: _id("TSE23.TW"); value1 = _getGroupVolumeSum(_id);**支援的使用方式：**

- **固定商品**
value1 = _getGroupVolumeSum("TSE23.TW");

- **執行商品**
value1 = _getGroupVolumeSum(symbol);

- **input 傳入的商品**
input: _id("TSE23.TW"); value1 = _getGroupVolumeSum(_id);

## 3. XS 編輯器支援警告功能

為了幫助使用者撰寫更穩健的腳本，在腳本編譯結束時，除了原本的「編譯成功 / 失敗」訊息外，現在還會加上 **「警告項目」** 。使用者可以藉由切換到 **編譯結果** 頁面來確認相關訊息。

- **警告的意義**：警告訊息 **不會影響編譯結果** （ 腳本仍可執行 ） ，而是提示腳本中所撰寫的內容可能會發生運算結果錯誤的部分、目前已無效，以及未來會被移除的語法。
- **建議做法**：使用者可以依據警告內容來改善腳本，避免未來發生可能的錯誤。
[##### 前一篇

營收領先指標：合約負債與營收的搭配實務——用 XS 腳本判讀訂單蓄水池動能](https://www.xq.com.tw/lesson/xspractice/advanced-applications-of-contractual-liabilities/)[##### 下一篇

SetTotalBar資料讀取範圍與腳本執行的關係](https://www.xq.com.tw/lesson/xspractice/資料讀取範圍與腳本執行的關係/)
