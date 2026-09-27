"""biglot dashboard 重構：成因標籤 `_cause_tags`（見 docs/biglot-refactor-roadmap.md「後續要做的
（下次對話）」段落）。

跟 biglot/xq_style.py 同一條規則：模組頂層只 `import biglot_dashboard`（不
`from biglot_dashboard import X`），函式本體內用 `biglot_dashboard.X` 屬性
存取 biglot_dashboard.py 自己定義的全域（`CATS`/`ST`/`SUBCAT`）——這樣不會
有 stale reference，也不會有循環 import 在載入期就炸掉。

其他模組的名字（`biglot.scoring_support._disposal_today`、
`biglot.utils._limit_down`、`biglot.day_views._mops_load`/`_prev_mops_day`）
直接 import，不透過 biglot_dashboard 轉手。函式本體與 docstring 逐字複製，
不改一行邏輯。
"""
from __future__ import annotations

import biglot_dashboard
from biglot.day_views import _mops_load, _prev_mops_day
from biglot.scoring_support import _disposal_today
from biglot.utils import _limit_down


def _cause_tags(rows, today: str):
    """每檔寫入 r["cause"] = [(標籤, tooltip 說明含來源/時間)];只在 |盤中分|≥10 或急跌/過熱時才算族群,其餘也照標處置/跌停。"""
    disp = _disposal_today(today); mops = _mops_load(today); pday, pmops = _prev_mops_day(today)
    grp = {}
    for r in rows:
        grp.setdefault(biglot_dashboard.SUBCAT.get(r["sid"]) or biglot_dashboard.CATS.get(r["sid"]), []).append(r)
    # 大盤狀態(進場過濾器,不計分;jack 2026-09-24):36 檔等權 近5分 / 近30秒
    _w5s = [q["w_ret_r"] for q in rows if q.get("w_ret_r") is not None]; _r30ss = [q["r30s_r"] for q in rows if q.get("r30s_r") is not None]
    mkt5 = sum(_w5s) / len(_w5s) if len(_w5s) >= 10 else None; mkt30s = sum(_r30ss) / len(_r30ss) if len(_r30ss) >= 10 else None
    for r in rows:
        tags = []; sid = r["sid"]
        w5_, r30s_ = r.get("w_ret_r"), r.get("r30s_r")
        if w5_ is not None and w5_ <= -20 and mkt5 is not None:
            if mkt5 <= -20 and (w5_ - mkt5) > -10:
                tags.append(("跟盤殺", f"個股5分 {w5_:+.0f} vs 大盤5分 {mkt5:+.0f}bps,相對<10bps=只是跟著大盤;127日:聯合 −2.9/−3.7(t−2.3)、原始後60分 −20/−24 → 不做多(大盤不反轉)"))
            elif mkt30s is not None and r30s_ is not None and r30s_ <= -10 and mkt30s >= 0:
                tags.append(("自己殺", f"個股末30秒 {r30s_:+.0f} 而大盤末30秒 {mkt30s:+.0f}bps=大盤止跌它還在殺;127日:聯合 +3.4/+5.6(t2.1)、原始 +6/+7 → 要的格"))
            elif mkt30s is not None and mkt30s < -5:
                tags.append(("大盤仍跌", f"大盤末30秒 {mkt30s:+.0f}bps 仍在跌;127日:個股續跌∧大盤續跌 聯合 −3.4/−5.6、原始 −5/−18 → 等大盤止住"))
        if sid in disp:
            m, end = disp[sid]; tags.append((f"處置{m}", f"處置窗至 {end}(disposal_windows.csv,分盤撮合、當沖停)"))
        bk = biglot_dashboard.ST.book.get(sid) or {}
        try:
            y = float(bk.get("y")) if bk.get("y") not in (None, "-", "") else None
            z = float(bk.get("z")) if bk.get("z") not in (None, "-", "") else None
            lo = float(bk.get("l")) if bk.get("l") not in (None, "-", "") else None
            if y:
                ld = _limit_down(y); bq0 = (bk.get("bq") or [0])[0] or 0
                if z is not None and z <= ld + 1e-9 and not bq0:
                    tags.append(("跌停鎖", f"現價 {z} ≤ 跌停 {ld} 且買一為空(MIS 五檔 {bk.get('t')});鎖死中不可買→非流動性反轉樣本"))
                elif lo is not None and lo <= ld + 1e-9:
                    tags.append(("觸跌停", f"今低 {lo} ≤ 跌停 {ld},現 {z}(MIS {bk.get('t')});打開後反彈屬跌停機制,勿與一般急跌混看"))
        except Exception:  # noqa: BLE001
            pass
        r30 = r.get("r30_r")
        if r30 is not None and abs(r30) >= 200:
            peers = [q for q in grp.get(biglot_dashboard.SUBCAT.get(sid) or biglot_dashboard.CATS.get(sid), []) if q["sid"] != sid and q.get("r30_r") is not None]
            same = [q for q in peers if (q["r30_r"] <= -200 if r30 < 0 else q["r30_r"] >= 200)]
            if peers:
                k = len(same)
                tags.append((f"族群{k}/{len(peers)}", f"同細分產業 {biglot_dashboard.SUBCAT.get(sid) or biglot_dashboard.CATS.get(sid)} 其餘 {len(peers)} 檔中 {k} 檔近30分同向≥2%:" +
                             ("、".join(q['name'] for q in same) if same else "無") + ";≥半數=族群連鎖(超額已扣籃子但仍屬共同衝擊,反轉率不同)"))
        if mops and sid in mops:
            for hm, subj in mops[sid][:2]:
                tags.append((f"MOPS{hm}", f"今日重大訊息 {hm}:{subj[:60]}(mops_today 快取);有訊息的急跌傾向延續非反轉"))
        elif mops is None:
            tags.append(("MOPS?", "今日盤中 MOPS 無即時來源(OpenAPI 只有 T-1 快照)——不是「無訊息」,下單前自行查 mops.twse.com.tw"))
        if pmops and sid in pmops:
            for hm, subj in pmops[sid][:2]:
                tags.append((f"昨MOPS{hm}", f"{pday} 發言 {hm}:{subj[:60]}(TWSE/TPEx OpenAPI 快照,fetch_mops_today.py);盤後訊息會反映在今日跳空與盤中"))
        r["cause"] = tags
