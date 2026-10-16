"""籌碼 SOP（/chips）：只讀 chip_sop.py 每日落地的 chip_sop_latest.json，不連外、不寫檔。

分組＝SOP 第一關：買方集中／強賣／中性／資料不全（門檻見快照 params）。
隔日沖席位只做標示，不加權；一致度是個數（0–4），不是分數。
頁面上所有數值門檻（說明文字、分組標籤、表頭）一律讀快照的 params，這裡不寫死數字。
"""
from __future__ import annotations

import html as html_mod
import json
import re
from collections import Counter
from datetime import date as _date

from stock_db import DATA_DIR

CHIP_DIR = DATA_DIR / "chip"
LATEST = CHIP_DIR / "chip_sop_latest.json"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
esc = html_mod.escape


def _g(v) -> str:
    return "?" if v is None else f"{v:g}"


def _fill(text: str, p: dict) -> str:
    """把 {key} 換成快照 params 的值（比例類換成百分數）；沒有的鍵顯示 ?，不會偷用預設值。"""
    vals = {k: _g(v) for k, v in p.items() if not isinstance(v, list)}
    vals["complete_pct"] = _g(p["complete_ratio"] * 100) if "complete_ratio" in p else "?"
    vals["reversal_pct"] = _g(p["flip_reversal"] * 100) if "flip_reversal" in p else "?"
    vals["dominant_pct"] = _g(p["dominant_share"] * 100) if "dominant_share" in p else "?"
    vals["foreign_pct"] = _g(p["foreign_share"] * 100) if "foreign_share" in p else "?"
    vals["consensus_big"] = "＋".join(esc(str(x)) for x in p.get("consensus_big", [])) or "?"
    for k, v in vals.items():
        text = text.replace("{" + k + "}", v)
    return re.sub(r"\{(?!(?:d0|prev|next|asof)\})[a-z_]+\}", "?", text)


def _groups(p: dict) -> tuple:
    return (("buy", _fill("買方集中（主力日 ≥ +{band_pct}%）", p)), ("sell", _fill("強賣（主力日 &lt; −{band_pct}%）", p)),
            ("neutral", _fill("中性（|主力日| &lt; {band_pct}%）", p)), ("bad", "資料不全（分點未完整，不判讀）"))


CSS = """<style>
table.cz{border-collapse:collapse;font-size:12px;width:100%}
table.cz th{position:sticky;top:0;background:#1a1a1a;color:#8b949e;padding:5px 7px;text-align:right;white-space:nowrap}
table.cz td{padding:4px 7px;border-bottom:1px solid #222;text-align:right;white-space:nowrap;vertical-align:top}
table.cz td.l,table.cz th.l{text-align:left}
table.cz td.grp{background:#1d1d24;color:#d2a8ff;font-weight:600;padding:7px;text-align:left}
.up{color:var(--price-up,#ff7b72)}.dn{color:var(--price-down,#3fb950)}.dim{color:#6e7681;font-size:10px}
.seat-h{color:#ffa657;font-weight:700}.seat-l{color:#8b949e}.seat-u{color:#d2a8ff}
.fl{display:inline-block;margin:0 2px 2px 0;padding:0 5px;border-radius:4px;background:#21262d;color:#c9d1d9;font-size:10px}
.note{color:#8b949e;font-size:11px;line-height:1.7;margin:10px 0;padding:8px 10px;background:#141414;border-left:3px solid #333}
.note summary{cursor:pointer;color:#c9d1d9}.note p{margin:6px 0}
.fresh{font-size:11px;color:#8b949e;margin:6px 0 10px;padding:6px 10px;background:#141414;border-left:3px solid #3fb950}
.fresh.bad{border-left-color:var(--status-error,#f85149);line-height:1.8}
.alert{margin:8px 0;padding:8px 12px;background:#2a1215;border-left:4px solid var(--status-error,#f85149);font-size:12px;line-height:1.8}
.alert b{color:#ff9b9b}
tr.flip3 td{background:#2a1215}
.flip3tag{display:inline-block;padding:0 5px;border-radius:4px;background:#b62324;color:#fff;font-size:10px;font-weight:700}
.nav{margin:6px 0;font-size:12px;line-height:2}.nav a{margin-right:4px}.nav .cur{color:#fff;background:#1f6feb;padding:1px 6px;border-radius:4px;margin-right:4px}
.pend{color:#6e7681;font-size:10px}
.gsum{color:#8b949e;font-weight:400;font-size:11px;margin-left:10px}
</style>"""


