{@type:filter}
// GS09_地板股 T日黑K爆量＋月線下彎＋乖離月線≤該股近500日5%分位
// 來源：權證小哥 S 規則 × chip_sop.py（docs/warrant-xiaoge/chip-sop-spec.md） S5（地板股SOP）；對齊 /inventory M05
// 證據分級：T2（validation/floor_stock_v2.md：勝率宣稱95-99%不成立，期望值小正未顯著）；分級說明：T0=無專屬檢定(描述) T2=有檢定但結果混合 T3=有檢定且不支持或無預測力 TX=XQ作者自述未驗證 E=事件或風險清單(非方向)
// 備註：「地板價」小哥用軟體逐檔統計、公式未公開；/inventory M05 與本腳本都用「乖離月線<=該股近500日5%分位」近似（NthLowest 第25小≈5%分位）
// 備註：小哥：昨日黑K（十字也行）爆量、月線下彎的弱勢中；本腳本是『T日名單→隔天殺低爆量才買』的前一晚名單
// 備註：我們檢定：日線代理版不支持『95–99%反彈』，期望值小正未顯著，僅供建清單
// 性質：描述盤後狀態的條件是否成立，不是買賣訊號；門檻標「操作化」者為我們自訂；流動性閘門預設：前20日均量>=1000張、均成交值>=0.5億、股價>=10元（可在選股中心調參）。

input: burst(2,"爆量倍數"), lenB(500,"乖離統計天期"), nth(25,"乖離歷史低檔名次(500日中第25小≈5%分位)"), minLots(1000,"前20日均量下限(張)"), minAmt(0.5,"前20日均成交值下限(億元)"), minPx(10,"股價下限(元)");
SetTotalBar(lenB + 30);
variable: vm20(0), am20(0), vr(0), mc1(0), mc5(0), chg(0), liq(0);
variable: ma(0), bias(0), th(0);

vm20 = (summation(volume, 21) - volume) / 20;
am20 = (summation(GetField("成交金額(億)","D"), 21) - GetField("成交金額(億)","D")) / 20;
if vm20 > 0 then vr = volume / vm20;
if volume > 0 then mc1 = GetField("主力買賣超張數","D") / volume * 100;
if summation(volume, 5) > 0 then mc5 = summation(GetField("主力買賣超張數","D"), 5) / summation(volume, 5) * 100;
chg = RateOfChange(close, 1);
liq = 0;
if vm20 >= minLots and am20 >= minAmt and close >= minPx then liq = 1;
ma = average(close, 20);
if ma > 0 then bias = (close / ma - 1) * 100;
th = NthLowest(nth, bias, lenB);

if ma < ma[1] and close <= open and vr >= burst and bias <= th and liq = 1 then ret = 1;

// ---- 標準輸出欄 1-8（所有 GS／A 腳本共用，順序勿改，匯入器依欄名讀取）----
outputfield(1, close, 2, "收盤");
outputfield(2, chg, 2, "漲跌幅%");
outputfield(3, vr, 2, "量比");
outputfield(4, vm20, 0, "前20日均量");
outputfield(5, am20, 2, "前20日均成交值(億)");
outputfield(6, mc1, 1, "主力集中度1日%");
outputfield(7, mc5, 1, "主力集中度5日%");
outputfield(8, liq, 0, "流動性閘門(1=通過)");
outputfield(9, bias, 1, "乖離月線%");
outputfield(10, th, 1, "該股乖離5%分位門檻%");
outputfield(11, open, 2, "開盤");
