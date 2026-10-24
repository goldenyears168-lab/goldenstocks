---
source: https://www.xq.com.tw/lesson/indicator/「自訂-k-線」說明/
title: 「自訂 K 線」說明 - XQ 全球贏家
fetched: 2026-10-23
---

# 「自訂 K 線」說明

###### 2025-06-30 15:30:59

複製連結

XQ 增加了讓指標腳本能夠繪製 K 棒的語法。 

### **函數語法**

 PlotK(序列編號, Open, High, Low, Close) PlotK(序列編號, Open, High, Low, Close, "序列名稱") 

### **函數說明**

 

- 序列編號是 1~999 的數值，與目前 XS Plot 的序列編號相同。
- 序列名稱是非必需的參數，如果不傳的話，預設的序列名稱為 "Plot" + 序列編號，例如 "Plot2"。
- 「Open, High, Low, Close」對應的是自訂 K 棒的開高低收。
 

### **函數的運作方式**

 PlotK 在每次腳本運算時，會在對應的橫軸位置 (也就是在運算的 K 棒位置上) 依據輸入的數值畫出 K 棒。 若該次腳本運算時沒有執行到 PlotK 的話，對應位置上就不會畫出 K 棒。而有運算到 PlotK，但又想將其清除的話，可以使用 NoPlot 函數。 

### **PlotK 繪圖設定**

 K 棒柱體和外框的顏色可以在繪圖設定中作修改，如下圖。 [![](../images/75090fe7e2.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/01-1.png)若希望將畫出的 K 線取代原有的主圖，可以在技術線圖設定中將主圖的 K 線改為連線圖，並將連線的顏色設定成和背景顏色相同。 [![](../images/da579830e1.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/02-2.png)這樣只要將所需的 XS 指標設定為主圖疊圖，就可以達到近似的效果。 [![](../images/a5f1841627.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/03-2.png) 

### **簡單範例 1 - 平均 K 線 (Heikin-Ashi)**

 var: ha_open(0), ha_high(0), ha_low(0), ha_close(0); if currentbar = 1 then ha_open = (open + close) / 2 else ha_open = (ha_open[1] + ha_close[1]) / 2; ha_close = (open + high + low + close) / 4; ha_high = maxlist(high, ha_open, ha_close); ha_low = minlist(low, ha_open, ha_close); PlotK(1, ha_open, ha_high, ha_low, ha_close, "平均K線"); 

### **簡單範例 2 - 不同商品的比較**

 如果希望可以將大盤的 K 棒和股票放在一起比較的話，可以透過自訂 K 棒的方式來達成。PlotK(1, GetSymbolField("TSE.TW", "Open"), GetSymbolField("TSE.TW", "High"), GetSymbolField("TSE.TW", "Low"), GetSymbolField("TSE.TW", "Close")); 由於大盤的價格和股票的價格相差甚遠不適合直接比較，可以將兩者除以相同 K 棒位置的價格，讓其有相同的起始點，這樣就很容易看出兩者漲跌差異。 //紀錄開始計算的第一根K棒開盤價 if currentBar = 1 then begin value100 = GetSymbolField("TSE.TW", "Open"); value101 = open; end; //將之後的價格都除以開盤價 value1 = GetSymbolField("TSE.TW", "Open") / value100; value2 = GetSymbolField("TSE.TW", "High") / value100; value3 = GetSymbolField("TSE.TW", "Low") / value100; value4 = GetSymbolField("TSE.TW", "Close") / value100; PlotK(1, value1, value2, value3, value4); value5 = open / value101; value6 = high / value101; value7 = low / value101; value8 = close / value101; PlotK(2, value5, value6, value7, value8);[##### 前一篇

PlotLine語法的介紹](https://www.xq.com.tw/lesson/indicator/plotline語法的介紹/)[##### 下一篇

「繪圖偏移」說明](https://www.xq.com.tw/lesson/indicator/「繪圖偏移」說明/)
