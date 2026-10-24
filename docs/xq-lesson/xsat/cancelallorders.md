---
source: https://www.xq.com.tw/lesson/xsat/cancelallorders/
title: CancelAllOrders語法說明 - XQ 全球贏家
fetched: 2026-10-23
---

# CancelAllOrders語法說明

###### 2025-07-18 11:27:07

複製連結

大家好，今天跟大家介紹一個XS自動交易的新語法：CancelAllOrders，目的是用來刪除委託單。 

## 設計目的

 在某些交易情境底下，使用者可能想要做的是取消目前尚未成交的委託，例如送出委託單之後如果等10分鐘沒有完全成交的話，就取消這一筆委託。 以目前的設計方式，如果想要刪除委託單的話，可以使用SetPosition的方式來完成：if Filled <> Position then begin SetPosition(Filled); end;可是，這個腳本執行時，可能會因為成交的先後順序而產生與預期不同的執行方式。if Condition1 and Position = 0 then begin // 進場 SetPosition(2); end else if Condition2 and Position = 2 and Position <> Filled then begin // 試著刪除委託 SetPosition(Filled); end;假設腳本執行時，Condition1成立了，所以呼叫了SetPosition(2)。 接下來Condition2成立了，如果此時尚未成交(Filled = 0)，Position <> Filled，所以腳本接下來呼叫了SetPosition(0)。 當XS執行SetPosition(0)時，他會檢查目前委託單的成交狀況，此時應該有一筆買進2張的委託單，尚未成交，所以XS就會送出刪單的指令，當這一筆委託單被刪除之後，這時候就符合Position = 0的定義了。 可是，萬一刪除這筆委託單時，發現這筆委託單成交了1張，此時Filled的數值是1，可是腳本希望把Position設定成 0，所以接下來XS就會送出賣出1張的委託單，讓部位變成0。 這個賣出的行為，可能不是使用者預期的，也許使用者會希望先刪除委託單，萬一成交的話，那也先留著看之後怎麼處理。為了讓使用者有更好的控制權，於是這一版我們就增加了CancelAllOrders的語法。 

## 語法說明

 CancelAllOrders的語法很簡單：CancelAllOrders() CancelAllOrders(label:="指令標記")當XS執行CancelAllOrders指令時，此時會依序做以下的動作：

- 檢查目前的Filled跟Position是否有差異，如果沒有差異的話，就不需要執行。
- 檢查目前委託單的狀態，如果委託單尚未完全成交的話，則送出刪單的指令。
- 等待刪單指令執行完成後，依照實際成交情形，更新Filled以及Position的數值。
 

## 執行邏輯以及Position的異動順序

 我們延伸先前的範例，把刪單的動作改成呼叫CancelAllOrders：if Condition1 and Position = 0 then begin // 進場 SetPosition(2); end else if Condition2 and Position = 2 and Position <> Filled then begin // 刪除委託 CancelAllOrders(); end;以下我們逐步來trace腳本的執行流程。 

### 時間點#1

腳本剛開始執行時，Position = 0，Filled = 0。 

### 時間點#2

假設接下來Condition1成立了，呼叫了SetPosition(2)。 XS跑完這一次洗價之後，會送出一筆買進2張的委託單，同時把Position改成2，Filled還是0。 

### 時間點#3

假設過一陣子，這張委託單成交了一張，此時XS會更新Filled (=1)，下一次洗價時就會印出Position = 2 Filled = 1的訊息。 

### 時間點#4

假設接下來Condition2成立了，這時候Position = 2，Filled = 1，Position <> Filled，所以呼叫了CancelAllOrders。 XS跑完這一次洗價之後，開始執行CancelAllOrders。執行CancelAllOrders的第一個動作，是檢查目前委託單的狀態。此時應該有一筆買進2張的委託單，成交了1張，所以XS接下來會送出刪單的動作。 

### 時間點#5

假設刪單還沒有回報之前，又觸發了一次洗價。 請注意：這時候印出來的Position的數值還是2，Filled的數值也還是1。 也就是說，XS呼叫CancelAllOrders之後，不會更改Position的數值。會這樣子設計的原因，是因為刪單後的實際刪除數量必須等到執行完成時才會知道，所以無法事先知道刪單後的Position會是多少。 

### 時間點#6

假設接下來交易主機端回傳了這次刪單的結果，有可能是：

- 取消了1張，所以此時腳本的Position跟Filled都會變成1。
- 多成交了1張，所以取消了0張，此時腳本的Position跟Filled都會變成2。
這個時候，XS才會去更改Position跟Filled的數值。在下一次洗價時，腳本就會發現Position的數值跟Filled的數值是一樣的了。 如果要判斷刪單是否已經執行完成，一種作法，是在呼叫CancelAllOrders時設立一個旗標(State = 1)，然後檢查這個旗標以及Position = Filled (下方紅字處)，當兩者都成立時，就表示刪單已經執行完成了，接下來腳本就可以依照此時的Filled來決定接下來該怎麼運作。var: intrabarpersist State(0); Print("Position=", NumToStr(Position, 0), "Filled=", NumToStr(Filled, 0)); if Condition1 and Position = 0 then begin // 進場 SetPosition(2); end else if Condition2 and Position = 2 and Position <> Filled then begin // 刪除委託 CancelAllOrders(); State = 1; end else if State = 1 and Position = Filled then begin // 刪單完成了 end; 

## CancelAllOrders執行期間不會進行部位異動

 當腳本呼叫CancelAllOrders後XS會送出了刪單，可是萬一在這一筆刪單的狀態還沒有完全確定之前，腳本又再次呼叫了SetPosition的話，那會發生什麼事情？ 為了確保委託單的執行順序，一旦XS開始執行CancelAllOrders，在委託單的刪除狀態完全確認之前，任何的SetPosition指令，都不會被立即執行。 會這樣子做的原因，是因為刪單還沒有執行完成之前，XS不知道實際的Filled，所以也不會知道該怎麼送出委託單來達到這個新的SetPosition的要求！ 如果在CancelAllOrders執行期間，腳本真的呼叫了SetPosition的話， 那麼XS會記住最後一次的SetPosition的數值，然後等到CancelAllOrders執行完成時，依照最新的Filled，再決定該如何執行這一次的SetPosition。 小編會建議有這樣子交易需求時，可以在腳本內設計自訂的旗標變數(例如上面範例腳本內的State變數)，另外搭配Position/Filled的數值比對，這樣子比較確定目前該執行什麼樣子的邏輯。[##### 前一篇

自動交易語法：取得「交易帳號」使用說明](https://www.xq.com.tw/lesson/xsat/at_id/)[##### 下一篇

XS策略雷達](https://www.xq.com.tw/lesson/sensor)
