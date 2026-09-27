"""biglot dashboard 重構：`_tx_panel` 讀 `TX_SER`/`FUT_PX`/`TX_LAST`/`TZ`/`datetime`，
全部是 `biglot_dashboard.py` 自己定義的模組級名字。`FUT_PX` 是
docs/biglot-refactor-roadmap.md 列出的「每日整包重新賦值」危險全域之一
（過日重載會用 `global FUT_PX; FUT_PX = {...}` 整包換掉物件），所以一律走屬性存取，
不能在檔案頂層 `from biglot_dashboard import FUT_PX`。`TX_SER`/`TX_LAST` 雖然是原地
mutate（`TX_SER["t"].append(...)`、`TX_LAST["z"] = z`）不是整包重新賦值，但為了跟
危險全域的存取方式保持一致、降低未來誤判風險，一律用同一套屬性存取寫法（比照
biglot/pe_and_shadow.py 對 `SHADOW`/`SUBCAT` 的處理）。

如果這裡跟 Phase 1 的 biglot/utils.py 一樣用 `from biglot_dashboard import FUT_PX`，
搬過來的名字只會抓到「當下那一刻」的參照，過日後 `biglot_dashboard.py` 自己那份
換了新物件，這裡卻還指著昨天的舊物件——這正是路線圖裡標注過的 stale-reference 風險。

`datetime` 也一樣：這支函式只用到 `datetime.fromisoformat`/`datetime.fromtimestamp`，
不是 `.now()`，理論上跟 wall-clock 無關，但仍統一走 `biglot_dashboard.datetime`
屬性存取（比照 biglot/trade_ingest.py 的處理），避免特例判斷、也避免將來有人在這支
模組裡加 `.now()` 呼叫時漏掉「必須走 biglot_dashboard.datetime」這條規則——
`from datetime import datetime` 直接匯入會繞過測試 harness 凍結時鐘的 monkey-patch
（這正是這次重構已經踩過一次的真 bug）。

正確做法（比照 biglot/xq_style.py）：只 `import biglot_dashboard`（模組本身，不指
名字），函式本體內用 `biglot_dashboard.TX_SER` 等屬性存取——這是屬性查找，每次呼叫
都會拿到當下 biglot_dashboard 模組裡最新的值，不管 ingest() 換過幾次日都不會過期。

`_agg_lines` 已在 Phase B 第二批搬進 `biglot/scoring_support.py`（零全域依賴的純
SVG 疊圖 helper），直接 `from biglot.scoring_support import _agg_lines` 匯入。
`json` 是標準庫，直接匯入；`bisect` 沿用原檔案寫法，維持函式本體內的 local import
（比照 biglot/utils.py、biglot/scoring_support.py 對 local import 的處理方式）。

函式本體與 docstring 逐字複製，不改一行邏輯。
"""
from __future__ import annotations

import json

import biglot_dashboard
from biglot.scoring_support import _agg_lines


