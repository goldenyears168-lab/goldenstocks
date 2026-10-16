"""期交所(TAIFEX)公開頁取數與解析 —— 取代 FinMind 的期貨類資料集(2026-10 起不再續約 FinMind)。

目前提供:
  * fetch_futures_institutional(day, futures_id)  三大法人-區分各期貨契約(成交口數 + 未平倉口數)
  * fetch_market_oi_total(day, commodity_id)      期貨每日交易行情『小計』列的全月份未沖銷契約量

頁面是 UTF-8 的伺服器端表格,不是正式 API,格式改版時解析會回 None(呼叫端須視為『無資料』而不是 0)。
請求之間請自行節流(建議 >= 1 秒)。
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from datetime import date

BASE = "https://www.taifex.com.tw/cht/3/"
USER_AGENT = "Mozilla/5.0 (goldenstocks research)"

# FinMind futures_id → 期交所三大法人表中的商品名稱
FUTURES_LABELS = {
    "TX": "臺股期貨",
    "TE": "電子期貨",
    "TF": "金融期貨",
    "MTX": "小型臺指期貨",
    "TMF": "微型臺指期貨",
}
# 表內身份別 → 與 FinMind 既有資料一致的名稱
IDENTITIES = (("自營商", "自營商"), ("投信", "投信"), ("外資", "外資"))

_NUM = r"-?[\d,]+"
_ROW_TMPL = r"%s\|自營商\|((?:%s\|){12})投信\|((?:%s\|){12})外資\|((?:%s\|){12})"


def flatten(raw: str) -> str:
    """HTML → 以 | 分隔的純文字(先解碼實體再壓縮空白,&nbsp; 才會被壓掉)。"""
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "|", raw)))
    return re.sub(r"(\|\s*)+", "|", t)


def _post(page: str, data: dict, timeout: float = 40.0) -> str:
    req = urllib.request.Request(
        BASE + page,
        data=urllib.parse.urlencode(data).encode(),
        method="POST",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return flatten(r.read().decode("utf-8", errors="ignore"))


def _ints(s: str) -> list[int]:
    return [int(x.replace(",", "")) for x in s.strip("|").split("|")]


def parse_futures_institutional(text: str, futures_id: str, trade_date: str) -> list[dict] | None:
    """從三大法人表的純文字取出某商品三個身份別;找不到(休市日/格式改版)回 None。

    每個身份別 12 個數字 = 6 組(口數,契約金額):成交多/空/淨、未平倉多/空/淨。
    """
    label = FUTURES_LABELS.get(futures_id)
    if label is None:
        raise KeyError(f"未知的期貨代號 {futures_id!r};請先加入 FUTURES_LABELS")
    m = re.search(_ROW_TMPL % (re.escape(label), _NUM, _NUM, _NUM), text)
    if not m:
        return None
    rows = []
    for (_, name), grp in zip(IDENTITIES, m.groups()):
        v = _ints(grp)
        rows.append(
            {
                "futures_id": futures_id,
                "trade_date": trade_date,
                "inst_name": name,
                "long_oi_vol": float(v[6]),
                "short_oi_vol": float(v[8]),
                "net_oi_vol": float(v[10]),
                "long_deal_vol": float(v[0]),
                "short_deal_vol": float(v[2]),
                "net_deal_vol": float(v[4]),
                "source": "taifex",
            }
        )
    return rows


def fetch_futures_institutional(day: date, futures_id: str = "TX") -> list[dict] | None:
    text = _post(
        "futContractsDate",
        {"queryType": "1", "goDay": "", "doQuery": "1", "dateaddcnt": "",
         "queryDate": day.strftime("%Y/%m/%d"), "commodityId": ""},
    )
    return parse_futures_institutional(text, futures_id, day.isoformat())


def parse_market_oi_total(text: str, commodity_id: str) -> int | None:
    """期貨每日交易行情頁:取 `( <ID> ) 行情表` 之後第一個『小計:』列的未沖銷契約量(第 4 個數字)。"""
    i = text.find(f"( {commodity_id} ) 行情表")
    if i < 0:
        return None
    seg = text[i:]
    j = seg.find("小計:|")
    if j < 0:
        return None
    nums = re.findall(_NUM, seg[j + 4:j + 60])
    return int(nums[3].replace(",", "")) if len(nums) >= 4 else None


def fetch_market_oi_total(day: date, commodity_id: str = "MTX") -> int | None:
    text = _post(
        "futDailyMarketReport",
        {"queryType": "2", "marketCode": "0", "dateaddcnt": "", "commodity_id": commodity_id,
         "commodity_id2": "", "queryDate": day.strftime("%Y/%m/%d"), "MarketCode": "0",
         "commodity_idt": commodity_id},
    )
    return parse_market_oi_total(text, commodity_id)
