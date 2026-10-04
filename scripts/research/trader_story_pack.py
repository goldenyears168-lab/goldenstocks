#!/usr/bin/env python3
"""21 筆手動「新倉」成交單 → 送出時刻／成交時刻的儀表板快照包（給逐筆判讀 agent 用）。

每筆輸出一個 JSON：
  · own:   自己在 5分/30分/全日 的大戶淨佔比%、散戶淨佔比%、散戶參與度(買+賣)%、成交額、價格報酬
  · xs:    同一秒 42 檔宇宙內的橫斷面百分位（大戶30分/全日、散戶30分、量30分、價5分/30分）＋市場均值
  · story: 用 scripts/research/biglot/story_tags.py 的同一套規則（核心 23 檔為池）判這一秒的故事標籤
           標的不在核心池(玉晶光/大立光/金像電/台虹為「獨」)時,另外把它塞進池再判一次(story_incl)
  · fut:   期貨買一/賣一(當下)、委託價相對買一差幾檔
  · panel: trader_feature_panel 該分鐘列(含 fwd30/fwd60/fwd_close 事後結果、距當日高%、族群欄、er)
  · exit:  配對的平倉成交(同商品、成交後第一筆 Close)與損益
口徑跟 trade_ingest 一致（大戶≥1000萬單筆、散戶=1張<500萬）。只讀檔，不碰富邦、不寫面板。
"""
from __future__ import annotations

import bisect
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src"); sys.path.insert(0, "scripts/research")
import biglot_dashboard  # noqa: E402,F401  (story_tags 的 import 規則)
from biglot.story_tags import story_tags  # noqa: E402
from trader_biglot_decision_replay import replay_day, window  # noqa: E402
from trader_peer_group_choice import day_universe  # noqa: E402
import trader_signal_watch as tsw  # noqa: E402

DATA = Path.home() / "goldenstocks-data"
OUT = DATA / "data/scratch/trader_story_pack"
DB = DATA / "data/stocks.db"
SPECS = json.loads((DATA / "data/cache/taifex_stock_futures_specs.json").read_text())
ROLES = json.loads(Path("config/biglot_roles.json").read_text())
EXCL = {r["sid"] for r in ROLES["reference_in_pool"]} | {r["sid"] for r in ROLES["independent"]} | {"2481", "1303"}
INDEP = {r["sid"]: r for r in ROLES["independent"]}
PANEL = pd.read_parquet(DATA / "data/scratch/trader_feature_panel.parquet")
PANEL["sid"] = PANEL["sid"].astype(str)


