---
source: https://www.xq.com.tw/lesson/indicator/plotline語法的介紹/
title: PlotLine語法的介紹 - XQ 全球贏家
fetched: 2026-10-23
---

# PlotLine語法的介紹

###### 2025-06-30 15:28:45

複製連結

大家好，今天來跟大家介紹一個XS指標的新功能：PlotLine，用來繪製直線。

## 設計目的

大家都知道，可以透過指標腳本的Plot(...)語法，在每一根bar洗價時，畫出指標的數值，所以如果要畫一條直線的話，可以在每次Plot時都傳入同一個數值(水平線)，或是依照預期的斜率算出Y的數值，就可以產生直線的效果。 可是透過Plot語法，每次都是畫一個點，而且是畫在洗價的那一根bar上面。如果我希望繪製一條趨勢線，例如底下黃色的這條線： [![](../images/5ea3de2a4c.png)](https://www.xq.com.tw/wp-content/uploads/2023/10/PlotLine1.png)這條水平線，是這檔商品今年以來迄今的最高價格，我希望可以把這條線畫出來，當成是一個可能的壓力。可是這樣子的趨勢線，目前Plot語法是辦不到的：

- 我們想要計算的是從今年開始到昨日為止的最高價，所以腳本必須跑到昨日那一根bar，才能算出這個數值。
- 當腳本算出最高點是546.0時，這時候已經跑到昨日了。之後只能把這個數值畫在昨日的點，以及未來的點上面，可是沒有辦法往回畫！
 

## 基本使用方式

針對這樣子的需求，我們提供了一個新的PlotLine的語法，讓指標腳本可以更自由的繪製直線。 PlotLine的基本語法如下：PlotLine(序列編號, x1, y1, x2, y2) PlotLine(序列編號, x1, y1, x2, y2, "序列名稱")首先，必須傳入一個序列編號。序列編號的意義跟Plot語法是一樣的，代表這是這個指標的第幾條線，範圍從1到999。 請注意，一個指標腳本內可以同時呼叫Plot(...)以及PlotLine(...)，每次呼叫Plot或是PlotLine時都必須指定一個序列編號，這些序列編號會共用這個1到999的範圍，也就是說，每一個Plot/PlotLine的編號必須是不同的才不會產生問題。 接下來要傳入這條線的起點跟終點，每一個點包含兩個數值，第一個數值是這個點的X座標，第二個數值是這個點的Y座標。 X座標的單位是第幾根bar，從1開始，一直到CurrentBar。 Y座標的單位則是價位，也就是從Open、High、Low、Close等演算出來的數值。 除了傳入起點/終點的座標之外，也可以傳入序列名稱，用法與Plot語法是一樣的。 接下來我們來看一個簡單的範例：{ 從畫面第一點畫一條水平線，位置是目前最新的收盤價 } PlotLine(1, 1, Close, CurrentBar, Close, “最新價”); 我們把上面的指標命名為PlotClose，然後用主圖疊圖的方式把PlotClose加到圖上面： [![](../images/e563e1314c.png)](https://www.xq.com.tw/wp-content/uploads/2023/10/PlotLine2.png)跟一般的Plot一樣，可以選擇繪圖樣式等參數，不過如果是PlotLine的話，那麼形式就只能選擇線條，或是線段，不能選擇其他的樣式，畢竟我們想要畫的是一條線，對吧！ 按完成之後，接下來在技術分析圖上面就可以看到這個PlotLine的效果了： [![](../images/6dc780b6c8.png)](https://www.xq.com.tw/wp-content/uploads/2023/10/PlotLine3.png)

## 重畫的行為

 我們回過頭來思考一下這個指標腳本執行的過程。 我們知道指標腳本會跑過資料區間內的每一根bar，所以

- 在跑資料區間的第一根bar時，腳本呼叫PlotLine，起點是(1, 第一根bar的close)，終點是(1, 第一根bar的close)。
- 跑資料區間的第二根bar時，腳本呼叫PlotLine， 起點是(1, 第二根bar的close), 終點是(2, 第二根bar的close)。
- 跑第三根bar時, 繼續呼叫PlotLine，起點是(1, 第三根bar的close)，終點是(3, 第三根bar的close)。
- 一直到最新一根bar，這時候起點是(1, 最新一根bar的close), 終點是(CurrentBar, 最新一根bar的close)。
 那麼，指標呼叫了這麼多次PlotLine，傳入的起點/終點的位置都不一樣，為何只會畫出一條線呢？ 這個表示，當腳本呼叫PlotLine時，預設的行為會把同一個繪圖序列先前的PlotLine清掉，然後用這一次傳入的起點/終點來取代，所以腳本重複的呼叫PlotLine，最後顯示出來的是最後一次呼叫PlotLine的結果。 

## 線條跟線段的差異

 先前我們有提到PlotLine可以選擇以線條或是線段的方式來繪製，那這兩者有什麼區別呢？ 我們用底下的範例來說明：{ 畫出過去N日(不包含當日)的最高價/最低價 } input: days(5); if BarFreq <> "Min" then return; if Date <> Date[1] then begin value1 = Highest(GetField("High", "D")[1], days); value2 = Lowest(GetField("Low", "D")[1], days); PlotLine(1, 1, value1, CurrentBar, value1, "Max"); PlotLine(2, 1, value2, CurrentBar, value2, "Min"); end;我們把這個腳本存成「PlotMinMax」，然後把這個指標用主圖疊圖的方式放到0050的5分K線圖上面。如果我們選擇的是「線條」的話，呈現方式如下圖： [![](../images/3b2a269890.png)](https://www.xq.com.tw/wp-content/uploads/2023/10/PlotLine4.png)其中紅色的水平線代表的是最近5日(不含最新一日，也就是不含5/19日)的最高價，綠色的水平線代表的是最近5日的最低價。 同樣的指標，我們如果選擇的是「線段」的話，那麼呈現的方式如下圖： [![](../images/409a4ef81d.png)](https://www.xq.com.tw/wp-content/uploads/2023/10/PlotLine5.png)注意到這兩張圖的差別是，當使用線段時，5/19日的資料都沒有畫出來。 我們來看看腳本的寫法，好理解為何會有這樣子的差異：

- 因為是統計過去N日的資料，所以只在換日的時候做一次統計(if Date <> Date[1])。
- 算出區間高點跟區間低點之後，上面的腳本也只會在換日的時候才呼叫PlotLine。
 依照先前的討論，每一次呼叫PlotLine時，都會清掉先前的PlotLine重畫。所以以紅色這條線來說，最後一次呼叫PlotLine時傳入的起點是(1, 5月14~5/18日的最高價)，終點則是(5/19的起點，5月14~5/18日的最高價)。 如果選擇的是「線段」的話，那麼繪圖的範圍就依照指定的起點/終點。 可是如果選擇的是「線條」的話，那麼系統就會自動依照這條線的斜率，把「終點」一路延伸到最新的一根bar，所以當5/19日有新的K棒出現時，這兩條PlotLine也會自動的畫出來，產生類似射線的效果。 請注意：

- 所謂的起點跟終點，是以傳入的X座標來決定，起點永遠在終點的左邊，也就是說PlotLine(1, 5, 5, 10, 10)跟PlotLine(1, 10, 10, 5, 5)的效果是一樣的。
- 選擇「直線」的話，只會自動延伸終點到最新一根bar，不會自動從PlotLine的起點往左邊延伸。
 

## 如何繪製多個區段

 趨勢線未必就是一條直線，也有可能是由多個線段所組合而成的。例如底下這張圖裡面顯示的zigzag指標： [![](../images/2efb7518f4.png)](https://www.xq.com.tw/wp-content/uploads/2023/10/PlotLine6.png)如果要畫出藍色的這條線，肯定必須要呼叫好幾次PlotLine，每次繪製一個線段，然後把這些線段接起來。可是先前我們有提到如果重新呼叫PlotLine時，先前繪製的線會被塗掉，那我們要怎麼呼叫PlotLine才能達到這樣子的效果呢？ 如果我們希望呼叫PlotLine時，保留目前已經畫好的線，不要去清除他的話，那麼可以使用add這個參數：PlotLine(序列編號, x1, y1, x2, y2, add:=1) PlotLine(序列編號, x1, y1, x2, y2, "序列名稱", add:=1)add是一個optional的參數，預設的數值是0，表示每次呼叫時就清除先前的線條。可是如果我們傳入1的話，那麼效果就是不清除先前的線條，而是把這次想要畫的線條疊加上去。透過這個參數，我們就可以畫出上圖zigzag指標的效果了！ 底下是一個使用add參數的範例： [![](../images/2192a7cf96.png)](https://www.xq.com.tw/wp-content/uploads/2023/10/PlotLine7.png) 這個指標腳本使用add參數，在每一日的分K圖上面畫出了CDP指標的五條線。程式碼如下：if BarFreq <> "Min" then RaiseRunTimeError("請跑分鐘頻率"); var: HH(0), LL(0), CC(0); var: CDP(0), AH(0), NH(0), NL(0), AL(0); if Date <> Date[1] then begin // 換日 // HH = GetField("High", "D")[1]; LL = GetField("Low", "D")[1]; CC = GetField("Close", "D")[1]; CDP = (HH + LL + 2*CC) / 4; AH = CDP + HH - LL; NH = 2*CDP - LL; NL = 2*CDP - HH; AL = CDP - (HH - LL); PlotLine(1, CurrentBar, CDP, CurrentBar, CDP, "CDP", add:=1); PlotLine(2, CurrentBar, NH, CurrentBar, NH, "NH", add:=1); PlotLine(3, CurrentBar, NL, CurrentBar, NL, "NL", add:=1); PlotLine(4, CurrentBar, AH, CurrentBar, AH, "AH", add:=1); PlotLine(5, CurrentBar, AL, CurrentBar, AL, "AL", add:=1); end;在每一天換日時，計算當日的CDP指標，同時使用add:=1的參數把今日的CDP指標疊加到圖上面。 底下是指標的繪圖設定方式： [![](../images/75344c1ef6.png)](https://www.xq.com.tw/wp-content/uploads/2023/10/PlotLine8.png)因為CDP是水平線，他的數值在每一天開盤時就可以決定，所以我們不需要每一根bar都呼叫PlotLine，可以使用「線條」的方式，讓CDP指標自動延伸到今天最新的一根bar。 另外眼尖的讀者應該有注意到在這個指標腳本內呼叫PlotLine時，起點跟終點的X座標都是一樣的，例如起點是(CurrentBar, CDP)，終點也是(CurrentBar, CDP)。當起點跟終點的X座標都是同一個數值時，系統會認定這就是一條水平線(斜率=0)，所以腳本只需要在換日時呼叫PlotLine一次就可以把這一整天的CDP指標都畫好了。 

## 其他進階用法

 

### 可以清掉線條嗎?

 如果腳本在某些情境底下想要把先前畫好的直線清掉的話(類似NoPlot(..))，那可以怎麼做呢？if condition1 then PlotLine(1, 0, 0, 0, 0); // 清掉這條線可以像上面這樣子寫，把起點/終點都設定成0，就可以清掉目前的線了。 

### 起點終點重疊的定義

 先前我們有提過呼叫PlotLine時可以透過add參數來保留先前已經畫好的直線。可是如果這一次傳入的座標與先前畫好的直線重疊的話，那會發生什麼事情？ 舉例：

- 先呼叫 PlotLine(1, 1, 100, 50, 100)
- 再呼叫 PlotLine(1, 25, 200, 100, 200, add:=1)
 首先我們畫出了(1, 100) ~ (50, 100)的一條水平線。 接下來我們要畫(25, 200) ~ (100, 200)的另外一條水平線，同時希望保留先前的直線。 由於XS的PlotLine的機制底層還是是透過Plot的方式來實做，所以每一個X位置，只會畫出一個點。 當繪圖的X範圍遇到重疊時(例如上面從x = 25到x=50的區塊)，目前的作法就是讓後面的PlotLine覆蓋先前的PlotLine。所以上面這兩次PlotLine就會產生以下的結果：

- (1, 100) ~ (24, 100)的水平線，加上
- (25, 200) ~ (100, 200)的水平線
 

### PlotLine的限制

在其他與XS類似的產品內，也有類似PlotLine的功能。例如Multichart有TL的[相關語法](https://www.multicharts.com/trading-software/index.php?title=Category:Trendline_Drawing)，TradingView也有[Lines的語法](https://www.tradingview.com/pine-script-docs/en/v5/concepts/Lines_and_boxes.html#lines)。 XS的PlotLine與這些語法最大的差異是，PlotLine的底層還是是一種Plot，所以會受到以下的限制：

- 每個PlotLine必須指定一個繪圖序列，而且這個繪圖序列的數值必須是一個constant，不能動態的定義。
- 每個PlotLine的繪圖序列，在一個X位置上，只會有一個Y值，所以沒有辦法繪製垂直線。
 使用者在設計腳本時，請留意這些限制，我們也會持續努力，讓PlotLine的功能可以更加的強大。[##### 前一篇

XS指標的錯誤代碼說明](https://www.xq.com.tw/lesson/indicator/xs指標的錯誤代碼說明/)[##### 下一篇

「自訂 K 線」說明](https://www.xq.com.tw/lesson/indicator/「自訂-k-線」說明/)
