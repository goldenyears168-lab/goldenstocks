---
source: https://www.xq.com.tw/tutorial/基礎教學/如何在新版mac電腦m1-cpu上面執行xq/
title: 如何在搭配M系列晶片的Mac電腦(M1~M5 CPU)上面執行XQ - XQ 全球贏家
fetched: 2026-10-23
---

# 如何在搭配M系列晶片的Mac電腦(M1~M5 CPU)上面執行XQ

###### 2021-04-07 09:16:08

複製連結

最近很多用戶詢問如果買的Mac電腦搭配的是最新的M系列晶片的話，可不可以跑XQ。由於M系列晶片是Arm的架構，而XQ則是跑在Windows 10(x64)底下的程式，所以要可以在M系列晶片上面執行XQ的話，需要其他程式的支援。就目前我們得到的資訊，大家可以嘗試照底下的方式來設定安裝。 註：2025年已發布M5晶片

## # 安裝方式

請參考這一篇YouTube影片的說明 ([M1 Macs: How to run Windows 10 for ARM using Parallels 16 Technical Preview - YouTube](https://www.youtube.com/watch?v=G7Qfk8hhA-c)) 底下一一說明每個安裝步驟.

### Step 1: 安裝Parallel Desktop for Mac版本

請到這裡【[Parallels Desktop for Mac](https://www.parallels.com/hk/products/desktop/?utm_source=techpreview&utm_medium=email&utm_campaign=m1)】 依照說明下載安裝正式版的Parallels Desktop for Mac版本。

### Step 2: 下載Windows 10 for Arm的版本

因為Mac的M系列CPU是Arm的架構，所以Parallel Desktop的虛擬機也必須執行Windows for Arm的版本. 請到 [https://www.microsoft.com/en-us/software-download/windowsinsiderpreviewARM64](https://www.microsoft.com/en-us/software-download/windowsinsiderpreviewARM64) 這裡依照說明進行下載。你必須註冊加入Windows Insider Program之後才能下載。

### Step 3: 安裝VM for Windows 10

接下來請在Parallel Desktop內建立一個虛擬機器，選擇Step 2下載的檔案，依照步驟建立。 建議虛擬機需有至少2虛擬CPU， 以及4G的RAM。

### Step 4: 設定Windows 10

因為Windows 10 for Arm的版本是英文版，為了可以讓XQ正確的顯示中文，請依照這篇文章的說明調整Windows的語言相關設定。 《[如何在英文版Windows底下執行執行XQ](https://www.xq.com.tw/tutorial/%E5%9F%BA%E7%A4%8E%E6%95%99%E5%AD%B8/%E8%8B%B1%E6%96%87%E7%89%88os%E5%AE%89%E8%A3%9Dap%E4%B8%8D%E9%A1%AF%E7%A4%BA%E4%BA%82%E7%A2%BC%E7%9A%84%E8%99%95%E7%90%86%E6%96%B9%E5%BC%8F/)》

### Step 5: 下載XQ

接下來請到XQ官網下載XQ安裝檔進行安裝。 安裝完成之後，請務必在XQ icon按右鍵, 選擇以「管理員身份執行」即可 (這是Windows 10 for Arm已知的bug). ----[我是分隔線]--------------------------------------------------------------------------- 大家在這樣子的環境底下使用XQ時，如果有遇到任何問題的話，也歡迎隨時跟我們提出，我們會確認這些是平台的問題還是XQ本身的問題。
