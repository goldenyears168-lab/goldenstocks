#!/usr/bin/env python3
"""籌碼 SOP 每日計算（權證小哥版）：8771 儀表板名單 → ${DATA}/chip/chip_sop_{date}.json（/chips 頁唯一資料來源）。

  PYTHONPATH=src .venv/bin/python scripts/research/chip_sop.py [--asof YYYY-MM-DD] [--no-write]

規格與書的對照：docs/warrant-xiaoge/chip-sop-spec.md（每條規則的書中出處、操作化定義、資料缺口）。
口徑（2026-10-21 起全面對齊小哥書；先前的席位名單／家數差／價量背離／席位共識／一致度已移除，非小哥來源）：
  主力 = 分點前 N(=15) 買超合計 + 前 N 賣超合計（張）；集中度% = 主力 ÷ 成交量。窗口 1／5／10／20 日（軟體欄位主1／主5／主10，籌碼K線 20 日籌碼集中）。
  期間版 = 先把窗內各分點淨額加總，再取前 N ÷ 窗內買進合計（全分點日）；含任一前段日的窗＝逐日主力加總 ÷ 窗內成交量。
  集中度門檻 BAND=10%（V6：至少 10%）；「連續」＝連續 2 個交易日都達門檻（使用者 2026-10-21 定義）。
  位置＝布林通道（src/boll_position.py）：帶寬級距、月線方向、收盤區位、高檔（收盤≥月線）／低檔；規則亮燈全部帶位置條件。
  隔日沖＝逐股看紀錄（src/branch_habit.py，與 /inventory M07／M09 共用）：今日該股買超前 5 且 ≥3% 量的分點，查它在「這檔股票」近 90 日的大買→隔日賣紀錄。
  小哥點名分點＝config/xiaoge_named_branches.json（關鍵分點／公司派／地緣／高手…）中，點名過這檔股票的分點，今日動向。
  資料閘門：full 日＝分點買進佔量 ≥97% 且 ≥100 分點；top 日＝買超與賣超分點各 ≥10。期間版只用「到最新日為止連續完整」的交易日。
  所有門檻只定義在下方常數；快照 params 記下當時的值，/chips 頁的說明讀它，不另寫數字。
  書沒有、我們沒資料的：地緣（無分點／公司地址）、申報轉讓與庫藏股、關鍵分點選法（4000 日損益前 15 與贏家/輸家標籤）、集保戶數（FinMind 停用）→ 快照 gaps 明列，不假裝有。

兩種分點口徑(2026-10-17 起,FinMind 不續約):
  full = 全分點底稿(FinMind 時期,≤2026-10-15)；top = 前段分點(富邦行情站:買超前 15 + 賣超前 15 名,加上專家/席位表分點)。
  『每日主力』本來就只用前段，所以日主力張數相同，分母改用當日成交量；top 日沒有每個分點每天的淨額可跨日加總。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date as _date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import boll_position  # noqa: E402
import branch_habit  # noqa: E402
import market_calendar  # noqa: E402
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

NAMED = ROOT / "config" / "xiaoge_named_branches.json"
OUT_DIR = DATA_DIR / "chip"
CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"
EXCLUDE_STOCKS = {"6173"}      # 信昌電暫停交易
TOP_N = 15
WINDOWS = (1, 5, 10, 20)
COMPLETE_RATIO = 0.97
MIN_BRANCHES = 100
THIN_LOTS = 3000        # R27：日均量 3,000 張以上才是當沖基本盤；「日均」天數書未載，操作化為 20 日
BAND = 10.0             # 主力集中度門檻 %（V6）
STREAK_DAYS = 2         # 「連續」＝連續 2 個交易日（使用者 2026-10-21 定義）
TOP_MIN_SIDE = 10       # top 口徑:當日至少要有這麼多個買超分點與賣超分點(富邦頁最多各列 15)
SEATS_SHOWN = 3
BIG_SELL_SHARE = 0.03   # R21c「大分點狂賣」：賣超第一名分點淨賣 ≥ 該股當日量 3%（書未給數字，沿用大買事件同一門檻）
HABIT_DAYS = 90         # 慣性回看（日曆日）
HIGH_OUT_BIAS = 0.40    # R12 乖離年線 >40%
SCHEMA = 4
BAR_LOOKBACK_DAYS = 420   # 240 日均線（乖離年線）需要的日 K
TAPE_LOOKBACK_DAYS = 120  # 窗口 20 日 + 慣性 90 日
IT_LOOKBACK_DAYS = 45

GAPS = [
    "地緣：沒有分點營業地址與公司地址，只呈現小哥點名過的地緣分點",
    "申報轉讓／庫藏股（大股東賣、公司派買）：沒有資料來源",
    "關鍵分點選法（約 4,000 日區間損益前 15、軟體贏家／輸家標籤）：本機分點史只有約 2 年，無法重算；只呈現小哥點名的關鍵分點",
    "集保戶數／大戶持股（整理期沉澱、惡化四件組）：來源 FinMind 已停用",
]

_NAMED_CACHE: dict | None = None


def load_named() -> list[dict]:
    global _NAMED_CACHE
    if _NAMED_CACHE is None:
        try:
            _NAMED_CACHE = json.loads(NAMED.read_text(encoding="utf-8"))["branches"]
        except (OSError, ValueError, KeyError):
            _NAMED_CACHE = []
    return _NAMED_CACHE


def named_for(sid: str, named: list[dict] | None = None) -> dict[str, dict]:
    """點名過這檔股票的分點 {分點代號: {name, role}}（逐股；來源 key-branches.json 的 stocks 欄）。"""
    out: dict[str, dict] = {}
    tag = f"({sid})"
    for e in (load_named() if named is None else named):
        if any(tag in s for s in e.get("stocks") or []):
            for tid in e.get("ids") or []:
                out[str(tid)] = {"name": e["name"], "role": e.get("role")}
    return out


def main_net(nets: list[float], top_n: int = TOP_N) -> float:
    """nets 為各分點淨額（股）；回傳前 top_n 買超與前 top_n 賣超的合計（股）。"""
    s = sorted(nets)
    return sum(n for n in s[::-1][:top_n] if n > 0) + sum(n for n in s[:top_n] if n < 0)


def sign(x: float | None, eps: float = 0.0) -> int:
    if x is None:
        return 0
    return 1 if x > eps else -1 if x < -eps else 0


def make_warnings(rows: list[dict]) -> list[str]:
    warns = []
    bad = [r["sid"] for r in rows if r.get("status") != "ok"]
    if bad:
        warns.append(f"分點未完整/無價/資料日無日 K {len(bad)} 檔：{','.join(bad)}")
    miss = [r["sid"] for r in rows if r.get("it_missing")]
    if miss:
        warns.append(f"投信資料缺最新日 {len(miss)} 檔：{','.join(miss)}")
    return warns


def load_stock(conn: sqlite3.Connection, sid: str, asof: str, since_bars: str, since_tape: str, since_it: str) -> dict:
    """bars: 日期 → (量, 收, 開, 高, 低)。"""
    bars = {
        d: (v, c, o, h, lo)
        for d, v, c, o, h, lo in conn.execute(
            "SELECT trade_date, MAX(volume), MAX(close), MAX(open), MAX(high), MIN(CASE WHEN low>0 THEN low END) FROM stock_daily_bars "
            "WHERE stock_id=? AND trade_date BETWEEN ? AND ? AND close>0 GROUP BY trade_date",
            (sid, since_bars, asof),
        )
    }
    days: dict[str, dict[str, tuple[str, float, float, float]]] = {}
    full: set[str] = set()                    # 有 FinMind 全分點底稿的日子
    for d, tid, nm, buy, sell, net, src in conn.execute(
        "SELECT trade_date, securities_trader_id, securities_trader, buy, sell, net, source FROM stock_broker_branch_daily "
        "WHERE stock_id=? AND trade_date BETWEEN ? AND ?",
        (sid, since_tape, asof),
    ):
        days.setdefault(d, {})[tid] = (nm, buy or 0.0, net or 0.0, sell or 0.0)
        if src == "finmind":
            full.add(d)
    it = {
        d: v
        for d, v in conn.execute(
            "SELECT trade_date, investment_trust_net FROM stock_institutional_daily "
            "WHERE stock_id=? AND trade_date BETWEEN ? AND ?",
            (sid, since_it, asof),
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
    """'full'=全分點底稿完整;'top'=前段分點完整(買超與賣超各 ≥TOP_MIN_SIDE);None=不完整。
    全分點日若沒過 97% 買進占量閘門（常見 92～96%，缺的是小分點），前段名單仍完整，主力（前 15 買＋前 15 賣）照算，
    所以退回 'top' 判定，不讓一個缺小分點的日子把連續完整的窗口截斷。"""
    if is_full and day_complete(rows, vol):
        return "full"
    return "top" if top_complete(rows) else None


def window_stats(tape: dict, dates: list[str], modes: dict | None = None, vols: dict | None = None) -> dict:
    """modes/vols 省略時 = 全分點算法；窗內含任一 top 日則走『逐日主力加總 ÷ 窗內成交量』。"""
    if modes and any(modes.get(d) == "top" for d in dates):
        main = 0.0
        vol = 0.0
        for d in dates:
            main += main_net([r[2] for r in tape[d].values()])
            vol += (vols or {}).get(d, 0.0)
        last_agg = {tid: r[2] for tid, r in tape[dates[-1]].items()}
        return {"main_lots": round(main / 1000), "conc_pct": round(main / vol * 100, 2) if vol else None, "agg": last_agg}
    agg: dict[str, float] = {}
    buy = 0.0
    for d in dates:
        for tid, r in tape[d].items():
            agg[tid] = agg.get(tid, 0.0) + r[2]
            buy += r[1]
    m = main_net(list(agg.values()))
    return {"main_lots": round(m / 1000), "conc_pct": round(m / buy * 100, 2) if buy else None, "agg": agg}


def top_seats(tape_day: dict, side: int, named: dict[str, dict], k: int = SEATS_SHOWN) -> list[dict]:
    items = sorted(tape_day.items(), key=lambda kv: kv[1][2], reverse=(side > 0))
    out = []
    for tid, r in items:
        if (r[2] > 0) != (side > 0) or len(out) >= k:
            break
        nm = named.get(str(tid))
        out.append({"id": tid, "name": r[0], "lots": round(r[2] / 1000), "named": nm["role"] if nm else None})
    return out


def position_label(boll: dict | None) -> str:
    return "未知位置" if not boll else "高檔" if boll["upper_half"] else "低檔"


def rule_flags(ctx: dict) -> list[dict]:
    """小哥規則亮燈。每條帶書的規則 ID；位置條件來自布林（boll）。ctx 欄位見 analyse。純函式，可單測。"""
    out: list[dict] = []
    conc, boll, s2 = ctx.get("conc"), ctx.get("boll"), ctx.get("streak2") or {}
    pos = position_label(boll)

    def add(rid: str, label: str, side: str) -> None:
        out.append({"id": rid, "label": label, "side": side})

    buy_gate = conc is not None and conc >= BAND
    sell_gate = conc is not None and conc <= -BAND
    ma_down = bool(boll) and boll["ma_dir"] == "down"
    if boll and buy_gate and boll["open_boll"]:
        add("R03", "開布林·主力大買", "buy")
    if boll and buy_gate and boll["bw_level"] == "寬" and boll["touch_lb"]:
        add("R14", "大帶寬觸下軌·主力買", "buy")
    if s2.get("buy"):
        add("R24", f"主力連續{STREAK_DAYS}日買超≥{BAND:g}%·{pos}", "buy")
    if s2.get("sell"):
        add("R41", f"主力連續{STREAK_DAYS}日賣超≥{BAND:g}%·{pos}", "sell")
    it = ctx.get("it_d1")
    if it:
        if buy_gate and it > 0:
            add("R22", "主投同買", "buy")
        elif sell_gate and it < 0:
            add("R22", "主投同賣", "sell")
        elif sell_gate and it > 0:
            add("R22", "主賣投買（不算：投信可能幫人出貨）", "neutral")
    pool = None
    if ma_down and sell_gate and boll["bw_level"] == "寬":
        pool = "R11"
    elif ma_down and (ctx.get("bias_year") or 0) > HIGH_OUT_BIAS:
        pool = "R12"
    if pool:
        add(pool, "高檔出貨池" + ("（月線下彎·主力賣·大帶寬）" if pool == "R11" else f"（月線下彎·乖離年線>{HIGH_OUT_BIAS * 100:g}%）"), "sell")
        if boll["upper_half"]:
            add("R13", "反彈至月線上·逃命點", "sell")
    if ma_down and sell_gate:
        add("R16a", "短線空頭（月線下彎＋主力賣）", "sell")
    if boll and boll["upper_half"] and (ctx.get("big_sell_share") or 0) >= BIG_SELL_SHARE:
        add("R21c", "高檔大分點狂賣", "sell")
    hab = [h for h in ctx.get("habit") or [] if h["flips"] >= 1]
    if hab:
        strong = any(h["strong"] for h in hab)
        add("R21b", "隔日沖慣犯大買（該股" + ("≥2次" if strong else "1次") + "）", "sell")
        if pool and ctx.get("red"):
            sold = ctx.get("holder_sell")
            add("S17", "隔日空候選（高檔出貨池＋收紅＋慣犯大買；大股東賣：" + ("有點名公司派分點在賣" if sold else "缺資料") + "）", "sell")
    if ctx.get("thin"):
        add("R27", "量小（20日均量不足）", "neutral")
    return out


def analyse(sid: str, name: str, data: dict, asof: str, cal: list[str], named: dict[str, dict] | None = None) -> dict:
    """cal＝市場交易日（升冪、含資料日）。個股序列一律以市場日曆為準：缺的那天就是缺，不會被跳過去把窗口悄悄拉長；日曆外的日子（殭屍列）不採用。"""
    cal = [d for d in cal if d <= asof]
    tape, it = data["tape"], data["it"]
    named = named_for(sid) if named is None else named
    calset = set(cal)
    bars = {d: v for d, v in data["bars"].items() if d in calset}
    have = [d for d in cal if d in bars]
    last = have[-1] if have else None
    row: dict = {"sid": sid, "name": name, "date": last}
    if last is None:
        return {**row, "status": "no_price"}
    vol, close = bars[last][0], bars[last][1]
    if last != asof:
        return {**row, "status": "stale_price", "close": close, "chg_pct": None, "vol_lots": round(vol / 1000), "it": {}}
    pday = cal[-2] if len(cal) >= 2 else None
    prev = bars[pday][1] if pday in bars else None
    row.update(close=close, chg_pct=round((close / prev - 1) * 100, 2) if prev else None, vol_lots=round(vol / 1000))
    recent_vol = [bars[d][0] for d in have[-20:]]
    row["avg20_lots"] = round(sum(recent_vol) / len(recent_vol) / 1000) if recent_vol else None
    row["thin"] = bool(recent_vol) and sum(recent_vol) / len(recent_vol) / 1000 < THIN_LOTS
    closes = [bars[d][1] for d in have]
    row["boll"] = boll_position.boll_position(closes, bars[last][4])
    row["bias_year"] = None if boll_position.bias_year(closes) is None else round(boll_position.bias_year(closes) * 100, 1)
    row["red"] = bool(bars[last][2] and close > bars[last][2])

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

    row["it"] = {f"d{n}": it_win(n) for n in (1, 5, 20)}
    row["it_missing"] = last not in it

    if not row["tape_ok"]:
        row.update(status="tape_incomplete", win={}, buyers=[], sellers=[], rules=[], flags=[], habit=[], named=[], streak2=None)
        return row

    stats = {n: window_stats(tape, run[-n:], modes, vols) for n in WINDOWS if len(run) >= n}
    row["win"] = {str(n): {"main_lots": s["main_lots"], "conc_pct": s["conc_pct"]} for n, s in stats.items()}
    d1 = stats[1]
    row["buyers"] = top_seats(tape[last], +1, named)
    row["sellers"] = top_seats(tape[last], -1, named)
    row["day"] = row["win"]["1"]            # 相容：當日主力
    c = d1["conc_pct"]
    row["signal"] = "buy" if c is not None and c >= BAND else "sell" if c is not None and c < -BAND else "neutral"

    conc_prev = None
    if len(run) >= STREAK_DAYS:
        pv = window_stats(tape, [run[-2]], modes, vols)["conc_pct"]
        conc_prev = pv
        both = [c, pv]
        row["streak2"] = {"prev_conc_pct": pv, "buy": all(x is not None and x >= BAND for x in both), "sell": all(x is not None and x <= -BAND for x in both)}
    else:
        row["streak2"] = None

    # 隔日沖：逐股看紀錄（src/branch_habit.py）。D 只含完整日；habit_of 以「相鄰交易日」成對，缺日不成對
    cut = (_date.fromisoformat(asof) - timedelta(days=HABIT_DAYS)).isoformat()
    hdays = [d for d in cal if d >= cut]
    D = {(sid, d): {tid: (r[1], r[3]) for tid, r in tape[d].items()} for d in hdays if modes.get(d)}
    bvol = {(sid, d): bars[d][0] for d in hdays if d in bars}
    names = {tid: r[0] for tid, r in tape[last].items()}
    buyers_big = branch_habit.top_buyers(D.get((sid, last), {}), vol)
    row["habit"] = branch_habit.habit_summary(D, bvol, hdays, sid, last, buyers_big, names)

    # 小哥點名分點（逐股）：今日動向。top 日沒列入＝不在前段名單（None，不是 0）
    nm_today = []
    for tid, info in named.items():
        r = tape[last].get(tid)
        if r is None and row["tape_mode"] == "full":
            continue
        nm_today.append({"id": tid, "name": info["name"], "role": info["role"], "lots": round(r[2] / 1000) if r else None})
    row["named"] = sorted(nm_today, key=lambda x: -abs(x["lots"] or 0))

    top_sell = sorted(tape[last].values(), key=lambda r: r[2])[:1]
    big_sell_share = (-top_sell[0][2] / vol) if top_sell and top_sell[0][2] < 0 and vol else 0.0
    holder_sell = any(x["role"] == "公司派" and (x["lots"] or 0) < 0 for x in row["named"])
    ctx = {"conc": c, "streak2": row["streak2"], "boll": row["boll"], "bias_year": (row["bias_year"] / 100) if row["bias_year"] is not None else None,
           "red": row["red"], "it_d1": (row["it"].get("d1") or {}).get("lots"), "big_sell_share": big_sell_share,
           "habit": row["habit"], "holder_sell": holder_sell, "thin": row["thin"]}
    row["rules"] = rule_flags(ctx)
    row["flags"] = [r["label"] for r in row["rules"]]
    row["status"] = "ok"
    return row


def universe() -> list[tuple[str, str]]:
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    return [(str(r["sid"]), str(r.get("name") or r["sid"])) for r in calib["universe"] if str(r["sid"]) not in EXCLUDE_STOCKS]


def _minus(conn: sqlite3.Connection, d: str, days: int) -> str:
    return conn.execute("SELECT date(?, ?)", (d, f"-{days} day")).fetchone()[0]


def compute_rows(conn: sqlite3.Connection, days: list[str], uni: list[tuple[str, str]] | None = None) -> dict[str, list[dict]]:
    """唯一的計算路徑：逐股一次載入整段、再逐日截斷（PIT：每日只看 ≤ 該日、回看各自的 LOOKBACK）。單日＝days 只有一天。"""
    uni = universe() if uni is None else uni
    since = _minus(conn, days[0], BAR_LOOKBACK_DAYS)
    market = market_calendar.trading_days(conn, since, days[-1])
    not_trading = [d for d in days if d not in set(market)]
    if not_trading:
        raise ValueError(f"非交易日（官方日 K 列數不足）：{','.join(not_trading)}")
    per_day: dict[str, list[dict]] = {d: [] for d in days}
    lookback = {"bars": BAR_LOOKBACK_DAYS, "tape": TAPE_LOOKBACK_DAYS, "it": IT_LOOKBACK_DAYS}
    for sid, nm in uni:
        full = load_stock(conn, sid, days[-1], since, _minus(conn, days[0], TAPE_LOOKBACK_DAYS), _minus(conn, days[0], IT_LOOKBACK_DAYS))
        named = named_for(sid)
        for d in days:
            los = {k: _minus(conn, d, v) for k, v in lookback.items()}
            cut = {k: {x: v for x, v in full[k].items() if los[k] <= x <= d} for k in ("bars", "tape", "it")}
            cut["full"] = full["full"]
            per_day[d].append(analyse(sid, nm, cut, d, [c for c in market if los["bars"] <= c <= d], named))
    return per_day


PARAMS = {
    "top_n": TOP_N, "windows": list(WINDOWS), "complete_ratio": COMPLETE_RATIO, "min_branches": MIN_BRANCHES, "top_min_side": TOP_MIN_SIDE,
    "thin_lots": THIN_LOTS, "band_pct": BAND, "streak_days": STREAK_DAYS, "seats_shown": SEATS_SHOWN, "big_sell_share_pct": BIG_SELL_SHARE * 100,
    "habit_days": HABIT_DAYS, "habit_minsh_pct": branch_habit.MINSH * 100, "habit_alpha": branch_habit.ALPHA, "habit_topn": branch_habit.TOPN,
    "bw_very_narrow_pct": boll_position.BW_VERY_NARROW * 100, "bw_narrow_pct": boll_position.BW_NARROW * 100, "bw_wide_pct": boll_position.BW_WIDE * 100,
    "high_out_bias_pct": HIGH_OUT_BIAS * 100,
}


def make_snapshot(conn: sqlite3.Connection, asof: str, rows: list[dict], source: str) -> dict:
    """唯一的快照組裝點：metadata、日曆、警告都只在這裡產生。source＝live（排程當日）或 backfill（事後補算）。"""
    cal = market_calendar.calendar_for(conn, asof)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "asof": asof,
        "source": source,
        "calendar": cal,
        "params": dict(PARAMS),
        "gaps": list(GAPS),
        "warnings": make_warnings(rows),
        "rows": rows,
    }


def build(asof: str | None = None) -> dict:
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        asof = asof or market_calendar.latest_trading_day(conn, datetime.now().date().isoformat())
        rows = compute_rows(conn, [asof])[asof]
        return make_snapshot(conn, asof, rows, "live")
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
    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        per_day = compute_rows(conn, days)
        for d, rows in per_day.items():
            write_json(OUT_DIR / f"chip_sop_{d}.json", make_snapshot(conn, d, rows, "backfill"))
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
