---
source: https://www.xq.com.tw/learning/xs-編輯器：如何在選股腳本使用可轉債商品？/
title: 用戶說明- XS 在可轉債的應用 - XQ 全球贏家
fetched: 2026-10-23
---

[學習地圖](https://www.xq.com.tw/learning/)XS 編輯器：如何在選股腳本使用可轉債商品？

# XS 編輯器：如何在選股腳本使用可轉債商品？

###### 2024-12-10 17:14:44

學習分類：[台股進階模組](https://www.xq.com.tw/learning/?category=tw_source)內容標籤：[市場資訊](https://www.xq.com.tw/learning/?tags=marketing_info)[策略開發](https://www.xq.com.tw/learning/?tags=trading_strategy)

複製連結

[![XS 編輯器：如何在選股腳本使用可轉債商品？](images/47cdf2190d.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/31.png)在 7.13.01 / 3.13.01 版本中，XS增添支援可轉債商品的功能，現在可以使用 XS 選股中心與 XS 指標，透過可轉債商品的特徵，來尋找未來可能上漲的可轉債標的股。

## **1. 在 XS 中應用可轉債有什麼好處？**

 

 **好處分類** | **詳細說明** |

 篩選潛力標的 | - 基於可轉債特徵快速篩選潛力股，例如到期日、剩餘張數和轉換價格。 - 提前布局可能因可轉債轉換而上漲的標的股。 |

 提供精確的數據支持 | - 內建多項可轉債指標，如轉換溢價率和剩餘張數，提升分析效率。 - 支持日以上頻率數據更新，滿足不同分析需求。 |

 高效的選股與技術分析整合 | - 可轉債選股與技術分析無縫結合，進一步驗證潛力股走勢。 - 每日自動選股功能簡化篩選與檢視流程。 |

 提升策略靈活性與精準性 | - 支持自訂選股條件，靈活應對市場變化。 - 快速測試交易策略，驗證投資假設。 |

 

## **2. 如何在 XS 中應用可轉債相關功能？**

 先簡單介紹可轉債，可轉債是允許持有者，按照轉換價格將債券轉換為公司的普通股，如果債券到期時，沒有轉換為股票，則公司須以現金買回債權，償還持有者。 再來說明策略想法，可轉債快到期時，若公司沒有足夠資金償還給持有者，公司可能會拉抬股價超過轉換價格，希望讓持有者手中的可轉債，轉換成股票，這樣公司就不用以資金償還給債權人。 所以我們可以撰寫以下 3 個 XS 選股條件，篩選出可能會被公司派拉抬的股票名單。

- 近期內快到期
- 可轉債剩餘張數有一定數量
- 標的股價尚未到達轉換價格
 XS選股腳本的範例語法如下：``input:_Length(120, "可轉債N天內到期"),_Vol(0 ,"可轉債剩餘張數大於X"); //value1 = 已轉換比例 if GetSymbolInfo("發行張數") <> 0 then value1 = 1 - GetField("CB剩餘張數", "W") / GetSymbolInfo("發行張數"); condition1 = DateDiff(GetSymbolInfo("到期日"),Date) <= _Length;//N天內可轉債到期 condition2 = GetField("CB剩餘張數", "W") > _Vol;//可轉債剩餘張數 //標的股價尚未到達轉換價格 → 股票轉換價差(%) < 0 condition3 = (GetSymbolField("Underlying", "收盤價") - GetField("轉換價格")) / GetField("轉換價格") * 100 < 0; ret = condition1 and condition2 and condition3; outputField1(DateDiff(GetSymbolInfo("到期日"),Date),"N天內可轉債到期"); outputField2(GetSymbolInfo("到期日"),"可轉債到期日"); outputField3(value1 * 100,"已轉換比例"); outputField4(GetField("CB剩餘張數", "W"),"可轉債剩餘張數"); outputField5(GetSymbolInfo("發行張數"),"發行張數(張)"); outputField6((GetSymbolField("Underlying", "收盤價") - GetField("轉換價格")) / GetField("轉換價格") * 100 ,"股票轉換價差(%)"); outputField7(GetField("轉換價格"),"轉換價格"); outputField8( GetSymbolField("Underlying", "收盤價"),"標的股價"); outputField9( getsymbolFieldDate("Underlying", "收盤價"),"標的股價更新時間"); ```在XS選股腳本編譯成功，加入選股後，記得在商品範圍選擇「台股可轉債(系統)」 [![在XS選股腳本編譯成功，加入選股後，記得在商品範圍選擇「台股可轉債(系統)」](images/efeca9a527.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image4-31.png)完成後，若有購買選股模組，建議設定每日自動執行，方便在自選股商品檢視每日篩選的結果 [![完成後，若有購買選股模組，建議設定每日自動執行，方便在自選股商品檢視每日篩選的結果](images/127505450f.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image1-34.png)最後，再用這些股票，搭配技術分析頁面做功課，決定要購入哪些股票，期待未來股價會因為可轉債到期而上漲。

- 下圖面向螢幕左邊的報價組合頁面，可以設定商品組合為「每日自動選股清單」
[![下圖面向螢幕左邊的報價組合頁面，可以設定商品組合為「每日自動選股清單」](images/6b0305aeeb.png)](https://www.xq.com.tw/wp-content/uploads/2024/12/image2-33.png)除此之外，我們在 7.13.01 / 3.13.01 版本中，XS 指標與選股中心有新增一些可轉債相關的XS指標與選股條件如下兩個表格，希望幫助初學 XS 的使用者，可以用系統內建的可轉債 XS 指標與 XS 選股條件，進行分析：

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

 

## **3. 總結**

XS 的可轉債應用功能為投資者提供了一個完整的選股與分析解決方案，從篩選即將到期且可能被公司拉抬的可轉債標的，到結合技術分析深入研究股價走勢，幫助用戶更精準地發現投資機會。此外，平台還內建了多項可轉債相關的 XS 指標與選股條件，即使是初學者也能快速上手，將分析結果應用於每日自動選股與自選股清單中。這項更新不僅提升了可轉債投資的可操作性，也為投資者提供了一個高效的交易工具，進一步助力於實現投資增值的目標。我是 XQ 小編，我們下次見。

## 最新文章

[【功能更新】量化積木新增「等額（100 萬元）」交易模式！三大更新重點全解析](https://www.xq.com.tw/learning/xq-blocks-equal-value-trading-mode/)[【新手也能上手】量化積木支援「期貨商品」啦！](https://www.xq.com.tw/learning/xqblockfuture/)[【常見情境實戰篇】新手也能懂的期貨策略應用](https://www.xq.com.tw/learning/xqblockfuturestrategy/)[【新春活動】2026 金馬年開運紅包：新用戶專屬三大量化投資策略公開！](https://www.xq.com.tw/learning/xq2026threestragy/)[為什麼要做「量化交易」？避開散戶 3 大心態，打造你的投資 SOP](https://www.xq.com.tw/learning/quanttrading3retailinvestorminds/)

## 學習分類

[不分模組](https://www.xq.com.tw/learning/)[基本功能](https://www.xq.com.tw/learning/?category=basic)[尊榮會員](https://www.xq.com.tw/learning/?category=prime)[台股進階模組](https://www.xq.com.tw/learning/?category=tw_source)[量化積木模組](https://www.xq.com.tw/learning/?category=blocks_feature)[盤中量化交易模組](https://www.xq.com.tw/learning/?category=trading_feature)[盤後量化選股模組](https://www.xq.com.tw/learning/?category=selection_feature)[產業數據模組](https://www.xq.com.tw/learning/?category=industry_feature)[權證操作模組](https://www.xq.com.tw/learning/?category=warrant_feature)[美股分析模組](https://www.xq.com.tw/learning/?category=us_source)
