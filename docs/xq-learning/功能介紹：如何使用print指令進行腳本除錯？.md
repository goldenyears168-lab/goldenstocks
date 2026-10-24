---
source: https://www.xq.com.tw/learning/功能介紹：如何使用print指令進行腳本除錯？/
title: 功能介紹：如何使用Print指令進行腳本除錯？ - XQ 全球贏家
fetched: 2026-10-23
---

[學習地圖](https://www.xq.com.tw/learning/)功能介紹：如何使用Print指令進行腳本除錯？

# 功能介紹：如何使用Print指令進行腳本除錯？

###### 2024-12-11 09:41:13

學習分類：[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)內容標籤：[XS 腳本](https://www.xq.com.tw/learning/?tags=xs_script)[策略開發](https://www.xq.com.tw/learning/?tags=trading_strategy)[自動交易](https://www.xq.com.tw/learning/?tags=automated-_trading)

複製連結

## 

[![功能介紹：如何使用Print指令進行腳本除錯？](images/12b307a939.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/你的段落文字-3-5.png)

## **1. 摘要**

我們在寫腳本的時候，如果遇到執行結果跟原先預期不一樣？我們就可以使用Print指令進行除錯。 Print指令除了可以用來抓Bug之外，他也可以幫我們把腳本中運算完的數據輸出成文字檔，方便我們把運算結果儲存下來或是再做其他的應用。

## **2. 語法介紹**

Print的語法使用範圍很廣泛，從指標腳本、選股腳本、警示腳本、交易腳本皆可使用，首先來介紹基本的語法： Value1 = Average(Close, 5); Print("Date=", Date, "Close=", Close, "Value1", Value1);如上範例，在Print指令內，我們可以把想要印出來的欄位一股腦的寫在裡面，可以印出字串，也可以印出數值。 然後當腳本執行時，在XScript編輯器內的下方的「執行」視窗內，就可以即時看到印出來的數值。在這裡可以同時看到不同腳本執行時印出來的內容，商品欄位顯示的是指標目前的商品，或是策略雷達執行的商品，腳本就是**有使用到Print指定的腳本名稱**： ▼XScript編輯器內的下方的「執行」視窗內，就可以即時看到印出來的數值： [![XScript編輯器內的下方的「執行」視窗內，就可以即時看到印出來的數值：](images/26daca805e.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image1-39.png)有了這個指令之後，以後如果腳本跑出來的結果跟你想的不一樣的話，那就直接在腳本內加上Print指令，把想要看到的數值印出來就方便許多了！ Print的結果，除了在XScript編輯器內可以看到之外，另外在XS的安裝目錄底下也會產生檔案，檔案的路徑是在C:\SysJust\XQ2005\XS\Print底下，檔案的名稱，預設是**腳本名稱_商品名稱.log:** 另外，如果使用到Print的腳本很多的話，那這個目錄內就會越來越亂。我們可以指定Print產出的檔案的位置與名稱。 ▼檔案的路徑是在C:\SysJust\XQ2005\XS\Print底下 [![檔案的路徑是在C:\SysJust\XQ2005\XS\Print底下](images/33899c0a3c.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image2-38.png)

## **3. 指定輸出檔案路徑**

在Print指令的第一個參數位置，加上[**file**](http://xshelp.xq.com.tw/api.aspx?a=File&b=bif)(“檔案路徑")的參數，就可以指定print出來的檔案要放置的路徑。例如，我們想輸出成交價至C:\Print下，可以這樣寫：print(file("C:\print\"),date,symbol,close);這個腳本輸出的檔案就會移到C:\Print下。  

## **4. 指定輸出檔案名稱**

既然可以指定輸出檔案路徑，當然也可以指定輸出的檔案名稱。一樣是透過[**file**](http://xshelp.xq.com.tw/api.aspx?a=File&b=bif)這個參數來指定輸出的檔案名稱。 例如，我們想把檔案名稱改為“**商品代碼.log**”，可以這樣寫：print(file("c:\Print\[Symbol].log"),date,symbol,close);▼利用Print指令指定檔案路徑與檔案名稱 [![利用Print指令指定檔案路徑與檔案名稱](images/5107ca1d9c.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image3-36.png)有沒有注意到我們上一個範例怎麼指定商品代碼的？用[Symbol]。只要是在[**file**](http://xshelp.xq.com.tw/api.aspx?a=File&b=bif)裡面寫了[Symbol]的位置，在輸出的時候就會自動用當時的商品代碼來取代。另外，你也可以使用[ScriptName]、[Freq]或[Date]。這些特殊字串在指定路徑或檔名的時候都可以用，這樣子我們可以輸出的變化就很多了。 如果我們想按商品代碼來整理數據，可以這樣寫：print(file("C:\print\[Symbol]\"),date,symbol,close);▼這樣子可以把不同腳本輸出的資料都集中到個別商品的目錄當中。 [![這樣子可以把不同腳本輸出的資料都集中到個別商品的目錄當中。](images/54c428d8fc.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image5-31.png)或者是，我們想把所有數據都輸出到同一個檔案print.log中，可以這樣寫：print(file("C:\print\print.log"),date,symbol,close);▼所有數據都輸出到同一個檔案print.log [![所有數據都輸出到同一個檔案print.log](images/aed5415db8.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image8-21.png) 有了這些功能，我們就可以控制檔案輸出的位置，不管是要把執行結果存下來，或是跟其他程式共用檔案，都可以輕易的辦到。 小編要在這裡告訴大家一個好消息，在XQ操盤高手 2.40 改版中，Print指令已可在回測與選股中心使用！由於回測與選股中心Print指令是從雲端上運行，故使用此功能跑回測時，執行回測的時間會較久一些，此特性請大家注意一下囉！ ▼回測或選股腳本撰寫好Print指令後，在回測設定畫面下方記得勾選「啟動腳本內Print指令」 [![回測或選股腳本撰寫好Print指令後，在回測設定畫面下方記得勾選「啟動腳本內Print指令」](images/42e0415de1.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image4-36.png)▼在選股中心畫面右上角記得勾選「啟動腳本內Print指令」，即可在執行回測或選股策略後，將所需要檢查的數據輸出，方便查找問題的原因。 [![在選股中心畫面右上角記得勾選「啟動腳本內Print指令」，即可在執行回測或選股策略後，將所需要檢查的數據輸出，方便查找問題的原因。](images/d7fb1ac856.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image7-26.png)

## 5. 總結

這篇文章的重點在於 1.從代碼表中找到錯誤原因，並進行排除。 2.若無法自行排除時也提供了官方的反映管道。當然，需要大家一起按照步驟幫我們整理好資料再寄送給我們喔，這樣問題處理的速度才會加快。 以上就是 如何使用Print指令進行腳本除錯教學，我是 XQ 小編，我們下次見。 

## 最新文章

[【功能更新】量化積木新增「等額（100 萬元）」交易模式！三大更新重點全解析](https://www.xq.com.tw/learning/xq-blocks-equal-value-trading-mode/)[【新手也能上手】量化積木支援「期貨商品」啦！](https://www.xq.com.tw/learning/xqblockfuture/)[【常見情境實戰篇】新手也能懂的期貨策略應用](https://www.xq.com.tw/learning/xqblockfuturestrategy/)[【新春活動】2026 金馬年開運紅包：新用戶專屬三大量化投資策略公開！](https://www.xq.com.tw/learning/xq2026threestragy/)[為什麼要做「量化交易」？避開散戶 3 大心態，打造你的投資 SOP](https://www.xq.com.tw/learning/quanttrading3retailinvestorminds/)

## 學習分類

[不分模組](https://www.xq.com.tw/learning/)[基本功能](https://www.xq.com.tw/learning/?category=basic)[尊榮會員](https://www.xq.com.tw/learning/?category=prime)[台股進階模組](https://www.xq.com.tw/learning/?category=tw_source)[量化積木模組](https://www.xq.com.tw/learning/?category=blocks_feature)[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)[盤後量化選股模組](https://www.xq.com.tw/learning/?category=selection_feature)[產業數據模組](https://www.xq.com.tw/learning/?category=industry_feature)[權證操作模組](https://www.xq.com.tw/learning/?category=warrant_feature)[美股分析模組](https://www.xq.com.tw/learning/?category=us_source)
