{@type:filter}
// A04_投信佈局 投信低持股且連買、當日收紅、近5日量足
// 來源：sysjust-xq/XScript_Preset @d15a4157cc（該庫供用戶作範例與修改）「投信從空手到開始佈局」「投信爭買的小型成長股」+XQ影音〈傳授看投信進出表時的技巧〉
// 證據分級：TX（XQ作者自述五年勝率68%，未驗證）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：XQ影音自述：連續5日量>500、投信買超連2日占量高、持股<1.5%、當日收紅4%，五年勝率68%（未驗證）；『占量高』5%為操作化
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: pItMax(1.5,"投信持股比例上限%"), pMinV(500,"近5日每日量下限(張)"), pItShare(5,"投信買超佔量下限%"), pUpPct(4,"當日漲幅下限%"), pMinLots(1000,"前20日均量下限(張)"), pMinAmt(0.5,"前20日均成交值下限(億元)"), pMinPx(10,"股價下限(元)");
SetTotalBar(30);
variable: gVm20(0), gAm20(0), gVr(0), gMc1(0), gMc5(0), gChg(0), gLiq(0);
variable: gIt(0), gItr(0);

gVm20 = (summation(volume, 21) - volume) / 20;
gAm20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if gVm20 > 0 then gVr = volume / gVm20;
if volume > 0 then gMc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then gMc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
gChg = RateOfChange(close, 1);
gLiq = 0;
if gVm20 >= pMinLots and gAm20 >= pMinAmt and close >= pMinPx then gLiq = 1;
gIt = GetField("投信買賣超","D", default := 0);
gItr = GetField("投信持股比例","D", default := 99);

if volume > 0 and volume[1] > 0 and lowest(volume, 5) > pMinV and gIt > 0 and gIt[1] > 0 and gIt / volume * 100 >= pItShare and gIt[1] / volume[1] * 100 >= pItShare and gItr < pItMax and close / close[1] - 1 >= pUpPct / 100 and gLiq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, gChg, 2, "漲跌幅%");
outputfield(3, gVr, 2, "量比");
outputfield(4, gVm20, 0, "前20日均量");
outputfield(5, gAm20, 2, "前20日均成交值(億)");
outputfield(6, gMc1, 1, "主力集中度1日%");
outputfield(7, gMc5, 1, "主力集中度5日%");
outputfield(8, gLiq, 0, "流動性閘門(1=通過)");
outputfield(9, gItr, 2, "投信持股比例%");
outputfield(10, gIt, 0, "投信買賣超(張)");
