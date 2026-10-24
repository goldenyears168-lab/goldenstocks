---
source: https://www.xq.com.tw/lesson/sensor/multifilters/
title: 跨頻率多重濾網交易策略的設計 - XQ 全球贏家
fetched: 2026-10-23
---

# 跨頻率多重濾網交易策略的設計

###### 2025-07-24 10:26:39

複製連結

在設計交易策略時，常有跨頻率的需求，今天來跟大家討論，類似這樣的交易策略，要如何設計。 在程式交易這個領域裡，Alexander Elder的著作："Trading for a living"是大家常會拿來參考的一本書 [![trading for a living](../images/cf93b8201e.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/03/trading-for-a-living.png) 這本書有中譯本，名稱叫作《操作生涯不是夢》。 [![操作生涯不是夢](../images/c59d7b9421.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/03/操作生涯不是夢.png) 在這本書裡，Alexander 提出了一個觀念：叫作三重濾網交易系統(Triple Screen Trading System)，它的概念是把股價波動的趨勢分成三種，一是主趨勢，二是中期趨勢，三是短期趨勢，他用Tide(潮汐)，波浪(Wave)與小漣漪(Ripple)三種海浪的變化來代表這三種不同的趨勢，顧名思義，潮汐是長線的主要趨勢，漲潮時，海水會愈來愈逼近海岸，退潮時則海水會離海岸愈來愈遠。在漲潮退潮的過程中，海浪則是一波波打向岸邊，不斷地往前衝然後後退，漣漪則是海浪踫到不同地形地物時所激起的浪花，雖然漂亮而惹人注目，但對於大方向無關緊要。 Alexander的三重濾網交易系統的概念就是 1. 掌握主要趨勢 (週線) 2. 在短期超買超賣後尋找進場時機 (日線) 3. 從股價的波動中確認進場點 (盤中) 這樣的觀念，等於在一個交易系統中，同時使用了週線，日線及盤中洗價(例如60分鐘線)這樣的概念，這是一個跨三種頻率的交易策略，這樣的策略該怎麼建構呢？ 首先，我們先用選股來處理日線及日線以上頻率的條件，如週線及月線等，然後再把選股的結果拿去跑一個盤中洗價的日線或分鐘線的策略雷達腳本。 舉個例子，我們來建構一個符合下列三個條件的三重濾網交易系統： 1. 週線的移動平均線是多頭排列。 2. 日線KD進入超賣區 3. 盤中60分鐘線創新高 首先，我們先寫下兩個選股腳本：input:S1(6,"天期"); value1=average(close,s1); if close>value1 then ret=1;上面這個是移動平均線多頭排列，我們把這腳本應用在週線上。 下面這個則是KD進入超賣區：// KD指標, K值由下往上穿越D值 // input: Length(9), RSVt(3), Kt(3); variable: rsv(0), k(0), _d(0); SetBarBack(maxlist(Length,6)); SetTotalBar(maxlist(Length,6) * 4); SetInputName(1, "天數"); SetInputName(2, "RSVt權數"); SetInputName(3, "Kt權數"); Stochastic(Length, RSVt, Kt, rsv, k, _d); IF k<20 and _d<30 then ret=1; 我們把這個腳本用在日線上。 最後我們運用突破近期新高的腳本。input: Price(close); setinputname(1,"比較價別"); input: Length(10); setinputname(2,"近期期數"); if Price > highest(high[1] ,Length) then ret=1; 

然後把這個腳本用在60分鐘線 透過這三個腳本，我們串起了一個跨頻率的交易策略 首先我們運用前兩個腳本，挑出符合條件的股票。

[![030101](../images/98b24e5823.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/03/030101.png)然後再把這個選股策略取個名字叫「三重濾網之前兩重」，然後讓系統「每日自動執行」。 最後我們再把第三個腳本納入每日自動執行的策略雷達： [![030102](../images/42418a0969.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/03/030102.png)如此一來，符合這三重濾網的股票，在盤中就會出現在警示中心這個頁面了。 市場的老人常叫我們不要逆勢操作，也常要我們用長線保護短線，或是說看長作短，三重濾網交易系統的設計概念，跟市場老手們的想法不謀而合。 至於這三重交易系統應該用什麼指標，書上是建議第一重看長線，可以用趨勢型的指標，如MA，MACD等，第二重則建議用震盪指標，如KD，RSI等，第三重則是用突破型交易策略，因為這三者合在一起就可以找出在對的趨勢裡，短期整理後又重新往趨勢前進的股票。 要提醒大家的是，目前的XS系統，並沒有在同一個策略雷達腳本中，讓User處理跨頻率，而選股腳本又只能處理日線及以上的頻率，因此，目前我們目前在XS上執行三重濾網的交易策略時，前兩重一定要日線以上，例如我們無法執行一個用日線，60分鐘線及一分鐘線所構成的三重濾網交易系統，這還有賴嘉實的XS產品開發團隊的努力才能達成，目前是做不到的。 [##### 前一篇

看盤用的腳本](https://www.xq.com.tw/lesson/sensor/marketscript/)[##### 下一篇

主力一直在買的股票何時可以進場跟單？](https://www.xq.com.tw/lesson/sensor/whenenter/)
