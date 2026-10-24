---
source: https://www.xq.com.tw/learning/xs-編輯器：選股腳本撰寫教學/
title: XS 編輯器：選股腳本撰寫教學 - XQ 全球贏家
fetched: 2026-10-23
---

[學習地圖](https://www.xq.com.tw/learning/)XS 編輯器：選股腳本撰寫教學

# XS 編輯器：選股腳本撰寫教學

###### 2024-12-06 16:39:06

學習分類：[盤後量化選股模組](https://www.xq.com.tw/learning/?category=selection_feature)內容標籤：[XS 腳本](https://www.xq.com.tw/learning/?tags=xs_script)[策略開發](https://www.xq.com.tw/learning/?tags=trading_strategy)

複製連結

[![XS 編輯器：選股腳本撰寫教學](images/72dfc89752.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image2-21.png)大家好，今天我們就與大家分享如何使用 XS 編輯器來撰寫一份「選股腳本」。在這一篇文章中，我們會和各位分享使用 XS 編輯器中「選股策略」的基礎邏輯，並手把手帶著大家寫出第一個自己的選股策略。

## **1. 為什麼要學習撰寫「選股腳本」**

在交易中，內建的工具與篩選功能雖然便利，但常常因為條件過於簡單或範圍有限，無法滿足我們對交易策略的細緻要求。這時候，學會撰寫「XS選股腳本」就成為了一項重要的技能，以下是幾個主要原因：

### **1.1 突破內建功能限制，實現精準篩選**

市場中每位交易者的策略都各有不同，內建篩選條件無法涵蓋所有需求。透過撰寫選股腳本，你可以設定符合個人交易邏輯的條件，例如：

- 使用者可以依據自己的交易邏輯和需求，撰寫完全符合策略的腳本，而非依賴於市面上預設的指標或策略。
- 可以針對特定市場特性（如波動性、趨勢強度）調整策略參數。
- 動態更新選股邏輯，根據市場變化快速調整。

### **1.2 強化量化交易能力**

撰寫選股腳本的過程中，你可以將交易邏輯量化，並搭配回測功能驗證策略的可行性。例如，文件中提到的「回測用交易程式」和「選股股池每日更新」，這些功能都可以通過腳本實現，以便快速測試和優化策略​。

### **1.3 提升策略的個人化與優勢**

市場中的競爭日益激烈，透過自訂腳本可以打造屬於自己的交易優勢。你不再局限於大眾使用的工具，而是根據自己的交易邏輯設計獨一無二的策略，從而提升在市場中的競爭力。

- 使用 XScript 撰寫的策略可以隨時修改和擴充，適應不同的市場條件或交易目標。
- 自行撰寫的腳本能更容易整合自定義技術指標、風險管理策略等功能。

- 自行撰寫的策略不會被其他交易者輕易模仿，提升市場競爭力。
- 確保交易邏輯的保密性，減少策略被公開或盜用的風險。
 

## **2. 如何新增一個選股腳本**

當你進入 XS 編輯器之後，您可以點擊[![](images/c6cb737467.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image6-17.png)鍵，新創一個策略，具體的位置是在下圖中紅框的位置。

##### ▼ 新增一個 XS 策略

[![新增一個 XS 策略](images/a730948ae0.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image15-6.png)點擊之後，就會出現「新增腳本」的視窗，我們在這邊點擊「選股」，並命名你的腳本之後按下「確認」鍵。

##### ▼這邊可以選擇腳本類型

[![這邊可以選擇腳本類型](images/b6e09217dc.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image13-8.png)

## **3. 選股腳本基礎邏輯**

如果對於寫 CODE 不熟悉的朋友，可能一開始會有點迷茫，不太確定面對一大塊白布的介面要怎麼開始，沒關係，我們這邊一步步和大家說怎麼進行撰寫。 在「選股腳本」當中，我們可以簡單將整個腳本拆成三個大區塊：

### **3.1 宣告、敘述與輸出的角色與目的**

#### **3.1.1 宣告:**目的是向程式明確說明需要用到的資料和變數。

- 自訂變數： 如果你需要自訂變數，應該使用 Vars 來定義變數的預設值與型態，以便程式知道如何處理這些變數。
- 自訂參數： 若需自訂參數（例如策略中可調整的設定值），則應使用 Input 來宣告參數的名稱與預設值，讓程式能正確理解並在執行時使用這些參數。

#### **3.1.2 敘述:**向程式描述如何進行計算和邏輯判斷的過程。

以範例為例，如果想篩選出振幅和量能都符合條件的股票，可以透過邏輯變數 Condition 來定義篩選條件。 這部分的目的是明確告訴程式：「當滿足這些條件時，標的應視為符合策略要求。」

#### **3.1.3 輸出:**定義程式應該輸出什麼結果。

- **回傳值 (Ret)：**使用 Ret（Return value 的縮寫）來回傳符合條件的結果。例如，當所有條件（如 Condition1 和 Condition2）均成立時，程式將回傳 1，表示該標的符合我們的策略邏輯。 這個回傳結果可以作為程式篩選後的輸出，幫助我們快速識別符合條件的標的，進而提升篩選效率。

##### ▼選股腳本的三個區塊

[![選股腳本的三個區塊](images/c8f1aa11e1.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image18-4.png)

## **4. 一起來寫一個選股腳本吧**

我們只要把自己想要寫的選股策略依照上述的區塊填上，就可以完成你的腳本撰寫囉。那麼接下來，我們就以撰寫一個腳本，來找出「EPS與營收對比上季都有成長」的公司吧。

### **4.1 宣告**

第一步我們是要先呼叫會運用到的數據，在這個策略裡，我們要先呼叫「現在的EPS」、「上一季的EPS」、「現在的營業收入」、「上一季的營業收入」，總共有四個變數。 那麼，我們就要先呼叫這四個數據，因此我們就先寫下以下的指令，用內建的 value1~value4 來簡單設定一下吧。接著，我們可以使用 GetField 的這個指令，把這些變數分別定義成 value1 = 現在的營業收入 value2 = 上一季的營業收入 value3 = 現在的 eps value4 = 上一季的 eps 

##### ▼宣告的寫法

[![宣告的寫法](images/eb3e2b4f12.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image16-4.png)如果不想要一個一個找函數的話，也可以在輸入的時候，選擇「GetField (插入)」，用直接選取的方式呼叫自己想要鎖定的欄位呦。

##### ▼運用 GetField 函數找資訊

[![運用 GetField 函數找資訊](images/6b9a0ba051.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image10-12.png)也可以點擊上面的「欄位」[![](images/596158acbc.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image9-12.png)按鈕，或是快截鍵 F7 ，也可以直接進入插入函數的方便功能喔。

##### ▼插入欄位

[![插入欄位](images/6a80992904.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image20-3.png)在呼叫數據這一個部份，除了 GetField 是常用的以外，也有其他的功能，例如呼叫時間、呼叫特定商品等等，想要知道更多的話，可以點擊上面的函數搜尋，探索更多用法。 

### **4.2 敘述**

在我們呼叫完數據之後，我們就要來判斷這些數據之間的邏輯，例如大小、等於、上穿、下穿等等的邏輯。 我們這次的邏輯很簡單，只要判斷兩個事情：

- 這季的營收比上季高： value1 > value2；
- 這季的 EPS 比上季高：value3 > value4；
這個時候，我們就可以使用內建的變數 condition1, condition2 來判斷選股條件有沒有符合，如果有符合的話，那就會回傳「true」，反之，則會回傳「false」。

##### ▼敘述的寫法

[![敘述的寫法](images/fffee6dd2b.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image7-17.png)

### **4.3 輸出**

#### **4.3.1 RET=1 執行選股**

最後我們只要讓系統幫我們選出 condition1 與 condition2 都通過的股票就好了；因此只要和系統說：如果condition1 和 condition2 都為真的情況下，執行「ret=1」。 這個ret=1，就是告訴電腦「執行」的意思。因此，如果你想要執行選股的話，選股腳本中就加入「ret=1」，讓電腦執行這個腳本。

##### ▼回傳結果

[![回傳結果](images/f2fa44b4aa.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image12-10.png)

#### **4.3.2 OUTPUTFIELD 輸出資料**

除了 ret=1 來執行選股以外，您也可以使用 outputfield 等函數，讓它在選股中心中可以新增一個欄位 show 出你想要看的資料喔。

##### ▼outputfield回傳結果

[![outputfield](images/fca6fa5019.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image3-20.png)這個函數可以將您想看的數據直接呈現在選股結果上面。

##### ▼Outputfield 的顯示結果

[![Outputfield 的顯示結果](images/c085b71a9f.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image14-8.png)好的，基本上到這邊我們已經告一段落了，我們再快速複習一次：

##### ▼一個選股腳本的構成

[![一個選股腳本的構成 ](images/3f8769d7d6.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image8-13.png)

- 第一階段使用 value1~4 與 GetField 函數呼叫了四個數據；
- 第二階段使用 condition1~2 與簡單的大於小於 判斷了邏輯；
- 第三階段使用 ret =1 執行邏輯，並使用outputfield1~4 來回傳結果。
 

### **4.4 編譯與執行**

在我們完成撰寫之後，還差一小步就可以拿它來運行了，這個時候請您點擊「編譯」按鈕[![](images/0ceb780acf.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image4-20.png)，或是按下 F6 進行編譯；這個動作是讓系統檢查一次你的腳本是否都正確可以上路了，只要出現「全部物件編譯結束」之後，就代表這個腳本是可以上路的囉。

##### ▼編譯的結果

[![編譯的結果](images/c04b82d310.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image19-3.png)當然編譯也有可能會幫你發現程式碼不對的地方，這個時候您就可以透過上面指示的行號與字元數找到問題出現的位置，修正之後再進行編譯。

##### ▼編譯錯誤的情況

[![編譯錯誤的情況](images/9742c85367.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image17-4.png)在編譯完成之後，就可以點擊加入選股[![](images/695de3b5e5.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image1-22.png)來運行這個策略囉；當然，您也可以在選股中心的「選股腳本」當中隨時呼叫您撰寫過的腳本，與其他選股條件組合成您獨有的選股策略。

##### ▼呼叫寫好的選股腳本

[![呼叫寫好的選股腳本 ](images/0a3f74f8ac.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image5-18.png)

## **5. 常見的選股腳本運用的函數**

這邊我們也提供一些常見的選股腳本會運用的函數，同時付上函數說明的連結 

 **函數** | **常見使用步驟** | **用途** |

 [Input](https://xshelp.xq.com.tw/XSHelp/?HelpName=Input&group=DECLARATION) | 宣告 | Input語法用來宣告腳本參數的名稱以及資料類型(也可以寫成Inputs) |

 [inputkind](https://xshelp.xq.com.tw/XSHelp/?HelpName=inputkind&group=DECLARATION) | 宣告 | input宣告的時候，可以使用 inputkind 這個命名參數(named parameter)，用來控制系統參數設定的介面(UI)。再搭配 Dict 、 DateRange 或 SymbolPrice 函數來產生對應的內容 |

 [Var](https://xshelp.xq.com.tw/XSHelp/?HelpName=Var&group=DECLARATION) | 宣告 | Var語法用來宣告變數，並且給定變數的預設值 |

 [Condition](https://xshelp.xq.com.tw/XSHelp/?HelpName=condition&group=DECLARATION) | 敘述 | 系統內建999個邏輯變數，名稱分別是Condition1, Condition2, Condition3, 一直到**Condition999*。 腳本內可以直接使用這些變數，不需要預先宣告。 |

 [IF / THEN / ELSE](https://xshelp.xq.com.tw/XSHelp/article?a=If_Then_Else) | 敘述 | 使用IF/THEN/ELSE這三個語法來判斷某個條件成立時該執行那個動作，不成立時又該執行那個動作。 |

 [Cross Above / Cross Below](https://xshelp.xq.com.tw/XSHelp/?HelpName=Cross&group=CONTROLFLOW) | 敘述 | Cross Above 或是 Cross Over 是用來檢查目前的欄位數值是否向上穿越某個欄位的前期數值。 Cross Below 或是 Cross Under 則是用來檢查目前的欄位數值是否向下跌破某個欄位的前期數值。 |

 [Ret](https://xshelp.xq.com.tw/XSHelp/?HelpName=Ret&group=DECLARATION) | 輸出 | Ret是一個系統的內建變數，他的數值會決定警示腳本以及選股腳本執行結果。 當警示腳本以及選股腳本在每根bar重新執行時 Ret的數值會被設定為0，當這根bar結束完成時如果Ret的數值不是0的話，則會產生觸發訊號(警示腳本)，或是選取這檔商品(選股腳本)。 |

 [OutputField](https://xshelp.xq.com.tw/XSHelp/?HelpName=OutputField&group=GENERALFUNC) | 輸出 | 選股腳本預設只會顯示被選到的商品的商品名稱，成交，漲幅，成交量這幾個欄位。如果有需要在選股結果內輸出更多的欄位的話，則可以透過OutputField函數來新增欄位。 |

## **6. 總結**

今天我們用大框架的方式與各位分享如何撰寫一個選股腳本的基礎邏輯，從使用GETFIELD呼叫數據開始，到RET=1 回傳選股結果；希望這樣的小練習能讓大家更熟悉選股中心的邏輯，讓您可以在量化交易的世界中無往不利！我是 XQ 小編，我們下次見。

## 最新文章

[【功能更新】量化積木新增「等額（100 萬元）」交易模式！三大更新重點全解析](https://www.xq.com.tw/learning/xq-blocks-equal-value-trading-mode/)[【新手也能上手】量化積木支援「期貨商品」啦！](https://www.xq.com.tw/learning/xqblockfuture/)[【常見情境實戰篇】新手也能懂的期貨策略應用](https://www.xq.com.tw/learning/xqblockfuturestrategy/)[【新春活動】2026 金馬年開運紅包：新用戶專屬三大量化投資策略公開！](https://www.xq.com.tw/learning/xq2026threestragy/)[為什麼要做「量化交易」？避開散戶 3 大心態，打造你的投資 SOP](https://www.xq.com.tw/learning/quanttrading3retailinvestorminds/)

## 學習分類

[不分模組](https://www.xq.com.tw/learning/)[基本功能](https://www.xq.com.tw/learning/?category=basic)[尊榮會員](https://www.xq.com.tw/learning/?category=prime)[台股進階模組](https://www.xq.com.tw/learning/?category=tw_source)[量化積木模組](https://www.xq.com.tw/learning/?category=blocks_feature)[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)[盤後量化選股模組](https://www.xq.com.tw/learning/?category=selection_feature)[產業數據模組](https://www.xq.com.tw/learning/?category=industry_feature)[權證操作模組](https://www.xq.com.tw/learning/?category=warrant_feature)[美股分析模組](https://www.xq.com.tw/learning/?category=us_source)
