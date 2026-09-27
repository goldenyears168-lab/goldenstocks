"""biglot dashboard 重構：/history 與 /day 兩個歷史分頁 render 函式 + 其相依的 MOPS
發言日 helper（見 docs/biglot-refactor-roadmap.md「後續要做的（下次對話）」段落）。

跟 biglot/xq_style.py 同一條規則：模組頂層只 `import biglot_dashboard`（不
`from biglot_dashboard import X`），函式本體內用 `biglot_dashboard.X` 屬性
存取 biglot_dashboard.py 自己定義的全域——這樣不會有 stale reference，也不
會有循環 import 在載入期就炸掉。

`.now()` 一律走 `biglot_dashboard.datetime.now(...)`，不直接
`from datetime import datetime`（測試harness 凍結時鐘是掛在
`biglot_dashboard.datetime` 上，不是掛在這個新模組自己 import 的物件上）。

其他模組的名字（`biglot.reference_loaders._load_snap`/`_snap_dates`、
`biglot.scoring_support._day_sig_counts`、`stock_db.DATA_DIR`）直接 import，
不透過 biglot_dashboard 轉手。函式本體與 docstring 逐字複製，不改一行邏輯。
"""
from __future__ import annotations

import html as html_mod
import json

from stock_db import DATA_DIR

import biglot_dashboard
from biglot.reference_loaders import _load_snap, _snap_dates
from biglot.scoring_support import _day_sig_counts


def _mops_load(day: str) -> dict:
    """MOPS 重大訊息 sid → [(hh:mm, 主旨)]。來源 ${DATA_DIR}/cache/mops_today_{發言日}.json(fetch_mops_today.py,
    TWSE/TPEx OpenAPI 每日快照:發言日=前一營業日;盤中即時 MOPS 尚無來源)。無檔回 None(=未抓,非無訊息)。"""
    f = DATA_DIR / "cache" / f"mops_today_{day}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _prev_mops_day(today: str) -> tuple[str, dict] | tuple[None, None]:
    """往回找最近一個有檔的發言日(≤7 天)。"""
    from datetime import date as _d, timedelta as _td
    d0 = _d.fromisoformat(today)
    for k in range(1, 8):
        day = (d0 - _td(days=k)).isoformat(); m = _mops_load(day)
        if m is not None:
            return day, m
    return None, None


def snapshot_day():
    """收盤後存當日 EOD 快照(冪等)。"""
    today = biglot_dashboard.ST.date
    if not today:
        return
    f = biglot_dashboard.SNAP_DIR / f"eod_{today}.json"
    if f.exists() or not biglot_dashboard.ST.day:
        return
    sig = _day_sig_counts()
    rows = []
    for sid in biglot_dashboard.NAMES:
        ds = biglot_dashboard.ST.day.get(sid)
        px = biglot_dashboard.ST.last_px.get(sid)
        if not ds or px is None:
            continue
        s1, s2 = sig.get(sid, (0, 0))
        m = biglot_dashboard.ST.buckets.get(sid, {})
        pm_px = [m[bk]["px"] for bk in m if bk.hour >= 12 and m[bk]["px"]]
        ret_open_sh = (ds["ret_open"] / ds["tot_open"] * 100) if ds["tot_open"] else None
        ret_close_sh = (ds["ret_close"] / ds["tot_close"] * 100) if ds["tot_close"] else None
        rows.append({"sid": sid, "name": biglot_dashboard.NAMES[sid], "close": px, "px0": ds["px0"],
                     "pm_low": min(pm_px) if len(pm_px) >= 3 else None,
                     "big": ds["big"], "ret2": ds["ret2"], "retn": ds["ret"], "tot": ds["tot"],
                     "sig1": s1, "sig2": s2,
                     "ret_open_sh": ret_open_sh, "ret_close_sh": ret_close_sh,          # 散戶版SMFI觀察欄(2026-09-25)
                     "ret_smfi": ((ret_close_sh - ret_open_sh) if (ret_open_sh is not None and ret_close_sh is not None) else None)})
    if len(rows) >= 20:                      # 資料太少不存(避免半天斷線垃圾)
        json.dump({"date": today, "rows": rows}, open(f, "w"))


def render_history():
    ds = _snap_dates()
    lis = "".join(f"<li><a href='/day?d={d}'>{d}</a></li>" for d in reversed(ds))
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=0.8'>"
            f"<title>歷史分頁</title>{biglot_dashboard.ARC_CSS}</head><body>"
            f"<h3>每日收盤快照 <a href='/'>←即時</a></h3><ul>{lis or '<li>尚無</li>'}</ul>"
            f"<div class='meta'>每頁最後兩欄=次日漲跌/次日排名(次日收盤後自動補上)</div></body></html>")


