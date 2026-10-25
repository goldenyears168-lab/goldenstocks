{@type:filter}
// A10_多方勢力共振 外資、投信、自營、主力至少3者5日同向買超
// 來源：XQ影音〈各方勢力共襄盛舉的股票〉
// 證據分級：TX（XQ影片觀點，未驗證）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：XQ影音作者自述每天用此類選股找『公司派、主力、投信、外資、代操、自營商都看好』；3者與3%為操作化
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: minForces(3,"同向勢力數下限"), triPct(3,"三大法人5日買超佔量下限%"), minLots(1000,"前20日均量下限(張)"), minAmt(0.5,"前20日均成交值下限(億元)"), minPx(10,"股價下限(元)");
SetTotalBar(30);
variable: vm20(0), am20(0), vr(0), mc1(0), mc5(0), chg(0), liq(0);
variable: f5(0), t5(0), d5(0), m5(0), n(0), tp(0), v5(0);

vm20 = (summation(volume, 21) - volume) / 20;
am20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if vm20 > 0 then vr = volume / vm20;
if volume > 0 then mc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then mc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
chg = RateOfChange(close, 1);
liq = 0;
if vm20 >= minLots and am20 >= minAmt and close >= minPx then liq = 1;
f5 = summation(GetField("外資買賣超","D"), 5);
t5 = summation(GetField("投信買賣超","D"), 5);
d5 = summation(GetField("自營商買賣超","D"), 5);
m5 = summation(GetField("主力買賣超張數","D"), 5);
v5 = summation(volume, 5);
n = 0;
if f5 > 0 then n = n + 1;
if t5 > 0 then n = n + 1;
if d5 > 0 then n = n + 1;
if m5 > 0 then n = n + 1;
if v5 > 0 then tp = (f5 + t5 + d5) / v5 * 100;

if n >= minForces and tp >= triPct and liq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, chg, 2, "漲跌幅%");
outputfield(3, vr, 2, "量比");
outputfield(4, vm20, 0, "前20日均量");
outputfield(5, am20, 2, "前20日均成交值(億)");
outputfield(6, mc1, 1, "主力集中度1日%");
outputfield(7, mc5, 1, "主力集中度5日%");
outputfield(8, liq, 0, "流動性閘門(1=通過)");
outputfield(9, n, 0, "同向勢力數");
outputfield(10, tp, 1, "三大法人5日佔量%");
