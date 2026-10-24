---
source: https://www.xq.com.tw/lesson/filter/用xs寫籌碼集中度的選股策略/
title: 用XS寫籌碼集中度的選股策略 - XQ 全球贏家
fetched: 2026-10-23
---

# 用XS寫籌碼集中度的選股策略

###### 2025-06-02 10:58:08

複製連結

網友想要直接有籌碼集中度的欄位可以拿來用，在XS改版前遠水救不了近火，所以我就寫了一個自訂函數來算籌碼集中度，並且拿它來選那些籌碼集中度高且股價還沒有大漲的股票。 我的函數寫法如下：input:days(numericsimple,"計算天數"); value1=GetField("主力買賣超張數"); value2=summation(volume,days); value3=summation(value1,days); if value2<>0 then value4=value3/value2*100; bs=value4;我把這個函數的名稱取為BS，是Buysell(買賣超)的簡寫，各位可以取不同的名字。 接下來我用這個函數寫了下面這個腳本： input:days(10,"天數"); input:ratio(5,"最低百分比"); input:percent(10,"漲幅上限"); value1=bs(days); if close[days-1]<>0 and close>close[days-1] then value2=(close-close[days-1])/close[days-1]*100; if value1>ratio and value2<percent then ret=1; outputfield(1,value1,0,"籌碼集中度"); outputfield(2,value2,1,"區間漲幅%");這個腳本可以找出在N日內，籌碼集中度高於多少百分比，且漲幅低於多少百分比的股票。 我用這個腳本來跑今天的資料，挑出的股票如下圖： [![021801](../images/e539a2d7f2.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/02/021801.png) 如果想要更改區間，只要點選下圖的調整參數按鈕。 [![021802](../images/d5d56dcc03.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/02/021802.png)就會跳出以下的視窗，調整不同的天期或百分比了。 [![021803](../images/8e6137071a.png)](https://www.xq.com.tw/xstrader-content/uploads/2016/02/021803.png)[##### 前一篇

到底什麼樣的股票可以拿來當存股的標的](https://www.xq.com.tw/lesson/filter/到底什麼樣的股票可以拿來當存股的標的/)[##### 下一篇

年底前投信作帳的可能標的](https://www.xq.com.tw/lesson/filter/年底前投信作帳的可能標的/)
