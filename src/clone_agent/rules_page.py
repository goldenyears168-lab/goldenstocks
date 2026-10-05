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
    ("階段 A", "建議→人工裁決→記錄；裁決紀錄是之後升級（連續 20 日同意率 ≥80%）的唯一依據"),
]

_FALSIFIED = [
    ("「掛低等回檔」", "實測掛得低＝買在大戶出貨的那一秒（逆選擇），所以進場價用「買一下 1 檔」而不是更低的價位"),
    ("急殺後掛下方限價", "改用 bps 距離後樣本外歸零；以檔數定義的「平台」是價位級距的假結構"),
    ("回檔承接（/stories）", "<span class='warnv'>與本代理人故事直接衝突</span>：故事頁判決該標籤 t=0.47、n=67、無 edge。代理人的樣本內數字尚未被樣本外支持，兩者必須並陳"),
]

_METHOD = [
    ("門檻是在這 4 天上反推的", "9/29–10/2 所有成績都是樣本內；每多一個真正的樣本外交易日才算驗證一天"),
    ("名單用 10/2 價格回推 9/29", "名單依 10/2 收盤價帶凍結（華新科 9/30、10/1 實際低於 350 元），屬事後名單；實際上線時名單每日重評"),
    ("毛 vs 淨", "bps 欄為毛報酬；「元」欄已扣手續費＋期交稅"),
    ("手續費已確認", "jack 2026-10-05 確認實際費率就是 176 元／口／邊（與牌價相同）；實測 0→300 元只差約 2 萬，不是關鍵"),
    ("排隊模擬", "qcons／qpro／qopt 是對「前面排多少量、撤單多少」的三種假設；同一檔先後兩張單在模擬裡各自獨立排隊（真實上第二張會排在第一張後面），只有真實掛單才能校準"),
    ("結果對路徑敏感", "人工批准延遲 5→30 秒損益 +481k→+507k、60 秒 +419k：時間差一點，保證金佔用順序就變，損益約有 ±15% 的雜訊"),
    ("樣本只有 39 筆", "有效樣本 &lt; 39（同一檔連續出卡不獨立）；叢集重抽 90% 區間只反映抽樣，不含「門檻是看過資料才定」的選擇偏誤"),
    ("回測名單口徑與 live 差一個價格帶", "回測 headline（+481k）只套凍結名單；live watcher 另加卡片掛價價格帶（350–2000）。同一份歷史改套價格帶是 +467k（敏感度表「名單改用逐筆價格帶」），差約 1.4 萬，主要是華新科 9/30、10/1 低於 350 元的卡"),
    ("帳務逐日重置", "回測每天重算保證金，無跨日持倉；隔夜風險未納入"),
]