_WD = "一二三四五六日"


def _md(d) -> str:
    return str(d)[5:] if d else "—"


def _d(d) -> str:
    try:
        return f"{str(d)[5:]}({_WD[_date.fromisoformat(str(d)).weekday()]})"
    except (TypeError, ValueError):
        return "—"


def _mode(vals) -> str | None:
    c = Counter(v for v in vals if v)
    return c.most_common(1)[0][0] if c else None


def _cls(v) -> str:
    return "" if v is None or v == 0 else "up" if v > 0 else "dn"


def _num(v, fmt="{:+,.0f}", suffix="") -> str:
    return "—" if v is None else f"<span class='{_cls(v)}'>{fmt.format(v)}{suffix}</span>"


def _period(w) -> str:
    if not w:
        return "—"
    return f"{_num(w['conc_pct'], '{:+.1f}', '%')}<div class='dim'>{w['main_lots']:+,} 張</div>"


def _it(w) -> str:
    if not w:
        return "—"
    return f"{_num(w['lots'])}<span class='dim'> {w['pct']:+.1f}%量</span>" if w["pct"] is not None else _num(w["lots"])


def _seats(items: list[dict]) -> str:
    out = []
    for s in items:
        tag, cls = "", ""
        seat = s.get("seat")
        if seat:
            if seat["level"] == "high":
                tag, cls = f"【沖{seat['flip']:.2f}】", "seat-h"
            elif seat["level"] == "low":
                tag, cls = "(沖低)", "seat-l"
            else:
                tag, cls = "【沖?】", "seat-u"
        out.append(f"<div class='{cls}'>{esc(s['name'])} {s['lots']:+,}{tag}</div>")
    return "".join(out) or "—"


def _next(r: dict) -> str:
    nx = r.get("next")
    if not nx:
        return "<span class='pend'>待下一交易日收盤</span>" if r.get("close") is not None else "—"
    return (f"<b>{_num(nx['ret_pct'], '{:+.2f}', '%')}</b><div class='dim'>{esc(_d(nx['date']))} 跳空 {_num(nx['gap_pct'], '{:+.2f}', '%')}"
            f" · 盤中 {_num(nx['intraday_pct'], '{:+.2f}', '%')}</div>")


def _cons(c: dict | None) -> str:
    if not c:
        return "—"
    head = f"{c['agree']}/{c['known']}" if c["known"] else "—"
    notes = [f"{n}票{lab}" for k, lab in (("abstain", "棄權"), ("missing", "缺資料")) if (n := c.get(k))]
    return head + (f"<div class='dim'>{'、'.join(notes)}</div>" if notes else "")


def _flip3_cell(r: dict) -> str:
    f = r.get("flip3")
    if not f or not f["items"]:
        return "—"
    out = []
    for i in f["items"]:
        mark = ""
        if i["sell_rank"] and i["sell_rank"] <= f["k"]:
            mark = f" <span class='flip3tag'>{_md(r['date'])}賣超第{i['sell_rank']}</span>"
        elif i["sell_rank"]:
            mark = f" <span class='dim'>{_md(r['date'])}轉賣</span>"
        out.append(f"<div>{esc(i['name'])} <span class='up'>{i['prev_lots']:+,}</span>→{_num(i['now_lots'])}{mark}</div>")
    return "".join(out)


