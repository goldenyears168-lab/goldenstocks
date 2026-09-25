#!/usr/bin/env python3
"""本益比同族群排名(2026-09-25 jack 交辦,依楊育華分析師邏輯:同族群比,不同族群不比)。唯讀 DB + FinMind(唯讀抓取,不寫入生產 DB)。

⚠ 誠實揭露:她說的本益比分母是「預估EPS」(法說會/營收/毛利率推算的未來EPS);我們沒有分析師預估EPS的資料源,
只能用「已公布 TTM(近四季)EPS」——落後指標,不是預估指標。她自己說EPS「兩三個月才變」,落後版本失真程度有限,
但這是與原方法唯一的實質差異,必須誠實標註,不能含糊成「本益比」三個字了事。

族群定義:沿用 biglot_dashboard.py 既有的 SUBCAT 細分類(本來就是照「不能拿IC設計跟記憶體比」這個邏輯手動建的),
每組人工擴充到真實上市櫃同業(PEER_MAP,依 TaiwanStockInfo 驗證代號存在)。42 檔本身多數 SUBCAT 只有 1~2 檔,
擴充後多數組 3~8 檔、少數到 6~10 檔——老實講,大多數台股細分族群天生就沒有她說的 20~30 檔那麼多真實同業,
不足處已在輸出中如實呈現,不硬湊數字。

用法:PYTHONPATH=src .venv/bin/python scripts/research/pe_peer_group_research.py
輸出:~/goldenstocks-data/scratch/pe_peer_group_2026-09-25.json(給 dashboard 讀)+ 終端報表
"""
from __future__ import annotations
import json, re, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, "src")
import stock_db  # noqa: E402
from finmind_client import fetch_finmind  # noqa: E402
from datetime import date

OUT = Path.home() / "goldenstocks-data/scratch/pe_peer_group_2026-09-25.json"
CACHE = Path.home() / "goldenstocks-data/scratch/pe_eps_cache_2026-09-25.json"

PEERS = {
 "被動-MLCC":  ["2327", "2492", "6197", "2375", "2456", "2452", "2478"],
 "被動-陶瓷":  ["6173", "2492", "6197", "2375"],
 "石英元件":  ["3042", "6173"],
 "矽晶圓":    ["6182", "6488", "3532", "5483"],
 "晶圓代工":  ["2303", "6770", "5347"],
 "封測":      ["2449", "6239", "8150", "6147", "3374", "2441"],
 "封測-驅動IC": ["6147", "3374"],
 "封測-晶圓級": ["3374", "6147"],
 "DRAM記憶體":  ["2408", "2344", "2337", "3006", "4967", "8299"],
 "記憶體IC設計": ["3006", "2337", "2344", "8299", "4967"],
 "記憶體-利基":  ["2344", "2337", "2408", "4967"],
 "記憶體-NOR":  ["2337", "2344"],
 "ABF載板":   ["3189", "8046", "3037", "2313"],
 "PCB-銅箔":  ["8358", "2371"],
 "PCB-軟板":  ["4958", "6213", "2313"],
 "PCB-軟板材料": ["8039", "2383"],
 "PCB-伺服器板": ["2368", "2313", "4958"],
 "CCL銅箔基板": ["6213", "6274", "2383", "1305", "1308"],
 "塑化-CCL":  ["1303", "1305", "1308"],
 "砷化鎵-PA":  ["3105", "2455"],
 "砷化鎵-磊晶": ["2455", "3105"],
 "光學鏡頭":  ["3008", "3406", "2417"],
 "散熱":      ["3324", "3017", "3653"],
 "散熱-均熱片": ["3653", "3324", "3017"],
 "光電-電源":  ["2301", "2308"],
 "光通訊":    ["3081", "4979", "4977"],
 "半導體設備-探針卡": ["6223", "3260", "3131"],
 "二極體-功率": ["2481", "2436", "2437"],
 "品牌NB":    ["2357", "2382", "2353"],
 "貨櫃航運":  ["2615", "2609", "2603"],
}


def load_subcat():
    src = (Path(__file__).parent / "biglot_dashboard.py").read_text(encoding="utf-8")
    m = re.search(r"SUBCAT = \{(.*?)\n\}\n", src, re.S)
    ns = {}
    exec("SUBCAT = {" + m.group(1) + "}", ns)  # noqa: S102
    return ns["SUBCAT"]


