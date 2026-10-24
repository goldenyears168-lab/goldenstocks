---
source: https://www.xq.com.tw/lesson/xspractice/xs函數「isfirstcall」說明/
title: XS函數「isfirstcall」說明 - XQ 全球贏家
fetched: 2026-10-23
---

# XS函數「isfirstcall」說明

###### 2025-06-12 16:51:47

複製連結

isfirstcall 能迅速解決腳本撰寫的負擔，它最重要的作用就是：在特定事件下的第一次觸發。 這麼說可能有些抽象，舉凡變數重置、在 K 棒內限制觸發次數等等，都是腳本撰寫上常會用到的邏輯。 如果你曾經遇過「實際執行腳本時，並不如想像中在達成條件後僅觸發一次，而是瘋狂觸發」、「每次都要為了限制觸發，而花不少時間空間設計腳本」或是「明明條件沒有符合，卻還是觸發了(因為沒有重置條件 or 變數)」，那 isfirstcall 能解決很多這方面的困擾。 isfirstcall 要傳入字串形式的事件代碼，回 true / false，以下便來說明幾種 isfirstcall 目前支援的使用情境。

### **一、isfirstcall(" ")：此次執行的第一次洗價**

只會 true 一次，後續洗價維持 false。 會用到這個事件，通常是為了在腳本啟動後，處理需要引用的資料或變數，而且只需要在一開始執行一次，之後不再需要。 只在第一次洗價時判斷條件，之後都不需再次判斷。

- 不用 isfirstcall 的寫法：
{先宣告變數_firstTime，來限制觸發一次} var:_firstTime(0); condition1 = ……; if _firstTime = 0 then begin if condition1 then …… ; _firstTime = 1; //第一次洗價後_firstTime就會持續=1，此後不會再=0了 end;  

- isfirstcall(" ") 的寫法：
condition1 = ……; if isfirstcall(" ") then condition1 then ……;  

### **二、isfirstcall("Bar")：此根 Bar 的第一次洗價**

換 Bar 就會更新為 true 一次，其餘洗價維持 false。 這些應該是 XS 玩家最常用到的情境了： **(一) 逐筆洗價時，援引「上一根 Bar 最後一次的洗價結果」，做 Bar 內單次觸發**

- 不用 isfirstcall 的寫法：
{宣告一個intrabarpersist性質的變數_Bar，來限制Bar內觸發一次} var: intrabarpersist _Bar(0); condition1 = ......; if _Bar <> currentbar then begin if condition1[1] then ......; _Bar = currentbar; //更新 _Bar的數值 end; 

- isfirstcall("Bar") 的寫法：
condition1 = ......; if isfirstcall("Bar") and condition1[1] then ......; **(二) Bar 內 N 次觸發，使用最新洗價的結果來判斷**

- 不用 isfirstcall 的寫法：
input:N(3,"限制觸發次數"); var: intrabarpersist _Bar(0), intraBarPersist counts(0); {將累積觸發次數counts，在Bar內第一次洗價時歸0} if _Bar <> currentbar then begin counts = 0; _Bar = currentbar; end; {Bar內最多累積N次觸發} condition1 = ......; if condition1 and counts < N then begin ......; counts += 1; end;  

- isfirstcall ("Bar") 的寫法：
input:N(3,"限制觸發次數"); var:intraBarPersist counts(0); {將累積觸發次數counts，在Bar內第一次洗價時歸0} if isfirstcall("Bar") then counts = 0; {Bar內最多累積N次觸發} if condition1 and counts < N then begin ......; counts += 1; end;  

### **三、isfirstcall("Date")：此交易日的第一次洗價**

依照執行商品的交易日來決定何時換日，例如：

- 股票會在 09:00 時換日
- 期貨(一般)會在 08:45 時換日
- 期貨(全日)會在 15:00 時換日
換交易日就會更新為 true 一次，其餘洗價維持 false。 所以會根據執行商品的交易日更新時間，來決定第一次洗價的時機，會與單純用 date<>date[1] 來判斷 00:00 時換日，有所不同。 **隔日沖開盤出場**

- 若是股票、期貨(一般)，不用 isfirstcall 的寫法：
if date<>date[1] and Position > 0 then SetPosition(0, label:="換日開盤清倉"); 

- 若是期貨(全日)，不用 isfirstcall 的寫法(通用於全部商品)：
{用isSessionFirstBar來鎖定交易日的第一根Bar，再加上單根Bar內限制觸發一次的邏輯} var:intraBarPersist _Bar; if isSessionFirstBar and _Bar <> currentBar and Position > 0 then begin SetPosition(0, label:="換日開盤清倉"); _Bar = currentBar; end;  

