"""biglot dashboard 重構：零呼叫其他頂層函式的次順位候選批次（見
docs/biglot-refactor-roadmap.md「後續要做的（下次對話）」段落）。這批是餵給
盤中分數/渲染管線的小 helper 抓一把（tag engine、5/30 分滾動窗、隱形大戶
成交確認、V2.2 並列分數、分數落地、處置窗查表、分數欄位 HTML、💎 訊號回放、
五檔快照查詢），依賴分析確認彼此互不呼叫，可獨立搬移。

跟 biglot/xq_style.py 同一條規則：模組頂層只 `import biglot_dashboard`（不
`from biglot_dashboard import X`），函式本體內用 `biglot_dashboard.X` 屬性
存取 biglot_dashboard.py 自己定義的全域（不管是不是 18 個「整包重新賦值」
危險全域之列，一律比照辦理，理由見 xq_style.py 開頭注解與路線圖）——這樣
不會有 stale reference，也不會有循環 import 在載入期就炸掉。

其他模組的名字（`stock_db.DATA_DIR`、`biglot.utils._par30`/`_b5n`）直接
import，不透過 biglot_dashboard 轉手。函式本體與 docstring 逐字複製，不改
一行邏輯。
"""
from __future__ import annotations

import html as html_mod
import json

from stock_db import DATA_DIR

import biglot_dashboard
from biglot.utils import _b5n, _par30


def _wrt_cum(sid, order):
    """依分鐘鍵 order 回傳權證簽號淨額的累計序列(元);無資料回 None。"""
    d = biglot_dashboard.WRT_MIN["data"].get(sid)
    if not d:
        return None
    keys = sorted(d)
    out, c, j = [], 0.0, 0
    for k in order:
        while j < len(keys) and keys[j] <= k:
            c += d[keys[j]]; j += 1
        out.append(c)
    return out   # 台指近月 10 秒樣本(txf_10s_*.jsonl 增量),頂部校準圖用


def _agg_lines(t0, t1, W, H, L, R):
    """台指面板疊圖:36 檔累計大戶(紅)/散戶(藍)/權證簽號(紫)加總,各自以 ±最大值正規化到同一畫面(0 線置中)。"""
    if not biglot_dashboard.AGG.get("mins"):
        return ""
    mins = biglot_dashboard.AGG["mins"]; mid = H / 2
    def X(hm):
        h, m = hm.split(":"); ts = t0 + ((int(h) - 8) * 60 + int(m) - 45) * 60
        return L + max(0.0, min(1.0, (ts - t0) / (t1 - t0))) * (W - L - R)
    out = [f"<line x1='{L}' y1='{mid:.0f}' x2='{W-R}' y2='{mid:.0f}' stroke='#30363d' stroke-dasharray='2,3'/>"]
    lab = []
    for key, col, nm in (("big", "#ff7b72", "大戶"), ("ret", "#58a6ff", "散戶"), ("wrt", "#d2a8ff", "權證")):
        v = biglot_dashboard.AGG.get(key) or []
        if not v:
            continue
        mx = max(abs(x) for x in v) or 1.0
        pts = " ".join(f"{X(k):.0f},{mid - (x / mx) * (H / 2 - 8):.0f}" for k, x in zip(mins, v))
        out.append(f"<polyline points='{pts}' fill='none' stroke='{col}' stroke-width='1.2' opacity='0.85'/>")
        lab.append(f"<tspan fill='{col}'>{nm} {v[-1]/1e4:+,.0f}萬(尺±{mx/1e4:,.0f})</tspan>")
    out.append(f"<text x='{W-R}' y='{H-2}' font-size='9' text-anchor='end'>36檔累計 " + " ".join(lab) + "</text>")
    return "".join(out)