def fetch_eps_ttm(sid: str) -> dict | None:
    """抓近 3 年 FinMind TaiwanStockFinancialStatements 的 EPS 列,算近四季合計(TTM)。"""
    try:
        rows = fetch_finmind("TaiwanStockFinancialStatements", sid, date(2023, 1, 1), date.today())
    except Exception as e:  # noqa: BLE001
        print(f"  [{sid}] fetch failed: {e}", file=sys.stderr)
        return None
    eps_by_q: dict[str, float] = {}
    for r in rows:
        if r.get("type") == "EPS":
            d = str(r.get("date", ""))[:10]
            v = r.get("value")
            if d and v is not None:
                eps_by_q[d] = float(v)
    if len(eps_by_q) < 4:
        return {"quarters": eps_by_q, "eps_ttm": None, "asof": max(eps_by_q) if eps_by_q else None}
    qs = sorted(eps_by_q)[-4:]
    return {"quarters": eps_by_q, "eps_ttm": round(sum(eps_by_q[q] for q in qs), 4), "asof": qs[-1]}


def main():
    subcat = load_subcat()
    con = sqlite3.connect(f"file:{stock_db.DEFAULT_DB_PATH}?mode=ro", uri=True)
    dashboard_sids = set(subcat)
    allids = sorted(set(s for v in PEERS.values() for s in v) | dashboard_sids)

    cache = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    print(f"候選(42 檔 + 擴充同業)共 {len(allids)} 檔,開始抓 EPS(已快取 {len(cache)} 檔)...")
    for i, sid in enumerate(allids):
        if sid in cache and cache[sid].get("eps_ttm") is not None:
            continue
        r = fetch_eps_ttm(sid)
        cache[sid] = r or {"quarters": {}, "eps_ttm": None, "asof": None}
        if i % 10 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(allids)}", flush=True)
        time.sleep(0.35)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    px = {}
    for sid in allids:
        r = con.execute("SELECT close FROM stock_daily_bars WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1", (sid,)).fetchone()
        px[sid] = r[0] if r else None
    names = {r["stock_id"]: r["stock_name"] for r in json.load(open("/tmp/tw_info.json"))} if Path("/tmp/tw_info.json").exists() else {}

    pe = {}
    for sid in allids:
        e = cache.get(sid, {}).get("eps_ttm")
        p = px.get(sid)
        pe[sid] = (round(p / e, 2) if (e and e > 0 and p) else None, cache.get(sid, {}).get("asof"))

    result = {}
    print(f"\n{'族群':16s}{'家數':>4s}  排名(本益比由低到高;None=虧損或無資料)")
    for grp, sids in PEERS.items():
        rows = []
        for sid in sids:
            v, asof = pe.get(sid, (None, None))
            rows.append({"sid": sid, "name": names.get(sid, sid), "pe": v, "eps_asof": asof, "eps_ttm": cache.get(sid, {}).get("eps_ttm")})
        valid = sorted([r for r in rows if r["pe"] is not None and r["pe"] > 0], key=lambda r: r["pe"])
        for i, r in enumerate(valid):
            r["rank"] = i + 1; r["n_valid"] = len(valid)
            r["pctile"] = round((i) / max(1, len(valid) - 1) * 100, 0) if len(valid) > 1 else 50.0
        invalid = [r for r in rows if r not in valid]
        for r in invalid:
            r["rank"] = None; r["n_valid"] = len(valid); r["pctile"] = None
        result[grp] = valid + invalid
        line = f"{grp:16s}{len(sids):4d}  " + " | ".join(f"{r['name']}{r['pe']:.1f}" if r["pe"] else f"{r['name']}虧損/無" for r in rows)
        print(line[:170])

    OUT.write_text(json.dumps({"generated": str(date.today()), "peers": PEERS, "table": result}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n寫入 {OUT}")

    print("\n==== 42 檔在各自族群中的位置(本益比百分位,0=最便宜) ====")
    for sid in sorted(dashboard_sids):
        grp = subcat[sid]
        rows = result.get(grp, [])
        me = next((r for r in rows if r["sid"] == sid), None)
        if not me:
            continue
        if me["pe"] is None:
            print(f"  {sid} {names.get(sid, sid):6s} [{grp}] 本益比無法計算(虧損或缺EPS/股價)")
        else:
            print(f"  {sid} {names.get(sid, sid):6s} [{grp}] PE={me['pe']:.1f} 族群第{me['rank']}/{me['n_valid']}低 (百分位{me['pctile']:.0f}%,越低越便宜)")


if __name__ == "__main__":
    main()
