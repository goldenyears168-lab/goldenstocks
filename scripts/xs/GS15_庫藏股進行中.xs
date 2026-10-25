{@type:filter}
// GS15_庫藏股進行中 庫藏股買回期間內，附預計與實際買回張數
// 來源：XQ 欄位「庫藏股開始日期／結束日期／預計買回張數／實際買回張數」；小哥庫藏股（書 S6）
// 證據分級：E（庫藏股事件清單）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；本清單為事件或風險清單，預設不套流動性閘門（輸入皆為0），閘門旗標仍輸出在第8欄。

input: minLots(0,"前20日均量下限(張，0=不過濾)"), minAmt(0,"前20日均成交值下限(億元，0=不過濾)"), minPx(0,"股價下限(元，0=不過濾)");
SetTotalBar(30);
variable: vm20(0), am20(0), vr(0), mc1(0), mc5(0), chg(0), liq(0);
variable: s1(0), s2(0), dt(0), plan(0), done(0);

vm20 = (summation(volume, 21) - volume) / 20;
am20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if vm20 > 0 then vr = volume / vm20;
if volume > 0 then mc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then mc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
chg = RateOfChange(close, 1);
liq = 0;
if vm20 >= minLots and am20 >= minAmt and close >= minPx then liq = 1;
s1 = GetField("庫藏股開始日期");
s2 = GetField("庫藏股結束日期");
dt = GetField("Date");
plan = GetField("庫藏股預計買回張數");
done = GetField("庫藏股實際買回張數");

if s1 > 0 and dt >= s1 and dt <= s2 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, chg, 2, "漲跌幅%");
outputfield(3, vr, 2, "量比");
outputfield(4, vm20, 0, "前20日均量");
outputfield(5, am20, 2, "前20日均成交值(億)");
outputfield(6, mc1, 1, "主力集中度1日%");
outputfield(7, mc5, 1, "主力集中度5日%");
outputfield(8, liq, 0, "流動性閘門(1=通過)");
outputfield(9, plan, 0, "預計買回(張)");
outputfield(10, done, 0, "實際買回(張)");
outputfield(11, s2, 0, "買回期間結束(西元年月日)");
