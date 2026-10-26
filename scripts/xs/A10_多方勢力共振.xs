{@type:filter}
// A10_多方勢力共振 外資、投信、自營、主力至少3者5日同向買超
// 來源：XQ影音〈各方勢力共襄盛舉的股票〉
// 證據分級：TX（XQ影片觀點，未驗證）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：XQ影音作者自述每天用此類選股找『公司派、主力、投信、外資、代操、自營商都看好』；3者與3%為操作化
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: pMinForces(3,"同向勢力數下限"), pTriPct(3,"三大法人5日買超佔量下限%"), pMinLots(1000,"前20日均量下限(張)"), pMinAmt(0.5,"前20日均成交值下限(億元)"), pMinPx(10,"股價下限(元)");
SetTotalBar(30);
variable: gVm20(0), gAm20(0), gVr(0), gMc1(0), gMc5(0), gChg(0), gLiq(0);
variable: gF5(0), gT5(0), gD5(0), gM5(0), gN(0), gTp(0), gV5(0);

gVm20 = (summation(volume, 21) - volume) / 20;
gAm20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if gVm20 > 0 then gVr = volume / gVm20;
if volume > 0 then gMc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then gMc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
gChg = RateOfChange(close, 1);
gLiq = 0;
if gVm20 >= pMinLots and gAm20 >= pMinAmt and close >= pMinPx then gLiq = 1;
gF5 = summation(GetField("外資買賣超","D"), 5);
gT5 = summation(GetField("投信買賣超","D"), 5);
gD5 = summation(GetField("自營商買賣超","D"), 5);
gM5 = summation(GetField("主力買賣超張數","D"), 5);
gV5 = summation(volume, 5);
gN = 0;
if gF5 > 0 then gN = gN + 1;
if gT5 > 0 then gN = gN + 1;
if gD5 > 0 then gN = gN + 1;
if gM5 > 0 then gN = gN + 1;
if gV5 > 0 then gTp = (gF5 + gT5 + gD5) / gV5 * 100;

if gN >= pMinForces and gTp >= pTriPct and gLiq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, gChg, 2, "漲跌幅%");
outputfield(3, gVr, 2, "量比");
outputfield(4, gVm20, 0, "前20日均量");
outputfield(5, gAm20, 2, "前20日均成交值(億)");
outputfield(6, gMc1, 1, "主力集中度1日%");
outputfield(7, gMc5, 1, "主力集中度5日%");
outputfield(8, gLiq, 0, "流動性閘門(1=通過)");
outputfield(9, gN, 0, "同向勢力數");
outputfield(10, gTp, 1, "三大法人5日佔量%");
