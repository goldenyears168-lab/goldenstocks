"""biglot dashboard 重構：期散（小型契約 1 口成交 = 期貨市場散戶代理；jack 2026-09-25
交辦）——增量讀 tick 落地檔累積 deque（`_ingest_mini_fut`）、算近 5/30 分淨額與占比
（`_mini_stats`）、渲染詳情頁單一儲存格（`_mini_td`）。描述性、不進分數。

依 docs/biglot-refactor-roadmap.md 訂下的規則搬移：只 `import biglot_dashboard`
（模組本身，不指名字），函式本體內對 `biglot_dashboard.py` 自己定義的全域一律用
`biglot_dashboard.NAME` 屬性存取（`MINI_ROOT`/`MINI_ST`/`FUT_MINI`）——包含
`_deque_mini`：它在 `biglot_dashboard.py` 是 `from collections import deque as
_deque_mini` 這行 import 綁定出來的模組層級名字，不是這個模組自己定義的東西，
但既然它是 `biglot_dashboard.py` 命名空間裡的一個名字，一律走同一套屬性存取規則，
用 `biglot_dashboard._deque_mini(...)`，不特例判斷「這是不是危險全域」。`MINI_ST`
本身是原地 `.update()`/`[...]=` 修改、不是整包 `global MINI_ST; MINI_ST = ...`
重新賦值，但同一個理由：屬性存取不比 bare name 貴，一律套同一個安全模式最不容易
出錯，不逐一分類。`json`/`time` 是標準庫，直接匯入；`html` 一樣直接匯入成
`html_mod`（與 `biglot_dashboard.py` 用同一個別名習慣）。`DATA_DIR` 來自
`stock_db`、不是 `biglot_dashboard.py` 自己定義的全域，直接 `from stock_db import
DATA_DIR` 匯入。

參見 scripts/research/biglot/xq_style.py 開頭的完整說明（同一套規則第一次
被端到端驗證的地方）。
"""
from __future__ import annotations

import html as html_mod
import json
import time

from stock_db import DATA_DIR

import biglot_dashboard


def _ingest_mini_fut(today: str) -> None:
    """增量讀 {root}_trades_{today}.jsonl(期貨簿收集器落地,交易所 µs 時戳),只留 FUT_MINI 檔;主動方向以該筆 bid/ask 判。"""
    if biglot_dashboard.MINI_ST["day"] != today:
        biglot_dashboard.MINI_ST.update({"day": today, "off": {}, "q": {}})
    for sid, root in biglot_dashboard.MINI_ROOT.items():
        f = DATA_DIR.parent / "cache" / f"{root}_trades" / f"{root}_trades_{today}.jsonl"
        if not f.exists():
            continue
        try:
            with open(f, "rb") as fh:
                fh.seek(biglot_dashboard.MINI_ST["off"].get(root, 0)); chunk = fh.read()
            nl = chunk.rfind(b"\n")
            if nl == -1:
                continue
            biglot_dashboard.MINI_ST["off"][root] = biglot_dashboard.MINI_ST["off"].get(root, 0) + nl + 1
            q = biglot_dashboard.MINI_ST["q"].setdefault(sid, biglot_dashboard._deque_mini())
            for line in chunk[:nl].split(b"\n"):
                try:
                    o = json.loads(line)
                    if o.get("stale") or o.get("quote_type") not in (None, "FUTURE"):
                        continue
                    px, sz, b, a = float(o["price"]), float(o["size"]), o.get("bid"), o.get("ask")
                    sgn = 1 if (a is not None and px >= float(a)) else (-1 if (b is not None and px <= float(b)) else 0)
                    ts = float(o["trade_time"]) / 1e6
                    q.append((ts, px, sz, sgn, px * sz * 100))          # 小型契約 1 口 = 100 股
                except Exception:  # noqa: BLE001
                    continue
            cut = time.time() - 7200
            while q and q[0][0] < cut:
                q.popleft()
        except Exception:  # noqa: BLE001
            continue


def _mini_stats(sid: str, nts: float) -> dict | None:
    q = biglot_dashboard.MINI_ST["q"].get(sid)
    if not q:
        return None
    out = {}
    for lab, win in (("5", 300), ("30", 1800)):
        t0 = nts - win; n1 = a1 = tot = 0.0; nb = ns = 0
        for ts, px, sz, sgn, amt in q:
            if ts <= t0: continue
            tot += amt
            if sz == 1 and sgn:
                n1 += sgn * amt; a1 += amt; nb += (sgn > 0); ns += (sgn < 0)
        out[f"net{lab}"] = n1; out[f"share{lab}"] = (a1 / tot * 100) if tot > 0 else None; out[f"n{lab}"] = (nb, ns); out[f"tot{lab}"] = tot
    return out


def _mini_td(r) -> str:
    if r["sid"] not in biglot_dashboard.FUT_MINI:
        return "<td class='dim'>—</td>"
    m = r.get("mini")
    if not m or m.get("tot30", 0) <= 0:
        return "<td class='dim' title='期散:小型契約 1 口成交(期貨散戶代理);今日尚無小型成交'>無</td>"
    net, sh, (nb, ns) = m["net30"], m["share30"], m["n30"]
    cls = "up" if net > 0 else ("dn" if net < 0 else "dim")
    tip = (f"期散(描述性,不進分數):小型契約單筆 1 口(1 口=100 股≈{r.get('px') or 0:.0f}×100 元)的主動買−主動賣淨額,期貨散戶代理。"
           f"近30分 淨 {net/1e4:+,.0f} 萬(主動買 {nb} 筆/主動賣 {ns} 筆)·1 口成交占全部小型成交 {sh:.0f}%;近5分 淨 {m['net5']/1e4:+,.0f} 萬。"
           "⚠ 與現股散戶(1 張<500 萬)是不同母體;小型契約有造市商對敲,主動簽號只能濾掉一部分;累 20 日後與可測檔對照再決定用途")
    return (f"<td class='{cls}' title='{html_mod.escape(tip, quote=True)}'>{net/1e4:+,.0f}"
            f"<span class='dim' style='font-size:9px'> {sh:.0f}%·{nb}/{ns}</span></td>")
