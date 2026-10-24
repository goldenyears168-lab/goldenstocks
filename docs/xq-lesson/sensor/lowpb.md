---
source: https://www.xq.com.tw/lesson/sensor/lowpb/
title: 盤中開始暴量的低PB股 - XQ 全球贏家
fetched: 2026-10-23
---

# 盤中開始暴量的低PB股

###### 2025-07-24 10:22:43

複製連結

行情走到這邊，我發現不少異軍突起的股票，都是屬於那種低股價淨值比的，低PB的股票我們用XS選股每天隨時都可以挑出來，但不是每一檔低PB的股票都會漲，特別是如果盤中我們想要在這類股票一發動時就進場，那麼我們最好能夠在盤中就掌握低PB的股票人氣回籠的徵兆。 怎麼判斷呢？ 預估量是一個方法，這些低PB的股票之所以低PB，很大的原因是因為乏人問津。一般乏人問津的股票，其具體的表現就是沒有量，所以當一檔低PB的股票預估當日成交量會比十日均量高出三成且主要都是外盤成交時，就很值得留意了。 那麼我們怎麼透過XS來在盤中找到這種股票呢？ 首先我們可以用XS選股挑出低PB的股票： [![102002](../images/bf7800173b.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/10/102002.png)接下來就要拿這341檔來跑預估量會暴增的策略 先前我們有介紹過預估量的函數，其腳本如下：variable:CloseTime(133000); // 收盤時間 variable: OpenMinutes(270);//一天有幾分鐘開盤 variable: MinutestoClose(270); //到收盤還有幾分鐘 variable: Length(20); //用過去幾天日資料計算 variable: AvgDayVol(0); //平均日量 variable: AvgMinVolinDay(0); //平均分鐘量 variable: LeftVol(0); //剩餘時間的估計量 variable: estVol(0); //最終估計量 AvgDayVol = average(V,Length); AvgMinVolinDay = AvgDayVol/OpenMinutes; //過去這段時間每分鐘的平均量 MinutestoClose = Timediff(CloseTime,currenttime,"M"); //現在到收盤還有幾分鐘 LeftVol = MinutestoClose *AvgMinVolinDay;// 剩餘時間乘上每分鐘均量 = 盛夏時間可能有多少量 if ( barfreq ="D") then //是日線才會對 begin if Date =currentdate then //今天才回估量 estVol =volume + LeftVol //估計量 等於 現在的日總量 加上 剩下時間估計的量 else estVol =v; //過去的話就直接回實際的量 end; estvolume =estVol;有了這個預估量的函數，我們就可以很容易的寫出預估量暴增的腳本value1=q_InSize;//當日內盤量 value2=q_OutSize;//當日外盤量 if estvolume > average(volume[1],10)*1.3//預估量比十日均量多出三成 and value2>value1//外盤量比內盤量多 then ret=1;接下來我們就用這個腳本來設定策略雷達： [![102001](../images/f1d8f1335d.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/10/102001.png)如上圖，我們在指定範圍設成PB<0.8的選股法，然後把"觸發設定"設成日內單次觸發，這樣一來，我們就可以讓電腦在盤中只要有那一檔PB<0.8的個股預估量爆增，馬上透過警示中心通知我們。 如下圖： [![102003](../images/955d05b280.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/10/102003.png)以今天為例，我大約9:28分啟動這個策略，到中午11:30左右為止，符合低PB且預估量暴量的股票如下： [![102004](../images/dad31c80a9.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/10/102004.png)從上圖我們可以發現，雖然今天指數到11:28分是下跌的，但上面大部份的股票是上漲的。 以在名單上的康那香為例， 09:47的時候，康那香符合預估量爆增的條件，那時候的康那香價位是12.15，到了11點之後，康那香的股價已經來到12.85。 [![102005](../images/41f355258c.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/10/102005.png)透過這個策略我們確實可以在這些低PB乏人問津股票出量的第一時間就掌握進場的時機，但在這邊還是要跟大家說明的是，這些股票能走多遠，還是要看基本面有沒有變化而定。 以在名單中的昱晶為例，到第二季財報為止，它的每股淨值是29.7，如果以股價低於PB X 0.8來算，當股價跌破23.8時，它就符合低PB的條件，我們從K線圖上可以發現，必須要K線也配合是屬於中長紅的格局時，才是多頭人氣重新匯聚的徵兆。 [![102006](../images/5739e6a777.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/10/102006.png) 如果是在大漲之後，或者是在下降趨勢之中，則不應輕取妄動。 整體來說，這個低PB暴量的策略如果挑出來的股票又符合多次到底而破，整理後突破等策略，其後市看漲的機率就更高了。 [##### 前一篇

私房交易策略介紹之旱地拔葱](https://www.xq.com.tw/lesson/sensor/scallion/)[##### 下一篇

幫手中持股設定賣出訊號出現時的通知機制](https://www.xq.com.tw/lesson/sensor/sellnotice/)
