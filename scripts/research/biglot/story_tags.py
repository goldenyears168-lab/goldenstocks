"""主表「故事」欄 —— 即時判斷每檔現在符合哪個大戶/散戶互動故事的**狀態**。

## 這一欄是「狀態描述」，不是「訊號」（2026-10-05 jack 定案）

今天九個故事經多 agent 檢定後,判決要拆成三個問題:①機制合理嗎 ②狀態存在嗎 ③有 edge 嗎。
結果:機制不合理的只有兩個(故事三、故事六的「散戶不在場」那一半),狀態不存在的只有一個
(故事五,四天沒有空頭 regime),**其餘六個都是「機制合理、狀態存在、只是沒有 edge 或 edge 反向」**。

「故事對」和「跟著做能賺」是兩回事。這一欄只回答第②題 —— 現在這檔長什麼樣 —— 給人工
判斷當素材。它**不給分數、不進淨分**,跟「標記」欄同性質。反向 edge 的故事也照標,因為
「這種時候別買」同樣是有用的描述。

## 兩條硬規則
1. **只用儀表板看得到的欄位**(jack:「全部拿 html 自製儀表板的欄位當素材」)。
   每個標籤的規則都寫在 /stories 頁,可逐條核對。
2. **門檻一律用「同分鐘橫斷面排名」,不用固定數字**。今天抓到的坑:流量欄有時段漂移
   (`ret_30m` 均值 09 時 +1.71 → 13 時 −3.28),任何池化固定門檻都會變成時段指示器。
   同分鐘內互比天生時段中性。

依賴規則同 biglot 其他模組:只 `import biglot_dashboard` 做屬性存取,不 from-import 名稱。
"""
from __future__ import annotations

import html as _html

import biglot_dashboard

# ── 門檻(橫斷面百分位,0=最低 100=最高)。集中放一起方便核對與調整。
TH = {
    "nobig_absday_pct": 30,     # 無大戶:|全日大戶淨額| 落在最低 30%
    "nobig_rvol": 0.7,          # 無大戶:全日量能倍數 < 0.7
    "retail_arrive_pct": 80,    # 散戶到場:散戶參與Δ30分 在最高 20%(且 >0)
    "crash_pct": 15,            # 急殺:近5分漲跌 在最低 15%(且 <0)
    "lock_gross_pct": 25,       # 籌碼鎖定:散戶參與度(散買%+散賣%) 在最低 25%
    "lock_rvol": 1.0,           # 籌碼鎖定:全日量能 ≥1(否則是死股,不是鎖定)
}

# ── 標籤定義:key → (顯示文字, 顏色class, 一句話規則, 來源故事, edge 狀態)
#    顏色只用儀表板既有語意:up=紅(台股慣例,這裡當警示)/dn=綠/warnv=琥珀/dim=灰/sigup藍不用
TAGS = {
    "nobig":  ("無大戶", "up",    "|全日大戶淨額| 橫斷面最低30% ∧ 全日量能<0.7x",
               "故事十「沒人在場」", "負向濾網:大單筆數≤2 勝率36.9% t=−7.1、大戶參與度後30% t=−12.5,4/4 天 —— 今天最硬的發現。**紅=別進**"),
    "pullbk": ("回檔·大戶接", "",   "30分大戶淨額>0 ∧ 30分散戶淨額<0 ∧ 近30分漲跌<0",
               "故事一/九「回檔承接」", "機制合理、狀態存在;去重後 n=67 趟 t=0.47,**沒有 edge**(原先 n=2361 是列數灌大 35 倍)"),
    "arrive_push": ("散戶到場·大戶仍推", "", "散戶參與Δ30分 橫斷面最高20%(>0) ∧ 30分大戶淨額>0",
               "故事十一(中場)", "散戶參與度暴增後報酬反而**更好**(五分位單調升 0.315→0.413),不是出貨訊號。這是大戶還在推的中場"),
    "arrive_sell": ("散戶到場·大戶轉賣", "warnv", "散戶參與Δ30分 橫斷面最高20%(>0) ∧ 30分大戶淨額<0",
               "故事十一(可能的出貨)", "**唯一尚未檢定的出場候選**:散戶已在場 ∧ 大戶開始賣。今天沒測到這個交集,標出來是為了累積樣本"),
    "crash_sell": ("急殺·大戶砸", "dn", "近5分漲跌 橫斷面最低15%(<0) ∧ 5分大戶淨額<0",
               "故事十二", "急殺期間 P(大戶淨買>0) 只有 7.6%、大戶排名掉到後 1/4 (p=8.7e-43) —— **92% 的急殺是大戶自己在賣,不是洗盤**"),
    "crash_absorb": ("急殺·有人接", "warnv", "近5分漲跌 橫斷面最低15%(<0) ∧ 5分大戶淨額>0",
               "故事十二(稀有的 7.6%)", "這才是真正的「下面有人接」。稀有,未單獨檢定 edge;主動方符號口徑下有量測偏誤(下跌時主動賣佔優)"),
    "lock":   ("籌碼鎖定", "",    "散買參與%+散賣參與% 橫斷面最低25% ∧ 全日大戶淨額>0 ∧ 全日量能≥1x",
               "故事六 Q1", "狀態存在(約 6% 時點)、方向一致但 t≤1.12 低於 MDE。**必須配活動量**:散戶參與度最低那格是沒人交易的死股(勝率 21.6%),不是鎖定"),
    "defend": ("護盤·逆強", "",   "相對大盤欄=「逆強」(市場30分跌、它漲)",
               "故事一進階「護盤式卡位」", "jack 2026-10-05 版:大盤跌它不跌=大戶硬撐。條件式假說,agent 檢定中(護盤 + 下跌時量縮/爆量)"),
}


