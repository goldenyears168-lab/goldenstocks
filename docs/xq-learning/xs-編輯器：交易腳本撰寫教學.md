---
source: https://www.xq.com.tw/learning/xs-編輯器：交易腳本撰寫教學/
title: XS編輯器：交易腳本撰寫教學 - XQ 全球贏家
fetched: 2026-10-23
---

[學習地圖](https://www.xq.com.tw/learning/)XS 編輯器：交易腳本撰寫教學

# XS 編輯器：交易腳本撰寫教學

###### 2024-12-17 11:47:26

學習分類：[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)內容標籤：[XS 腳本](https://www.xq.com.tw/learning/?tags=xs_script)[策略開發](https://www.xq.com.tw/learning/?tags=trading_strategy)[自動交易](https://www.xq.com.tw/learning/?tags=automated-_trading)

複製連結

[![XS編輯器：交易腳本撰寫教學](images/5ff74b6d13.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image6-1.png)

## **1. 了解交易語法：新手必讀**

在撰寫交易腳本之前，熟悉交易語法是非常重要的一步。所有交易語法都可以在 [**XS 函數搜尋**](https://xshelp.xq.com.tw/XSHelp/) 找到，但面對大量的語法，我們可能會覺得不知從何下手。因此，我們整理出幾個**必學交易語法**，幫助您快速入門。

### **1.1. 重要交易語法介紹**

以下是一些在撰寫自動交易腳本時經常使用的函數語法：

- Position： 表示預期達到的目標部位，可以視為策略設定的委託單。
- Filled： 交易帳號中實際成交後的持有部位。
- SetPosition： 交易策略預計建立的目標部位。
- Market： 市價送出委託單。
- AddSpread： 調整委託單價格，增加檔位。
掌握這些語法，您就能開始撰寫腳本，實現自動交易的核心邏輯，接下來我們再對上述語法進行深入說明。 ▼XQ函數庫 > 內建函數 >交易函數在這個路徑下，可以找到XQ可以使用的交易函數。 [![XQ函數庫 > 內建函數 >交易函數在這個路徑下，可以找到XQ可以使用的交易函數。](images/f0d955c782.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image1-4.png)

## **2.交易腳本的語法結構**

▼ 黃色為宣告、綠色為敘述、紅色為輸出。 [![黃色為宣告、綠色為敘述、紅色為輸出。](images/aa8de0bb9a.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image3-3.png)

### **2.1. 宣告**

宣告的目的，是向程式說明你要用到哪些資料。 假設你要使用自訂變數，那你就必須使用 [Var](https://xshelp.xq.com.tw/XSHelp/?HelpName=Vars&group=DECLARATION) 去宣告變數的預設值與型態。假設你要自訂參數，那你就必須使用 [Input](https://xshelp.xq.com.tw/XSHelp/?HelpName=Input&group=DECLARATION) 去宣告參數的預設值與名稱。 以上圖為例，我們想製作技術指標RSI黃金交叉買進、死亡交叉賣出相關策略，我用[Input](https://xshelp.xq.com.tw/XSHelp/?HelpName=Input&group=DECLARATION)去宣告短期RSI及長期RSI的預設數值及保留數值調整的彈性；另外，我用[Var](https://xshelp.xq.com.tw/XSHelp/?HelpName=Vars&group=DECLARATION)去定義變數名稱，方便我後續取得的RSI數值，要放置在哪個變數名稱中。 

### **2.2. 敘述**

敘事的目的，是向程式說明你要怎麼計算和判斷。 以上圖為例，我描述了變數要放置甚麼數值後，也描述了RSI黃金交叉及RSI死亡交叉，這就是我想跟程式說明的判斷方式。 

### **2.3. 輸出**

定義程式應該輸出什麼結果。 以交易腳本來說，最後的行為都是要導向下單這個行為。所以是使用[SetPosition](https://xshelp.xq.com.tw/XSHelp/?HelpName=SetPosition&group=TRANSACTIONFUNC)這個函數在嘗試建立部位的價格、數量。

## **3. 部位控管Position 與 Filled 的差異**

在了解部位控制時，很多人會想：**為什麼要分成 Position 和 Filled來分別控管？用一個語法控管不行嗎？**我們透過一個簡單的多單進場範例來說明。

### **3.1 程式範例：多單進場與出場**

以下是一段模擬的腳本範例：假設我想在台積電800元以下承接，1200元以上自動賣出，則我們可以這樣表示。 condition1 = Close < 800; condition2 = Close >= 1200; if Position = 0 and filled = 0 and condition1 then begin SetPosition(1, MARKET); { 以市價買進 } end; if Position >= 1 and filled >= 1 and condition2 then begin SetPosition(0, addspread(close,+2));{以收盤+2檔賣出} end; 在上述語法中，我們看到進出場前皆有判斷Position和Filled的部位。 我們來實際模擬以上code進出場過程中Position 與 Filled部位的變動。

### **3.2 模擬執行過程：Position 與 Filled 的變動**

進場： 一開始起始部位Position和Filled皆為0，但在Condition1條件成立後，Position部位會變為1，並送出委託單，而這時Filled部位還是0，需等到買進成交單打回來後，Filled部位才會變為1**。** [![一開始起始部位Position和Filled皆為0，但在Condition1條件成立後，Position部位會變為1，並送出委託單，而這時Filled部位還是0，需等到買進成交單打回來後，Filled部位才會變為1。](images/5a9ae09df4.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image2-3.png)出場： 起始部位Position和Filled皆有進場的部位因此為1，但在賣出條件Condition2成立時，Position部位會先變為0，並送出賣出委託單，直到送出的委託單成交後，Filled部位才會變為0。 [![起始部位Position和Filled皆有進場的部位因此為1，但在賣出條件Condition2成立時，Position部位會先變為0，並送出賣出委託單，直到送出的委託單成交後，Filled部位才會變為0。](images/5b7cd7226a.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image5-1.png)根據上述範例，我們可以理解Position是預期達到的目標部位，所以當條件成立後Positon就會開始變動。而Filled則是實際成交後的部位。 我們也可以把Position直接想成下的委託單，Filled則是實際庫存部位。 以生活化的例子來看： 假設明天蘋果有特價，一顆20元，因此我預計明天去菜市場買3顆20元的蘋果，這時Position就會變為3，Filled=0，明天我實際將3顆蘋果買回家後，這時Filled= 3, Position = 3，而買到的價是20元。 

### **3.3 只寫Filled可以嗎?**

當然可以!但是，因為尚未有Position來控制目標部位，因此在尚未成交之前會重複的傳入相同部位的不同價格，也就是下了委託單後，若條件再次成立但是委託單尚未成交，會在同一個委託書上「改價」。 若是希望在每次條件成立時，都可以下到最新價格時，就可以單使用Filled來判斷進出場就好。 若是拿上面的買蘋果來舉例： 我想要買蘋果3顆，但是還沒預計什麼時候去買價格也不知道，因此價格會根據市場價格一直變動，直到我們實際買進的那天。所以我們可以改寫程式碼如下表示： **if** Filled = 0 **and** **Condition1** **then** SetPosition(1); **if** Filled = 1 **and** **Condition2** **then** SetPosition(0);

### **3.4 只寫Position可以嗎?**

若是單只寫Positon判斷進出場的話，當進場條件condition1符合就會下買進委託單了，但如果買進委託沒有成交，且出場條件condition2符合條件時就會把原先買進的那筆委託單刪單，所以當進出場條件容易觸發時，且買進委託尚未成交時會反覆出現買進委託，刪除委託的動作，所以**不建議**只用Position判斷進出場。 **if** position = 0 **and** **Condition1** **then** setposition(1); **if** position = 1 **and** **Condition2** **then** setposition(0); 

### **3.5 編寫交易腳本**

有了上述觀念後，若我們需要編寫交易腳本，可以在XS編譯器內，腳本請選擇**「交易」**這個類型。 交易腳本的設計方式跟警示腳本非常的類似，支援的報價欄位、資料欄位也是一樣的，最主要的差異就是可以透過SetPosition這些語法來指示XS自動交易系統如何進行交易。 ▼腳本類型 >選擇 「交易」。 [![腳本類型 >選擇 「交易」。](images/30e49725aa.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image4-3.png)▼若我們有不熟悉的語法，在XScript編輯器中也提供了預設64組範例腳本讓您參考 [![若我們有不熟悉的語法，在XScript編輯器中也提供了預設64組範例腳本讓您參考](images/fde02dd080.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image7-1.png)

## 4. 總結

這篇文章的重點在於掌握 Position（預期目標部位）與 Filled（實際成交部位）的使用，並透過核心語法如 SetPosition、Market 等實現精準且靈活的自動交易策略。。 以上就是交易腳本的撰寫說明，我是 XQ 小編，我們下次見。 

## 最新文章

[【功能更新】量化積木新增「等額（100 萬元）」交易模式！三大更新重點全解析](https://www.xq.com.tw/learning/xq-blocks-equal-value-trading-mode/)[【新手也能上手】量化積木支援「期貨商品」啦！](https://www.xq.com.tw/learning/xqblockfuture/)[【常見情境實戰篇】新手也能懂的期貨策略應用](https://www.xq.com.tw/learning/xqblockfuturestrategy/)[【新春活動】2026 金馬年開運紅包：新用戶專屬三大量化投資策略公開！](https://www.xq.com.tw/learning/xq2026threestragy/)[為什麼要做「量化交易」？避開散戶 3 大心態，打造你的投資 SOP](https://www.xq.com.tw/learning/quanttrading3retailinvestorminds/)

## 學習分類

[不分模組](https://www.xq.com.tw/learning/)[基本功能](https://www.xq.com.tw/learning/?category=basic)[尊榮會員](https://www.xq.com.tw/learning/?category=prime)[台股進階模組](https://www.xq.com.tw/learning/?category=tw_source)[量化積木模組](https://www.xq.com.tw/learning/?category=blocks_feature)[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)[盤後量化選股模組](https://www.xq.com.tw/learning/?category=selection_feature)[產業數據模組](https://www.xq.com.tw/learning/?category=industry_feature)[權證操作模組](https://www.xq.com.tw/learning/?category=warrant_feature)[美股分析模組](https://www.xq.com.tw/learning/?category=us_source)
