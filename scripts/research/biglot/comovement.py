"""儀表板「股價共動」欄：每檔顯示資料上最像哪家、本族群內誰連動誰無關、是否該提防族群判斷。

資料檔由 scripts/research/sector_comovement_tags.py 產生（近 1 年日報酬、扣大盤殘差相關）。
純描述性提醒，不進分數、不影響排序。檔案缺失或損毀時整欄退回「—」，不影響儀表板。
"""
from __future__ import annotations

import html as html_mod
import json
import time
from datetime import date

import biglot_dashboard
from stock_db import DATA_DIR

_PATH = DATA_DIR.parent / "cache" / "biglot_live_watch" / "comovement_tags.json"
_PATH_BROAD = _PATH.with_name("comovement_broad.json")
_CACHE: dict = {"mtime": None, "checked": 0.0, "data": None}
_CACHE_B: dict = {"mtime": None, "checked": 0.0, "data": None}
_BLUE, _BLUE_DIM, _YEL, _ORG, _GREY = "#58a6ff", "#6e9fd0", "#d29922", "#f0883e", "#8b949e"
_WEAK = 0.35   # 殘差相關低於此視為弱


def _load():
    now = time.time()
    if now - _CACHE["checked"] < 60:
        return _CACHE["data"]
    _CACHE["checked"] = now
    try:
        mt = _PATH.stat().st_mtime
        if mt != _CACHE["mtime"]:
            _CACHE["data"] = json.loads(_PATH.read_text(encoding="utf-8"))
            _CACHE["mtime"] = mt
    except Exception:  # noqa: BLE001 -- 缺檔不影響儀表板
        _CACHE["data"] = None
    return _CACHE["data"]


def _load_broad():
    now = time.time()
    if now - _CACHE_B["checked"] < 60:
        return _CACHE_B["data"]
    _CACHE_B["checked"] = now
    try:
        mt = _PATH_BROAD.stat().st_mtime
        if mt != _CACHE_B["mtime"]:
            _CACHE_B["data"] = json.loads(_PATH_BROAD.read_text(encoding="utf-8"))
            _CACHE_B["mtime"] = mt
    except Exception:  # noqa: BLE001
        _CACHE_B["data"] = None
    return _CACHE_B["data"]


def _tip(o: dict, meta: dict, stale_days: int, bo: dict | None = None, bmeta: dict | None = None) -> str:
    L = [f"股價共動(描述性,不進分數):{o['name']} · 儀表板族群『{o['group']}』",
         f"資料:{meta['start']}~{meta['end']} 近1年 {meta['n_days']} 個交易日日報酬,扣加權指數後的相關係數(殘差相關)。"
         + (f"⚠資料已 {stale_days} 天未更新,請重跑 sector_comovement_tags.py。" if stale_days > 45 else "")]
    if o["aff_own"] is not None:
        L.append(f"與本族群其他成員平均的相關:{o['aff_own']:+.2f}(≥.45 黏 / .30~.45 中等 / <.30 弱)。"
                 f"最像的其他族群:{o['best_other_group']} {o['best_other_aff']:+.2f}")
    elif o["best_other_group"]:
        L.append(f"單檔族群,無族內可比。最像的族群:{o['best_other_group']} {o['best_other_aff']:+.2f}")
    if bo:
        L.append(f"── 全市場約{bmeta['n_stocks']}檔中最像的 5 檔(殘差 Spearman;A強/B中/C不可靠;*=不同官方產業)"
                 f"→ 本股結論:{bo['verdict']}")
        L.extend(f"  {p['name']} {p['c']:+.2f}(前半{p['c1']:+.2f}/後半{p['c2']:+.2f}) {p['tier']}{'' if p['same_ind'] else '*'}"
                 for p in bo["partners"][:5])
        L.append("  分級樣本外驗證:A級訓練.67→未來.58;B級.51→.40;C級.36→.20(隨機亂配.09、同產業亂配.16、雜訊底.20~.25)")
    L.append("── 儀表板42檔範圍內最相關的 5 檔(相關係數 / 族群特有偏相關;穩=通過顯著性且前後半年同號)")
    for p in o["partners"]:
        L.append(f"  {p['name']}({p['group']}) {p['res']:+.2f} / {p['par']:+.2f} {'穩' if p['stable'] else '不穩'}")
    if o["mates"]:
        L.append("── 同族群成員")
        st = {"linked": "連動", "unrelated": "無關(不顯著或前後半年不一致)", "negative": "反向"}
        for m in o["mates"]:
            L.append(f"  {m['name']} {m['res']:+.2f} / {m['par']:+.2f} {st[m['status']]}")
    if o["neg"]:
        L.append("── 扣大盤後顯著偏反向(原始相關仍為正,只表示不是同一路在動,大盤漲跌時照樣一起動):")
        L.extend(f"  {n['name']} {n['res']:+.2f}" for n in o["neg"])
    L.append(f"提醒:全池平均相關 大盤上漲日 {meta['pool_raw_up']:+.2f} / 下跌日 {meta['pool_raw_down']:+.2f},"
             "大跌日同族一起殺不代表族群有獨立訊息。相關≠因果,也≠可配對交易;近1年單一行情,換環境可能變。"
             "『更像X組』是資料歸屬假說,未經樣本外驗證。")
    return "\n".join(L)


