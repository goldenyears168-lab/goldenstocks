#!/usr/bin/env python3
"""籌碼 SOP 每日計算：8771 儀表板名單 → ${DATA}/chip/chip_sop_{date}.json（/chips 頁唯一資料來源）。

  PYTHONPATH=src .venv/bin/python scripts/research/chip_sop.py [--asof YYYY-MM-DD] [--no-write]

口徑（見 digest-chip-branch 與 branch-concentration-column-20261009）：
  主力買賣超 = 分點前 N 買超合計 + 前 N 賣超合計（張）；集中度% = 主力 ÷ 當日分點買進合計（≈成交量）
  期間版 = 先把窗內各分點淨額加總，再取前 N ÷ 窗內買進合計
  資料閘門：當日分點買進佔成交量比例與分點數都要達門檻；期間版只用「到最新日為止連續完整」的交易日
  一致度 = 5日主力、20日主力、投信5日、當日價格 四票與當日主力方向「同意」的個數／有效票數；家數差只顯示、不計分
  （街口六指標檢定：家數差方向反轉，t=-7.11）。三態：未達死區＝棄權，資料缺＝缺票，兩者都不進分母；有效票數＝同意＋不同意。
  隔日沖席位只標示、不加權。

兩種分點口徑(2026-10-17 起,FinMind 不續約):
  full = 全分點底稿(FinMind 時期,≤2026-10-15)——算法完全不變。
  top  = 前段分點(富邦行情站:買超前 15 + 賣超前 15 名,加上專家/席位表分點)。『每日主力』= 買超前 N + 賣超前 N 本來就只用到
         前段,所以日主力張數與全分點版相同;分母改用當日成交量(全分點版用分點買進合計,兩者本就≈)。
         期間版(5/20 日)改為『逐日主力加總 ÷ 窗內成交量』(沒有每個分點每天的淨額可跨日加總);
         家數差需要全分點,top 日不提供(None)。含任一 top 日的窗一律走逐日加總。
  完整性閘門:full 日 = 分點數與買進占比達門檻;top 日 = 至少 TOP_MIN_SIDE 個買超與賣超分點。
  所有數值門檻只定義在下方常數與 PARAMS；快照的 params 欄位記下當時用的值，/chips 頁的說明文字與分組標籤都讀它，不另寫數字。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import market_calendar  # noqa: E402
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

CFG = ROOT / "config" / "chip_seats.yaml"
OUT_DIR = DATA_DIR / "chip"
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
TOP_N = 15
COMPLETE_RATIO = 0.97
MIN_BRANCHES = 100
THIN_LOTS = 3000
BAND = 10.0
TOP_MIN_SIDE = 10      # top 口徑:當日至少要有這麼多個買超分點與賣超分點(富邦頁最多各列 15)
IT_ACT_PCT = 2.0
PERIOD_DEAD_PCT = 2.0
PRICE_DEAD_PCT = 0.01
FLIP_K = 3
SEATS_SHOWN = 3
DOMINANT_SHARE = 0.4
FOREIGN_SHARE = 0.5
CONSENSUS_MIN_SEATS = 2
SCHEMA = 3
LOOKBACK_DAYS = 45
FOREIGN_KW = ("摩根", "高盛", "美林", "瑞銀", "花旗", "麥格理", "野村", "匯豐", "大和", "德意志", "法國興業", "里昂", "瑞士信貸", "星展", "新加坡商", "港商", "美商")


def load_cfg() -> dict:
    c = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    c["seats"] = {str(k): v for k, v in c["seats"].items()}  # 分點代號大小寫是不同分點（989g 元大嘉義 ≠ 989G 元大大同），不可轉小寫
    return c


def main_net(nets: list[float], top_n: int = TOP_N) -> float:
    """nets 為各分點淨額（股）；回傳前 top_n 買超與前 top_n 賣超的合計（股）。"""
    s = sorted(nets)
    return sum(n for n in s[::-1][:top_n] if n > 0) + sum(n for n in s[:top_n] if n < 0)


def sign(x: float | None, eps: float = 0.0) -> int:
    if x is None:
        return 0
    return 1 if x > eps else -1 if x < -eps else 0


def vote(val: float | None, s: int, dead: float = 0.0) -> str:
    if val is None:
        return "missing"
    if abs(val) < dead or sign(val) == 0:
        return "abstain"
    return "agree" if sign(val) == s else "disagree"


def make_warnings(rows: list[dict]) -> list[str]:
    warns = []
    bad = [r["sid"] for r in rows if r.get("status") != "ok"]
    if bad:
        warns.append(f"分點未完整/無價/資料日無日 K {len(bad)} 檔：{','.join(bad)}")
    miss = [r["sid"] for r in rows if r.get("it_missing")]
    if miss:
        warns.append(f"投信資料缺最新日 {len(miss)} 檔：{','.join(miss)}")
    return warns


def seat_tag(tid: str, cfg: dict) -> dict | None:
    s = cfg["seats"].get(str(tid))
    if s is None:
        return None
    f = s.get("flip")
    return {"flip": f, "level": "unknown" if f is None else "high" if f >= cfg["flip_high_threshold"] else "low"}


def is_foreign(name: str) -> bool:
    return any(k in name for k in FOREIGN_KW)


def load_stock(conn: sqlite3.Connection, sid: str, asof: str, since: str) -> dict:
    bars = {
        d: (v, c)
        for d, v, c in conn.execute(
            "SELECT trade_date, MAX(volume), MAX(close) FROM stock_daily_bars "
            "WHERE stock_id=? AND trade_date BETWEEN ? AND ? AND close>0 GROUP BY trade_date",
            (sid, since, asof),
        )
    }
    days: dict[str, dict[str, tuple[str, float, float]]] = {}
    full: set[str] = set()                    # 有 FinMind 全分點底稿的日子
    for d, tid, nm, buy, net, src in conn.execute(
        "SELECT trade_date, securities_trader_id, securities_trader, buy, net, source FROM stock_broker_branch_daily "
        "WHERE stock_id=? AND trade_date BETWEEN ? AND ?",
        (sid, since, asof),
    ):
        days.setdefault(d, {})[tid] = (nm, buy or 0.0, net or 0.0)
        if src == "finmind":
            full.add(d)
    it = {
        d: v
        for d, v in conn.execute(
            "SELECT trade_date, investment_trust_net FROM stock_institutional_daily "
            "WHERE stock_id=? AND trade_date BETWEEN ? AND ?",
            (sid, since, asof),
        )
    }
    return {"bars": bars, "tape": days, "it": it, "full": full}


def day_complete(rows: dict, vol: float | None) -> bool:
    if not rows or not vol:
        return False
    return len(rows) >= MIN_BRANCHES and sum(r[1] for r in rows.values()) >= COMPLETE_RATIO * vol


def top_complete(rows: dict) -> bool:
    pos = sum(1 for r in rows.values() if r[2] > 0)
    neg = sum(1 for r in rows.values() if r[2] < 0)
    return pos >= TOP_MIN_SIDE and neg >= TOP_MIN_SIDE


def day_mode(rows: dict, vol: float | None, is_full: bool) -> str | None:
    """'full'=全分點底稿完整;'top'=前段分點完整(富邦);None=不完整。"""
    if is_full:
        return "full" if day_complete(rows, vol) else None
    return "top" if top_complete(rows) else None


def window_stats(tape: dict, dates: list[str], modes: dict | None = None, vols: dict | None = None) -> dict:
    """modes/vols 省略時 = 舊的全分點算法(逐位元不變);窗內含任一 top 日則走『逐日主力加總 ÷ 窗內成交量』。"""
    if modes and any(modes.get(d) == "top" for d in dates):
        main = 0.0
        vol = 0.0
        for d in dates:
            main += main_net([n for (_, _, n) in tape[d].values()])
            vol += (vols or {}).get(d, 0.0)
        last_agg = {tid: n for tid, (_, _, n) in tape[dates[-1]].items()}
        return {
            "main_lots": round(main / 1000),
            "conc_pct": round(main / vol * 100, 2) if vol else None,
            "diff_cnt": None,
            "agg": last_agg if len(dates) == 1 else dict(sorted(last_agg.items())),
        }
    agg: dict[str, float] = {}
    buy = 0.0
    for d in dates:
        for tid, (_, b, n) in tape[d].items():
            agg[tid] = agg.get(tid, 0.0) + n
            buy += b
    m = main_net(list(agg.values()))
    return {
        "main_lots": round(m / 1000),
        "conc_pct": round(m / buy * 100, 2) if buy else None,
        "diff_cnt": sum(1 for n in agg.values() if n > 0) - sum(1 for n in agg.values() if n < 0),
        "agg": agg,
    }


def top_seats(tape_day: dict, cfg: dict, side: int, k: int = SEATS_SHOWN) -> list[dict]:
    items = sorted(tape_day.items(), key=lambda kv: kv[1][2], reverse=(side > 0))
    out = []
    for tid, (nm, _, n) in items:
        if (n > 0) != (side > 0) or len(out) >= k:
            break
        out.append({"id": tid, "name": nm, "lots": round(n / 1000), "seat": seat_tag(tid, cfg)})
    return out


def buy_to_sell(tape: dict, cal: list[str], last: str, k: int = FLIP_K, min_branches: int = MIN_BRANCHES) -> dict | None:
    """前一交易日（cal 中 last 的前一天）淨買超前 k 名的分點，今日轉為淨賣超前 k 名（strict）或僅淨賣超（loose）。前一日分點不完整則回 None。"""
    if len(cal) < 2:
        return None
    prev = cal[-2]
    if prev not in tape or len(tape[prev]) < min_branches:
        return None
    today = tape[last]
    sell_rank = {tid: i + 1 for i, (tid, v) in enumerate(sorted(today.items(), key=lambda kv: kv[1][2])) if v[2] < 0}
    out = []
    for tid, (nm, _, n) in sorted(tape[prev].items(), key=lambda kv: -kv[1][2])[:k]:
        if n <= 0:
            break
        now = today.get(tid)
        out.append({
            "id": tid, "name": nm, "prev_lots": round(n / 1000),
            "now_lots": round(now[2] / 1000) if now else 0,
            "sell_rank": sell_rank.get(tid),
        })
    return {"prev_date": prev, "k": k, "items": out, "strict": any(i["sell_rank"] and i["sell_rank"] <= k for i in out), "loose": any(i["sell_rank"] for i in out)}


CONSENSUS_LOTS = 100
CONSENSUS_BIG = {"9268", "9800"}  # 量大的綜合券商，兩家同向太容易發生，只有這一對不算共識


def seat_consensus(tape_day: dict, cfg: dict) -> dict:
    """名單席位當日淨買/賣 ≥CONSENSUS_LOTS 張者；同方向 ≥CONSENSUS_MIN_SEATS 個且不是只有 CONSENSUS_BIG 兩家＝共識。"""
    out = {"buy": [], "sell": []}
    for tid, (nm, _, n) in tape_day.items():
        if str(tid) not in cfg["seats"] or abs(n) < CONSENSUS_LOTS * 1000:
            continue
        out["buy" if n > 0 else "sell"].append({"id": str(tid), "name": nm, "lots": round(n / 1000)})
    for side in ("buy", "sell"):
        out[side].sort(key=lambda x: -abs(x["lots"]))
    out["buy_hit"] = len(out["buy"]) >= CONSENSUS_MIN_SEATS and not {x["id"] for x in out["buy"]} <= CONSENSUS_BIG
    out["sell_hit"] = len(out["sell"]) >= CONSENSUS_MIN_SEATS and not {x["id"] for x in out["sell"]} <= CONSENSUS_BIG
    return out


def analyse(sid: str, name: str, data: dict, asof: str, cfg: dict, cal: list[str]) -> dict:
    """cal＝市場交易日（升冪、含資料日）。個股序列一律以市場日曆為準：缺的那天就是缺，不會被跳過去把窗口悄悄拉長；日曆外的日子（殭屍列）不採用。"""
    cal = [d for d in cal if d <= asof]
    tape, it = data["tape"], data["it"]
    calset = set(cal)
    bars = {d: v for d, v in data["bars"].items() if d in calset}
    have = [d for d in cal if d in bars]
    last = have[-1] if have else None
    row: dict = {"sid": sid, "name": name, "date": last}
    if last is None:
        return {**row, "status": "no_price"}
    vol = bars[last][0]
    close = bars[last][1]
    if last != asof:
        return {**row, "status": "stale_price", "close": close, "chg_pct": None, "vol_lots": round(vol / 1000), "it": {}}
    pday = cal[-2] if len(cal) >= 2 else None
    prev = bars[pday][1] if pday in bars else None
    row.update(close=close, chg_pct=round((close / prev - 1) * 100, 2) if prev else None, vol_lots=round(vol / 1000))
    row["thin"] = vol / 1000 < THIN_LOTS

    full = data.get("full", set())
    modes = {d: day_mode(tape.get(d, {}), bars[d][0], d in full) for d in cal if d in bars}
    vols = {d: v[0] for d, v in bars.items()}
    run: list[str] = []
    for d in reversed(cal):
        if d in bars and modes.get(d):
            run.append(d)
        else:
            break
    run.reverse()
    row["tape_days"] = len(run)
    row["tape_ok"] = bool(run) and run[-1] == last
    row["tape_mode"] = modes.get(last)

    def it_win(n: int) -> dict | None:
        ds = cal[-n:]
        if len(ds) < n or any(d not in it or d not in bars for d in ds):
            return None
        net = sum(it[d] for d in ds)
        v = sum(bars[d][0] for d in ds)
        return {"lots": round(net / 1000), "pct": round(net / v * 100, 2) if v else None}

    row["it"] = {"d1": it_win(1), "d5": it_win(5), "d20": it_win(20)}
    row["it_missing"] = last not in it

    if not row["tape_ok"]:
        row.update(status="tape_incomplete", day=None, d5=None, d20=None, buyers=[], sellers=[], flags=[], consistency=None)
        return row

    d1 = window_stats(tape, [last], modes, vols)
    d5 = window_stats(tape, run[-5:], modes, vols) if len(run) >= 5 else None
    d20 = window_stats(tape, run[-20:], modes, vols) if len(run) >= 20 else None
    row["day"] = {k: d1[k] for k in ("main_lots", "conc_pct", "diff_cnt")}
    row["d5"] = {k: d5[k] for k in ("main_lots", "conc_pct", "diff_cnt")} if d5 else None
    row["d20"] = {k: d20[k] for k in ("main_lots", "conc_pct", "diff_cnt")} if d20 else None
    row["buyers"] = top_seats(tape[last], cfg, +1)
    row["sellers"] = top_seats(tape[last], cfg, -1)
    row["flip3"] = (
        buy_to_sell(tape, cal, last, min_branches=1 if modes.get(pday) == "top" else MIN_BRANCHES)
        if pday in bars and modes.get(pday)
        else None
    )
    row["consensus"] = seat_consensus(tape[last], cfg)

    c = d1["conc_pct"]
    row["signal"] = "buy" if c is not None and c >= BAND else "sell" if c is not None and c < -BAND else "neutral"

    flags: list[str] = []
    if row["thin"]:
        flags.append("量小")
    top15 = sorted(d1["agg"].items(), key=lambda kv: -kv[1])[:TOP_N]
    pos = [(tid, n) for tid, n in top15 if n > 0]
    tot = sum(n for _, n in pos)
    if tot > 0 and pos[0][1] / tot >= DOMINANT_SHARE:
        flags.append("單點主導")
    if tot > 0 and sum(n for tid, n in pos if is_foreign(tape[last][tid][0])) / tot >= FOREIGN_SHARE:
        flags.append("外資主導")
    if row["signal"] != "neutral" and row["chg_pct"] is not None and sign(row["chg_pct"]) == -sign(c):
        flags.append("價量背離")
    if row["flip3"] and row["flip3"]["strict"]:
        flags.insert(0, "買超前三轉賣超前三")
    if row["consensus"]["buy_hit"]:
        flags.append("席位共識買超")
    if row["consensus"]["sell_hit"]:
        flags.append("席位共識賣超")
    if any(s["seat"] and s["seat"]["level"] == "high" for s in row["buyers"]):
        flags.append("買方有隔日沖")
    if any(s["seat"] and s["seat"]["level"] == "high" for s in row["sellers"]):
        flags.append("賣方有隔日沖")
    it5 = row["it"]["d5"]
    if it5 and it5["pct"] is not None and abs(it5["pct"]) >= IT_ACT_PCT:
        flags.append("投信買超" if it5["pct"] > 0 else "投信賣超")
    row["flags"] = flags

    if row["signal"] == "neutral":
        row["consistency"] = None
    else:
        s = sign(c)
        votes = [
            vote(d5["conc_pct"] if d5 else None, s, PERIOD_DEAD_PCT),
            vote(d20["conc_pct"] if d20 else None, s, PERIOD_DEAD_PCT),
            vote(it5["pct"] if it5 else None, s, IT_ACT_PCT),
            vote(row["chg_pct"], s, PRICE_DEAD_PCT),
        ]
        n = {k: votes.count(k) for k in ("agree", "disagree", "abstain", "missing")}
        row["consistency"] = {**n, "known": n["agree"] + n["disagree"], "total": 4}
    row["status"] = "ok"
    return row


def universe(cfg: dict) -> list[tuple[str, str]]:
    skip = set(cfg.get("exclude_stocks", []))
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    return [(str(r["sid"]), str(r.get("name") or r["sid"])) for r in calib["universe"] if str(r["sid"]) not in skip]


def compute_rows(conn: sqlite3.Connection, cfg: dict, days: list[str], uni: list[tuple[str, str]] | None = None) -> dict[str, list[dict]]:
    """唯一的計算路徑：逐股一次載入整段、再逐日截斷（PIT：每日只看 ≤ 該日、回看 LOOKBACK_DAYS）。單日＝days 只有一天。"""
    uni = universe(cfg) if uni is None else uni
    since = conn.execute("SELECT date(?, ?)", (days[0], f"-{LOOKBACK_DAYS} day")).fetchone()[0]
    market = market_calendar.trading_days(conn, since, days[-1])
    not_trading = [d for d in days if d not in set(market)]
    if not_trading:
        raise ValueError(f"非交易日（官方日 K 列數不足）：{','.join(not_trading)}")
    per_day: dict[str, list[dict]] = {d: [] for d in days}
    for sid, nm in uni:
        full = load_stock(conn, sid, days[-1], since)
        for d in days:
            lo = conn.execute("SELECT date(?, ?)", (d, f"-{LOOKBACK_DAYS} day")).fetchone()[0]
            cut = {k: {x: v for x, v in full[k].items() if lo <= x <= d} for k in ("bars", "tape", "it")}
            per_day[d].append(analyse(sid, nm, cut, d, cfg, [c for c in market if lo <= c <= d]))
    return per_day


PARAMS = {
    "top_n": TOP_N, "complete_ratio": COMPLETE_RATIO, "min_branches": MIN_BRANCHES, "top_min_side": TOP_MIN_SIDE, "thin_lots": THIN_LOTS, "band_pct": BAND,
    "it_act_pct": IT_ACT_PCT, "period_dead_pct": PERIOD_DEAD_PCT, "price_dead_pct": PRICE_DEAD_PCT, "flip_k": FLIP_K, "seats_shown": SEATS_SHOWN,
    "dominant_share": DOMINANT_SHARE, "foreign_share": FOREIGN_SHARE, "consensus_lots": CONSENSUS_LOTS, "consensus_min_seats": CONSENSUS_MIN_SEATS,
}
SEAT_CFG_PARAMS = ("flip_high_threshold", "flip_reversal", "flip_top_k", "flip_min_lots")


def make_snapshot(conn: sqlite3.Connection, cfg: dict, asof: str, rows: list[dict], source: str) -> dict:
    """唯一的快照組裝點：metadata、日曆、警告都只在這裡產生。source＝live（排程當日）或 backfill（事後補算）。"""
    cal = market_calendar.calendar_for(conn, asof)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "asof": asof,
        "source": source,
        "calendar": cal,
        "seats_flip_asof": cfg["flip_asof"],
        "params": {**PARAMS, **{k: cfg[k] for k in SEAT_CFG_PARAMS},
                   "consensus_big": [cfg["seats"].get(i, {}).get("name", i) for i in sorted(CONSENSUS_BIG)]},
        "warnings": make_warnings(rows),
        "rows": rows,
    }


def build(asof: str | None = None) -> dict:
    cfg = load_cfg()
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        asof = asof or market_calendar.latest_trading_day(conn, datetime.now().date().isoformat())
        rows = compute_rows(conn, cfg, [asof])[asof]
        return make_snapshot(conn, cfg, asof, rows, "live")
    finally:
        conn.close()


def next_day(conn: sqlite3.Connection, sid: str, d: str, close: float) -> dict | None:
    """d 之後第一根日 K 的隔日表現；尚未收盤（無資料）回 None。ret=收盤對收盤、gap=開盤對前收、intraday=開盤到收盤。"""
    r = conn.execute(
        "SELECT trade_date, MAX(open), MAX(close) FROM stock_daily_bars WHERE stock_id=? AND trade_date>? AND close>0 "
        "GROUP BY trade_date ORDER BY trade_date LIMIT 1",
        (sid, d),
    ).fetchone()
    if not r or not r[2] or not close:
        return None
    nd, o, c = r
    return {
        "date": nd, "open": o, "close": c,
        "ret_pct": round((c / close - 1) * 100, 2),
        "gap_pct": round((o / close - 1) * 100, 2) if o else None,
        "intraday_pct": round((c / o - 1) * 100, 2) if o else None,
    }


def fill_outcomes(conn: sqlite3.Connection, path: Path) -> int:
    """把還沒有 next 的列補上隔日表現；回傳補了幾列。隔日必須等於市場日曆的下一交易日，該股缺那天的 K 就維持待補，不拿更晚的日子頂替。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    cal = data.get("calendar")
    changed = False
    if cal is None or not cal.get("next"):
        fresh = market_calendar.calendar_for(conn, data["asof"])
        if fresh != cal:
            data["calendar"], cal, changed = fresh, fresh, True
    n = 0
    for r in data["rows"]:
        if r.get("next") or r.get("close") is None or not r.get("date"):
            continue
        nx = next_day(conn, r["sid"], r["date"], r["close"])
        if nx and cal.get("next") and nx["date"] == cal["next"]:
            r["next"] = nx
            n += 1
    if n or changed:
        write_json(path, data)
    return n


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def build_range(days: list[str]) -> None:
    if not days:
        return
    cfg = load_cfg()
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        per_day = compute_rows(conn, cfg, days)
        for d, rows in per_day.items():
            write_json(OUT_DIR / f"chip_sop_{d}.json", make_snapshot(conn, cfg, d, rows, "backfill"))
    finally:
        conn.close()
    print(f"backfilled {len(days)} days")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="逐日重建 START~END 的歷史快照（PIT，只用 ≤ 該日資料；已存在則跳過）")
    ap.add_argument("--force", action="store_true", help="--backfill 時覆寫已存在的快照（口徑變更後重算舊日）")
    a = ap.parse_args()
    if a.backfill:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        try:
            days = market_calendar.trading_days(conn, *a.backfill)
        finally:
            conn.close()
        build_range([d for d in days if a.force or not (OUT_DIR / f"chip_sop_{d}.json").exists()])
        _fill_all()
        return 0
    out = build(a.asof)
    n_ok = sum(1 for r in out["rows"] if r.get("status") == "ok")
    print(f"asof={out['asof']} stocks={len(out['rows'])} ok={n_ok}")
    for w in out["warnings"]:
        print("警告：" + w)
    if not a.no_write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        write_json(OUT_DIR / f"chip_sop_{out['asof']}.json", out)
        write_json(OUT_DIR / "chip_sop_latest.json", out)
        print(f"wrote {OUT_DIR / 'chip_sop_latest.json'}")
        _fill_all()
    return 0 if n_ok else 1


def _fill_all() -> None:
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        total = sum(fill_outcomes(conn, p) for p in sorted(OUT_DIR.glob("chip_sop_2*.json")))
    finally:
        conn.close()
    latest = OUT_DIR / "chip_sop_latest.json"
    if latest.exists():
        stamp = json.loads(latest.read_text(encoding="utf-8"))["asof"]
        src = OUT_DIR / f"chip_sop_{stamp}.json"
        if src.exists():
            write_json(latest, json.loads(src.read_text(encoding="utf-8")))
    print(f"隔日表現補齊 {total} 列")


if __name__ == "__main__":
    raise SystemExit(main())
