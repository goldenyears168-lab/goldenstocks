---
source: https://www.xq.com.tw/lesson/xspractice/tick欄位的應用/
title: Tick欄位的應用 - XQ 全球贏家
fetched: 2026-10-23
---

# Tick欄位的應用

###### 2020-07-08 17:45:36

複製連結

在上一篇文章「[盤中即時資料欄位的應用](https://www.xq.com.tw/learn/xspractice/realtime/)」，我們介紹了如何在腳本內引用「即時資料欄位」來繪製指標或是執行警示，在這一篇文章內我們將為大家介紹另外一種盤中會即時更新的資料: Tick資料。 什麼是Tick資料呢？Tick資料簡單的來說，就是每一筆成交資料。 [![](../images/cd4c578589.png)](https://www.xq.com.tw/wp-content/uploads/2020/06/7.02-Tick成交明細.png)從7.02版開始，你可以從腳本內取得每一筆成交資料的詳細資料，除了成交時間，成交價格，成交單量之外，還可以取得這一筆成交資料的內外盤標記(這一筆是外盤成交，還是內盤成交呢？)。以下是Tick資料所支援的所有欄位，以及從腳本內讀取資料的範例：value1 = GetField("Date", "Tick"); // 成交日期，例如20200611 (2020年6月11日) value2 = GetField("Time", "Tick"); // 成交時間，例如103011 (10點30分11秒) value3 = GetField("Close", "Tick"); // 成交價格 value4 = GetField("Volume", "Tick"); // 成交單量 value5 = GetField("BidAskFlag", "Tick"); // 內外盤標記: 1代表外盤成交(紅色), -1代表內盤成交(綠色), 0代表中立 value6 = GetField("BidPrice", "Tick"); // 買進價格 value7 = GetField("AskPrice", "Tick"); // 賣出價格 value8 = GetField("SeqNo", "Tick"); // 資料編號, 每個交易日從1開始編制, 第一筆是1, 第二筆是2, 以下類推 value22 = GetField("Close", "Tick")[1]; // 前一筆成交價格 value23 = GetField("Close", "Tick")[2]; // 前兩筆成交價格透過Tick資料，我們從腳本內就可以更掌握目前的行情資料。我們來看一個大單篩選的警示範例，假如我們希望商品出現單筆成交量大於100張的成交時就通知我們的話，可以用底下這個警示腳本來完成：input: filterMode(1, "篩選方式", inputkind:=dict(["買盤",1], ["賣盤",-1])); input: filterVolume(100, "大單門檻"); value1 = GetField("Time", "Tick"); // 時間 value2 = GetField("Close", "Tick"); // 價格 value3 = GetField("Volume", "Tick"); // 單量 value4 = GetField("BidAskFlag", "Tick"); // 外盤=1, 內盤=-1 if value4 = filterMode and value3 >= filterVolume then ret=1; 我們把這個腳本加到策略雷達內，選擇1分鐘頻率，勾選逐筆洗價，同時指定要篩選的參數，例如filterMode選擇買盤，filterVolume選擇100，這樣子就完成了。 [![](../images/308aef8701.png)](https://www.xq.com.tw/wp-content/uploads/2020/06/7.02-策略雷達大單篩選設定.png)Tips: 上面這個腳本有使用到inputKind這個語法，讓參數設定時可以用選單的方式來挑選。有興趣的同學可以參考[inputKind的說明](https://xshelp.xq.com.tw/XSHelp?HelpName=inputkind&group=DECLARATION)。

## 

### **Tick欄位 v.s. 報價欄位**

在XS腳本內，你也可以透過[報價欄位](https://xshelp.xq.com.tw/XSHelp/lists?a=QOFTEN)來取得盤中的行情欄位，例如[q_Last](https://xshelp.xq.com.tw/XSHelp/?HelpName=成交&group=QPRICE)可以取得最新一筆成交價格，[q_TickVolume](https://xshelp.xq.com.tw/XSHelp/?HelpName=單量&group=QQUANTITY)可以取得最新一筆成交單量，也可以透過[q_BidAskFlag](https://xshelp.xq.com.tw/XSHelp/?HelpName=內外盤&group=QPRICE)來取得最新一筆成交的內盤外盤註記。 那在這個腳本內為何不使用報價欄位，而要使用Tick欄位呢？ 要說明這個問題時請大家先看底下這張圖： [![](../images/455292864c.png)](https://www.xq.com.tw/wp-content/uploads/2020/07/Tick說明_0708.png)從上面這張圖內我們可以觀察到XS洗價的執行方式：

- 啟動逐筆洗價時，當系統收到成交資料時就會觸發K棒的洗價，
- 一般而言每收到一筆成交就會觸發一次K棒的洗價，請注意上圖內的洗價時間是示意資料，實際洗價時間會因為電腦CPU，指定的洗價速度等因素而有差異，
- 可是如果快市的話，就有可能不是每一筆Tick都觸發一次洗價，而是好幾筆Tick才觸發一次洗價，例如在上面這張圖內, Tick編號#3跟Tick編號#4兩筆資料的間隔時間很短，所以觸發洗價時已經收到兩筆Tick了(編號#3跟編號#4)，
- 報價欄位所回傳的是目前最新的行情，在上面這個情形時就是編號#4的數值，所以大部分的時候報價欄位的數值會等於最新一筆Tick的資料，
- 可是有可能在電腦洗價時又收到了更多的成交資料，此時報價欄位會更新，q_Last就不一定會跟洗價時K棒的Close是一樣的數值，例如在上面這張圖內，執行第四次洗價時如收到了編號#6的Tick的話, q_Last會被更新成編號#6的資料，此時就會跟第四次洗價時所抓到的價格不一樣，
- 如果在洗價時去抓取Tick資料的話，則系統會保證此時抓到的Tick資料跟洗價當時的K棒內容是一致的。例如第四次洗價時抓到的Tick會是編號#5的Tick，縱使洗價當時編號#6的Tick已經收到了，
- 腳本可以在洗價當時透過讀取Tick資料的方式來抓到兩次洗價之間的所有Ticks：例如在第四次洗價時抓到的Tick是編號#5的Tick，此時腳本可以再抓取「前一筆」Tick，就會抓到編號#4的Tick，
K棒洗價的速度可以透過系統的設定功能來指定，如果調整成最快的話約是125ms會執行一次洗價，如果調整成最慢的話則是每1秒執行一次洗價，使用者可以依照腳本複雜度，CPU的等級等來決定合適的數值。 [![](../images/13d6e47997.png)](https://www.xq.com.tw/wp-content/uploads/2020/06/7.02-系統參數.png)

### 

 

### **如何抓到每一筆Tick資料**

雖然在快市時不一定可以每次收到成交資料時就執行洗價，可是我們還是可以利用以下的技巧來抓到上一次洗價到這一次洗價之間的所有Tick資料：

- 每一筆Tick資料有一個「SeqNo」的欄位，這個欄位代表的是這一筆Tick是這個交易日的第幾筆，每個交易日會重新從1開始編制，
- Tick資料跟1分K, 5分K一樣，都是一個序列，可以使用[1]，[2]的語法來取得前一筆的數值
所以如果我們在腳本內記住這一次洗價時的SeqNo的數值，然後在下一次洗價時比對當時的SeqNo，就可以知道這兩次洗價之間相隔幾筆Tick，然後透過[n]的方式就可以把這些Tick資料都讀出來。例如上一次洗價時SeqNo是3，而這一次洗價時SeqNo是5，那麼這兩次之間就隔了兩筆Tick，所以腳本只要讀取當時的Tick，跟前一筆Tick資料(共兩筆)，就可以清楚知道兩次洗價之間市場的變化了。 我們把大單篩選的腳本改用這樣子的方式來撰寫。觸發的邏輯改成是如果兩次洗價之間出現了任何一筆大單的話，都顯示警示。input: filterMode(1, "篩選方式", inputkind:=dict(["買盤",1], ["賣盤",-1])); input: filterVolume(100, "大單門檻"); var: intrabarpersist last_seqno(0); // 上次洗價時最後一筆Tick的SeqNo var: curr_seqno(0); // 這次洗價時最後一筆Tick的SeqNo if Date <> CurrentDate then return; // 只跑今日的資料 curr_seqno = GetField("SeqNo", "Tick"); // 最新一筆Tick編號 if last_seqno = 0 then last_seqno = curr_seqno - 1; // 第一次洗價時只洗當時那一筆 var: seq_no(0), offset(0); seq_no = curr_seqno; offset = 0; while seq_no > last_seqno begin // 讀取Tick資料 value1 = GetField("Time", "Tick")[offset]; value2 = GetField("Close", "Tick")[offset]; value3 = GetField("Volume", "Tick")[offset]; value4 = GetField("BidAskFlag", "Tick")[offset]; if value4 = filterMode and value3 >= filterVolume then begin ret=1; end; seq_no = seq_no - 1; offset = offset + 1; end; last_seqno = curr_seqno; 上面這個腳本比較長一點，所以我們花點篇幅說明處理的方式:

- 第3行內宣告了last_seqno這個變數，這個變數是用來儲存上一次洗價時所對應的SeqNo，由於我們採用逐筆洗價方式，所以必須宣告成[intrabarpersist](https://xshelp.xq.com.tw/XSHelp?HelpName=IntraBarPersist&group=DECLARATION)，才可以讓這個變數的數值在同一根bar內可以正常更新，
- 每一次洗價時腳本會抓取當時的SeqNo (第9行)，然後把這個數值存在curr_seqno這個變數內，
- 腳本內使用[while](https://xshelp.xq.com.tw/XSHelp/?HelpName=While&group=CONTROLFLOW)語法，來跑過curr_seqno到last_seqno之間的所有資料(第16行開始)，

- 假如last_seqno是3，而curr_seqno是5的話，那這兩次洗價之間總共有2筆Tick資料，編號分別是4跟5(3的那一筆上一次已經讀過了)，
- 腳本內宣告了offset這個變數(第13行)，用來控制往前讀取的位置，一開始offset = 0(第15行)，
- 進入while loop後，第一次讀到的是GetField("Close", "Tick")[0](第19~22行)，也就是編號5的這一筆，
- 每跑一次loop，offset會加1(第29行)，表示下一次我們會往前再讀一筆，
- 第二次while loop讀到的是GetField("Close", "Tick")[1], 也就是編號4的這一筆，
- 我們用seq_no這個變數來控制while loop的次數，seq_no一開始是curr_seqno(第14行)， 也就是5， 每一次loop seq_no會減1(第28行)（因為我們是往前讀），
- 這個while loop總共跑了2次：第一次seq_no = 5，第二次=4，第二次跑完之後seq_no變成3， 此時就跳出loop了，
- 全部跑完之後，要記得更新last_seqno (第32行)
這次的介紹到這裡先告個段落，在下一篇文章「[台股逐筆撮合的連續成交Tick序列](https://www.xq.com.tw/learn/xspractice/tickseq/)」，我們會再介紹台股逐筆撮合制度所產生的另外一種資料，也會跟大家介紹系統內建的ReadTicks函數，透過這個函數來簡化讀取Tick資料的流程。 下次再見囉。[##### 前一篇

盤中即時資料欄位的應用](https://www.xq.com.tw/lesson/xspractice/盤中即時資料欄位的應用/)[##### 下一篇

台股逐筆撮合的連續成交Tick序列](https://www.xq.com.tw/lesson/xspractice/台股逐筆撮合的穿價tick/)