def _consensus_cell(r: dict) -> str:
    c = r.get("consensus")
    if not c:
        return "—"
    out = []
    for side, cls, lab in (("buy", "up", "買"), ("sell", "dn", "賣")):
        if c.get(f"{side}_hit"):
            names = "、".join(f"{esc(x['name'])} {x['lots']:+,}" for x in c[side])
            out.append(f"<div><span class='{cls}'>共識{lab}超 {len(c[side])}</span> <span class='dim'>{names}</span></div>")
    return "".join(out) or "—"


def _consensus_alert(rows: list[dict], p: dict, d0: str | None = None) -> str:
    lines = []
    for side, lab in (("buy", "買超"), ("sell", "賣超")):
        hits = [(r, r["consensus"][side]) for r in rows if (r.get("consensus") or {}).get(f"{side}_hit")]
        hits.sort(key=lambda h: -len(h[1]))
        if hits:
            segs = "；".join(f"<b>{esc(r['sid'])} {esc(r['name'])}</b>（{'、'.join(esc(x['name']) + f' {x['lots']:+,}' for x in it)}）" for r, it in hits)
            lines.append(f"<div><b>共識{lab} {len(hits)} 檔</b>：{segs}</div>")
    if not lines:
        return _fill("<div class='fresh'>席位共識：" + _md(d0) + " 無（名單席位同方向淨量 ≥{consensus_lots} 張且 ≥{consensus_min_seats} 個）</div>", p)
    return _fill("<div class='fresh'>席位共識 " + _md(d0) + "（名單席位同方向淨量 ≥{consensus_lots} 張、≥{consensus_min_seats} 個；只有 {consensus_big} 不算）", p) + f"{''.join(lines)}</div>"


def _row(r: dict) -> str:
    name = f"<b>{esc(r['sid'])}</b> {esc(r['name'])}"
    price = ""
    if r.get("close") is not None:
        price = f"<div class='dim'>{r['close']:g} <span class='{_cls(r.get('chg_pct'))}'>{r['chg_pct']:+.2f}%</span></div>" if r.get("chg_pct") is not None else f"<div class='dim'>{r['close']:g}</div>"
    it = r.get("it") or {}
    if r.get("status") != "ok":
        why = {"tape_incomplete": "分點最新日未完整", "stale_price": "資料日無日 K"}.get(r.get("status"), "無價")
        return (f"<tr><td class='l'><a href='/stock?sid={esc(r['sid'])}'>{name}</a>{price}</td><td colspan='3' class='l dim'>{why}</td>"
                f"<td>{_it(it.get('d1'))}<div class='dim'>5d {_it(it.get('d5'))}</div></td><td colspan='5'></td><td>{_next(r)}</td></tr>")
    day = r["day"]
    diff = f"<div class='dim'>家數差 {day['diff_cnt']:+,}</div>" if day.get("diff_cnt") is not None else ""
    mode = "<div class='dim' title='FinMind 停用後分點只剩買超/賣超前段名單;主力日張數相同,集中度改除以成交量,家數差不提供'>前段分點口徑</div>" if r.get("tape_mode") == "top" else ""
    main = f"{_num(day['conc_pct'], '{:+.1f}', '%')}<div class='dim'>{day['main_lots']:+,} 張</div>{diff}{mode}"
    flags = "".join(f"<span class='fl'>{esc(f)}</span>" for f in r["flags"]) or "—"
    cons = _cons(r.get("consistency"))
    itcell = f"{_it(it.get('d1'))}<div class='dim'>5d {_it(it.get('d5'))}</div><div class='dim'>20d {_it(it.get('d20'))}</div>"
    if r.get("it_missing"):
        itcell += "<div class='dim'>缺最新日</div>"
    strict = bool((r.get("flip3") or {}).get("strict"))
    return (f"<tr{' class=flip3' if strict else ''}><td class='l'><a href='/stock?sid={esc(r['sid'])}'>{name}</a>{price}</td><td>{main}</td>"
            f"<td>5d {_period(r['d5'])}<div style='height:3px'></div>20d {_period(r['d20'])}</td><td>{itcell}</td>"
            f"<td class='l'>{_seats(r['buyers'])}</td><td class='l'>{_seats(r['sellers'])}</td><td class='l'>{_consensus_cell(r)}</td><td class='l'>{_flip3_cell(r)}</td>"
            f"<td class='l'>{flags}</td><td>{cons}</td><td>{_next(r)}</td></tr>")


