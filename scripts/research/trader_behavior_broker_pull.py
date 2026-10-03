#!/usr/bin/env python3
"""唯讀拉取富邦委託／成交／已實現損益歷史 → JSON（交易習慣診斷用）。

目的：把 HTML 手動下單的「委託過程」與「成交結果」撈出來，供行為診斷
（下單時段分布、改價／刪單猶豫度、處置效應、報復性交易、加碼模式…）。

券商端限制：order_history / filled_history 單次查詢上限 30 天 → 本腳本自動
切成 28 天一段串起來。

安全性：只呼叫 *_history / realized_gains_and_loses 這類查詢 API，不下單、
不改單、不刪單；用完立刻 logout 釋放帳號連線額度（mini 已有 5 條生產連線，
第 6 條會撞上限）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, "src")

from fubon_neo.constant import FutOptMarketType  # noqa: E402
from stock_db import DATA_DIR  # noqa: E402

from order.fubon_session import connect_fubon, safe_logout  # noqa: E402

CHUNK_DAYS = 28
THROTTLE_S = 1.2
_MARKETS = (
    FutOptMarketType.Future,
    FutOptMarketType.FutureNight,
    FutOptMarketType.Option,
    FutOptMarketType.OptionNight,
)


def _ok(res) -> bool:
    return bool(getattr(res, "is_success", False))


def _plain(v):
    """純量化單一欄位。

    ⚠ SDK 的 enum（BSAction / OrderType / PriceType…）是 builtins 物件，用
    dir() 往下展開會得到「類別上所有列舉成員」而不是這一筆的值——buy_sell 的
    Buy/Sell 會因此變成同一包字典、真值消失（2026-10-02 第一版就踩到）。
    因此除了明確要展開的 details，其餘一律 str()。
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return str(v)


def _obj_fields(o) -> dict:
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    d = {}
    for name in dir(o):
        if name.startswith("_"):
            continue
        try:
            val = getattr(o, name)
        except Exception:  # noqa: BLE001
            continue
        if callable(val):
            continue
        d[name] = _plain(val)
    return d


def _rows(res) -> list[dict]:
    """展平一批結果；`details`（＝委託過程的逐筆改價／改量／刪單／成交）
    另外往下展一層。"""
    out = []
    for item in getattr(res, "data", None) or []:
        row = _obj_fields(item)
        raw_details = getattr(item, "details", None) if not isinstance(item, dict) else item.get("details")
        if raw_details:
            row["details"] = [_obj_fields(x) for x in raw_details]
        out.append(row)
    return out


def _call(call, tries: int = 5):
    """送一次查詢；撞「業務系統流量控管」就退讓重試。"""
    last = None
    for i in range(tries):
        time.sleep(THROTTLE_S)
        try:
            res = call()
        except Exception as exc:  # noqa: BLE001
            return None, repr(exc)
        if _ok(res):
            return res, None
        last = str(getattr(res, "message", None))
        if "流量" not in last:
            return None, last
        time.sleep(2.0 * (i + 1))
    return None, last


def _chunks(start: date, end: date):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=args.days)
    tag = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    out_path = Path(args.out) if args.out else DATA_DIR / "scratch" / f"broker_history_{tag}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    session = connect_fubon(realtime=False)
    payload: dict = {"start": start.isoformat(), "end": end.isoformat(), "accounts": [], "errors": []}
    try:
        for acc in session.accounts or []:
            label = f"{getattr(acc, 'branch_no', '')}-{getattr(acc, 'account', '')}"
            kind = str(getattr(acc, "account_type", "") or "").lower()
            is_fut = "fut" in kind or "opt" in kind
            api = session.sdk.futopt if is_fut else session.sdk.stock
            entry: dict = {"label": label, "account_type": kind}

            for name in ("order_history", "filled_history"):
                fn = getattr(api, name, None)
                if fn is None:
                    continue
                rows: list[dict] = []
                for c0, c1 in _chunks(start, end):
                    s, e = c0.strftime("%Y%m%d"), c1.strftime("%Y%m%d")
                    variants = []
                    if is_fut:
                        for mt in _MARKETS:
                            if name == "order_history":
                                variants.append((str(mt), lambda f=fn, a=acc, m=mt, s=s, e=e: f(a, s, e, m)))
                            else:
                                variants.append((str(mt), lambda f=fn, a=acc, m=mt, s=s, e=e: f(a, m, s, e)))
                    else:
                        variants.append(("", lambda f=fn, a=acc, s=s, e=e: f(a, s, e)))
                    for mtag, call in variants:
                        res, err = _call(call)
                        if res is None:
                            payload["errors"].append(f"{label}.{name}[{s}-{e}]{mtag}: {err}")
                            continue
                        for r in _rows(res):
                            r["_chunk"] = f"{s}-{e}"
                            if mtag:
                                r["_market_type"] = mtag
                            rows.append(r)
                entry[name] = rows

            if not is_fut:
                for name in ("realized_gains_and_loses", "realized_gains_and_loses_summary"):
                    fn = getattr(session.sdk.accounting, name, None)
                    if fn is None:
                        continue
                    # 已實現損益 API 不吃日期區間，一次回全部。
                    res, err = _call(lambda f=fn, a=acc: f(a))
                    if res is None:
                        payload["errors"].append(f"{label}.{name}: {err}")
                        entry[name] = []
                    else:
                        entry[name] = _rows(res)
            payload["accounts"].append(entry)
    finally:
        safe_logout(session)

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out_path}")
    for a in payload["accounts"]:
        counts = {k: len(v) for k, v in a.items() if isinstance(v, list)}
        print(f"  {a['label']} type={a['account_type']} {counts}")
    uniq = sorted({err.split(": ", 1)[-1] for err in payload["errors"]})
    for u in uniq[:12]:
        print(f"  ERRKIND {u}")
    print(f"  n_errors={len(payload['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
