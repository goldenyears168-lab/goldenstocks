"""代理人交易規則 + 研究 + 最常見故事頁（/agentrules）。

只描述「機器人」的規則與回測，不是人類的下單習慣。純靜態 HTML，無任何對外連線與下單能力。
規則來自 config/clone_agent_rules.json（有版本號與指紋），回測數字讀
data/clone_agent/backtest_<id>_v<version>.json（由 scripts/research/clone_agent_rules_backtest.py 產生）。
"""
from __future__ import annotations

import html as _html
import json

from clone_agent.approval import _data_dir
from clone_agent.rules_version import fingerprint, label, load_rules

CSS = (
    "body{background:#0d1117;color:#c9d1d9;font:13px/1.7 -apple-system,'PingFang TC',sans-serif;margin:0;padding:14px 16px 40px}"
    "h2{font-size:17px;margin:2px 0 4px}h3{font-size:14px;color:#79c0ff;margin:20px 0 4px;border-bottom:1px solid #30363d;padding-bottom:3px}"
    "a{color:#79c0ff}.meta{color:#8b949e;font-size:12px;margin-bottom:10px}"
    "table{border-collapse:collapse;width:100%;max-width:1100px;margin:2px 0;font-size:12px}"
    "th{background:#161b22;color:#8b949e;text-align:left;padding:5px 8px;border-bottom:1px solid #30363d}"
    "td{border-bottom:1px solid #21262d;padding:5px 8px;vertical-align:top}"
    "td.c{color:#e6edf3;font-weight:600;white-space:nowrap}td.n{text-align:right;white-space:nowrap}"
    ".up{color:#ff7b72}.dn{color:#3fb950}.warnv{color:#e3b341}.dim{color:#8b949e}"
    ".leg{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;margin:10px 0;font-size:12px;max-width:1100px}"
    ".story{background:#0f1b2d;border:1px solid #1f6feb;border-radius:6px;padding:10px 14px;margin:10px 0;font-size:13px;max-width:1100px}"
    ".ver{background:#10201a;border:1px solid #238636;border-radius:6px;padding:8px 12px;margin:10px 0;font-size:13px;max-width:1100px}"
    "details{max-width:1100px;margin:4px 0}summary{cursor:pointer;color:#79c0ff}"
    "code{background:#161b22;padding:1px 5px;border-radius:3px;font-size:11px;color:#adbac7}"
)

_BOUNDARY = [
    ("不碰富邦 API", "watcher 與頁面只讀本地檔（逐筆 raw、委託簿快取），不開任何富邦連線；期貨買一價取自既有收集器的快取"),
    ("沒有下單能力", "套件內禁止出現富邦 SDK 與下單層的任何 import 或送單呼叫（由 <code>test_no_order_capability_anywhere_in_package</code> 釘住）"),
    ("訊號只上網頁", "出現在 /agent 等 HTML 頁，不寄 email"),
    ("實單層（2026-10-06 起）", "規則通過的卡由獨立下單層自動送單；頁面沒有批准／略過按鈕，只有「關閉自動送單」與 AI 監督員撤單能擋單；出場仍手動"),
]

_FALSIFIED = [
    ("「掛低等回檔」", "實測掛得低＝買在大戶出貨的那一秒（逆選擇），所以進場價掛買一而不是更低的價位（2026-10-06 起 live 由「買一下 1 檔」改為買一，兩者回測無法區分）"),
    ("急殺後掛下方限價", "改用 bps 距離後樣本外歸零；以檔數定義的「平台」是價位級距的假結構"),
    ("回檔承接（/stories）", "<span class='warnv'>與本代理人故事直接衝突</span>：故事頁判決該標籤 t=0.47、n=67、無 edge。代理人的樣本內數字尚未被樣本外支持，兩者必須並陳"),
]

_METHOD = [
    ("門檻是看過資料才定的", "9/29–10/5 共 5 日全是樣本內（10/5 在 1.2.0 設計時已被看過）；從 10/6 起每多一個紙上交易日才算驗證一天"),
    ("名單用 10/2 價格回推 9/29", "名單依 10/2 收盤價帶凍結（華新科 9/30、10/1 實際低於 350 元），屬事後名單；實際上線時名單每日重評"),
    ("毛 vs 淨", "bps 欄為毛報酬；「元」欄已扣手續費＋期交稅"),
    ("手續費已確認", "jack 2026-10-05 確認實際費率就是 176 元／口／邊（與牌價相同）；實測 0→300 元只差約 2 萬，不是關鍵"),
    ("排隊模擬", "qcons／qpro／qopt 是對「前面排多少量、撤單多少」的三種假設；同一檔先後兩張單在模擬裡各自獨立排隊（真實上第二張會排在第一張後面），只有真實掛單才能校準"),
    ("結果對路徑敏感", "保證金上限只讓一部分訊號成交，批准延遲差幾十秒、或把 1.2.0 換成別的濾網組合，成交的是哪幾筆就整批改變，損益很容易差 ±15% 以上（v1.0.0 當時：5→30 秒 +481k→+507k）"),
    ("樣本只有三十幾筆", "v1.2.0 在 5 日內只有 31 筆成交，有效樣本更少（同一檔連續出卡不獨立）；叢集重抽 90% 區間只反映抽樣，不含「門檻是看過資料才定」的選擇偏誤"),
    ("回測名單口徑與 live 差一個價格帶", "回測 headline 只套凍結名單；live watcher 另加卡片掛價價格帶（350–2000）。1.2.0 的回測已先套名單與價格帶，兩者一致"),
    ("帳務逐日重置", "回測每天重算保證金，無跨日持倉；隔夜風險未納入"),
]

