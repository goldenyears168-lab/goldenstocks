---
source: https://www.xq.com.tw/lesson/xspractice/xscript-編輯器已支援-xs-副檔名的檔案/
title: XScript 編輯器已支援 *.xs 副檔名的檔案 - XQ 全球贏家
fetched: 2026-10-23
---

# XScript 編輯器已支援 *.xs 副檔名的檔案

###### 2025-06-12 16:52:48

複製連結

在 XQ 7.14.01 / 3.14.01 版本的 XScript 編輯器，有提供開啟與儲存 *.xs 副檔名的檔案功能，可以透過這個功能開啟 [XQ 全球贏家官方 GitHub](https://github.com/sysjust-xq) 提供的「[量化積木條件腳本](https://github.com/sysjust-xq/XS_Blocks)、[XScript編輯器系統腳本](https://github.com/sysjust-xq/XScript_Preset)、[XQ選股中心的選股條件腳本](https://github.com/sysjust-xq/XQStrategy)」*.xs 副檔名的檔案（按下 <> Code ▽ 找到 DownLoad ZIP 選項，點擊下載後解壓縮）就能在 XScript 編輯器中參考這些腳本集，來構思自己的交易策略靈感。 比方說，如果在量化積木，看到「可能被處置的股票」的條件很感興趣，想知道這個條件的 XS 程式碼，就可以透過「開啟 XS 檔案」功能，打開此條件的 *.xs 檔案，在 XS 編輯器顯示此條件的 XS 程式碼，如下示意圖。 [![](../images/8f66b92ee7.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/00_開啟XS檔案介紹.png) 那麼在哪裡才能找到量化積木條件腳本呢？大家可以從 [XQ 全球贏家官方 GitHub](https://github.com/sysjust-xq) 提供的 [量化積木條件腳本](https://github.com/sysjust-xq/XS_Blocks) 下載壓縮檔（按下 <> Code ▽ 找到 DownLoad ZIP 選項，點擊下載後解壓縮）解壓縮後就能取得量化積木條件腳本的 *.xs 檔案。 [![](../images/7b0fe63b70.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/00_下載解壓縮.png) 除了可以開啟 *.xs 檔案檢視外，也可以將自己的 XS 腳本，儲存成 *.xs 檔案 [![](../images/b1b014fa30.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/00_儲存成XS檔案.png) 儲存成 *.xs 檔案後，就可以透過其他方式開啟 *.xs 檔案來檢視 XS 程式碼，下圖是透過記事本開啟 *.xs 檔案來檢視 XS 程式碼， [![](../images/c818bc5bc0.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/00_記事本.png) 推薦安裝 [VSCode](https://code.visualstudio.com/) 檢視程式碼，也建議安裝 [XS 程式碼的外掛](https://marketplace.visualstudio.com/items?itemName=sysjust-xq.xs)，VSCode 就能依照 XS 語法自動調整顯示的顏色，下圖是透過 VSCode 檢視 XS 程式碼， [![](../images/8cce5e2695.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/00_檢視XS程式碼.png) 用 VSCode 檢視 *.xs 檔案時，第一行有以下這行註解： {@type:sensor|@guid:c75d3c176ff446be90cd891b828e7f6d} 這是用來開啟 XS 檔案的時候，讓系統判斷要用什麼腳本類型開啟，以及要開啟哪個腳本 ID

- type 是指腳本類型：

- indicator＝指標、filter＝選股、sensor＝警示、autotrade＝交易、function＝函數。
- guid 是指這個腳本的 ID
 最後，如果大家透過 XS 編輯器開啟 *.xs 檔案，與既存 XS 編輯器上的腳本重複（腳本的 ID 一樣代表重複）這時候會有一個選項詢問是否要覆蓋目前腳本，提供給大家決定是否要覆蓋。 [![](../images/e12083621a.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/00_覆蓋目前腳本.png) 以上就是 XScript 編輯器支援開啟與儲存 *.xs 檔案的功能介紹。 ※ 注意：「隱藏程式碼」的腳本是無法被儲存成 XS 檔案格式的。 [![](../images/11ea7a43ba.png)](https://www.xq.com.tw/wp-content/uploads/2024/09/隱藏程式碼.png)[##### 前一篇

XS函數「isfirstcall」說明](https://www.xq.com.tw/lesson/xspractice/xs函數「isfirstcall」說明/)[##### 下一篇

XS指標 / 策略雷達 / 自動交易：支援選股欄位](https://www.xq.com.tw/lesson/xspractice/xs指標-策略雷達-自動交易：支援選股欄位/)