def _pct_rank(vals: dict) -> dict:
    """同分鐘橫斷面百分位(0~100,<v 的比例)。None 不計。少於 5 檔回空(排名無意義)。"""
    xs = [v for v in vals.values() if v is not None]
    if len(xs) < 5:
        return {}
    n = len(xs)
    # ★ 中位秩(並列取平均),不是嚴格 <。用嚴格 < 時,並列在最低值的全部得 0 分位 ——
    #   盤前散買/散賣參與全為 0、整池並列,會把每一檔都標成「籌碼鎖定」(合成測試抓到的)。
    #   中位秩下全池並列 → 每檔 50 分位 → 不觸發任何「最低X%」條件。
    return {k: 100.0 * (sum(1 for x in xs if x < v) + 0.5 * sum(1 for x in xs if x == v) - 0.5) / n
            for k, v in vals.items() if v is not None}


def story_tags(rows: list) -> dict:
    """核心列(不含參考列)→ {sid: [tag_key, ...]}。橫斷面排名只在這一批內算。"""
    if not rows:
        return {}
    g = lambda r, k: r.get(k)  # noqa: E731
    absday = _pct_rank({r["sid"]: (abs(g(r, "bigday")) if g(r, "bigday") is not None else None) for r in rows})
    dsh = _pct_rank({r["sid"]: g(r, "dsh30_r") for r in rows})
    w5 = _pct_rank({r["sid"]: g(r, "w_ret_r") for r in rows})
    gross = _pct_rank({r["sid"]: ((g(r, "rbuy30_r") or 0) + (g(r, "rsell30_r") or 0))
                       if (g(r, "rbuy30_r") is not None or g(r, "rsell30_r") is not None) else None
                       for r in rows})
    out = {}
    for r in rows:
        sid = r["sid"]; t = []
        big30, ret30, r30 = g(r, "big30_r"), g(r, "retn30_r"), g(r, "r30_r")
        big5, w, bigday = g(r, "big5_r"), g(r, "w_ret_r"), g(r, "bigday")
        rvd, ctx = g(r, "rvol_day"), (g(r, "mkt_ctx") or "")
        # 無大戶(紅燈)
        if sid in absday and absday[sid] <= TH["nobig_absday_pct"] and rvd is not None and rvd < TH["nobig_rvol"]:
            t.append("nobig")
        # 回檔·大戶接
        if big30 is not None and ret30 is not None and r30 is not None and big30 > 0 and ret30 < 0 and r30 < 0:
            t.append("pullbk")
        # 散戶到場(拆大戶仍推/轉賣)
        d = g(r, "dsh30_r")
        if sid in dsh and dsh[sid] >= TH["retail_arrive_pct"] and d is not None and d > 0 and big30 is not None:
            t.append("arrive_push" if big30 > 0 else "arrive_sell")
        # 急殺(拆大戶砸/有人接)
        if sid in w5 and w5[sid] <= TH["crash_pct"] and w is not None and w < 0 and big5 is not None:
            t.append("crash_absorb" if big5 > 0 else "crash_sell")
        # 籌碼鎖定(必須配活動量)
        if sid in gross and gross[sid] <= TH["lock_gross_pct"] and bigday is not None and bigday > 0 \
                and rvd is not None and rvd >= TH["lock_rvol"]:
            t.append("lock")
        # 護盤·逆強(直接讀相對大盤欄)
        if "逆強" in ctx:
            t.append("defend")
        out[sid] = t
    return out


