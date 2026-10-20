"""biglot dashboard 重構 Phase 1：從 biglot_dashboard.py 搬出的葉節點 HTML 片段產生器。

搬移依據同 biglot/utils.py——見 scripts/research/biglot_phase0/dep_graph.py
的依賴分析，兩者都是零全域依賴、零呼叫其他頂層函式的真葉節點。純搬移，函式
本體逐字保留，不改行為。
"""
from __future__ import annotations


def _book_table(bk):
    if not bk:
        return "<div class='meta'>無五檔資料</div>"
    bp, bq = bk.get("bp") or [], bk.get("bq") or []
    ap, aq = bk.get("ap") or [], bk.get("aq") or []
    rows = []
    for i in range(5):                                   # 賣五~賣一 由上而下
        j = 4 - i
        ax = f"{ap[j]:g}" if j < len(ap) and ap[j] else "—"
        aqx = f"{aq[j]:g}" if j < len(aq) and aq[j] else ""
        rows.append(f"<tr><td class='dim'>賣{j+1}</td><td class='dn'>{ax}</td><td>{aqx}</td></tr>")
    for i in range(5):                                   # 買一~買五
        bx = f"{bp[i]:g}" if i < len(bp) and bp[i] else "—"
        bqx = f"{bq[i]:g}" if i < len(bq) and bq[i] else ""
        rows.append(f"<tr><td class='dim'>買{i+1}</td><td class='up'>{bx}</td><td>{bqx}</td></tr>")
    tsline = f"<div class='meta'>五檔快照 {bk.get('t','')} · 委買/委賣量單位:張</div>"
    return (tsline + "<table class='book'><thead><tr><th></th><th>價</th><th>量(張)</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>")


def _mark_eod_fallback(cell_html: str, asof: str) -> str:
    """把一個已經組好的 `<td>...</td>` 格子標記成「這是上一個交易日(asof)的全日定格
    數字,不是今天的」——2026-10-03 jack 交辦:全日大戶/全日散戶/權證全日/大戶-散戶/
    大戶佔比/散戶背離這組欄位,在非交易日或收盤後該檔今天確實整天零成交時(呼叫端已
    判斷過,見 render_main.py 的 `_eod_fallback`),與其顯示一片空白,不如回退顯示
    上一個已知交易日的最終數字,但必須清楚標註是哪一天的,不能讓它看起來像今天的
    即時數字——跟 `_load_holder_big800` 的「較上週」標註同一個設計理由。
    純字串插入,不改動被包裝的格子本身(`_bigret_diff_td`/`_wrt_td`/行內組字串)
    的渲染邏輯,所以每個呼叫端各自要不要套用這層標記互不影響。"""
    if not cell_html.endswith("</td>"):
        return cell_html
    return cell_html[:-len("</td>")] + f"<span class='dim' style='font-size:9px'> {asof}</span></td>"


def _bigret_diff_td(big_amt, ret_amt, tot_amt, unm=False, full_stats=False):
    """大戶−散戶格(=大戶佔比−散戶佔比,%),含A/B/C/D分類(2026-09-24定案邏輯,原本只在全日尺度算)。
    2026-10-02 jack 交辦:把這個算法延伸到5分/30分也各算一份(欄位架構重整)。
    full_stats=True(全日專用,唯一經127日面板實測驗證的尺度)才附上具體bps數字;
    5分/30分版本只給A/B/C/D分類名稱、不附bps——那些數字是全日尺度校準出來的,沒有
    分別對5分/30分窗重跑驗證,不能直接挪用(避免看起來像兩個尺度都驗證過)。"""
    if unm or big_amt is None or ret_amt is None or not tot_amt:
        return "<td class='dim'>—</td>"
    bp = big_amt / tot_amt * 100
    rp = ret_amt / tot_amt * 100
    dp = bp - rp
    if bp >= 10 and rp < 5:
        lab, cls = "A 大戶買·散戶未主導" + ("(127日隔夜 +124,散0~5%最佳 +185/t4.5)" if full_stats else ""), "up"
    elif bp >= 10 and rp >= 5:
        lab, cls = "B 大戶帶散戶(散戶≥5%" + (",127日 +75/t1.2 n15,打折)" if full_stats else ")"), "warnv"
    elif bp <= -10 and rp >= 5:
        lab, cls = "C 大戶倒·散戶接刀" + ("(127日隔夜 −11,低於基準 +73 約 85bps)" if full_stats else ""), "dn"
    elif bp <= -10:
        lab, cls = "D 大戶賣·散戶不接" + ("(127日隔夜 −13,低於基準約 85bps)" if full_stats else ""), "dn"
    else:
        lab, cls = "佔比未過 ±10% 門檻,不判", ""
    bold = "font-weight:700" if abs(dp) >= 15 else ""
    return (f"<td class='{cls or ('up' if dp > 0 else ('dn' if dp < 0 else ''))}' data-sort='{dp}' style='{bold}' "
            f"title='大戶佔比 {bp:+.1f}% − 散戶佔比 {rp:+.1f}% = {dp:+.1f}% → {lab}'>{dp:+.1f}%</td>")


def _wrt_td(c, p, bull, bear, tip):
    """權證多空一格:購額/售額(未簽號活動量,萬)+ 小字=簽號後多方占比(主動買call+主動賣put ÷ 全部主動額);
    顏色依簽號淨額:紅=多方>空方、綠=空方>多方、灰=皆0。簽號欄缺時退回未簽號(舊檔相容)。"""
    if bull is None or bear is None:
        bull, bear = c, p
    tot = bull + bear
    # 權證單價低(幾毛~幾塊),一筆 1 張只有幾百元;<10 萬顯示一位小數,免得「0/0 100%」。
    def _w(v):
        return f"{v/1e4:,.1f}" if v < 1e5 else f"{v/1e4:,.0f}"
    MIN_JUDGE = 1e5                                  # 主動額 <10 萬不下判斷(幾百元的成交不算方向)
    pct_sort = (bull / tot * 100) if tot > 0 else None   # 排序鍵:簽號後多方占比%(比購/售活動量更貼近多空判斷,2026-09-29)
    if tot >= MIN_JUDGE:
        pct = pct_sort
        shr = f"{pct:.0f}%"
        # 判斷帶:≥60% 偏多(紅)、≤40% 偏空(綠)、其間中性(灰)——顏色與文字同一規則
        cls, lab = ("up", "偏多") if pct >= 60 else (("dn", "偏空") if pct <= 40 else ("dim", "中性"))
    elif tot > 0:
        shr, cls, lab = f"{pct_sort:.0f}%", "dim", "量小"
    else:
        shr, cls, lab = "—", "dim", ""
    _dsort = f" data-sort='{pct_sort}'" if pct_sort is not None else ""
    return (f"<td class='{cls}'{_dsort} style='font-size:11px' title='{tip} · 活動量 購{_w(c)}萬/售{_w(p)}萬 · "
            f"簽號 多方{_w(bull)}萬/空方{_w(bear)}萬 · 多方占比{shr} {lab}(主動額<10萬不判斷)'>"
            f"{_w(c)}/{_w(p)}<span class='dim' style='font-size:9px'> {shr}</span>"
            f"{(' <b>' + lab + '</b>') if lab else ''}</td>")
