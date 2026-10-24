---
source: https://www.xq.com.tw/learning/xs-函數：如何運用函數繪製填色區塊？/
title: 如何運用函數繪製填色區塊 - XQ 全球贏家
fetched: 2026-10-23
---

[學習地圖](https://www.xq.com.tw/learning/)XS 函數：如何運用函數繪製填色區塊？

# XS 函數：如何運用函數繪製填色區塊？

###### 2024-12-10 17:24:48

學習分類：[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)內容標籤：[XQ 設定](https://www.xq.com.tw/learning/?tags=xq_setting)[策略開發](https://www.xq.com.tw/learning/?tags=trading_strategy)

複製連結

 [![XS 函數：如何運用函數繪製填色區塊？](images/8902d15a5d.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/新增標題-21.png)XQ 提供了能夠在線圖上指定區域填色的功能，例如 KD 指標的超買超賣區間，讓使用者能夠更簡單的辨識指標間的範圍。

## **1. 使用「指定區域填色」有什麼好處呢？**

「指定區域填色」功能在技術分析中提供了直觀、清晰的視覺效果，能幫助投資者快速解讀數據、發現機會並做出更準確的決策。以下是主要的好處：

 **好處分類** | **說明** |

 突顯關鍵區域，提升可讀性 | - 直觀標示指標區間（如超買超賣、支撐壓力區域）。 - 降低數據過載感，強調重要數據點。 |

 提高分析效率 | - 快速判斷市場狀況，迅速定位圖表中的關鍵點。 - 減少重複計算與閱讀時間，提升決策效率。 |

 支援策略回測與優化 | - 視覺化策略效果，標示交易執行區域。 - 透過填色分布，便於調整策略參數。 |

 多場景靈活應用 | - 技術指標分析（如 RSI、KD 的超買超賣區域）。 - 趨勢策略檢測，顯示突破支撐/壓力的區間。 - 跨市場比較，發現潛在交易機會。 |

 視覺化決策輔助 | - 降低人工失誤，自動準確繪製區域。 - 增強交易信心，直觀驗證策略假設。 |

 

## **2. 如何使用「指定區域填色」？**

### **2.1. 函數語法：**

 PlotFill(序列編號, vFrom, vTo) PlotFill(序列編號, vFrom, vTo, "序列名稱") 

### **2.2. 函數說明：**

- 第一個參數是設定序列編號，會是 1~999 的數值，與目前 Plot 的序列編號相同。
- 第二個和第三個參數分別是當根 K 棒要填色的開始和結束點。
- 第四個參數是設定序列的名稱，為選填的參數。若沒有設定的話預設會是 " Plot "+序列編號。

### **2.3. 函數的運作方式：**

在第一次執行 (K棒X1) PlotFill(1, Y1_U, Y1_D) 時，此函數會在 Y1_U 到 Y1_D 間畫出一條垂直的直線，此時並不會有填色的效果。 [![在第一次執行 (K棒X1) PlotFill(1, Y1_U, Y1_D) 時，此函數會在 Y1_U 到 Y1_D 間畫出一條垂直的直線，此時並不會有填色的效果。](images/b4abd1a1e2.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image2-35.png)在第二次執行 (K棒X2) PlotFill(1, Y2_U, Y2_D) 時，且與兩次執行間沒有使用 NoPlot(1) 的狀況下，此時系統會將上次 (X1) 與此次 (X2) 的開始點 (Y1_U和Y2_U) 以及結束點 (Y1_D和Y2_D) 之間用直線連起來，搭配上 Y1_U, Y1_D 和 Y2_U, Y2_D 兩條垂直線，四條線連起來的區塊A填色。 [![在第二次執行 (K棒X2) PlotFill(1, Y2_U, Y2_D) 時，且與兩次執行間沒有使用 NoPlot(1) 的狀況下，此時系統會將上次 (X1) 與此次 (X2) 的開始點 (Y1_U和Y2_U) 以及結束點 (Y1_D和Y2_D) 之間用直線連起來，搭配上 Y1_U, Y1_D 和 Y2_U, Y2_D 兩條垂直線，四條線連起來的區塊A填色。](images/fdbb6c1541.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image5-29.png)在第三次執行 (K棒X3) PlotFill(1, Y3_U, Y3_D) 時，且與第二次執行間沒有使用 NoPlot(1) 的狀況下，系統會將 Y2_U, Y3_U 和 Y2_D, Y3_D 連接，搭配上 Y2_U, Y2_D 和 Y3_U, Y3_D 兩條垂直線的區塊B填色。 [![在第三次執行 (K棒X3) PlotFill(1, Y3_U, Y3_D) 時，且與第二次執行間沒有使用 NoPlot(1) 的狀況下，系統會將 Y2_U, Y3_U 和 Y2_D, Y3_D 連接，搭配上 Y2_U, Y2_D 和 Y3_U, Y3_D 兩條垂直線的區塊B填色。 ](images/ccff5ebf88.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image4-33.png)如果兩次呼叫此函數間有希望不塗色的部份的話，可以在區間內呼叫 NoPlot 函數來取消填色。 需注意開始點和結束點的順序，會影響畫出的區塊。 舉例來說： [![需注意開始點和結束點的順序，會影響畫出的區塊。](images/bf94acda9d.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image3-33.png)左圖第一次執行是 PlotFill(1, 100, 200)，第二次執行是 PlotFill(1, 100, 200)。 右圖第一次執行是是 PlotFill(1, 100, 200)，第二次執行是 PlotFill(1, 200, 100)。 可以看到因為第二次的開始結束點交換了，導致填塞區塊也跟著交錯。 此外 PlotFill 在查價視窗中不會顯示任何數值，只有單純的填色效果。 若需要知道填色區塊邊緣的數值的話，可以搭配其他的繪圖函數來做使用。 

### **2.4. 簡單範例- KD 的超買超賣區塊填色：**

 //計算KD指標，並將KD的超買超賣區間填色 input: Length(9, "天數"), RSVt(3, "RSVt權數"), Kt(3, "Kt權數"); input: OverBought(80, "超買"), OverSold(20, "超賣"); variable: rsv(0), k(0), _d(0); Stochastic(Length, RSVt, Kt, rsv, k, _d); Plot1(k, "K(%)"); Plot2(_d, "D(%)"); //當K值在超買線以上時 if k > OverBought then begin //將由超買線開始到K值結束的區間填色 PlotFill(3, OverBought, k); NoPlot(4); //當K值在超賣線以下時 end else if k < OverSold then begin //將由超賣線開始到K值結束的區間填色 PlotFill(4, OverSold, k); NoPlot(3); end else begin //當K值位於中間時，不要填色 NoPlot(3); NoPlot(4); end;

## **3. 總結**

「指定區域填色」功能是提升技術分析效率與準確度的有力工具。透過視覺化數據標示，投資者能更快速地捕捉市場機會、驗證策略效果，並在操作中降低錯誤風險，實現交易效率的全面提升。我是 XQ 小編，我們下次見。

## 最新文章

[【功能更新】量化積木新增「等額（100 萬元）」交易模式！三大更新重點全解析](https://www.xq.com.tw/learning/xq-blocks-equal-value-trading-mode/)[【新手也能上手】量化積木支援「期貨商品」啦！](https://www.xq.com.tw/learning/xqblockfuture/)[【常見情境實戰篇】新手也能懂的期貨策略應用](https://www.xq.com.tw/learning/xqblockfuturestrategy/)[【新春活動】2026 金馬年開運紅包：新用戶專屬三大量化投資策略公開！](https://www.xq.com.tw/learning/xq2026threestragy/)[為什麼要做「量化交易」？避開散戶 3 大心態，打造你的投資 SOP](https://www.xq.com.tw/learning/quanttrading3retailinvestorminds/)

## 學習分類

[不分模組](https://www.xq.com.tw/learning/)[基本功能](https://www.xq.com.tw/learning/?category=basic)[尊榮會員](https://www.xq.com.tw/learning/?category=prime)[台股進階模組](https://www.xq.com.tw/learning/?category=tw_source)[量化積木模組](https://www.xq.com.tw/learning/?category=blocks_feature)[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)[盤後量化選股模組](https://www.xq.com.tw/learning/?category=selection_feature)[產業數據模組](https://www.xq.com.tw/learning/?category=industry_feature)[權證操作模組](https://www.xq.com.tw/learning/?category=warrant_feature)[美股分析模組](https://www.xq.com.tw/learning/?category=us_source)