def _iceberg_trade_confirms(sid, ts_lo, ts_hi, price):
    """交叉比對 ST.recent[sid](逐筆真實成交,_ingest_trade 已在填)是否有成交打在 price 附近、
    時間落在 [ts_lo, ts_hi]。取代舊版用『當天累計量有沒有動』當代理(落差三,見腳本開頭說明)。"""
    lo, hi = price * (1 - biglot_dashboard.ICEBERG_TRADE_TOL), price * (1 + biglot_dashboard.ICEBERG_TRADE_TOL)
    for ts, px, _amt, _sgn, _big, _ret in biglot_dashboard.ST.recent.get(sid, ()):
        if ts < ts_lo:
            continue
        if ts > ts_hi:
            break
        if lo <= px <= hi:
            return True
    return False


def _rolling(sid, nts):
    """每秒滾動窗:5分=(now−300s, now]、30分=(now−1800s, now]、參與Δ=本30分 − 前30分。
    只供欄位顯示;標籤照舊用完成的 5 分桶。無逐筆時回 None。"""
    out = {k: None for k in ("big5_r", "big30_r", "retn5_r", "rbuy5_r", "rsell5_r",
                             "rbuy30_r", "rsell30_r", "dsh30_r", "w_ret_r", "r30_r",
                             "tot5_r", "tot30_r", "big5p_r", "bigp30_r", "share5_r", "dshare5_r", "sell30s_r", "r30s_r")}
    q = biglot_dashboard.ST.recent.get(sid)
    if not q:
        return out
    t5, t30, t60 = nts - 300, nts - 1800, nts - 3600
    t10, t35 = nts - 600, nts - 2100          # 前一個 5 分窗 / 「本 5 分之前的 30 分」(純機構的逆大戶條件)
    big5 = big30 = tot5 = tot30 = retn5 = ret2_5 = retn30 = ret2_30 = 0.0
    tot_p = ret2_p = 0.0
    big5p = tot5p = ret2_5p = bigp30 = 0.0
    t30s = nts - 30
    sbuy = ssell = 0.0                        # 近 30 秒主動買/賣金額(竭盡狀態格用)
    px5 = px30 = px30s = None                 # px30s = 30 秒前成交價(近30秒報酬,V2.5 真空/已止跌判定)
    for ts, px, amt, sgn, isbig, isret in q:
        if ts <= t60:
            continue
        if t35 < ts <= t5 and isbig:
            bigp30 += sgn * amt
        if t10 < ts <= t5:
            tot5p += amt
            if isbig:
                big5p += sgn * amt
            if isret:
                ret2_5p += amt
        if ts <= t30:
            px30 = px
            tot_p += amt
            if isret:
                ret2_p += amt
            continue
        tot30 += amt
        if isbig:
            big30 += sgn * amt
        if isret:
            ret2_30 += amt
            retn30 += sgn * amt
        if ts > t30s:
            if sgn > 0:
                sbuy += amt
            elif sgn < 0:
                ssell += amt
        else:
            px30s = px
        if ts <= t5:
            px5 = px
        else:
            tot5 += amt
            if isbig:
                big5 += sgn * amt
            if isret:
                ret2_5 += amt
                retn5 += sgn * amt
    pxnow = q[-1][1]
    if px5 is None:
        px5 = px30
    out["big5_r"], out["big30_r"], out["retn5_r"] = big5, big30, retn5
    out["tot5_r"], out["tot30_r"], out["big5p_r"], out["bigp30_r"] = tot5, tot30, big5p, bigp30
    if sbuy + ssell > 0:
        out["sell30s_r"] = ssell / (sbuy + ssell)
    if px30s and pxnow:
        out["r30s_r"] = (pxnow / px30s - 1) * 10000
    if tot5 > 0:
        out["rbuy5_r"] = (ret2_5 + retn5) / 2 / tot5 * 100
        out["rsell5_r"] = (ret2_5 - retn5) / 2 / tot5 * 100
        out["share5_r"] = ret2_5 / tot5 * 100                      # 5 分散戶參與(買+賣)
        if tot5p > 0:
            out["dshare5_r"] = out["share5_r"] - ret2_5p / tot5p * 100   # 參與 Δ(本 5 分 − 前 5 分,pp)
    if tot30 > 0:
        out["rbuy30_r"] = (ret2_30 + retn30) / 2 / tot30 * 100
        out["rsell30_r"] = (ret2_30 - retn30) / 2 / tot30 * 100
        if tot_p > 0:
            out["dsh30_r"] = ret2_30 / tot30 * 100 - ret2_p / tot_p * 100
    if px5:
        out["w_ret_r"] = (pxnow / px5 - 1) * 10000
    if px30:
        out["r30_r"] = (pxnow / px30 - 1) * 10000
    return out


