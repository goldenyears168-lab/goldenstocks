---
source: https://www.xq.com.tw/lesson/filter/outputfield排序/
title: outputfield排序 - XQ 全球贏家
fetched: 2026-10-23
---

# outputfield排序

###### 2017-08-02 18:35:21

複製連結

相信有在使用【選股中心】的用戶，對outputfield應該相當熟悉，本篇來介紹outputfield的新功能。 如果沒用過outputfield的用戶可以看[OutputField - (內建函數)](https://xshelp.xq.com.tw/index.aspx?HelpName=OutputField&group=GENERALFUNC) 各位對小編說的outputfield 新功能想必非常好奇，話不多說，小編趕緊介紹。 Outputfield 在新版2.30中，多了一個排序的功能，也就是可以直接藉由outputfield 的語法來設定排序，接著小編就用腳本來做示範：

### 

 

### **排序功能1：由大至小**

1ret = 1; 2outputfield(1,GetField("每股稅後淨利(元)","Q"),2,"EPS", order := 1);[![由大至小](../images/af579f9e69.png)](https://www.xq.com.tw/wp-content/uploads/2017/07/由大至小.png)可以看到在outputfield 後方多下一個函數設定order := 1 ，所選出的選股結果，就會依照每股稅後淨利的數值，由大至小的做排序的動作。 

### **排序功能2：由小至大**

1ret = 1; 2outputfield(1,GetField("每股稅後淨利(元)","Q"),2,"EPS", order := -1);[![由小至大](../images/f71a27879a.png)](https://www.xq.com.tw/wp-content/uploads/2017/07/由小至大.png)將函數設定order :=- 1 ，所選出的選股結果，則會變為由小至大做排序的動作。 相信在outputfield增加排序的功能後，要挑選股票時就會更加方便啦。[##### 前一篇

如何創造個人排行榜](https://www.xq.com.tw/lesson/filter/如何創造個人排行榜/)[##### 下一篇

如何找出大戶持股比例增加的股票？](https://www.xq.com.tw/lesson/filter/如何找出大戶持股比例增加的股票？/)