_TODO = [
    ("watcher 的名單／價格帶落地驗證", "2026-10-05 起 live watcher 只對凍結名單出卡、卡片掛價超出價格帶不寫入、候選檔帶規則版本；歷史重掃 10/5 與回測逐筆一致（25/25），但 live 實際出卡行為要等下一個交易日才驗得到"),
    ("成交資料驗證 qpro", "把真實掛單的成交與否與等待時間，拿來校準排隊模擬"),
    ("book_time 與 trade_time 對齊", "委託簿與逐筆時間戳是否同步尚未驗證"),
    ("20 日紙上帳", "累積真成交率與 ≥80% 同意率，才能談升級；v1.0.0 凍結後的每一天都是真樣本外"),
    ("期貨 tick 修正（v1.0.1 已完成）", "1000 元以上標的進場價原本偏低 4 元；v1.0.1 已修（升版、補測試、全部回測重跑，淨損益 +481k → +340k）。<b>跑 v1.0.0 的 live watcher 仍是舊碼，要收盤後重啟才吃到</b>"),
    ("故事版訊號 1.2.0 的 live 驗證", "新濾網的排名母體在回測（watcher 的 tape 池）與 live 必須一致；10/6 起的紙上交易才是真樣本外（10/5 已被看過）"),
    ("CA-R1.1 出場候選的 20 日紙上帳", "90 分鐘 + 賣一 + 5 分鐘改價要累積真樣本外；過夜跳空與隔夜保證金還沒有任何實例"),
    ("A–D 排除過濾上卡片", "目前只有研究；先在卡片上標示命中，不先拿來擋單"),
    ("台達電／日月光加入收集", "只能收盤後做，並驗證啟動 banner"),
]

_STORY = [
    ("先講一個方法上的發現", "<span class='warnv'>同一分鐘出現多張卡時（尤其 09:30 整點一次冒出十幾張），誰先搶到 147 萬額度是任意的；同一組訊號只換同分鐘的先後順序，淨損益就能差好幾十萬。</span>例：只用①全日大戶佔比，順序一種是 +5k、另一種是 +348k。所以下面每一列都同時給「照掃描順序」與「同分鐘隨機排序 40 次的平均／標準差」；<b>只看單一數字會被騙</b>。第 3 節的成績單用掃描順序（+269k），隨機排序平均是 +233k。"),
    ("主檢定：新規則 vs 無濾網", "無濾網（09:30 起、30 分高點回檔）312 張訊號：掃描順序 34 筆成交 −16k，隨機排序平均 +1k（標準差 122k）。1.2.0 146 張：31 筆成交 <b>+269k</b>（每筆 +8.7k、勝率 52%、毛均 39 bps），隨機排序平均 <b>+233k（標準差 57k、5%～95% = +157k～+313k，40 次全為正）</b>。逐日 −10k／+184k／+55k／−9k／+50k（3 勝 2 負）。"),
    ("隨機剔除對照", "從無濾網的訊號隨機剔除同樣比例（留 38.4%）、同時隨機排序、再套冷卻與保證金模擬，200 次：淨損益平均 +106k、標準差 139k、5%～95% 為 −115k～+345k。1.2.0 的隨機排序平均（+233k）落在第 <b>81</b> 百分位：<span class='warnv'>方向對、但不顯著</span>——隨機亂剔也常有 +100k 以上，因為這週整體偏多。"),
    ("集中度與超額", "前 3 筆（環球晶 9/30 +99.6k、景碩 10/5 +65.6k、環球晶 +43.6k）合計 +208.7k，<b>扣掉後剩 +60.5k</b>；中位數每筆只有 +1.6k、中位毛報酬 15 bps。同窗名單等權平均漲 31 bps，1.2.0 平均毛 39 bps，<b>平均超額只有 +7.8 bps、中位超額 −20.8 bps</b>；10/5 超額 −38 bps。叢集重抽（21 叢集）每筆平均 +8.7k、90% 區間 −0.5k ~ +18.8k（含 0）。31 筆成交裡有 13 筆訊號在 09:35 前。"),
    ("拆掉一條看貢獻（隨機排序平均）", "完整①③④ +233k；拿掉① +294k（③④合用，最好、勝率 66%）；拿掉③ +162k；拿掉④ +143k。①單獨 +84k（對同量隨機剔除第 53.5 百分位＝與隨機無異）。單一「30 分大戶−散戶佔比」+211k。[推論] 訊號主要來自 30 分鐘大戶與散戶流量，全日大戶條件沒有增量、甚至略拖累。這個結論的證據強度只有「方向一致」，各列的標準差都在 4 萬～15 萬。"),
    ("加上符號條件（對照）", "只用排名 +233k；加「30 分大戶淨額 >0」+188k；再加「散戶淨額 <0」+80k；只用符號 +72k。[推論] 排名比排名加符號好，符號條件把有用的樣本也砍掉。"),
    ("中實桶診斷（只看不濾）", "在 1.2.0 內再依中實單淨買佔比切：前半 +81k、後半 +229k（隨機排序平均）。[推論] 後半較佳，但只是診斷；尚未寫進規則，卡片上只顯示該數字。"),
    ("09:30 前（不納入規則）", "4 日 982 張卡：無濾網 82 張訊號、16 筆成交、+240k、勝率 75%；套新濾網 47 張、15 筆、+213k；被剔除的 48 張反而 +338k、勝率 87%。<span class='warnv'>這組是在舊的排序處理下算的、沒有用隨機排序重算，數字只作方向參考。</span>[推論] 開盤前「大戶已就位」的條件看不出資訊，所以 09:30 前維持人工判斷。"),
    ("搭配 1.1 出場候選", "1.2.0 進場 + 90 分鐘賣一出場：25 筆成交 +350.5k（2 筆過夜）；無濾網同出場 +175.6k。<span class='warnv'>同樣是舊排序處理下的數字，未重算。</span>"),
    ("結論與限制", "[事實] 事後檢定、5 日、單一偏多 regime、約 30 筆成交；同分鐘排序造成的路徑雜訊（標準差 5.7 萬）本身就大到足以改變排名；對隨機剔除在第 81 百分位、不顯著；扣掉前三筆只剩 +60k。[推論] 故事在 30 分鐘流量部分有一致的方向，全日大戶條件沒有增量。<b>10/6 起的紙上交易才是真樣本外</b>；在那之前不得為了讓 5 日更好看而改門檻。"),
]

