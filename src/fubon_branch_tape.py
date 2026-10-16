"""富邦行情站(fubon-ebrokerdj)分點頁 → stock_broker_branch_daily 資料列(source='fubon')。

2026-10 起取代 FinMind `TaiwanStockTradingDailyReport`(Sponsor 不續約)。兩條取數路徑:

* 依個股頁  `zc/zco/zco.djhtm?a=<股票>&e=<日>&f=<日>`:買超前 15、賣超前 15 名分點,每列帶分點代號與買進/賣出張數(精確)。
* 依分點頁  `zg/zgb/zgb0.djhtm?a=<分點>&b=<hex分點>&c=E|B&e=<日>&f=<日>`:該分點買超/賣超各前約 50 名股票;
  c=E 依張數排名(張數精確)、c=B 依金額排名(單位仟元)。對專家池核心分點,金額排名的召回率 96.7%
  (淨額 ≥1 億 99%),見 scripts/research/validate_fubon_branch_tape.py。

限制(誠實版):只有『前段』,不是每檔股票全部約 800 個分點;小部位看不到。金額排名專用列的股數由
金額 ÷ 收盤價換算(±1~2%),淨金額則與官方一致。網頁不是正式 API,格式改版時解析會回空(視為無資料而非 0)。
請求間隔預設 1.2 秒。
"""
from __future__ import annotations

import html
import re
import time
import urllib.request
from collections.abc import Callable
from datetime import date

USER_AGENT = "Mozilla/5.0 (goldenstocks research)"
BRANCH_URL = ("https://fubon-ebrokerdj.fbs.com.tw/z/zg/zgb/zgb0.djhtm?"
              "a={b}&b={hexb}&c={kind}&e={d}&f={d}")
STOCK_URL = "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm?a={sid}&e={d}&f={d}"
SOURCE = "fubon"

_BRANCH_ROW = re.compile(
    r"GenLink2stk\('AS(\w+)','([^']*)'\);\s*//-->\s*</SCRIPT>\s*</td>\s*"
    r"<td[^>]*>([\d,]+)</td>\s*<td[^>]*>([\d,]+)</td>\s*<td[^>]*>(-?[\d,]+)</td>",
    re.S | re.I,
)
_STOCK_ROW = re.compile(
    r'<a href="[^"]*zco0\.djhtm\?a=\w+&b=([0-9A-Fa-f]+)(?:&BHID=\w+)?">([^<]+)</a></TD>\s*'
    r"<TD[^>]*>([\d,]+)</TD>\s*<TD[^>]*>([\d,]+)</TD>",
    re.I,
)


def hex_id(branch_id: str) -> str:
    """分點代號以 UTF-16BE 十六進位傳入,保留大小寫(585b ≠ 585B)。"""
    return "".join("%04X" % ord(c) for c in branch_id)


def decode_branch_id(token: str) -> str:
    """個股頁連結的 b= 參數:純數字代號原樣,英數混合代號是 UTF-16BE 十六進位(例 0039003200300046 → 920F)。"""
    if re.fullmatch(r"[0-9]{4}", token):
        return token
    if len(token) % 4 == 0 and re.fullmatch(r"[0-9A-Fa-f]+", token):
        try:
            s = "".join(chr(int(token[i:i + 4], 16)) for i in range(0, len(token), 4))
            if s.isascii() and s.isalnum():
                return s
        except ValueError:
            pass
    return token


def _int(tok: str) -> int:
    return int(tok.replace(",", ""))


def parse_branch_page(raw: str) -> dict[str, tuple[int, int]]:
    """依分點頁 → {股票: (買進, 賣出)}(c=E 為張、c=B 為仟元)。"""
    return {sid: (_int(b), _int(s)) for sid, _name, b, s, _net in _BRANCH_ROW.findall(raw)}


def parse_stock_page(raw: str) -> list[dict]:
    """依個股頁 → [{id, name, buy, sell}](張);買超 15 + 賣超 15 名,同一分點出現兩側時合併取較大者無意義,保留各列。"""
    out: dict[str, dict] = {}
    for token, name, b, s in _STOCK_ROW.findall(raw):
        bid = decode_branch_id(token)
        row = {"id": bid, "name": html.unescape(name).strip(), "buy": _int(b), "sell": _int(s)}
        out[bid] = row            # 兩側表格是不同分點;同一分點不會同時在買超與賣超前段
    return list(out.values())


def _http(url: str, timeout: float, retries: int = 3) -> str | None:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("big5", errors="ignore")
        except Exception as e:  # noqa: BLE001 — 網路暫時失敗:退避重試
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"富邦行情站請求失敗: {last}")


class FubonTape:
    """單次執行內的分點頁快取:對 N 檔股票只抓一次分點頁,每檔股票只多抓一頁個股頁。"""

    def __init__(self, branches: dict[str, str], *, delay: float = 1.2, timeout: float = 40.0,
                 http: Callable[[str, float], str | None] | None = None):
        self.branches = dict(branches)            # 分點代號 → 名稱(專家池核心分點)
        self.delay = delay
        self.timeout = timeout
        self._http = http or (lambda u, t: _http(u, t))
        self._bpages: dict[tuple[str, str, str], dict | None] = {}
        self._spages: dict[tuple[str, str], list[dict] | None] = {}

    def _sleep(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)

    def branch_page(self, branch_id: str, day: str, kind: str) -> dict | None:
        key = (branch_id, day, kind)
        if key not in self._bpages:
            raw = self._http(BRANCH_URL.format(b=branch_id, hexb=hex_id(branch_id), kind=kind, d=day), self.timeout)
            self._sleep()
            self._bpages[key] = parse_branch_page(raw) if raw else None
        return self._bpages[key]

    def stock_page(self, stock_id: str, day: str) -> list[dict] | None:
        key = (stock_id, day)
        if key not in self._spages:
            raw = self._http(STOCK_URL.format(sid=stock_id, d=day), self.timeout)
            self._sleep()
            self._spages[key] = parse_stock_page(raw) if raw else None
        return self._spages[key]

    def day_ready(self, day: str) -> bool:
        """富邦該日是否已有資料:任一專家分點頁有列,或探測用的大型股個股頁有列。"""
        for b in self.branches:
            pg = self.branch_page(b, day, "E")
            if pg:
                return True
        return bool(self.stock_page("2330", day))

    def rows_for_stock(self, stock_id: str, day: str, close: float | None) -> list[dict]:
        """某檔股票某日的分點資料列(source='fubon')。"""
        rows: dict[str, dict] = {}

        def put(tid: str, name: str, buy: float, sell: float) -> None:
            if not buy and not sell:
                return
            rows[tid] = {"trade_date": day, "securities_trader_id": tid, "securities_trader": name or tid,
                         "stock_id": stock_id, "buy": float(buy), "sell": float(sell),
                         "net": float(buy) - float(sell), "source": SOURCE}

        for r in self.stock_page(stock_id, day) or []:                      # 前 15 名買/賣超(精確張數)
            put(r["id"], r["name"], r["buy"] * 1000, r["sell"] * 1000)
        for b, name in self.branches.items():                               # 專家分點:優先精確張數,否則由金額換算
            if b in rows:
                continue
            e = (self.branch_page(b, day, "E") or {}).get(stock_id)
            if e:
                put(b, name, e[0] * 1000, e[1] * 1000)
                continue
            a = (self.branch_page(b, day, "B") or {}).get(stock_id)
            if a and close and close > 0:
                put(b, name, a[0] * 1000 / close, a[1] * 1000 / close)                   # 仟元 → 元(×1000) → 股(÷收盤價)
        return list(rows.values())
