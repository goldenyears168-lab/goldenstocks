{@type:filter}
// A05_主力買超且融券創新高 主力N日集中度達標，且融券餘額創近期新高
// 來源：sysjust-xq/XScript_Preset @d15a4157cc（該庫供用戶作範例與修改）「選股/06.籌碼選股/券增價漲.xs」 ＋XQ影音〈找出主力買超而融券增加的股票〉
// 證據分級：TX（XQ作者自述年報酬約15%，未驗證）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：XQ影音自述年報酬約15%（未驗證）；『距融券最後回補日不遠』無對應欄位，未實作
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: days(5,"主力計算天數"), ratio(5,"主力買超佔量下限%"), hiLen(20,"融券新高天期"), minLots(1000,"前20日均量下限(張)"), minAmt(0.5,"前20日均成交值下限(億元)"), minPx(10,"股價下限(元)");
SetTotalBar(hiLen + 25);
variable: vm20(0), am20(0), vr(0), mc1(0), mc5(0), chg(0), liq(0);
variable: rN(0), vN(0), cN(0), sb(0);

vm20 = (summation(volume, 21) - volume) / 20;
am20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if vm20 > 0 then vr = volume / vm20;
if volume > 0 then mc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then mc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
chg = RateOfChange(close, 1);
liq = 0;
if vm20 >= minLots and am20 >= minAmt and close >= minPx then liq = 1;
rN = summation(GetField("主力買賣超張數","D"), days);
vN = summation(volume, days);
if vN > 0 then cN = rN / vN * 100;
sb = GetField("融券餘額張數","D");

if cN >= ratio and sb > 0 and sb = highest(sb, hiLen) and liq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, chg, 2, "漲跌幅%");
outputfield(3, vr, 2, "量比");
outputfield(4, vm20, 0, "前20日均量");
outputfield(5, am20, 2, "前20日均成交值(億)");
outputfield(6, mc1, 1, "主力集中度1日%");
outputfield(7, mc5, 1, "主力集中度5日%");
outputfield(8, liq, 0, "流動性閘門(1=通過)");
outputfield(9, cN, 1, "主力N日集中度%");
outputfield(10, sb, 0, "融券餘額(張)");
