{@type:filter}
// GS01_開布林主力大買 昨日帶寬窄、今日往上開、主力集中度達標
// 來源：權證小哥 S 規則 x chip_sop.py（docs/warrant-xiaoge/chip-sop-spec.md） R03／S1
// 證據分級：T3（validation/bollinger_open.md 不支持：扣0.44%成本後1/5/10日 -0.64/-0.49/-0.35%；『開布林x主力大買』20日+0.60pp t2.1 僅邊緣）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：帶寬=上軌/下軌-1（母體標準差±2σ，與 boll_position.py 一致）；開布林=昨日帶寬<=10%、今日帶寬擴大、上軌上彎、收盤在月線上（chip-sop-spec 操作化）
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: pLen(20,"布林天數"), pNarrow(10,"昨日帶寬上限%"), pRatio(10,"主力集中度下限%"), pMinLots(1000,"前20日均量下限(張)"), pMinAmt(0.5,"前20日均成交值下限(億元)"), pMinPx(10,"股價下限(元)");
SetTotalBar(pLen + 25);
variable: gVm20(0), gAm20(0), gVr(0), gMc1(0), gMc5(0), gChg(0), gLiq(0);
variable: gUp(0), gDn(0), gMa(0), gBw(0);

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

if gBw[1] <= pNarrow and gBw > gBw[1] and gUp > gUp[1] and close > gMa and gMc1 >= pRatio and gLiq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, gChg, 2, "漲跌幅%");
outputfield(3, gVr, 2, "量比");
outputfield(4, gVm20, 0, "前20日均量");
outputfield(5, gAm20, 2, "前20日均成交值(億)");
outputfield(6, gMc1, 1, "主力集中度1日%");
outputfield(7, gMc5, 1, "主力集中度5日%");
outputfield(8, gLiq, 0, "流動性閘門(1=通過)");
outputfield(9, gBw, 1, "帶寬%");
outputfield(10, gBw[1], 1, "昨日帶寬%");
outputfield(11, gMa, 2, "月線");
