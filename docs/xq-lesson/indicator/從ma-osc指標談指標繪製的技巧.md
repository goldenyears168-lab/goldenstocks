---
source: https://www.xq.com.tw/lesson/indicator/從ma-osc指標談指標繪製的技巧/
title: 從MA-Osc指標談指標繪製的技巧 - XQ 全球贏家
fetched: 2026-10-23
---

# 從MA-Osc指標談指標繪製的技巧

###### 2016-03-25 16:33:24

複製連結

[![8-1](../images/f12b434deb.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/8-1.png)小編常常被問到關於XS自訂指標的一個問題是，我怎麼樣可以畫出上漲是紅棒，而下跌是綠棒的圖呢 ? 如果你已經用過了XS自訂指標之後，應該有看過指標繪圖的設定畫面，從這裡可以指定繪圖樣式為柱狀體。 可是，目前XS只支援一個顏色，沒有辦法直接從設定畫面來指定上漲的柱狀體顏色以及跟下跌的柱狀體顏色。那有其他辦法可以辦到嗎？ 答案當然是可以的。 設計的方式，我們一樣來看一下腳本：input: Length1(5), Length2(10); SetInputName(1, "天數一"); SetInputName(2, "天數二"); value1 = Average(close, Length1); value2 = Average(close, Length2); value3 = (value1 - value2); Plot1(value3, "MA-Osc"); 上面的範例是一個很有名的指標，叫做[移動平均線擺盪指標](http://www.moneydj.com/KMDJ/wiki/wikiViewer.aspx?keyid=635abbb6-1fe6-46ab-b309-68d5eb96b6a6)。 這個指標的原理是計算兩條均線之間的差異(短均線─長均線)，用來觀察均線目前是位於多頭排列還是空頭排列，也可以比較清楚的看到交叉的轉折點。 要把這個指標畫出來的話，直接使用線條來描繪當然可以。可是如果是畫成柱狀體的話，會更容易看的出來。所以我們把繪圖方式改成柱狀體， 畫出來的樣子像底下這樣： [![MA_Osc_0325_02](../images/c8f7792606.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/MA_Osc_0325_02.png)雖然畫出來了，可是還不夠完美。如果可以把多頭排列區(Value3 > 0)的地方畫成紅色，把空頭排列區(Value3 < 0)的地方畫成綠色的話，那就更好了。 那要怎麼做呢？如果指標的一個plot只能設定一種繪圖方式/顏色的話，那我們是不是可以試著把這個指標變成兩個plot呢？input: Length1(5), Length2(10); SetInputName(1, "天數一"); SetInputName(2, "天數二"); value1 = Average(close, Length1); value2 = Average(close, Length2); value3 = (value1 - value2); if value3 >= 0 then Plot1(value3, "+MA-Osc") else Plot2(value3, "-MA-Osc"); 稍微修改一下程式，當value3 >= 0的時候，我們畫成Plot1，而value3 < 0的時候，我們畫成Plot2。 這樣子我們就可以把Plot1跟Plot2設定成不同的顏色了！ [![MA_Osc_0325_03](../images/258b23bf01.png)](https://www.xq.com.tw/wp-content/uploads/2016/03/MA_Osc_0325_03.png)最後小編要提醒大家，因為Plot1跟Plot2其實都是在畫一樣的數值，所以設定畫面內的座標必須要設成一樣的(在這裡都選I)，才不會畫出來的上下比例不正確。 另外，如果是從K線端，修改完設定之後，可以按【設為XS指標預設值】，以後這個指標就會長這個樣子囉！ 在XS自訂指標還沒有支援雙色的柱狀設定之前，還是可以運用這一篇的技巧來達到一樣的效果。大家如果有類似的需求的話，可以試看看。[##### 前一篇

新增XS副圖指標](https://www.xq.com.tw/lesson/indicator/新增xs副圖指標/)[##### 下一篇

如何繪製進出場訊號](https://www.xq.com.tw/lesson/indicator/如何繪製進出場訊號/)