def _tx_panel(now):
    """頂部右側:台指近月即時價 + 對昨結 + 5分/30分 bps + 1分 z(影子帳砍尾同口徑)+ 10 秒線 SVG。無資料回空字串。"""
    t, px = biglot_dashboard.TX_SER["t"], biglot_dashboard.TX_SER["px"]
    tx = biglot_dashboard.FUT_PX.get("TXF") if isinstance(biglot_dashboard.FUT_PX.get("TXF"), dict) else None
    if not t or not tx or not tx.get("px"):
        return ""
    last = float(tx["px"]); fpc = tx.get("fpc"); nts = t[-1]
    import bisect as _bs
    def _at(sec):
        i = _bs.bisect_right(t, nts - sec) - 1
        return px[i] if i >= 0 else None
    p5, p30 = _at(300), _at(1800)
    b5 = (last / p5 - 1) * 1e4 if p5 else None
    b30 = (last / p30 - 1) * 1e4 if p30 else None
    # 1 分 z:30 秒格點的 1 分報酬歷史 σ(與 zcrash_shadow 相同)
    z = None
    if len(t) > 12:
        r1 = []
        g = t[0] + 60
        while g <= nts:
            i = _bs.bisect_right(t, g) - 1; j = _bs.bisect_right(t, g - 60) - 1
            if i >= 0 and j >= 0 and px[j]:
                r1.append(px[i] / px[j] - 1)
            g += 30
        if len(r1) >= 10:
            m = sum(r1) / len(r1); sd = (sum((x - m) ** 2 for x in r1) / len(r1)) ** 0.5
            p60 = _at(60)
            if sd > 0 and p60:
                z = (last / p60 - 1) / sd
    chg = (last / fpc - 1) * 100 if fpc else None
    biglot_dashboard.TX_LAST["z"] = z
    cls = "up" if (chg or 0) > 0 else ("dn" if (chg or 0) < 0 else "")
    zcls = " style='background:#6e1a1a;color:#ffb3b3;padding:0 4px'" if (z is not None and z <= -1) else (
        " style='background:#1a4d2e;color:#b3ffcc;padding:0 4px'" if (z is not None and z >= 1) else "")
    # SVG:固定 08:45→13:45 時間軸,y 含昨結
    W, H, L, R = 470, 250, 4, 4          # 頂部說明文字隱藏後,圖高拉到 250
    t0 = biglot_dashboard.datetime.fromisoformat(f"{biglot_dashboard.TX_SER['day']}T08:45:00+08:00").timestamp(); t1 = t0 + 5 * 3600
    ys = px + ([fpc] if fpc else [])
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-9:
        hi = lo + 1
    def X(ts): return L + (ts - t0) / (t1 - t0) * (W - L - R)
    def Y(v): return 6 + (hi - v) / (hi - lo) * (H - 12)
    step = max(1, len(t) // 600)
    samp = list(zip(t, px))[::step]
    pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in samp)
    # hover 用:每個取樣點的 (x 像素, y 像素, 時間, 價),前端找最近 x 顯示
    hov = json.dumps([[round(X(a), 1), round(Y(b), 1), biglot_dashboard.datetime.fromtimestamp(a, biglot_dashboard.TZ).strftime("%H:%M:%S"), b] for a, b in samp])
    col = "#ff7b72" if (chg or 0) > 0 else "#3fb950"
    svg = (f"<svg width='{W}' height='{H}' style='display:block' data-pts='{hov}'>"
           + (f"<line x1='{L}' y1='{Y(fpc):.1f}' x2='{W-R}' y2='{Y(fpc):.1f}' stroke='#8b949e' stroke-dasharray='3,3'/>" if fpc else "")
           + f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='1.2'/>"
           + f"<circle cx='{X(nts):.1f}' cy='{Y(last):.1f}' r='2.5' fill='{col}'/>"
           + _agg_lines(t0, t1, W, H, L, R)
           + f"<text x='{L}' y='10' font-size='9' fill='#8b949e'>{hi:,.0f}</text>"
           + f"<text x='{L}' y='{H-1}' font-size='9' fill='#8b949e'>{lo:,.0f}</text></svg>")
    f = lambda v: f"{v:+.0f}" if v is not None else "—"  # noqa: E731
    fp = lambda v: f"{v/100:+.2f}%" if v is not None else "—"  # noqa: E731  # 統一用 %
    # 左文右圖:文字欄固定 200px 直排,圖吃剩餘寬度、高度拉滿
    left = (f"<div><b>台指近月</b> <span class='dim'>{tx.get('t', '')}</span></div>"
            f"<div><span class='{cls}' style='font-size:24px;font-weight:700'>{last:,.0f}</span></div>"
            + (f"<div class='{cls}'>{last - fpc:+,.0f} ({chg:+.2f}%) <span class='dim'>對昨結 {fpc:,.0f}</span></div>" if fpc else ""))
    if z is not None:
        left += (f"<div>5分 <b>{fp(b5)}</b> · 30分 <b>{fp(b30)}</b></div>"
                 f"<div>1分z <b{zcls}>{z:+.1f}</b>"
                 + (f" · 買{tx.get('bid')}/賣{tx.get('ask')}" if tx.get("bid") else "") + "</div>"
                 "<div class='dim' style='font-size:10px;line-height:1.3'>校準:z≤−1 紅=急殺做多砍尾中<br>z≥+1 綠=急拉做空砍尾中</div>")
    return (f"<div id='txsrc' hidden><div style='display:flex;gap:10px;align-items:stretch'>"
            f"<div style='flex:0 0 200px'>{left}</div><div style='flex:1 1 auto'>{svg}</div></div></div>")