_EXIT_SPEC = [
    ("① 計畫出場時間", "成交後<b>滿 90 分鐘</b>送出平倉委託（賣出）。時間從<b>進場成交那一刻</b>算起，不是從出卡算起。"),
    ("② 平倉掛價", "掛<b>賣一</b>（期貨當下最佳賣價），不看內外盤、不看量。簡單版：不做任何判斷。"),
    ("③ 改價", "平倉單送出後每 <b>5 分鐘</b>撤單、重新讀賣一、重掛（撤單重掛會失去排隊位置，模擬已如實扣掉）。送單延遲 5 秒。"),
    ("④ 收盤不強制平倉", "日盤 13:45 收盤時，未成交的平倉單作廢；<b>部位與保證金續留</b>，次一交易日 09:00 起以同樣的掛價與改價規則繼續送（已超過 90 分鐘，所以一開盤就送）。資料結束仍未平的只算帳面估值、不列為已實現。"),
    ("⑤ 保證金", "147 萬上限照舊，先到先得；持倉（含過夜）與未成交的新倉委託都佔用，<b>直到平倉成交才釋放</b>。持有時間變長 → 同一天能接的新單變少，這是 90 分鐘比 60 分鐘「成交筆數少」的原因。"),
    ("⑥ 成本", "手續費 176 元／口／邊、期交稅 0.002%／邊（名目金額）。掛賣一的平倉價 ≥ 當時買一，所以是<b>被動賣出</b>：不付價差，但要排隊，可能多掛幾輪。"),
    ("⑦ 提早平倉條件（只警示，不自動出場）", "四條流量條件（單位<b>一律 %</b>，百分位以儀表板全部列約 42 檔為母體）：<br>"
     "• 30 分鐘組：C 散戶 30 分鐘淨額佔比<b>前 30%</b> ∧ D 大戶 30 分鐘淨額佔比<b>倒數 30%</b>，兩者同時成立；<br>"
     "• 日級組：A 近兩日大戶佔比<b>倒數 30%</b> ∨ B 當日大戶佔比<b>倒數 30%</b>，任一成立；<br>"
     "• 任一組<b>連續 5 分鐘（逐 30 秒檢查、連續 11 點都成立，且全部發生在成交之後）</b>→ 提早送平倉單。<br>"
     "<span class='warnv'>研究結論是這些條件<b>比隨機提早出場還差</b>（見 2B），所以候選規則只在卡片上顯示警示、不自動提早出場。</span>"),
    ("⑧ 內外盤判斷（已放棄）", "原本想依近 30 分鐘外盤比例 ≥55% 掛賣一、≤45% 掛買一、其間掛中價。實測與「一律賣一」沒有差別、且成績較差（見 2B），jack 決定改用簡單版，<b>不用內外盤判斷</b>。"),
]

_EXIT_EVIDENCE = [
    ("停損一律變差", "固定停損（30–200 bps、3 千–2 萬元／口）在<b>每一個</b>檔位都比「不停損」賺得少：不設保證金上限，無停損 +773k，停損 30 bps −27k、50 bps +73k、120 bps +361k、200 bps +636k；勝率從 57% 掉到 29–53%。[猜測] 原因：進場是被動掛在買一下 1 檔（回測口徑），成交後常先逆向跳動再回來，停損正好砍在回來之前（未獨立檢定）。"),
    ("移動停利只改善最差日", "移動停利（啟動 80–200 bps、回吐 40–80 bps）總損益 +401k～+649k，都低於無停損 +773k，且最差的 10/5 也沒有變好（−72k～−110k vs 無停損 −48k）。不採用。"),
    ("單位：% 與元差不多", "把 % 與元兩套門檻調成等價後比較，總損益幾乎一樣；% 在不同價位的股票上更一致（離散比 1.5–1.6 對 1.8–2.3），所以流量條件一律用 %。"),
    ("提早出場比隨機差", "用「隨機重新分配持有時間」做置換檢定：30 分鐘組（C∧D）提早出場後實際 −28 萬～−35 萬，隨機分配持有時間的平均約 −7 萬～+17 萬，p 值 0.98–1.0（p＝隨機結果不低於實際的比例，越接近 1 越代表實際比隨機差）；日級組 A∨B 與隨機沒有顯著差別；四條件任一成立（ABCD）p 值 0.97–0.999。"),
    ("內外盤規則沒有預測力", "近 30 分鐘外盤比例與之後的價格變化相關係數約 −0.05；內外盤規則成績（+410k）低於一律賣一（+476k），不值得多一層複雜度。"),
    ("最終模擬成績（CA-R1.1 候選）", "90 分鐘 + 賣一 + 5 分鐘改價，無提早出場：147 萬上限、先到先得下<b>接受 27 筆、全部平倉</b>，已實現淨 <b>+475,563</b> 元、勝率 74%、平均持有 99 分鐘、平均掛單 2.4 次，<b>沒有任何一筆留倉過夜</b>；逐日 +97.7k／+69.5k／+90.1k／+88.8k／+129.5k（5 日皆正）。"),
    ("對照：其他出場口徑（同一批進場）", "一律掛買一（立即成交）+530,264（28 筆）；內外盤規則 +409,639（27 筆）；賣一 + 提早出場 +413,295（35 筆、17 筆提早，勝率 60%）；內外盤 + 提早出場 +321,631（37 筆、勝率 51%）。<b>掛賣一比掛買一少賺約 5.5 萬</b>。[推論] 被動排隊賣出要等，等的期間價格有時已回落；省下的價差抵不過等待成本（未逐筆拆解）。"),
    ("要小心的地方", "[事實] 只有 5 個交易日、單一偏多 regime、27 筆有效樣本；[推論] 賣一排隊成交用的是模擬（五檔簿＋逐筆成交），真實排隊位置未驗證；[猜測] 空頭日、跳空下殺日可能讓「不停損、不提早出場」吃到大虧——這個情境在樣本裡沒有出現過，不能當作已被排除。"),
]