def _group_of(r: dict) -> str:
    return "bad" if r.get("status") != "ok" else r["signal"]


def _next_stats(rows: list[dict]) -> dict | None:
    nx = [r["next"] for r in rows if r.get("next")]
    if not nx:
        return None
    avg = lambda k: sum(x[k] for x in nx if x[k] is not None) / max(1, sum(1 for x in nx if x[k] is not None))  # noqa: E731
    return {"n": len(nx), "dates": "/".join(sorted({_md(x["date"]) for x in nx})), "ret": avg("ret_pct"), "gap": avg("gap_pct"),
            "intra": avg("intraday_pct"), "up": sum(1 for x in nx if x["ret_pct"] > 0)}


def _group_summary(grp: list[dict], base: dict | None = None) -> str:
    st = _next_stats(grp)
    if not st:
        return ""
    rel = f"（對全體 {st['ret'] - base['ret']:+.2f}）" if base else ""
    return (f"<span class='gsum'>下一交易日 {st['dates']} n={st['n']}：收盤均 {st['ret']:+.2f}%{rel} · 上漲 {st['up']}/{st['n']} · 跳空均 {st['gap']:+.2f}% · "
            f"盤中均 {st['intra']:+.2f}%（描述用，非預測）</span>")


def _cal_dates(rows: list[dict], cal: dict | None, asof: str | None = None) -> tuple:
    """(資料日, 前一交易日, 下一交易日)：有快照日曆就照讀，舊快照才由各列推斷。下一交易日只在已有隔日資料時才顯示。"""
    if cal:
        has_next = any(r.get("next") for r in rows)
        return cal.get("asof"), cal.get("prev"), cal.get("next") if has_next else None
    return (asof or _mode(r.get("date") for r in rows), _mode((r.get("flip3") or {}).get("prev_date") for r in rows),
            _mode((r.get("next") or {}).get("date") for r in rows))


def render_table(rows: list[dict], asof: str | None = None, cal: dict | None = None, params: dict | None = None) -> str:
    p = params or {}
    d0, prev, nxt = _cal_dates(rows, cal, asof)
    k = _g(p.get("flip_k"))
    nx_head = f"{_d(nxt)} 收盤漲跌<br>對 {_md(d0)} 收盤 · 跳空 · 盤中" if nxt else f"下一交易日收盤漲跌<br>（尚未收盤）· 對 {_md(d0)} 收盤"
    head = (f"<tr><th class='l'>代號名稱</th><th>主力 {_md(d0)}<br>集中度 / 張</th><th>主力 5日 / 20日<br>（至 {_md(d0)}）集中度 / 張</th><th>投信 {_md(d0)} / 5d / 20d<br>張 · 佔量</th>"
            f"<th class='l'>買方前{_g(p.get('seats_shown'))}（張）</th><th class='l'>賣方前{_g(p.get('seats_shown'))}（張）</th><th class='l'>席位共識</th><th class='l'>{_d(prev)} 買超前{k}<br>→ {_md(d0)} 淨張數</th><th class='l'>旗標</th><th>一致度</th>"
            f"<th>{nx_head}</th></tr>")
    body = []
    base = _next_stats(rows)
    if base:
        body.append(f"<tr><td class='grp' colspan='11'>全體基準 · {len(rows)} 檔{_group_summary(rows)}<span class='gsum'>各組請對照此列，扣掉大盤當天漲跌</span></td></tr>")
    for key, label in _groups(p):
        grp = [r for r in rows if _group_of(r) == key]
        if not grp:
            continue
        if key == "sell":
            grp.sort(key=lambda r: r["day"]["conc_pct"])
        elif key in ("buy", "neutral"):
            grp.sort(key=lambda r: -abs(r["day"]["conc_pct"]))
        body.append(f"<tr><td class='grp' colspan='11'>{label} · {len(grp)} 檔{_group_summary(grp, base)}</td></tr>")
        body.extend(_row(r) for r in grp)
    return f"<table class='cz'>{head}{''.join(body)}</table>"


