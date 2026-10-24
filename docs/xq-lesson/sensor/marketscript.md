---
source: https://www.xq.com.tw/lesson/sensor/marketscript/
title: 看盤用的腳本 - XQ 全球贏家
fetched: 2026-10-23
---

# 看盤用的腳本

###### 2025-07-24 10:25:40

複製連結

看盤是大學問，從盤面中挑股票作短線是另外一門大學問，我認識的大戶們，都是這兩門學問的高材生。我試著用XS把他們的智慧轉化為警示腳本，供大家參考。 過去在證券商及投信服務時，有些機會，認識了一些大戶級的朋友，也有機會坐在他們的貴賓室，陪他們看盤，我整理了不同大戶的看盤邏輯，可以分成看大勢及挑股票兩種不同的心法。 先說看大勢，基本上大戶只要今天的盤不會大跌就願意進場，我留意到他們在研判今天的盤會不會大跌，主要是看幾個部份： 1. 大架構是不是處於多頭走勢。 2. 人氣有沒有潰散。 3. 指標股能不能帶動追價 再來看挑股票，基本上一個原則：愈強愈好。從盤面上，則可以留意以下幾個特徵： 1. 一直在外盤成交 2. 掛委買的張數明顯積極 3. 佔大盤成交比重明顯成長 4. 開盤委買張數及均張都明顯變大 根據上述的邏輯，我寫了兩個腳本。 一個用來看大勢：condition1=false; condition2=false; condition3=false; if average(close,5)>average(close,22) then condition1=true;//週線在月線之上 if estvolume>average(volume,20) then condition2=true;//預估量高於20日均量 value3=q_UpLimitSecs;//漲停家數 value4=q_UpSecurities;//上漲家數 if value3>15 or value4>250 then condition3=true;//漲停夠多或上漲的夠多 if condition1 and condition2 and condition3 then ret=1;另一個用來挑可以繼續盯著看的股票：value1=q_InSize;//內盤量 value2=q_OutSize;//外盤量 value3=q_SumBidSize;//總委買 value4=q_BidAskDiff;//委買賣差(張) value5=q_CashDirect;//佔大盤成交比重 value6=q_BoughtLotsAtOpen;//開盤委買張數 value7=q_BoughtAverageAtOpen;//開盤委買均張 condition1=false; condition2=false; condition3=false; condition4=false; input:r1(1.5,"外盤量/內盤量的比值"); input:q1(1000,"外盤量-內盤量"); input:q2(300,"總委買下限"); input:q3(200,"委買賣差下限"); input:r2(20,"佔大盤成交比重成長下限"); input:q4(300,"開盤委買張數下限"); input:r3(20,"開盤委買均張成長率下限"); if value1<>0 then value8=value2/value1; if time>090500 and value8>=r1 then condition1=true;//外盤是內盤的 1.5倍以上 if value3>=q2 and value4>q3 then condition2=true;//總委賣夠大且買賣差也夠大 value9=average(value5,20); if value5>=value9*(1+r2/100) then condition3=true;//佔大盤成交值比重明顯回昇 if value6>q4 and value7>average(value7,20)*(1+r3/100) then condition4=true;//開盤委買張數及均張都夠迷人 if condition1 or condition2 or condition3 or condition4 then ret=1;在設定策略雷達時，我用第二個腳本，並且在參照參數的地方，我選擇加權指數，並且使用第一個腳本。 [![021901](../images/6bf6a1bf46.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/02/021901.png)透過這樣的設定，我們就可以在盤中只有大勢還可以作多時，把那些值得盯盤的股票，透過策略雷達，一檔檔送到我們面前來，這麼一來，應該就不必再盯著電視牆了。[##### 前一篇

運用趨勢性指標時，透過股性做確認趨勢是否成形](https://www.xq.com.tw/lesson/sensor/trendindicator/)[##### 下一篇

跨頻率多重濾網交易策略的設計](https://www.xq.com.tw/lesson/sensor/multifilters/)
