---
source: https://www.xq.com.tw/lesson/sensor/whenenter/
title: 主力一直在買的股票何時可以進場跟單？ - XQ 全球贏家
fetched: 2026-10-23
---

# 主力一直在買的股票何時可以進場跟單？

###### 2025-07-24 10:27:29

複製連結

為什麼主力買超這麼多，股票還是不會漲？ 主力買超的股票我衝進去買，會不會剛好被主力出貨？ 自從主力買超這樣的資料隨處可以取得之後，這兩個問題常常被提出來。 我寫過幾個腳本，想要找出主力佈局後開始拉高的第一時間點，以下是我的研究過程。 首先我想先找出主力買超且股價近期還沒有大漲過的股票，我用的選股條件如下： [![022603](../images/079bbc0763.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/02/022603.png)首先我先定義那些股票是屬於主力買超： 1. 近2天每天都買超超過500張 或者是 2. 近2天每天買超張數合計佔成交量5%以上 或者是 3. 最近10天加起來買超超過2000張 其次，我只挑中小型的股票，所以我設了一個條件把股本超過100億元的股票都濾掉。 最後，為了避免被主力逢高出貨，所以我濾掉那些主力買超但十天內漲幅超過一成的股票，這個部份我是寫了一個選股的腳本如下：input:period(10,"計算區間"); input:ratio(10,"最低漲跌幅"); if close[period-1]<>0 then begin if (close/close[period-1]-1)*100<ratio then ret=1; end; outputfield(1,(close/close[period-1]-1)*100,1,"區間漲跌幅"); 

以昨天為例，昨天符合這些條件的股票共有45檔，我根據主力買超張數排序，超過100張的名單如下：[![020604](../images/733fbec092.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/02/020604.png)

接下來我用符合這些條件的股票，用盤中的腳本去跑，希望找到那些主力即將開始拉昇的股票，以下的這個腳本，是我覺得不錯用的：if barfreq <> "Min" or Barinterval <>1 then RaiseRuntimeError("請設定頻率為1分鐘"); input:n1(10); setinputname(1,"開盤連續幾分鐘"); variable:BarNumberOfToday(0); if Date <> Date[1] then BarNumberOfToday=1 else BarNumberOfToday+=1;{記錄今天的Bar數} if Date =currentdate then begin variable: idx(0),tTime(0); tTime=0; for idx = 0 to n1-1 begin if Close[idx] > Close[idx+1] then tTime+=1; {推升時記1} end; if tTime >=n1*0.6//1分鐘線大部份的時間都在上漲 and q_PriceChangeRatio < 5 {漲幅仍在5%內} and Timediff(Time,Time[BarNumberOfToday-1],"M") =n1{分鐘} {離開盤第1個價10分鐘內} then ret=1; end;這個腳本是去尋找今天開盤前十分鐘，十根一分鐘線裡，至少六根是上漲的。為什麼這麼寫呢? 因為通常主力買超的數據在前一晚公佈後，今天開盤一定會有散戶去搶，主力如果還沒有要開始拉，開盤的時候應該是會站在賣方，打下來之後再繼續吃，如果開盤十分鐘走勢超過六成的時間都上漲，很有可能就是今天要表態了。 當然這個腳本如果在大盤開高時，容易出現錯誤的訊號，但如果在大盤開低或開平時，就很值得參考了。 以上是我的研究，報告完畢。 [##### 前一篇

跨頻率多重濾網交易策略的設計](https://www.xq.com.tw/lesson/sensor/multifilters/)[##### 下一篇

XS選股](https://www.xq.com.tw/lesson/filter)