NOTE = """<details class='note'><summary>怎麼讀（SOP）</summary>
<p><b>主力日</b>＝分點前{top_n}買超＋前{top_n}賣超（張）；集中度％＝主力 ÷ 當日分點買進合計（≈成交量）。期間版先把窗內各分點淨額加總再取前{top_n}。
只有分點買進 ≥ {complete_pct}% 成交量且 ≥{min_branches} 分點的日子才算完整；5日／20日只用到最新日為止連續完整的交易日，不足顯示「—」。
分組：主力日集中度 ≥ +{band_pct}% 為買方集中、&lt; −{band_pct}% 為強賣；成交量 &lt; {thin_lots} 張標「量小」；單一分點佔買超前{top_n}合計 ≥ {dominant_pct}% 標「單點主導」、外資分點 ≥ {foreign_pct}% 標「外資主導」。</p>
<p><b>前段分點口徑（2026-10-17 起）</b>：FinMind 停用後，分點來源改為富邦行情站，每檔只有買超前{top_n}與賣超前{top_n}名（加上專家／隔日沖席位）。主力日張數與全分點版相同（本來就只用前{top_n}名），集中度改除以當日成交量；
5日／20日改為「逐日主力加總 ÷ 窗內成交量」（沒有每個分點每天的淨額可跨日加總）；家數差不提供；完整日＝買超與賣超分點各 ≥{top_min_side} 個。標「前段分點口徑」的列屬此口徑，之前的日子仍是全分點算法，兩者不要直接比較期間版。</p>
<p><b>投信</b>含被動 ETF 申贖，5日佔量 ≥ {it_act_pct}% 才算有動作。<b>一致度</b>＝5日主力、20日主力、投信5日、當日價格四票中與當日主力同向的「同意票／有效票」，只是個數、不是分數。
三態：未達死區（期間主力 |集中度| &lt; {period_dead_pct}%、投信5日佔量 &lt; {it_act_pct}%、|漲跌幅| &lt; {price_dead_pct}%）＝棄權，資料缺＝缺資料，兩者皆不進分母，卡片會標出幾票；
家數差只顯示（街口六指標檢定其方向反轉，不計入一致度）。</p>
<p><b>{prev} 買超前{flip_k} → {d0} 淨張數</b>＝{prev}（{d0} 的前一個交易日）淨買超前{flip_k}名的分點，在 {d0} 的淨張數；落在 {d0} 淨賣超前{flip_k}名者標紅並列於頁首（「轉賣」＝僅轉為淨賣超）；前一個交易日的分點也須完整才比對。
<b>{next} 收盤漲跌</b>＝{next} 收盤對 {d0} 收盤（跳空＝{next} 開盤對 {d0} 收盤、盤中＝{next} 開盤到收盤），{next} 資料入庫後由排程補上；各組平均僅描述，
41檔×129天檢定顯示 SOP 欄位對下一交易日收盤無獨立預測力（chips-sop-next-close-verdict-20261013）。</p>
<p><b>席位共識</b>＝名單席位當日同方向淨買／賣 ≥{consensus_lots} 張且 ≥{consensus_min_seats} 個（只有 {consensus_big} 不算，兩家量大太容易同向）。席位本來就是下一交易日賣的，共識買超的意義是「下一交易日可能有賣壓」，不是看多；30天回看兩方向下一交易日報酬都無差異。</p>
<p><b>隔日沖席位</b>（【沖x.xx】＝近期該分點當日淨買超前{flip_top_k}且 ≥{flip_min_lots} 張後，下一交易日淨賣回 ≥{reversal_pct}% 的比率，≥ {flip_high_threshold} 標【沖】，名單重建於 {asof}，會隨習慣改變、定期重建）僅供提示，
分點選擇在歷史檢定中不優於隨機（159檔×22窗），不加權、不當進出場訊號。地緣券商目前沒有資料，未納入。</p></details>"""


