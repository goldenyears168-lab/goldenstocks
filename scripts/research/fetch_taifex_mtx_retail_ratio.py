#!/usr/bin/env python3
"""期交所官方公開頁 → 小型臺指(MTX)『三大法人未平倉』與『全市場未平倉』→ 日頻散戶多空比(只讀研究工具,不碰 DB 與下單層)。

散戶多空比 = −(三大法人多空未平倉淨額合計) ÷ 全市場未平倉(所有到期月合計);>0 代表散戶(三大法人的對手方)淨偏多。
這是市場通行的推算口徑(期交所本身沒有官方公布此指標),見 docs 預先登記附註。

輸出  <狀態根>/data/research/mtx_retail/mtx_retail_ratio.csv
來源  https://www.taifex.com.tw/cht/3/futContractsDate (三大法人-區分各期貨契約)
      https://www.taifex.com.tw/cht/3/futDailyMarketReport (期貨每日交易行情,小計列=全月份未沖銷契約量)
不吃 FinMind 額度;請求間隔 1.2 秒。
"""
import argparse
import csv
import datetime as dt
import html
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
try:
    from stock_db import DATA_DIR as _D
    STATE_ROOT = pathlib.Path(_D).parent
except Exception:
    STATE_ROOT = pathlib.Path(os.environ.get("GOLDENSTOCKS_DATA_DIR", os.path.expanduser("~/goldenstocks-data")))

BASE = "https://www.taifex.com.tw/cht/3/"
NUM = r"-?[\d,]+"
INST_RE = re.compile(
    r"小型臺指期貨\|自營商\|((?:%s\|){12})投信\|((?:%s\|){12})外資\|((?:%s\|){12})" % (NUM, NUM, NUM))


def post(page, data):
    req = urllib.request.Request(BASE + page, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"User-Agent": "Mozilla/5.0 (research; goldenstocks)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        raw = r.read().decode("utf-8", errors="ignore")
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "|", raw)))     # 先解碼再壓縮空白(&nbsp; 會變成 \xa0)
    return re.sub(r"(\|\s*)+", "|", t)


def ints(s):
    return [int(x.replace(",", "")) for x in s.strip("|").split("|")]


def inst_oi(day):
    t = post("futContractsDate", {"queryType": "1", "goDay": "", "doQuery": "1", "dateaddcnt": "",
                                  "queryDate": day.strftime("%Y/%m/%d"), "commodityId": ""})
    m = INST_RE.search(t)
    if not m:
        return None
    out = {}
    for name, g in zip(("dealer", "trust", "foreign"), m.groups()):
        v = ints(g)      # 6 組(口數,金額):成交多/空/淨、未平倉多/空/淨
        out[name] = {"oi_long": v[6], "oi_short": v[8], "oi_net": v[10]}
    return out


def total_oi(day):
    t = post("futDailyMarketReport", {"queryType": "2", "marketCode": "0", "dateaddcnt": "", "commodity_id": "MTX",
                                      "commodity_id2": "", "queryDate": day.strftime("%Y/%m/%d"),
                                      "MarketCode": "0", "commodity_idt": "MTX"})
    i = t.find("( MTX ) 行情表")
    if i < 0:
        return None
    seg = t[i:]
    j = seg.find("小計:|")
    if j < 0:
        return None
    nums = re.findall(r"-?[\d,]+", seg[j + 4:j + 60])
    return int(nums[3].replace(",", "")) if len(nums) >= 4 else None     # 盤後量|一般量|合計量|未沖銷契約量


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-10")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    a = ap.parse_args()
    out = STATE_ROOT / "data" / "research" / "mtx_retail"
    out.mkdir(parents=True, exist_ok=True)
    d, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    rows = []
    while d <= end:
        if d.weekday() < 5:
            try:
                inst = inst_oi(d)
                time.sleep(1.2)
                tot = total_oi(d) if inst else None
                time.sleep(1.2)
            except Exception as e:                     # 網路/格式問題:略過並印出,不中斷
                print(d, "ERR", type(e).__name__, str(e)[:80])
                inst = tot = None
            if inst and tot:
                net = sum(v["oi_net"] for v in inst.values())
                rows.append({"date": d.isoformat(), "total_oi": tot, "inst_net_oi": net,
                             "dealer_net": inst["dealer"]["oi_net"], "trust_net": inst["trust"]["oi_net"],
                             "foreign_net": inst["foreign"]["oi_net"], "retail_ratio_pct": round(-net / tot * 100, 3)})
                print(d, rows[-1]["retail_ratio_pct"], tot, net)
            else:
                print(d, "無資料(非交易日或格式不符)")
        d += dt.timedelta(days=1)
    if rows:
        with open(out / "mtx_retail_ratio.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(f"完成 {len(rows)} 日 → {out / 'mtx_retail_ratio.csv'}")


if __name__ == "__main__":
    main()
