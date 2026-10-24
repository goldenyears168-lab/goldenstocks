---
source: https://www.xq.com.tw/lesson/xspractice/台股逐筆撮合的穿價tick/
title: 台股逐筆撮合的連續成交Tick序列 - XQ 全球贏家
fetched: 2026-10-23
---

# 台股逐筆撮合的連續成交Tick序列

###### 2020-07-08 17:50:08

複製連結

大家好。在上一篇文章「[Tick欄位的應用](https://www.xq.com.tw/learn/xspractice/tick/)」，我們說明了如何從腳本內取得「Tick」頻率的欄位。在這一篇文章內我們要介紹台股逐筆撮合制度上線後所產生的另外一種成交方式，我們稱之為「連續成交序列」。 大家先看一下底下這張圖： [![](../images/093c046ff3.png)](https://www.xq.com.tw/wp-content/uploads/2020/07/連續Tick_8111.png)注意到在12:51:04秒左右有三筆資料後面有標示星號，成交價分別是11.70元26張，11.75元51張，以及11.80元23張，可是這個星號是什麼意思呢？ 之所以會有這個星號，是因為這三筆成交是由同一筆買進委託所產生的，而這一筆買進委託的委託量是100張，而且委託價格是11.8元。當交易所收到這一筆委託時，因為當時最低的委賣價是11.70元，只有26張委賣量，所以這一筆委託先撮合出11.70元26張的成交之後，接著交易所繼續撮合次高的委賣價11.75元51張，最後再撮合11.80元的委賣23張(26 + 51 + 23 = 100張)。 底下是這筆成交發生之前委賣五檔委託簿的樣子，請注意委賣價格以及委賣數量。 [![](../images/487ae51d96.png)](https://www.xq.com.tw/wp-content/uploads/2020/07/賣出委託部示意圖.png) 當發生這種情形時，因為產生了三筆不同的成交，所以交易所會傳送出3筆連續的成交資料。可是如果我們去思考這次成交的買賣力道時，觀察的重點應該是產生這一系列成交的這一筆委買單，所以在大單追蹤這個畫面內我們用星號把這幾筆成交資料的關係標示出來，計算單別時也會把這三筆成交加總，來判斷這一筆的實際力道。 那麼這樣子的資訊，是不是可以從XS內得知呢？Yes，We Can。value1 = GetField("TickGroup", "Tick"); 在Tick資料內有一個欄位叫做「TickGroup」，這個欄位是用來標示這一筆成交資料是怎麼撮合出來的，總共有5種可能的數值：

- -1: 表示這一筆成交並不是逐筆撮合所產生的。例如開盤的第一筆成交，收盤的那一筆成交都會標示-1，
- 0: 表示這一筆成交是逐筆撮合所產生的，只有撮出一筆，
- 1: 表示這一筆成交是連續成交序列內的第一筆，
- 2: 表示這一筆成交是連續成交序列內中間的任何一筆，
- 3: 表示這一筆成交是連續成交序列內的最後一筆
[![](../images/baf20ffcce.png)](https://www.xq.com.tw/wp-content/uploads/2020/06/Screen-Shot-2020-06-17-at-3.53.43-PM.png)如果對照到上面大單分析的那張圖的話，7.70元的那一筆的TickGroup是1，7.71 元以及7.72元的那兩筆的TickGroup都是2, 7.74元的那一筆的TickGroup是3。在腳本內，就可以藉由TickGroup這個欄位，把連續成交序列的每一筆Tick找出來，然後計算這個連續成交序列的總成交量。 為了簡化讀取Tick資料的流程，7.02版新增了一個ReadTicks函數，可以協助我們自動把把連續成交序列合併統計，讓腳本的的邏輯可以更簡單。我們把上一篇文章「[7.02-盤中即時資料欄位的應用](https://www.xq.com.tw/lesson/xspractice/盤中即時資料欄位的應用/)」內的篩選大單的腳本改用ReadTicks這個函數來重寫：input: filterMode(1, "篩選方式", inputkind:=dict(["買盤",1], ["賣盤",-1])); input: filterVolume(100, "大單門檻"); var: intrabarpersist readtick_cookie(0);// ReadTicks內部使用, 每次呼叫時請照實傳入 array: tick_array[100, 11](0); // 需要宣告一個2維陣列來儲存Tick資料 var: row_count(0), idx(0); // 讀取Tick資料 row_count = ReadTicks(tick_array, readtick_cookie); for idx = 1 to row_count begin if tick_array[idx, 5] = filterMode and tick_array[idx, 10] >= filterVolume then begin ret=1; end; end; 要呼叫ReadTicks之前，我們要先準備一個二維的[陣列](https://xshelp.xq.com.tw/XSHelp/?HelpName=Array&group=DECLARATION)(第5行的tick_array)，一個二維陣列就像是一個Excel的表格，呼叫完ReadTicks之後，每一橫列會儲存一筆Tick資料，而這一筆Tick資料的每個欄位則會存在這個橫列的每一行裡面。 除了這個陣列之外，我們還需要準備一個變數(第4行的readtick_cookie)，這個變數用來讓ReadTicks函數可以紀錄上次讀到的Tick資料位置。 接著每一次洗價時，我們就請ReadTicks函數讀取上次洗價到目前為止的所有Tick資料(第13行)，ReadTicks會回傳這次讀回的筆數(row_count)，接著我們就用[for迴圈](https://xshelp.xq.com.tw/XSHelp/?HelpName=For&group=CONTROLFLOW)來檢視讀到的Tick資料。 ReadTicks讀回的Tick資料會存放在tick_array內，最新的一筆是第一個橫列(row), 前一筆是第二個row，以下類推。每一個row內包含11個欄位(column)，內容分別是：

- 第1個column是日期
- 第2個column是時間
- 第3個column是成交價
- 第4個column是成交量
- 第5個column是內外盤註記
- 第6個column是這一筆Tick的編號(SeqNo)
- 如果ReadTicks遇到多筆連續成交序列時，他會把這些Ticks都合併成1個row，第7個column存放的是這個連續成交序列總共有多少筆
- 如果這是連續成交序列的話，第8個column存放的是這個連續成交序列的第一筆的位置，第9個column存放的則是這個連續成交序列最後一筆的位置
- 第10個column存放的是這些連續成交序列的總成交量，如果這不是連續成交序列的話，這個column存放的則是這一筆的成交量(跟第四個column相同
- 第11個column存放的是這些連續成交序列的總成交金額(元)，如果這不是連續成交序列的話，這個column存放的則是這一筆的成交金額
所以透過ReadTicks之後，我們就可以很簡單的檢查這一筆資料是否符合我們要篩選的規則if tick_array[idx, 5] = filterMode and tick_array[idx, 10] >= filterVolume then ... 上面這個判斷式內，tick_array[idx, 5]是內外盤註記，而tick_array[idx, 10]則是這一筆成交的總量(或是連續成交序列的總量)！ 到這裡為止，我們已經跟大家說明了Tick資料的所有欄位以及應用的方式，大家如果有任何建議或是問題的話，麻煩到我們的臉書或是XS技術支援區留言。下次再見囉。[##### 前一篇

Tick欄位的應用](https://www.xq.com.tw/lesson/xspractice/tick欄位的應用/)[##### 下一篇

選股中心創掛牌新高與大單欄位的應用](https://www.xq.com.tw/lesson/xspractice/選股中心創掛牌新高與大單欄位的應用/)