def snapshot_dates() -> list[str]:
    return sorted(p.stem[len("chip_sop_"):] for p in CHIP_DIR.glob("chip_sop_2*.json") if DATE_RE.match(p.stem[len("chip_sop_"):]))


def _load(date: str | None) -> dict | None:
    path = CHIP_DIR / f"chip_sop_{date}.json" if date and DATE_RE.match(date) else LATEST
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _date_nav(dates: list[str], cur: str) -> str:
    if not dates:
        return ""
    earlier = [d for d in dates if d < cur]
    later = [d for d in dates if d > cur]
    prev = f"<a href='/chips?date={earlier[-1]}'>← {_d(earlier[-1])}</a>" if earlier else ""
    nxt = f"<a href='/chips?date={later[0]}'>{_d(later[0])} →</a>" if later else ""
    recent = dates[-30:]
    links = "".join(f"<span class='cur'>{d[5:]}</span>" if d == cur else f"<a href='/chips?date={d}'>{d[5:]}</a>" for d in recent)
    older = f" ｜ 更早 {len(dates) - len(recent)} 日：<a href='/chips?all=1'>全部日期</a>" if len(dates) > len(recent) else ""
    return f"<div class='nav'>{prev} {nxt} ｜ {links}{older} ｜ <a href='/chips?fmt=csv'>CSV（全部日期，供研究）</a></div>"


def _missing_msg(date: str, dates: list[str]) -> str:
    if not DATE_RE.match(date):
        return f"日期格式錯誤：{esc(date)}（需 YYYY-MM-DD）"
    try:
        wd = _date.fromisoformat(date).weekday()
    except ValueError:
        return f"不存在的日期：{esc(date)}"
    if wd >= 5:
        return f"{_d(date)} 為週末，無交易、無快照"
    if dates and date > dates[-1]:
        return f"{_d(date)} 晚於最新快照 {_d(dates[-1])}，尚未產生"
    return f"{_d(date)} 無快照：休市日，或該日未產生（目前沒有交易日曆可區分兩者）。可由下方日期列選擇有快照的日子"


def _flip_alert(rows: list[dict], cal: dict | None = None, params: dict | None = None) -> str:
    k = _g((params or {}).get("flip_k"))
    hits = [r for r in rows if (r.get("flip3") or {}).get("strict")]
    if not hits:
        d0, prev, _ = _cal_dates(rows, cal)
        return f"<div class='fresh'>買超前{k} → 賣超前{k}：{_md(d0)} 無（{_md(prev)} 淨買超前{k}名分點，{_md(d0)} 皆未進入淨賣超前{k}）</div>"
    lines = []
    for r in hits:
        f = r["flip3"]
        segs = "；".join(f"{esc(i['name'])} {_md(f['prev_date'])} {i['prev_lots']:+,}→{_md(r['date'])} {i['now_lots']:+,}（{_md(r['date'])}賣超第{i['sell_rank']}）" for i in f["items"] if i["sell_rank"] and i["sell_rank"] <= f["k"])
        lines.append(f"<div><b>{esc(r['sid'])} {esc(r['name'])}</b> · {segs}</div>")
    return f"<div class='alert'><b>買超前{k} → 賣超前{k} · {len(hits)} 檔</b>（比對 {esc(_d(_mode(h['flip3']['prev_date'] for h in hits)))} → {esc(_d(_mode(h['date'] for h in hits)))}，淨張數）{''.join(lines)}</div>"


def _all_dates_page(dates: list[str]) -> str:
    return "<div class='nav'>" + " ".join(f"<a href='/chips?date={d}'>{d}</a>" for d in reversed(dates)) + "</div>"


