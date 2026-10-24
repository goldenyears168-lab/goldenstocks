---
source: https://www.xq.com.tw/learning/xqblocksbuildstraegy/
title: 【XQ 量化積木】0程式基礎也能撰寫量化交易策略 - XQ 全球贏家
fetched: 2026-10-23
---

[學習地圖](https://www.xq.com.tw/learning/)【新手教學】什麼是量化積木？如何用它製作選股策略教學（附範例）

# 【新手教學】什麼是量化積木？如何用它製作選股策略教學（附範例）

###### 2025-10-29 20:56:02

學習分類：[量化積木模組](https://www.xq.com.tw/learning/?category=blocks_feature)內容標籤：[策略開發](https://www.xq.com.tw/learning/?tags=trading_strategy)[量化積木](https://www.xq.com.tw/learning/?tags=learn_xqblocks)

複製連結

[![](images/80a26e6b9a.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木首圖.png)

## **不懂程式也能打造專屬選股策略**

想做量化交易，卻被複雜的程式碼擋在門外？聽過很多選股指標（例如 KD、MACD），想把它們組合起來，卻不知道如何回測驗證？ 如果你也有一套自己的選股邏輯，例如「我想找營收成長、法人又在買的股票」，但苦於無法將想法轉化為實際的策略，那麼 XQ 全球贏家的「量化積木」就是為您量身打造的利器。 本文將從零開始，一步步教您什麼是「量化積木」，並透過一個完整的範例，帶您實際操作，製作出您的第一個選股策略，徹底解決「有想法，但不會寫程式」的痛點。 **適合對象：**

- 想學習量化交易，但沒有任何程式基礎的投資新手
- 有自己的選股邏輯，想驗證其長期績效的投資人
- 想客觀、系統化執行選股，不想再憑感覺交易的投資人

## **文章目錄**

## **一、什麼是「量化積木」？為何新手該用它？**

「量化積木」是 XQ 內建的「**視覺化**」策略編輯器。 詳情可參閱➡️[【量化積木】量化投資策略績效大整合，快速找出適合自己的策略](https://www.xq.com.tw/learning/xqblocksbeginner/) 傳統上，要回測一個策略，您可能需要學習 XS 腳本語言（一種程式語言）。但「量化積木」讓您**完全不需要撰寫任何程式碼**，只需要像「堆積木」一樣，用滑鼠將您想要的「條件」（例如：指標、籌碼、價量）拖拉到編輯區，就能組合出千變萬化的選股策略。

### **新手使用量化積木的 3 大優勢：**

- **零程式門檻：** 真正實現「所見即所得」，用中文邏輯就能打造策略。
- **即時回測：** 組合完成後，點擊「回測」按鈕，XQ 會立即幫您跑完過去數年的歷史數據，告訴您這套策略的「總報酬」、「勝率」、「最大虧損」等關鍵數據。
- **邏輯清晰：** 設定過程會條列出您的策略邏輯與條件，也能靈活調整各項參數。

## **二、如何找到 XQ 的【量化積木】功能**

您可以透過以下路徑，開啟量化積木的功能介面：

### **路徑1：** 主選單 ➡️ 「**量化積木**」

[![](images/6b908f1bdd.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木1.png)

### **路徑2：**點選主功能列中的「策略」 ➡️ 「**量化積木**」。

[![](images/c2647241c8.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木2.png)

## **三、實戰教學：三步驟打造「KD黃金交叉 + 成交量放大」策略**

接下來，我們用一個最經典的範例，教您如何製作一個「**技術面剛發動**」的短線選股策略。 **我們的策略邏輯是：**

- 技術指標：收盤日KD 黃金交叉
- 量能配合：成交量 > 昨日成交量的 2 倍（代表放量）
- 當日量能下限：成交量>=1000張

### **步驟一：開啟「量化積木」並新增策略**

- 開啟「量化積木」，點選左上角「**新增策略**」或中間的「**新增專屬策略**」。
[![](images/ab9efd9466.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木3.png)

- 為您的策略命名，例如：「KD黃金交叉+放量」。
- 右方可撰寫策略重點，讓自己回顧時快速了解此策略的邏輯。
[![](images/4513192158.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木4改.png)

### **步驟二：開始打造投資策略**

下方會開始進行策略的細部設定：「**策略設定**」、「**進場條件**」、「**出場條件**」與最後的「**回測**」。

- 「**策略設定**」**：**

- 設定交易方向 ➡️「**作多**」。
- 設定投資週期 ➡️「**短線**」。
- 設定執行商品 ➡️「**上市櫃普通股**」。
- 設定執行商品 ➡️「**無**」。
如果沒有需特別排除的標（如當沖策略需排除無法當沖之標的），就設定無。 [![](images/a2a9168db7.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木5.png)

- 「**進場條件**」**：**
這個策略主要使用的都是「價量」條件，參數部分則可由您依照喜好自行調整

- 「技術指標」➡️「KD 黃金交叉」
[![](images/451fb0404a.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木6.png)[![](images/4ccf2942f6.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木7.png)[![](images/5af2700fe8.jpg)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木8.jpg)

- 「成交量」➡️「成交量>=1000張」、「成交量 > 昨量的 2 倍」
[![](images/47d9020036.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木9.png)[![](images/4cedf8368f.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木10.png)[![](images/41a93c322c.jpg)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木12.jpg)[![](images/2b6ad6ba61.jpg)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木11.jpg) 

- 「**出場條件**」**：**

- 停損設定➡️20%
- 停利設定➡️30%
- 持有期間➡️10天
- 您也可以如「**進場條件**」一樣，隨自身喜好加入其他的出場條件進行測試。
[![](images/39f1e2f2ef.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木13.png)

- 「**回測**」**：**
**執行回測，檢視策略績效** 當積木都組合完畢後，點擊「**回測**」。 調整回測日期後，點擊右上角的執行回測，XQ 系統會馬上開始運算，並在下方顯示出完整的「**績效報告**」。 [![](images/efe363a91b.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木14.png)這份績效報告顯示，這套短線策略似乎不太可行，那是否「KD黃金交叉 + 成交量放大」策略就一定沒用了呢？

## **四、如何解讀回測報告？**

回測報告會告訴您這套策略的「體質」。新手請先看懂以下三個指標：

- **總報酬率：** 策略在回測期間（例如 5 年）的總獲利百分比。
- **最大回撤 (MDD)：** 策略在歷史上「最慘」的時候，曾經虧損多少百分比。這個數字幫您評估風險承受度。
- **勝率：** 所有交易中，賺錢出場的比例有多少。
詳情可參閱➡️[【量化積木】量化投資策略績效大整合，快速找出適合自己的策略](https://www.xq.com.tw/learning/xqblocksbeginner/) 透過這份報告，您可以客觀地判斷：「我剛才設計的這套策略，到底可不可行？」如果績效不佳，試著加入或修改不同的量化條件，這就是「策略優化」的過程！ 比方說剛剛回測出來糟糕透了的策略，我們試著把停利調整為50%，並將持有期間從10天的短期策略，調整為60天的中期策略，整體的淨值曲線似乎就好上了不少，是否有其他的條件加入後可以讓這個策略更好呢？就等您自行嘗試啦！ [![](images/b87e8476a3.png)](https://www.xq.com.tw/wp-content/uploads/2025/10/【新手教學】量化積木15.png)

## **結語：您的量化交易第一步**

恭喜您！您已經學會了如何使用「量化積木」製作選股策略了。從一個想法，變成一個經過數據驗證的選股策略，就是這項功能最大的價值，這讓您能快速地「測試」您的各種靈感，而不需要承擔拿真錢去試錯的風險。 現在，輪到您來動手試試看，打造您專屬的獲利模型吧！

## **立即註冊，免費體驗策略回測！** ?

別讓「不懂程式」成為您投資路上的絆腳石。XQ 全球贏家提供強大的「量化積木」功能，讓您用最直覺的方式，實現腦中的投資藍圖。 [**點此免費下載體驗 XQ 量化積木**](https://xq.com.tw/xqblocks/)，開始打造您的專屬量化策略！

## 最新文章

[【功能更新】量化積木新增「等額（100 萬元）」交易模式！三大更新重點全解析](https://www.xq.com.tw/learning/xq-blocks-equal-value-trading-mode/)[【新手也能上手】量化積木支援「期貨商品」啦！](https://www.xq.com.tw/learning/xqblockfuture/)[【常見情境實戰篇】新手也能懂的期貨策略應用](https://www.xq.com.tw/learning/xqblockfuturestrategy/)[【新春活動】2026 金馬年開運紅包：新用戶專屬三大量化投資策略公開！](https://www.xq.com.tw/learning/xq2026threestragy/)[為什麼要做「量化交易」？避開散戶 3 大心態，打造你的投資 SOP](https://www.xq.com.tw/learning/quanttrading3retailinvestorminds/)

## 學習分類

[不分模組](https://www.xq.com.tw/learning/)[基本功能](https://www.xq.com.tw/learning/?category=basic)[尊榮會員](https://www.xq.com.tw/learning/?category=prime)[台股進階模組](https://www.xq.com.tw/learning/?category=tw_source)[量化積木模組](https://www.xq.com.tw/learning/?category=blocks_feature)[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)[盤後量化選股模組](https://www.xq.com.tw/learning/?category=selection_feature)[產業數據模組](https://www.xq.com.tw/learning/?category=industry_feature)[權證操作模組](https://www.xq.com.tw/learning/?category=warrant_feature)[美股分析模組](https://www.xq.com.tw/learning/?category=us_source)
