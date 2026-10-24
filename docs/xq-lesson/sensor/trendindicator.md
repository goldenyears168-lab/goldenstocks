---
source: https://www.xq.com.tw/lesson/sensor/trendindicator/
title: 運用趨勢性指標時，透過股性做確認趨勢是否成形 - XQ 全球贏家
fetched: 2026-10-23
---

# 運用趨勢性指標時，透過股性做確認趨勢是否成形

###### 2025-07-24 10:24:39

複製連結

當趨勢指標(如MA，MACD出現買進訊號時，我們最忐忑的是，這把下去不知道會不會又踫到盤整盤所出現的假訊號，偏偏老手們常說一年裡頭三個月漲，三個月跌，六個月盤，所以摃龜的機率還蠻大的。我試著在趨勢指標出現買進訊號時，再加上一個濾網，看看是否當上昇趨勢成形時，股性是否也明顯變好，如果兩者同步，代表脫離盤整的機會比較高。 基於上述的精神，我寫了一個腳本如下： //先預計兩個前提假設都不存在 condition1=false; condition2=false; //定義趨勢明確的條件 // MACD 黃金交叉 (dif向上穿越macd) // input: FastLength(12, "DIF短期期數"), SlowLength(26, "DIF長期期數"), MACDLength(9, "MACD期數"); variable: difValue(0), macdValue(0), oscValue(0); SetBarBack(maxlist(FastLength,SlowLength,6) + MACDLength); SetTotalBar((maxlist(FastLength,SlowLength,6) + MACDLength) * 4); MACD(weightedclose(), FastLength, SlowLength, MACDLength, difValue, macdValue, oscValue); if difValue Crosses Above macdValue then condition1=true; //定義有足夠的證據顯示股性出現轉變 input:day(66,"股性指標的移動平均天數"); input:ratio(10,"股性指標超出均值比率"); variable:count(0); value1=q_TotalTicks;//總成交次數 value2=average(value1,day); value3=GetField("強弱指標"); value4=average(value3,day); value5=GetField("外盤均量"); value6=average(value5,day); value7=GetField("主動買力"); value8=average(value7,day); value9=GetField("開盤委買"); value10=average(value9,day); count=0; if value1>=value2*(1+ratio/100) then count=count+1; if value3>=value4*(1+ratio/100) then count=count+1; if value5>=value6*(1+ratio/100) then count=count+1; if value7>=value8*(1+ratio/100) then count=count+1; if value9=value10*(1+ratio/100) then count=count+1; if count >=3 then condition2=true; //當上昇趨勢成形且股性明顯轉變時 if condition1 and condition2 then ret=1; 這個腳本就是使用了強弱指標，外盤均量，主動買力，總成交次數及開盤委買張數等五個指標做為衡量股性的工具，任何MACD出現黃金交叉的股票，都必須在上述五個股性指標中，有三個以上能夠出現比平均值高出一成以上的表現。 我試著用這樣的方法，來避開趨勢性指標出現太多假訊號，造成過多停損的後果。 當然也有朋友會透過選股的方式，先找出原本就認為比較有上漲機會的股票，這也是一種方式。 找形成上昇趨勢的股票，並且努力濾掉那些假訊號，是一個值得長期study的topic。 [##### 前一篇

幫手中持股設定賣出訊號出現時的通知機制](https://www.xq.com.tw/lesson/sensor/sellnotice/)[##### 下一篇

看盤用的腳本](https://www.xq.com.tw/lesson/sensor/marketscript/)