def _story_td(sid: str, stories: dict) -> str:
    """主表格子。參考列/無標籤 → 灰色破折號。多標籤並列,各帶規則 tooltip。"""
    keys = stories.get(sid)
    if not keys:
        return "<td class='dim' data-sort='0'>—</td>"
    chips = []
    for k in keys:
        txt, cls, rule, src, _ = TAGS[k]
        tip = _html.escape(f"{src}:{rule}", quote=True)
        chips.append(f"<span class='{cls}' title=\"{tip}\" style='white-space:nowrap'>{txt}</span>")
    return (f"<td style='text-align:left;font-size:11px' data-sort='{len(keys)}'>"
            + " · ".join(chips) + "</td>")


# ────────────────────────────────────────────────────────────── /stories 說明頁

_VERDICTS = [
    # (故事, 機制合理?, 狀態存在?, edge?, 關鍵數字)
    ("一 回檔承接", "合理", "存在(67趟)", "<b class='dn'>無</b>", "去重後 t=0.47、超額勝率 41.2% < 隨機 46.5%、洗牌不過 p95"),
    ("一b 護盤式卡位(大盤跌它不跌)", "合理", "<b class='warnv'>環境不存在</b>", "<b class='dn'>無</b>", "4天大盤最大回撤 −0.95%,沒有「跌500點」;劑量反應相反:z≤−1 +0.179%→z≤−1.5 +0.012%;大盤平盤時相對強度反而最大 +0.306% → 只是已知日內反轉,不是護盤;「鐵板」需五檔不可觀測"),
    ("一c 洗盤vs出貨(下跌量縮/爆量)", "合理", "存在", "<b class='dn'>無(只有排除側)</b>", "量縮−放量|自身跌 +0.016%(t 0.10);急凍−爆量在「上漲」分鐘更強(+0.243%、4/4),跟敘述相反;唯一大t格 spread 全來自放量腳 −0.357% → 「下跌有量」預測下跌(別買),「下跌沒量」不預測上漲;洗盤格 +0.382% 是去重選點造出來的(同分鐘口徑 −0.036%);融資/股東/新聞不可觀測"),
    ("二 族群外溢", "合理", "存在", "<b class='dn'>反向</b>", "族群強∧個股落後 −0.118%;與故事一交集 −0.498%"),
    ("三 卡位後洗盤", "<b class='dn'>第一環不成立</b>", "完整簽名 n=0~3", "測不到", "急殺時散戶沒動 p=0.43;大戶排名掉到後1/4 p=8.7e-43 → 急殺是大戶在賣"),
    ("四 低量先吃", "合理(散戶5日後確實到場)", "存在", "<b class='dn'>無</b>", "repo 三次否證同向;盤中版低量三口徑全負、勝率 38–42%"),
    ("五 散戶投降", "合理(regime 邊界對)", "<b class='warnv'>不存在</b>", "測不到", "四天全多頭:斷頭潮 0 例、當日收跌∧大戶買 0 例;翻案需空頭樣本"),
    ("六 接力(jack版)", "半對:「散戶不在場」<b class='dn'>錯</b>", "存在(鎖定 6%)", "<b class='dn'>無</b>", "帳面 +0.736% 是大單筆數代理(固定筆數後 t=−0.04);接力時散戶參與度 25.2% > 單邊 21.6%"),
    ("七 震盪磨耗", "合理", "存在", "<b class='dn'>反向</b>", "效率比最低25% −0.099%;最高25% 才正"),
    ("八 假跌破", "合理", "存在(42%列)", "<b class='dn'>反向</b>", "破開盤後站回 −0.348%;「今天沒破開盤」才正"),
    ("九 乾淨上升+回檔", "合理", "存在(23趟)", "<b class='warnv'>資料不足</b>", "剔 2 筆歸零(t=0.13);`er` 是「剛開盤」代理(bars≤10 時 76% 過門檻)"),
]

