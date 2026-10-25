{@type:filter}
// GS13_關鍵關聯券商買超 XQ 關鍵券商或關聯券商當日買超佔量
// 來源：XQ 欄位「關鍵券商買賣超張數」「關聯券商買賣超張數」
// 證據分級：T0（新欄位，無檢定；XQ關鍵券商非小哥點名清單）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: ratio(3,"買超佔量下限%"), minLots(1000,"前20日均量下限(張)"), minAmt(0.5,"前20日均成交值下限(億元)"), minPx(10,"股價下限(元)");
SetTotalBar(30);
variable: vm20(0), am20(0), vr(0), mc1(0), mc5(0), chg(0), liq(0);
variable: k1(0), k2(0), r1(0), r2(0);

vm20 = (summation(volume, 21) - volume) / 20;
am20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if vm20 > 0 then vr = volume / vm20;
if volume > 0 then mc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then mc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
chg = RateOfChange(close, 1);
liq = 0;
if vm20 >= minLots and am20 >= minAmt and close >= minPx then liq = 1;
k1 = GetField("關鍵券商買賣超張數","D", default := 0);
k2 = GetField("關聯券商買賣超張數","D", default := 0);
if volume > 0 then begin
  r1 = k1 / volume * 100;
  r2 = k2 / volume * 100;
end;

if (r1 >= ratio or r2 >= ratio) and liq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, chg, 2, "漲跌幅%");
outputfield(3, vr, 2, "量比");
outputfield(4, vm20, 0, "前20日均量");
outputfield(5, am20, 2, "前20日均成交值(億)");
outputfield(6, mc1, 1, "主力集中度1日%");
outputfield(7, mc5, 1, "主力集中度5日%");
outputfield(8, liq, 0, "流動性閘門(1=通過)");
outputfield(9, k1, 0, "關鍵券商買賣超(張)");
outputfield(10, k2, 0, "關聯券商買賣超(張)");
outputfield(11, GetField("地緣券商買賣超張數","D", default := 0), 0, "地緣券商買賣超(張)");
