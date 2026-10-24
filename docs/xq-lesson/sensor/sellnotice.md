---
source: https://www.xq.com.tw/lesson/sensor/sellnotice/
title: 幫手中持股設定賣出訊號出現時的通知機制 - XQ 全球贏家
fetched: 2026-10-23
---

# 幫手中持股設定賣出訊號出現時的通知機制

###### 2025-07-24 10:23:43

複製連結

這一波快速的拉回，到昨天指數跌了500點，個股跌超過15%的一大堆，小弟我很慘，有一檔股票跌了兩成，之所以這麼慘，是因為前幾天的下跌時我在忙，看它跌多了就想說等反彈再來賣，沒想到後來就一直破底。 為了防止這樣的慘案再次發生，我今天寫了一個腳本，準備搭配XQ新開放的自動下單機制，讓電腦幫我賣股票。 這個腳本如下://DMI賣出訊號 input:Length(14); setinputname(1,"計算期數"); variable: pdi(0), ndi(0), adx_value(0); DirectionMovement(Length, pdi, ndi, adx_value); if pdi<pdi[1] and ndi>ndi[1] and ndi crosses over pdi then ret=1 else begin //KD高檔死亡交叉 input: Length1(9), RSVt(3), Kt(3), HighBound(75); SetInputName(1, "計算期數"); SetInputName(2, "RSVt權數"); SetInputName(3, "Kt權數"); SetInputName(4, "高檔區"); variable: rsv(0), k(0), _d(0); Stochastic(Length1, RSVt, Kt, rsv, k, _d); if k>HighBound and k crosses under _d then ret=1 else begin //MTM由正轉負 input: Length2(10); SetInputName(1, "期數"); if momentum(close,Length2) crosses under 0 then ret=1 else begin //投信法人都賣超 if Open < Close[1] and Close < Close[1] and GetField("外資買賣超")[1]<0 and GetField("投信買賣超")[1]<0 then ret=1 else begin //MACD出現賣出訊號 input: FastLength(12), SlowLength(26), MACDLength(9); SetInputName(1, "DIF短期期數"); SetInputName(2, "DIF長期期數"); SetInputName(3, "MACD天數"); variable: difValue(0), macdValue(0), oscValue(0); MACD(weightedclose(), FastLength, SlowLength, MACDLength, difValue, macdValue, oscValue); if oscValue Crosses Below 0 then ret=1 else begin //RSI高檔死亡交叉 input: Length4(6); SetInputName(1, "短期期數"); input: Length5(12); SetInputName(2, "長期期數"); input: HighBound1(75); SetInputName(3, "高檔區"); value1=RSI(close,Length4); value2=RSI(close,Length5); if value1 crosses under value2 and value1>HighBound1 then ret=1 else begin //一舉跌破多根均線 input: shortlength(5); setinputname(1,"短期均線期數"); input: midlength(10); setinputname(2,"中期均線期數"); input: Longlength(20); setinputname(3,"長期均線期數"); variable: shortaverage(0); variable: midaverage(0); variable: Longaverage(0); shortaverage=Average(close,shortlength); midaverage=Average(close,midlength) ; Longaverage = Average(close,Longlength); if close crosses under shortaverage and close crosses under midlength and close crosses under Longaverage then ret=1; end; end; end; end; end; end;透過這個腳本，只要系統有出現以下七種狀況的其中一種，電腦就會自動通知我。 1. DMI賣出訊號 2. KD高檔死亡交叉 3. MTM由正轉負 4. 投信外資都賣超 5. MACD出現賣出訊號 6. RSI高檔死亡交叉 7. 一舉跌破多根均線 然後把這個腳本拿去設成策略雷達，設定的畫面如下： [![121501](../images/7c02549f6d.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/12/121501.png)執行商品設成庫存，如此一來，當出現賣出訊號時就會通知我了。 如果您的交易戶XQ有Support 的話，您也可以像我一樣，直接把單子下出去，不過目前這功能必須自行填交易張數，不能自動帶庫存。 [![1125](../images/cdf8911c98.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/12/1125.png)[##### 前一篇

盤中開始暴量的低PB股](https://www.xq.com.tw/lesson/sensor/lowpb/)[##### 下一篇

運用趨勢性指標時，透過股性做確認趨勢是否成形](https://www.xq.com.tw/lesson/sensor/trendindicator/)