def render_day(d):
    snap = _load_snap(d)
    if not snap:
        return f"<!DOCTYPE html><html><head>{biglot_dashboard.ARC_CSS}</head><body>無 {html_mod.escape(d)} 快照 <a href='/history'>返回</a></body></html>"
    ds_all = _snap_dates()
    i = ds_all.index(d) if d in ds_all else -1
    prev = _load_snap(ds_all[i - 1]) if i > 0 else None
    nxt = _load_snap(ds_all[i + 1]) if 0 <= i < len(ds_all) - 1 else None
    pc = {r["sid"]: r["close"] for r in prev["rows"]} if prev else {}
    nc = {r["sid"]: r["close"] for r in nxt["rows"]} if nxt else {}
    no = {r["sid"]: r.get("px0") for r in nxt["rows"]} if nxt else {}
    rows = []
    for r in snap["rows"]:
        base = pc.get(r["sid"]) or r["px0"]
        r["dret"] = (r["close"] / base - 1) * 100 if base else None
        n = nc.get(r["sid"])
        r["nret"] = (n / r["close"] - 1) * 100 if (n and r["close"]) else None
        op = no.get(r["sid"])
        r["ngap"] = (op / r["close"] - 1) * 100 if (op and r["close"]) else None
        rows.append(r)
    rows.sort(key=lambda r: -(r["dret"] if r["dret"] is not None else -99))
    nrank = {r["sid"]: k + 1 for k, r in enumerate(
        sorted([r for r in rows if r["nret"] is not None], key=lambda r: -r["nret"]))}
    grank = {r["sid"]: k + 1 for k, r in enumerate(
        sorted([r for r in rows if r["ngap"] is not None], key=lambda r: -r["ngap"]))}
    trs = []
    for k, r in enumerate(rows, 1):
        def pct(v):
            if v is None:
                return "<td class='dim'>—</td>"
            return f"<td class='{'up' if v > 0 else 'dn' if v < 0 else ''}'>{v:+.2f}%</td>"
        unm = r["sid"] in biglot_dashboard.RET_UNM
        share = (r["ret2"] / r["tot"] * 100) if (r["tot"] and not unm) else None
        trs.append(
            f"<tr><td>{k}</td><td class='nm'>{r['sid']} {r['name']}</td>"
            f"<td>{r['close']:g}</td>" + pct(r["dret"])
            + f"<td class='{'up' if r['big'] > 0 else 'dn' if r['big'] < 0 else ''}'>{r['big'] / 1e8:+.2f}</td>"
            + (f"<td class='{'up' if r['big'] > 0 else 'dn'}'>{r['big'] / r['tot'] * 100:+.1f}%</td>"
               if r['tot'] else "<td class='dim'>—</td>")
            + (f"<td>{share:.1f}%</td>" if share is not None else "<td class='dim'>不可測</td>")
            + f"<td>{r['tot'] / 1e8:.1f}</td>"
            + f"<td>{r['sig1'] or ''}</td>"
            + f"<td>{r['sig2'] or ''}</td>"
        )
        # 次日兩欄
        if r["ngap"] is None:
            trs[-1] += "<td class='nx dim'>—</td><td class='nx dim'>—</td>"
        else:
            gcl = 'up' if r['ngap'] > 0 else 'dn' if r['ngap'] < 0 else ''
            gk = grank.get(r['sid'])
            gk_cl = ('top5' if gk and gk <= 5 else
                     'bot5' if gk and gk > len(grank) - 5 else '')
            trs[-1] += (f"<td class='nx {gcl}'>{r['ngap']:+.2f}%</td>"
                        f"<td class='nx {gk_cl}'>{gk or '—'}</td>")
        if r["nret"] is None:
            trs[-1] += "<td class='nx dim'>—</td><td class='nx dim'>—</td></tr>"
        else:
            cl = 'up' if r['nret'] > 0 else 'dn' if r['nret'] < 0 else ''
            rk = nrank.get(r['sid'])
            rk_cl = ('top5' if rk and rk <= 5 else
                     'bot5' if rk and rk > len(nrank) - 5 else '')
            trs[-1] += (f"<td class='nx {cl}'>{r['nret']:+.2f}%</td>"
                        f"<td class='nx {rk_cl}'>{rk or '—'}</td></tr>")
    nav_p = f"<a href='/day?d={ds_all[i-1]}'>←{ds_all[i-1]}</a>" if i > 0 else ""
    nav_n = f"<a href='/day?d={ds_all[i+1]}'>{ds_all[i+1]}→</a>" if 0 <= i < len(ds_all) - 1 else ""
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=0.7'>"
            f"<title>{d} 收盤</title>{biglot_dashboard.ARC_CSS}</head><body>"
            f"<h3>{d} 收盤快照 &nbsp;{nav_p} <a href='/history'>索引</a> {nav_n}</h3>"
            f"<div class='meta'>點欄位標題可排序(再點反向) · 預設=當日漲跌 · 漲跌基準=前一快照收盤(缺則用當日首價) · "
            f"💎欄=當日核心/強訊號觸發數 · 深底色兩欄=<b>次日</b>漲跌與排名(次日收盤自動補)</div>"
            f"<table><thead><tr><th>#</th><th class='stk'>股票</th><th>收盤</th><th>當日%</th>"
            f"<th>全日大戶(億)</th><th title='大戶淨流÷成交,127日驗證次日排名IC+0.043/t3.2=最佳排序鍵'>大戶佔比</th><th>散戶參與</th><th>成交(億)</th><th>💎</th><th>💎💎</th>"
            f"<th class='nx' title='次日開盤vs今收(隔夜跳空);127日:大戶佔比→開盤IC+0.097/t7.1=最可預測段'>次日開%</th><th class='nx'>開名</th><th class='nx' title='次日收對收;=開盤慣性−日內回吐的殘影'>次日%</th><th class='nx'>收名</th></tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table>"
            + biglot_dashboard.SORT_JS + "</body></html>")
