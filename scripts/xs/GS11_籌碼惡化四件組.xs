{@type:filter}
// GS11_籌碼惡化四件組 主力賣超＋散戶買超＋大戶比例減＋集保戶數增
// 來源：權證小哥 S 規則 × chip_sop.py（docs/warrant-xiaoge/chip-sop-spec.md） 書 2.3.4／S25
// 證據分級：T0（無檢定（集保戶數資料曾缺））；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：5日主力集中度門檻5%為操作化（書只說『主力賣超』）
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: ratio(5,"主力5日賣超集中度下限%"), minLots(1000,"前20日均量下限(張)"), minAmt(0.5,"前20日均成交值下限(億元)"), minPx(10,"股價下限(元)");
SetTotalBar(30);
variable: vm20(0), am20(0), vr(0), mc1(0), mc5(0), chg(0), liq(0);
variable: r5(0), b0(0), b1(0), n0(0), n1(0);

vm20 = (summation(volume, 21) - volume) / 20;
am20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if vm20 > 0 then vr = volume / vm20;
if volume > 0 then mc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then mc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
chg = RateOfChange(close, 1);
liq = 0;
if vm20 >= minLots and am20 >= minAmt and close >= minPx then liq = 1;
r5 = summation(GetField("散戶買賣超張數","D"), 5);
b0 = GetField("大戶持股比例","W",param:=400);
b1 = GetField("大戶持股比例","W",param:=400)[1];
n0 = GetField("總持股人數","W");
n1 = GetField("總持股人數","W")[1];

if mc5 <= -ratio and r5 > 0 and b0 < b1 and n0 > n1 and liq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, chg, 2, "漲跌幅%");
outputfield(3, vr, 2, "量比");
outputfield(4, vm20, 0, "前20日均量");
outputfield(5, am20, 2, "前20日均成交值(億)");
outputfield(6, mc1, 1, "主力集中度1日%");
outputfield(7, mc5, 1, "主力集中度5日%");
outputfield(8, liq, 0, "流動性閘門(1=通過)");
outputfield(9, r5, 0, "散戶5日買賣超(張)");
outputfield(10, n0 - n1, 0, "集保戶數週變化");
outputfield(11, b0 - b1, 2, "大戶400張以上比例週變化pp");
