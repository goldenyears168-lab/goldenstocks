{@type:filter}
// A02_不明買盤介入 不明買盤（扣除法人、資券互抵、散戶買張）佔量的N日均線上穿門檻
// 來源：sysjust-xq/XScript_Preset @d15a4157cc（該庫供用戶作範例與修改）「選股/06.籌碼選股/不明買盤介入.xs」
// 證據分級：TX（XQ作者自述近兩年年均報酬近4成、月均約2次訊號，未驗證）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：XQ專家策略（影音〈過去兩年每年平均報酬率接近四成的交易策略〉）：作者自述近兩年年均報酬近4成、月均約2次訊號，未驗證
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: pPeriod(5,"均線期間"), pRatio(30,"不明買盤比重%"), pMinLots(1000,"前20日均量下限(張)"), pMinAmt(0.5,"前20日均成交值下限(億元)"), pMinPx(10,"股價下限(元)");
SetTotalBar(pPeriod + 25);
variable: gVm20(0), gAm20(0), gVr(0), gMc1(0), gMc5(0), gChg(0), gLiq(0);
variable: gU(0), gUa(0);

gVm20 = (summation(volume, 21) - volume) / 20;
gAm20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if gVm20 > 0 then gVr = volume / gVm20;
if volume > 0 then gMc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then gMc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
gChg = RateOfChange(close, 1);
gLiq = 0;
if gVm20 >= pMinLots and gAm20 >= pMinAmt and close >= pMinPx then gLiq = 1;
if volume > 0 then gU = (volume - GetField("法人買張","D") - GetField("資券互抵張數","D") - GetField("散戶買張","D")) * 100 / volume;
gUa = average(gU, pPeriod);

if gUa crosses over pRatio and gLiq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, gChg, 2, "漲跌幅%");
outputfield(3, gVr, 2, "量比");
outputfield(4, gVm20, 0, "前20日均量");
outputfield(5, gAm20, 2, "前20日均成交值(億)");
outputfield(6, gMc1, 1, "主力集中度1日%");
outputfield(7, gMc5, 1, "主力集中度5日%");
outputfield(8, gLiq, 0, "流動性閘門(1=通過)");
outputfield(9, gU, 1, "不明買盤比重%");
outputfield(10, gUa, 1, "不明買盤比重均線%");
