---
source: https://www.xq.com.tw/lesson/indicator/修正式價量指標vptvolume-price-trend/
title: 修正式價量指標VPT(Volume price trend) - XQ 全球贏家
fetched: 2026-10-23
---

# 修正式價量指標VPT(Volume price trend)

###### 2025-06-30 15:43:47

複製連結

[![2016061701](../images/f290c73bfa.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/06/2016061701.png)在推廣XS的過程中，我有一些年紀大的，不大有學習動機跟能力的朋友，會直接嗆我說：「你說的這些我都聽攏謀，霧煞煞。你直接給我一個指標，看漲看跌清清楚楚的，我學不了那麼多。」 每次碰到這種，我都很想請他自己去買把槍，買個口罩及安全帽。 在我使用XS的過程中，我確實是設計過很多的指標，若要說有考慮到價跟量，又能指出多空趨勢，又要能夠濾掉那些因為一時衝動所造成的假訊號的，倒是有個指標。 這個指標我稱之為「修下式價量指標」。 這個指標的概念很簡單： 1. 先算出每天的Midprice。也就是每天的開高低收四個值的平均值。 2. 然後把這個值乘以每天價格的漲跌幅，再乘上當天的成交量。 3. 然後一路累加這個數值。 這樣計算出來的指標，在盤整時整條線會很貼近水平線，只有在漲跌趨勢很明確時才會上漲或是下跌，所以如果拿它跟它自身的一定週期移動平均線來對照，我們可以看到非常明確的進出場訊號。 我把上述的概念及算法用XS語法寫的指標如下： 移動平均線的天期就勞煩各位自己調整了。input:days(10); setinputname(1,"移動平均線天數"); var:tvp(0),mpc(0); mpc=(open+high+low+close)/4; if mpc[1]<>0 then tvp=tvp[1]+(mpc-mpc[1])/mpc[1]*volume else tvp=tvp[1]; value1=average(tvp,days); plot1(tvp,"修正型價量指標"); plot2(value1,"移動平均");我們來看兩個例子： [![061801](../images/0d34fc2406.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/06/061801.png)[![061802](../images/8435e104d2.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/06/061802.png)這兩檔股票都曾經歷經很長的整理期，tvp展現出來的就是長期橫向的水平走勢，我們每天看到小紅小黑，心裡七上八下，但從tvp指標來看，有沒有真的離開橫向盤整的格局，其實就很清楚了。[##### 前一篇

從細產業資金流向看類股輪動](https://www.xq.com.tw/lesson/indicator/從細產業資金流向看類股輪動/)[##### 下一篇

XS自動交易](https://www.xq.com.tw/lesson/xsat)