_TODO = [
    ("watcher 的名單／價格帶落地驗證", "2026-10-05 起 live watcher 只對凍結名單出卡、卡片掛價超出價格帶不寫入、候選檔帶規則版本；歷史重掃 10/5 與回測逐筆一致（25/25），但 live 實際出卡行為要等下一個交易日才驗得到"),
    ("成交資料驗證 qpro", "把真實掛單的成交與否與等待時間，拿來校準排隊模擬"),
    ("book_time 與 trade_time 對齊", "委託簿與逐筆時間戳是否同步尚未驗證"),
    ("20 日紙上帳", "累積真成交率與 ≥80% 同意率，才能談升級；v1.0.0 凍結後的每一天都是真樣本外"),
    ("台達電／日月光加入收集", "只能收盤後做，並驗證啟動 banner"),
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
        ("2 寬條件（出卡）", f"(近 30 分鐘 OR 全日 散戶賣給大戶) ∧ 30 分成交額排名 ≥{s['amt_pct_min']} ∧ 非 {s['skip_hours'][0]}–{s['skip_hours'][1]} 時 ∧ 距當日高 ≤ {s['pullback_max']}%",
         "<code>signal.*</code>", "已實作"),
        ("3 冷卻", f"同一檔 {s['recheck_min']} 分鐘內不重複出卡", f"recheck_min={s['recheck_min']}", "已實作"),
        ("4 星等", f"四個嚴旗標各 1 顆星：①30 分∧全日同時成立 ②成交額排名 ≥{s['amt_pct_strict']} ③距高 ≤ {s['pullback_strict']}% ④共動夥伴內當日漲幅排後半（≤{s['chg_cm_strict']}）。星等只排序，不當篩選",
         "<code>signal.*_strict</code>", "已實作"),
        ("5 進場價", f"{R['entry']['price']}，{R['entry']['style']}排隊；不追價、不吃賣一，{R['entry']['lots']} 口／張卡", "lim = bid − 1 tick", "已實作"),
        ("6 資金", f"保證金上限 <b>{c['margin_cap']:,}</b>（淨值 {c['equity']:,} 的 {100*c['margin_cap']/c['equity']:.1f}%）；<b>持倉與未成交的新倉委託都佔用</b>；超過就整張卡放棄",
         "每口 = 價 × 2000 × 保證金率", "<span class='warnv'>回測模擬；watcher 只標註</span>"),
        ("7 委託順序", f"先到先得（{o['policy']}）；排隊模型 <b>{o['queue_model']}</b>（撤單依前後比例分配）；人工批准延遲 {o['submit_lag_s']} 秒；沒成交掛到收盤",
         "撤低星換高星、每檔限 1 口兩種變體皆未勝出", "研究"),
        ("8 出場", f"成交後持有 {e['hold_min']} 分鐘、以{e['price']}出場，收盤 {e['session_end']} 截斷（PRD 另定 13:25 強制平倉，回測證實兩者結果相同）",
         f"hold={e['hold_min']}min", "<span class='warnv'>僅研究</span>"),
        ("9 成本", f"手續費 {k['fee_per_lot_per_side']} 元／口／邊（{k['fee_basis']}）＋期交稅 {k['futures_tax_rate_per_side']*100:.3f}%／邊", "fee、tax", "回測已計"),
        ("10 人工批准", "階段 A＝只建議：產生確認卡，由 jack 手動下單；代理人沒有任何下單能力", "升級 C 需連續 20 日 ≥80%", "已實作"),
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
         "<br><b>v1.0.0 之後的每個交易日都是真樣本外</b>：升版前不得因為某天表現差而改參數。</div>",
         "<div class='leg'>代理人是一支<b>只建議、不下單</b>的程式（階段 A）。它每分鐘掃描戰場個股期貨，"
         "找「<b>散戶賣給大戶 ∧ 價格自當日高回檔 ∧ 成交活躍</b>」的標的，在<b>買一下 1 檔</b>排隊承接，預期持有約一小時。</div>"]

    p.append("<h3>1. 硬邊界</h3>")
    p.append(_table(["邊界", "內容"], _BOUNDARY))
    p.append(f"<h3>2. 規則流水線（{lab}）</h3>")
    p.append(_table(["步驟", "規則", "參數／位置", "狀態"], _pipeline(R)))
    p.append("<div class='leg'><b>狀態欄讀法</b>：「已實作」＝live 程式現在就這樣做；「僅回測／僅研究」＝規則只在回測腳本裡成立，live 還沒有。目前只有排隊成交模擬、保證金上限與持有 60 分鐘出場仍是回測／研究，見第 12 節。</div>")

    if not B:
        p.append("<div class='leg warnv'>找不到此版本的回測結果檔，請執行 <code>scripts/research/clone_agent_rules_backtest.py</code>。</div></body></html>")
        return "".join(p)

    H, IO, CC, BM, RK, SP = B["headline"], B["is_oos"], B["concentration"], B["benchmark"], B["risk"], B["signal_profile"]
    all_n, list_n = B["signals_all"], B["signals_in_list"]

    p.append(f"<h3>3. 回測成績單 —— 全部代理人選股、照規則全送（9/29–10/5，{lab}）</h3>")
    p.append(f"<div class='leg'>設定：代理人 5 日共 <b>{all_n}</b> 筆寬條件訊號，名單內 <b>{list_n}</b> 筆全部照送；保證金上限 {B['cap']:,}、"
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

    p.append("<h3>5. 樣本內 vs 樣本外，以及扣掉「大家一起漲」之後</h3>")
    p.append(_table(["口徑", "樣本內 9/29–10/2（4 天）", "樣本外 10/5（1 天）"],
                    [("成交筆數", IO["is_n"], IO["oos_n"]),
                     ("淨損益", _money(IO["is_net"]), _money(IO["oos_net"])),
                     ("買一價出場淨損益", "—", _money(IO["oos_net_bid"])),
                     ("勝率", f"{100*IO['is_win']:.0f}%", f"{100*IO['oos_win']:.0f}%"),
                     ("毛報酬 平均／中位 (bps)", f"{IO['is_mean_bps']:.0f} / {IO['is_median_bps']:.0f}", f"{IO['oos_mean_bps']:.0f} / {IO['oos_median_bps']:.0f}"),
                     ("相對同窗名單等權平均的超額 (bps)", _n(BM["is_excess_bps"], True), _n(BM["oos_excess_bps"], True))],
                    num_cols=(1, 2)))
    p.append(f"<div class='leg'><b>判讀</b>：[事實] 樣本外那一天平均毛報酬 {IO['oos_mean_bps']:.0f} bps、中位 {IO['oos_median_bps']:.0f} bps，勝率 {100*IO['oos_win']:.0f}%；"
             f"它的 {_n(IO['oos_net'], True)} 元淨利<b>全靠 {CC['top1']} 一筆 {_n(CC['top1_net'], True)}</b>，扣掉那筆是 {_n(IO['oos_net']-CC['top1_net'], True)}（元報酬被高價股的單筆放大，bps 與元不同向）。"
             f"<br>[事實] 這週名單內標的在同窗口平均漲 {BM['mean_bench_bps']:.0f} bps（多頭週），所以「買了就賺」本身就有 beta；扣掉後樣本內超額 {_n(BM['is_excess_bps'], True)} bps、樣本外 {_n(BM['oos_excess_bps'], True)} bps。"
             "<br>[推論] 門檻在樣本內反推 → 樣本內必然高估；一天樣本外既不能證實也不能否證。</div>")

    p.append("<h3>6. 集中度與風險</h3>")
    top = sorted(CC["by_stock"].items(), key=lambda kv: -kv[1]["net"])
    p.append(_table(["標的", "淨損益", "筆數"], [(k, _money(v["net"]), v["n"]) for k, v in top], num_cols=(1, 2)))
    ci = B["cluster_ci"]
    p.append(f"<div class='leg'>[事實] 前 3 大單筆合計 {_n(CC['top3_net'], True)}（佔 {100*CC['top3_net']/H['net']:.0f}%），扣掉後 {_n(CC['ex_top3_net'], True)}。"
             f"依（日×標的）叢集重抽，每筆平均 {_n(ci['mean'], True)} 元、90% 區間 {_n(ci['lo90'], True)} ~ {_n(ci['hi90'], True)}（{ci['clusters']} 個叢集；"
             "不含選擇偏誤，僅看量級）。<br>[事實] 輸家集中在低價股（華新科、南亞科）：價位小、跳動單位占比大，買一下 1 檔的進場優勢與費用相對更重。"
             "<br>[事實] 帳戶風險：名目部位峰值約淨值 3.3–3.9 倍，盤中最差浮虧 −2.8%～−4.5% 淨值——這是「贏的一週」的數字。</div>")

    p.append("<h3>7. 敏感度（每列只改一個條件，其餘同基準）</h3>")
    p.append(_table(["情境", "5 日損益", "樣本內 4 日", "樣本外 10/5", "成交", "接受", "擋掉", "賺日數", "峰值保證金(萬)"],
                    [(r["情境"], _money(r["損益"]), _money(r["樣本內4日"]), _money(r["樣本外10-05"]), r["成交"], r["接受"], r["擋掉"], r["賺日數"], r["峰值萬"])
                     for r in B["sensitivity"]], num_cols=tuple(range(1, 9))))
    p.append("<div class='leg'>[事實] 對排隊假設、手續費、批准延遲、出場價都不翻負（+419k ~ +507k）；對<b>保證金額度</b>最敏感（100 萬 +291k、200 萬 +485k、不設上限 +1.03M，但不設上限時樣本外只剩 +15k）；"
             "「只做 ★≥N」「每檔限 1 口」樣本內與樣本外的排序不一致（★≥3 樣本內較差、樣本外較好；每檔限 1 口樣本內少約 20 萬、樣本外多約 5 萬），屬事後挑選，<b>不採用</b>；不套名單時樣本外 −130k，名單過濾看起來有用，但名單本身是看過 10/2 才定的。</div>")

    p.append("<h3>8. 代理人「最常用的」故事版本</h3>")
    gs = "、".join(f"{k}（{v}）" for k, v in list(SP["groups"].items()))
    ns = "、".join(f"{k}（{v}）" for k, v in list(SP["names"].items())[:6])
    st = SP["stars"]
    p.append("<div class='story'><b>「大戶在拉高後回檔時接走散戶的賣單；同族群裡漲得比別人少的那一檔，會被補漲。我在買一下 1 檔排隊接，等大戶的買盤把價格推回去，抱約一小時。」</b>"
             f"<br><br><span class='dim'>具體樣子（名單內 {list_n} 筆訊號）：</span>"
             f"<br>• 場景：{gs}；出現最多的是 {ns}。"
             f"<br>• 星等分布：★0 {st.get('0', 0)}／★1 {st.get('1', 0)}／★2 {st.get('2', 0)}／★3 {st.get('3', 0)}／★4 {st.get('4', 0)}，多數落在 ★2–★3。"
             "<br>• 觸發：自當日高回檔 ≥0.5%、30 分成交額排名 ≥30、近 30 分鐘或全日散戶賣給大戶（寬條件；星等再看嚴旗標）。"
             f"<br>• 掛法：買一下 1 檔，被動排隊，中位等待 {H['wait_median_min']:.1f} 分鐘；沒成交的掛到收盤、不追價。"
             "<br>• 持有：約 60 分鐘，最後成交價出場；集中在 09:00–12:00 進場（12–13 時不出卡）。"
             "<br>• 會賺的版本：<b>穩懋、景碩、南電、環球晶</b>這類高價、族群帶頭的標的；會賠的版本：低價股（華新科、南亞科）上被動排隊成交後又沒有上漲。</div>")
    p.append("<h3>9. 這個故事的三維判決與合理性評估</h3>")
    p.append(_table(["問題", "判決", "依據"], [
        ("① 機制合理？", "<span class='dn'>合理</span>", "大戶以主動成交把價格推高後，回檔時承接散戶賣單、同族群落後者補漲，是常見敘事；被動掛單避開買賣價差是合理的成本控制。"),
        ("② 狀態存在？", "<span class='dn'>存在</span>", f"5 日 {all_n} 筆寬條件訊號、名單內 {list_n} 筆（每天 22–33 筆），集中在 7 個族群 12 檔。"),
        ("③ Edge？", "<span class='warnv'>樣本內有、樣本外未證實</span>",
         f"樣本內淨 {_n(IO['is_net'], True)}、超額 {_n(BM['is_excess_bps'], True)} bps；樣本外 1 天淨 {_n(IO['oos_net'], True)} 但全靠單筆、超額 {_n(BM['oos_excess_bps'], True)} bps。與 /stories 的「回檔承接 t=0.47」未解決的衝突。"),
        ("④ 資金／委託可行？", "<span class='warnv'>可行但資金是瓶頸</span>", f"{H['skipped_would_fill']} 張會成交的單被保證金擋掉；名目 3–4 倍淨值、最差浮虧 −3%～−4.5%。"),
        ("⑤ 成本？", "<span class='dn'>不是主因</span>", f"費稅合計 {_n(H['cost'])} 元，佔毛利 {100*H['cost']/H['gross']:.0f}%；真正的成本是排隊成交的逆選擇與出場價假設（買一價出場少約 {_n(H['net']-H['net_bid_exit'])} 元）。"),
    ]))
    p.append("<div class='leg'><b>結論</b>：規則在<b>機制、可執行性、成本</b>上合理，作為「階段 A 建議卡」沒有問題；"
             "但<b>「有 edge」尚不能宣稱</b>——證據只有樣本內與單日樣本外。合理的下一步是：凍結 v1.0.0，累積 ≥10～20 個真樣本外日，"
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
