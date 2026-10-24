---
source: https://www.xq.com.tw/lesson/sensor/larrywilliams/
title: Larry Williams 短線交易秘訣 - XQ 全球贏家
fetched: 2026-10-23
---

# Larry Williams 短線交易秘訣

###### 2025-07-18 11:52:29

複製連結

[![r1](../images/3da1da3cae.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/05/r1.png)Larry Williams的著名著作《短線交易密訣》中提到一個非常重要的概念:當收盤上漲時,價格若處於強勢波動率收斂，當股價再創短其新高時，就是非常理想的進場點，反之則為空點。 這樣的概念可能有點抽象，讓我們來情境試想：有一檔股票正在漲，昨天的波動率假設是2，今天又上漲了，而且今天收盤價和最低價的距離只有不到1，這表示往下去的力道已經收斂掉了，當這樣的情況出現，如果股價突然衝過這兩天的高點，那就是多方要出擊了，這是個一決勝點。當然，這時後最好是記下這天的低點，用來當作停損使用！ 我們來看一下腳本：vars: _MarketPosition(0); Condition1 = Close > Close[1] and (Close-Low) <= 0.5*(High[1]-Low[1]); Condition2 = Close < Close[1] and (High-Close) <= 0.5*(High[1]-Low[1]); {整個策略非常單純,上面兩行僅判斷波動率,下面則是部位判斷與停損點的設定} if _MarketPosition < 1 then begin if condition1[1] and C > maxlist(H[1],H[2]) then begin plot4(C*1.01 ,"作多"); plot5(C*1.02);plot6(C*1.03);plot7(C*1.04); plot8(C*1.05);plot9(C*1.06);plot10(C*1.07); _MarketPosition=1; value1 =minlist(L[1],L[2]); end; if C cross over value2 and _MarketPosition=-1 then begin plot4(C*1.01 ,"回補"); _MarketPosition=0; end; end else if _MarketPosition >-1 then begin if condition2[1] and C < minlist(L[1],L[2]) then begin plot11(C*0.99 ,"作空"); plot12(C*0.98);plot13(C*0.97);plot14(C*0.96); plot15(C*0.95);plot16(C*0.94);plot17(C*0.93); _MarketPosition=-1; value2 = maxlist(H[1],H[2]) ; end; if C cross under value1 and _MarketPosition=1 then begin plot3(C*1.01 ,"出場"); _MarketPosition=0; end; end;可以從圖裡面看得到這個點容易成為決勝點。 改成警示的話腳本會變成下面，跑出來的結果各位可以參考對照是不是有這樣的現象：vars: _MarketPosition(0); Condition1 = Close > Close[1] and (Close-Low) <= 0.5*(High[1]-Low[1]); Condition2 = Close < Close[1] and (High-Close) <= 0.5*(High[1]-Low[1]); if _MarketPosition < 1 then begin if condition1[1] and C > maxlist(H[1],H[2]) then begin ret=1; value1 =minlist(L[1],L[2]); end; if C cross over value2 and _MarketPosition=-1 then begin _MarketPosition=0; end; end else if _MarketPosition >-1 then begin if condition2[1] and C < minlist(L[1],L[2]) then begin _MarketPosition=-1; value2 = maxlist(H[1],H[2]) ; end; if C cross under value1 and _MarketPosition=1 then begin _MarketPosition=0; end; end;[![r2](../images/c2ff42a53b.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/05/r2.png)[##### 前一篇

潛龍飛天](https://www.xq.com.tw/lesson/sensor/hiddendragon/)[##### 下一篇

股價越過整數關卡](https://www.xq.com.tw/lesson/sensor/roundnumber/)
