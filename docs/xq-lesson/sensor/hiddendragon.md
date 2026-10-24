---
source: https://www.xq.com.tw/lesson/sensor/hiddendragon/
title: 潛龍飛天 - XQ 全球贏家
fetched: 2026-10-23
---

# 潛龍飛天

###### 2025-07-18 11:50:53

複製連結

今天想跟大家介紹一個小弟我長年看K線時觀察到的一個交易策略我把它稱之為潛龍飛天。 各位請看以下這張圖： [![071204](../images/092127e86e.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/07/071204.png)我發現，當一檔股票在急漲後，如果在其拉回整理的過程中，底部愈墊愈高，且整理的時間夠久，當它最終突破幾個整理期間的高點連線後，後市持續上漲的機率很高。 通常這種線型代表這檔股票的供需出現變化，買方在一陣急拉後，不再追高，改採低接的方式來增加持股，在低接一陣子之後，買方如果願意再墊高一點買進，代表在原來的低價區已接不到股票了，這樣持續幾次，股價就會出現長長未再破底，但高檔有賣壓的線型，這就是上圖的樣式。 這樣的股票，如果搭配我們發現有主力在長期吸納持股，等到浮額都被清光了，賣壓愈來愈輕，最終一突破整理區間的高價區，代表多方終於打贏了決定性的戰役，空方一定要撤退到另一個陣線才能阻擋多方的攻勢，於是短多的機率就會變高，這樣的走勢，很像一尾潛在水面下的龍終於冒出水面，所以我稱為潛龍飛天。 我舉一個例子， [![071501](../images/f71eebf3f2.png)](https://www.xq.com.tw/xstrader-content/uploads/2015/07/071501.png)各位看上圖，會發現，佳格前一陣子的走勢完全符合潛龍飛天的型態，而且我們看到收集派發指標也出現籌碼在被收集的訊號。所以當佳格股價突破80元左右的空方防線區之後，空方一直退到90元左右。 接下來大家一定會問我，那要怎麼找到這種潛龍飛天的股票呢？每天要一檔一檔看，看1500檔嗎？ 以前YES，我都是直接打開財訊快報，快速瞄過每檔股票的線圖； 但現在有了XS，我們可以寫一個策略，叫電腦在盤中把符合條件的股票挑出來。 以下就是符合潛龍飛天概念的腳本：input:StartDate(20150301); input:LowMonth(3); if currentbar =1 and date < startdate then raiseruntimeerror("日期不夠遠"); variable:iHigh(0); iHigh=maxlist(iHigh,H); variable:iLow(100000); iLow=minlist(iLow,L); variable:hitlow(0),hitlowdate(0); if iLow = Low then //觸低次觸與最後一次觸低日期 begin hitlow+=1; hitlowdate =date; end; if DateAdd(hitlowdate,"M",3) < Date and//如果自觸低點那天三個月後都沒有再觸低 iHigh/iLow < 1.3 and //波動在三成以內 iHigh = High then //來到設定日期以來最高點 ret =1; 這樣挑出來的股票不會多，所以我們就可以一檔一檔檢視其籌碼的情況。 如此一來，潛龍起飛時，我們就可以第一時間就掌握住了。 不過在此要跟大家報告，這個腳本不見得天天挑得出股票，挑出來的股票，也是要看大盤好不好，如果大盤不好，千萬不要勉強作多，畢竟再多整理一下，多方有更便宜的股票可以買，不見得願意追高。 [##### 前一篇

策略雷達與雷達回測的錯誤代碼說明](https://www.xq.com.tw/lesson/sensor/策略雷達與雷達回測的錯誤代碼說明/)[##### 下一篇

Larry Williams 短線交易秘訣](https://www.xq.com.tw/lesson/sensor/larrywilliams/)
