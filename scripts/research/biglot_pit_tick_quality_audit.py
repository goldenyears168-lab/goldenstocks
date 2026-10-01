"""pit_universe_tick 逐日資料品質稽核（2026-10-01）。

動機：`biglot_bigpct_robustness.py` 發現大戶佔比的橫斷面 IC 在 2026-08 走弱、
2026-09 標準化多空價差崩 13 倍。先排除資料污染再談「真衰減」。

稽核四項（每個交易日 × 100 檔）：
  ① 檔案覆蓋與盤中筆數（09:00–13:30）
  ② **TickType 組成** —— 最大嫌疑。TickType=1 為買方主動（見 finmind-ticktype-sign-test
     記憶：別用「下一筆漂移」判，踩過兩次）。若 FinMind 某段期間 TickType 退化
     （整欄同值／缺值），主動方簽號即失效 → bigpct 變雜訊，但筆數與成交額看起來正常。
  ③ 等間隔快照閘（複製 build_pit_tick_flow.py 的判準）
  ④ 時間覆蓋：首尾 tick 時戳、有成交的分鐘數
"""
import collections
import json
import math
import sys
from pathlib import Path

CACHE = Path.home() / "goldenstocks-data/data/cache/pit_universe_tick"
OPEN_T, CLOSE_T = 9 * 3600, 13 * 3600 + 1800


def tsec(s: str) -> float:
    h, m, r = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(r)


def audit(date_prefixes):
    per_date: dict[str, dict] = collections.defaultdict(
        lambda: {"files": 0, "ticks": 0, "amt": 0.0, "tt": collections.Counter(),
                 "snap": 0, "thin": 0, "mins": [], "first": [], "last": []})
    files = sorted(p for p in CACHE.glob("*.json")
                   if not p.name.startswith("_")
                   and any(d in p.stem for d in date_prefixes))
    print(f"掃描 {len(files):,} 檔", flush=True)
    for i, f in enumerate(files):
        if i % 500 == 0:
            print(f"  {i:,}/{len(files):,}", flush=True)
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        date = f.stem.split("_")[1]
        S = per_date[date]
        S["files"] += 1
        sess = [t for t in d if OPEN_T <= tsec(t["Time"]) < CLOSE_T]
        if len(sess) < 200:
            S["thin"] += 1
            continue
        sess.sort(key=lambda x: x["Time"])
        ts = [tsec(t["Time"]) for t in sess]
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        mg = sum(gaps) / len(gaps) if gaps else 0
        if mg > 0:
            sd = math.sqrt(sum((g - mg) ** 2 for g in gaps) / len(gaps))
            if sd / mg < 0.5:
                S["snap"] += 1
                continue
        S["ticks"] += len(sess)
        S["amt"] += sum(t["deal_price"] * t["volume"] for t in sess) * 1000
        for t in sess:
            S["tt"][str(t.get("TickType", "?"))] += 1
        S["mins"].append(len({int(x // 60) for x in ts}))
        S["first"].append(ts[0]); S["last"].append(ts[-1])
    return per_date


def main():
    prefixes = sys.argv[1:] or ["2026-07", "2026-08", "2026-09"]
    pd_ = audit(prefixes)
    print("\n日期       檔 薄 快照 |    盤中筆數 | 成交額(億) | TickType 0/1/2 占比  | 分鐘中位 | 首tick 末tick")
    for date in sorted(pd_):
        S = pd_[date]
        tt = S["tt"]; tot = sum(tt.values()) or 1
        p0 = tt.get("0", 0) / tot * 100
        p1 = tt.get("1", 0) / tot * 100
        p2 = tt.get("2", 0) / tot * 100
        mins = sorted(S["mins"]); med = mins[len(mins) // 2] if mins else 0
        fi = sorted(S["first"]); la = sorted(S["last"])
        f0 = fi[len(fi) // 2] if fi else 0
        l0 = la[len(la) // 2] if la else 0
        print(f"{date} {S['files']:>3} {S['thin']:>2} {S['snap']:>4} | {S['ticks']:>11,} |"
              f" {S['amt']/1e8:>10.0f} | {p0:>5.1f}/{p1:>5.1f}/{p2:>5.1f} |"
              f" {med:>8} | {int(f0)//3600:02d}:{int(f0)%3600//60:02d}"
              f" {int(l0)//3600:02d}:{int(l0)%3600//60:02d}")
    print("\n[判讀] TickType 1=買方主動、2=賣方主動（見 finmind-ticktype-sign-test）。")
    print("       若某段期間 0 的占比暴增或 1/2 比例劇變 → 主動方簽號失效 → bigpct 變雜訊。")


if __name__ == "__main__":
    main()
