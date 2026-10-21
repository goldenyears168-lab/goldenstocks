"""籌碼 SOP（/chips，權證小哥版）：只讀 chip_sop.py 每日落地的 chip_sop_latest.json，不連外、不寫檔。

分組＝SOP 第一關：買方集中／強賣／中性／資料不全（主力集中度門檻見快照 params）。
位置（布林）、連續、規則亮燈、隔日沖（逐股）、小哥點名分點都來自快照；規格見 docs/warrant-xiaoge/chip-sop-spec.md。
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
    if v is None:
        return "?"
    return v if isinstance(v, str) else f"{v:g}"


def _fill(text: str, p: dict) -> str:
    """把 {key} 換成快照 params 的值（比例類換成百分數）；沒有的鍵顯示 ?，不會偷用預設值。"""
    vals = {k: (esc(v) if isinstance(v, str) else _g(v)) for k, v in p.items() if not isinstance(v, list)}
    vals["complete_pct"] = _g(p["complete_ratio"] * 100) if "complete_ratio" in p else "?"
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
.fl{display:inline-block;margin:0 2px 2px 0;padding:0 5px;border-radius:4px;background:#21262d;color:#c9d1d9;font-size:10px}
.note{color:#8b949e;font-size:11px;line-height:1.7;margin:10px 0;padding:8px 10px;background:#141414;border-left:3px solid #333}
.note summary{cursor:pointer;color:#c9d1d9}.note p{margin:6px 0}
.fresh{font-size:11px;color:#8b949e;margin:6px 0 10px;padding:6px 10px;background:#141414;border-left:3px solid #3fb950}
.fresh.bad{border-left-color:var(--status-error,#f85149);line-height:1.8}
.alert{margin:8px 0;padding:8px 12px;background:#2a1215;border-left:4px solid var(--status-error,#f85149);font-size:12px;line-height:1.8}
.alert b{color:#ff9b9b}
.fl.buy{background:#3b1d1f;color:#ff9b9b}.fl.sell{background:#12301d;color:#7ee2a0}.fl.neutral{background:#21262d;color:#8b949e}
.role{color:#d2a8ff;font-size:10px}.hab{color:#ffa657;font-size:10px}
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


SCHEMA_MIN = 4
_ZONE = {"above_ub": "上軌之上", "ma_to_ub": "月線~上軌", "lb_to_ma": "下軌~月線", "below_lb": "下軌之下"}


def _w(r: dict, n: int) -> dict | None:
    return (r.get("win") or {}).get(str(n))


def _cell_w(w: dict | None) -> str:
    if not w:
        return "—"
    return f"{_num(w['conc_pct'], '{:+.1f}', '%')}<div class='dim'>{w['main_lots']:+,} 張</div>"


def _it(w) -> str:
    if not w:
        return "—"
    return f"{_num(w['lots'])}<span class='dim'> {w['pct']:+.1f}%量</span>" if w["pct"] is not None else _num(w["lots"])


def _boll(r: dict) -> str:
    b = r.get("boll")
    if not b:
        return "<span class='dim'>日 K 不足</span>"
    arrow = "<span class='up'>月線↑</span>" if b["ma_dir"] == "up" else "<span class='dn'>月線↓</span>"
    pos = "高檔" if b["upper_half"] else "低檔"
    extra = ""
    if r.get("bias_year") is not None:
        extra = f"<div class='dim'>乖離年線 {r['bias_year']:+.0f}%</div>"
    return (f"<div>帶寬 {b['bw_pct']:.1f}%（{esc(b['bw_level'])}）</div><div>{arrow} {b['ma_slope_pct']:+.2f}%</div>"
            f"<div class='dim'>{_ZONE.get(b['zone'], '')} · {pos}</div>{extra}")


def _streak(r: dict) -> str:
    s = r.get("streak2")
    if not s:
        return ""
    prev = s.get("prev_conc_pct")
    lab = "連續買超" if s.get("buy") else "連續賣超" if s.get("sell") else ""
    out = f"<div class='dim'>前一日 {prev:+.1f}%</div>" if prev is not None else ""
    return (f"<div><span class='fl {'buy' if s.get('buy') else 'sell'}'>{lab}</span></div>" if lab else "") + out


def _seats(items: list[dict], habit: list[dict] | None = None) -> str:
    hab = {h["name"]: h for h in habit or []}
    out = []
    for s in items:
        extra = ""
        if s.get("named"):
            extra += f" <span class='role'>小哥點名·{esc(s['named'])}</span>"
        h = hab.get(s["name"])
        if h and h["events"]:
            extra += f" <span class='hab' title='這檔股票近期：大買 {h['events']} 次、隔日沖 {h['flips']} 次'>隔日沖 {h['flips']}/{h['events']}</span>"
        out.append(f"<div>{esc(s['name'])} {s['lots']:+,}{extra}</div>")
    return "".join(out) or "—"


def _named_cell(r: dict) -> str:
    items = r.get("named") or []
    if not items:
        return "—"
    out = []
    for n in items:
        lots = "<span class='dim'>未入前段名單</span>" if n["lots"] is None else _num(n["lots"])
        out.append(f"<div>{esc(n['name'])} <span class='role'>{esc(n.get('role') or '')}</span> {lots}</div>")
    return "".join(out)


def _rules_cell(r: dict) -> str:
    return "".join(f"<span class='fl {esc(x['side'])}' title='{esc(x['id'])}'>{esc(x['label'])}</span>" for x in r.get("rules") or []) or "—"


def _next(r: dict) -> str:
    nx = r.get("next")
    if not nx:
        return "<span class='pend'>待下一交易日收盤</span>" if r.get("close") is not None else "—"
    return (f"<b>{_num(nx['ret_pct'], '{:+.2f}', '%')}</b><div class='dim'>{esc(_d(nx['date']))} 跳空 {_num(nx['gap_pct'], '{:+.2f}', '%')}"
            f" · 盤中 {_num(nx['intraday_pct'], '{:+.2f}', '%')}</div>")


def _row(r: dict) -> str:
    name = f"<b>{esc(r['sid'])}</b> {esc(r['name'])}"
    price = ""
    if r.get("close") is not None:
        price = f"<div class='dim'>{r['close']:g} <span class='{_cls(r.get('chg_pct'))}'>{r['chg_pct']:+.2f}%</span></div>" if r.get("chg_pct") is not None else f"<div class='dim'>{r['close']:g}</div>"
    it = r.get("it") or {}
    if r.get("status") != "ok":
        why = {"tape_incomplete": "分點最新日未完整", "stale_price": "資料日無日 K"}.get(r.get("status"), "無價")
        return (f"<tr><td class='l'><a href='/stock?sid={esc(r['sid'])}'>{name}</a>{price}</td><td class='l'>{_boll(r)}</td><td colspan='3' class='l dim'>{why}</td>"
                f"<td>{_it(it.get('d1'))}<div class='dim'>5d {_it(it.get('d5'))}</div></td><td colspan='4'></td><td>{_next(r)}</td></tr>")
    itcell = f"{_it(it.get('d1'))}<div class='dim'>5d {_it(it.get('d5'))}</div><div class='dim'>20d {_it(it.get('d20'))}</div>"
    if r.get("it_missing"):
        itcell += "<div class='dim'>缺最新日</div>"
    mode = "<div class='dim' title='FinMind 停用後分點只剩買超/賣超前段名單；主力日張數相同，集中度除以成交量'>前段分點口徑</div>" if r.get("tape_mode") == "top" else ""
    return (f"<tr><td class='l'><a href='/stock?sid={esc(r['sid'])}'>{name}</a>{price}</td><td class='l'>{_boll(r)}</td>"
            f"<td>{_cell_w(_w(r, 1))}{_streak(r)}{mode}</td>"
            f"<td>5d {_cell_w(_w(r, 5))}<div style='height:3px'></div>10d {_cell_w(_w(r, 10))}<div style='height:3px'></div>20d {_cell_w(_w(r, 20))}</td>"
            f"<td>{itcell}</td><td class='l'>{_seats(r['buyers'], r.get('habit'))}</td><td class='l'>{_seats(r['sellers'])}</td>"
            f"<td class='l'>{_named_cell(r)}</td><td class='l'>{_rules_cell(r)}</td><td>{_next(r)}</td></tr>")


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
    """(資料日, 前一交易日, 下一交易日)：有快照日曆就照讀。下一交易日只在已有隔日資料時才顯示。"""
    if cal:
        has_next = any(r.get("next") for r in rows)
        return cal.get("asof"), cal.get("prev"), cal.get("next") if has_next else None
    return (asof or _mode(r.get("date") for r in rows), None, _mode((r.get("next") or {}).get("date") for r in rows))


def render_table(rows: list[dict], asof: str | None = None, cal: dict | None = None, params: dict | None = None) -> str:
    p = params or {}
    d0, prev, nxt = _cal_dates(rows, cal, asof)
    nx_head = f"{_d(nxt)} 收盤漲跌<br>對 {_md(d0)} 收盤 · 跳空 · 盤中" if nxt else f"下一交易日收盤漲跌<br>（尚未收盤）· 對 {_md(d0)} 收盤"
    head = (f"<tr><th class='l'>代號名稱</th><th class='l'>位置（布林）<br>帶寬 / 月線 / 區位</th><th>主力 {_md(d0)}<br>集中度 / 張 · 連續</th>"
            f"<th>主力 5 / 10 / 20 日<br>集中度 / 張</th><th>投信 {_md(d0)} / 5d / 20d<br>張 · 佔量</th>"
            f"<th class='l'>買方前{_g(p.get('seats_shown'))}（張）<br>· 隔日沖紀錄（該股）</th><th class='l'>賣方前{_g(p.get('seats_shown'))}（張）</th>"
            f"<th class='l'>小哥點名分點<br>（該股，今日淨張）</th><th class='l'>小哥規則亮燈</th><th>{nx_head}</th></tr>")
    body = []
    base = _next_stats(rows)
    if base:
        body.append(f"<tr><td class='grp' colspan='10'>全體基準 · {len(rows)} 檔{_group_summary(rows)}<span class='gsum'>各組請對照此列，扣掉大盤當天漲跌</span></td></tr>")
    for key, label in _groups(p):
        grp = [r for r in rows if _group_of(r) == key]
        if not grp:
            continue
        if key == "sell":
            grp.sort(key=lambda r: r["day"]["conc_pct"])
        elif key in ("buy", "neutral"):
            grp.sort(key=lambda r: -abs(r["day"]["conc_pct"]))
        body.append(f"<tr><td class='grp' colspan='10'>{label} · {len(grp)} 檔{_group_summary(grp, base)}</td></tr>")
        body.extend(_row(r) for r in grp)
    return f"<table class='cz'>{head}{''.join(body)}</table>"


NOTE = """<details class='note'><summary>怎麼讀（小哥版 SOP）</summary>
<p><b>主力</b>＝分點前{top_n}買超＋前{top_n}賣超（張）；集中度％＝主力 ÷ 成交量（小哥畫面：買超前15合計＋賣超前15合計，÷區間成交量）。窗口 {windows} 日（軟體欄位主1／主5／主10、籌碼K線20日籌碼集中）。
分組：當日集中度 ≥ +{band_pct}%（V6：至少 10%）為買方集中、&lt; −{band_pct}% 為強賣。<b>連續</b>＝連續 {streak_days} 個交易日都達門檻。</p>
<p><b>位置（布林通道）</b>：中軌＝20日均線（月線），上下軌 ±2σ；帶寬＝上軌÷下軌−1：&lt;{bw_very_narrow_pct}% 非常窄、≤{bw_narrow_pct}% 窄、{bw_narrow_pct}–{bw_wide_pct}% 正常、≥{bw_wide_pct}% 寬。
月線方向＝今日月線 vs 昨日月線。高檔＝收盤在月線之上、低檔＝在月線之下。每條規則亮燈都帶位置條件（小哥：先分多空、再從那一格挑招）。</p>
<p><b>規則亮燈</b>（滑鼠移上去看書的規則編號）：R03 開布林＋主力大買（昨日帶寬窄、今日帶寬擴大且上軌上彎、收盤在月線上，為操作化）；R14 大帶寬觸下軌＋主力買；R24 主力連續買超（低檔更重）；
R41 主力連續賣超；R22 主投同買／同賣，主賣投買不算；R11／R12 高檔出貨池（月線下彎＋主力賣＋大帶寬，或乖離年線 &gt;{high_out_bias_pct}%）、R13 反彈到月線上＝逃命點；R16a 短線空頭；
R21c 高檔大分點狂賣（賣超第一名淨賣 ≥ 當日量 {big_sell_share_pct}%，書未給數字，沿用大買事件門檻）；R21b 隔日沖慣犯大買；S17 隔日空候選（高檔出貨池＋收紅＋慣犯大買）；R27 量小（20日均量 &lt; {thin_lots} 張）。</p>
<p><b>隔日沖（逐股看紀錄）</b>：今日該股買超前{habit_topn}且 ≥{habit_minsh_pct}% 量的分點，查它在<b>這檔股票</b>近 {habit_days} 日的大買事件，隔日賣出 ≥{habit_alpha} 倍淨買算一次隔日沖，顯示「次數／事件」。
不給分點貼全域標籤（小哥：同分點在不同股不通用）。與 /inventory 的 M07／M09 共用同一支程式。全市場檢定：慣犯隔天確實更常賣，但「慣犯大買→隔天黑 K」的價格面不成立（見 flip-habit-study-results）。</p>
<p><b>小哥點名分點</b>：書與直播點名過這檔股票的分點（關鍵分點、公司派、地緣、高手…；來源 key-branches.json），顯示今日淨張數；「未入前段名單」＝不在買超／賣超前 {top_n} 名，不是 0。
S17 的「大股東賣」只認小哥點名的公司派分點。</p>
<p><b>前段分點口徑（2026-10-17 起）</b>：FinMind 停用後，分點來源改為富邦行情站，每檔只有買超前{top_n}與賣超前{top_n}名。主力日張數與全分點版相同，集中度除以當日成交量；期間版為「逐日主力加總 ÷ 窗內成交量」；
完整日＝買超與賣超分點各 ≥{top_min_side} 個。</p>
<p><b>書有、我們沒資料的</b>：{gaps}</p>
<p><b>{next} 收盤漲跌</b>＝{next} 收盤對 {d0} 收盤（跳空＝{next} 開盤對 {d0} 收盤、盤中＝{next} 開盤到收盤），資料入庫後由排程補上；各組平均僅描述，
41檔×129天檢定顯示 SOP 欄位對下一交易日收盤無獨立預測力（chips-sop-next-close-verdict-20261013）。</p></details>"""


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


def _rules_alert(rows: list[dict], d0: str | None) -> str:
    """頁首：今天有哪些規則亮燈（依規則編號彙整股票），一眼看今天該看誰。"""
    by: dict[str, list[str]] = {}
    label: dict[str, str] = {}
    for r in rows:
        for x in r.get("rules") or []:
            by.setdefault(x["id"], []).append(f"{r['sid']} {r['name']}")
            label.setdefault(x["id"], x["label"].split("·")[0].split("（")[0])
    if not by:
        return f"<div class='fresh'>小哥規則亮燈：{_md(d0)} 無</div>"
    lines = "".join(f"<div><b>{esc(rid)} {esc(label[rid])} {len(v)} 檔</b>：{esc('、'.join(v))}</div>" for rid, v in sorted(by.items()))
    return f"<div class='fresh'>小哥規則亮燈 {_md(d0)}{lines}</div>"


def _all_dates_page(dates: list[str]) -> str:
    return "<div class='nav'>" + " ".join(f"<a href='/chips?date={d}'>{d}</a>" for d in reversed(dates)) + "</div>"


def render_csv() -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["asof", "sid", "name", "status", "signal", "close", "chg_pct", "conc_1d", "main_lots_1d", "conc_5d", "conc_10d", "conc_20d", "streak_buy", "streak_sell",
                "bw_pct", "bw_level", "ma_dir", "zone", "it_5d_pct", "rules", "next_date", "next_ret_pct", "next_gap_pct", "next_intraday_pct"])
    for d in snapshot_dates():
        data = _load(d) or {"rows": []}
        if (data.get("schema") or 0) < SCHEMA_MIN:
            continue
        for r in data["rows"]:
            nx, b, s2 = r.get("next") or {}, r.get("boll") or {}, r.get("streak2") or {}
            win = r.get("win") or {}
            g = lambda n, k: (win.get(str(n)) or {}).get(k)  # noqa: E731
            w.writerow([d, r["sid"], r["name"], r.get("status"), r.get("signal", ""), r.get("close"), r.get("chg_pct"), g(1, "conc_pct"), g(1, "main_lots"),
                        g(5, "conc_pct"), g(10, "conc_pct"), g(20, "conc_pct"), int(bool(s2.get("buy"))), int(bool(s2.get("sell"))),
                        b.get("bw_pct"), b.get("bw_level"), b.get("ma_dir"), b.get("zone"), ((r.get("it") or {}).get("d5") or {}).get("pct"),
                        "|".join(x["id"] for x in r.get("rules", [])),
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
    elif (data.get("schema") or 0) < SCHEMA_MIN:
        fresh = _date_nav(dates, str(data["asof"]))
        body = (f"<div class='alert'>{esc(str(data['asof']))} 是舊口徑快照（schema {data.get('schema')}；2026-10-21 起改為小哥版，欄位不同）。"
                "重建：<code>chip_sop.py --backfill START END --force</code></div>")
    else:
        warns = "".join(f"<div>⚠ {esc(w)}</div>" for w in data.get("warnings", []))
        cls = "fresh bad" if warns else "fresh"
        fresh = (f"{_date_nav(dates, str(data['asof']))}<div class='{cls}'>資料日 {esc(str(data['asof']))}（{esc(_d(data['asof'])[-2:-1])}） · 產生於 {esc(str(data['generated_at']))}"
                 f"{' · 事後補算（只用 ≤ 資料日的資料）' if (data.get('source') == 'backfill' or data.get('backfilled')) else ''}{warns}</div>"
                 f"{_rules_alert(data['rows'], str(data['asof']))}")
        try:
            rows, cal = data["rows"], data.get("calendar")
            _, prev, nxt = _cal_dates(rows, cal, str(data["asof"]))
            params = data.get("params") or {}
            params = {**params, "windows": "／".join(str(x) for x in params.get("windows", [])), "gaps": "；".join(data.get("gaps", []))}
            note = _fill(NOTE, params).replace("{d0}", _md(data["asof"])).replace("{next}", _d(nxt) if nxt else "下一交易日")
            body = note + render_table(rows, str(data["asof"]), cal, params)
        except Exception as exc:  # noqa: BLE001
            body = f"<div class='note'>渲染失敗：{esc(repr(exc))}</div>"
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>籌碼 SOP</title>{biglot_dashboard.ARC_CSS}{CSS}</head><body>"
            f"<div class='meta'><a href='/'>← 大戶儀表板</a> ｜ <b>籌碼 SOP</b></div>{fresh}{body}</body></html>")