_UNDETECTABLE = [
    ("三 卡位後洗盤", "要「前一日大戶已買夠」", "面板 big_2d 是「昨天+今天至今」的污染欄;需新欄 `前日大戶淨額`"),
    ("四 低量沉澱", "月級", "日線資料,不是盤中欄"),
    ("五 散戶投降", "要空頭 regime + 融資斷頭", "日線 20MA + stock_margin_daily;融資下降≠斷頭,須 ∧ 在20MA下"),
    ("七 震盪", "要效率比", "ST 要加路徑追蹤;且護欄要改成 bars≥30(不是 path_pct)"),
    ("八 假跌破", "要「今天曾跌破開盤」", "ST 要追蹤當日最低價"),
    ("二 族群帶動", "要族群整體強度", "儀表板有股價共動欄但沒有族群聚合值"),
]

_METHOD = [
    ("去重:每檔每日只取第一個觸發", "面板每分鐘一列,同一波行情重複計入幾十次。n=363 去重後 23;n=2361 去重後 67。所有列級 p 值作廢"),
    ("日聚類 t", "用四天的日均值算 SE,不用列級 t"),
    ("超額勝率", "四天全多頭,原始勝率 56% 的超額勝率只有 43%,低於隨機 46.5%。要問「贏不贏同分鐘 23 檔平均」"),
    ("集中度:最大 sid-day 佔比 + 剔最強單趟", "洗牌 p99 擋隨機不擋集中。一格 n=71 勝率 84.5% 過 p99,59% 來自同一檔,剔掉就不過"),
    ("去 day×sid 均值", "唯一能分開「選股」與「選時機」:成交額門檻 +0.685% 去後剩 +0.021%(純選股);故事九去後仍 +1.174"),
    ("時段漂移:流量欄也有", "ret_30m 均值 09時 +1.71 → 13時 −3.28。池化固定門檻=時段指示器。本欄全用同分鐘橫斷面排名"),
    ("★ 去重口徑下洗牌 p99 失效", "99 格有 34 格過 p99(虛無下該~1 格)。洗牌只打亂「同分鐘是哪幾檔」,去重統計量卻由「挑到哪一分鐘」決定。去重口徑看選股後α與日聚類t,不看 p99;同一條件去重 t=6.91、同分鐘 long-short t=1.09 —— 後者才是對「同分鐘排名型」規則的正確口徑。徵兆:規則與反規則同號(res15 前20% +0.060、後20% +0.126)"),
    ("劑量反應比 p 值先看", "任何「越極端越強」的敘述先掃門檻單調性:護盤式卡位 z≤−1 +0.179%、z≤−1.5 +0.012%、平盤 +0.306% → 條件化方向反了,不必再看顯著性。4 日叢集臨界 t=3.18 不是 2;MDE 0.44–1.55%/趟,headline 點估計全低於此"),
    ("「量低」是兩個變數", "橫斷面低量(比別檔小)−0.273% 1/4日 vs 自身同時段量縮(比自己平常安靜)+0.141% 4/4日,ρ 僅 0.31。符號相反、都對;報結論必寫是哪一個"),
    ("大戶欄解釋一切、散戶欄不說話", "兩個 agent 獨立收斂:5×5 格跨欄變化遠大於跨列;去重對照散戶極端賣增量 +0.003%。散戶行為≈價格的函數,控住價格後無剩餘資訊"),
]