def daily_ref(sids: set[str], day: str) -> dict[str, dict]:
    """前收 + 20 日均成交額(NT$) + 20MA，PIT(< day)。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = f"""select stock_id, trade_date, close, amount from stock_daily_bars
            where trade_date < ? and trade_date >= date(?, '-45 day') and source='finmind'
              and stock_id in ({','.join('?' * len(sids))})"""
    df = pd.read_sql(q, con, params=[day, day, *sorted(sids)]); con.close()
    out = {}
    for sid, g in df.sort_values("trade_date").groupby("stock_id"):
        g = g.tail(20)
        out[sid] = {"prev_close": float(g["close"].iloc[-1]), "avg20_amt": float(g["amount"].mean()),
                    "ma20": float(g["close"].mean()) if len(g) >= 20 else None}
    return out


def gross_pct(A, t_end, sec):
    ts = A["ts"]
    j = bisect.bisect_right(ts, t_end); i = bisect.bisect_left(ts, t_end - sec)
    if j <= i:
        return None, None
    tot = A["cum_amt"][j] - A["cum_amt"][i]
    return (100 * (A["cum_rabs"][j] - A["cum_rabs"][i]) / tot if tot else None), tot


def px_at(A, t):
    i = bisect.bisect_right(A["ts"], t) - 1
    return A["px"][i] if i >= 0 else None


def row_for(sid, A, t, ref):
    """story_tags 需要的鍵（同儀表板 _rolling 命名；_r=滾動）。"""
    b5, r5, tot5, pr5, _, _ = window(A, t, 300)
    b30, r30n, tot30, pr30, _, _ = window(A, t, 1800)
    bd, rd, totd, prd, _, _ = window(A, t, None)
    g30, _ = gross_pct(A, t, 1800)
    g30p, _ = gross_pct(A, t - 1800, 1800)
    px = px_at(A, t)
    rv = (totd / ref[sid]["avg20_amt"]) if (sid in ref and totd and ref[sid]["avg20_amt"]) else None
    return {"sid": sid,
            "big5_r": None if b5 is None else b5 * tot5 / 100, "big30_r": None if b30 is None else b30 * tot30 / 100,
            "retn30_r": None if r30n is None else r30n * tot30 / 100, "bigday": None if bd is None else bd * totd / 100,
            "rbuy30_r": None if (g30 is None or r30n is None) else (g30 + r30n) / 2,
            "rsell30_r": None if (g30 is None or r30n is None) else (g30 - r30n) / 2,
            "dsh30_r": None if (g30 is None or g30p is None) else g30 - g30p,
            "w_ret_r": None if pr5 is None else pr5 * 100, "r30_r": None if pr30 is None else pr30 * 100,
            "rvol_day": rv, "px": px,
            "_pct": {"big5": b5, "big30": b30, "bigday": bd, "ret5": r5, "ret30": r30n, "retday": rd, "gross30": g30,
                     "amt5": tot5, "amt30": tot30, "amtday": totd, "pxret5": pr5, "pxret30": pr30, "pxret_open": prd,
                     "chg_pct": (100 * (px / ref[sid]["prev_close"] - 1)) if (px and sid in ref) else None,
                     "bias20": (100 * (px / ref[sid]["ma20"] - 1)) if (px and sid in ref and ref[sid]["ma20"]) else None}}


def pctile(vals: dict, sid):
    xs = [v for v in vals.values() if v is not None]
    v = vals.get(sid)
    if v is None or len(xs) < 5:
        return None
    return round(100 * (sum(1 for x in xs if x < v) + 0.5 * sum(1 for x in xs if x == v) - 0.5) / len(xs), 1)


def snapshot(sid, t, acc, ref, uni):
    rows = {s: row_for(s, acc[s], t, ref) for s in uni if acc.get(s) and acc[s]["ts"] and acc[s]["ts"][0] <= t}
    if sid not in rows:
        return {"note": "此刻無逐筆(收集器尚未開始或標的未訂閱)"}
    me = rows[sid]
    r30s = [r["r30_r"] for r in rows.values() if r["r30_r"] is not None]
    w5s = [r["w_ret_r"] for r in rows.values() if r["w_ret_r"] is not None]
    mkt30 = sum(r30s) / len(r30s) if r30s else None
    mkt5 = sum(w5s) / len(w5s) if w5s else None
    for r in rows.values():                      # 相對大盤脈絡(同儀表板四分類)
        if r["r30_r"] is None or mkt30 is None:
            r["mkt_ctx"] = ""
        else:
            up_m, up_s = mkt30 >= 0, r["r30_r"] >= 0
            r["mkt_ctx"] = "順漲" if (up_m and up_s) else "順跌" if (not up_m and not up_s) else "逆強" if up_s else "逆弱"
    core = [r for s, r in rows.items() if s not in EXCL]
    tags_core = story_tags(core)
    tags_incl = story_tags(core + [me]) if sid in EXCL else tags_core
    xs = {k: pctile({s: r["_pct"][k] for s, r in rows.items()}, sid)
          for k in ("big5", "big30", "bigday", "ret5", "ret30", "retday", "gross30", "amt5", "amt30", "amtday", "pxret5", "pxret30", "pxret_open", "chg_pct")}
    dsh = pctile({s: r["dsh30_r"] for s, r in rows.items()}, sid)
    return {"own": {**{k: (None if v is None else round(v, 3)) for k, v in me["_pct"].items()},
                    "dsh30_pp": None if me["dsh30_r"] is None else round(me["dsh30_r"], 2),
                    "rvol_day_sofar": None if me["rvol_day"] is None else round(me["rvol_day"], 3),
                    "mkt_ctx": me["mkt_ctx"], "px": me["px"]},
            "xs_pctile_in_42": {**xs, "dsh30": dsh, "n_pool": len(rows)},
            "mkt": {"avg_pxret30_bps": None if mkt30 is None else round(mkt30, 1), "avg_pxret5_bps": None if mkt5 is None else round(mkt5, 1),
                    "n_core_pool": len(core)},
            "story_tags_core_pool": tags_core.get(sid, "（非核心池,見 story_tags_if_included）"),
            "story_tags_if_included": tags_incl.get(sid),
            "n_core_with_each_tag": {k: sum(1 for v in tags_core.values() if k in v) for k in ("nobig", "pullbk", "arrive_push", "arrive_sell", "crash_sell", "crash_absorb", "lock", "defend")}}


def panel_row(sid, day, hm):
    g = PANEL[(PANEL["sid"] == sid) & (PANEL["day"] == day)]
    if g.empty:
        return None
    g = g[g["hm"] <= hm]
    if g.empty:
        return None
    r = g.iloc[-1].to_dict()
    r.pop("t", None)
    return {k: (None if (isinstance(v, float) and pd.isna(v)) else (round(v, 4) if isinstance(v, float) else (str(v) if not isinstance(v, (int, bool)) else v))) for k, v in r.items()}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    d = pd.read_pickle(DATA / "data/scratch/trader_behavior_tables.pkl")
    f = d["futopt"]["fills"]; f = f[~f["auto"]].copy()
    f["day"] = pd.to_datetime(f["date"]).dt.strftime("%Y-%m-%d")
    f = f[f["day"].between("2026-09-29", "2026-10-02")]
    f["root"] = f["symbol"].str.slice(2)
    f["stock"] = f["root"].map(lambda r: SPECS.get(r[:-1] if r.endswith("F") else r, {}).get("stock_id"))
    f["name"] = f["root"].map(lambda r: SPECS.get(r[:-1] if r.endswith("F") else r, {}).get("name"))
    f["oc"] = f["order_type_o"].astype(str).str.split(".").str[-1]
    f = f.sort_values(["day", "filled_time"])
    news = f[(f["oc"] == "New") & ~f["stock"].isin({r["sid"] for r in ROLES["reference_in_pool"]})]
    print(f"新倉成交(剔力積電/臻鼎) n={len(news)}")
    index = []
    for day, g in news.groupby("day"):
        uni = day_universe(day)
        if not uni:
            print(f"{day}: 無 raw"); 
        ref = daily_ref(uni | set(g["stock"]), day) if uni else {}
        acc = replay_day(day, uni | set(g["stock"])) if uni else {}
        print(f"{day}: 宇宙 {len(uni)} · 有逐筆 {sum(1 for a in acc.values() if a['ts'])}")
        for k, r in g.iterrows():
            idx = len(index) + 1
            root = r["root"][:-1] if r["root"].endswith("F") else r["root"]
            rec = {"idx": idx, "day": day, "stock": r["stock"], "name": r["name"], "symbol": r["symbol"], "order_no": r["order_no"],
                   "side": r["side"], "oc": "New", "lot": int(r["filled_lot"]), "limit_price": float(r["order_price"]), "fill_price": float(r["filled_price"]),
                   "submit_time": str(r["submit_time"])[:12], "fill_time": str(r["filled_time"])[:12],
                   "role": ("核心池" if r["stock"] not in EXCL else f"獨({INDEP.get(r['stock'], {}).get('kind', '')}):{INDEP.get(r['stock'], {}).get('reason', '')}"),
                   "margin_rate_hint": "見 taifex_stock_futures_margin_rates.json"}
            ex = f[(f["symbol"] == r["symbol"]) & (f["oc"] == "Close") & (f["side"] != r["side"]) &
                   ((f["day"] > day) | ((f["day"] == day) & (f["filled_time"] > r["filled_time"])))].head(1)
            if not ex.empty:
                e = ex.iloc[0]; sgn = 1 if r["side"] == "Buy" else -1
                rec["exit"] = {"day": e["day"], "submit_time": str(e["submit_time"])[:12], "fill_time": str(e["filled_time"])[:12],
                               "limit_price": float(e["order_price"]), "fill_price": float(e["filled_price"]),
                               "ret_pct": round(100 * sgn * (float(e["filled_price"]) / float(r["filled_price"]) - 1), 3)}
            for lab, tm in (("at_submit", rec["submit_time"]), ("at_fill", rec["fill_time"])):
                t = datetime.fromisoformat(f"{day}T{tm[:8]}+08:00").timestamp()
                snap = snapshot(r["stock"], t, acc, ref, uni) if acc else {"note": "當天無 raw 逐筆"}
                bid, ask = tsw.fut_quote(root, day, t)
                tick = None
                if bid and ask and r["side"] == "Buy":
                    tick = round(float(r["order_price"]) - bid, 2)
                elif bid and ask:
                    tick = round(float(r["order_price"]) - ask, 2)
                snap["fut_quote"] = {"bid": bid, "ask": ask, "limit_minus_bid(Buy)/minus_ask(Sell)": tick}
                snap["panel_minute_row"] = panel_row(r["stock"], day, tm[:5])
                rec[lab] = snap
            path = OUT / f"{idx:02d}_{day}_{r['stock']}_{r['name']}.json"
            path.write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str))
            index.append({"idx": idx, "file": path.name, "day": day, "stock": r["stock"], "name": r["name"], "side": r["side"],
                          "submit": rec["submit_time"], "fill": rec["fill_time"], "limit": rec["limit_price"], "fill_px": rec["fill_price"],
                          "tags_submit": rec["at_submit"].get("story_tags_if_included"), "tags_fill": rec["at_fill"].get("story_tags_if_included"),
                          "has_tick_submit": "own" in rec["at_submit"], "has_tick_fill": "own" in rec["at_fill"],
                          "exit_ret_pct": (rec.get("exit") or {}).get("ret_pct")})
    pd.DataFrame(index).to_csv(OUT / "INDEX.csv", index=False)
    print(pd.DataFrame(index).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