- isfirstcall("Date") 的寫法(通用於全部商品)：
if IsFirstCall("Date") and Position > 0 then SetPosition(0, label:="換日開盤清倉");  

### **四、isfirstcall("Realtime")：進入即時洗價區間的第一次洗價**

從資料歷史區間轉進即時區間後，就會更新為 true 一次，其餘洗價維持 false。 如題，只有在即時洗價區間才會成立，洗價區間分為歷史與即時，歷史洗價區間就是所謂「資料讀取筆數(totalbar)」或「自動交易的策略部位計算」的範圍；即時洗價區間則是在資料讀取完成後，進入盤中即時行情的範圍。 **策略進入即時洗價後 N 分鐘，停止策略**

- 不用 isfirstcall 的寫法：
input:N(60,"幾分鐘後停止策略"); var:_firstTime(0), _startTime(0); if _firstTime = 0 then begin _startTime = currentTime; _firstTime = 1; end; if currentTime >= TimeAdd(_startTime,"MM",N) then raiseRunTimeError("時間到停止策略");  

- isfirstcall("Date") 的寫法：
input:N(60,"幾分鐘後停止策略"); var: _startTime(0); if isfirstcall("Realtime") then _startTime = currentTime; if currentTime >= TimeAdd(_startTime,"MM",N) then raiseRunTimeError("時間到停止策略");  

### **五、isfirstcall("RealBar")：此交易日進入即時洗價區間，首次產生成交事件後的第一次洗價**

當腳本從歷史資料區間進入即時洗價區間，並且在當日首次產生成交事件後，第一次洗價時，isfirstcall("RealBar") 會回傳 true，之後的洗價都回傳 false。 isfirstcall("Realtime") 在進入即時洗價區間就會成立，而 isfirstcall("RealBar") 則需要額外等待當日第一筆成交事件發生後才會觸發。在多數情況下，兩者觸發時機接近。 兩者在特殊情境下的差異： 在某些市場狀況下，進入即時區間後，不一定立即有成交事件發生，例如：

- 市場暫緩開盤： 因故市場延遲開盤，進入即時區間後可能沒有成交。
- 開盤後暫無成交： 在極端清淡的行情下，開盤後可能短時間內沒有成交產生。
在這些情境中， isfirstcall("Realtime") 會直接在進入即時區間時回傳 true，而 isfirstcall("RealBar") 則會持續等待，直到當日第一筆成交事件出現才回傳 true。 此事件適用於需要確保在市場實際開始交易並有成交量產生後，才執行特定操作的策略，例如：

- 等待開盤後第一筆成交出現，才開始判斷進場條件： 避免在市場尚未正式開始交易 (例如暫緩開盤或無成交) 時，就提前觸發進場訊號。
- 確認有成交量後，才啟動需要成交量數據的指標計算： 確保指標計算的數據基礎是建立在實際成交之上。

### **補充：isfirstcall 的底層邏輯**

isfirstcall 不會受到 if…else… 等流程函數限制，它的作法是讀取當次洗價，是否為事件定義的第一次洗價：若是則為 true；若不是，那就會維持 false 直到事件再次更新。 舉 isfirstcall(" ") 為例，只有此次執行的第一次洗價為 true，若腳本設定的資料讀取筆數 (settotalbar)<>0，或是交易策略有設定歷史策略部位計算，那 isfirstcall(" ") 就會在歷史區間的第一根 Bar 為 true，之後都會維持 false 的狀態。 不會因為在盤中啟動腳本，才在即時區間的第一次洗價為 true，即時區間的第一次洗價要使用 isfirstcall("Realtime")。 另外，若是用 if… 等流程函數將 isfirstcall 包住，導致避開事件定義的第一次洗價，那之後即使通過流程函數並第一次呼叫 isfirstcall，那也會是 false。 第一段所謂「事件再次更新」的意思是，例如 isfirstcall("Bar") 在進到新的 Bar 時、isfirstcall("Date") 在跨到新的交易日時、isfirstcall("Realtime") 每次從歷史區間跨到即時區間時，會重新為 true 一次。[##### 前一篇

XS在可轉債的應用](https://www.xq.com.tw/lesson/xspractice/xs在可轉債的應用/)[##### 下一篇

XScript 編輯器已支援 *.xs 副檔名的檔案](https://www.xq.com.tw/lesson/xspractice/xscript-編輯器已支援-xs-副檔名的檔案/)
