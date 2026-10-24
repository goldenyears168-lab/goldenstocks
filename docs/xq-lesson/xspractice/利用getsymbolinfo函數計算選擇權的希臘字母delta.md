---
source: https://www.xq.com.tw/lesson/xspractice/利用getsymbolinfo函數計算選擇權的希臘字母delta/
title: 利用GetSymbolInfo函數計算選擇權的希臘字母Delta - XQ 全球贏家
fetched: 2026-10-23
---

# 利用GetSymbolInfo函數計算選擇權的希臘字母Delta

###### 2018-12-19 20:42:24

複製連結

市場上投資工具五花八門，常見的就有股票、期貨、選擇權、權證等等。時至今日，選擇權和權證已經是很普遍使用的投資標的。評估一檔股票值不值得投資，我們會用基本面或技術面等條件篩選，利用XS都可以輕鬆做到。在投資選擇權時，用來評價選擇權風險就是所謂的希臘字母（Delta、Gamma、Theta、Vega、Rho）。這項功課，現在XS也能幫你完成。 我們以選擇權的Delta為例。打開[BSDelta](http://xshelp.xq.com.tw/api.aspx?a=BSDelta&b=sys)函數我們可以看到，在計算選擇權希臘字母時我們會需要以下資訊：

- 買賣權
- 標的價格
- 履約價
- 到期天數
- 無風險利率
- 持有成本
- 波動率
其中，買賣權、履約價、到期天數算是商品的基本資料，是固定的。在6.42版以前, XS沒有提供GetSymbolInfo函數的狀況下，我們會需要一點巧門來取得這些資訊。1買賣權=midstr(symbol,6,1); //利用商品名稱取得 2履約價=strtonum(midstr(symbol,7,strlen(symbol) - 9)); //利用商品名稱取得 3到期天數利用daystoexpirationtf函數，直接寫死找出每月第三個星期三也就是因為這些規則是死的，利用商品名稱取得資訊的方式讓我們之前系統內建指標只能提供台指選專用的指標。更不要說碰到颱風或農曆春節導致結算日異動時，這個指標就會失真。 在有了GetSymbolInfo函數之後，這些資訊我們就可以直接使用。1買賣權=GetSymbolInfo("買賣權"); 2履約價=GetSymbolInfo("履約價"); 3到期天數=DateDiff(GetSymbolInfo("到期日"), Date) + 1;所有的選擇權商品，XS都會提供這些商品基本資料，我們就可以寫出通用版的Delta指標，而不是僅限台指選專用。改寫後的腳本如下：1input: 2 iRate100(2,"無風險利率%"), 3 iHV(20,"標的歷史波動率計算期間"); 4 5 variable:vStrikePrice(0),vVolity100(0),vTTMdays(0); 6 7 if symboltype <> 5 then 8 raiseruntimeerror("僅支援選擇權"); 9 10 if iHV > 0 then 11 vVolity100 = HVolatility(getsymbolfield("Underlying","收盤價","D"),iHV) 12 else 13 vVolity100 = 20; 14 15 vStrikePrice = getsymbolinfo("履約價"); 16 vTTMdays = DateDiff(GetSymbolInfo("到期日"), Date) + 1; 17 18 value1 = bsdelta(leftstr(getsymbolinfo("買賣權"),1), //C表買權、P表賣權 19 getsymbolfield("Underlying","收盤價"), //標的價格 20 vStrikePrice, //履約價 21 vTTMdays, //到期天數 22 iRate100, //無風險利率 23 0, //持有成本 24 vVolity100); //波動率 25 26 plot1(value1,"Delta");計算Delta需要的所有參數，都是透過GetSymbolInfo取得系統資料，所以現在不止台指選可以用，其他電子選、金融選甚至是個股選擇權都可以使用這個Delta指標。 有沒有注意到在取得標的價格的地方我們是怎麼寫的？1getsymbolfield("Underlying","收盤價");是的，這次我們同時強化了GetSymbolField的功能，在商品欄的位置可以直接使用"Underlying"或"標的"動態取得標的商品。如此一來，不止是本篇文章說明的選擇權希臘字母可以計算，要自行計算期貨商品的價差值也是可行的。 以上是GetSymbolInfo的應用範例，GetSymbolInfo完整的支援項目清單請參考[XSHelp說明](http://xshelp.xq.com.tw/api.aspx?a=GetSymbolInfo&b=bif)[##### 前一篇

關於背離的寫法](https://www.xq.com.tw/lesson/xspractice/關於背離的寫法/)[##### 下一篇

利用InputKind函數製作跨頻率週期的選取介面](https://www.xq.com.tw/lesson/xspractice/利用inputkind函數製作跨頻率週期的選取介面/)
