---
source: https://www.xq.com.tw/lesson/xspractice/利用inputkind函數製作跨頻率週期的選取介面/
title: 利用InputKind函數製作跨頻率週期的選取介面 - XQ 全球贏家
fetched: 2026-10-23
---

# 利用InputKind函數製作跨頻率週期的選取介面

###### 2018-12-19 20:43:30

複製連結

使用XS撰寫完成策略後，在介面上輸入參數可能會輸入錯誤的數值，雖然執行後會秀出錯誤訊息告知，但時常會造成困擾，所以我們提供InputKind函數讓使用者可以在腳本內控制介面的行為，讓大家能在介面選擇輸入值，就能避免輸入錯誤的狀況發生。 我們以KD指標的跨頻率語法為例。打開[xfMin_stochastic](http://xshelp.xq.com.tw/api.aspx?a=xfMin_stochastic&b=sys)函數我們可以看到，在計算有支援跨分鐘頻率的KD指標時我們會需要以下資訊：

- 頻率
- 資料期數
- K值平滑期數
- D值平滑期數
- 輸出RSV值
- 輸出K值
- 輸出D值
其中，指定的分鐘頻率僅支援1分鐘、5分鐘、10分鐘、15分鐘、30分鐘與60分鐘，以上6種分鐘頻率。在6.42版以前，XS沒有提供InputKind函數的狀況下，我們會使用input函數指定分鐘頻率，但是在介面可能會輸入到沒有支援的指定頻率，執行後發現此錯誤往往讓使用者覺得困擾。 在有了InputKind函數之後，我們就能將可支援的分鐘頻率，製作成選項顯示在介面上，讓使用者在這些選項中選擇，就能避免輸入錯誤的狀況發生。 以上是InputKind的應用範例，InputKind使用說明可以參考[XSHelp說明](http://xshelp.xq.com.tw/api.aspx?a=inputkind)。改寫後的跨分鐘與日頻率KD指標腳本如下： // 跨頻率KD指標，預設跨頻率為30分 // 不支援大頻率跨小頻率，例如： // 不支援主頻率60分鐘，跨頻率計算30分鐘KD技術指標。 // input: Length(9, "天數"), RSVt(3, "RSVt權數"), Kt(3, "Kt權數"), FreqType("30", "跨頻率週期", inputkind:=dict(["1分鐘","1"],["5分鐘","5"],["10分鐘","10"],["15分鐘","15"],["30分鐘","30"],["60分鐘","60"],["日","D"],["還原日","AD"])); variable: rsv(0), k(0), _d(0); if barfreq <> "Tick" and barfreq <> "Min" and barfreq <> "D" and barfreq <> "AD" then raiseruntimeerror("此範例僅支援分鐘、日與還原日頻率"); xfMin_stochastic(FreqType, Length, RSVt, Kt, rsv, k, _d); Plot1(k, "分鐘與日K(%)"); Plot2(_d, "分鐘與日D(%)"); // 防呆，大頻率跨小頻率時，在線圖秀不支援 // switch (FreqType) begin case "1": if barfreq <> "Tick" and barfreq <> "Min" then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於1分鐘"); if barinterval <> 1 then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於1分鐘"); setplotlabel(1, "1分K(%)"); setplotlabel(2, "1分D(%)"); case "5": if barfreq <> "Tick" and barfreq <> "Min" then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於5分鐘"); if barinterval <> 1 and barinterval <> 2 and barinterval <> 3 and barinterval <> 5 then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於5分鐘"); setplotlabel(1, "5分K(%)"); setplotlabel(2, "5分D(%)"); case "10": if barfreq <> "Tick" and barfreq <> "Min" then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於10分鐘"); if barinterval <> 1 and barinterval <> 2 and barinterval <> 3 and barinterval <> 5 and barinterval <> 10 then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於10分鐘"); setplotlabel(1, "10分K(%)"); setplotlabel(2, "10分D(%)"); case "15": if barfreq <> "Tick" and barfreq <> "Min" then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於15分鐘"); if barinterval <> 1 and barinterval <> 2 and barinterval <> 3 and barinterval <> 5 and barinterval <> 10 and barinterval <> 15 then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於15分鐘"); setplotlabel(1, "15分K(%)"); setplotlabel(2, "15分D(%)"); case "30": if barfreq <> "Tick" and barfreq <> "Min" then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於30分鐘"); if barinterval <> 1 and barinterval <> 2 and barinterval <> 3 and barinterval <> 5 and barinterval <> 10 and barinterval <> 15 and barinterval <> 20 and barinterval <> 30 then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於30分鐘"); setplotlabel(1, "30分K(%)"); setplotlabel(2, "30分D(%)"); case "60": if barfreq <> "Tick" and barfreq <> "Min" then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於60分鐘"); if barinterval <> 1 and barinterval <> 2 and barinterval <> 3 and barinterval <> 5 and barinterval <> 10 and barinterval <> 15 and barinterval <> 20 and barinterval <> 30 and barinterval <> 45 and barinterval <> 60 then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於60分鐘"); setplotlabel(1, "60分K(%)"); setplotlabel(2, "60分D(%)"); case "D": if barfreq <> "Tick" and barfreq <> "Min" and barfreq <> "D" and barfreq <> "AD" then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於日"); setplotlabel(1, "日K(%)"); setplotlabel(2, "日D(%)"); case "AD": if barfreq <> "Tick" and barfreq <> "Min" and barfreq <> "D" and barfreq <> "AD" then raiseruntimeerror("不支援大頻率跨小頻率：主頻率大於還原日"); setplotlabel(1, "還原日K(%)"); setplotlabel(2, "還原日D(%)"); end;[![](../images/4b45507181.png)](https://www.xq.com.tw/wp-content/uploads/2018/12/input_1-1.png)[##### 前一篇

利用GetSymbolInfo函數計算選擇權的希臘字母Delta](https://www.xq.com.tw/lesson/xspractice/利用getsymbolinfo函數計算選擇權的希臘字母delta/)[##### 下一篇

盤中即時資料欄位的應用](https://www.xq.com.tw/lesson/xspractice/盤中即時資料欄位的應用/)
