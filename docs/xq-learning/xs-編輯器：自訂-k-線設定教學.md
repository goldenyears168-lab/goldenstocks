---
source: https://www.xq.com.tw/learning/xs-編輯器：自訂-k-線設定教學/
title: XS 指標：自訂 K 線教學 - XQ 全球贏家
fetched: 2026-10-23
---

[學習地圖](https://www.xq.com.tw/learning/)XS 編輯器：自訂 K 線設定教學

# XS 編輯器：自訂 K 線設定教學

###### 2024-12-10 17:20:08

學習分類：[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)內容標籤：[XQ 設定](https://www.xq.com.tw/learning/?tags=xq_setting)[策略開發](https://www.xq.com.tw/learning/?tags=trading_strategy)

複製連結

[![XS 編輯器：自訂 K 線設定教學](images/993487e2d2.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/32.png)在技術分析中，K線是觀察市場趨勢與交易訊號的重要工具。XS 在此次改版中，新增了能夠透過指標腳本繪製自訂 K 線的功能，讓用戶可以根據不同的需求設計專屬的 K 線圖表，例如繪製平均 K 線 (Heikin-Ashi) 或比較不同商品的 K 線走勢。此功能的推出，進一步提升了技術分析的靈活性與可操作性，是進階投資者不可錯過的實用工具。

## **1. 為什麼要使用自訂 K 線呢？**

### **1.1 自訂化的技術分析**

- 量身打造專屬圖表：可根據個人交易需求設計特定的 K 線樣式，例如平均 K 線或比較多商品走勢。
- 突破預設限制：讓技術圖表不再局限於系統內建的 K 線呈現方式。

### **1.2 提高數據解讀效率**

- 簡化視覺對比：將多個商品的 K 線整合於同一圖表，清晰比較漲跌趨勢。
- 自定義顏色與樣式：通過調整繪圖設定，提升圖表的辨識度，讓關鍵資訊一目了然。

### **1.3 支援進階策略測試**

- 靈活運用腳本語法：提供強大的腳本支援，可實現如基於不同指標或參數生成的專屬 K 線圖表。
- 與其他指標整合：結合 XS 指標，讓自訂 K 線與其他技術分析工具協同運作，提升策略的可操作性。

### **1.4 更靈活的圖表應用**

- 替代原有主圖 K 線：可將自訂 K 線直接作為主圖，取代預設 K 線，呈現更符合需求的數據。
- 豐富的應用場景：適用於多商品對比、特殊指標應用與視覺化策略驗證等多種場景。
 

## **2. 如何使用自訂 K 線？**

### **2.1 函數語法：**

 PlotK(序列編號, Open, High, Low, Close) PlotK(序列編號, Open, High, Low, Close, "序列名稱") 

### **2.2 函數說明：**

- 序列編號是1~999的數值，與目前XS Plot的序列編號相同。
- 序列名稱是非必需的參數，如果不傳的話，預設的序列名稱為"Plot"+序列編號，例如”Plot2”。
- Open, High, Low, Close 對應的是自訂K棒的開高低收。
 

### **2.3 函數的運作方式：**

PlotK在每次腳本運算時，會在對應的橫軸位置 (也就是在運算的K棒位置上) 依據輸入的數值畫出K棒。 若該次腳本運算時沒有執行到 PlotK 的話，對應位置上就不會畫出K棒。而有運算到 PlotK，但又想將其清除的話，可以使用 NoPlot 函數。 

### **2.4 PlotK繪圖設定：**

K棒柱體和外框的顏色可以在繪圖設定中作修改，如下圖。 [![K棒柱體和外框的顏色可以在繪圖設定中作修改](images/b1e240b347.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image1-35.png)若希望將畫出的K線取代原有的主圖，可以在技術線圖設定中將主圖的K線改為連線圖，並將連線的顏色設定成和背景顏色相同。 [![若希望將畫出的K線取代原有的主圖，可以在技術線圖設定中將主圖的K線改為連線圖，並將連線的顏色設定成和背景顏色相同。](images/5461a25e11.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image3-32.png)這樣只要將所需的XS指標設定為主圖疊圖，就可以達到近似的效果。 

### **2.5 簡單範例1- 平均K線(Heikin-Ashi)：**

 var: ha_open(0), ha_high(0), ha_low(0), ha_close(0); if currentbar = 1 then ha_open = (open + close) / 2 else ha_open = (ha_open[1] + ha_close[1]) / 2; ha_close = (open + high + low + close) / 4; ha_high = maxlist(high, ha_open, ha_close); ha_low = minlist(low, ha_open, ha_close); PlotK(1, ha_open, ha_high, ha_low, ha_close, "平均K線"); 

### **2.6 簡單範例2- 不同商品的比較：**

如果希望可以將大盤的K棒和股票放在一起比較的話，可以透過自訂K棒的方式來達成。PlotK(1, GetSymbolField("TSE.TW", "Open"), GetSymbolField("TSE.TW", "High"), GetSymbolField("TSE.TW", "Low"), GetSymbolField("TSE.TW", "Close")); 由於大盤的價格和股票的價格相差甚遠不適合直接比較，可以將兩者除以相同K棒位置的價格，讓其有相同的起始點，這樣就很容易看出兩者漲跌差異。 //紀錄開始計算的第一根K棒開盤價 if currentBar = 1 then begin value100 = GetSymbolField("TSE.TW", "Open"); value101 = open; end; //將之後的價格都除以開盤價 value1 = GetSymbolField("TSE.TW", "Open") / value100; value2 = GetSymbolField("TSE.TW", "High") / value100; value3 = GetSymbolField("TSE.TW", "Low") / value100; value4 = GetSymbolField("TSE.TW", "Close") / value100; PlotK(1, value1, value2, value3, value4); value5 = open / value101; value6 = high / value101; value7 = low / value101; value8 = close / value101; PlotK(2, value5, value6, value7, value8); 

## **3. 總結**

XS 的自訂 K 線功能為用戶提供了一個靈活且強大的工具，無論是設計專屬的技術圖表，還是進行多商品的走勢對比，都能大幅提升數據分析的精確性與效率。同時，這項功能支援進階腳本語法，適合有一定基礎的投資者探索更多應用場景。不僅如此，自訂 K 線還能與其他技術指標無縫結合，幫助用戶更好地測試與優化交易策略，是提升交易決策的一大利器。我是 XQ 小編，我們下次見。

## 最新文章

[【功能更新】量化積木新增「等額（100 萬元）」交易模式！三大更新重點全解析](https://www.xq.com.tw/learning/xq-blocks-equal-value-trading-mode/)[【新手也能上手】量化積木支援「期貨商品」啦！](https://www.xq.com.tw/learning/xqblockfuture/)[【常見情境實戰篇】新手也能懂的期貨策略應用](https://www.xq.com.tw/learning/xqblockfuturestrategy/)[【新春活動】2026 金馬年開運紅包：新用戶專屬三大量化投資策略公開！](https://www.xq.com.tw/learning/xq2026threestragy/)[為什麼要做「量化交易」？避開散戶 3 大心態，打造你的投資 SOP](https://www.xq.com.tw/learning/quanttrading3retailinvestorminds/)

## 學習分類

[不分模組](https://www.xq.com.tw/learning/)[基本功能](https://www.xq.com.tw/learning/?category=basic)[尊榮會員](https://www.xq.com.tw/learning/?category=prime)[台股進階模組](https://www.xq.com.tw/learning/?category=tw_source)[量化積木模組](https://www.xq.com.tw/learning/?category=blocks_feature)[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)[盤後量化選股模組](https://www.xq.com.tw/learning/?category=selection_feature)[產業數據模組](https://www.xq.com.tw/learning/?category=industry_feature)[權證操作模組](https://www.xq.com.tw/learning/?category=warrant_feature)[美股分析模組](https://www.xq.com.tw/learning/?category=us_source)