def _tag_engine(rows, nts, now):
    """回傳 sid -> {"bull":[txt...], "bear":[...], "ex":[...]};同時維護 TAG_STATE 並落地觸發事件。"""
    day = now.strftime("%Y-%m-%d")
    statep = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"tag_state_{day}.json"
    if biglot_dashboard.TAG_LOG_DAY["d"] != day:
        biglot_dashboard.TAG_STATE.clear()
        biglot_dashboard.TAG_LOG_DAY["d"] = day
        try:                                   # 同日重啟:接續既有觸發時刻(不然全部歸零變 0′)
            for k, v in json.loads(statep.read_text()).items():
                sid, tag = k.split("|", 1)
                biglot_dashboard.TAG_STATE[(sid, tag)] = {"pend": None, "t0": v["t0"], "hz": v["hz"], "dead": v["dead"], "tail": v["tail"], "txt": v["txt"]}
        except Exception:  # noqa: BLE001
            pass
    logp = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"tag_events_{day}.jsonl"
    fired = False
    hm = now.strftime("%H:%M")
    out = {}
    for r in rows:
        sid = r["sid"]
        res = {"bull": [], "bear": [], "ex": []}
        for tag, kind, hz, cond, dead, name in biglot_dashboard.TAG_DEFS:
            st = biglot_dashboard.TAG_STATE.setdefault((sid, tag), {"pend": None, "t0": None, "hz": hz, "dead": False, "tail": False, "txt": ""})
            try:
                ok = bool(cond(r))
            except Exception:  # noqa: BLE001
                ok = False
            active = st["t0"] is not None and nts - st["t0"] < hz
            if ok:
                if st["pend"] is None:
                    st["pend"] = nts
                if not active and nts - st["pend"] >= biglot_dashboard.TAG_HOLD_SEC:
                    st.update({"t0": nts, "dead": False, "tail": hm >= "13:00" and hz >= 1800, "txt": name(r)})
                    active = True
                    fired = True
                    try:
                        with logp.open("a", encoding="utf-8") as f:
                            f.write(json.dumps({"t": now.strftime("%H:%M:%S"), "sid": sid, "name": r["name"], "tag": tag, "txt": st["txt"],
                                                "px": r.get("px"), "big30_r": r.get("big30_r"), "big5_r": r.get("big5_r"),
                                                "par30": _par30(r), "r30_r": r.get("r30_r"), "w_ret_r": r.get("w_ret_r"),
                                                "rvol5_r": r.get("rvol5_r")}, ensure_ascii=False) + "\n")
                    except Exception:  # noqa: BLE001
                        pass
            else:
                st["pend"] = None
            if active:
                try:
                    if dead(r):
                        st["dead"] = True
                except Exception:  # noqa: BLE001
                    pass
                age = int((nts - st["t0"]) // 60)
                txt = f"{st['txt']}{age}′" + ("✗" if st["dead"] else "") + ("尾" if st["tail"] else "")
                if age < 5 and not st["dead"]:
                    txt = f"<b>{txt}</b>"                      # ≤5 分 = 最佳狀態(粗體)
                res[kind].append(txt)
        out[sid] = res
    if fired:
        try:
            statep.write_text(json.dumps({f"{s}|{t}": {"t0": v["t0"], "hz": v["hz"], "dead": v["dead"], "tail": v["tail"], "txt": v["txt"]}
                                          for (s, t), v in biglot_dashboard.TAG_STATE.items() if v["t0"] is not None}, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
    return out


def _active_tags(sid, nts):
    """標籤 → 時間價值權重(依 TAG_DECAY 各格不同曲線);未 ✗ 且尚未歸零者才算。"""
    out = {}
    for (s_, tag), st in biglot_dashboard.TAG_STATE.items():
        if s_ != sid or st["t0"] is None or st["dead"]:
            continue
        flat, zero = biglot_dashboard.TAG_DECAY.get(tag, (0, st["hz"]))
        age = nts - st["t0"]
        if age >= zero:
            continue
        out[tag] = 1.0 if age < flat else max(0.0, 1.0 - (age - flat) / max(1, zero - flat))
    return out


def _score_v22_legacy(r, mkt30, hm):
    """V2.2 加總(bps;僅供並列落地/tooltip 對照,不顯示主值)。與 f63aa01 版同邏輯。"""
    if hm < "09:30":
        return 0.0
    fac = 0.5 if hm < "10:00" else 1.0; sc = 0.0
    w5 = r.get("w_ret_r"); r30 = r.get("r30_r"); rb5 = r.get("rbuy5_r"); unm = r["unm"]; b5n = _b5n(r)
    ret = []
    if w5 is not None and w5 > 20 and rb5 is not None and rb5 >= 5 and not unm: ret.append(-10 if w5 > 50 else -8)
    if w5 is not None and w5 > 20 and (((r.get("dshare5_r") or 0) > 5 and not unm) or (b5n is not None and b5n < -5)): ret.append(-8)
    if ret: sc += min(ret)
    if w5 is not None and w5 < -20 and rb5 is not None and rb5 >= 5 and not unm: sc += 4
    if r30 is not None:
        if r30 >= 600: pass
        elif r30 >= 400: sc += -21 * (600 - r30) / 200
        elif r30 >= 300: sc += -18
        elif r30 >= 200: sc += -13
        elif r30 >= 150: sc += -10
        if r30 <= -600: sc += 23
        elif r30 <= -400: sc += 22
        elif r30 <= -300: sc += 14
        elif r30 <= -200: sc += 6
        if mkt30 >= 5 and r30 >= 20 and "10:00" <= hm < "12:00": sc += -3
        if mkt30 <= -5 and r30 <= -20: sc += 3
        if mkt30 <= -5 and r30 >= 20: sc += -7
        if mkt30 >= 5:
            if r30 <= -100: sc += 11
            elif r30 <= -50: sc += 8
            elif r30 <= -20: sc += 6
    dr = r.get("day_ret")
    if dr is not None:
        if dr > 5: sc += -15
        elif dr > 3: sc += -9
        elif dr < -5: sc += 12
        elif dr < -3: sc += 8
    if (hm >= "10:00" and b5n is not None and b5n > 10 and (r.get("tot5_r") or 0) > 0 and r.get("share5_r") is not None and r["share5_r"] < 5 and not unm
            and (r.get("bigp30_r") if r.get("bigp30_r") is not None else 0) < 0 and (r.get("big5p_r") if r.get("big5p_r") is not None else 0) < 0):
        sc += 12
    sc = round(sc * fac, 1)
    return max(-40.0, min(40.0, sc))


def _log_scores(rows, day):
    """每個 5 分桶第一次 render 時把各檔 V2.3/V2.2/隔夜分 落到 score_v2_{日}.jsonl(供累 20 日算 IC / 對照面板)。"""
    now = biglot_dashboard.datetime.now(biglot_dashboard.TZ); bk = f"{now.hour:02d}:{now.minute - now.minute % 5:02d}"
    if not ("09:30" <= bk <= "13:25"):
        return
    logp = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"score_v2_{day}.jsonl"
    out = []
    for r in rows:
        key = (day, bk, r["sid"])
        if key in biglot_dashboard.SC_LOGGED or r.get("sc_v2") is None:
            continue
        biglot_dashboard.SC_LOGGED.add(key)
        out.append(json.dumps({"date": day, "bucket": bk, "ts": now.strftime("%H:%M:%S"), "sid": r["sid"], "px": r.get("px"),
                               "sc_v23": r["sc_v2"], "sc_v22": r.get("sc_v22"), "sc_ov": r.get("sc_ov"), "sc_v1": r.get("sc_in"),
                               "items": r.get("sc_v2_items") or [], "cause": [t for t, _ in (r.get("cause") or [])],
                               "mini": ({k: (v if k != "n30" and k != "n5" else list(v)) for k, v in r["mini"].items()} if r.get("mini") else None),
                               "hold": r.get("hold")}, ensure_ascii=False))
    if out:
        try:
            logp.parent.mkdir(parents=True, exist_ok=True)
            with logp.open("a", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
        except Exception:  # noqa: BLE001
            pass


def _disposal_today(today: str) -> dict:
    """處置窗內的 sid → (measure, end)。來源 ${DATA_DIR}/disposal/disposal_windows.csv(fetch_disposal_list.py,
    limitup-fade-nightly 每晚更新;TWSE 可回溯、TPEx 只有當日快照靠每日累積)。"""
    f = DATA_DIR / "disposal" / "disposal_windows.csv"
    try:
        mt = f.stat().st_mtime
    except OSError:
        return {}
    if biglot_dashboard._DISP_CACHE["date"] == today and biglot_dashboard._DISP_CACHE["mtime"] == mt:
        return biglot_dashboard._DISP_CACHE["sids"]
    out = {}
    try:
        import csv
        with f.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("start", "") <= today <= row.get("end", ""):
                    out[str(row.get("stock_id", ""))] = (row.get("measure", "")[:6], row.get("end", ""))
    except Exception:  # noqa: BLE001
        pass
    biglot_dashboard._DISP_CACHE.update(date=today, sids=out, mtime=mt)
    return out


def _score_td(r):
    ov, sc = r.get("sc_ov"), r.get("sc_in")
    if ov is None or sc is None:
        return "<td class='dim'>—</td>"

    def _c(v):
        return "up" if v > 0 else ("dn" if v < 0 else "dim")
    v2 = r.get("sc_v2"); v2i = r.get("sc_v2_items") or []
    tip = ("隔夜分:" + (" · ".join(f"{k} {v:+d}" for k, v in r["sc_ov_items"]) or "無") +
           " ‖ 盤中分V2.5(bps,60分,IS聯合OLS×0.7,上限±40):" + (" · ".join(f"{k} {v:+.1f}" for k, v in v2i) or "無") +
           " ‖ 盤中分V1(0/±1/±2,標籤×衰減,並列20日):" + (" · ".join(f"{k} {v:+.1f}" for k, v in r["sc_in_items"]) or "無") +
           f" = {sc:+.1f}" +
           (f" ‖ V2.2 加總並列 {r['sc_v22']:+.1f}" if r.get("sc_v22") is not None else "") +
           (f" ‖ 高波動日 ×(今日振幅 {r['amp_ratio']:.1f}x 20日均:同分對應更大 bps,分數不變)" if (r.get("amp_ratio") or 0) >= 1.5 else "") +
           " ‖ V2.3 IS/OOS 見 scratch/v23_fit_2026-09-24.txt;每桶落地 score_v2_{日}.jsonl 供累 20 日算 IC")
    z = biglot_dashboard.TX_LAST.get("z")
    bg = " background:#21262d;" if (z is not None and abs(z) >= 1) else ""
    big_ov = " style='font-size:13px'" if abs(ov) >= 3 else ""
    v2s = v2 if v2 is not None else 0.0
    big_sc = " style='font-size:13px'" if abs(v2s) >= 15 else ""
    pk = r.get("sc_v2_peak")
    pk_html = (f" <span class='dim' style='font-size:9px'>峰<span class='{_c(pk[0])}'>{pk[0]:+.0f}</span>@{pk[1]}</span>"
               if (pk and abs(pk[0]) >= 15 and abs(pk[0]) > abs(v2s)) else "")
    cause = r.get("cause") or []
    if cause:
        tip += " ‖ 成因:" + " · ".join(f"[{t}] {d}" for t, d in cause)
    pp = r.get("paper") or {}; paper_html = ""
    for bk_, q in pp.items():
        if q:
            paper_html += (f" <span style='font-size:9px;color:#d2a8ff'>紙{bk_[:1]} {q['pnl']:+.0f} · {q['min']:.0f}分{'·嚴' if q['strict'] else '·樂'}{' 賣中' if q['sell'] else ''}</span>")
    h = r.get("hold"); hold_html = paper_html
    if h:
        pn = f"{h['pnl']:+.0f}" if h["pnl"] is not None else "—"
        fl = " ".join(f"<b style='color:#f85149'>{html_mod.escape(f)}</b>" for f in h["flags"])
        hint = f" <span style='color:#3fb950'>{h['hint']}</span>" if h.get("hint") else ""
        hold_html += (f" <span style='font-size:10px;color:#79c0ff'>持 {h['hm'][:5]} 損益 {pn} · {h['min']:.0f}分 · 分 {h['score'] if h['score'] is not None else '—'}"
                     f"{(' 低'+str(int(h['low_s']))+'s') if h['low_s'] else ''}</span> {fl}{hint}")
        tip += f" ‖ 持倉:進 {h['hm']} @ {h['px0']} · 出場規則=分數≤0 連續 30 秒 / 壞標籤(虛拉·過熱·竭盡∧散戶接) / 60 分到期;獲利≥50 可停利;不設移動停利/硬停損/破昨低(面板對照較差)"
    _col = {"處置": "#f0883e", "跌停鎖": "#f85149", "觸跌停": "#f85149", "族群": "#d29922", "MOPS?": "#8b949e", "MOPS": "#a371f7", "昨MOPS": "#7d5bbe", "跟盤殺": "#f85149", "自己殺": "#3fb950", "大盤仍跌": "#d29922"}
    def _cc(t):
        return next((v for k, v in _col.items() if t.startswith(k)), "#8b949e")
    cause_html = (" <span style='font-size:9px'>" + " ".join(f"<span style='color:{_cc(t)}'>{html_mod.escape(t)}</span>" for t, _ in cause) + "</span>") if cause else ""
    return (f"<td style='text-align:left;white-space:nowrap;{bg}' title='{html_mod.escape(tip, quote=True)}'>"
            f"<span class='dim'>隔</span><b class='{_c(ov)}'{big_ov}>{ov:+d}</b> "
            f"<span class='dim'>盤</span><b class='{_c(v2s)}'{big_sc}>{v2s:+.0f}</b><span class='dim' style='font-size:9px'>bps</span>{pk_html}{cause_html}{hold_html}</td>")


def _day_sig_counts():
    """以儀表板旗標同款條件回放今日:每檔 💎 / 💎💎 觸發數。"""
    out = {}
    for sid, m in biglot_dashboard.ST.buckets.items():
        if sid in biglot_dashboard.RET_UNM:
            continue
        bks = sorted(m)
        c1 = c2 = 0
        for i in range(7, len(bks)):
            cur, prev, prior6 = bks[i], bks[i - 1], bks[i - 6:i]
            a, p = m[cur], m[prev]
            if not a["tot"] or a["big"] <= 0.10 * a["tot"]:
                continue
            if a["ret2"] / a["tot"] * 100 >= 5:
                continue
            if sum(m[b]["big"] for b in prior6) >= 0 or p["big"] >= 0:
                continue
            c1 += 1
            if a["big"] >= 3e7:
                c2 += 1
        out[sid] = (c1, c2)
    return out


def _book_of(sid, day):
    """該日 sid 最後一筆五檔快照(今天用記憶體 ST.book,過去日讀檔尾)。"""
    today = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%Y-%m-%d")
    if day == today and sid in biglot_dashboard.ST.book:
        return biglot_dashboard.ST.book[sid]
    bf = DATA_DIR / "cache" / "watchlist_books" / f"watchlist_books_{day}.jsonl"
    if not bf.exists():
        return None
    last = None
    with open(bf) as f:
        for line in f:
            if sid not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if str(r.get("sym")) == sid:
                last = r
    return last
