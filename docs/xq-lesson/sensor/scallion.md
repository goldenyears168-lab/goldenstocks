---
source: https://www.xq.com.tw/lesson/sensor/scallion/
title: 私房交易策略介紹之旱地拔葱 - XQ 全球贏家
fetched: 2026-10-23
---

# 私房交易策略介紹之旱地拔葱

###### 2025-07-24 10:21:32

複製連結

 眼尖的各位，若一檔股票，一段時間量縮橫向整理後的中長紅，後市往往還有高點。 是的，這是自從我開始從事程式交易以來，最靠譜的交易策略 這個策略有三個原則 1. 超過十個交易日股價橫向整理 2. 在整理期間成交量明顯萎縮 3. 以中長紅的姿態突破整理 4. 突破時帶著比先前多出不少的成交量 過去為了找到這樣的股票，我曾經寫過也用過幾個策略 例如我常推薦大家使用的多次到頂而破策略雷達input:HitTimes(3); setinputname(1,"設定觸頂次數"); input:RangeRatio(1); setinputname(2,"設定頭部區範圍寬度%"); input:Length(20); setinputname(3,"計算期數"); variable: theHigh(0); theHigh = Highest(High[1],Length); //找到過去其間的最高點 variable: HighLowerBound(0); HighLowerBound = theHigh *(100-RangeRatio)/100; // 設為瓶頸區間上界 variable: TouchRangeTimes(0); //期間中進入瓶頸區間的低點次數,每跟K棒要歸0 //回算在此區間中 進去瓶頸區的次數 TouchRangeTimes = CountIF(High[1] > HighLowerBound, Length); if TouchRangeTimes >= HitTimes and ( q_ask> theHigh or close > theHigh) then ret=1;要使用這個策略，可以直接在雲端策略中心中訂閱多次到頂突破這個策略即可 [![101707](../images/af55035ad3.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/10/101707.png)訂閱後每天可以從警示中心這一頁盤中即時跳出符合這個策略的股票 [![101708](../images/4353e13538.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/10/101708.png)這個腳本的最大問題是只考慮價沒有考慮量，而且只考慮向上突破而沒有考慮先有是否有量縮盤整。 後來我有寫過另一個叫股票箱突破的策略雷達 腳本如下:input:length(12); value1=NthHighest(1,high,length); value3=nthhighest(3,high,length); value4=Nthlowest(1,low,length); value5=nthlowest(3,low,length); if value1[1]<=1.03*value3[1] and value5[1]<=value4[1]*1.03 and value1[1]<=value4[1]*1.1 and close>value1[1] then ret=1;這個腳本昨天出現訊號的股票如下圖 [![101709](../images/b34ef0475e.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/10/1017091.png)大家不妨參考對照未來的走勢。 除了上述兩個腳本之外，各位也可以根據上面的那四個定義，另外再寫出一個符合上述邏輯的策略，我覺得只要follow這四個精神，應該可以找到短多的股票，而且，勝率真的夠高。 這個交易策略背後的邏輯是，當股價脫離原先的盤整區域，代表原來的多空平衡已被破壞，一根中長紅代表的是多方正式撃潰空方的最後防線，接下來就是長驅直入了，我們在這時候進場，圖的就是這種空頭全面潰敗，多頭乘勝追擊追撃的過程，這個過程很重要的是 1. 要一確定中長紅就跟進去。 2. 最好挑那種對峙很久的，這種一被撃潰就後防空虛，多頭可以一路追撃。 3. 最好上面沒有太多解套賣壓的。 根據上述這三個原則，各位可以把這個策略的腳本改寫的更精準。[##### 前一篇

股價越過整數關卡](https://www.xq.com.tw/lesson/sensor/roundnumber/)[##### 下一篇

盤中開始暴量的低PB股](https://www.xq.com.tw/lesson/sensor/lowpb/)
