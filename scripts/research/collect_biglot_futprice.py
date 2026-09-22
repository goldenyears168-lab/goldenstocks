#!/usr/bin/env python
"""個股期貨即時價輪詢 → futprice_{date}.json,供 biglot dashboard「期貨」欄。

唯讀、REST 輪詢(非持久 ws)、與現股 collector 及其他富邦連線並存(已驗多連線安全)。
- 啟動時對 biglot 宇宙每檔用 tickers(product=root) 解析近月合約(標準契約 F 優先,
  避開減資調整後契約 FT1/IX1 的低流動性坑)。
- 之後每 interval 秒 quote 一次,寫 sid->{px,t} 到 futprice_{date}.json(整檔覆寫)。
- 單檔失敗不中斷;13:45 自動退出。
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DATA_DIR  # noqa: E402
from order.fubon_session import connect_fubon  # noqa: E402  (session helper,比照 biglot_live_watch)

TZ = ZoneInfo("Asia/Taipei")
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
OUT_DIR = DATA_DIR.parent / "cache" / "biglot_live_watch"
INTERVAL = int(os.environ.get("FUTPRICE_INTERVAL", "10"))
END_HHMM = "13:45"


def _now():
    return datetime.now(TZ)


def resolve_front(fut, root, today):
    data = fut.intraday.tickers(type="FUTURE", exchange="TAIFEX", product=root)
    rows = data if isinstance(data, list) else (data.get("data") or data.get("tickers") or [])
    cands = []
    for r in rows or []:
        sym = str(r.get("symbol") or "")
        end = str(r.get("endDate") or r.get("end_date") or "")[:10]
        if not sym.startswith(root) or (end and end < today):
            continue
        std = 0 if sym[len(root):len(root) + 1] == "F" else 1  # 標準契約優先
        cands.append((end or "9999-99-99", std, sym))
    cands.sort()
    return cands[0][2] if cands else None


def _last_px(q):
    if not isinstance(q, dict):
        return None
    for k in ("lastPrice", "lastTrade", "closePrice", "close"):
        v = q.get(k)
        if isinstance(v, dict):
            v = v.get("price")
        if v:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def main():
    cal = json.loads(CALIB.read_text())
    uni = {r["sid"]: (r.get("fut_code") or "") for r in cal["universe"]}
    sess = connect_fubon(realtime=True)
    fut = sess.sdk.marketdata.rest_client.futopt
    today = _now().strftime("%Y-%m-%d")
    # 解析近月
    sym_of = {}
    for sid, fc in uni.items():
        root = fc[:2] if len(fc) >= 3 else fc
        if not root:
            continue
        try:
            s = resolve_front(fut, root, today)
            if s:
                sym_of[sid] = s
        except Exception as exc:  # noqa: BLE001
            print(f"resolve {sid}/{root} 失敗: {exc}", flush=True)
    print(f"解析近月 {len(sym_of)}/{len(uni)} 檔", flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"futprice_{today}.json"
    once = "--once" in sys.argv
    while _now().strftime("%H:%M") < END_HHMM:
        snap = {}
        for sid, sym in sym_of.items():
            try:
                px = _last_px(fut.intraday.quote(symbol=sym))
                if px:
                    snap[sid] = {"px": px, "sym": sym, "t": _now().strftime("%H:%M:%S")}
            except Exception:  # noqa: BLE001
                pass
        try:
            out.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"寫檔失敗: {exc}", flush=True)
        print(f"{_now():%H:%M:%S} 期貨價 {len(snap)}/{len(sym_of)} 檔", flush=True)
        if once:
            break
        time.sleep(INTERVAL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
