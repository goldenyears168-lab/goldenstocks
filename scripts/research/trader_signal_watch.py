#!/usr/bin/env python3
"""盤中訊號監看 —— 符合條件就寄信通知，人用手機自己下單。

jack 2026-10-04 定案：「妳先不要動富邦 API，避免明天資料沒有蒐集好的風險，
我明天手機下單，你負責幫我過濾訊號通知我即可。」

## 硬約束：零富邦連線
本程式**只讀檔**，不 import 富邦 SDK、不開任何連線：
  · 現貨逐筆  `cache/biglot_live_watch/raw_{日}.jsonl`（biglot-live-watch 寫的）
  · 期貨五檔  `cache/{root}_books/{root}_books_{日}.jsonl`（futopt-books-collect 寫的）
增量讀取（記住 byte offset）—— raw 檔一天會長到 ~120MB，不能每輪重讀。
它掛掉不會影響任何收集器，因為它跟它們沒有共用資源。

## 訊號（2026-10-04 sweep 定案，口徑全部照他的問卷）
  · 戰場 **23 檔**（config/biglot_roles.json 推導），不是 42 檔
  · **散戶賣給大戶**：大戶淨買>0 ∧ 散戶淨賣<0，**30分 與 全日 都要成立**
    （相減會把「大戶大買但散戶也買」跟「大戶小買但散戶大賣」混成同一個數字）
  · 成交額 30 分 前 30%（橫斷面排名用全部 42 檔算 —— 他畫面上就是這樣排的）
  · **股價共動夥伴**內 當日漲幅最低一半（不用 SUBCAT：31 群裡 24 個只有 1 檔）
  · 距當日高 ≤ −1%（不追高；實測貼著高點追是 −0.216%）
  · 避開 12–13 點盤（該時段超額 −0.802%、四天日一致 0/4）

## 建議掛價
期貨**買一往下 1 檔**。他的實際習慣是掛低幾檔等回檔（掛價距中價 IQR 4.7 檔、
新倉成交率 48%、中位等 6.4 分），不是吃對手價。期貨買賣價差中位 0.276%，
吃對手價等於每趟先付掉它。

⚠ 這些門檻是從 2026-09-29→10-02 四天反向工程來的，**樣本內**。
通知只是提醒你去看，不是進場指令。
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")

DATA = Path.home() / "goldenstocks-data"
RAW_DIR = DATA / "cache/biglot_live_watch"
CACHE = DATA / "cache"
STATE_DIR = DATA / "data/clone_agent"
#: 宇宙與昨收的 SSOT。注意不在 biglot_live_watch/ 底下 —— 儀表板讀
#: pit_universe_tick/_live_calib.json、收集器讀 _live_calib_v3.json（兩檔並存,
#: 見 sector-comovement 記憶裡「兩檔都要改且一致」那條）。這裡跟儀表板同源。
CALIB = DATA / "data/cache/pit_universe_tick/_live_calib.json"

BIG_AMT = 10_000_000          # 單筆主動成交 ≥1000 萬 = 大戶
RETAIL_CAP = 5_000_000        # 1 張且 <500 萬 = 散戶
GAP_SECONDS, GAP_JUMP_AMT = 90, 500_000_000
# ── 門檻分兩層（2026-10-04 jack 要求「卡片大概 50 筆以上,我還要審批」）──
#
# 寬門檻決定「會不會跳卡」,嚴門檻決定「排在多前面」。他要的是一份**有富集的短名單**,
# 自己做第二關,不是一份已經篩到剩 5 張的清單。網格搜尋的結果（4 天、23 檔戰場）：
#   嚴（原 v5c）  5.5 張/日 · 超額 +1.953% · 4/4 · 9 檔
#   寬（現行）   48.0 張/日 · 超額 +0.872% · 4/4 · 20 檔   ← 仍是無條件基準 +0.432% 的 2 倍
# 代價是超額砍 55%,換來 8.7 倍的候選量與 2.2 倍的標的涵蓋。
AMT_PCT_MIN = 30              # 寬：成交額 30 分 前 70%
AMT_PCT_STRICT = 70           # 嚴：前 30%
PULLBACK_MAX = -0.5           # 寬：距當日高 ≤ −0.5%
PULLBACK_STRICT = -1.0        # 嚴：≤ −1%
CHG_CM_STRICT = 50            # 嚴：共動夥伴內當日漲幅最低一半
SKIP_HOURS = (12, 13)
RECHECK_MIN = 20              # 同一檔重複跳卡的冷卻（分鐘）


def tick_size(px: float) -> float:
    for lim, t in ((10, .01), (50, .05), (100, .1), (500, .5), (1000, 1.)):
        if px < lim:
            return t
    return 5.


def core_universe() -> set[str]:
    r = json.loads(Path("config/biglot_roles.json").read_text(encoding="utf-8"))
    drop = ({x["sid"] for x in r["reference_in_pool"]}
            | {x["sid"] for x in r["independent"]} | {"2481", "1303"})
    cal = json.loads(CALIB.read_text(encoding="utf-8"))
    return {str(r_["sid"]) for r_ in cal["universe"]} - drop


def comove_peers(uni: set[str]) -> dict[str, list[str]]:
    p = CACHE / "biglot_live_watch/comovement_broad.json"
    if not p.exists():
        return {}
    S = json.loads(p.read_text(encoding="utf-8"))["stocks"]
    return {s: [s] + [q["sid"] for q in (S.get(s, {}).get("partners") or [])
                      if q.get("tier") in ("A", "B") and q["sid"] in uni] for s in uni}


class Tape:
    """增量讀 raw_{日}.jsonl，維護每檔的滾動大戶／散戶／成交額。"""

    def __init__(self, day: str):
        self.path = RAW_DIR / f"raw_{day}.jsonl"
        self.off = 0
        self.ev: dict[str, deque] = defaultdict(deque)   # (t, amt, big, ret)
        self.day_amt: dict[str, float] = defaultdict(float)
        self.day_big: dict[str, float] = defaultdict(float)
        self.day_ret: dict[str, float] = defaultdict(float)
        self.px: dict[str, float] = {}
        self.hi: dict[str, float] = {}
        self._lastvol: dict[str, float] = defaultdict(float)
        self._seen: set[str] = set()
        self._bid: dict[str, float] = {}
        self._ask: dict[str, float] = {}
        self._lastpx: dict[str, float] = {}
        self._lastseen: dict[str, float] = {}
        self._pxev: dict[str, list] = defaultdict(list)   # (t, px) 供重放時還原當下價

    def poll(self) -> int:
        if not self.path.exists():
            return 0
        n = 0
        with open(self.path) as fh:
            fh.seek(self.off)
            for line in fh:
                if not line.endswith("\n"):          # 寫到一半的行，下輪再讀
                    break
                self.off += len(line.encode())
                if '"trades"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("kind") != "message":
                    continue
                msg = rec.get("payload") or {}
                if msg.get("channel") != "trades" or msg.get("event") not in (None, "data"):
                    continue
                d = msg.get("data") or {}
                sid = str(d.get("symbol", ""))
                px, vol, ts = d.get("price"), d.get("volume"), rec.get("ts")
                if not sid or px is None or vol is None or d.get("isTrial") or not ts:
                    continue
                px, vol = float(px), float(vol)
                dv = vol - self._lastvol[sid]
                self._lastvol[sid] = vol
                if dv <= 0 or d.get("isOpen") or d.get("isClose"):
                    continue
                if sid not in self._seen:            # 第一筆是累計基準，不算流量
                    self._seen.add(sid); self.px[sid] = px; self.hi[sid] = px
                    continue
                b, a = d.get("bid"), d.get("ask")
                if b is not None:
                    self._bid[sid] = float(b)
                if a is not None:
                    self._ask[sid] = float(a)
                side = 1 if (a is not None and px >= float(a)) else (
                    -1 if (b is not None and px <= float(b)) else 0)
                if side == 0:
                    p = self._lastpx.get(sid)
                    side = 0 if p is None else (1 if px > p else (-1 if px < p else 0))
                self._lastpx[sid] = px
                t = datetime.fromisoformat(ts).timestamp()
                gap = sid in self._lastseen and (t - self._lastseen[sid]) > GAP_SECONDS
                self._lastseen[sid] = t
                amt = px * dv * 1000
                if gap and amt >= GAP_JUMP_AMT:      # 斷線後的補量不算
                    continue
                sgn = 1 if side > 0 else (-1 if side < 0 else 0)
                big = sgn * amt if amt >= BIG_AMT else 0.0
                ret = sgn * amt if (dv == 1 and amt < RETAIL_CAP) else 0.0
                self.ev[sid].append((t, amt, big, ret))
                self.day_amt[sid] += amt; self.day_big[sid] += big; self.day_ret[sid] += ret
                self.px[sid] = px
                self.hi[sid] = max(self.hi.get(sid, px), px)
                self._pxev[sid].append((t, px))
                n += 1
        return n

    def window(self, sid: str, now: float, sec: float | None):
        """`sec=None` 代表當日累計。

        ★ 必須同時擋掉 `t > now` 的**未來**事件。live 跑的時候檔案裡本來就只有過去,
        但歷史重放（--asof）時 deque 裡有整天的資料,不擋就是前視偏誤 —— 驗證會
        變成作弊。這個 bug 在第一版就有,靠重放對不上數字才抓到。
        """
        lo = -1e18 if sec is None else now - sec
        amt = big = ret = 0.0
        for t, a, bg, rt in reversed(self.ev[sid]):
            if t > now:
                continue
            if t < lo:
                break
            amt += a; big += bg; ret += rt
        return amt, big, ret

    def px_at(self, sid: str, now: float):
        """(最後成交價, 當日最高) —— 同樣只看 <= now。"""
        last = hi = None
        for t, *_ in ():                              # 佔位,下面用 _pxev
            pass
        for t, px in self._pxev.get(sid, ()):
            if t > now:
                break
            last = px
            hi = px if hi is None else max(hi, px)
        return last, hi


_FUT_CACHE: dict[tuple[str, str], tuple[float, list, list, list]] = {}


def fut_quote(root: str, day: str, now: float):
    """期貨買一／賣一 **在 now 當下**的值。只讀檔,不連富邦。

    三個坑都處理了：
      · 不能只取檔尾最後一筆 —— 重放時那是收盤報價（前視）;要用時間索引取 ≤ now。
      · `stale=True` 的快照要丟（買賣簿逾時沒更新,價格是凍的）。
      · bp[0]/ap[0] 偶爾回 0.0 搭配巨量,bp[1]/ap[1] 才是真最優價
        （見 [[watchlist-books-zero-price-quirk]]）—— 一律過濾 price>0。

    檔案會持續長大,所以快取記錄上次讀到的 byte offset,每次只讀新增的部分。
    """
    key = (root, day)
    for cand in (root.lower(), root.lower() + "f"):
        path = CACHE / f"{cand}_books" / f"{cand}_books_{day}.jsonl"
        if path.exists() and path.stat().st_size > 100:
            break
    else:
        return None, None
    off, ts, bids, asks = _FUT_CACHE.get(key, (0.0, [], [], []))
    if path.stat().st_size > off:
        with open(path) as fh:
            fh.seek(int(off))
            for line in fh:
                if not line.endswith("\n"):
                    break
                off += len(line.encode())
                if '"bids"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if r.get("stale") or r.get("session") != "day":
                    continue
                bb = [x for x in (r.get("bids") or []) if float(x.get("price") or 0) > 0]
                aa = [x for x in (r.get("asks") or []) if float(x.get("price") or 0) > 0]
                if not bb or not aa:
                    continue
                try:
                    t = datetime.fromisoformat(r["ts"]).timestamp()
                except Exception:  # noqa: BLE001
                    continue
                ts.append(t); bids.append(float(bb[0]["price"])); asks.append(float(aa[0]["price"]))
        _FUT_CACHE[key] = (off, ts, bids, asks)
    if not ts:
        return None, None
    j = bisect.bisect_right(ts, now) - 1
    if j < 0 or now - ts[j] > 300:        # 超過 5 分鐘沒更新就當沒有報價
        return None, None
    return bids[j], asks[j]


def pct_rank(vals: dict[str, float], sid: str) -> float | None:
    """橫斷面百分位。分母用**全部有成交的標的**（他畫面上就是這樣排的），
    不是只用 23 檔戰場 —— 排名的參考系跟下單的候選集刻意不同。"""
    xs = [v for v in vals.values() if v is not None]
    if len(xs) < 5 or vals.get(sid) is None:
        return None
    return 100 * sum(v < vals[sid] for v in xs) / len(xs)


STAR_LABEL = {4: "★★★ 嚴格條件全過", 3: "★★☆ 過 3/4", 2: "★★ 過 2/4",
              1: "★ 過 1/4", 0: "☆ 僅達寬門檻"}


def build_card(sid: str, name: str, now: float, feat: dict, root: str, day: str) -> dict:
    bid, ask = fut_quote(root, day, now)
    lim = round(bid - tick_size(bid), 2) if bid else None
    hits, risks = [], []
    mark = lambda ok: "✓嚴" if ok else "·寬"   # noqa: E731
    st = feat.get("strict", [False] * 4)
    hits.append(f"{mark(st[0])} 散戶賣給大戶："
                + ("30分＋全日都成立" if feat.get("ok30") and feat.get("okday")
                   else ("僅 30分成立" if feat.get("ok30") else "僅 全日成立")))
    hits.append(f"　　30分 大戶{feat['big30']:+.0f}萬／散戶{feat['ret30']:+.0f}萬"
                f"｜全日 大戶{feat['bigday']:+.0f}萬／散戶{feat['retday']:+.0f}萬")
    hits.append(f"{mark(st[1])} 成交額30分 第{feat['amt_pct']:.0f}百分位（寬≥前70% / 嚴≥前30%）")
    hits.append(f"{mark(st[2])} 距當日高 {feat['dist_hi']:+.2f}%（寬≤−0.5% / 嚴≤−1%）")
    if feat.get("chg_cm") is not None:
        hits.append(f"{mark(st[3])} 共動夥伴內 當日漲幅第{feat['chg_cm']:.0f}百分位"
                    f"（{feat['n_cm']}檔夥伴，嚴≤50）")
    else:
        hits.append("·寬 共動夥伴不足 2 檔，無法做族群內比較")
    if bid is None:
        risks.append("⚠ 抓不到期貨買賣價，掛價僅供參考")
    elif ask and bid:
        sp = 100 * (ask - bid) / ((ask + bid) / 2)
        if sp > 0.4:
            risks.append(f"⚠ 期貨價差 {sp:.2f}% 偏寬（中位 0.276%），吃對手價會很貴")
    risks.append("⚠ 門檻由 2026-09-29→10-02 四天反向工程而來，屬樣本內；這是提醒不是指令")
    stars = feat.get("stars", 0)
    return {
        "day": day, "time": datetime.fromtimestamp(now).strftime("%H:%M:%S"),
        "symbol": f"{root}({name})", "uname": name, "side": "Buy",
        "stars": stars, "tier": STAR_LABEL[stars],
        "action": "新倉買進", "price": lim if lim else round(feat["px"], 2),
        "price_note": (f"期貨買一 {bid} / 賣一 {ask} → 建議掛 買一下1檔 = {lim}"
                       if bid else "無期貨報價"),
        "lot": 1, "shares": feat["shares"], "rate": feat["mrate"],
        "equity": 1_748_587, "lot_cap": feat["lot_cap"],
        "margin": round((lim or feat["px"]) * feat["shares"] * feat["mrate"]),
        "leg_note": f"每口保證金率 {feat['mrate']*100:.2f}%",
        "hits": hits, "bonus": [], "risks": risks, "verdict": "ok",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--start", default="09:00")
    ap.add_argument("--end", default="13:25")
    ap.add_argument("--once", action="store_true", help="只跑一輪（測試用）")
    ap.add_argument("--day", default=None)
    ap.add_argument("--scan", action="store_true",
                    help="歷史日全掃：讀一次檔後,把當天每分鐘都跑一遍（驗訊號量用）")
    ap.add_argument("--asof", default=None,
                    help="歷史日重放用的時點 HH:MM（讓視窗算在那個當下,而不是現在）")
    args = ap.parse_args()

    from clone_agent.approval import intent_id

    day = args.day or datetime.now().strftime("%Y-%m-%d")
    core = core_universe()
    cal = json.loads(CALIB.read_text(encoding="utf-8"))
    full_uni = {str(r["sid"]) for r in cal["universe"]}
    peers = comove_peers(full_uni)
    specs = json.loads((DATA / "data/cache/taifex_stock_futures_specs.json").read_text())
    mr = json.loads((DATA / "data/cache/taifex_stock_futures_margin_rates.json").read_text())
    rates = {v["sid"]: v["原始%"] for v in mr.values() if v.get("原始%")}
    s2r, s2sh, s2nm = {}, {}, {}
    for root, sp in specs.items():
        sid = str(sp.get("underlying_id") or sp.get("stock_id") or "")
        n = sp.get("contract_shares") or 0
        if sid and n and n > s2sh.get(sid, 0):
            s2r[sid], s2sh[sid], s2nm[sid] = root, n, sp.get("name", sid)

    # 昨收要從 DB 拿 —— _live_calib.json 只有校準當下的 px,沒有昨收。
    # ma20_and_prevclose 是 PIT 的（只用 < day 的收盤）,且唯讀開 stocks.db。
    from trader_peer_group_choice import ma20_and_prevclose
    ref = ma20_and_prevclose(full_uni, day)
    prev_close = {k: v[1] for k, v in ref.items()}
    print(f"昨收載入 {len(prev_close)}/{len(full_uni)} 檔", flush=True)

    tape = Tape(day)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = STATE_DIR / f"candidates_{day}.jsonl"
    seen: dict[str, float] = {}

    def roll(new_day: str):
        """跨日換檔：重建 tape／候選檔／冷卻表。可以今晚就啟動,明早自動接上。"""
        nonlocal day, tape, out, seen, prev_close
        day = new_day
        tape = Tape(day)
        out = STATE_DIR / f"candidates_{day}.jsonl"
        seen = {}
        prev_close = {k: v[1] for k, v in ma20_and_prevclose(full_uni, day).items()}
        print(f"── 換日 {day} · 昨收 {len(prev_close)} 檔 ──", flush=True)
    print(f"訊號監看啟動 · {day} · 戰場 {len(core)} 檔 · 每 {args.interval}s 一輪 · "
          f"候選寫到 {out}\n⚠ 本程式不連富邦,只讀 {tape.path.name} 與期貨五檔快取", flush=True)

    scan_times = None
    if args.scan:
        t0 = datetime.fromisoformat(f"{day}T{args.start}:00")
        t1 = datetime.fromisoformat(f"{day}T{args.end}:00")
        scan_times = []
        while t0 <= t1:
            scan_times.append(t0); t0 += timedelta(minutes=1)
        tape.poll()
        print(f"全掃模式：{len(scan_times)} 個分鐘", flush=True)

    si = 0
    while True:
        if scan_times is not None:
            if si >= len(scan_times):
                print(f"全掃完成,共 {sum(1 for _ in open(out)) if out.exists() else 0} 筆候選",
                      flush=True)
                break
            now_dt = scan_times[si]; si += 1
        else:
            now_dt = datetime.now()
        hm = now_dt.strftime("%H:%M")
        if scan_times is None and not args.once:
            today = now_dt.strftime("%Y-%m-%d")
            if today != day:
                roll(today)
            if not (args.start <= hm <= args.end):
                # 盤前就睡、收盤後也睡（等明天）—— 不 return,這樣今晚啟動明早會自動接上
                time.sleep(min(300, args.interval * 5)); continue
        n = 0 if scan_times is not None else tape.poll()
        if args.asof:            # 歷史重放:把「現在」固定在指定時點
            now_dt = datetime.fromisoformat(f"{day}T{args.asof}:00")
            hm = args.asof
        now = now_dt.timestamp()
        amt30 = {s: tape.window(s, now, 1800)[0] for s in tape.ev}
        fired = []
        rej = defaultdict(int)          # 逐條件淘汰計數,沒訊號時才知道卡在哪
        for sid in core:
            if sid not in tape.ev or not tape.ev[sid]:
                continue
            a30, b30, r30 = tape.window(sid, now, 1800)
            _, bday, rday = tape.window(sid, now, None)      # 當日累計也要截在 now
            # 寬門檻:30 分【或】全日 成立即可（嚴門檻要兩個都成立,留給評級）
            ok30 = b30 > 0 and r30 < 0
            okday = bday > 0 and rday < 0
            if not (ok30 or okday):
                rej["散戶賣給大戶(30分或全日)"] += 1; continue                                   # 散戶賣給大戶：兩尺度都要
            ap_ = pct_rank(amt30, sid)
            if ap_ is None or ap_ < AMT_PCT_MIN:
                rej["成交額30分前70%"] += 1; continue
            if now_dt.hour in SKIP_HOURS:
                rej["避開12-13點"] += 1; continue
            px, hi = tape.px_at(sid, now)
            if not px or not hi:
                continue
            dist_hi = 100 * (px / hi - 1)
            if dist_hi > PULLBACK_MAX:
                rej["距當日高≤−0.5%"] += 1; continue                                   # 不追高
            # 族群排名改成**評級用**,不再當硬門檻（算不出來就記 None,不淘汰）
            ps = [x for x in peers.get(sid, [])
                  if prev_close.get(x) and tape.px_at(x, now)[0]]
            chg_cm = None
            if len(ps) >= 2:
                chg = {x: (tape.px_at(x, now)[0] or 0) / prev_close[x] for x in ps}
                chg_cm = 100 * sum(v < chg[sid] for v in chg.values()) / len(chg)                                   # 族群內挑漲幅低的
            if sid in seen and (now - seen[sid]) < RECHECK_MIN * 60:
                continue
            seen[sid] = now
            root = s2r.get(sid)
            if not root:
                continue
            # 評級：嚴門檻通過幾項 → ★★★ / ★★ / ★。他由上往下看,第一關等於做完了。
            strict = [ok30 and okday, ap_ >= AMT_PCT_STRICT,
                      dist_hi <= PULLBACK_STRICT,
                      chg_cm is not None and chg_cm <= CHG_CM_STRICT]
            stars = sum(strict)
            per = px * s2sh[sid] * rates.get(sid, 20.25) / 100
            c = build_card(sid, s2nm.get(sid, sid), now, {
                "stars": stars, "strict": strict, "ok30": ok30, "okday": okday,
                "big30": b30 / 1e4, "ret30": r30 / 1e4,
                "bigday": bday / 1e4, "retday": rday / 1e4,
                "amt_pct": ap_, "chg_cm": chg_cm, "n_cm": len(ps),
                "dist_hi": dist_hi, "px": px, "shares": s2sh[sid],
                "mrate": rates.get(sid, 20.25) / 100,
                "lot_cap": max(1, int((1_748_587 / 3) // per)) if per else 1,
            }, root, day)
            c["intent_id"] = intent_id(c)
            with open(out, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
            fired.append(f"{'★'*max(1,stars)}{c['uname']}@{c['price']}")
        if scan_times is not None:
            if fired:
                print(f"[{hm}] ★ {len(fired)}: {'、'.join(fired)}", flush=True)
            continue
        msg = f"[{hm}] 新事件 {n:>6} · 觀測 {len(tape.ev)} 檔"
        if fired:
            msg += f" · ★ 訊號 {len(fired)}: {'、'.join(fired)}"
        elif rej:
            msg += " · 淘汰於 " + " / ".join(f"{k}×{v}" for k, v in rej.items())
        print(msg, flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