def render_stories() -> str:
    css = ("body{background:#0d1117;color:#c9d1d9;font:13px/1.7 -apple-system,'PingFang TC',sans-serif;margin:0;padding:14px 16px 40px}"
           "h2{font-size:17px;margin:2px 0 4px}h3{font-size:14px;color:#79c0ff;margin:18px 0 4px;border-bottom:1px solid #30363d;padding-bottom:3px}"
           "a{color:#79c0ff}.meta{color:#8b949e;font-size:12px;margin-bottom:10px}"
           "table{border-collapse:collapse;width:100%;max-width:1100px;margin:2px 0;font-size:12px}"
           "th{background:#161b22;color:#8b949e;text-align:left;padding:5px 8px;border-bottom:1px solid #30363d}"
           "td{border-bottom:1px solid #21262d;padding:5px 8px;vertical-align:top}"
           "td.c{color:#e6edf3;font-weight:600;white-space:nowrap}"
           ".up{color:#ff7b72}.dn{color:#3fb950}.warnv{color:#e3b341}.dim{color:#484f58}"
           ".leg{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;margin:10px 0;font-size:12px;max-width:1100px}"
           "code{background:#161b22;padding:1px 5px;border-radius:3px;font-size:11px;color:#adbac7}")
    p = [f"<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'><title>故事欄說明</title><style>{css}</style></head><body>",
         "<div class='meta'><a href='/'>← 大戶儀表板</a> ｜ <b>主表「故事」欄：規則核對頁</b> ｜ 2026-10-05</div>",
         "<h2>這一欄是「狀態描述」，不是「訊號」</h2>",
         "<div class='leg'>今天九個故事經多 agent 檢定後，判決要拆成三個問題：<b>①機制合理嗎 ②狀態存在嗎 ③有 edge 嗎</b>。"
         "結果：機制不合理的只有兩個，狀態不存在的只有一個，<b>其餘六個都是「機制合理、狀態存在、只是沒有 edge 或 edge 反向」</b>。"
         "「故事對」和「跟著做能賺」是兩回事 —— 大部分故事是對的，市場確實這樣運作，但那個資訊已經被價格反映掉了。"
         "<br>這一欄只回答第②題：<b>現在這檔長什麼樣</b>，給人工判斷當素材。不給分數、不進淨分。反向 edge 的故事也照標，因為「這種時候別買」同樣有用。"
         "<br>唯一帶紅色的是「無大戶」—— 它是唯一 4/4 天站得住的發現（負向濾網），紅=別進。</div>"]
    # 標籤規則表
    p.append("<h3>主表標籤 → 規則（全部是儀表板現有欄位）</h3>")
    p.append("<table><tr><th>標籤</th><th>規則（同分鐘橫斷面排名，天生時段中性）</th><th>來源</th><th>資料怎麼說</th></tr>")
    for k, (txt, cls, rule, src, note) in TAGS.items():
        p.append(f"<tr><td class='c {cls}'>{txt}</td><td>{_html.escape(rule)}</td><td>{_html.escape(src)}</td><td>{note.replace('**', '')}</td></tr>")
    p.append("</table>")
    p.append("<div class='leg'>門檻（可調，集中在 <code>biglot/story_tags.py:TH</code>）：" +
             " · ".join(f"<code>{k}={v}</code>" for k, v in TH.items()) + "</div>")
    # 三維判決
    p.append("<h3>九個故事的三維判決（2026-10-05 多 agent 檢定，4 天 × 23 檔戰場、去重口徑）</h3>")
    p.append("<table><tr><th>故事</th><th>① 機制合理？</th><th>② 狀態存在？</th><th>③ Edge？</th><th>關鍵數字</th></tr>")
    for s, m, st, e, n in _VERDICTS:
        p.append(f"<tr><td class='c'>{s}</td><td>{m}</td><td>{st}</td><td>{e}</td><td>{n}</td></tr>")
    p.append("</table>")
    # 判不了的
    p.append("<h3>即時判不了的故事（需要日級／週級／序列資料）</h3>")
    p.append("<table><tr><th>故事</th><th>為什麼判不了</th><th>需要什麼</th></tr>")
    for s, why, need in _UNDETECTABLE:
        p.append(f"<tr><td class='c'>{s}</td><td>{why}</td><td>{need}</td></tr>")
    p.append("</table>")
    # 方法
    p.append("<h3>檢定紀律（今天踩出來的，往後所有盤中規則都要過）</h3>")
    p.append("<table><tr><th>檢查</th><th>為什麼</th></tr>")
    for a, b in _METHOD:
        p.append(f"<tr><td class='c'>{a}</td><td>{b}</td></tr>")
    p.append("</table>")
    p.append("<div class='leg'><b>兩條硬限制</b>：全部結論基於 2026-09-29→10-02 四天、單一多頭 regime、23 檔。去重後真實樣本 23～67 趟。"
             "任何「該進」的訊號在這批資料上都已榨乾；可信的只有「別進」的負向濾網與狀態描述。"
             "<br>研究腳本：<code>scripts/research/story{3,4,5,6,1b}_*.py</code>、<code>verify_story19_*.py</code>；結果 csv 在 <code>data/scratch/story*_*.csv</code>。</div>")
    p.append("</body></html>")
    return "".join(p)
