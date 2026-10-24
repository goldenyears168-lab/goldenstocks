---
source: https://www.xq.com.tw/lesson/xspractice/xs在可轉債的應用/
title: XS在可轉債的應用 - XQ 全球贏家
fetched: 2026-10-23
---

# XS在可轉債的應用

###### 2025-06-12 16:50:54

複製連結

XS已增添支援可轉債商品的功能，現在可以使用 XS 選股中心與 XS 指標，透過可轉債商品的特徵，來尋找未來可能上漲的可轉債標的股。 可轉債是允許持有者，按照轉換價格將債券轉換為公司的普通股，如果債券到期時，沒有轉換為股票，則公司須以現金買回債權，償還持有者。 策略想法是，可轉債快到期時，若公司沒有足夠資金償還給持有者，公司可能會拉抬股價超過轉換價格，希望讓持有者手中的可轉債，轉換成股票，這樣公司就不用以資金償還給債權人。 所以我們可以撰寫以下 3 個 XS 選股條件，篩選出可能會被公司派拉抬的股票名單。

- 近期內快到期
- 可轉債剩餘張數有一定數量
- 標的股價尚未到達轉換價格
 XS選股腳本的範例語法如下：input:_Length(120, "可轉債N天內到期"),_Vol(0 ,"可轉債剩餘張數大於X"); //value1 = 已轉換比例 if GetSymbolInfo("發行張數") <> 0 then value1 = 1 - GetField("CB剩餘張數", "W") / GetSymbolInfo("發行張數"); condition1 = DateDiff(GetSymbolInfo("到期日"),Date) <= _Length;//N天內可轉債到期 condition2 = GetField("CB剩餘張數", "W") > _Vol;//可轉債剩餘張數 //標的股價尚未到達轉換價格 → 股票轉換價差(%) < 0 condition3 = (GetSymbolField("Underlying", "收盤價") - GetField("轉換價格")) / GetField("轉換價格") * 100 < 0; ret = condition1 and condition2 and condition3; outputField1(DateDiff(GetSymbolInfo("到期日"),Date),"N天內可轉債到期"); outputField2(GetSymbolInfo("到期日"),"可轉債到期日"); outputField3(value1 * 100,"已轉換比例"); outputField4(GetField("CB剩餘張數", "W"),"可轉債剩餘張數"); outputField5(GetSymbolInfo("發行張數"),"發行張數(張)"); outputField6((GetSymbolField("Underlying", "收盤價") - GetField("轉換價格")) / GetField("轉換價格") * 100 ,"股票轉換價差(%)"); outputField7(GetField("轉換價格"),"轉換價格"); outputField8( GetSymbolField("Underlying", "收盤價"),"標的股價"); outputField9( getsymbolFieldDate("Underlying", "收盤價"),"標的股價更新時間");在 XS 選股腳本編譯成功，加入選股後，記得在商品範圍選擇「台股可轉債(系統)」 [![](../images/ded8e06af6.png)](https://www.xq.com.tw/wp-content/uploads/2024/05/可轉債01.png)完成後，若有購買選股模組，建議設定每日自動執行，方便在自選股商品檢視每日篩選的結果。 [![](../images/2e9e3d9078.png)](https://www.xq.com.tw/wp-content/uploads/2024/05/可轉債02.png)最後，再用這些股票，搭配技術分析頁面做功課，決定要購入哪些股票，期待未來股價會因為可轉債到期而上漲。 下圖面向螢幕左邊的報價組合頁面，可以設定商品組合為「每日自動選股清單」。 [![](../images/2294ec61fa.png)](https://www.xq.com.tw/wp-content/uploads/2024/05/可轉債03.png)除此之外，在 7.13.01 / 3.13.01 版本中，XS 指標與選股中心有新增一些可轉債相關的XS指標與選股條件如下兩個表格，希望幫助初學 XS 的使用者，可以用系統內建的可轉債 XS 指標與 XS 選股條件，進行分析：

 指標名稱 | 支援頻率 | 說明 |

 股東人數 | 日以上的頻率 | 可轉債是每個月更新一次。 |

 CB轉換溢價率 | 全部 | 轉換溢價率(%)＝（可轉債市場價格－轉換價格）÷轉換價格×100 比率大於零時，為溢價率；比率小於零時，為折價率。 |

 CB剩餘張數 | 日以上的頻率 | 目前流通在外的可轉債張數（就是還沒有轉換的）每週更新。 |

 

 選股欄位 | 選股條件 |

 轉換價格 | 轉換價格大於X |

 轉換價格 | 轉換價格小於X |

 轉換價格 | 標的股價大於轉換價格X%、小於N% |

 CB剩餘張數 | 連續N週CB剩餘張數減少 |

 CB剩餘張數 | 連續N週CB剩餘張數減少X%以上 |

 以上是XS支援可轉債的應用介紹，若大家有任何的心得與建議，也歡迎來信至 XQservice@XQ.com.tw 回饋分享。[##### 前一篇

如何使用函數取得商品的實際庫存數量](https://www.xq.com.tw/lesson/xspractice/如何使用函數取得商品的實際庫存數量/)[##### 下一篇

XS函數「isfirstcall」說明](https://www.xq.com.tw/lesson/xspractice/xs函數「isfirstcall」說明/)
