---
source: https://www.xq.com.tw/lesson/xspractice/選股欄位放大鏡：談outputfield跟getfielddate這兩個函數/
title: 選股欄位放大鏡：談OutputField跟GetFieldDate這兩個函數 - XQ 全球贏家
fetched: 2026-10-23
---

# 選股欄位放大鏡：談OutputField跟GetFieldDate這兩個函數

###### 2016-03-17 19:48:06

複製連結

在[前一篇文章](https://www.xq.com.tw/learn/xspractice/printdebug/)內我們談到了如何運用Print語法列印腳本的執行狀態，讓臭蟲無所遁形。由於目前選股腳本內還沒有辦法支援Print的語法，所以今天就來跟大家談一下小編在面對選股腳本的臭蟲時常用的幾個技倆。 首先要介紹的是[OutputField](http://xshelp.xq.com.tw/index.aspx?HelpName=OutputField&group=GENERALFUNC)這個語法。 OutputField這個語法的主要功能，就是讓使用者可以在選股的結果畫面內增加想要顯示的欄位，欄位的內容可以是任意的數值，或是字串。所以除了可以拿來顯示商品的資料欄位之外，當然也可以拿來檢查腳本的執行狀態。 比方說我想要找EMA多頭排列的股票，可是怎麼找出來的怪怪的呢？這時候OutputField就派上用場了： [![EMACode](../images/f057419ef1.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/EMACode.png)程式碼內第12行到第16行是小編為了確認執行結果時寫上去了。透過OutputField的語法，小編把這一檔商品是否符合(Condition1)以及均線的數值(Value1, Value2, Value3)都**列印**出來。特別注意到第12行的ret=1。這一行等於是把所有的股票都選出來了，這樣子的話才好檢查資料是不是正確啊。 底下是跑出來的結果： [![EMATable](../images/0d6c5323f7.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/EMATable.png)有沒有看到紫色框線內的這四個欄位！這時候我們就可以利用排序的方式，甚至把結果匯出到Excel，然後再來做更深入的檢視。 除了腳本邏輯的問題之外，在設計選股方式時，小編也常常遇到需要計算財報/籌碼等欄位資料的需求。由於這些資料的公佈時間不一，而且單位也比較複雜(有的是億，有的是百萬，有的是百分比)，所以在確認腳本的執行邏輯前，小編通常也會利用OutputField把欄位印出來先看過一遍，再來決定腳本怎麼寫比較對。 例如說我想要估算最新一季的季營收時該怎麼寫？先把資料印出來再說：Value1 = GetField("月營收","M"); ret = 1; outputfield(1, GetFieldDate("月營收","M"), "最新月份"); outputfield(2, Value1); outputfield(3, Value1[1]); outputfield(4, Value1[2]); 透過OutputField把最近三期的月營收資料印出來，另外也透過[GetFieldDate這個函數](http://xshelp.xq.com.tw/index.aspx?HelpName=GetFieldDate&group=FIELDFUNC)把最新一期月營收的資料日期印出來。 [![GetFieldDateDemo](../images/34d1fc515f.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/GetFieldDateDemo.png)一樣的，我們還是利用ret=1，讓所有的商品都符合。一旦對資料的內容清楚之後，接下來我們就可以開始撰寫腳本的邏輯了。 我們想要估算季營收的方式是這樣子的：

- 同一季內如果已經公佈營收的，就用公佈的數值，
- 如果還沒有公佈的，就假設下個月的營收跟最新一期的營收是一樣的
這樣子單然是很簡單的估算方式，大家可以依照自己的經驗再做調整。底下就是估算季營收的程式碼，在這裡GetFieldDate就派上用場了:Var: mm(0); Value1 = getfield("月營收","M"); mm = Month(GetFieldDate("月營收","M")); if mm=1 or mm=4 or mm=7 or mm=10 then value2=Value1 * 3; if mm=2 or mm=5 or mm=8 or mm=11 then value2=Value1 * 2 + Value1[1]; if mm=3 or mm=6 or mm=9 or mm=12 then value2=Value1+Value1[1]+Value1[2]; // 預估獲利(單位=百萬) = 季營收 * 毛利率 - 營業費用 // value3 = value2 * GetField("營業毛利率","Q") - GetField("營業費用","Q"); OutputField(1, mm, "營收月份"); OutputField(2, Value1, "本月"); OutputField(3, Value1[1], "本月[1]"); OutputField(4, Value1[2], "本月[2]"); OutputField(5, value2, "預估單季營收(億)"); OutputField(6, value3 / 100, "預估單季本業獲利(億)"); ret = 1; 這段程式碼有點長，基本上就是運用GetFieldDate回傳的營收月份來決定該怎麼樣估算這一季的營收，然後在利用最新一期財報上的毛利率跟營業費用來估算是否會獲利。有興趣的朋友可以用這個腳本跑看看，記得要把執行的頻率設定成"月"喔。 在這篇文章內小編為大家介紹了OutputField，以及GetFieldDate這兩個函數。再搭配上[GetField這個函數](http://xshelp.xq.com.tw/index.aspx?HelpName=GetField&group=FIELDFUNC)的話，相信大家在遇到選股方面的臭蟲時，應該知道有哪些武器可以運用了吧！ [##### 前一篇

如何運用Print指令來抓程式的臭蟲](https://www.xq.com.tw/lesson/xspractice/如何運用print指令來抓程式的臭蟲/)[##### 下一篇

計算區間漲跌幅的自訂函數](https://www.xq.com.tw/lesson/xspractice/計算區間漲跌幅的自訂函數/)