def _role_line2(sid: str) -> tuple[str | None, str]:
    """監測名單角色(config/biglot_roles.json)→(第二行 HTML 或 None, tooltip 附註)。核心列回 None 沿用原標籤。"""
    ref = biglot_dashboard.REF_POOL.get(sid) or biglot_dashboard.REF_EXTRA.get(sid)
    if ref:
        return (f"<span style='color:{_GREY};font-weight:700'>參考列</span>"
                f"<span class='sub'> {html_mod.escape(ref['note'])}</span>",
                f"【參考列】只看族群脈絡,不進排名/標籤/淨分/旗標;交易標的是同組有期貨的核心檔。{ref['note']}")
    ind = biglot_dashboard.INDEP.get(sid)
    if ind:
        return (f"<span style='color:{_YEL};font-weight:700'>獨·{html_mod.escape(ind['kind'])}</span>"
                f"<span class='sub'> {html_mod.escape(ind['reason'])}</span>",
                f"【獨立標注·{ind['kind']}】{ind['reason']}(原地標注、不隱藏)")
    return None, ""


def _broad_only_td(sid: str, line2: str | None, note: str) -> str:
    """池外參考列:不在 42 檔 tags 檔內,只用全市場 A/B 夥伴(comovement_broad.json)。"""
    bd = _load_broad()
    bo = (bd or {}).get("stocks", {}).get(sid)
    if not bo:
        return f"<td class='dim' title='無共動資料:{html_mod.escape(note, quote=True)}'>—</td>"
    tc = {"A": _BLUE, "B": _BLUE_DIM, "C": _GREY}
    pool = set(biglot_dashboard.NAMES)
    good = [p for p in bo["partners"] if p["tier"] in "AB"][:2] or bo["partners"][:1]
    line1 = "最像 " + " · ".join(
        f"<b>{html_mod.escape(p['name'].rstrip('*'))}</b> <span style='color:{tc[p['tier']]}'>{p['c']:+.2f}{p['tier']}</span>"
        + ("" if p["sid"] in pool else "<span class='sub'>池外</span>") for p in good)
    L = [f"股價共動·{bo['name']}(池外參考列;殘差Spearman,A強/B中/C不可靠)", note]
    L.extend(f"  {p['name']} {p['c']:+.2f}(前半{p['c1']:+.2f}/後半{p['c2']:+.2f}) {p['tier']}" for p in bo["partners"][:5])
    tip = html_mod.escape("\n".join(L), quote=True).replace("\n", "&#10;")
    return (f"<td class='cmv' data-sort='{bo['partners'][0]['c']}' style='font-size:11px;white-space:nowrap' "
            f"title='{tip}'>{line1} <span style='color:{_GREY}'>│</span> {line2 or ''}</td>")


