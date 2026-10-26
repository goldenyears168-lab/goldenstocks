{@type:filter}
// GS15_庫藏股進行中 庫藏股買回期間內，附預計與實際買回張數
// 來源：XQ 欄位「庫藏股開始日期／結束日期／預計買回張數／實際買回張數」；小哥庫藏股（書 S6）
// 證據分級：E（庫藏股事件清單）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；本清單為事件或風險清單，預設不套流動性閘門（輸入皆為0），閘門旗標仍輸出在第8欄。

input: pMinLots(0,"前20日均量下限(張，0=不過濾)"), pMinAmt(0,"前20日均成交值下限(億元，0=不過濾)"), pMinPx(0,"股價下限(元，0=不過濾)");
SetTotalBar(30);
variable: gVm20(0), gAm20(0), gVr(0), gMc1(0), gMc5(0), gChg(0), gLiq(0);
variable: gS1(0), gS2(0), gDt(0), gPlan(0), gDone(0);

gVm20 = (summation(volume, 21) - volume) / 20;
gAm20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if gVm20 > 0 then gVr = volume / gVm20;
if volume > 0 then gMc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then gMc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
gChg = RateOfChange(close, 1);
gLiq = 0;
if gVm20 >= pMinLots and gAm20 >= pMinAmt and close >= pMinPx then gLiq = 1;
gS1 = GetField("庫藏股開始日期");
gS2 = GetField("庫藏股結束日期");
gDt = GetField("Date");
gPlan = GetField("庫藏股預計買回張數");
gDone = GetField("庫藏股實際買回張數");

if gS1 > 0 and gDt >= gS1 and gDt <= gS2 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, gChg, 2, "漲跌幅%");
outputfield(3, gVr, 2, "量比");
outputfield(4, gVm20, 0, "前20日均量");
outputfield(5, gAm20, 2, "前20日均成交值(億)");
outputfield(6, gMc1, 1, "主力集中度1日%");
outputfield(7, gMc5, 1, "主力集中度5日%");
outputfield(8, gLiq, 0, "流動性閘門(1=通過)");
outputfield(9, gPlan, 0, "預計買回(張)");
outputfield(10, gDone, 0, "實際買回(張)");
outputfield(11, gS2, 0, "買回期間結束(西元年月日)");
