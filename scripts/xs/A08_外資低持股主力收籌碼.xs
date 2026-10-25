{@type:filter}
// A08_外資低持股主力收籌碼 外資持股低，主力N日買張佔量高
// 來源：sysjust-xq/XScript_Preset @d15a4157cc（該庫供用戶作範例與修改）「選股/06.籌碼選股/外資完全不碰的股票有人在收籌碼.xs」
// 證據分級：TX（官方預設腳本，無勝率宣稱，未驗證）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：主力『買張』是前十五大買超分點的買超張數（不扣賣超），比重>50%常見於量小股
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: period(5,"計算期間"), investorLimit(2000,"外資持股上限(張)"), ratio(50,"主力買張比重%"), volLimit(500,"成交均量下限(張)"), minLots(1000,"前20日均量下限(張)"), minAmt(0.5,"前20日均成交值下限(億元)"), minPx(10,"股價下限(元)");
SetTotalBar(period + 25);
variable: vm20(0), am20(0), vr(0), mc1(0), mc5(0), chg(0), liq(0);
variable: mb(0), vN(0), mr(0);

vm20 = (summation(volume, 21) - volume) / 20;
am20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if vm20 > 0 then vr = volume / vm20;
if volume > 0 then mc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then mc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
chg = RateOfChange(close, 1);
liq = 0;
if vm20 >= minLots and am20 >= minAmt and close >= minPx then liq = 1;
mb = summation(GetField("主力買張","D"), period);
vN = summation(volume, period);
if vN > 0 then mr = mb / vN * 100;

if GetField("外資持股","D") < investorLimit and mr > ratio and vN > volLimit * period and liq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, chg, 2, "漲跌幅%");
outputfield(3, vr, 2, "量比");
outputfield(4, vm20, 0, "前20日均量");
outputfield(5, am20, 2, "前20日均成交值(億)");
outputfield(6, mc1, 1, "主力集中度1日%");
outputfield(7, mc5, 1, "主力集中度5日%");
outputfield(8, liq, 0, "流動性閘門(1=通過)");
outputfield(9, mr, 1, "主力買張比重%");
outputfield(10, GetField("外資持股","D"), 0, "外資持股(張)");
