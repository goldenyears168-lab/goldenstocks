"""個股現貨 × 個股期貨『基差』資料層（2026-10-21 jack 交辦；規格 docs/futures-basis-spec.md）。唯讀、不進分數、不下單。

定義（全站一致：主表、/stock 副圖、/grid 小數字）：
    基差% = (期貨中價 ÷ 現貨中價 − 1) × 100；中價 = (買一 + 賣一) / 2。
    期貨中價 = `{root}_books` 五檔簿最新一筆（日盤、非 stale、買一<賣一）；現貨中價 = 逐筆帶的買一賣一（缺值退回成交價）。
為什麼用中價：個股期貨流動性差，最後成交價基差逐分鐘波動比中價大 10~40%，且 76~100% 的分鐘才有成交；五檔簿 100% 的分鐘有更新
（10/20 台虹、南亞科、景碩、金像電、玉晶光、旺宏實測）。

合約：用收集器實際寫進檔案的 symbol（近月；收集器每 ≤58 分鐘重新解析、結算日隔天自動換月，9/17 換月實測無斷）。
符號 `KIFJ6`＝KI＋F(標準)＋J(10月)＋6(2026)；`PK1J6` 之類是減資等調整後合約（非標準，與主表挑的標準契約可能不同）→ 標題警告。
時段：基差只算 09:00～13:25（13:25 起現貨進入收盤競價沒有連續成交；結算日 9/16 實測 13:25～13:29 基差不收斂反而越來越負，是時間不同步）。
結算日（合約月第三個星期三）：到期合約日盤 13:30 收盤、以收盤價結算 → 標註。除息日：`stock_ex_adjust_event`（證交所除權息計算結果，事後當天有）→ 標註；
預先公告資料（`stock_dividend_history`）目前追蹤池沒有未來除息日，所以無法事先提醒。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from stock_db import DATA_DIR, DEFAULT_DB_PATH

ROOT_DIR = Path(__file__).resolve().parents[3]
MAP_PATH = ROOT_DIR / "config" / "taifex_stock_futures_map.json"
CACHE = DATA_DIR.parent / "cache"
OPEN_MIN, CLOSE_MIN = 9 * 60, 13 * 60 + 25      # 基差只算 09:00～13:25
CARRY_MIN = 3                                   # 該分鐘沒有簿更新時，沿用前 ≤3 分鐘的中價
MIN_REFRESH = 2.0                               # 今天的檔：兩次增量讀取至少間隔秒數
MONTHS = "ABCDEFGHIJKL"

_LOCK = threading.Lock()
_MAP: dict = {}
_DIRS: dict = {}
_DAYS: dict = {}
_EXDIV: dict = {}


def _load_map() -> dict:
    if not _MAP:
        try:
            rows = json.loads(MAP_PATH.read_text(encoding="utf-8"))["rows"]
        except Exception:  # noqa: BLE001
            rows = []
        for r in rows:
            _MAP.setdefault(r["stock_id"], []).append(r)
    return _MAP


def futures_dir_for(sid: str):
    """回傳 (目錄前綴, 對照表列)：優先標準契約（2,000 股），沒有收集才用小型（100 股）。目錄名有 root（ca）與 futures_id（ccf）兩種寫法。"""
    if sid in _DIRS:
        return _DIRS[sid]
    res = None
    rows = sorted(_load_map().get(sid, []), key=lambda r: 0 if r.get("contract_size") == "2,000" else 1)
    for r in rows:
        for d in (r["root"].lower(), r["futures_id"].lower()):
            if (CACHE / f"{d}_books").is_dir():
                res = (d, r)
                break
        if res:
            break
    _DIRS[sid] = res
    return res


def parse_symbol(sym: str):
    """'KIFJ6' → {'standard': True, 'month': 10, 'year_digit': 6, 'root': 'KI'}；'PK1J6' → standard False。無法解析回 None。"""
    if not sym or len(sym) < 4 or sym[-2] not in MONTHS or not sym[-1].isdigit():
        return None
    return {"standard": sym[-3] == "F", "month": MONTHS.index(sym[-2]) + 1, "year_digit": int(sym[-1]), "root": sym[:-3]}


def third_wednesday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first = (2 - d.weekday()) % 7 + 1                # 第一個星期三的日
    return date(year, month, first + 14)


def contract_expiry(sym: str, ref_day: str):
    """合約到期日（第三個星期三；假日順延沒有資料可查，以曆法為準）。年份用 ref_day 的年代推（個位數年）。"""
    p = parse_symbol(sym)
    if not p:
        return None
    ry = int(ref_day[:4])
    year = ry - ry % 10 + p["year_digit"]
    if year < ry - 5:
        year += 10
    try:
        return third_wednesday(year, p["month"])
    except ValueError:
        return None


def is_settlement_day(sym: str, day: str) -> bool:
    e = contract_expiry(sym, day)
    return bool(e and e.isoformat() == day)


def exdiv_info(sid: str, day: str):
    """除息／除權日當天的資料（證交所除權息計算結果）；沒有回 None。結果快取 5 分鐘。"""
    k = (sid, day)
    hit = _EXDIV.get(k)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    info = None
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        row = conn.execute("SELECT prev_close, ref_price, kind FROM stock_ex_adjust_event WHERE stock_id=? AND anchor_date=? AND anchor_kind='ex'",
                           (sid, day)).fetchone()
        conn.close()
        if row:
            info = {"prev_close": row[0], "ref_price": row[1], "kind": row[2]}
    except Exception:  # noqa: BLE001
        info = None
    _EXDIV[k] = (time.time(), info)
    return info


def mid(bid, ask, last=None):
    """中價。買賣一缺值（<=0 或 None）退回 last；都沒有回 None。"""
    try:
        b, a = float(bid), float(ask)
        if b > 0 and a > 0 and b <= a:
            return (b + a) / 2
    except (TypeError, ValueError):
        pass
    return float(last) if last else None


def basis_pct(fmid, smid):
    return None if not fmid or not smid else (fmid / smid - 1) * 100


class _FutDay:
    """單一股票單一日的期貨五檔簿：增量讀檔，每分鐘保留最後一筆中價。"""

    def __init__(self, path: Path):
        self.path, self.off, self.mins, self.syms, self.t = path, 0, {}, Counter(), 0.0

    def update(self, today: bool) -> None:
        if not self.path.exists():
            return
        if self.off and (not today or time.time() - self.t < MIN_REFRESH):
            return
        self.t = time.time()
        with open(self.path, "rb") as f:
            f.seek(self.off)
            chunk = f.read()
        nl = chunk.rfind(b"\n")
        if nl < 0:
            return
        self.off += nl + 1
        for line in chunk[: nl + 1].split(b"\n"):
            if not line or b'"stale": false' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("session") != "day" or r.get("quote_type", "FUTURE") != "FUTURE":
                continue
            bids, asks = r.get("bids") or [], r.get("asks") or []
            if not bids or not asks:
                continue
            bb, aa = bids[0].get("price"), asks[0].get("price")
            if not bb or not aa or bb >= aa:               # 鎖死／交叉簿不用
                continue
            ts = r.get("ts") or ""
            try:
                mk = int(ts[11:13]) * 60 + int(ts[14:16])
            except ValueError:
                continue
            if OPEN_MIN <= mk <= CLOSE_MIN:
                self.mins[mk] = ((bb + aa) / 2, bb, aa)
                self.syms[r.get("symbol")] += 1


def series(sid: str, day: str):
    """個股頁用：回傳 {'dir','row','sym','standard','mini','label','settle','exdiv','fmid':{'HH:MM':中價},'n'}；沒有期貨或沒資料回 None。"""
    fd = futures_dir_for(sid)
    if not fd:
        return None
    d, row = fd
    today = datetime.now().strftime("%Y-%m-%d") == day
    with _LOCK:
        k = (sid, day)
        fday = _DAYS.get(k)
        if fday is None:
            if len(_DAYS) > 60:
                _DAYS.pop(next(iter(_DAYS)))
            fday = _DAYS[k] = _FutDay(CACHE / f"{d}_books" / f"{d}_books_{day}.jsonl")
        fday.update(today)
        mins, syms = dict(fday.mins), Counter(fday.syms)
    if not mins:
        return None
    sym = syms.most_common(1)[0][0] if syms else None
    p = parse_symbol(sym) if sym else None
    out, last = {}, None
    for mk in range(OPEN_MIN, CLOSE_MIN + 1):
        if mk in mins:
            last = (mk, mins[mk][0])
        if last and mk - last[0] <= CARRY_MIN:
            out[f"{mk // 60:02d}:{mk % 60:02d}"] = last[1]
    mini = row.get("contract_size") != "2,000"
    label = None
    if sym:
        label = f"{sym}（{p['month']}月契約" + ("·小型100股" if mini else "") + ("" if (p and p["standard"]) else "·非標準調整後合約") + "）" if p else sym
    return {"dir": d, "row": row, "sym": sym, "standard": bool(p and p["standard"]), "mini": mini, "label": label,
            "settle": bool(sym and is_settlement_day(sym, day)), "exdiv": exdiv_info(sid, day), "fmid": out, "n": len(mins)}


def header_html(info, last_basis):
    """/stock 標題列的期貨資訊與警示（沒有期貨回空字串）。"""
    if not info:
        return ""
    bits = [f"<span class='dim'>期貨 {info['label'] or '—'}</span>"]
    if last_basis is not None:
        bits.append(f"<b class='{'up' if last_basis > 0 else 'dn' if last_basis < 0 else ''}'>基差 {last_basis:+.2f}%</b>")
    if not info["standard"] and info["sym"]:
        bits.append("<span style='color:#d29922' title='逐筆/簿檔實際合約不是標準契約(F)；主表挑標準契約，兩邊數字可能不同'>⚠ 非標準合約</span>")
    if info["settle"]:
        bits.append("<span style='color:#d29922' title='到期合約日盤 13:30 收盤、以收盤價結算；13:25 後現貨進入收盤競價，基差只算到 13:25'>⚠ 結算日</span>")
    if info["exdiv"]:
        e = info["exdiv"]
        bits.append(f"<span style='color:#d29922' title='除息/除權日(昨收{e['prev_close']}→參考價{e['ref_price']})；期貨契約調整與現貨不同步時基差可能失真'>⚠ 除息日</span>")
    return "<div class='dim' style='margin:2px 0 4px 0'>" + " · ".join(bits) + "</div>"
