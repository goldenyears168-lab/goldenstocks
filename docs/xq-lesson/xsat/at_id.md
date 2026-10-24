---
source: https://www.xq.com.tw/lesson/xsat/at_id/
title: 自動交易語法：取得「交易帳號」使用說明 - XQ 全球贏家
fetched: 2026-10-23
---

# 自動交易語法：取得「交易帳號」使用說明

###### 2025-07-18 11:25:01

複製連結

在這次改版當中，交易函數裡新增了可以取得目前策略執行時所使用的券商和帳號的功能。 如此一來便可以在交易腳本中限制使用的券商帳號，或是在模擬交易和實際交易下單時使用不同的交易邏輯。 舉例來說，使用者可能希望在模擬交易運作時用市價下單，這樣就可以觀察策略觸發進場的時間點是否為自己所希望的，但在實際交易時為了則會希望可以下限價單來避免滑價過大造成成本過高的問題。 以下會針對如何使用帳號相關的函數作說明，並簡單舉例。 

### **函數語法**

我們是在「GetInfo」這個函數語法函數中，新增「AT_EnableTrade」、「AT_BID」、「AT_AccType」、「AT_AccType」這四個參數，讓大家可以藉由這四個參數來取得交易帳號的資訊複製var: enableTrade(0), str_tsid(“”), acctype(0), str_aid(“”); enableTrade = GetInfo(“AT_EnableTrade”); str_tsid = GetInfo(“AT_BID”); acctype = GetInfo(“AT_AccType”); str_aid = GetInfo(“AT_AID”); 

### **函數說明**

在繼續往下閱讀前，有一件事情大家必須要了解，也就是這四個參數僅支援交易腳本。 GetInfo(“AT_EnableTrade”) 將會回傳目前策略是否有啟動帳號，分成以下幾種狀況：

- 如果是回測的話，回傳數值0。
- 如果是即時的策略運作，但並沒有使用任何交易帳號的話，回傳數值0。
- 如果是即時的策略運作，且有設定交易帳號，無論是模擬還是券商帳號都是回傳數值1。
 GetInfo(“AT_BID”) 將會回傳券商的字串代碼。 需注意若目前策略沒有啟動帳號，或是在回測時的狀況下 (也就是GetInfo(“AT_EnableTrade”)回傳0的狀況下)，此函數會回傳空白字串。 GetInfo(“AT_AccType”) 將會回傳目前策略運作的業務類別。

- 如果是證券的話，回傳數值1。
- 如果是期貨的話，回傳數值2。
- 如果是複委託的話，回傳數值3。
- 若目前策略沒有啟動帳號，或是在回測時的狀況下 (也就是GetInfo(“AT_EnableTrade”)回傳0的狀況下)，此函數會回傳 0。
 GetInfo(“AT_AID”) 將會回傳目前策略運作的帳號。 這會是一個由券商代碼加上帳號組成的字串。 需注意若目前策略沒有啟動帳號，或是在回測的狀況下 (也就是GetInfo(“AT_EnableTrade”)回傳0的狀況下)，此函數會回傳空白字串。 此字串有區分英文字母的大小寫，但XS字串比對預設是不區分大小寫的，因此在比對時可以使用 [StrCompare](http://xshelp.xq.com.tw/XSHelp/?HelpName=StrCompare&group=STRINGFUNC) 函數並將第三個參數設為False讓其區分大小寫。 

### **相關數值輸出示意圖**

證券模擬帳號： [![](../images/43c09fcb8b.png)](https://www.xq.com.tw/wp-content/uploads/2023/04/02.png)期權模擬帳號： [![](../images/6f58696597.png)](https://www.xq.com.tw/wp-content/uploads/2023/04/03.png)回測模擬帳號： [![](../images/9278e2ad6c.png)](https://www.xq.com.tw/wp-content/uploads/2023/04/04.png)

### 

### 

 

### **腳本範例**

**範例1：** 下面是用布林通道當作進出場條件的簡單策略，當收盤價向上穿越布林通道下軌時買入，並在收盤價向上穿越20期移動平均時賣出。 當腳本運作在即時且設定券商為華南永昌證券的狀況下，下單進場時用觸發價加上2個Tick買進，出場時則用觸發價減2個Tick賣出，其他的狀況下則用市價賣出。複製var: enableTrade(0), str_tsid(""); enableTrade = GetInfo("AT_EnableTrade"); str_tsid = GetInfo("AT_BID"); value1 = average(close, 20); value2 = bollingerband(Close, 20, -2); condition1 = close cross over value2 and position = 0; condition2 = close cross over value1 and position > 0; condition3 = enableTrade = 1 and str_tsid = "HNS"; if condition1 then begin if condition3 then setposition(1, addSpread(close, 2)) else setposition(1, market); end; if condition2 then begin if condition3 then setposition(0, addSpread(close, -2)) else setposition(0, market); end;  **範例2：** 若要確保該腳本只能執行在特定帳號上的話，可以加上帳號限制，讓其無法在使用其他帳號的情況下運作。 我們可以先把帳號判斷先撰寫成函數，這樣只要在交易腳本使用該函數，傳入所使用的 AT_AID 就可以判斷是否為設定的帳號。 首先我們先撰寫「CheckAID」函式腳本複製函數腳本 CheckAID: input: _AID(string); Condition1 = StrCompare(_AID, "SYSTRADE_SSJ00-TestAccount-01_1", False) = 0; if condition1 then CheckAID = True else RaiseRunTimeError("帳號不符合"); 再來，我們拿範例1的腳本當例子，來使用上面剛剛所新增的「CheckAID」函數複製var: enableTrade(0), str_tsid(""), str_aid(""); enableTrade = GetInfo("AT_EnableTrade"); str_tsid = GetInfo("AT_BID"); str_aid = GetInfo("AT_AID"); CheckAID(“str_aid”); value1 = average(close, 20); value2 = bollingerband(Close, 20, -2); condition1 = close cross over value2 and position = 0; condition2 = close cross over value1 and position >0; condition3 = enableTrade = 1 and str_tsid = "SYSTRADE"; if condition1 then begin if condition3 then setposition(1, addSpread(close, 2)) else setposition(1, market); end; if condition2 then begin if condition3 then setposition(0, addSpread(close, -2)) else setposition(0, market); end; 如此一來，若策略使用的帳號與函數中設定的帳號不同的話，就會執行失敗並顯示帳號不符合的錯誤訊息。 以上就是這次針對取得交易帳號的腳本說明！ 

### **TSID 列表**

 

 **TSID** | **說明** |

 SYSTRADE | 內建的模擬交易帳號 |

 SYSCAMPUS | 校園投資模擬競賽，可以從 AT_AID 內判斷帳號的 GameID。

假設 GameID 是 JUST123，使用者的身份證是 A01，那麼證券的 AT_AID 會是 SJUST123-A01。 |

 TISC | 群益金鼎 |

 PSCAP | 統一 |

 HNS | 華南永昌 |

 WLS | 國票 |

 SINOAP | 永豐金 |

 TSS | 台新 |

 HONSEC | 宏遠 |

 TCB | 合庫 |

 MEGA | 兆豐 |

 LINK | 元富 |

 FUBONFAP | 富邦 |

 YUANTA | 元大 |

 KGIAP | 凱基 |

 CTS | 中國信託 |

[##### 前一篇

庫存異動時自動同步數值](https://www.xq.com.tw/lesson/xsat/庫存異動時自動同步數值/)[##### 下一篇

CancelAllOrders語法說明](https://www.xq.com.tw/lesson/xsat/cancelallorders/)
