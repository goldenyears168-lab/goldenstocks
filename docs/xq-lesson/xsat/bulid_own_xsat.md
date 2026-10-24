---
source: https://www.xq.com.tw/lesson/xsat/bulid_own_xsat/
title: 如何建立自動交易策略 - XQ 全球贏家
fetched: 2026-10-23
---

# 如何建立自動交易策略

###### 2020-10-06 10:01:38

複製連結

[video/embed](https://www.youtube.com/embed/5JCpGGbB08o?rel=0&amp;wmode=transparent)

[![](../images/de01970d69.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/XSAT_03.png)

### **編寫交易腳本**

首先，在XS編譯器內，要設計自動交易時，腳本請選擇**「交易」**這個類型。 交易腳本的設計方式跟警示腳本非常的類似，支援的報價欄位、資料欄位也是一樣的，最主要的差異就是可以透過SetPosition這些語法來指示XS自動交易系統如何進行交易。 [![](../images/7c027cae02.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/如何建立自動交易策略1.png) 

### **自動交易策略**

交易腳本編譯完畢之後，我們就可以建立一個**「自動交易策略」**來執行這個腳本。 建立自動交易策略的方式有兩種。 **第一種**方式是直接在XS編輯器內，選擇交易腳本，然後按右鍵「加到自動交易」。 [![](../images/2ae976855f.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/如何建立自動交易策略3-1.png)第二種方式則是開啟「自動交易中心」，然後點選**「新增」**按鈕，或是從右鍵選單內選擇「新增自動交易」 [![](../images/f2f38b003f.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/如何5.png) 要建立一個自動交易策略時，必須填入以下設定： 執行商品 決定策略要執行哪些商品。 提供的選項包含：

- 可以選擇一個或是多個商品
- 可以選擇一個分類清單(例如我的某組自選股)
- 可以選擇一個XS選股法，執行前會先執行這個選股法，符合選股法的商品就會執行這個腳本
- 可以選擇帳號的庫存
- 可以選擇從外部檔案匯入欲執行的商品
[![](../images/1910f3bb90.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/XSAT_執行商品2.png)[![](../images/78ca52f336.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/如何6.png)

#### 執行頻率以及是否逐筆洗價

選擇策略執行時所讀取的商品資料頻率，以及是每一根K棒執行一次，還是是價格有異動時就執行一次。 [![](../images/d423b75f53.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/如何16-改.png) 資料讀取範圍以及是否使用歷史資料計算策略部位 透過這些參數，可以指定策略讀取歷史資料的範圍，以及是不是要使用歷史資料來模擬算出策略的部位，例如一個均線交易策略，可以使用歷史資料來算出策略目前是處在進場或是出場的位置，然後再接續當日的即時資料進行波段交易。 請參考[此篇文章](https://www.xq.com.tw/learn/xsat/parameters/)。 [![](../images/2b40576bd0.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/如何18-改.png) 

#### 交易帳號，以及是否連結帳號庫存

自動交易策略可以在不指定帳號的情形底下執行。此時策略執行時不會傳送委託單到券商端，而是靠著目前市場的即時行情來模擬策略的部位異動是否會成交。透過這樣子的設定方式，使用者可以在不啟動實單交易的情形下確認策略執行的邏輯是否符合預期。 [![](../images/e8dd94a01a.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/XSAT_帳號設定.png)或是，可以開啟帳號設定，指定證券或是期貨帳號，同時設定帳號的相關參數，之後XS自動交易執行時就會傳送真實的委託單到指定券商端。 更詳細的說明請參考[此篇文章](https://www.xq.com.tw/learn/xsat/parameters/)。 [![](../images/5578999a80.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/如何17-改.png) 

#### 交易安控

XS自動交易提供了部位，進場次數，以及交易金額等安控選項。一旦啟動安控之後，如果交易指令的內容違反安控的話，XS自動交易將不會執行這一個交易指令。 [![](../images/ac2eee1b81.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/如何15.png) 

#### 進出場設定

設定策略預設的買進價格以及賣出價格。腳本內的交易指令可以指定委託的價格，此時系統就會依照指定價格送單。如果交易指令內不指定價格的話，則會使用策略的預設買進/賣出價格來傳送委託。 使用者也可以決定萬一行情網路發生連線問題時，策略的執行會自動停止，還是等到行情連線恢復時繼續執行。 [![](../images/46b4ee0b5d.png)](https://www.xq.com.tw/wp-content/uploads/2020/10/如何19-改.png)到此為止，我們簡單的說明了自動交易策略的一些主要參數。在[下一篇文章](https://www.xq.com.tw/learn/xsat/parameters/)內我們會更詳細的介紹自動交易策略的所有參數以及執行交易時的行為。 另外關於自動交易中心的操作介面，也請大家參考[自動交易中心操作界面](https://www.xq.com.tw/learn/xsat/guideline/)這一篇文章。[##### 前一篇

自動交易的基本觀念](https://www.xq.com.tw/lesson/xsat/introduction/)[##### 下一篇

自動交易策略參數總覽](https://www.xq.com.tw/lesson/xsat/xsat_overview/)
