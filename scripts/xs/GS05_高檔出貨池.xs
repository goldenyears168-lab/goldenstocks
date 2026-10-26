{@type:filter}
// GS05_高檔出貨池 月線下彎+主力賣+大帶寬，或月線下彎+乖離年線>40%
// 來源：權證小哥 S 規則 x chip_sop.py（docs/warrant-xiaoge/chip-sop-spec.md） R11／R12／R13、S3
// 證據分級：T0（R11/R12/R13 無專屬檢定；M04 高檔出貨僅描述）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：新上市不足240根者乖離年線不可靠
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: pLen(20,"布林天數"), pWide(20,"帶寬下限%"), pRatio(10,"主力賣超集中度下限%"), pBias(40,"乖離年線下限%"), pMinLots(1000,"前20日均量下限(張)"), pMinAmt(0.5,"前20日均成交值下限(億元)"), pMinPx(10,"股價下限(元)");
SetTotalBar(260);
variable: gVm20(0), gAm20(0), gVr(0), gMc1(0), gMc5(0), gChg(0), gLiq(0);
variable: gUp(0), gDn(0), gMa(0), gBw(0), gMa240(0), gBy(0), gPool(0), gEsc(0);

gVm20 = (summation(volume, 21) - volume) / 20;
gAm20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if gVm20 > 0 then gVr = volume / gVm20;
if volume > 0 then gMc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then gMc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
gChg = RateOfChange(close, 1);
gLiq = 0;
if gVm20 >= pMinLots and gAm20 >= pMinAmt and close >= pMinPx then gLiq = 1;
gUp = bollingerband(close, pLen, 2);
gDn = bollingerband(close, pLen, -2);
gMa = average(close, pLen);
if gDn > 0 then gBw = (gUp / gDn - 1) * 100;
gMa240 = average(close, 240);
if gMa240 > 0 then gBy = (close / gMa240 - 1) * 100;
gPool = 0;
if gMa < gMa[1] and gMc1 <= -pRatio and gBw >= pWide then gPool = 1;
if gPool = 0 and gMa < gMa[1] and gBy > pBias then gPool = 2;
if close >= gMa then gEsc = 1 else gEsc = 0;

if gPool > 0 and gLiq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, gChg, 2, "漲跌幅%");
outputfield(3, gVr, 2, "量比");
outputfield(4, gVm20, 0, "前20日均量");
outputfield(5, gAm20, 2, "前20日均成交值(億)");
outputfield(6, gMc1, 1, "主力集中度1日%");
outputfield(7, gMc5, 1, "主力集中度5日%");
outputfield(8, gLiq, 0, "流動性閘門(1=通過)");
outputfield(9, gPool, 0, "池型(1=主力賣+大帶寬 2=乖離年線)");
outputfield(10, gBy, 1, "乖離年線%");
outputfield(11, gBw, 1, "帶寬%");
outputfield(12, gEsc, 0, "收盤>=月線(逃命點)");