_EXCL = [
    ("A 近兩日大戶佔比倒數 30%", "排除後 5 日淨 +556k（基準 +481k）；被排除的卡平均毛報酬 29 bps，沒被排除 63 bps。隨機同量剔除檢定 p=0.057（邊緣）。"),
    ("B 當日大戶佔比倒數 30%", "排除後 +549k；p=0.057（邊緣）。"),
    ("C 30 分鐘散戶佔比前 30%", "名單內只有 4 張卡命中；排除後 +457k，與隨機剔除沒有差別（p=0.73），不採用。"),
    ("D 30 分鐘大戶佔比倒數 30%", "命中 8 張；排除後 +457k，p=0.63，不採用。"),
    ("四條件任一命中就排除", "+550k，勝率 70%，樣本外 10/5 +103k；隨機剔除 p=0.017。「只排除 A+B」p=0.023，「只排除 C+D」p=0.54——<b>有訊息的是日級大戶佔比（A、B），不是 30 分鐘流量（C、D）</b>。"),
    ("目前狀態", "<span class='warnv'>只是研究，尚未寫進規則、卡片上也還沒標 A–D 命中。</span>門檻是看過資料才定的，且 5 日單一 regime；必須等真樣本外累積後才能升版採用。"),
]

_ASSUME = [
    ("進場掛價與 tick", "live 自 2026-10-06 起掛買一（v1.2.1）；以下<b>所有歷史回測數字皆為買一下 1 檔口徑</b>。tick 用期貨級距：&lt;10 0.01、&lt;50 0.05、&lt;100 0.1、&lt;500 0.5、≥500 1（v1.0.0 誤用現貨級距，1000 元以上少掛 4 元，v1.0.1 已修並重跑全部回測）。"),
    ("每口與保證金", "每口 2000 股；保證金率約 20.25%（每口 = 價 × 2000 × 保證金率）；上限 147 萬。"),
    ("排隊模型", "qpro：撤單依前後口數比例分配；同價位送單後排在最後；5 秒送單延遲。賣出排隊用同一套（鏡像）。"),
    ("送單時點", "實單層在卡片出現後約 5 秒自動送出（10:00–12:00 視窗），與回測假設的 5 秒一致；回測中延遲 30 秒內對損益影響約 ±15%。"),
    ("樣本", "2026-09-29 ～ 10-05 共 5 日；9/29–10/2 是樣本內、10/5 是唯一樣本外；名單用 10/2 價格回推，屬事後名單。"),
    ("隔夜風險", "持倉可留到次日 09:00 繼續平倉。模擬 5 日內沒有出現過夜，所以<b>隔夜跳空風險與隔夜保證金（假設與日內相同）都沒被驗證</b>。"),
    ("資料口徑", "期貨逐筆成交（依 trade_time＋serial 去重）＋五檔委託簿，日盤 08:45–13:45；不含夜盤（個股期貨夜盤只有 2330、2303）。"),
]

_VERSIONS = [
    ("CA-R1 v1.2.0（故事版訊號，in-sample）", "出卡條件改為「全日大戶主動買佔比不在倒數 30%、近 30 分鐘高點回檔 ≥0.5%、30 分大戶佔比不在倒數 30%、30 分散戶佔比留淨買由小到大 70%、30 分成交額排名 ≥30」，09:30 起；兩日條件與舊的「散戶賣給大戶」符號門檻拿掉，濾網在 20 分鐘冷卻之前。進場與出場不變（回測口徑買一下 1 檔、60 分鐘；live 自 v1.2.1 改掛買一）。1.1 保留給出場候選。結果見 2F。"),
    ("CA-R1 v1.0.1", "只修期貨 tick（1000 元以上標的進場價偏低 4 元）。全部回測重跑：淨 +340k（原 +481k）、40 筆成交。"),
    ("CA-R1 v1.0.0（凍結）", "舊訊號（寬條件、全時段）。出場＝成交後 60 分鐘、最後成交價、收盤截斷。第 2D、2E、11 節中 +481k 的數字都是這個口徑。"),
    ("CA-R1.1 候選（未凍結）", "進場不變；出場改為 90 分鐘 + 賣一 + 5 分鐘改價 + 不強制平倉 + 可過夜。模擬 +475,563（27 筆，見 2B；那是 v1.0.0 訊號上的數字）。要成為正式版：改 <code>config/clone_agent_rules.json</code> 的 <code>exit</code> 並升版號、登記指紋、更新 watcher 與測試、重跑版本化回測；<b>在那之前，卡片與 watcher 以 60 分鐘為準。</b>"),
]


