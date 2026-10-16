"""期交所(TAIFEX)公開頁取數與解析 —— 取代 FinMind 的期貨類資料集(2026-10 起不再續約 FinMind)。

目前提供:
  * fetch_futures_institutional(day, futures_id)  三大法人-區分各期貨契約(成交口數 + 未平倉口數)
  * fetch_market_oi_total(day, commodity_id)      期貨每日交易行情『小計』列的全月份未沖銷契約量
  * fetch_vix_ticks(day)                          臺指選擇權波動率指數(VIXTWN)當日 15 秒逐筆(日盤 09:00~13:45)
  * fetch_futures_daily(day, id, session)         期貨每日交易行情(各到期月):'position'=一般時段、'after_market'=盤後

頁面是 UTF-8 的伺服器端表格,不是正式 API,格式改版時解析會回 None(呼叫端須視為『無資料』而不是 0)。
請求之間請自行節流(建議 >= 1 秒)。
"""
from __future__ import annotations

import html
import re
import urllib.error
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


VIX_URL = "https://www.taifex.com.tw/cht/7/getVixData?filesname={d}"
VIX_ZIP_URL = ("https://www.taifex.com.tw/file/taifex/Dailydownload/vix/logdata/"
               "{ym}" + urllib.parse.quote("歷史紀錄波動率指數_新VIX.zip"))
# 期交所只保留『當月逐日檔 + 前 3 個月壓縮檔』;更早的就拿不到了 —— 所以下載當下一定要歸檔原始檔。


def parse_vix_ticks(raw: str, day: date) -> list[dict]:
    """解析 getVixData 逐日檔(tab 分隔;時間欄為 HMMSSff,例如 9001500 = 09:00:15.00)。

    只收數值列;最後一列 `Last 1 min AVG`(收盤前一分鐘平均)是統計列,不當逐筆。
    回傳依時間排序的 [{date, time:'HH:MM:SS', vix}],與 FinMind TaiwanOptionVix 的欄位同形。
    """
    ymd = day.strftime("%Y%m%d")
    out = []
    for line in raw.splitlines():
        p = line.split("\t")
        if len(p) < 5 or p[0].strip() != ymd:
            continue
        t = p[1].strip()
        if not t.isdigit():
            continue
        try:
            v = float(p[4])
        except ValueError:
            continue
        t8 = t.zfill(8)
        out.append({"date": day.isoformat(), "time": f"{t8[0:2]}:{t8[2:4]}:{t8[4:6]}", "vix": v})
    out.sort(key=lambda r: r["time"])
    return out


def _read(url: str, timeout: float) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError:
        return None


def _vix_from_zip(day: date, archive_dir, timeout: float) -> list[dict] | None:
    """當月以前的日期:從月壓縮檔取(下載後存在 archive_dir/zip,之後不再重抓)。"""
    import io
    import zipfile
    from pathlib import Path

    ym = day.strftime("%Y%m")
    zp = Path(archive_dir) / "zip" / f"{ym}.zip" if archive_dir else None
    blob = zp.read_bytes() if zp is not None and zp.exists() else None
    if blob is None:
        blob = _read(VIX_ZIP_URL.format(ym=ym), timeout)
        if blob is None or blob[:2] != b"PK":
            return None
        if zp is not None:
            zp.parent.mkdir(parents=True, exist_ok=True)
            zp.write_bytes(blob)
    try:
        raw = zipfile.ZipFile(io.BytesIO(blob)).read(f"tvix_{day.strftime('%Y%m%d')}").decode("big5", errors="ignore")
    except KeyError:
        return None
    return parse_vix_ticks(raw, day) or None


def fetch_vix_ticks(day: date, archive_dir=None, timeout: float = 40.0) -> list[dict] | None:
    """當日 VIXTWN 逐筆;休市日/來源已過期回 None。檔案是 Big5。

    順序:本機歸檔 → 期交所逐日檔(當月)→ 期交所月壓縮檔(前 3 個月)。
    `archive_dir` 給定時,下載到的原始檔會存下來(archive_dir/daily/YYYYMMDD.txt),永久保存。
    """
    from pathlib import Path

    ymd = day.strftime("%Y%m%d")
    local = Path(archive_dir) / "daily" / f"{ymd}.txt" if archive_dir else None
    if local is not None and local.exists():
        return parse_vix_ticks(local.read_bytes().decode("big5", errors="ignore"), day) or None
    blob = _read(VIX_URL.format(d=ymd), timeout)
    if blob is not None:
        ticks = parse_vix_ticks(blob.decode("big5", errors="ignore"), day)
        if ticks:
            if local is not None:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(blob)
            return ticks
    if day.strftime("%Y%m") < date.today().strftime("%Y%m"):
        return _vix_from_zip(day, archive_dir, timeout)
    return None


def _num(tok: str) -> float | None:
    tok = tok.strip().replace(",", "")
    return float(tok) if re.fullmatch(r"-?\d+(?:\.\d+)?", tok) else None


def parse_futures_daily(text: str, commodity_id: str, day: date, session: str) -> list[dict]:
    """解析『期貨每日交易行情』主表(各到期月一列),輸出與 FinMind TaiwanFuturesDaily 同形的欄位。

    session='position'(一般時段 08:45~13:45):欄位 開高低收、漲跌、漲跌%、盤後量、一般量、合計量、結算價、未沖銷…
    session='after_market'(盤後):開高低收、漲跌、漲跌%、成交量、結算價(-)、未沖銷(-)…
    盤後頁以『交易量歸屬日期』查詢:queryDate=D 的盤後 = D-1 日 15:00 ~ D 日 05:00,與 FinMind 的 date 語意相同。
    價差列(`202610/202611`)與週契約不收;沒有成交(收盤 '-')的列略過。
    """
    i = text.find(f"( {commodity_id} ) 行情表")
    if i < 0:
        return []
    seg = text[i:]
    j = seg.find("小計:|")
    seg = seg[: j if j > 0 else len(seg)]
    out = []
    row_start = re.compile(r"\|%s \|(\d{6}) \|" % re.escape(commodity_id))
    starts = list(row_start.finditer(seg))
    for k, m in enumerate(starts):                  # 依列起點切段(不能讓正規式吃到下一列的開頭)
        end = starts[k + 1].start() if k + 1 < len(starts) else len(seg)
        cells = seg[m.end():end].split("|")
        close = _num(cells[3])
        if not close:
            continue
        pct = re.search(r"-?[\d.]+", cells[5] or "")
        sign = -1.0 if "▼" in (cells[5] or "") else 1.0
        vol_idx = 7 if session == "position" else 6
        vol = _num(cells[vol_idx]) if len(cells) > vol_idx else None
        out.append(
            {
                "date": day.isoformat(),
                "futures_id": commodity_id,
                "contract_date": m.group(1),
                "open": _num(cells[0]),
                "max": _num(cells[1]),
                "min": _num(cells[2]),
                "close": close,
                "spread_per": sign * float(pct.group(0)) if pct else None,
                "volume": int(vol) if vol is not None else 0,
                "trading_session": session,
            }
        )
    return out


def fetch_futures_daily(day: date, commodity_id: str, session: str) -> list[dict]:
    mc = "0" if session == "position" else "1"
    text = _post(
        "futDailyMarketReport",
        {"queryType": "2", "marketCode": mc, "dateaddcnt": "", "commodity_id": commodity_id,
         "commodity_id2": "", "queryDate": day.strftime("%Y/%m/%d"), "MarketCode": mc,
         "commodity_idt": commodity_id},
    )
    return parse_futures_daily(text, commodity_id, day, session)
