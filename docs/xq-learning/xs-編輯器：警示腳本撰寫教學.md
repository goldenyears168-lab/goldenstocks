---
source: https://www.xq.com.tw/learning/xs-編輯器：警示腳本撰寫教學/
title: XS 編輯器：警示腳本撰寫教學 - XQ 全球贏家
fetched: 2026-10-23
---

[學習地圖](https://www.xq.com.tw/learning/)XS 編輯器：警示腳本撰寫教學

# XS 編輯器：警示腳本撰寫教學

###### 2024-12-16 19:00:43

學習分類：[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)內容標籤：[XS 腳本](https://www.xq.com.tw/learning/?tags=xs_script)[策略開發](https://www.xq.com.tw/learning/?tags=trading_strategy)

複製連結

[![XS 編輯器：警示腳本撰寫教學](images/b48e231a78.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image2-46.png)

## **1. 為什麼要學習警示腳本？**

當你開始接觸腳本撰寫時，勢必已經遇到系統內建的腳本無法滿足你的情況，也就是時後來學習，如何把自己的想法寫成腳本。 警示腳本跟交易腳本是屬於比較相近的腳本，都可以支援使用者計算和判斷盤中資料。所謂的盤中資料就是，日頻率以下的價格變動 ( 如：Tcik、1分K、5分K等等 )，又或者是盤中的內外盤量、大中小單等逐筆搓合的成交紀錄。目前這些資料的運算，就必須仰賴警示腳本或交易腳本。 但為什麼「 警示腳本 」和「 交易腳本 」又要區分為二呢？ 簡單來說，執行警示腳本時，系統不會根據使用者的投入金額去計算交易單位。警示腳本只能告訴你，你的腳本是否符合條件，也就是給你進出場訊號。但交易腳本則可以根據你實際的投入金額，去換算交易單位，甚至是根據庫存成本發動交易指令，所以我們可以把警示腳本當作是交易腳本的入門。如果你能把警示腳本學好，相信撰寫交易腳本時就能更如魚得水。 

## **2. 如何學習警示腳本？**

在開始學習警示腳本前，我們可能要先對語法撰寫有一些基礎觀念，也就是搞清楚腳本的基本組成。基本上所有腳本都可以區分為三個區塊：宣告、敘事以及輸出。 

##### ▼ 黃色為宣告、紅色為敘事、紫色為輸出。

[![下圖為警示腳本的結構，黃色為宣告、紅色為敘事、紫色為輸出。](images/ddcf7a9e49.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image1-48.png)

### 2.1. 宣告

宣告的目的，是向程式說明你要用到哪些資料。 假設你要使用自訂變數，那你就必須使用 [Vars](https://xshelp.xq.com.tw/XSHelp/?HelpName=Vars&group=DECLARATION) 去宣告變數的預設值與型態。假設你要自訂參數，那你就必須使用 [Input](https://xshelp.xq.com.tw/XSHelp/?HelpName=Input&group=DECLARATION) 去宣告參數的預設值與名稱。 好比像上圖範例，由於我想要找出振幅 >= 振幅基準的股票，所以我向程式宣告，Datum 是振幅基準參數的意思，預設為 3。 在判斷振幅時，我就可以使用 Datum 來做為判斷用的參數。 

### **2.2. 敘述**

敘事的目的，是向程式說明你要怎麼計算和判斷。 一樣以上圖為例子，我想找出振幅和量能都符合條件的股票，所以我藉由 [Condition](https://xshelp.xq.com.tw/XSHelp/?HelpName=condition&group=DECLARATION)這個邏輯變數，幫我定義我的條件，這就是我想跟程式說明的判斷方式。 

### **2.3. 輸出**

當我們已經把「 要用什麼？」與「 如何使用？」的內容，通通寫到程式裡面，剩下的，就是我們希望程式要輸出什麼結果給我們。 這時我們就可以使用 [Ret](https://xshelp.xq.com.tw/XSHelp/?HelpName=Ret&group=DECLARATION) ( 其實它就是 Return value 的意思「 回傳值 」)，當我們條件都符合時，程式只要回傳 1 ，則表示程式正在執行的這個標的，是符合我們定義的條件，這時程式就會把這個結果回傳給我們。如上圖的例子一樣，當 Condition1 和 Condition2 都符合時，程式就會告訴我們。 

## **3. 動手寫寫看警示腳本**

### **3.1. 盤中形成布林開口**

布林軌道是很多老師或投資人喜歡使用的技術指標。 小編隨機在網路上參考其中一種布林軌道策略，它的邏輯如下： 

- 布林帶寬 <= 5%
- 窄帶寬維持超過 20 天
- 當天布林軌道開口
- 股價突破布林上軌
- 股價上漲 4 % 以上
我們來試寫看看這個邏輯，把它寫成腳本：// ***** 布林軌道策略 ***** SetBackBar(180); Input: Period(5, "窄帶寬定義"), Days(20, "維持期間定義"); Input: SD(2, "標準差"); // 判斷 布林帶寬 = 窄帶寬 Condition1 = BollingerBandWidth(Close, Days, SD, SD) <= Period; // 判斷 窄帶寬維持天數 >= 維持期間定義 Condition2 = CountIf(Condition1, Days) >= Days; // 判斷 當天布林軌道開口 Condition3 = BollingerBand(Close, Days, SD) > BollingerBand(Close, Days, SD)[1]; // 判斷 當天股價突破布林上軌 Condition4 = GetField("High", "D") > BollingerBand(Close, Days, SD); // 判斷 當天股價上漲超過 4 % Condition5 = (GetField("High", "D") - GetField("參考價", "D")) / GetField("參考價", "D") >= 0.04; IF Condition1 and Condition2 and Condition3 and Condition4 and Condition5 Then Ret = 1;

### **3.2. 突破近期大股東成本價**

大股東的動向總是讓人很想關注，畢竟當他們如果進場或加碼時，常常都保持著長期持有的決心和公司共進退。所以，小編試想出一種邏輯，由於執行庫藏股時，特定券商的大量買入，我們會將其視為關鍵券商 ( 大股東的券商 )，我們藉由計算這些分點買入的最低價平均，試圖建倉在他們成本價附近，和他們持有差不多成本的部位。 這個邏輯我們可以這樣寫：// ***** 突破近期大股東成本 ***** Input: Sample(100, "樣本張數定義"), Days(20, "計算期間"); // 計算 關鍵券商買賣超張數 > 0 在計算期間的張數加總 Value1 = SummationIf(GetField("關鍵券商買賣超張數", "D") > 0, GetField("關鍵券商買賣超張數", "D"), Days); // 判斷 關鍵券商買賣超張數在計算期間的張數加總 >= 樣本張數定義 Condition1 = Value1 >= Sample; // 計算 關鍵券商買賣超張數 > 0 在計算期間的最低價平均 (假設為大股東成本) Value2 = AverageIF(GetField("關鍵券商買賣超張數", "D") > 0, GetField("關鍵券商買賣超張數", "D"), Days); // 判斷 收盤價由下而上穿過大股東成本 Condition2 = GetField("Close" ,"D") Cross Over Value2; IF Condition1 and Condition2 Then Ret = 1;

## **4. 總結**

警示腳本的撰寫方向，其實還有非常多種變化，使用者可以盡量去嘗試各種可能性。至於要如何獲取這些想法，你可以透過各種學習管道 ( 例如看書、向大師請教，參加投資課程等 )，只要任何有助於你找到靈感的地方，或許都是下一個警示腳本誕生起源。有時候書籍或大師未必會講得非常明白易懂，這時必須仰賴你的理解能力，以及假設推測能力，去完整拆解這些知識。 以上就是警示腳本撰寫教學，若想要知道更多功能說明，歡迎點擊文中的內部連結。希望透過本文介紹，能讓你更了解 XQ 的功能應用。 我是 XQ 小編，我們下次見。

## 最新文章

[【功能更新】量化積木新增「等額（100 萬元）」交易模式！三大更新重點全解析](https://www.xq.com.tw/learning/xq-blocks-equal-value-trading-mode/)[【新手也能上手】量化積木支援「期貨商品」啦！](https://www.xq.com.tw/learning/xqblockfuture/)[【常見情境實戰篇】新手也能懂的期貨策略應用](https://www.xq.com.tw/learning/xqblockfuturestrategy/)[【新春活動】2026 金馬年開運紅包：新用戶專屬三大量化投資策略公開！](https://www.xq.com.tw/learning/xq2026threestragy/)[為什麼要做「量化交易」？避開散戶 3 大心態，打造你的投資 SOP](https://www.xq.com.tw/learning/quanttrading3retailinvestorminds/)

## 學習分類

[不分模組](https://www.xq.com.tw/learning/)[基本功能](https://www.xq.com.tw/learning/?category=basic)[尊榮會員](https://www.xq.com.tw/learning/?category=prime)[台股進階模組](https://www.xq.com.tw/learning/?category=tw_source)[量化積木模組](https://www.xq.com.tw/learning/?category=blocks_feature)[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)[盤後量化選股模組](https://www.xq.com.tw/learning/?category=selection_feature)[產業數據模組](https://www.xq.com.tw/learning/?category=industry_feature)[權證操作模組](https://www.xq.com.tw/learning/?category=warrant_feature)[美股分析模組](https://www.xq.com.tw/learning/?category=us_source)
