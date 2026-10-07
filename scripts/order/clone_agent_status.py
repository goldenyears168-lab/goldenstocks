#!/usr/bin/env python3
"""複製人實單層現況(唯讀,只讀 data/order 的 JSON,不登入券商):心跳、開關、今日帳本、券商端最近一次核對。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from project_dotenv import load_project_dotenv  # noqa: E402

load_project_dotenv()
from clone_agent.order_switch import read_heartbeat, read_ledger_display, read_switch  # noqa: E402


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    hb, sw, led = read_heartbeat(), read_switch(), read_ledger_display(day)
    age = None
    try:
        age = round((datetime.now().astimezone() - datetime.fromisoformat(hb["ts"])).total_seconds())
    except (KeyError, ValueError, TypeError):
        pass
    print(f"[{day}] worker={'運行' if age is not None and age < 90 else '未運行'}(心跳 {age}s 前,{hb.get('mode')}) "
          f"開關={'開' if sw['enabled'] else '關'}({sw['state']}) 熔斷={led.get('halted')} 冷卻至={led.get('cooldown_until')}")
    bk = led.get("broker") or {}
    if bk:
        print(f"券商核對 {bk.get('at')} 持倉查詢={'OK' if bk.get('positions_ok') else '失敗'} 委託查詢={'OK' if bk.get('orders_ok') else '失敗'} "
              f"可用保證金={bk.get('available_margin')} errors={bk.get('errors')}")
        for p in bk.get("positions") or []:
            print(f"  持倉 {p.get('symbol')} {p.get('side')} {p.get('lots')}口 @ {p.get('price')}")
        for o in bk.get("open_orders") or []:
            print(f"  未了結委託 {o.get('symbol')} {o.get('side')} {o.get('lot')}口 @ {o.get('price')} 已成 {o.get('filled_lot')} {'(本層)' if o.get('ours') else '(非本層:手動/其他袖)'}")
    else:
        print("尚無券商核對紀錄(worker 未在窗內或 dry-run)")
    ents = sorted((led.get("entries") or {}).values(), key=lambda e: e.get("card_time") or "")
    sent = [e for e in ents if e.get("status") not in ("skipped",)]
    print(f"帳本 {len(ents)} 筆(送出相關 {len(sent)}):")
    for e in ents[-25:]:
        print(f"  {e.get('card_time')} {e.get('uname')}({e.get('sid')}) {e.get('price')} → {e.get('status')} {e.get('reason') or ''} "
              f"{'[留倉]' if e.get('carried_from') else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
