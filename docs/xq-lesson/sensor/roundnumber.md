---
source: https://www.xq.com.tw/lesson/sensor/roundnumber/
title: 股價越過整數關卡 - XQ 全球贏家
fetched: 2026-10-23
---

# 股價越過整數關卡

###### 2025-07-24 10:20:16

複製連結

我師父常說：「反常必有妖」。例如一檔股票平常都是十幾塊錢的，突然有一天站上20元，這時就有必要探討一下這家公司是不是基本面有什麼變化? 是不是接到大單？是不是產品價格看漲，毛利上昇。 以矽瑪為例，這檔股票近三個月多來都在 20元以下游走，最低還跌破15元，一旦見到2字頭，就代表有些情況在改變，結果是接大單，六月狂出貨，股價如今已站上30元。 [![072101](../images/b7317e20ab.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/07/072101.png)所以這種股價突破近期的整數關卡，實在是一個很值得我們重視的訊號。 為了在盤中就找出這樣的股票，我寫了一個腳本如下：input:period(10); setinputname(1,"維持原價位區間的天數"); value1=intportion(close/10); if trueall(value1[1]=value1[2],period) and value1>value1[1] and value1<10 then ret=1;這當中應用了一個叫做intportion的函數，這個函數是用來取整數用的，例如： value1=intportion(10.2); 那麼value1就等於10 我們用intportion這個函數，就可以知道close/10這個數字的有沒有更動，這個數字是股價的十位數，這個價位如果改變就代表十位數變了。 以上是挑十位數站上另一個整數區間的腳本，也可以用同樣的方法寫出跌破一個整數價位區間的腳本。 當個股發生這種事情，代表其供需有些改變，如果找出背後的原因，也許就找到一檔飆股了。[##### 前一篇

Larry Williams 短線交易秘訣](https://www.xq.com.tw/lesson/sensor/larrywilliams/)[##### 下一篇

私房交易策略介紹之旱地拔葱](https://www.xq.com.tw/lesson/sensor/scallion/)
