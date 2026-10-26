{@type:filter}
// A03_大戶持股比例連增 集保1000張以上持股比例連續N週上升
// 來源：sysjust-xq/XScript_Preset @d15a4157cc（該庫供用戶作範例與修改）「選股/06.籌碼選股/千張大戶增持.xs」 +「千張大戶增加而散戶減少」
// 證據分級：T3（集保大戶占比判決為流動性溢酬代理，非籌碼alpha（控週轉後負報酬））；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：連續4週為操作化；XQ『連增股』門檻未公開。週頻欄位在日頻腳本用 [k] 取前k週（見 lesson『取得資料欄位時的對位問題』）
// 備註：資料凍結（多週完全不變）或異常（總人數極小）需另行核對
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: pMinLots(1000,"前20日均量下限(張)"), pMinAmt(0.5,"前20日均成交值下限(億元)"), pMinPx(10,"股價下限(元)");
SetTotalBar(30);
variable: gVm20(0), gAm20(0), gVr(0), gMc1(0), gMc5(0), gChg(0), gLiq(0);
variable: gB0(0), gB1(0), gB2(0), gB3(0), gB4(0);

gVm20 = (summation(volume, 21) - volume) / 20;
gAm20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if gVm20 > 0 then gVr = volume / gVm20;
if volume > 0 then gMc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then gMc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
gChg = RateOfChange(close, 1);
gLiq = 0;
if gVm20 >= pMinLots and gAm20 >= pMinAmt and close >= pMinPx then gLiq = 1;
gB0 = GetField("大戶持股比例","W",param:=1000);
gB1 = GetField("大戶持股比例","W",param:=1000)[1];
gB2 = GetField("大戶持股比例","W",param:=1000)[2];
gB3 = GetField("大戶持股比例","W",param:=1000)[3];
gB4 = GetField("大戶持股比例","W",param:=1000)[4];

if gB0 > gB1 and gB1 > gB2 and gB2 > gB3 and gB3 > gB4 and gLiq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, gChg, 2, "漲跌幅%");
outputfield(3, gVr, 2, "量比");
outputfield(4, gVm20, 0, "前20日均量");
outputfield(5, gAm20, 2, "前20日均成交值(億)");
outputfield(6, gMc1, 1, "主力集中度1日%");
outputfield(7, gMc5, 1, "主力集中度5日%");
outputfield(8, gLiq, 0, "流動性閘門(1=通過)");
outputfield(9, gB0, 2, "大戶1000張以上比例%");
outputfield(10, gB0 - gB1, 2, "本週變化pp");
outputfield(11, gB0 - gB4, 2, "4週累計變化pp");
