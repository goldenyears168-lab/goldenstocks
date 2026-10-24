---
source: https://www.xq.com.tw/lesson/xspractice/如何運用print指令來抓程式的臭蟲/
title: 如何運用Print指令來抓程式的臭蟲 - XQ 全球贏家
fetched: 2026-10-23
---

# 如何運用Print指令來抓程式的臭蟲

###### 2016-03-17 18:14:21

複製連結

大家在寫腳本的時候，有沒有常常跟小編一樣，遇到跑出來的結果跟原先預期不一樣的情形？今天小編就要來跟大家介紹一個抓臭蟲(Bug)的工具：這個工具就叫做Print指令。Print指令除了可以用來抓臭蟲之外，他也可以幫我們把腳本中運算完的數據輸出成文字檔，方便我們把運算結果儲存下來或是再做其他的應用。 首先來介紹基本的語法：Value1 = Average(Close, 5); Print("Date=", Date, "Close=", Close, "Value1", Value1); 在Print指令內，我們可以把想要印出來的欄位一股腦的寫在裡面，可以印出字串，也可以印出數值。 然後當腳本執行時，在XScript編輯器內的下方的「執行」視窗內，就可以即時看到印出來的數值。在這裡可以同時看到不同腳本執行時印出來的內容，商品欄位顯示的是指標目前的商品，或是策略雷達執行的商品，腳本就是**有使用到Print指定的腳本名稱**： [![Print](../images/1ef40496c6.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/Print.png)有了這個指令之後，以後如果腳本跑出來的結果跟你想的不一樣的話，那就直接在腳本內加上Print指令，把想要看到的數值印出來就方便許多了！ Print的結果，除了在XScript編輯器內可以看到之外，另外在XS的安裝目錄底下也會產生檔案，檔案的路徑是在C:\SysJust\XQ2005\XS\Print底下，檔案的名稱，預設是**腳本名稱_商品名稱.log:** [![PrintFolder](../images/d8bdefde0e.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/PrintFolder.png)如果使用到Print的腳本很多的話，那這個目錄內就會越來越亂。有辦法控制Print產出的檔案的位置或是名稱嗎？ 

### **密技1：指定輸出檔案路徑**

在Print指令的第一個參數位置，加上[file](http://xshelp.xq.com.tw/api.aspx?a=File&b=bif)(“檔案路徑")的參數，就可以指定print出來的檔案要放置的路徑。例如，我們想輸出成交價至C:\Print下，可以這樣寫：print(file("C:\print\"),date,symbol,close); 這個腳本輸出的檔案就會移到C:\Print下。 

### **密技2：指定輸出檔案名稱**

不過，既然可以指定輸出檔案路徑，當然也可以指定輸出的檔案名稱。一樣是透過[file](http://xshelp.xq.com.tw/api.aspx?a=File&b=bif)這個參數來指定輸出的檔案名稱。 例如，我們想把檔案名稱改為“**商品代碼.log**”，可以這樣寫：print(file("c:\Print\[Symbol].log"),date,symbol,close); [![PrintToFolder](../images/75b604a28f.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/PrintToFolder.png) 有沒有注意到我們上一個範例怎麼指定商品代碼的？用[Symbol]。只要是在[file](http://xshelp.xq.com.tw/api.aspx?a=File&b=bif)裡面寫了[Symbol]的位置，在輸出的時候就會自動用當時的商品代碼來取代。另外，你也可以使用[ScriptName]、[Freq]或[Date]。這些特殊字串在指定路徑或檔名的時候都可以用，這樣子我們可以輸出的變化就很多了。 如果我們想按商品代碼來整理數據，可以這樣寫：print(file("C:\print\[Symbol]\"),date,symbol,close); 這樣子可以把不同腳本輸出的資料都集中到個別商品的目錄當中。 [![PrintToSymbolFolder](../images/cdf6b1c90e.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/PrintToSymbolFolder.png)或者是，我們想把所有數據都輸出到同一個檔案print.log中，可以這樣寫：print(file("C:\print\print.log"),date,symbol,close); [![PrintToSameFile](../images/76fb164806.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/PrintToSameFile.png)有了這些功能，我們就可以控制檔案輸出的位置，不管是要把執行結果存下來，或是跟其他程式共用檔案，都可以輕易的辦到。 小編要在這裡告訴大家一個好消息，在XQ操盤高手 2.40 改版中，Print指令已可在回測與選股中心使用！由於回測與選股中心Print指令是從雲端上運行，故使用此功能跑回測時，執行回測的時間會較久一些，此特性請大家注意一下囉！ 回測或選股腳本撰寫好Print指令後，在回測設定畫面下方記得勾選「啟動腳本內Print指令」 [![](../images/62532ca761.png)](https://www.xq.com.tw/wp-content/uploads/2018/07/V640改_XS語法應用_如何運用Print指令來抓程式的臭蟲_回測.png)在選股中心畫面右上角記得勾選「啟動腳本內Print指令」，即可在執行回測或選股策略後，將所需要檢查的數據輸出，方便查找問題的原因。 [![](../images/5ef463fa45.png)](https://www.xq.com.tw/wp-content/uploads/2018/07/V640改_XS語法應用_如何運用Print指令來抓程式的臭蟲_選股.png)希望從此之後，大家都可以鎮定的面對臭蟲，Happy Coding and Happy Hunting。[##### 前一篇

探討變數序列的觀念：幾天前黃金交叉商品為例](https://www.xq.com.tw/lesson/xspractice/如何找出幾天前黃金交叉的商品：探討變數序列的/)[##### 下一篇

選股欄位放大鏡：談OutputField跟GetFieldDate這兩個函數](https://www.xq.com.tw/lesson/xspractice/選股欄位放大鏡：談outputfield跟getfielddate這兩個函數/)