def _comovement_td(sid: str) -> str:
    d = _load()
    o = (d or {}).get("stocks", {}).get(sid)
    role_l2, role_note = _role_line2(sid)
    if not o:
        if sid in biglot_dashboard.REF_EXTRA:
            return _broad_only_td(sid, role_l2, role_note)
        return "<td class='dim' title='股價共動資料檔缺失或無此股(跑 scripts/research/sector_comovement_tags.py)'>—</td>"
    meta = d["meta"]
    try:
        stale = (date.today() - date.fromisoformat(meta["end"])).days
    except Exception:  # noqa: BLE001
        stale = 0
    e = html_mod.escape
    ps = o["partners"]
    p0 = ps[0]
    tag0 = "" if (p0["stable"] and p0["res"] >= _WEAK) else f" <span style='color:{_GREY}'>({'弱' if p0['stable'] else '不穩'})</span>"
    bd = _load_broad()
    bo = (bd or {}).get("stocks", {}).get(sid)
    in42 = set(d["stocks"])
    sort_v = p0["res"]
    if bo:
        bp = bo["partners"]
        good = [p for p in bp if p["tier"] in "AB"]
        tc = {"A": _BLUE, "B": _BLUE_DIM, "C": _GREY}

        def _fmt(p):
            out = f"<b>{e(p['name'])}</b> <span style='color:{tc[p['tier']]}'>{p['c']:+.2f}{p['tier']}</span>"
            return out + ("" if p["sid"] in in42 else "<span class='sub'>池外</span>")
        if good:
            line1 = "最像 " + " · ".join(_fmt(p) for p in good[:2])
        else:
            line1 = f"最像 {_fmt(bp[0])} <span style='color:{_YEL}'>無可靠夥伴</span>"
        sort_v = bp[0]["c"]
    else:
        line1 = f"最像 <b>{e(p0['name'])}</b> {p0['res']:+.2f}{tag0}"
        if o["group"] != p0["group"]:
            line1 += f"<span class='sub'> 跨組·{e(p0['group'])}</span>"
        if len(ps) > 1 and ps[1]["stable"] and ps[1]["res"] >= _WEAK:
            line1 += f" · {e(ps[1]['name'])} {ps[1]['res']:+.2f}"
    lab = o["label"]
    if lab == "sticky":
        line2 = f"<span style='color:{_BLUE};font-weight:700'>組內黏</span>"
    elif lab == "mid":
        line2 = f"<span style='color:{_BLUE_DIM}'>組內中等</span>"
    elif lab == "weak":
        line2 = f"<span style='color:{_YEL};font-weight:700'>組內弱</span>"
    elif lab == "misfit":
        own = "" if o["aff_own"] is None else f"<span class='sub'> 本組{o['aff_own']:+.2f}</span>"
        line2 = (f"<span style='color:{_ORG};font-weight:700'>更像{e(o['misfit_to'])}組</span>"
                 f"<span class='sub'> {o['best_other_aff']:+.2f}</span>{own}")
    elif lab == "orphan":
        line2 = f"<span style='color:{_YEL};font-weight:700'>無明顯同伴</span>"
    else:
        line2 = f"<span style='color:{_GREY}'>單檔族群</span>"
    unrel = [m["name"] for m in o["mates"] if m["status"] == "unrelated"]
    if unrel:
        shown = "、".join(unrel[:3]) + (f"等{len(unrel)}檔" if len(unrel) > 3 else "")
        line2 += f" <span style='color:{_GREY}'>與{e(shown)}無關</span>"
    if o["neg"]:
        n0 = min(o["neg"], key=lambda n: n["res"])
        line2 += f" <span style='color:{_YEL}'>⚠弱反向 {e(n0['name'])}{n0['res']:+.2f}</span>"
    gs = biglot_dashboard.GROUP_STRENGTH.get(biglot_dashboard.CLUSTER_OF.get(sid, ""))
    if role_l2:
        line2 = role_l2
    elif gs:
        line2 += f" <span style='color:{_YEL}'>群{e(gs)}</span>"
    tip = e((role_note + "\n" if role_note else "") + (f"族群整體:{gs}\n" if gs else "")
            + _tip(o, meta, stale, bo, (bd or {}).get('meta')), quote=True).replace("\n", "&#10;")
    return (f"<td class='cmv' data-sort='{sort_v}' style='font-size:11px;white-space:nowrap' "
            f"title='{tip}'>{line1} <span style='color:{_GREY}'>│</span> {line2}</td>")