def _table(head, rows, num_cols=()):
    out = ["<table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"]
    for r in rows:
        tds = []
        for i, c in enumerate(r):
            cls = "c" if i == 0 else ("n" if i in num_cols else "")
            tds.append(f"<td class='{cls}'>{c}</td>")
        out.append("<tr>" + "".join(tds) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _n(v, sign=False):
    if v is None:
        return "—"
    return f"{v:+,.0f}" if sign else f"{v:,.0f}"


def _c(v):
    return "up" if v > 0 else ("dn" if v < 0 else "")


def _money(v):
    return f"<span class='{_c(v)}'>{_n(v, True)}</span>"


def _bt(rules):
    p = _data_dir() / f"backtest_{rules['id']}_v{rules['version']}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _pipeline(R):
    u, s, c, o, e, k = R["universe"], R["signal"], R["capital"], R["order"], R["exit"], R["cost"]
    members = "、".join(f"{m['name']}" for m in u["members"])
    return [
        ("0 名單", f"只做 價格 {u['price_band'][0]}–{u['price_band'][1]} 元 ∧ 有族群 的個股期貨；<b>{u['list_frozen_on']} 凍結 {len(u['members'])} 檔</b>：{members}。戰場 {u['battlefield_n']} 檔為訊號母體，橫斷面排名用 {u['rank_pool_n']} 檔",
         "<code>universe.members</code>", "已實作（2026-10-05 起 watcher 只出名單內、價格帶內的卡；候選檔帶版本戳）"),
        ("1 買賣雙方", f"大戶＝單筆主動成交 ≥{s['big_amt']/1e4:,.0f} 萬；散戶＝單筆 1 張且 <{s['retail_cap']/1e4:,.0f} 萬；看「散戶賣給大戶」",
         "同 biglot/trade_ingest.py 口徑", "已實作"),
        ("2 出卡條件（故事版 1.2.0）", f"{s['start_hm']} 之後（之前由 jack 人工判斷）∧ ①全日大戶主動買佔成交金額，<b>不在倒數 {100-s['rank_keep_pct']}%</b> ∧ ②距近 {s['hi_window_min']} 分鐘高點 ≤ {s['pullback_max']}% ∧ ③近 30 分鐘大戶佔比不在倒數 {100-s['rank_keep_pct']}% ∧ ④近 30 分鐘散戶佔比：散戶是淨賣方，留淨買佔比由小到大的前 {s['rank_keep_pct']}% ∧ ⑤30 分成交額排名 ≥{s['amt_pct_min']} ∧ 非 {s['skip_hours'][0]}–{s['skip_hours'][1]} 時。佔比＝淨主動買額 ÷ 成交金額（%）；母體＝當下全部有成交的個股（與儀表板同）。<b>濾網在冷卻之前</b>：被濾掉的不啟動冷卻",
         "<code>signal.*</code>", "已實作（1.2.0）"),
        ("3 冷卻", f"同一檔 {s['recheck_min']} 分鐘內不重複出卡", f"recheck_min={s['recheck_min']}", "已實作"),
        ("4 星等", f"四個嚴旗標各 1 顆星：①30 分大戶−散戶佔比排前 {s['rank_keep_pct']}% ②成交額排名 ≥{s['amt_pct_strict']} ③距高 ≤ {s['pullback_strict']}% ④共動夥伴內當日漲幅排後半（≤{s['chg_cm_strict']}）。星等只排序，不當篩選",
         "<code>signal.*_strict</code>", "已實作"),
        ("5 進場價", f"{R['entry']['price']}，{R['entry']['style']}排隊；不追價、不吃賣一，{R['entry']['lots']} 口／張卡", "lim = bid − 1 tick", "已實作"),
        ("6 資金", f"保證金上限 <b>{c['margin_cap']:,}</b>（淨值 {c['equity']:,} 的 {100*c['margin_cap']/c['equity']:.1f}%）；<b>持倉與未成交的新倉委託都佔用</b>；超過就整張卡放棄",
         "每口 = 價 × 2000 × 保證金率", "<span class='warnv'>回測模擬；watcher 只標註</span>"),
        ("7 委託順序", f"先到先得（{o['policy']}）；排隊模型 <b>{o['queue_model']}</b>（撤單依前後比例分配）；人工批准延遲 {o['submit_lag_s']} 秒；沒成交掛到收盤",
         "撤低星換高星、每檔限 1 口兩種變體皆未勝出", "研究"),
        ("8 出場", f"<b>v1.0.0（凍結）</b>：成交後持有 {e['hold_min']} 分鐘、以{e['price']}出場，收盤 {e['session_end']} 截斷。<b>CA-R1.1 候選</b>：滿 90 分鐘掛賣一、每 5 分鐘改價、不強制平倉、可過夜，完整規則見 2A",
         f"hold={e['hold_min']}min（SSOT）", "<span class='warnv'>僅研究（候選未凍結）</span>"),
        ("9 成本", f"手續費 {k['fee_per_lot_per_side']} 元／口／邊（{k['fee_basis']}）＋期交稅 {k['futures_tax_rate_per_side']*100:.3f}%／邊", "fee、tax", "回測已計"),
        ("10 實單層", "規則通過的卡由獨立下單層（src/order/clone_agent_*）自動送限價買單；頁面套件本身沒有任何下單能力；出場手動、AI 監督員僅能請求撤單", "10:00–12:00、147 萬上限、每日 15 張", "已實作"),
    ]


def render_rules_page() -> str:
    R = load_rules()
    B = _bt(R)
    lab = label(R)
    p = [f"<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'><title>代理人交易規則與研究</title><style>{CSS}</style></head><body>",
         "<div class='meta'><a href='/'>← 大戶儀表板</a> ｜ <a href='/agent'>🤖 操盤手代理人</a> ｜ <a href='/stories'>📚 故事</a> ｜ "
         f"<b>代理人：機器人規則 · 回測 · 最常見故事</b> ｜ {lab}</div>",
         "<h2>這一頁寫的是「機器人」怎麼交易，不是人怎麼交易</h2>",
         f"<div class='ver'><b>規則版本 {lab}</b>　凍結日 {R['frozen_on']}　回測起點 {R['backtest_window_start']}　"
         f"<span class='dim'>{_html.escape(R['status'])}</span>"
         "<br>SSOT：<code>config/clone_agent_rules.json</code>（指紋由全部參數算出；改任何門檻／名單／資金／成本就要升版號，"
         "<code>tests/test_clone_agent_rules.py</code> 會擋；watcher 常數與此檔不一致也會擋）。"
         "<br><b>1.2.0 的真樣本外從 2026-10-06 起</b>（10/5 以前的資料已被看過、10/5 不再算樣本外）：升版前不得因為某天表現差而改參數。</div>",
         "<div class='leg'>代理人分兩層：<b>頁面與 watcher 只建議、不下單</b>，實單由獨立下單層自動送出（出場手動）。watcher 每分鐘掃描戰場個股期貨，"
         "09:30 之後，找「<b>全日與近 30 分鐘大戶主動買佔比都不弱 ∧ 近 30 分鐘散戶沒有在追買 ∧ 價格自 30 分鐘高點回檔 ≥0.5% ∧ 成交活躍</b>」的標的，在<b>買一</b>排隊承接（回測口徑為買一下 1 檔），預期持有約一小時。實單視窗 10:00–12:00，之外由人工判斷。</div>"]

    p.append("<h3>1. 硬邊界</h3>")
    p.append(_table(["邊界", "內容"], _BOUNDARY))
    p.append(f"<h3>2. 規則流水線（{lab}）</h3>")
    p.append(_table(["步驟", "規則", "參數／位置", "狀態"], _pipeline(R)))
    p.append("<div class='leg'><b>狀態欄讀法</b>：「已實作」＝live 程式現在就這樣做；「僅回測／僅研究」＝規則只在回測腳本裡成立，live 還沒有。目前只有排隊成交模擬、保證金上限與出場規則仍是回測／研究，見 2A 與第 12 節。</div>")

    p.append("<h3>2A. 出場規則（CA-R1.1 候選，未凍結）—— 完整版</h3>")
    p.append("<div class='leg'>出場和進場一樣拆成「<b>送出委託</b>」與「<b>排隊成交</b>」兩段模擬，賣出才算真的賣掉；"
             "不是「到時間就用最後成交價」。完整說明檔：<code>~/goldenstocks-data/data/clone_agent/exit_rules_CA-R1.1_candidate.md</code>（內含較早的內外盤版本，以本節為準）。</div>")
    p.append(_table(["項目", "規則"], _EXIT_SPEC))
    p.append("<h3>2B. 出場研究：測了什麼、結論是什麼（v1.0.0 訊號上的數字）</h3>")
    p.append(_table(["問題", "結果"], _EXIT_EVIDENCE))
    p.append("<h3>2C. 版本對照：哪一套才是「現在算數」的規則</h3>")
    p.append(_table(["版本", "內容"], _VERSIONS))
    p.append("<h3>2F. 故事版訊號（v1.2.0）回測與檢定</h3>")
    p.append("<div class='leg'>全部是事後（post-hoc）描述性檢定：門檻固定在 30／70、每個問題只跑一次、沒有掃參數。回測口徑＝qpro 排隊、147 萬上限先到先得、手續費 176、60 分鐘最後成交價出場。"
             "完整數字：<code>~/goldenstocks-data/data/clone_agent/story_study_CA-R1_v1.2.0.json</code>；本頁第 3 節以後是掃描順序的版本化回測。</div>")
    p.append(_table(["問題", "結果"], _STORY))
    p.append("<h3>2D. 進場前「排除過濾」研究（A–D 四條件，v1.0.0 訊號上的結果）</h3>")
    p.append("<div class='leg'>問題：進場訊號出現時，若已符合「兩日大戶倒數 30%（A）、當日大戶倒數 30%（B）、30 分散戶前 30%（C）、30 分大戶倒數 30%（D）」，是否該放棄這張卡？"
             "結果檔：<code>exclusion_filter_CA-R1_v1.0.0.json</code>。比較基準是「隨機剔除同樣張數」的分布。</div>")
    p.append(_table(["條件", "結果"], _EXCL))
    p.append("<h3>2E. 執行與帳務假設、已知缺陷</h3>")
    p.append(_table(["項目", "內容"], _ASSUME))

    if not B:
        p.append("<div class='leg warnv'>找不到此版本的回測結果檔，請執行 <code>scripts/research/clone_agent_rules_backtest.py</code>。</div></body></html>")
        return "".join(p)

    H, IO, CC, BM, RK, SP = B["headline"], B["is_oos"], B["concentration"], B["benchmark"], B["risk"], B["signal_profile"]
    all_n, list_n = B["signals_all"], B["signals_in_list"]

    p.append(f"<h3>3. 回測成績單 —— 全部代理人選股、照規則全送（9/29–10/5，{lab}）</h3>")
    p.append(f"<div class='leg'>設定：代理人 5 日共 <b>{all_n}</b> 筆訊號，名單內 <b>{list_n}</b> 筆全部照送；保證金上限 {B['cap']:,}、"
             f"先到先得、排隊模型 {B['mode']}（逐筆成交＋委託簿重放，同價位排在最後）、手續費 {B['fee']} 元／口／邊＋期交稅 0.002%／邊、"
             "委託後 5 秒進佇列、持有 60 分鐘用最後成交價出場。訊號是用 watcher 的 <code>--scan</code> 對歷史每分鐘重跑得到。</div>")
    p.append(_table(["項目", "數字", "說明"],
                    [("淨損益", _money(H["net"]), f"毛 {_n(H['gross'], True)} − 手續費與稅 {_n(H['cost'])}（費稅只佔毛利 {100*H['cost']/H['gross']:.0f}%）"),
                     ("成交／接受／擋掉", f"{H['fills']} / {H['fills'] and B['daily'] and sum(d['接受'] for d in B['daily'])} / {H['skipped_by_margin']}",
                      f"擋掉的 {H['skipped_by_margin']} 張裡有 {H['skipped_would_fill']} 張若不被擋會成交"),
                     ("勝率／平均／中位數（元）", f"{100*H['win']:.0f}% / {_n(H['mean_net'], True)} / {_n(H['median_net'], True)}", f"平均毛報酬 {H['mean_gross_bps']:.0f} bps、中位 {H['median_gross_bps']:.0f} bps"),
                     ("買一價出場（較保守）", _money(H["net_bid_exit"]), "把最後成交價出場換成賣在當時買一"),
                     ("不設保證金上限", _money(H["unconstrained_net"]), f"{H['unconstrained_fills']} 筆成交：資金是第一瓶頸，上限讓損益少一半"),
                     ("掛單中位等待", f"{H['wait_median_min']:.1f} 分鐘", f"名單內訊號有 {100*SP['filled_unconstrained_rate']:.0f}% 掛得到")],
                    num_cols=(1,)))
    p.append("<br>" + _table(["日期", "訊號", "接受", "擋掉", "成交", "掛到收盤沒成交", "毛損益", "費稅", "淨損益", "買一價出場", "峰值保證金(萬)", "勝率"],
                             [(d["day"], d["訊號"], d["接受"], d["擋掉"], d["成交"], d["掛到收盤未成交"], _n(d["毛損益"], True), _n(d["費稅"]),
                               _money(d["淨損益"]), _money(d["買一價出場淨損益"]), d["峰值保證金萬"],
                               "—" if d["勝率"] is None else f"{100*d['勝率']:.0f}%") for d in B["daily"]],
                             num_cols=tuple(range(1, 12))))

    p.append("<h3>4. 全部照送的話，每天實際怎麼交易（逐筆）</h3>")
    by_day: dict[str, list] = {}
    for r in B["ledger"]:
        by_day.setdefault(r["day"], []).append(r)
    skip_day: dict[str, int] = {}
    for r in B["skipped"]:
        skip_day[r["day"]] = skip_day.get(r["day"], 0) + 1
    for d in B["daily"]:
        rows = by_day.get(d["day"], [])
        rk = RK.get(d["day"], {})
        p.append(f"<details {'open' if d['day'] == B['daily'][-1]['day'] else ''}><summary><b>{d['day']}</b>　訊號 {d['訊號']}、接受 {d['接受']}、"
                 f"保證金擋掉 {d['擋掉']}、成交 {d['成交']}、淨損益 {_money(d['淨損益'])}　"
                 f"<span class='dim'>同時最多 {rk.get('max_concurrent')} 筆、同族群最多 {rk.get('max_same_group')} 筆、名目峰值 {rk.get('peak_notional_萬')} 萬（淨值 {rk.get('peak_notional_x_equity')} 倍）、"
                 f"盤中最差浮虧 {_n(rk.get('worst_mtm'), True)}（淨值 {rk.get('worst_mtm_pct_equity')}%，{rk.get('worst_at')}）</span></summary>")
        p.append(_table(["標的", "星", "卡片", "成交", "等待(分)", "出場", "進價", "出價", "保證金", "毛(bps)", "同窗名單均(bps)", "淨損益"],
                        [(r["name"], "★" * int(r["stars"]), r["signal"], r["fill"], r["wait_min"], r["exit"], r["entry"], r["exit_last"],
                          _n(r["margin"]), _n(r["gross_bps"], True), _n(r["bench_bps"], True), _money(r["net"])) for r in rows],
                        num_cols=tuple(range(4, 12))))
        p.append("</details>")

    p.append("<h3>5. 前 4 日 vs 10/5，以及扣掉「大家一起漲」之後（1.2.0 全為樣本內；真樣本外從 10/6 起）</h3>")
    p.append(_table(["口徑", "9/29–10/2（4 天）", "10/5（1 天，已被看過）"],
                    [("成交筆數", IO["is_n"], IO["oos_n"]),
                     ("淨損益", _money(IO["is_net"]), _money(IO["oos_net"])),
                     ("買一價出場淨損益", "—", _money(IO["oos_net_bid"])),
                     ("勝率", f"{100*IO['is_win']:.0f}%", f"{100*IO['oos_win']:.0f}%"),
                     ("毛報酬 平均／中位 (bps)", f"{IO['is_mean_bps']:.0f} / {IO['is_median_bps']:.0f}", f"{IO['oos_mean_bps']:.0f} / {IO['oos_median_bps']:.0f}"),
                     ("相對同窗名單等權平均的超額 (bps)", _n(BM["is_excess_bps"], True), _n(BM["oos_excess_bps"], True))],
                    num_cols=(1, 2)))
    p.append(f"<div class='leg'><b>判讀</b>：[事實] 10/5 那一天平均毛報酬 {IO['oos_mean_bps']:.0f} bps、中位 {IO['oos_median_bps']:.0f} bps，勝率 {100*IO['oos_win']:.0f}%；"
             f"它的 {_n(IO['oos_net'], True)} 元淨利<b>全靠 {CC['top1']} 一筆 {_n(CC['top1_net'], True)}</b>，扣掉那筆是 {_n(IO['oos_net']-CC['top1_net'], True)}（元報酬被高價股的單筆放大，bps 與元不同向）。"
             f"<br>[事實] 這週名單內標的在同窗口平均漲 {BM['mean_bench_bps']:.0f} bps（多頭週），所以「買了就賺」本身就有 beta；扣掉後樣本內超額 {_n(BM['is_excess_bps'], True)} bps、樣本外 {_n(BM['oos_excess_bps'], True)} bps。"
             "<br>[推論] 門檻在這 5 天上設計 → 全部高估；10/5 只是其中一天，不是驗證。</div>")

    p.append("<h3>6. 集中度與風險</h3>")
    top = sorted(CC["by_stock"].items(), key=lambda kv: -kv[1]["net"])
    p.append(_table(["標的", "淨損益", "筆數"], [(k, _money(v["net"]), v["n"]) for k, v in top], num_cols=(1, 2)))
    ci = B["cluster_ci"]
    p.append(f"<div class='leg'>[事實] 前 3 大單筆合計 {_n(CC['top3_net'], True)}（佔 {100*CC['top3_net']/H['net']:.0f}%），扣掉後 {_n(CC['ex_top3_net'], True)}。"
             f"依（日×標的）叢集重抽，每筆平均 {_n(ci['mean'], True)} 元、90% 區間 {_n(ci['lo90'], True)} ~ {_n(ci['hi90'], True)}（{ci['clusters']} 個叢集；"
             "不含選擇偏誤，僅看量級）。<br>[事實] 輸家集中在低價股（華新科、南亞科）：價位小、跳動單位占比大，進場掛價（回測口徑買一下 1 檔）的優勢與費用相對更重。"
             "<br>[事實] 帳戶風險：名目部位峰值約淨值 3.3–3.9 倍，盤中最差浮虧 −2.8%～−4.5% 淨值——這是「贏的一週」的數字。</div>")

    p.append("<h3>7. 敏感度（每列只改一個條件，其餘同基準）</h3>")
    p.append(_table(["情境", "5 日損益", "樣本內 4 日", "樣本外 10/5", "成交", "接受", "擋掉", "賺日數", "峰值保證金(萬)"],
                    [(r["情境"], _money(r["損益"]), _money(r["樣本內4日"]), _money(r["樣本外10-05"]), r["成交"], r["接受"], r["擋掉"], r["賺日數"], r["峰值萬"])
                     for r in B["sensitivity"]], num_cols=tuple(range(1, 9))))
    p.append("<div class='leg'>[事實] 本表是 1.2.0 的數字（掃描順序）：排隊保守 qcons 只剩 +77k、延遲 30／60 秒 +177k／+168k、保證金 100 萬／200 萬 +95k／+83k——<b>損益對排隊假設、批准延遲與額度都很敏感</b>，這與第 2F 節同分鐘排序的路徑雜訊是同一件事（額度只放行一部分訊號，放行哪幾筆決定一切）；不設上限 +1.04M（127 筆）。"
             "「只做 ★≥N」「每檔限 1 口」「撤低星單」屬事後挑選，<b>不採用</b>。</div>")

    p.append("<h3>8. 代理人「最常用的」故事版本</h3>")
    gs = "、".join(f"{k}（{v}）" for k, v in list(SP["groups"].items()))
    ns = "、".join(f"{k}（{v}）" for k, v in list(SP["names"].items())[:6])
    st = SP["stars"]
    p.append("<div class='story'><b>「大戶全天一直在買、近 30 分鐘也沒有收手，散戶卻沒有在追（甚至在賣）；價格從 30 分鐘高點回檔。我在買一（回測口徑為買一下 1 檔）排隊接，等大戶的買盤把價格推回去，抱約一小時。」</b>"
             f"<br><br><span class='dim'>具體樣子（名單內 {list_n} 筆訊號）：</span>"
             f"<br>• 場景：{gs}；出現最多的是 {ns}。"
             f"<br>• 星等分布：★0 {st.get('0', 0)}／★1 {st.get('1', 0)}／★2 {st.get('2', 0)}／★3 {st.get('3', 0)}／★4 {st.get('4', 0)}，多數落在 ★2–★3。"
             "<br>• 觸發（09:30 起）：全日大戶主動買佔比不在倒數 30%、近 30 分鐘高點回檔 ≥0.5%、30 分大戶佔比不在倒數 30%、30 分散戶佔比留淨買由小到大 70%、30 分成交額排名 ≥30（星等再看嚴旗標）。"
             f"<br>• 掛法：回測口徑買一下 1 檔（live 自 v1.2.1 掛買一），被動排隊，中位等待 {H['wait_median_min']:.1f} 分鐘；沒成交的掛到收盤、不追價。"
             "<br>• 持有：約 60 分鐘，最後成交價出場；09:30–12:00 進場（12–13 時不出卡）。"
             "<br>• 賺賠分布見第 6 節集中度：<b>賺的集中在少數高價、族群帶頭的標的</b>，賠的集中在低價股。</div>")
    p.append("<h3>9. 這個故事的三維判決與合理性評估</h3>")
    p.append(_table(["問題", "判決", "依據"], [
        ("① 機制合理？", "<span class='dn'>合理</span>", "大戶以主動成交把價格推高後，回檔時承接散戶賣單、同族群落後者補漲，是常見敘事；被動掛單避開買賣價差是合理的成本控制。"),
        ("② 狀態存在？", "<span class='dn'>存在</span>", f"5 日 {all_n} 筆訊號、名單內 {list_n} 筆，集中在名單內的幾個族群。"),
        ("③ Edge？", "<span class='warnv'>樣本內有、樣本外未證實</span>",
         f"樣本內淨 {_n(IO['is_net'], True)}、超額 {_n(BM['is_excess_bps'], True)} bps；樣本外 1 天淨 {_n(IO['oos_net'], True)} 但全靠單筆、超額 {_n(BM['oos_excess_bps'], True)} bps。與 /stories 的「回檔承接 t=0.47」未解決的衝突。"),
        ("④ 資金／委託可行？", "<span class='warnv'>可行但資金是瓶頸</span>", f"{H['skipped_would_fill']} 張會成交的單被保證金擋掉；名目 3–4 倍淨值、最差浮虧 −3%～−4.5%。"),
        ("⑤ 成本？", "<span class='dn'>不是主因</span>", f"費稅合計 {_n(H['cost'])} 元，佔毛利 {100*H['cost']/H['gross']:.0f}%；真正的成本是排隊成交的逆選擇與出場價假設（買一價出場少約 {_n(H['net']-H['net_bid_exit'])} 元）。"),
    ]))
    p.append("<div class='leg'><b>結論</b>：規則在<b>機制、可執行性、成本</b>上合理，作為「自動送單的候選規則」沒有問題；"
             "但<b>「有 edge」尚不能宣稱</b>——證據只有 5 日樣本內（對隨機剔除不顯著、扣掉前三筆只剩一小部分）。合理的下一步是：凍結 1.2.0，從 10/6 起累積 ≥10～20 個真樣本外日，"
             "每天自動用同一版規則評分，達到事先寫好的標準才升版或升級；在那之前不要加碼、不要依這份成績單調參。</div>")

    p.append("<h3>10. 已被否證／要小心的東西</h3>")
    p.append(_table(["項目", "說明"], _FALSIFIED))
    p.append("<h3>11. 檢定紀律（這套規則還沒過的關）</h3>")
    p.append(_table(["檢查", "說明"], _METHOD))
    p.append("<h3>12. 還沒做的事（缺口）</h3>")
    p.append(_table(["待辦", "為什麼重要"], _TODO))
    p.append("<div class='leg'>程式：<code>scripts/research/trader_signal_watch.py</code>（規則）、<code>clone_agent_scan_history.py</code>（歷史重掃）、"
             "<code>clone_agent_day_backtest.py</code>（逐日排隊回測）、<code>clone_agent_rules_backtest.py</code>（版本化端到端回測，輸出本頁數字與逐筆 CSV）；"
             "規格文件 <code>docs/trader-clone-agent-prd.md</code>。指紋 "
             f"<code>{fingerprint(R)}</code>。</div>")
    p.append("</body></html>")
    return "".join(p)