def render_csv() -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["asof", "sid", "name", "status", "signal", "close", "chg_pct", "conc_day", "main_lots_day", "conc_5d", "conc_20d", "it_5d_pct",
                "consistency_agree", "flip3_strict", "flip3_loose", "consensus_buy", "consensus_sell", "flags", "next_date", "next_ret_pct", "next_gap_pct", "next_intraday_pct"])
    for d in snapshot_dates():
        data = _load(d) or {"rows": []}
        for r in data["rows"]:
            day, d5, d20 = r.get("day") or {}, r.get("d5") or {}, r.get("d20") or {}
            nx, f, c = r.get("next") or {}, r.get("flip3") or {}, r.get("consistency") or {}
            w.writerow([d, r["sid"], r["name"], r.get("status"), r.get("signal", ""), r.get("close"), r.get("chg_pct"), day.get("conc_pct"), day.get("main_lots"),
                        d5.get("conc_pct"), d20.get("conc_pct"), ((r.get("it") or {}).get("d5") or {}).get("pct"), c.get("agree"),
                        int(bool(f.get("strict"))) if f else "", int(any(i["sell_rank"] for i in f["items"])) if f else "", "|".join(x["name"] for x in (r.get("consensus") or {}).get("buy", [])) if (r.get("consensus") or {}).get("buy_hit") else "",
                        "|".join(x["name"] for x in (r.get("consensus") or {}).get("sell", [])) if (r.get("consensus") or {}).get("sell_hit") else "",
                        "|".join(r.get("flags", [])),
                        nx.get("date"), nx.get("ret_pct"), nx.get("gap_pct"), nx.get("intraday_pct")])
    return buf.getvalue()


def render_chips(date: str | None = None, show_all: bool = False) -> str:
    import biglot_dashboard

    dates = snapshot_dates()
    bad_fmt = bool(date) and not DATE_RE.match(date)
    data = None if bad_fmt else _load(date)
    if show_all:
        body, fresh = _all_dates_page(dates), ""
    elif not data:
        anchor = date if date and not bad_fmt and DATE_RE.match(date) else (dates[-1] if dates else "")
        body, fresh = f"<div class='alert'>{_missing_msg(date, dates) if date else '尚無任何 chip_sop 快照'}</div>", _date_nav(dates, anchor) if anchor else ""
    else:
        warns = "".join(f"<div>⚠ {esc(w)}</div>" for w in data.get("warnings", []))
        cls = "fresh bad" if warns else "fresh"
        fresh = (f"{_date_nav(dates, str(data['asof']))}<div class='{cls}'>資料日 {esc(str(data['asof']))}（{esc(_d(data['asof'])[-2:-1])}） · 產生於 {esc(str(data['generated_at']))}"
                 f"{' · 事後補算（只用 ≤ 資料日的資料）' if (data.get('source') == 'backfill' or data.get('backfilled')) else ''}{warns}</div>"
                 f"{_flip_alert(data['rows'], data.get('calendar'), data.get('params'))}{_consensus_alert(data['rows'], data.get('params') or {}, str(data['asof']))}")
        try:
            rows, cal = data["rows"], data.get("calendar")
            _, prev, nxt = _cal_dates(rows, cal, str(data["asof"]))
            params = data.get("params") or {}
            note = (_fill(NOTE, params).replace("{asof}", esc(str(data.get("seats_flip_asof", "")))).replace("{d0}", _md(data["asof"]))
                    .replace("{prev}", _d(prev))
                    .replace("{next}", _d(nxt) if nxt else "下一交易日"))
            body = note + render_table(rows, str(data["asof"]), cal, params)
        except Exception as exc:  # noqa: BLE001
            body = f"<div class='note'>渲染失敗：{esc(repr(exc))}</div>"
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>籌碼 SOP</title>{biglot_dashboard.ARC_CSS}{CSS}</head><body>"
            f"<div class='meta'><a href='/'>← 大戶儀表板</a> ｜ <b>籌碼 SOP</b></div>{fresh}{body}</body></html>")
