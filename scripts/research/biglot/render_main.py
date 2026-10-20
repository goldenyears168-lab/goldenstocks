"""biglot dashboard 重構：`render()`——主表渲染函式，全檔案最大的單一函式
（約 860 行），從 scripts/research/biglot_dashboard.py 逐字搬移，邏輯不改一行。

這是這整個重構最後、也是體積最大的一次搬移。跟 ingest() 不同，render() 只『讀』
全域，不寫（確認過 writes=[]），風險模式跟第四／五批的一般讀取型搬移一樣，只是
規模大很多——依賴到 20+ 個 biglot_dashboard 原生全域跟 17 個已搬移函式。

搬移方法：先用 scripts/research/biglot_phase0/dep_graph.py 抓依賴清單，發現並修好
該工具的一個真 bug（module_level_names() 沒處理 tuple 解構賦值 `A, B, C = f()`，
導致 HIST_BIG/PREV_CLOSE/Y_PMLOW/UNI5/PE_TABLE/PE_PEERS/PE_GEN/PE_EPS/VOLRISK/
VOLRISK_DATE 十個全域從未被任何一次 dep_graph 執行正確算進「reads」——所幸這些
名字剛好都在另一份手動核對過的「危險全域」清單裡，之前搬移時已個別正確處理，
沒有造成實際 bug，但工具本身這次確實修好了）；然後用一支 AST 腳本、以「位元組
偏移」(col_offset 在 CPython 是 UTF-8 位元組偏移，不是字元偏移——這份檔案中文
註解/字串極多，字元索引在含 CJK 的行上一律算錯位置，踩到這個坑才發現)精確定位
每一個要加 `biglot_dashboard.` 前綴的識別字，搬移前後用「去除前綴後逐行比對
原始碼」驗證零邏輯差異（含函式內部本來就有自己區域賦值、絕對不能加前綴的
`_rb`／`_pc`／`_ds` 等區域變數，AST 掃描時特別排除）。

已搬移函式一律直接從各自現在的模組 import（不經過 biglot_dashboard 屬性存取，
因為它們不是 biglot_dashboard 自己定義的名稱）：`_cause_tags`/
`_log_scores`/`_oos_summary`/`_paper_summary`/
`_paper_update`/`_px_class`/`_rolling`/`_score_rows`/`_score_td`/
`_shadow_triple`/`_stock_note_td`/`_tag_engine`/`_tx_panel`/`_wrt_td`。
"""
from __future__ import annotations

import html as html_mod
import sys
import time
from datetime import timedelta

import biglot_dashboard
from biglot import streak_live as _streak_live
from biglot import xiaoge_cols as _xg_cols
from biglot import fut_basis as _fbmod
from biglot.cause_tags import _cause_tags
from biglot.user_state import _oos_summary, _stock_note_td
from biglot.scoring_support import _log_scores, _rolling, _score_td, _tag_engine
from biglot import fairvalue_live as _fv
from biglot.story_tags import _story_td, story_tags

from biglot.futures_bigflow import fut_merge_day_td
from biglot.paper_trading import _paper_summary, _paper_update
from biglot.utils import _px_class, _b5n, _b30n, _r30n, _rolling_now, _in_market
from biglot.score_rows import _score_rows
from biglot.reference_loaders import KEY_LINE_LOOKBACK as _KL_LOOKBACK
from biglot.reference_loaders import KEY_LINE_RECENT_BARS as _KL_RECENT

# 關鍵一條線欄各儲存格共用的 tooltip 尾巴(完整規則與回測數字在表頭 title)
_KL_TIP_TAIL = ("規則:紅K∧收盤漲>前一日+4%∧收盤突破前60日最高收盤=觸發棒,線=該棒最低點;"
                f"觸發棒須在最近 {_KL_RECENT} 個交易日內,且之後不曾有日收盤跌破線,否則視為畫不出線。"
                "2026-09-29 回測(21年史·IS/OOS拆2023·日聚類·扣籃子):能畫線狀態 20日 IS+56.2(t+5.07)/"
                "OOS+281.0(t+8.79),IS/OOS同號;但重疊報酬使t偏大、宇宙是自選42檔高波動股。"
                "此欄是狀態顯示不是買賣訊號,不進分數")
from biglot.pe_and_shadow import _shadow_triple
from biglot.tx_panel import _tx_panel
from biglot.futures_pnl import _pnl_panel
from biglot.html_fragments import _bigret_diff_td, _wrt_td, _mark_eod_fallback
from biglot.comovement import _comovement_td
from stock_db import DATA_DIR


_HL_BUY = " style='background:#1f6f43;color:#fff;padding:0 2px'"     # 合理價比賣一高≥0.5tick(持續≥3秒):賣一便宜可買
_HL_SELL = " style='background:#8b2c2c;color:#fff;padding:0 2px'"    # 合理價比買一低≥0.5tick(持續≥3秒):買一偏高可賣


def render():
    now = biglot_dashboard.datetime.now(biglot_dashboard.TZ)
    _hm_now = now.strftime("%H:%M")   # 算一次給散戶背離(SMFI)的 SMFI_ACTIVE_FROM 時間閘門用,不用每檔重算
    all_bks = sorted({bk for m in biglot_dashboard.ST.buckets.values() for bk in m})
    done = [bk for bk in all_bks if bk + timedelta(minutes=5, seconds=10) <= now]
    cur = done[-1] if done else None
    prev = done[-2] if len(done) >= 2 else None
    win6 = done[-6:] if done else []
    win6p = done[-12:-6] if len(done) >= 7 else []
    base_bk = max((bk for bk in all_bks if bk < win6[0]), default=None) if win6 else None

    prior6 = done[-7:-1] if len(done) >= 7 else []

    rows, rets5, rets30 = [], [], []
    _dayrets = []
    for sid in [*biglot_dashboard.NAMES, *biglot_dashboard.REF_EXTRA]:
        _is_ref = sid in biglot_dashboard.REF_SIDS
        m = biglot_dashboard.ST.buckets.get(sid, {})
        ds = biglot_dashboard.ST.day.get(sid)
        a = m.get(cur) if cur else None
        p = m.get(prev) if prev else None
        r = {"sid": sid, "name": biglot_dashboard.REF_NAMES.get(sid) or biglot_dashboard.NAMES[sid],
             "cat": biglot_dashboard.SUBCAT.get(sid, biglot_dashboard.CATS.get(sid, "")),
             "amp20": biglot_dashboard.AMP20.get(sid),
             "unm": sid in biglot_dashboard.RET_UNM or (sid in biglot_dashboard.REF_EXTRA and (biglot_dashboard.PREV_CLOSE.get(sid) or 0) * 1000 >= biglot_dashboard.RETAIL_CAP),
             "ref": _is_ref}
        # 5分窗
        r["w_ret"] = (a["px"] / p["px"] - 1) * 10000 if (a and p and a["px"] and p["px"]) else None
        if r["w_ret"] is not None and not _is_ref:     # 市場代理只用核心/獨立列,參考列不進
            rets5.append(r["w_ret"])
        r["big5"] = a["big"] if a else None
        r["big_prior6"] = (sum(m[bk]["big"] for bk in prior6 if bk in m)
                           if prior6 else None)
        r["big5p"] = p["big"] if p else None
        r["tot5"] = a["tot"] if a else None
        r["tot5p"] = p["tot"] if p else None
        r["retn5"] = a["retn"] if a else None
        rb = biglot_dashboard.RVOL_BASE.get(sid, {})
        bk_lbl = cur.strftime("%H:%M") if cur else None
        r["rvol5"] = (a["tot"] / rb[bk_lbl] if (a and a["tot"] and bk_lbl in rb and rb[bk_lbl] > 0)
                      else None)
        if win6:
            b30 = sum(rb.get(bk.strftime("%H:%M"), 0) for bk in win6)
            t30v = sum(m[bk]["tot"] for bk in win6 if bk in m)
            r["rvol30"] = t30v / b30 if b30 > 0 else None
        else:
            r["rvol30"] = None
        # 全日量能 = 今日累計成交額 ÷ 同時段基準累計(近5日同時段中位加總),供淨分 +1(127日:成交÷20日均額 控佔比後 +13.8/t2.64)
        _exp = sum(rb.get(bk.strftime("%H:%M"), 0) for bk in done)
        r["rvol_day"] = (ds["tot"] / _exp) if (ds and ds.get("tot") and _exp > 0) else None
        share = a["ret2"] / a["tot"] * 100 if (a and a["tot"]) else None
        share_p = p["ret2"] / p["tot"] * 100 if (p and p["tot"]) else None
        r["share5"] = share
        if a and a["tot"]:
            r["rbuy5"] = (a["ret2"] + a["retn"]) / 2 / a["tot"] * 100
            r["rsell5"] = (a["ret2"] - a["retn"]) / 2 / a["tot"] * 100
        else:
            r["rbuy5"] = r["rsell5"] = None
        r["dshare"] = share - share_p if (share is not None and share_p is not None) else None
        # 大戶連續同向窗數
        streak = 0
        for bk in reversed(done):
            v = m.get(bk, {}).get("big", 0)
            if streak == 0:
                sgn0 = 1 if v > 0 else (-1 if v < 0 else 0)
                if sgn0 == 0:
                    break
                streak = sgn0
            else:
                if (v > 0) == (streak > 0) and v != 0:
                    streak += 1 if streak > 0 else -1
                else:
                    break
        r["streak"] = streak
        # 30分窗
        if win6:
            r["big30"] = sum(m[bk]["big"] for bk in win6 if bk in m)
            t30 = sum(m[bk]["tot"] for bk in win6 if bk in m)
            r["tot30"] = t30
            _r2 = sum(m[bk]["ret2"] for bk in win6 if bk in m)
            _rn = sum(m[bk]["retn"] for bk in win6 if bk in m)
            r["rbuy30"] = (_r2 + _rn) / 2 / t30 * 100 if t30 else None
            r["rsell30"] = (_r2 - _rn) / 2 / t30 * 100 if t30 else None
            r["share30"] = (_r2 / t30 * 100
                            if t30 else None)
            r["big30p"] = sum(m[bk]["big"] for bk in win6p if bk in m) if win6p else None
            t30p = sum(m[bk]["tot"] for bk in win6p if bk in m) if win6p else 0
            sh30p = (sum(m[bk]["ret2"] for bk in win6p if bk in m) / t30p * 100
                     if t30p else None)
            r["dsh30"] = (r["share30"] - sh30p
                          if (r["share30"] is not None and sh30p is not None) else None)
            px_end = next((m[bk]["px"] for bk in reversed(win6) if bk in m and m[bk]["px"]), None)
            px_base = (m[base_bk]["px"] if base_bk and base_bk in m and m[base_bk].get("px")
                       else next((m[bk]["px"] for bk in win6 if bk in m and m[bk]["px"]), None))
            r["r30"] = (px_end / px_base - 1) * 10000 if (px_end and px_base) else None
            if r["r30"] is not None and not _is_ref:
                rets30.append(r["r30"])
        else:
            r["big30"] = r["share30"] = r["r30"] = None
            r["rbuy30"] = r["rsell30"] = None
            r["tot30"] = r["big30p"] = r["dsh30"] = None
        # 全日
        # 2026-10-03 jack 糾正:5分/30分滾動窗跟期散統計,盤中該用真實時間,但收盤後
        # (含伺服器任何時間點重啟、假日)改用該檔最後一筆已知成交時間當基準,見
        # biglot/utils.py::_rolling_now docstring——這是「收盤幾小時後重開伺服器,
        # 現價/5分/30分全部變成—」這個現象的根因修復,不是另外加判斷特例。
        _now_ref = _rolling_now(sid)
        r.update(_rolling(sid, _now_ref))                # 5分/30分 欄位每秒滾動(顯示用 + 即時標籤引擎)
        r["rvol5_r"] =((r["tot5_r"] / rb[bk_lbl]) if (r.get("tot5_r") and bk_lbl in rb and rb[bk_lbl] > 0)
                        else None)                        # 滾動 5 分量能倍數(對同時段基準)
        _pc = biglot_dashboard.PREV_CLOSE.get(sid)                       # 前一交易日收盤(專業看盤主報價基準)
        # 現價:今天完全沒有任何成交紀錄時(假日、今天尚未開盤、該檔今天從未成交),
        # 退回前一交易日官方收盤價顯示,不讓「現價」整欄空白——2026-10-03 jack 交辦:
        # 「收盤價還是可以顯示」。退回昨收時,對昨收漲跌自然算出 0(誠實反映「沒有比
        # 官方收盤價更新的資訊」,不是真的今天漲跌0%),r["px_is_prev_close"]供需要
        # 區分兩者的呼叫端使用(目前沒有欄位用到,保留供未來擴充)。
        last_price = biglot_dashboard.ST.close_px.get(sid) or biglot_dashboard.ST.last_px.get(sid)
        r["px_is_prev_close"] = last_price is None and _pc is not None
        if last_price is None:
            last_price = _pc
        r["px"] = last_price
        _pc2 = biglot_dashboard.PREV2_CLOSE.get(sid)
        if r["px_is_prev_close"] and _pc2:
            # 今天沒有成交(休市日/收盤後重啟):漲跌保留最近一個交易日收盤當時的數字,不顯示 0
            r["chg_amt"] = _pc - _pc2
            r["chg_pct"] = (_pc / _pc2 - 1) * 100
        else:
            r["chg_amt"] = (last_price - _pc) if (_pc and last_price) else None
            r["chg_pct"] = ((last_price / _pc - 1) * 100) if (_pc and last_price) else None
        r["day_ret"] = ((last_price / ds["px0"] - 1) * 100
                        if (ds and ds["px0"] and last_price) else None)
        r["bigday"] = ds["big"] if ds else None
        r["retday"] = ds["ret"] if ds else None
        r["bigpm"] = ds["big_pm"] if ds else None
        # 融資/借券變化幅度波動風險分數（T-1資料，只預測盤中振幅，非方向/跳空訊號）
        vr = biglot_dashboard.VOLRISK.get(sid)
        r["volrisk_score"] = vr["score"] if vr else None
        r["volrisk_tier"] = vr["tier"] if vr else None
        if not vr:
            r["volrisk_title"] = "融資/借券歷史資料不足60個交易日，無法計算分數"
        elif vr.get("stale"):
            r["volrisk_title"] = (
                f"融資或借券最新資料已 {vr['days_stale']} 天未更新(>{biglot_dashboard.VOLRISK_STALE_DAYS}天門檻)"
                f"(融資asof {vr['margin_asof']}／借券asof {vr['lending_asof']})，"
                f"可能是處置股停融資或資料延遲，不計分數")
        else:
            stale_note = f"（資料{vr['days_stale']}天前,較舊）" if vr["days_stale"] > 2 else ""
            r["volrisk_title"] = (
                f"波動風險分數 {vr['score']:.0f}/100{stale_note} · "
                f"T-1 融資({vr['margin_asof']})日變動{vr['margin_pct']*100:+.1f}%"
                f"(變動幅度歷史分位{vr['margin_abs_pctile']*100:.0f}%) · "
                f"借券({vr['lending_asof']})日變動{vr['lending_pct']*100:+.1f}%"
                f"(變動幅度歷史分位{vr['lending_abs_pctile']*100:.0f}%) · "
                f"宇宙回測:分數每+1分,控制當日振幅後隔日振幅仍+0.006pp(t3.40 p0.0007)。"
                f"只預測盤中來回幅度,對隔日淨報酬/跳空/量能皆無解釋力,非方向訊號")
        # 隔夜策略因子
        r["bigsh_d"] = (ds["big"] / ds["tot"] * 100) if (ds and ds["tot"]) else None
        r["ret_open_sh"] = (ds["ret_open"] / ds["tot_open"] * 100) if (ds and ds["tot_open"]) else None
        # 散戶背離(SMFI)「現在」這一側:2026-10-04 jack 交辦「我想要的是盤中當中可以用到的
        # 欄位,隨時取得,即時變化較好」——原設計是收盤後才看的隔夜因子(尾盤12:55-13:20 vs
        # 開盤),只在收盤前25分鐘才有值。改用近30分滾動窗(跟5分/30分欄位同算法,_r30n)取代
        # 固定尾盤窗口,從 SMFI_ACTIVE_FROM 起算、之前一律 None(顯示—)——見
        # biglot_dashboard.py::SMFI_ACTIVE_FROM 的完整回測依據(47點滾動窗時段驗證)。
        # ⚠ ds["ret_close"]/["tot_close"](固定尾盤窗累加器)不受影響、照常累積:day_views.py
        # (/day /history)跟 reference_loaders.py 的 PREV2D 回退需要「這天收盤後的最終定格
        # 值」,固定尾盤窗口剛好等於滾動窗撐到收盤那一刻收斂到的答案,兩者語意不同、互不干擾。
        r["ret_close_sh"] = _r30n(r) if _hm_now >= biglot_dashboard.SMFI_ACTIVE_FROM else None
        r["ret_smfi"] = ((r["ret_close_sh"] - r["ret_open_sh"]) if (r["ret_open_sh"] is not None and r["ret_close_sh"] is not None) else None)
        last12 = [m[bk]["px"] for bk in done[-12:] if bk in m and m[bk]["px"]]
        r["cmp1h"] = ((last_price / (sum(last12) / len(last12)) - 1) * 100
                      if (len(last12) >= 8 and last_price) else None)
        hb = biglot_dashboard.HIST_BIG.get(sid, [])
        streak_ok = (ds and ds["big"] > 0 and len(hb) >= 2 and hb[-1] > 0 and hb[-2] > 0)
        tongmai = (ds and ds["big"] < 0 and ds["ret"] < 0)
        weak_open = tongmai and (r["cmp1h"] is not None and r["cmp1h"] > 0)
        r["stamp"] = ("連3買" if streak_ok else "") + ("⚠同賣" if tongmai else "") + ("↓弱開" if weak_open else "")
        ypl = biglot_dashboard.Y_PMLOW.get(sid)
        r["pmlow"] = ypl                             # 昨日午後低=防線價(供破昨防線標註)
        r["pmlow_warn"] = (ypl is not None and last_price is not None and last_price <= ypl * 1.002)
        # VWAP 與委託簿
        tv = sum(v["vol"] for v in m.values())
        tpv = sum(v["pxvol"] for v in m.values())
        vwap = tpv / tv if tv else None
        r["vwap_gap"] = (last_price / vwap - 1) * 10000 if (vwap and last_price) else None
        bkk = biglot_dashboard.ST.book.get(sid)
        if bkk:
            try:
                v = int(bkk.get("v") or 0)
                tstr = bkk.get("t", "09:00:00")
                mins = max((int(tstr[:2]) - 9) * 60 + int(tstr[3:5]), 1)
                pm_rate = v / mins
                bq, aq = sum(bkk.get("bq", [])), sum(bkk.get("aq", []))
                r["bid_min"] = bq / pm_rate if pm_rate else None
                r["ask_min"] = aq / pm_rate if pm_rate else None
                u, px_now = bkk.get("u"), last_price or bkk.get("z")
                r["lu_dist"] = ((float(u) / px_now - 1) * 100
                                if (u and px_now) else None)
            except Exception:
                r["bid_min"] = r["ask_min"] = r["lu_dist"] = None
        else:
            r["bid_min"] = r["ask_min"] = r["lu_dist"] = None
        if r.get("day_ret") is not None and not _is_ref:
            _dayrets.append(r["day_ret"])
        rows.append(r)

    # 參考列(池內2+池外10)只借同一套逐列計算,之後立刻切開:不進橫斷面排名/旗標/標籤引擎/淨分/前10/計分落地
    ref_rows = [r for r in rows if r["ref"]]
    rows = [r for r in rows if not r["ref"]]
    for r in ref_rows:
        r.update(flag="", bull_n=0, bear_n=0, bull_txt="", bear_txt="", ex_txt="")

    mkt5 = sum(rets5) / len(rets5) if rets5 else 0.0
    mkt30 = sum(rets30) / len(rets30) if rets30 else 0.0

    # 排名(1=最大;注意力分流用,非訊號——個股排名持續性已檢定為不可持續)
    def _rank(key):
        order = sorted((r for r in rows if r.get(key) is not None),
                       key=lambda x: -x[key])
        return {r["sid"]: i + 1 for i, r in enumerate(order)}
    rk5, rk5p = _rank("big5"), _rank("big5p")
    rkh, rkhp = _rank("tot5"), _rank("tot5p")
    rk30, rk30p, rkd = _rank("big30"), _rank("big30p"), _rank("bigday")
    for r in rows:
        r["r5"] = rk5.get(r["sid"])
        r["d5"] = (rk5p[r["sid"]] - r["r5"]) if (r["r5"] and r["sid"] in rk5p) else None
        r["rh"] = rkh.get(r["sid"])
        r["dh"] = (rkhp[r["sid"]] - r["rh"]) if (r["rh"] and r["sid"] in rkhp) else None
        r["r30r"] = rk30.get(r["sid"])
        r["d30"] = ((rk30p[r["sid"]] - r["r30r"])
                    if (r["r30r"] and r["sid"] in rk30p) else None)
        r["rdr"] = rkd.get(r["sid"])
    # 旗標(30分尺度,127日面板驗證:勿追超額-4.9~-5.6bps cl-t≈-3;跌深大戶接+8.9bps cl-t+2.3)
    for r in rows:
        r["flag"] = ""
        big30n = (r["big30"] / r["tot30"] * 100
                  if (r.get("big30") is not None and r.get("tot30")) else None)
        if (r["r30"] is not None and r["r30"] < -30
                and big30n is not None and big30n > 5):
            r["flag"] = "🟢跌深大戶接"
        # 5分早期旗標(127日驗證預測未來30分:F8超額-6.1/cl-t-4.9、F9 -9.1/-5.0;
        #  F7 +10.8/+2.8僅觀察)——與30分旗標並列顯示
        big5n = (r["big5"] / r["tot5"] * 100
                 if (r.get("big5") is not None and r.get("tot5")) else None)
        early = ""
        if r["w_ret"] is not None and r["w_ret"] > 20:
            if ((r["dshare"] is not None and r["dshare"] > 5 and not r["unm"])
                    or (big5n is not None and big5n < -5)):
                early = "⚠勿追5m"
                if r["rvol5"] is not None and r["rvol5"] < 1.0:
                    early = "⚠勿追5m(枯量,最強帶)"   # 127日:<1x 帶超額-7~-9bps t≈-3.3
        elif (r["w_ret"] is not None and r["w_ret"] < -20
              and big5n is not None and big5n > 5):
            if r["rvol5"] is not None and r["rvol5"] >= 0.5:
                early = "🟢跌深大戶接"
        if early:
            r["flag"] = (r["flag"] + " " + early).strip()

    # 漲訊/跌訊 逐條件計分(只收錄已驗證格;每格獨立打勾,分數=命中數)
    for r in rows:
        bear = []
        if r.get("r30") is not None and r["r30"] >= 150:
            bear.append("噴後過熱")        # 波段峰後30分均-32/中位-45
        if "⚠勿追5m" in r["flag"]:
            bear.append("勿追5m")
        if "⚠同賣" in r["stamp"]:
            bear.append("同賣")        # 大戶賣∧散戶賣 隔夜-28bps/t-6
        if r.get("pmlow_warn"):
            bear.append(f"破昨防線@{r['pmlow']:g}" if r.get("pmlow") else "破昨防線")  # 觸昨日午後低=主力尾盤防守位 -125bps/73%貫穿
        bull = []
        if "🟢" in r["flag"]:
            bull.append("深接")        # 跌深大戶接(RVOL≥0.5,正EV) +11~14bps/t3.4+
        if (r.get("bigsh_d") is not None and r["bigsh_d"] >= 10
                and r.get("cmp1h") is not None and r["cmp1h"] < 0):
            bull.append("蓄勢隔夜")     # 佔比≥10%∧收盤前壓著=隔夜雙鍵(IC t7.1)
        if "連3買" in r["stamp"]:
            bull.append("連3買")       # 持續章(確認格)
        r["bear_n"], r["bear_txt"] = len(bear), "·".join(bear)
        r["bull_n"], r["bull_txt"] = len(bull), "·".join(bull)
    # ---- 即時標籤引擎覆寫盤中 7 格(完成桶版仍算,供 flagbar/舊欄);日級格照舊接在後面 ----
    try:
        _tg = _tag_engine(rows, time.time(), now)
        try:                                   # 故事欄(狀態描述,不進分數)。壞掉只讓該欄變「—」,不影響主表
            _stories = story_tags(rows)
        except Exception as _se:  # noqa: BLE001
            print(f"[story_tags] {_se!r}", file=sys.stderr); _stories = {}
        for r in rows:
            g = _tg.get(r["sid"], {"bull": [], "bear": [], "ex": []})
            bull = g["bull"] + [t for t in r["bull_txt"].split("·") if t in ("蓄勢隔夜", "連3買")]
            bear = g["bear"] + [t for t in r["bear_txt"].split("·") if t == "同賣" or t.startswith("破昨防線")]
            r["bull_n"], r["bull_txt"] = len(bull), "·".join(bull)
            r["bear_n"], r["bear_txt"] = len(bear), "·".join(bear)
            r["ex_txt"] = " ".join(g["ex"])
    except Exception as _e:  # noqa: BLE001 -- 引擎失敗退回完成桶版標籤
        print(f"[tag_engine] {_e!r}", file=sys.stderr)
    # ---- 淨分(2026-09-24 設計,加總分未驗證;各項權重依 127 日基準率 0/±1/±2)----
    try:
        _score_rows(rows, mkt30, time.time())
        _cause_tags(rows, biglot_dashboard.ST.date)
        try:
            _paper_update(rows, time.time())
        except Exception as _pe:  # noqa: BLE001
            print(f"[paper] {_pe!r}", file=sys.stderr)
        _log_scores(rows, biglot_dashboard.ST.date)
    except Exception as _e:  # noqa: BLE001
        print(f"[score] {_e!r}", file=sys.stderr)

    # 固定產業鏈排序(不隨大戶流跳位);查無者(理論上不會有)排最後、依big30
    rows.sort(key=lambda r: (biglot_dashboard.SORT_INDEX.get(r["sid"], 999), -(r["big30"] or 0)))
    rows_all = sorted(rows + ref_rows, key=lambda r: biglot_dashboard.SORT_INDEX.get(r["sid"], 999))   # 顯示用(含參考列)
    # 產業交界的最後一列→畫粗線
    grpend = set()
    for i, r in enumerate(rows_all):
        nxt = rows_all[i + 1] if i + 1 < len(rows_all) else None
        if nxt is None or biglot_dashboard.CLUSTER_OF.get(r["sid"]) != biglot_dashboard.CLUSTER_OF.get(nxt["sid"]):
            grpend.add(r["sid"])
    win_lbl = (f"{cur.strftime('%H:%M')}–{(cur+timedelta(minutes=5)).strftime('%H:%M')}"
               if cur else "—")
    w30_lbl = (f"{win6[0].strftime('%H:%M')}–{(win6[-1]+timedelta(minutes=5)).strftime('%H:%M')}"
               if win6 else "—")

    # 旗標速覽 + 資料延遲警示
    fl_catch = " ".join(f"{r['sid']}{r['name']}" for r in rows
                        if r["flag"] == "🟢跌深大戶接")
    flag_bar = ""
    gate_txt = ""
    if biglot_dashboard.UNI5 is not None:
        gate_on = biglot_dashboard.UNI5 < -5
        gate_txt = (f"<span style='color:{'#ff7b72' if gate_on else '#8b949e'}'>閘門(近5日{biglot_dashboard.UNI5:+.1f}%):"
                    f"{'🔴啟動-僅記帳' if gate_on else '🟢關'}</span> · ")
    on_pool = [r for r in rows if r.get("bigday") and r["bigday"] > 0 and not r["unm"]]
    cand_txt = ""
    if now.strftime("%H:%M") >= "13:00" and len(on_pool) >= 5:
        pool = sorted(on_pool, key=lambda r: -r["bigday"])[:10]
        pool = [r for r in pool if r.get("cmp1h") is not None]
        picks = sorted(pool, key=lambda r: r["cmp1h"])[:3]
        cand_txt = ("<span style='color:#ffd700'>隔夜候選(前10∧壓縮深3,13:25定案):</span> "
                    + " ".join(f"{r['sid']}{r['name']}({r['cmp1h']:+.1f}%/佔{r['bigsh_d']:.0f}%)"
                               for r in picks) + " · ")
    if fl_catch:
        flag_bar += f"<span style='color:#3fb950'>🟢跌深大戶接(唯一正EV格):</span> {fl_catch} "
    fl_e1 = " ".join(f"{r['sid']}{r['name']}" for r in rows if "⚠勿追5m" in r["flag"])
    fl_e2 = " ".join(f"{r['sid']}{r['name']}" for r in rows if "🟡接刀觀察" in r["flag"])
    if fl_e1:
        flag_bar += f"<span class='warnv'>⚠勿追5m(早期):</span> {fl_e1} "
    if fl_e2:
        flag_bar += f"<span style='color:#d29922'>🟡接刀觀察(5m早期,未達門檻):</span> {fl_e2}"
    if not flag_bar:
        flag_bar = "<span class='dim'>本窗無旗標</span>"
    raw_path = DATA_DIR.parent / "cache" / "biglot_live_watch" / f"raw_{biglot_dashboard.ST.date}.jsonl"
    in_mkt = now.weekday() < 5 and "08:30" <= now.strftime("%H:%M") <= "13:32"   # 含 08:30 起盤前試撮
    stale_bar = ""
    if in_mkt:
        try:
            age = time.time() - raw_path.stat().st_mtime
            if age > 90:
                stale_bar = (f"<div style='background:#6e1a1a;color:#ffb3b3;padding:4px 8px;"
                             f"font-weight:700'>⚠ 資料延遲 {age:.0f} 秒——collector 可能斷線,"
                             f"表格為舊資料</div>")
        except FileNotFoundError:
            stale_bar = ("<div style='background:#6e1a1a;color:#ffb3b3;padding:4px 8px;"
                         "font-weight:700'>⚠ 今日 raw 檔不存在——collector 未啟動</div>")
    else:
        stale_bar = "<div style='color:#8b949e;padding:2px 8px'>盤後定格(非交易時段)</div>"

    def td(v, fmt="wan", cls_by_sign=True, unm=False):
        if unm:
            return "<td class='dim'>不可測</td>"
        if v is None:
            return "<td class='dim'>—</td>"
        cls = ""
        if cls_by_sign:
            cls = "up" if v > 0 else ("dn" if v < 0 else "")
        if fmt == "wan":
            txt = f"{v/1e4:+,.0f}"
        elif fmt == "yi":
            txt = f"{v/1e8:+.2f}"
        elif fmt == "bps":
            txt = f"{v:+.0f}"
        elif fmt == "pct":
            txt = f"{v:.0f}%"
            cls = "warnv" if v >= 35 else ""
        elif fmt == "pct2":
            txt = f"{v:+.2f}%"
        elif fmt == "min":
            txt = f"{v:.1f}分"
            cls = "wall" if v >= 10 else ("dim" if v < 3 else "")
        elif fmt == "int":
            txt = f"{v:+d}"
        else:
            txt = str(v)
        return f"<td class='{cls}' data-sort='{v}'>{txt}</td>"

    def rk_td(n, d=None):
        if n is None:
            return "<td class='dim'>—</td>"
        arrow = ""
        if d is not None and d != 0:
            arrow = (f"<span class='up'>▲{d}</span>" if d > 0
                     else f"<span class='dn'>▼{-d}</span>")
        cls = "rk1" if n <= 3 else ("rkN" if n >= 43 else "")
        return f"<td class='{cls}'>{n}{arrow}</td>"

    def _role_chip(r):
        if r.get("ref"):
            _n = (biglot_dashboard.REF_POOL.get(r["sid"]) or biglot_dashboard.REF_EXTRA.get(r["sid"]) or {}).get("note", "")
            _t = html_mod.escape(f"族群參考列(不進排名/標籤/淨分/旗標,只看族群脈絡;交易標的是同組有期貨的核心檔)。{_n}", quote=True)
            return f"<span class='rolechip rref' title=\"{_t}\">參</span>"
        _i = biglot_dashboard.INDEP.get(r["sid"])
        if _i:
            _t = html_mod.escape(f"{_i['kind']}:{_i['reason']}(原地標注,不隱藏)", quote=True)
            return f"<span class='rolechip rind' title=\"{_t}\">獨</span>"
        return ""

    VR_CLS = {"🌊🌊": "vr2", "🌊": "vr1", "": "vr0"}   # 對應 VOLRISK_TIERS 的分級,單一事實來源

    def vr_td(r):
        title = html_mod.escape(r.get("volrisk_title") or "")
        score = r.get("volrisk_score")
        if score is None:
            return f"<td class='dim' title=\"{title}\">—</td>"
        tier = r.get("volrisk_tier")
        cls = VR_CLS.get(tier, "") if tier is not None else ""
        icon = tier if tier else ""
        return f"<td class='{cls}' data-sort='{score}' title=\"{title}\">{icon}{score:.0f}</td>"

    _mktday = sum(_dayrets) / len(_dayrets) if _dayrets else None
    for r in rows_all:
        r["rs_live"] = (r["day_ret"] - _mktday
                        if (r.get("day_ret") is not None and _mktday is not None) else None)
        # 順/逆市:個股30分方向 vs 市場30分方向(描述性脈絡,非訊號——市場是最強控制變數)
        if r.get("r30") is not None and abs(r["r30"]) >= 20 and abs(mkt30) >= 5:
            same = (r["r30"] > 0) == (mkt30 > 0)
            r["mkt_ctx"] = ("順漲" if (same and mkt30 > 0)
                            else "順跌" if same
                            else "逆強" if r["r30"] > 0
                            else "逆弱")
        else:
            r["mkt_ctx"] = None
        dt = biglot_dashboard.DAILY_TREND.get(r["sid"])
        r["dtrend"] = dt
        # 20MA 正乖離率(2026-09-25 jack 交辦):現價 ÷ 月線(20 日均價,PIT 用昨收含之前 20 日)− 1。
        # ≥30% = 短線超買過熱、回檔壓力極高的民間經驗法則;純描述性警示,不進分數。
        _ma20 = dt.get("ma20") if dt else None
        r["bias20"] = ((r["px"] / _ma20 - 1) * 100) if (_ma20 and r.get("px")) else None
        # 關鍵一條線(2026-09-29 jack 定案改二分口徑,見 _load_key_line docstring):
        # 只有 alive(20 個交易日內有觸發棒 ∧ 收盤未曾跌破)才算「畫得出線」、才有距離%;
        # base/stale/broken 一律顯示「沒有」不給數值(舊版的「已破線」標籤已移除)。
        _kl = biglot_dashboard.KEY_LINE.get(r["sid"]) or {}
        r["key_line_state"] = _kl.get("state", "base")
        r["key_line_age"] = _kl.get("age")
        _kl_alive = r["key_line_state"] == "alive"
        r["key_line"] = _kl.get("price") if _kl_alive else None
        r["key_line_date"] = _kl.get("date") if _kl_alive else None
        # 盤前/無即時價時退回昨收算距離,免得「有線」被誤顯示成「沒有」(2026-09-30 盤前實測踩到)
        _kl_ref = r.get("px") or biglot_dashboard.PREV_CLOSE.get(r["sid"])
        r["key_line_ref_prev"] = bool(_kl_alive and not r.get("px") and _kl_ref)
        r["key_line_dist"] = ((_kl_ref / _kl["price"] - 1) * 100) if (_kl_alive and _kl_ref) else None
        # 本益比同族群排名(2026-09-25 jack 交辦,見 _load_pe_peer docstring):分子用即時價現算,分母用快照 TTM EPS。
        _eps = biglot_dashboard.PE_EPS.get(r["sid"])
        r["pe_live"] = (r["px"] / _eps[0]) if (_eps and _eps[0] and _eps[0] > 0 and r.get("px")) else None
        r["pe_eps_asof"] = _eps[1] if _eps else None
        r["pe_group"] = biglot_dashboard.SUBCAT.get(r["sid"])
        _peer_rows = biglot_dashboard.PE_TABLE.get(r["pe_group"], [])
        _pe_list = [(_pr["sid"], r["pe_live"] if _pr["sid"] == r["sid"] else _pr.get("pe"))
                    for _pr in _peer_rows]
        _pe_list = sorted((x for x in _pe_list if x[1] is not None and x[1] > 0), key=lambda x: x[1])
        r["pe_rank"] = r["pe_n"] = r["pe_pctile"] = None
        for _i, (_psid, _pv) in enumerate(_pe_list):
            if _psid == r["sid"]:
                r["pe_rank"], r["pe_n"] = _i + 1, len(_pe_list)
                r["pe_pctile"] = round(_i / max(1, len(_pe_list) - 1) * 100) if len(_pe_list) > 1 else 50
                break
        # 集保戶股權分散表(2026-09-29 jack 交辦,見 _load_holder_big800 docstring):大戶(≥800張)比例%,
        # 週頻、純參考展示,不進分數(已知不可交易,詳見該函式 docstring 的文獻/對抗檢定依據)。
        _hb800 = biglot_dashboard.HOLDER_BIG800.get(r["sid"])
        r["holder_big800"] = _hb800[0] if _hb800 else None
        r["holder_big800_asof"] = _hb800[1] if _hb800 else None
        r["holder_big800_delta"] = _hb800[2] if _hb800 else None       # 2026-10-03新增:對前一週變動(pp)
        r["holder_big800_prev_asof"] = _hb800[3] if _hb800 else None
        # 隱形大戶守價位(2026-09-25 jack 交辦,見 _iceberg_update docstring):
        # 靠山(backing)兩側皆已驗證為雜訊,只當描述性顯示;突破壓力(breakout_bull)是唯一
        # 通過延遲檢定+安慰劑對照的真訊號(t-2.2~-2.9,回落/fade,非延續),近30分內顯示、並進分數。
        _ibstate = biglot_dashboard.ST.iceberg.get(r["sid"]) or {"bid": {}, "ask": {}, "last_breakout": None}
        _book = biglot_dashboard.ST.book.get(r["sid"]) or {}
        _bb = next((p for p, q in zip(_book.get("bp") or [], _book.get("bq") or []) if p and q and p > 0), None)
        _ba = next((p for p, q in zip(_book.get("ap") or [], _book.get("aq") or []) if p and q and p > 0), None)
        _backing_bid = bool(_bb and round(_bb, 4) in _ibstate["bid"] and _ibstate["bid"][round(_bb, 4)]["detected"])
        _backing_ask = bool(_ba and round(_ba, 4) in _ibstate["ask"] and _ibstate["ask"][round(_ba, 4)]["detected"])
        _lb = _ibstate.get("last_breakout")
        _recent_breakout = _lb if (_lb and time.time() - _lb["ts"] <= 30 * 60) else None
        r["iceberg_backing_bid"] = _backing_bid
        r["iceberg_backing_ask"] = _backing_ask
        r["iceberg_recent_breakout"] = _recent_breakout
        # 突破壓力後15分鐘延遲檢定均值-8.6bps(t-2.24);0.5倍縮水(僅14日樣本,比127日基準更保守),
        # 隨經過時間線性淡出(30分後歸零),只對 breakout_bull(fade)進分數,bear/backing 仍是NULL不進分數
        r["iceberg_score"] = 0.0
        if _recent_breakout and _recent_breakout["kind"] == "breakout_bull":
            _elapsed = time.time() - _recent_breakout["ts"]
            _decay = max(0.0, 1 - _elapsed / (30 * 60))
            r["iceberg_score"] = -8.6 * 0.5 * _decay
            if r["iceberg_score"] and r.get("sc_v2") is not None:
                r["sc_v2"] += r["iceberg_score"]
                (r.setdefault("sc_v2_items", [])).append(
                    (f"突破壓力防回落({_elapsed/60:.0f}′)", round(r["iceberg_score"], 1)))
    # 30分成交額百分位(代理人規則⑤):同 trader_signal_watch.pct_rank —— 嚴格小於 ÷ 母體(NAMES 42 檔、≥5 檔有值)×100
    _amt30_pool = [r["tot30_r"] for r in rows_all if r["sid"] in biglot_dashboard.NAMES and r.get("tot30_r") is not None]
    for r in rows_all:
        _x = r.get("tot30_r")
        r["amtpct30"] = (100 * sum(v < _x for v in _amt30_pool) / len(_amt30_pool)
                         if (len(_amt30_pool) >= 5 and _x is not None and r["sid"] in biglot_dashboard.NAMES) else None)
    _fv.ensure_started(); _FV_SNAP = _fv.snapshot().get("stocks", {})
    trs = []
    for r in rows_all:
        name = html_mod.escape(f"{r['sid']} {r['name']}")
        _role_cls = "refrow" if r.get("ref") else ("indep" if r["sid"] in biglot_dashboard.INDEP else "")      # 參考列/獨立標注底色
        _band = " class='" + " ".join(x for x in (("band" if r["sid"] in grpend else ""), _role_cls) if x) + "'" if (r["sid"] in grpend or _role_cls) else ""     # 產業交界粗線
        _cc = r.get("chg_amt")                          # 對昨收漲跌:紅漲綠跌(台股慣例)
        _qcls = _px_class(r.get("px"), biglot_dashboard.PREV_CLOSE.get(r["sid"]), r.get("chg_pct"))
        # 對昨收:只用一般紅漲綠跌字色(不要紅底白字——紅底只留給「價」欄)
        _ccls = "up" if (_cc is not None and _cc > 0) else ("dn" if (_cc is not None and _cc < 0) else "")
        if _cc is not None:
            _arrow = "▲" if _cc > 0 else ("▼" if _cc < 0 else "")
            _chgtd = f"<td class='{_ccls}' data-sort='{r['chg_pct']}'>{_arrow}{abs(_cc):g} {r['chg_pct']:+.2f}%</td>"
        else:
            _chgtd = "<td class='dim'>—</td>"
        # 個股期貨買一/賣一 拆兩欄:委託價+委託量(小字)。title 附成交價與基差%(期貨/現股−1)
        _fp = biglot_dashboard.FUT_PX.get(r["sid"]) if isinstance(biglot_dashboard.FUT_PX.get(r["sid"]), dict) else None
        _futpx = _fp.get("px") if _fp else None
        _fbid = _fp.get("bid") if _fp else None
        _fask = _fp.get("ask") if _fp else None
        # 基差% 全站統一：期貨中價÷現貨中價−1（中價=買一賣一平均；2026-10-21 jack 要求，原為期貨最新成交價÷現價）。
        # 現貨中價取逐筆帶的買一/賣一（缺值退回現價）；期貨簿斷線逾 30 秒會被剔除 → 基差不顯示。
        _fmid = _fbmod.mid(_fbid, _fask)
        _smid = _fbmod.mid(biglot_dashboard.ST.last_bid.get(r["sid"]), biglot_dashboard.ST.last_ask.get(r["sid"]), r.get("px"))
        _basis = _fbmod.basis_pct(_fmid, _smid) if (_fmid and _smid and not r.get("px_is_prev_close")) else None
        r["fut_basis_pct"] = _basis
        _bastxt = ""
        if _futpx and _basis is not None:
            _bastxt = f" · 期貨成交{_futpx:g} 中價基差{_basis:+.2f}%"
        _ffpc = _fp.get("fpc") if _fp else None         # 期貨自身昨結:算期貨漲跌停(紅底/綠底)
        # 著色:有期貨昨結→用 _px_class(漲停紅底白字/跌停綠底白字/一般紅漲綠跌);
        # 無昨結時退回買紅賣綠(維持買賣側可辨)。
        _fbcls = (_px_class(_fbid, _ffpc, (_fbid / _ffpc - 1) * 100) if (_fbid and _ffpc)
                  else ("up" if _fbid is not None else ""))
        _facls = (_px_class(_fask, _ffpc, (_fask / _ffpc - 1) * 100) if (_fask and _ffpc)
                  else ("dn" if _fask is not None else ""))
        # 成交價落在買一或賣一的那一邊:該側報價加黃底線(=主動方 price==ask主動買 / price==bid主動賣)
        _hitb = _futpx is not None and _fbid is not None and abs(_futpx - _fbid) < 1e-6
        _hita = _futpx is not None and _fask is not None and abs(_futpx - _fask) < 1e-6
        _mini = "小" if r["sid"] in biglot_dashboard.FUT_MINI else ""
        _bp = (f"<span class='hit'>{_fbid:g}</span>" if _hitb else f"{_fbid:g}") if _fbid is not None else "—"
        _ap = (f"<span class='hit'>{_fask:g}</span>" if _hita else f"{_fask:g}") if _fask is not None else "—"
        if _fbid is not None or _fask is not None:
            _bsz = f"<span class='dim' style='font-size:9px'>×{_fp.get('bidsz') or 0}{_mini}</span>" if _fbid is not None else ""
            _asz = f"<span class='dim' style='font-size:9px'>×{_fp.get('asksz') or 0}{_mini}</span>" if _fask is not None else ""
            _spr = f"{_fask - _fbid:g}" if (_fbid is not None and _fask is not None) else "—"
            _fbatd = (f"<td class='frz' data-sort='{(_fask - _fbid) if (_fbid is not None and _fask is not None) else ''}' "
                      f"title='個股期貨買一/賣一(委託價×委託量)。紅=買一、綠=賣一,黃底線=最新成交落在該側(主動方)。買賣價差 {_spr}。"
                      f"{_bastxt.strip(' ·')}'>"
                      f"<span class='{_fbcls or 'up'}'>{_bp}</span>{_bsz} / <span class='{_facls or 'dn'}'>{_ap}</span>{_asz}</td>")
        else:
            _fbatd = "<td class='frz dim'>—</td>"
        # 期貨中價／校正合理價(2026-10-20,fairvalue_live 背景執行緒每秒前進;邏輯同離線驗證 fairvalue_kalman.Filter):
        # 平常=買賣簿中價;台指自上次報價動 ≥4bps 且報價 ≥10 秒沒更新 → 改顯示校正價(橘『校』)。
        # 小字=買一/賣一/最新成交 各自偏離該價的 bps;報價年齡 ≥10 秒才標。⚠ 校正價前向驗證中(2026-11-03 判決)。
        _fv_snap = _FV_SNAP.get(r["sid"])
        if _fv_snap and not _fv_snap["why"]:
            _trig = _fv_snap["trig"]
            _fval = _fv_snap["fair"]               # 一直顯示 Kalman 校正合理價(中價只是買賣簿本身,沒有資訊)
            _tk = _fv.tick_size(_fval); _sz = biglot_dashboard.FUT_CONTRACT_SIZE.get(r["sid"], 2000.0)
            def _tm(px):    # 偏離 → (tick 數, 每口金額元);小於 0.5 tick 淡化成灰色
                d = px - _fval
                return d / _tk, d * _sz, ("dim" if abs(d / _tk) < 0.5 else "")
            def _fmt(lab, px, hl=""):
                t, mny, c = _tm(px)
                t, mny = round(t, 1) + 0.0, round(mny) + 0.0         # +0.0:避免顯示「-0.0」
                return f"<span class='{c}'{hl}>{lab}{t:+.1f}t</span>" if t else f"<span class='dim'>{lab}0t</span>"
            _agetxt = f" · 報價{_fv_snap['age']}秒前" if _fv_snap["age"] >= 10 else ""
            _lab = ("<b style='color:#f0883e' title='校正價(實驗)'>校</b> " if _trig else "")
            _sub = (f"<span style='font-size:9px'>{_fmt('買', _fv_snap['bid'], _HL_SELL if _fv_snap['sig_sell'] >= 3 else '')} / {_fmt('賣', _fv_snap['ask'], _HL_BUY if _fv_snap['sig_buy'] >= 3 else '')}"
                    + (f"<br>{_fmt('成', _futpx)}" if _futpx else "") + f"<span class='dim'>{_agetxt}</span></span>")
            _fvtd = (f"<td data-sort='{_fval}' title='{'Kalman 校正合理價(實驗·前向驗證中,2026-11-03 判決)'+('·台指已動≥4bps且報價≥10秒沒動' if _trig else '')}"
                     f"={_fval:.2f}(中價{_fv_snap['mid']:.2f};校正價{_fv_snap['fair']:.2f})。1 tick={_tk:g} 元,"
                     f"一口{_sz:g}股=每tick {_tk * _sz:,.0f} 元。買/賣/成=買一/賣一/最新成交偏離此價的tick數(乘每tick金額=每口元)。"
                     f"報價{_fv_snap['age']}秒前;台指自上次報價{_fv_snap['move']:+.1f}bps。灰色=偏離小於0.5tick。"
                     f"橘『校』=台指自上次報價動≥4bps且報價≥10秒沒動(校正量最有意義);其餘時間合理價與中價很接近。綠底=賣一比合理價低≥0.5tick(可買);紅底=買一比合理價高≥0.5tick(可賣),皆需持續≥3秒。'>"
                     f"{_lab}{_fval:.2f}<br>{_sub}</td>")
        else:
            _why = _fv_snap["why"] if _fv_snap else "無資料"
            _fvtd = f"<td class='dim' title='期貨中價不顯示:{_why}'>—</td>"
        # 期貨成交現價(緊鄰現價,方便目視比價差):顏色=對期貨自身昨結;小字=基差%(期貨/現股−1)
        if _futpx:
            _fchg = ((_futpx / _ffpc - 1) * 100) if _ffpc else None
            _fcls = _px_class(_futpx, _ffpc, _fchg) if _ffpc else ""
            _bsub = (f"<span class='dim' style='font-size:9px'> {_basis:+.2f}%</span>" if _basis is not None else "")
            _futpxtd = (f"<td class='frz {_fcls}' data-sort='{_basis if _basis is not None else ''}' "
                        f"title='個股期貨最新成交價{_futpx:g}(成交時間{_fp.get('t') or '—'};期貨昨結{_ffpc if _ffpc else '—'}"
                        f"{f';對昨結{_fchg:+.2f}%' if _fchg is not None else ''});小字=基差%=期貨中價÷現貨中價−1(中價=買一賣一平均),排序依基差'>"
                        f"{_futpx:g}{_mini and '<sup style=\"font-size:8px;color:#8b949e\">小</sup>'}{_bsub}</td>")
        else:
            _futpxtd = "<td class='frz dim'>—</td>"
        # 盤前試撮:08:30~09:00 無成交價時,試撮直接塞進現有欄位共用(價/對昨收/買簿/賣簿),不另立欄
        _tr = biglot_dashboard.PREOPEN.get(r["sid"]); _tpc = biglot_dashboard.PREV_CLOSE.get(r["sid"])
        # ⚠ 2026-10-05 jack 糾正(真bug,不是早盤資料本來就少):2026-10-03 加的「今天完全沒
        # 成交就退回昨收顯示現價」那個 PREV_CLOSE 回退(見上面 r["px"]/r["px_is_prev_close"]
        # 那段),原本設計是給假日/收盤後重啟這種「今天根本沒開盤」的情境用——但試撮期間
        # (08:30-09:00)同樣符合「ST.last_px 是 None」這個條件(逐筆成交的 isTrial=true
        # 記錄會被 _ingest_trade 直接跳過,不算進 last_px),於是 r["px"] 被這個回退值撐成
        # truthy,讓下面這個判斷式誤以為「已經有成交價」,直接搶在試撮分支前面把現價釘死在
        # 昨收、漲跌也跟著顯示假的 0.00%——試撮當下其實有試搓指示價可以顯示,是被這個回退
        # 邏輯擋住了,不是沒有資料。改用 r["px_is_prev_close"] 排除「這其實是回退值,不是
        # 真正的成交價」的情況,讓試撮分支搶得到。
        if r["px"] and not r["px_is_prev_close"]:       # 已有真實成交價:正常顯示
            _pxtd = f"<td class='frz {_qcls}' data-sort='{r['px']}'>{r['px']}</td>"
            _bidtd, _asktd = td(r["bid_min"], "min", False), td(r["ask_min"], "min", False)
        elif _tr and _tr.get("px") is not None:         # 盤前:借 價/對昨收/買賣簿 顯示試撮(標「試」上標)
            _tpx = _tr["px"]; _tg = ((_tpx / _tpc - 1) * 100) if _tpc else None
            _sup = "<sup style='font-size:8px;color:#8b949e'>試</sup>"
            _pxtd = f"<td class='frz {_px_class(_tpx, _tpc, _tg)}' data-sort='{_tpx}' title='盤前試撮價'>{_tpx:g}{_sup}</td>"
            if _tg is not None:
                _chgtd = (f"<td class='{'up' if _tg > 0 else ('dn' if _tg < 0 else '')}' data-sort='{_tg}' title='盤前試撮跳空%'>"
                          f"{_tg:+.2f}%{_sup}</td>")
            _bidtd = f"<td title='試撮買一'>{_tr.get('bid')}{_sup}</td>"
            _asktd = f"<td title='試撮賣一(撮合{_tr.get('size') or 0}張)'>{_tr.get('ask')}{_sup}</td>"
        elif r["px"]:                                    # 連試撮都沒有,但有昨收可以退回顯示(2026-10-03 設計)
            _pxtd = f"<td class='frz {_qcls}' data-sort='{r['px']}' title='今日尚無成交或試撮,顯示前一交易日收盤價'>{r['px']}</td>"
            _bidtd, _asktd = td(r["bid_min"], "min", False), td(r["ask_min"], "min", False)
        else:                                           # 連昨收都沒有(新股/資料缺失)
            _pxtd = "<td class='frz dim'>—</td>"
            _bidtd, _asktd = td(r["bid_min"], "min", False), td(r["ask_min"], "min", False)
        # 訊號合併欄:漲訊(紅)+跌訊(綠)+注記(黃)整成一格,不同顏色分辨方向。
        # 原本『章/跌訊/漲訊/旗標』四欄內容高度重複(連3買/同賣/破昨防線/勿追/深接/純機構
        # 都跨欄出現),bull/bear_txt 已把 stamp+flag 去重整合;只需補兩個未涵蓋的注記:
        # 章的『↓弱開』、旗標的『虛胖接刀(無效帶)』。
        _sig = []
        if r["bull_n"]:
            _sig.append(f"<span class='sigup'>{r['bull_txt']}</span>")
        if r["bear_n"]:
            _sig.append(f"<span class='sigdn'>{r['bear_txt']}</span>")
        _ex = []
        if "↓弱開" in r["stamp"]:
            _ex.append("↓弱開")
        if r.get("ex_txt"):
            _ex.append(r["ex_txt"])
        if _ex:
            _sig.append(f"<span class='warnv'>{' '.join(_ex)}</span>")
        _signum = r["bull_n"] + r["bear_n"]
        _sigtd = (f"<td style='text-align:left{';font-weight:700' if _signum >= 3 else ''}'>"
                  f"{' · '.join(_sig)}</td>" if _sig else "<td class='dim'>—</td>")
        # 權證多空 5分/30分兩欄:購/售 成交額(萬)+ 多方占比;紅=購>售 綠=售>購。描述性、未回測。
        _w = biglot_dashboard.WRT.get(r["sid"]) if isinstance(biglot_dashboard.WRT.get(r["sid"]), dict) else None
        if _w and (_w.get("n_call") or _w.get("n_put")):
            _wtip = (f"全日 購{(_w.get('call_day') or 0)/1e4:,.0f}萬/售{(_w.get('put_day') or 0)/1e4:,.0f}萬 · "
                     f"對映 購{_w.get('n_call', 0)}檔/售{_w.get('n_put', 0)}檔 · 更新{_w.get('t', '')}")
            _wrt5td = _wrt_td(_w.get("call_5") or 0.0, _w.get("put_5") or 0.0,
                              _w.get("bull_5"), _w.get("bear_5"), "權證5分 " + _wtip)
            _wrt30td = _wrt_td(_w.get("call_30") or 0.0, _w.get("put_30") or 0.0,
                               _w.get("bull_30"), _w.get("bear_30"), "權證30分 " + _wtip)
            _wrtdaytd = _wrt_td(_w.get("call_day") or 0.0, _w.get("put_day") or 0.0,
                                _w.get("bull_day"), _w.get("bear_day"), "權證全日 " + _wtip)
        else:
            _wrt5td = _wrt30td = _wrtdaytd = "<td class='dim'>—</td>"
        # 近兩日(2026-10-03 jack交辦):昨開盤累積到現在=昨全天(PREV2D,換日時載入一次)+今天即時累計。
        _p2d = biglot_dashboard.PREV2D.get(r["sid"])
        _tot_today = (biglot_dashboard.ST.day.get(r["sid"]) or {}).get("tot") or 0   # 今天原始累計(近兩日混算用,絕不回退)
        # 2026-10-03 jack 交辦(第二批):全日大戶/全日散戶/權證全日/大戶-散戶/大戶佔比/散戶
        # 背離這組「收盤後拿來決定隔夜部位」的欄位,在非交易日或收盤後該檔今天確實整天
        # 零成交時(ST.day 當天完全沒有這檔的紀錄,=r["bigday"] is None),回退顯示上一個
        # 已知交易日(PREV2D)的全日定格數字,用 _mark_eod_fallback 標註是哪一天的——跟
        # 現價的 PREV_CLOSE 回退同一個理由,總比一片空白有用,但不能悄悄假裝是今天的
        # 數字。盤中(_in_market() 為真)時即使這檔今天真的還沒成交也絕不回退,以免
        # 「尚未成交」被誤讀成「已經有今天的數據」。⚠ 近兩日欄位(下方 _wrt2dtd/c_big2d
        # 等)刻意不吃這層回退、一律用 `_tot_today`/`r.get("bigday")` 等今天的原始值——
        # 回退值本身就等於 PREV2D,混進「近兩日=昨全天+今天」的算式會把昨天重複加兩次。
        _eod_fallback = r.get("bigday") is None and not _in_market() and _p2d is not None
        if _eod_fallback:
            _disp_bigday, _disp_retday = _p2d.get("big"), _p2d.get("ret")
            _disp_tot = _p2d.get("tot") or 0
            _disp_bigsh = (_disp_bigday / _disp_tot * 100) if (_disp_bigday is not None and _disp_tot) else None
            _disp_smfi = _p2d.get("ret_smfi")
            _disp_ret_open_sh, _disp_ret_close_sh = _p2d.get("ret_open_sh"), _p2d.get("ret_close_sh")
            _eod_asof = _p2d.get("day", "")
            _pcall, _pput = _p2d.get("call_day") or 0.0, _p2d.get("put_day") or 0.0
            _pbull_d, _pbear_d = _p2d.get("bull_day"), _p2d.get("bear_day")
            if _pcall or _pput or _pbull_d or _pbear_d:
                _wrtdaytd = _mark_eod_fallback(
                    _wrt_td(_pcall, _pput, _pbull_d, _pbear_d,
                            f"權證全日(回退顯示{_eod_asof}) 購{_pcall/1e4:,.0f}萬/售{_pput/1e4:,.0f}萬"),
                    _eod_asof)
            else:
                _wrtdaytd = "<td class='dim'>—</td>"
        else:
            _disp_bigday, _disp_retday = r.get("bigday"), r.get("retday")
            _disp_tot = _tot_today
            _disp_bigsh, _disp_smfi = r.get("bigsh_d"), r.get("ret_smfi")
            _disp_ret_open_sh, _disp_ret_close_sh = r.get("ret_open_sh"), r.get("ret_close_sh")
            _eod_asof = None
        if _p2d is None:
            _wrt2dtd = "<td class='dim'>—</td>"
        else:
            _call2d, _put2d = (_p2d.get("call_day") or 0.0) + ((_w.get("call_day") if _w else 0.0) or 0.0), \
                               (_p2d.get("put_day") or 0.0) + ((_w.get("put_day") if _w else 0.0) or 0.0)
            _pbull, _pbear = _p2d.get("bull_day"), _p2d.get("bear_day")
            _wbull, _wbear = (_w.get("bull_day") if _w else None), (_w.get("bear_day") if _w else None)
            _bull2d = (_pbull or 0.0) + (_wbull or 0.0) if (_pbull is not None or _wbull is not None) else None
            _bear2d = (_pbear or 0.0) + (_wbear or 0.0) if (_pbear is not None or _wbear is not None) else None
            _wrt2dtd = _wrt_td(_call2d, _put2d, _bull2d, _bear2d,
                                f"權證近兩日(昨{_p2d.get('day','')}開盤起累積) 購{_call2d/1e4:,.0f}萬/售{_put2d/1e4:,.0f}萬")
        # ---- 各欄先算成具名字串,再依「同尺度 大戶→散戶→權證」順序組列(2026-09-24 重排) ----
        _offp = r["sid"] in biglot_dashboard.REF_EXTRA     # 池外參考列無個股頁/筆記(不在 NAMES)
        c_nm = (f"<td class='nm'>" + (f"<span>{name}</span>" if _offp else
                f"<a href='/stock?sid={r['sid']}' target='_blank' "
                f"title=\"{html_mod.escape(biglot_dashboard._stock_tip(r['sid']), quote=True).replace(chr(10), '&#10;')}\" "
                f"style='color:inherit;text-decoration:none'>{name}</a>")
                + f"<span class='cat'>{r['cat']}</span>"
                + _role_chip(r)
                + ("<span class='dim' style='font-size:9px' title='散戶不可測:1 張≥500 萬,散戶欄=不可測、散戶側訊號(虛拉/勿追/竭盡∧散戶接)關閉;大戶/真空/急跌/隔夜大戶佔比照常'> ✗散</span>" if r.get("unm") else "")
                + ("<span class='dim' style='font-size:9px' title='個股期貨為小型契約(100 股):期貨買/賣欄的量以小型口數計,1 口名目=價×100'> 小</span>" if r["sid"] in biglot_dashboard.FUT_MINI else "")
                + "</td>")
        c_vr = vr_td(r)
        c_open = td(r["day_ret"], "pct2")
        c_r30 = td(r["r30_r"] / 100 if r["r30_r"] is not None else None, "pct2")   # 統一用 %(2026-09-24)
        c_ctx = (f"<td class='{'up' if '逆強' in r['mkt_ctx'] or '順漲' in r['mkt_ctx'] else 'dn'}' "
                 f"style='font-size:11px'>{r['mkt_ctx']}</td>" if r.get("mkt_ctx") else "<td class='dim'>—</td>")
        c_big5, c_big30 = td(r["big5_r"], "wan"), td(r["big30_r"], "wan")
        c_bigday = td(_disp_bigday, "wan")
        if _eod_fallback and _disp_bigday is not None:
            c_bigday = _mark_eod_fallback(c_bigday, _eod_asof)
        elif not _eod_fallback:
            c_bigday = fut_merge_day_td(r, "big", _disp_bigday) or c_bigday
        c_w5 = td(r["w_ret_r"] / 100 if r["w_ret_r"] is not None else None, "pct2")
        c_ret5 = td(r["retn5_r"], "wan", unm=r["unm"])
        c_ret30 = td(r.get("retn30_r"), "wan", unm=r["unm"])
        c_retday = td(_disp_retday, "wan", unm=r["unm"])
        if _eod_fallback and _disp_retday is not None and not r["unm"]:
            c_retday = _mark_eod_fallback(c_retday, _eod_asof)
        elif not _eod_fallback:
            c_retday = fut_merge_day_td(r, "ret", _disp_retday, unm=r["unm"]) or c_retday
        # 2026-10-02 欄位重整(jack交辦):5分/30分/全日三尺度統一成「大戶→散戶→權證→大戶-散戶→大戶佔比」
        # 五欄並列;移除原本的散買/散賣參與%拆分跟散戶參與Δ(30分)——這些仍在算分數用,只是不再單獨開欄顯示。
        c_bigpct5 = td(_b5n(r), "pct2")
        c_bigpct30 = td(_b30n(r), "pct2")
        c_ret30pct = td(_r30n(r), "pct2")                    # 30分散戶佔比(代理人規則④;同代理人 ret30=散戶淨÷30分成交額)
        c_dhi30 = td(r.get("dhi30_r"), "pct2")               # 距近30分高%(代理人規則②:≤−0.5)
        c_amtp30 = (f"<td data-sort='{r['amtpct30']:.4f}'>{r['amtpct30']:.0f}</td>" if r.get("amtpct30") is not None
                    else "<td class='dim'>—</td>")           # 30分成交額百分位(代理人規則⑤:≥30)
        c_diff5 = _bigret_diff_td(r.get("big5_r"), r.get("retn5_r"), r.get("tot5_r"), r["unm"])
        c_diff30 = _bigret_diff_td(r.get("big30_r"), r.get("retn30_r"), r.get("tot30_r"), r["unm"])
        # 全日大戶−散戶(=大戶佔比−散戶佔比,%,含A/B/C/D判讀,2026-09-24定案)。
        # 2026-10-02 抽成 _bigret_diff_td 共用函式(html_fragments.py),5分/30分版本見上方 c_diff5/c_diff30。
        c_diff = _bigret_diff_td(_disp_bigday, _disp_retday, _disp_tot, r["unm"], full_stats=True)
        c_bigsh = (f"<td class='{'up' if _disp_bigsh > 0 else 'dn'}' data-sort='{_disp_bigsh}'>{_disp_bigsh:+.1f}%</td>"
                   if _disp_bigsh is not None else "<td class='dim'>—</td>")
        if _eod_fallback:
            c_diff = _mark_eod_fallback(c_diff, _eod_asof) if _disp_bigday is not None else c_diff
            c_bigsh = _mark_eod_fallback(c_bigsh, _eod_asof) if _disp_bigsh is not None else c_bigsh
        # 近兩日大戶/散戶/大戶-散戶/大戶佔比:昨全天(PREV2D)+ 今天截至目前(bigday/retday/_tot_today)。
        # ⚠ 一律用 r.get("bigday")/_tot_today(今天原始值),不可用上面的 _disp_*——後者在
        # _eod_fallback 時已經等於 PREV2D 本身,混進這裡的加總會把昨天重複算兩次。
        if _p2d is None:
            c_big2d = c_ret2d = c_diff2d = c_bigpct2d = "<td class='dim'>—</td>"
        else:
            _big2d_amt = (_p2d.get("big") or 0.0) + (r.get("bigday") or 0.0)
            _ret2d_amt = (_p2d.get("ret") or 0.0) + (r.get("retday") or 0.0)
            _tot2d = (_p2d.get("tot") or 0.0) + _tot_today
            c_big2d = td(_big2d_amt, "wan")
            c_ret2d = td(_ret2d_amt, "wan", unm=r["unm"])
            c_diff2d = _bigret_diff_td(_big2d_amt, _ret2d_amt, _tot2d, r["unm"])
            c_bigpct2d = td(_big2d_amt / _tot2d * 100 if _tot2d else None, "pct2")
        # 2026-10-04 jack 交辦:原本是收盤前25分鐘(尾盤12:55-13:20)才有值的隔夜因子,改成
        # 近30分滾動窗(跟5分/30分欄位同算法),從 SMFI_ACTIVE_FROM(11:45)起即時持續更新,
        # 之前一律—(47點滾動窗時段回測證實此前訊號不穩定,不是技術限制,見
        # biglot_dashboard.py::SMFI_ACTIVE_FROM)。EOD回退(_eod_fallback)顯示的是前一交易日
        # 收盤那一刻的定格值——對已結束的一天,滾動窗撐到收盤正好收斂成尾盤窗口的答案,
        # 兩種情境用同一個 _disp_ret_close_sh/_disp_smfi,只差 tooltip 怎麼描述「現在」這一側。
        _smfi_cur_desc = (f"前一交易日({_eod_asof})收盤定格值" if _eod_fallback
                          else f"近30分(≥{biglot_dashboard.SMFI_ACTIVE_FROM}起即時滾動更新)")
        c_smfi = (f"<td class='{'up' if _disp_smfi > 0 else ('dn' if _disp_smfi < 0 else '')}' data-sort='{_disp_smfi}' "
                 f"title='散戶背離(SMFI,2026-09-25已採納進隔夜分:≥+10pp 記+1):{_smfi_cur_desc}散戶淨額佔比 {_disp_ret_close_sh:+.1f}% − "
                 f"開盤(09:00-09:25) {_disp_ret_open_sh:+.1f}% = 背離 {_disp_smfi:+.1f}pp。127日面板(原尾盤版本):對次日跳空 OOS t+2.50(單變量t+3.07)、"
                 f"控大戶背離與既有隔夜四項後不衰減;IS 控制後邊緣未過(t+1.73)——方向反直覺:散戶偏買方反而預測次日偏多(像散戶跟隨法人已建方向)。"
                 f"2026-10-04改滾動窗後47點時段回測:{biglot_dashboard.SMFI_ACTIVE_FROM}起兩個標的連續站穩(OOS |t|>=2),之前雜訊不可信"
                 f"'>{_disp_smfi:+.1f}</td>" if _disp_smfi is not None else "<td class='dim'>—</td>")
        if _eod_fallback and _disp_smfi is not None:
            c_smfi = _mark_eod_fallback(c_smfi, _eod_asof)
        # 壓縮顯示規則(2026-09-24 jack 定案,多空對稱、不標字):
        #   多:全日大戶佔比 ≥+10% ∧ 壓縮 <0(價壓在近1h均價下)→ 黃粗體
        #   空:全日大戶佔比 ≤−10% ∧ 壓縮 >0(大戶倒完價仍在均價上)→ 黃粗體
        #   其餘淡化。127日參考:多格 +139/t2.9;空格 −28~−80(勿抱,非放空);基準 +73。
        if r["cmp1h"] is None:
            c_cmp = "<td class='dim'>—</td>"
        else:
            _cv = r["cmp1h"]; _bs = r.get("bigsh_d")
            if _bs is not None and _bs >= 10 and _cv < 0:
                c_cmp = (f"<td class='warnv' data-sort='{_cv}' style='font-weight:700' title='多:全日大戶佔比 {_bs:+.1f}% ≥+10 ∧ 壓縮 <0(價壓在近1h均價下)。"
                         f"127日隔夜 +139/t2.9(基準 +73)'>{_cv:+.2f}%</td>")
            elif _bs is not None and _bs <= -10 and _cv > 0:
                c_cmp = (f"<td class='warnv' data-sort='{_cv}' style='font-weight:700' title='空:全日大戶佔比 {_bs:+.1f}% ≤−10 ∧ 壓縮 >0(大戶倒完價仍在均價上)。"
                         f"127日隔夜 −28~−80(基準 +73);勿抱,非放空訊號'>{_cv:+.2f}%</td>")
            else:
                c_cmp = f"<td class='dim' data-sort='{_cv}' title='無方向:|大戶佔比|<10%,或方向與壓縮不對稱'>{_cv:+.2f}%</td>"
        if r.get("dtrend"):
            _dtr_ret5d = r["dtrend"].get("ret5d")
            # 排序鍵:5日動能%(較連續);缺值時退回 above_ma5 的 +1/−1,至少保留多空方向的排序意義
            _dtr_sort = _dtr_ret5d if _dtr_ret5d is not None else (1 if r["dtrend"]["above_ma5"] else -1)
            c_dtr = (f"<td class='up' data-sort='{_dtr_sort}' style='font-size:11px'>↑多"
                     + (f" {_dtr_ret5d:+.1f}%" if _dtr_ret5d is not None else "") + "</td>"
                     if r["dtrend"]["above_ma5"]
                     else f"<td class='dn' data-sort='{_dtr_sort}' style='font-size:11px'>↓空"
                     + (f" {_dtr_ret5d:+.1f}%" if _dtr_ret5d is not None else "") + "</td>")
        else:
            c_dtr = "<td class='dim'>—</td>"
        if r.get("bias20") is not None:
            _b20_bold = "font-weight:700" if r["bias20"] >= 30 else ""
            c_bias20 = (f"<td class='{'warnv' if r['bias20'] >= 30 else ('up' if r['bias20'] > 0 else 'dn')}' data-sort='{r['bias20']}' style='{_b20_bold}' "
                       f"title='20MA(月線)正乖離率=現價÷20日均價(PIT,不含今日)−1。≥30%=短線超買過熱、回檔壓力極高的經驗法則,純描述性警示,不進分數'>"
                       f"{r['bias20']:+.0f}%</td>")
        else:
            c_bias20 = "<td class='dim'>—</td>"
        _kl_state = r.get("key_line_state", "base")
        if _kl_state != "alive" or r.get("key_line_dist") is None:
            _why = {"base": f"掃描窗({_KL_LOOKBACK}個交易日)內完全沒有觸發棒",
                    "stale": f"最近一根觸發棒已是 {r.get('key_line_age')} 個交易日前(>{_KL_RECENT}日時效),線太舊不算數",
                    "broken": "觸發棒之後已有日收盤跌破線 → 線作廢,要等下一根觸發棒才會重新畫得出線",
                    }.get(_kl_state, "缺即時價,無法算距離")
            c_keyline = f"<td class='dim' title='畫不出線:{_why}。{_KL_TIP_TAIL}'>沒有</td>"
        else:
            _kd = r["key_line_dist"]
            _kcls = "up" if _kd > 0 else "dn"
            _kl_px, _kl_dt, _kl_age = r["key_line"], r["key_line_date"], r.get("key_line_age")
            c_keyline = (f"<td class='{_kcls}' data-sort='{_kd}' title='能畫線(alive):關鍵一條線={_kl_px:g}"
                        f"@{_kl_dt}(觸發棒最低點,{_kl_age} 個交易日前),收盤未曾跌破。"
                        f"距離={'昨收' if r.get('key_line_ref_prev') else '現價'}÷線−1={_kd:+.1f}%。"
                        f"{_KL_TIP_TAIL}'>{_kd:+.0f}%</td>")
        if r.get("pe_live") is None:
            c_pe = "<td class='dim' title='本益比:缺 TTM EPS 或即時價,無法計算(常見於11檔生產基本面表尚未同步的股票)'>—</td>"
        else:
            _pev, _pct, _pn, _prk = r["pe_live"], r.get("pe_pctile"), r.get("pe_n"), r.get("pe_rank")
            _pcls = "up" if (_pct is not None and _pct <= 20) else ("dn" if (_pct is not None and _pct >= 80) else "")
            _pasof, _pgrp = (r.get("pe_eps_asof") or "—"), (r.get("pe_group") or "—")
            _psub = f"{_pct:.0f}%" if _pct is not None else "單檔"
            _prk_txt = _prk if _prk is not None else "—"
            _pe_sort = _pct if _pct is not None else _pev   # 單檔族群無百分位,退回本益比絕對值排序
            c_pe = (f"<td class='{_pcls}' data-sort='{_pe_sort}' title='本益比=現價(即時)÷TTM近四季EPS(至{_pasof};⚠非分析師預估EPS,落後指標,見表頭說明)。"
                    f"同族群『{_pgrp}』{_pn or 0}檔中排第{_prk_txt}低(百分位{_psub},≤20%=族群內相對便宜·≥80%=族群內相對昂貴)。"
                    f"族群完整成員清單+各自本益比見個股詳情頁。僅供參考位置,未經嚴謹回測,不進分數'>{_pev:.1f}<span class=\"sub\">{_psub}</span></td>")
        _e981 = biglot_dashboard.ETF981_HOLD.get(r["sid"])
        _e981_asof_txt = biglot_dashboard.ETF981_ASOF or "—"
        _e981_prev_txt = biglot_dashboard.ETF981_PREV_ASOF or "—"
        if _e981 is None:
            c_etf981 = (f"<td class='dim' title='00981A(統一台股增長)最近兩次快照({_e981_asof_txt}"
                        f" / {_e981_prev_txt})皆未持有此股。純展示欄,不進分數'>—</td>")
        else:
            _eamt, _edelta = _e981["amount"], _e981["delta"]
            _ecls = "up" if _edelta > 0 else ("dn" if _edelta < 0 else "")
            _eamt_e, _edelta_e = _eamt / 1e8, _edelta / 1e8
            c_etf981 = (f"<td class='{_ecls}' data-sort='{_eamt}' title='00981A(統一台股增長)持股市值(ezmoney快照{_e981_asof_txt},"
                        f"股數×當時收盤價,非即時);Δ=(股數變動)×最新收盤價 vs前次快照({_e981_prev_txt}),只含買賣、不含漲跌價差,未扣贖回造成的等比例縮減;"
                        f"正=加碼/新進、負=減碼/出清。純展示欄,不進分數;跟單訊號另見 00981a-l1h9 daily brief'>"
                        f"{_eamt_e:.2f}億<span class=\"sub\">{_edelta_e:+.2f}億</span></td>")
        # 集保戶股權分散表·大戶≥800張比例(2026-09-29 jack 交辦):週頻(TDCC每週五公告),純參考展示。
        # ⚠已知不可交易——文獻查證此資料源零同儕審查支持;本系統自己用同一份資料做的HS因子
        # (散戶持股比)通過五項對抗檢定但控週轉率後淨值由+5.44%/年轉−0.14%~−3.33%/年,結論是低週轉
        # 流動性溢酬代理非真籌碼alpha(詳見 biglot/reference_loaders.py::_load_holder_big800 docstring)。
        _hb800v, _hb800d = r.get("holder_big800"), r.get("holder_big800_asof")
        _hb800delta, _hb800pasof = r.get("holder_big800_delta"), r.get("holder_big800_prev_asof")
        if _hb800v is None:
            c_holder800 = "<td class='dim' title='集保戶股權分散表無此股資料(或level_lo缺值)'>—</td>"
        else:
            _hb800dtxt = (f"{_hb800delta:+.1f}pp(較{_hb800pasof})" if _hb800delta is not None else "無上週資料可比")
            c_holder800 = (f"<td data-sort='{_hb800v}' title='集保戶股權分散表(as_of {_hb800d};上一筆快照{_hb800pasof or '—'}):"
                            f"大戶(持股≥800張)合計占比{_hb800v:.1f}%,較上週{_hb800dtxt}。週頻,非即時,跟本表其他盤中欄位不同尺度。"
                            f"⚠已知不可交易:文獻對此資料源零同儕審查支持,本系統自己用同一份資料做的HS因子(散戶持股比)"
                            f"通過五項對抗檢定但控週轉率後淨值轉負(+5.44%→−0.14%~−3.33%/年)——結論是低週轉流動性溢酬代理,"
                            f"非真籌碼alpha。純參考顯示,不進分數、不影響排序'>{_hb800v:.1f}%"
                            f"<span class='dim' style='font-size:9px'> {_hb800d}"
                            f"{(' ' + ('+' if _hb800delta >= 0 else '') + f'{_hb800delta:.1f}pp') if _hb800delta is not None else ''}</span></td>")
        _ib_tip = ("隱形大戶守價位(2026-09-25 依 Frey & Sandås (2009) CFR Working Paper No. 09-06 演算法重建,"
                   "取代第一版寬鬆定義)。方法:追蹤五檔全部價位(非僅最優價),量耗盡到接近零(≤原量15%)"
                   "且交叉比對逐筆真實成交確認打在該價位,第一次補回=偵測到(原文:detected after the first "
                   "replenishment,keeps state even if undercut until an expected replenishment has not occurred)。"
                   "⚠2026-09-25用14個交易日重跑(scripts/research/iceberg_frey_sandas_rebuild.py):靠山(backing,"
                   "目前最優價剛好是已偵測價位)兩側仍是雜訊(|t|<1.5,安慰劑範圍內),沒有復現原文Table V的顯著"
                   "效果,可能是TWSE五檔快照解析度不夠;跌破支撐(breakout_bear)延遲30秒後t從+0.73掉到-0.01,"
                   "確認雜訊。**突破壓力(breakout_bull)是唯一通過檢定的**:即時t-2.90、延遲30秒t-2.24、換算"
                   "Table V原文口徑(後30筆真實成交)t-2.93,集中度前5檔55%(不極端),安慰劑真實值(-10.2)落在"
                   "隨機5組範圍(-3.2~-1.6)之外——方向是『突破後回落』(fade),不是延續噴出。已用0.5倍縮水"
                   "(僅14日,比127日基準更保守)、30分鐘線性淡出納入淨分,近30分內顯示。")
        if r.get("iceberg_recent_breakout"):
            _rb = r["iceberg_recent_breakout"]
            _rb_min = (time.time() - _rb["ts"]) / 60
            if _rb["kind"] == "breakout_bull":
                c_iceberg = (f"<td class='dn' style='font-weight:700' title='{_ib_tip}'>"
                            f"突破壓力{_rb['price']:g} 防回落 {_rb_min:.0f}′</td>")
            else:
                c_iceberg = (f"<td class='dim' title='{_ib_tip}'>跌破支撐{_rb['price']:g}(NULL,僅顯示){_rb_min:.0f}′</td>")
        elif r.get("iceberg_backing_bid") or r.get("iceberg_backing_ask"):
            _side = "買一" if r.get("iceberg_backing_bid") else "賣一"
            c_iceberg = f"<td class='dim' title='{_ib_tip}'>靠{_side}(NULL,僅顯示)</td>"
        else:
            c_iceberg = f"<td class='dim' title='{_ib_tip}'>—</td>"
        c_rs = (f"<td class='{'dn' if r['rs_live'] < 0 else ('warnv' if r['rs_live'] > 1 else '')}' data-sort='{r['rs_live']}'>"
                f"{r['rs_live']:+.1f}</td>" if r.get("rs_live") is not None else "<td class='dim'>—</td>")
        c_rvol = (f"<td class='{'wall' if (r['rvol5'] or 0) >= 2 else ('dim' if (r['rvol5'] or 0) < 0.5 else '')}' data-sort='{r['rvol5']}'>"
                  f"{r['rvol5']:.1f}x</td>" if r["rvol5"] is not None else "<td class='dim'>—</td>")
        c_rvd = (f"<td class='{'wall' if r['rvol_day'] >= 1.5 else ('dim' if r['rvol_day'] < 0.7 else '')}'>{r['rvol_day']:.2f}x</td>"
                 if r.get("rvol_day") is not None else "<td class='dim'>—</td>")
        # 今日振幅倍數 = (今高−今低)/昨收% ÷ 20日均振幅%;波動聚集只預測振幅不預測方向 → 不進淨分,當倍率/風控提示
        _pc = biglot_dashboard.PREV_CLOSE.get(r["sid"]); _ds = biglot_dashboard.ST.day.get(r["sid"]) or {}
        if _pc and _ds.get("hi") and _ds.get("lo") and r.get("amp20"):
            _a20 = r["amp20"]
            _ampt = (_ds["hi"] - _ds["lo"]) / _pc * 100; _ar = _ampt / _a20 if _a20 > 0 else None
            r["amp_ratio"] = _ar
            if _ar is None:
                c_ampr = "<td class='dim'>—</td>"
            else:
                _acls = 'warnv' if _ar >= 1.5 else ('dim' if _ar < 0.7 else '')
                _abold = 'font-weight:700' if _ar >= 1.5 else ''
                c_ampr = (f"<td class='{_acls}' data-sort='{_ar}' style='{_abold}' title='今日振幅 {_ampt:.2f}% ÷ 20日均振幅 {_a20:.2f}% = {_ar:.2f}x。"
                          f"波動聚集:只預測明日振幅(真),方向 IC≈0 → 不進淨分;≥1.5x = 高波動日,淨分同分對應更大 bps、砍尾閾值可放寬'>{_ar:.2f}x</td>")
        else:
            r["amp_ratio"] = None
            c_ampr = "<td class='dim'>—</td>"
        # ATR 盤整壓縮/突破(2026-09-25 jack 交辦,見 _load_atr_state docstring):今日真實區間用即時高低現算,
        # 對比昨收已知的 ATR14,避免未來函數。異常事件本身已嚴謹回測 DROP,純描述性狀態,不進分數。
        _atrs = biglot_dashboard.ATR_STATE.get(r["sid"])
        if _atrs and _pc and _ds.get("hi") and _ds.get("lo"):
            _tr_today = max(_ds["hi"] - _ds["lo"], abs(_ds["hi"] - _pc), abs(_ds["lo"] - _pc))
            r["atr_pct"] = _atrs["atr_pct"]
            r["atr_pctile"] = _atrs.get("pctl")   # 排序鍵:壓縮%在自身120日歷史中的分位(0=最壓縮,1=最擴張,2026-09-29)
            r["atr_squeeze"] = _atrs["squeeze"]
            r["atr_ratio"] = (_tr_today / _atrs["atr14"]) if _atrs["atr14"] else None
            r["atr_abnormal"] = bool(r["atr_squeeze"] and r["atr_ratio"] is not None and r["atr_ratio"] > biglot_dashboard.ATR_BREAKOUT_K)
            r["atr_dir_up"] = (r.get("px") is not None and r["px"] > _pc)
        else:
            r["atr_pct"] = r["atr_ratio"] = r["atr_pctile"] = None
            r["atr_squeeze"] = r["atr_abnormal"] = False
            r["atr_dir_up"] = None
        r["atr_near_line"] = bool(_atrs and _atrs.get("atr14") and r.get("key_line") and r.get("px")
                                   and abs(r["px"] - r["key_line"]) <= _atrs["atr14"])
        _atr_tip_base = ("ATR(平均真實區間,Wilder 1978,14期)盤整壓縮/突破狀態(2026-09-25 jack 交辦,"
                          "來源:《御錢術》楊育華分析師節目)。壓縮=近120交易日ATR%(=ATR14÷收盤)落在自身歷史後30%分位"
                          "(自身相對壓縮,非跨股比較)。異常=壓縮狀態下,今日真實區間(即時高低現算)超過昨收已知ATR14的1.5倍"
                          "(節目原話:「超過1.5倍,方向改變了,要立刻出場」)。"
                          "⚠2026-09-25嚴謹回測(scripts/research/atr_key_line_research.py,21年史·IS/OOS拆2023·日聚類·扣籃子·"
                          "扣成本·安慰劑·集中度·逐年,僅限42檔):此突破事件本身DROP——10/40/60日IS/OOS異號、"
                          "安慰劑5組範圍蓋過真實均值(與隨機日不可區分)、前5檔佔比354%(逐年正負交替無穩定方向),不進分數。"
                          "唯一IS/OOS同號的子集是『恰好貼近關鍵一條線±1倍ATR內』(標★近線,IS t+1.66/OOS t+1.80),"
                          "但仍未過本案嚴格門檻(|t_OOS|≥2),僅供觀察、同樣不進分數。"
                          "⚠2026-09-29 起關鍵一條線改二分口徑(20日時效∧跌破作廢),★近線只會出現在『能畫線』的股票上,覆蓋比原★近線回測(舊口徑:線永不作廢)窄很多,原 IS t+1.66/OOS t+1.80 不能直接套到新口徑。")
        if r.get("atr_pct") is None:
            c_atr = f"<td class='dim' title='{_atr_tip_base}(此股資料不足140個交易日,無法計算)'>—</td>"
        else:
            _near = " ★近線" if r["atr_near_line"] else ""
            _atr_sort = r.get("atr_pctile")
            if r["atr_abnormal"]:
                _dcls = "up" if r["atr_dir_up"] else "dn"
                _dlbl = "突破↑" if r["atr_dir_up"] else "突破↓"
                c_atr = (f"<td class='{_dcls}' data-sort='{_atr_sort}' style='font-weight:700' title='{_atr_tip_base}'>"
                         f"{_dlbl}{_near} {r['atr_ratio']:.1f}x</td>")
            elif r["atr_squeeze"]:
                c_atr = f"<td class='warnv' data-sort='{_atr_sort}' title='{_atr_tip_base}'>壓縮 {r['atr_pct']:.1f}%</td>"
            else:
                c_atr = f"<td class='dim' data-sort='{_atr_sort}' title='{_atr_tip_base}'>{r['atr_pct']:.1f}%</td>"
        trs.append(
            f"<tr{_band} data-sid='{r['sid']}'{'' if r['sid'] in biglot_dashboard.NAMES else ' data-offpool=1'}>" + c_nm
            + _pxtd + _futpxtd + _fbatd + _fvtd + _chgtd + c_open + c_w5 + c_r30 + c_ctx   # ① 價(期貨買賣緊接現價;委買簿厚度 2026-10-05 已移除,覆蓋率太窄)
            + c_big5 + c_ret5 + _wrt5td + c_diff5 + c_bigpct5           # ② 5分:大戶→散戶→權證→大戶-散戶→大戶佔比
            + c_big30 + c_ret30 + _wrt30td + c_diff30 + c_bigpct30 + c_ret30pct + c_dhi30 + c_amtp30   # ③ 30分(同五欄;期貨散戶30分 2026-10-05 已移除,覆蓋僅4檔且觀察期未滿即判定無用)
            + c_bigday + c_retday + _wrtdaytd + c_diff + c_bigsh   # ④ 全日(同五欄;散戶背離 SMFI 2026-10-05 搬到⑤b訊號輸入區)
            + c_big2d + c_ret2d + _wrt2dtd + c_diff2d + c_bigpct2d          # ④b 近兩日(同五欄,2026-10-03新增)
            + c_dtr + c_bias20 + c_keyline + c_atr + c_pe + c_etf981 + c_holder800   # ⑤ 結構/隔夜事實欄(日線趨勢、20MA乖離、關鍵一條線、ATR盤整、本益比同族群、00981A持股、集保大戶800張;2026-10-03「振幅%(20日已實現)」移到個股詳情頁,主表不再顯示——這是選股門檻用的穩定統計量,不是盤中訊號,每秒重繪佔格子沒有對應資訊價值)
            + c_smfi + c_cmp + c_iceberg + _xg_cols.cell_author_streak(r['sid']) + _xg_cols.cell_author_short(r['sid'], r) + _xg_cols.cell_stats(r['sid'], r) + c_rs + c_rvol + c_rvd + c_vr + c_ampr   # ⑤b 訊號輸入(2026-10-05 jack 交辦,從事實欄搬出:這些欄位本身是對原始數據的判斷、其中散戶背離/壓縮/冰山委託(僅breakout_bull部分)/相對強弱/全日量能已進淨分公式,量能倍數/波動分/今日振幅未進淨分但是風險分級/情境注記——都不是忠實呈現數據,緊接在訊號/淨分之前當輸入區;現貨+期貨大戶加權合併欄2026-10-06新增,純觀察不進分)
            + _comovement_td(r["sid"]) + _story_td(r["sid"], _stories) + _sigtd + _score_td(r) + ("<td class='dim'>—</td>" if r["sid"] in biglot_dashboard.REF_EXTRA else _stock_note_td(r["sid"]))         # ⑥ 股價共動·訊號·淨分·筆記(最末)
            + "</tr>")

    try:
        _shadow_triple(rows, now)                    # 權證三條件影子帳(失敗不影響畫面)
    except Exception as _e:
        print(f"[shadow] {_e!r}", file=sys.stderr)
    upd_note = (f"每{biglot_dashboard.REFRESH_SEC}s自動更新(不重載)" if in_mkt
                else "盤後定格,已停止更新")
    try:
        _txp = _tx_panel(now)
    except Exception as _e:  # noqa: BLE001
        _txp = ""
        print(f"[tx_panel] {_e!r}", file=sys.stderr)
    try:
        _pnlp = _pnl_panel(now)
    except Exception as _e:  # noqa: BLE001
        _pnlp = ""
        print(f"[pnl_panel] {_e!r}", file=sys.stderr)
    biglot_dashboard.PAGE["rows"] = [r for r in rows_all if r["sid"] in biglot_dashboard.NAMES]   # 迷你圖格/36檔加總維持原42檔範圍
    biglot_dashboard.PAGE["in_mkt"] = in_mkt
    biglot_dashboard.PAGE["frag"] = f"""<div id="closed" data-closed="{0 if in_mkt else 1}" hidden></div>
{_pnlp}{_txp}{stale_bar}
<div class="meta">更新 {now.strftime('%H:%M:%S')} · 5分窗 {win_lbl} · 30分窗 {w30_lbl} ·
市場代理 5分 <b>{mkt5:+.1f}bps</b> / 30分 <b>{mkt30:+.1f}bps</b> · <span style='color:#d2a8ff' title='紙上交易(不送單):bucket=5分桶邊界取樣(回測口徑)、sec=每秒首次穿越;進 V2.5≥15 掛買一30秒,出 分數≤-5·30秒/壞標籤/60分 掛賣一60秒否則買一;嚴=價穿越才算成交、樂=觸價即成交;成本22bps;帳本 paper_trades_{{日}}.jsonl / paper_daily.json'>紙上 {_paper_summary()}</span> ·
紅=正/買 綠=負/賣 · <b>淨額單位一律=萬</b>(5分/30分/全日/近兩日/權證) · <b>5分/30分欄=每秒滾動窗</b>(往回300s/1800s);訊號欄標籤仍依完成的5分桶判定(=回測定義) ·簿深≥10分=牆(紫) <3分=真空(灰) ·
<b>大戶=≥1000萬</b>(127日:隔夜IC+0.13/接刀+12.7/勿追賣−9.6皆過檢) · <b>主尺度=30分</b>(旗標依127日驗證:
深接30 +7bps/t5.1;勿追5m +9bps/t13.7,2026-10-08 以144日面板重驗) · 5分組=執行細節 · {upd_note}</div>
<div class="flagbar" hidden>{gate_txt}<span style='color:#a5d6ff'>OOS: {_oos_summary()}</span> · {cand_txt}{flag_bar}</div>
<table{" data-live=1" if in_mkt else ""}><thead><tr>
<th class="stk">股票<span class="sub">點名稱看詳情</span></th>
<th class="frz" title="現價,顏色為對前一交易日收盤:紅漲綠跌(台股慣例)。盤前08:30~09:00 無成交時,此欄顯示『試撮價』(帶『試』上標),09:00開盤後轉為成交價。已凍結(隨股票欄一起固定,橫向捲動時不動)">現價</th>
<th class="frz" title="個股期貨最新成交價,緊鄰現價方便目視比價差。顏色=對期貨自身昨結(紅漲綠跌、漲跌停紅/綠底);小字=基差%(期貨中價÷現貨中價−1,中價=買一賣一平均;與個股頁副圖、/grid 同一定義),排序依基差。『小』=小型契約(100股)。資料源:個股期貨ws(futprice,每10秒;成交時間見滑鼠提示)。08:45起即有值(現貨09:00才開盤)。已凍結">期貨價<span class="sub">成交·基差%</span></th>
<th class="frz" title="個股期貨買一/賣一:委託價×委託量(小字)。紅=買一、綠=賣一;黃底線=最新成交落在該側(主動方);排序依買賣價差。資料源:個股期貨ws books channel(斷線逾30s剔除不顯示凍結價)。已凍結">期貨買/賣<span class="sub">買一×量 / 賣一×量</span></th>
<th title="個股期貨中價／校正合理價。Kalman 校正合理價(Hasbrouck式狀態空間+台指β+現股,參數已凍結),一直顯示;橘『校』=台指已動≥4bps且報價≥10秒沒更新。綠底/紅底=賣一低於/買一高於合理價≥0.5tick且持續≥3秒。小字=買一/賣一/成交各自偏離該價的tick數,<0.5tick灰色;每tick金額見滑鼠提示。⚠ 校正價前向驗證中(預註冊 docs/futures-fairvalue-prereg-20261020.md,2026-11-03 判決,未通過會移除)。台指/買賣簿/現股皆即時逐筆。">合理價<span class="sub">Kalman·偏離tick</span></th>
<th title="對前一交易日收盤的漲跌金額與%(專業看盤主報價)。盤前08:30~09:00 無成交時,此欄顯示『試撮跳空%』(帶『試』上標)">漲跌<span class="sub">對昨收</span></th>
<th title="現價/今日開盤−1(盤中相對開盤走勢,與對昨收互補)">對開盤%</th>
<th class="g5" title="近5分鐘價格報酬,單位bps。最短尺度、雜訊最大。">近5分漲跌<span class="sub">%</span></th>
<th class="g30" title="近30分鐘價格報酬,單位bps(1bps=0.01%)。主尺度。每秒滾動(現價 vs 1800秒前成交價);訊號標籤用完成5分桶版">近30分漲跌<span class="sub">%·滾動</span></th>
<th class="g30" data-nosort title="相對大盤:個股30分方向vs市場30分方向(描述性脈絡,非訊號):順漲/順跌=同向,逆強=市場跌它漲,逆弱=市場漲它跌。市場是個股報酬最強控制變數,讀任何訊號前先看這格。門檻:個股|30分|≥20bps∧市場≥5bps才標。">相對大盤<span class="sub">同步/背離</span></th>
<th class="g5" title="近5分大戶淨額(萬),每秒滾動(往回300秒)。大戶=單筆成交≥1000萬,按主動方向計正負。訊號標籤用完成5分桶版。">5分大戶<span class="sub">淨額·萬·滾動</span></th>
<th class="g5" title="5分窗散戶淨額(萬)。散戶=1張且<500萬。">5分散戶<span class="sub">淨額·萬</span></th>
<th class="g5" title="權證5分:該標的底下全部權證近5分。數字=認購/認售 成交額(活動量,萬);小字=簽號後『多方占比』=(主動買認購+主動賣認售)÷全部主動額;判斷與顏色同一規則:占比≥60%=偏多(紅)、≤40%=偏空(綠)、其間=中性(灰)。主動方以每筆成交價對當下買一/賣一判定(富邦 ws 逐筆)。名單=前一日成交額前600檔活躍權證。⚠描述性、尚未回測">權證5分<span class="sub">購/售·萬 (多方%) 判斷</span></th>
<th class="g5" title="(5分大戶淨 − 5分散戶淨)÷5分成交額=5分大戶佔比−5分散戶佔比(%),A/B/C/D分類同全日版(見全日大戶−散戶欄)。⚠此短窗版本尚未分別做過127日回測,只借用全日已驗證的分類門檻(±10%/±15%)跟分類邏輯,具體bps數字不適用、不附上——僅供描述性參考,不進分數">5分大戶−散戶<span class="sub">÷成交% · A/B/C/D</span></th>
<th class="g5" title="5分大戶淨÷5分成交額(%)。同全日大戶佔比公式,改用5分窗分母。純描述性參考,不進分數(5分窗訊號標籤仍用完成桶版的原有欄位判定)">5分大戶佔比<span class="sub">÷成交%</span></th>
<th class="g30" title="近30分大戶淨額(萬),每秒滾動(往回1800秒)。大戶=單筆≥1000萬。主尺度;訊號標籤用完成5分桶版。">30分大戶<span class="sub">淨額·萬·滾動</span></th>
<th class="g30" title="30分窗散戶淨額(萬),每秒滾動(往回1800秒)。散戶=1張且<500萬。">30分散戶<span class="sub">淨額·萬·滾動</span></th>
<th class="g30" title="權證30分:近30分 認購/認售 成交額(萬),小字=簽號後多方占比,判斷:≥60%偏多(紅)/≤40%偏空(綠)/其間中性(灰)。主尺度。資料源富邦 ws 逐筆(2連線×300檔)。⚠描述性、尚未回測,不是訊號;『權證做多』看占比與判斷,不看購/售活動量">權證30分<span class="sub">購/售·萬 (多方%) 判斷</span></th>
<th class="g30" title="(30分大戶淨 − 30分散戶淨)÷30分成交額=30分大戶佔比−30分散戶佔比(%),A/B/C/D分類同全日版。⚠此短窗版本尚未分別做過127日回測,只借用全日已驗證的分類門檻跟邏輯,具體bps數字不適用、不附上——僅供描述性參考,不進分數">30分大戶−散戶<span class="sub">÷成交% · A/B/C/D</span></th>
<th class="g30" title="30分大戶淨÷30分成交額(%)。主尺度;同全日大戶佔比公式,改用30分窗分母。純描述性參考(30分窗訊號標籤仍用完成桶版的原有欄位判定)">30分大戶佔比<span class="sub">÷成交%</span></th>
<th class="g30" title="30分散戶淨÷30分成交額(%),跟 30分大戶佔比同一分母。代理人規則④:全池由小到大排名,留小的 70%(散戶淨買越多越被淘汰)。">30分散戶佔比<span class="sub">÷成交%</span></th>
<th class="g30" title="(現價 ÷ 近30分最高成交價 − 1)%,每秒滾動。代理人規則②:≤ −0.5% 才算回檔(不追高)。">距30分高%<span class="sub">對近30分最高價</span></th>
<th class="g30" title="近30分成交額在全池(42 檔)的百分位 = 100×(成交額嚴格小於本檔的檔數)÷母體。代理人規則⑤:≥30(過濾最冷清的 30%)。">30分成交額百分位<span class="sub">全池 0–100</span></th>
<th class="gd" title="全日累計大戶淨額(萬)=盤中一路累加,收盤即全日淨額;最重要,÷成交=佔比%(隔夜排序主鍵IC+0.097/t7.1)。三尺度並排看背離:短窗買∧全日仍賣=誘多。帶「期」上標=已併入個股期貨大戶淨額(×10權重,08:45起;08:45–09:00 期貨門檻≥10口,之後≥1000萬)。純觀察展示,不進分數">全日大戶<span class="sub">淨額·萬</span></th>
<th class="gd" title="全日累計散戶淨額(萬)。散戶=現貨1張且<500萬。帶「期」上標=已併入個股期貨1口淨額(×8權重,08:45起;權重證據弱:相關0.37)。純觀察展示,不進分數">全日散戶<span class="sub">淨額·萬</span></th>
<th class="gd" title="權證全日:當日累計 認購/認售 成交額(萬),小字=簽號後多方占比,判斷:≥60%偏多(紅)/≤40%偏空(綠)/其間中性(灰)。格式同5分/30分權證,改用全天累計。⚠描述性、尚未回測,不是訊號">權證全日<span class="sub">購/售·萬 (多方%) 判斷</span></th>
<th class="gd" title="(全日大戶淨 − 全日散戶淨)÷ 全日成交額 = 大戶佔比 − 散戶佔比(%)。搭配大戶佔比讀:A 佔比≥+10%∧散戶<5% = 大戶買散戶未主導(紅;127日隔夜+124,散戶0~5%最佳+185/t4.5) · B 佔比≥+10%∧散戶≥5% = 大戶帶散戶(黃,+75/t1.2) · C 佔比≤−10%∧散戶≥5% = 大戶倒散戶接刀(綠,−11) · D 佔比≤−10% = 大戶賣散戶不接(綠,−13;全體基準+73)。門檻:佔比±10%≈p12/p85,差±15%≈p10/p90(粗體)。127日檢定:此欄控大戶佔比後無獨立方向資訊,排序仍用大戶佔比。">大戶−散戶<span class="sub">÷成交% · A/B/C/D</span></th>
<th class="gd" title="當日大戶淨流÷成交金額=隔夜排序主鍵(IC+0.097/t7.1)">大戶佔比<span class="sub">÷成交%</span></th>
<th class="gd" title="近兩日累計大戶淨額(萬)=昨開盤累積到現在:昨天全天(重放昨日raw封存)+今天截至目前。2026-10-03新增,純描述性,未回測,不進分數。">近兩日大戶<span class="sub">淨額·萬</span></th>
<th class="gd" title="近兩日累計散戶淨額(萬)。散戶=1張且<500萬。2026-10-03新增,純描述性,未回測,不進分數。">近兩日散戶<span class="sub">淨額·萬</span></th>
<th class="gd" title="權證近兩日:昨天收盤最後一次快照(warrantflow_昨日.json)+今天截至目前累計 認購/認售 成交額(萬),小字=簽號後多方占比,判斷同5分/30分/全日版。2026-10-03新增,描述性、尚未回測,不是訊號。">近兩日權證<span class="sub">購/售·萬 (多方%) 判斷</span></th>
<th class="gd" title="(近兩日大戶淨−近兩日散戶淨)÷近兩日成交額=近兩日大戶佔比−近兩日散戶佔比(%),A/B/C/D分類同全日版。2026-10-03新增,此尺度未分別做過127日回測,具體bps數字不適用、不附上——僅供描述性參考,不進分數。">近兩日大戶−散戶<span class="sub">÷成交% · A/B/C/D</span></th>
<th class="gd" title="近兩日大戶淨÷近兩日成交額(%)。同全日大戶佔比公式,改用近兩日(昨開盤起)分母。2026-10-03新增,純描述性參考,不進分數。">近兩日大戶佔比<span class="sub">÷成交%</span></th>
<th title="日線趨勢(截至最近日收盤):↑多=最新收盤站上5日均線,↓空=跌破;附5日動能%。回測:壓縮∧站上5日線隔夜+93.8bps/t5.10 vs 跌破+30/t1.65(差+63.5)——壓縮回檔在日線多頭股才是買點、空頭股是接刀。短線(壓縮/即時RS)×日線(此欄)分層,並行OOS影子帳驗證中,暫不改選股規則">日線趨勢</th>
<th title="20MA(月線)正乖離率 = 現價 ÷ 20日均價(PIT,用昨收含之前20日收盤,不含今日)− 1。2026-09-25 jack 交辦:取代『距離當天漲停%』——乖離率抓的是相對過去一個月成本的超買程度,不受個股漲跌停%上限差異影響。≥+30% 粗體黃字=短線漲幅過熱、超買回檔壓力極高的經驗法則;純描述性警示,不進分數、不做嚴謹回測。">20MA乖離<span class="sub">正乖離%</span></th>
<th title="「關鍵一條線」(2026-09-25 jack 交辦,來源:YouTube《御錢術》楊育華分析師;2026-09-29 jack 定案改二分口徑)。觸發棒=某日K棒同時 紅K(收盤>開盤)∧收盤漲幅>前一日收盤+4%∧收盤突破前60個交易日最高收盤;線=該棒最低點(含影線)。**只有「畫得出線」才顯示距離%**,其餘一律「沒有」:①掃描窗內無觸發棒 ②觸發棒已是20個交易日以前(線太舊) ③觸發棒後任一日收盤跌破線(作廢,要等下一根觸發棒)。作廢只看日收盤,盤中價跌到線下只會讓距離%變負、線還在。距離=現價÷線−1。⚠2026-09-29 嚴謹回測(scripts/research/key_line_daily_rigorous.py F段,scratch/key_line_daily_rigorous_2026-09-29.txt,21年史·IS/OOS拆2023·日聚類·扣42檔等權籃子):新定義(20日時效∧跌破作廢)的「能畫線」狀態對未來相對報酬 IS/OOS 同號且三個持有期全過——10日 IS+29.4(t+3.95)/OOS+146.6(t+7.57)、20日 IS+56.2(t+5.07)/OOS+281.0(t+8.79)、60日 IS+227.6(t+9.70)/OOS+310.7(t+4.28),能畫線只佔全樣本股-日 12.5%。舊定義(掃500日∧不作廢)同腳本重跑 60日 OOS 只剩+55.0(t+0.37)=熄火(覆蓋94%,旗標幾乎恆為1)。四態迴歸(base為基準,20日OOS):線太舊−87.6(t−6.55)、已破線−58.3(t−2.90)、能畫線+199.3(t+8.67)——「太舊」「已破」跟「沒有」同一邊,合併成二分不損失資訊。⚠舊欄位說明引用的「無線要避開 OOS t+9.6~+15.6」已作廢:那個對照組(完全無觸發棒)在OOS只有n<50列,形同不存在。⚠20/60日觀測每日重疊,日聚類未處理序列重疊,t偏大;宇宙是自選42檔高波動股。①節目主張「拉回線附近±3%買」仍 DROP(勝率42~43%、IS 96%超額集中前5檔、扣成本轉負)。小時線版另測全空(t<1.4)已否決。此欄是狀態顯示不是買賣訊號,不進分數。">關鍵一條線<span class="sub">距離%</span></th>
<th title="ATR(平均真實區間,Wilder 1978,14期)盤整壓縮/突破(2026-09-25 jack 交辦,來源:《御錢術》楊育華分析師節目ATR段落)。壓縮=近120交易日ATR%(=ATR14÷收盤)落在自身歷史後30%分位(自身相對低檔,非跨股比較);異常=壓縮狀態下今日真實區間超過昨收已知ATR14的1.5倍(節目原話:「超過1.5倍,方向改變了,要立刻出場」)。⚠2026-09-25嚴謹回測(scripts/research/atr_key_line_research.py,21年史·IS/OOS拆2023·日聚類·扣42檔籃子·扣50bps成本·安慰劑·集中度·逐年,僅限42檔):突破事件本身DROP——10/40/60日IS/OOS異號、安慰劑5組範圍蓋過真實均值(與隨機日不可區分)、前5檔佔比354%(逐年正負交替無穩定方向),不進分數。唯一IS/OOS同號子集=『恰好貼近關鍵一條線±1倍ATR內』(★近線,IS t+1.66/OOS t+1.80),仍未過本案嚴格門檻(|t_OOS|≥2),僅供觀察、同樣不進分數。純描述性狀態顯示,與關鍵一條線搭配看(★近線=兩者同時成立)。⚠2026-09-29 起關鍵一條線改二分口徑(20日時效∧跌破作廢),★近線只會出現在『能畫線』的股票上,覆蓋比原★近線回測(舊口徑:線永不作廢)窄很多,原 IS t+1.66/OOS t+1.80 不能直接套到新口徑。">ATR盤整<span class="sub">壓縮%/突破x</span></th>
<th title="本益比(同族群排名,2026-09-25 jack 交辦,依楊育華分析師《御錢術》節目邏輯:同族群比、不跨族群比,例如IC設計不跟記憶體比、被動元件不跟PCB比)。公式=現價(即時)÷TTM(近四季已公布)EPS。⚠與原方法差異:她說本益比分母該用『預估EPS』(法說會/營收/毛利率推算的未來EPS),我們沒有分析師預估EPS的資料源,只能用已公布TTM——落後指標非預估指標,她自己說EPS『兩三個月才變』故失真程度有限,但誠實揭露此為唯一實質差異。族群清單=既有SUBCAT細分類人工擴充真實上市櫃同業(scripts/research/pe_peer_group_research.py,2026-09-25驗證76檔代號皆存在)。百分位=現價本益比在族群內排名(0%=最便宜、100%=最貴,≤20%/≥80%標色);多數細分族群天生成員僅3~8檔,遠不到她說的20~30檔,如實呈現不硬湊。族群完整成員名單+個別本益比見個股詳情頁。純參考位置,未經嚴謹回測,不進分數">本益比<span class="sub">同族群%</span></th>
<th title="00981A(統一台股增長)持股市值(2026-09-27 jack 交辦)。金額=ezmoney快照當日市值(股數×當時收盤價,非即時);Δ=對前一個快照日的買賣金額(股數變動×最新收盤價,不含價格漲跌;未扣贖回等比例縮減),正(紅)=加碼/新進、負(綠)=減碼/出清,無資料(—)=近兩次快照皆未持有。純展示欄,與 00981a-l1h9 跟單研究線共用同一張 etf_holdings 表,不進分數、不影響任何評分或訊號,快照通常落後即時盤況一個交易日">00981A持股<span class="sub">市值億·Δ前次({biglot_dashboard.ETF981_PREV_ASOF or '—'})</span></th>
<th title="集保戶股權分散表(2026-09-29 jack 交辦):大戶(持股≥800張)合計占比%,TDCC 每週五公告(優先 tdcc 來源,缺值退回 finmind),週頻、非即時,跟本表其他盤中欄位不同尺度。⚠已知不可交易,僅供參考:①文獻查證(chip-signal-literature-verdicts 記憶)對「集保戶股權分散表」這個資料源本身 Google Scholar 零同儕審查支持,唯一像樣的實證是廠商回測 IC≈0.01(等同雜訊);國際基準文獻(CHS 2002 JFE)方向甚至相反,還被後續研究證實樣本外反轉。②本系統自己用同一份資料做的 HS 因子(散戶持股比,hs-factor-real-but-not-tradeable 記憶)通過五項對抗檢定(自相關/產業中性化/PIT緩衝/開收穩健/月份集中度)、原始 t=+4.23~4.45,但加控週轉率後年化淨值從 +5.44% 轉為 −0.14%,再控股價水準/產業後惡化到 −3.33%/年——結論是「低週轉率(流動性)溢酬」的代理,不是真正的籌碼 alpha。純參考展示欄,不進分數、不影響任何排序邏輯。">集保大戶800張<span class="sub">占比%·as_of·Δ前週</span></th>
<th title="散戶背離(SMFI,2026-09-25已採納進隔夜分):近30分散戶淨額佔比(≥{biglot_dashboard.SMFI_ACTIVE_FROM}起即時滾動更新,跟5分/30分欄位同算法) − 開盤(09:00-09:25)散戶淨額佔比。方向反直覺:散戶偏買方反而預測次日偏多(像散戶跟隨法人已建方向,非純反指標)。門檻不對稱——只有正向(≥+10pp)IS/OOS 一致,負向 OOS 在≥15pp 反號,故只設正向 +1、無負向項。2026-09-25原始版本(尾盤12:55-13:20固定窗)127日面板:IS IC 0.155→0.163(跳空)、OOS 0.068→0.074。2026-10-04改滾動窗後,47點5分鐘收尾點時段回測(IS 03-02~06-30/OOS 07-01~09-01,日聚類Spearman IC)證實{biglot_dashboard.SMFI_ACTIVE_FROM}之前訊號不穩定(KEEP/UNDECIDED/DROP交錯),之後兩個標的(次日跳空/收對收)連續站穩(OOS |t|>=2,12:05~12:30最強t+2.9~+4.6),故此欄改成{biglot_dashboard.SMFI_ACTIVE_FROM}前顯示—、之後即時更新,比原尾盤版本提早約70分鐘。⚠ IS 單變量控制後部分口徑未過嚴格 t≥2 門檻,依使用者明確指示採納。2026-10-05從④全日群組搬到訊號輸入區:已進淨分公式。">散戶背離<span class="sub">近30分−開盤 pp·{biglot_dashboard.SMFI_ACTIVE_FROM}起·≥10 記+1</span></th>
<th title="壓縮 =(現價 ÷ 近12個5分桶均價 − 1)%,需≥8桶;與全日大戶佔比聯合、多空對稱:黃粗體(多)= 大戶佔比≥+10% ∧ 壓縮<0(價壓著,127日隔夜 +139/t2.9);黃粗體(空)= 大戶佔比≤−10% ∧ 壓縮>0(大戶倒完價仍在均價上,−28~−80,勿抱非放空);其餘淡化。基準 +73。2026-10-05從⑤結構/隔夜事實欄搬到訊號輸入區:已進淨分公式。">壓縮<span class="sub">對1h均% × 大戶佔比</span></th>
<th data-nosort title="冰山委託(iceberg order)偵測守價位(2026-09-25 依 Frey & Sandås (2009) CFR Working Paper No. 09-06《The Impact of Iceberg Orders in Limit Order Books》原始演算法重建)。原文:『an iceberg to be detected after the first replenishment...keeps the detection state until...an expected replenishment has not occurred』『remembers the indicator values for multiple prices...undercut but later becomes the best quote again...still there』——本版修正三個與原文的落差:①觸發條件改成量耗盡到接近零(≤15%)才算,不是任意減少;②追蹤五檔全部價位(用價位當鍵),不是只追最優價,排名滑動仍持續追蹤;③交叉比對逐筆真實成交確認耗盡打在該價位,不只看當天總量。⚠14個交易日重跑結果:靠山(backing)兩側仍是雜訊(未復現原文Table V的顯著效果);跌破支撐(breakout_bear)延遲30秒後消失,確認雜訊;**突破壓力(breakout_bull)通過完整檢定**(即時/延遲30秒/Table V原文30筆成交口徑三種算法t值都達-2.2~-2.9,集中度55%不極端,安慰劑對照真實值在隨機範圍外)——方向是突破後回落(fade)非延續,已用0.5倍縮水、30分鐘線性淡出納入淨分,唯一進分數的部分;backing/breakout_bear仍是雜訊、純描述。2026-10-05從⑤結構/隔夜事實欄搬到訊號輸入區:整欄原樣搬動,只有breakout_bull部分已驗證進分。">冰山委託<span class="sub">守價位</span></th>
<th title="小哥原版·連次/連量（忠實作者，只用小哥公開的定義）：每5秒窗外盤量&gt;內盤量=紅、內&gt;外=綠；連次達±10亮燈🔔；連量=最近一窗主導方向張數（累計並列）；轉色=亮燈連攻段被打斷（紅轉綠=買盤竭盡、綠轉紅=賣盤竭盡）；緩搓=2分鐘漲幅&gt;3.5%。小哥未說明連量亮燈門檻。純描述，不進分數。">小哥·連次/連量<span class="sub">原版</span></th>
<th title="小哥原版·放空三步驟清單（說明欄2025-09-27）：①周轉率&gt;30%；②高壓區=連兩根以上大紅K／連兩三天漲停；③當日轉弱（人工判斷）。列出客觀可算的項目與今日紅K/黑K；『大紅K幾%』『轉弱怎麼算』小哥未說明故不自設。清單齊備不等於進場訊號，小哥要求最後再等轉色。純描述，不進分數。">小哥·放空三步驟<span class="sub">原版</span></th>
<th title="我們的統計·竭盡（與小哥原版分開）：窗×N=該窗÷每5秒均量；爆窗×k=單窗≥5×均量的窗數（5×自訂）；價差bps；P1=預登記規則（昨漲&gt;3%∧今紅K∧外攻連次≥10∧已有爆窗）前向驗證中。歷史毛+10~13bps但價差約26bps，淨約−15bps，非放空訊號。事件寫入forward_p1_events.jsonl。">統計·竭盡<span class="sub">我們的</span></th>
<th title="個股日內% − 宇宙日內%(百分點):負(綠)=相對大盤壓著(彈簧),>+1(黃)=已彈開;軟否決件:日線弱∧已彈=毒格−31bps。2026-10-05從⑤結構/隔夜事實欄搬到訊號輸入區:已進淨分公式(軟否決)。">相對強弱<span class="sub">對大盤</span></th>
<th title="5分窗成交金額 ÷ 近5日同時段中位(rvol)。≥5=爆量。2026-10-05從⑤結構/隔夜事實欄搬到訊號輸入區:未進淨分,純風險/情境注記。">量能倍數<span class="sub">x</span></th>
<th title="全日量能 = 今日累計成交額 ÷ 同時段基準累計(近5日同時段中位加總)。127日:成交÷20日均額 控大戶佔比後隔夜 +13.8/t2.64;≥1.5x 且大戶買時淨分 +1。2026-10-05從⑤結構/隔夜事實欄搬到訊號輸入區:已進淨分公式。">全日量能<span class="sub">x</span></th>
<th title="波動風險分數(0-100)＝融資日變動幅度歷史分位 與 借券日變動幅度歷史分位 的平均(不分方向,大增大減都算)。宇宙回測:分數與隔日盤中振幅單調正相關,控制當日振幅(排除純波動群聚)後仍顯著(t3.40 p0.0007)。只預測盤中來回幅度——對隔日淨報酬/跳空/量能皆無解釋力,非方向訊號,量能反而偏低(流動性變薄)。🌊🌊=≥92分 🌊=≥86分 藍字=≥80分。2026-10-05從⑤結構/隔夜事實欄搬到訊號輸入區:未進淨分,純風險分級注記。">波動分<span class="sub">隔日振幅預測</span></th>
<th title="今日振幅倍數 = (今高−今低)/昨收% ÷ 20日均振幅%。波動聚集:預測明日振幅為真、方向 IC≈0(tick排列/籌碼分數兩線驗過)→ 不投票、不進淨分;≥1.5x 黃粗=高波動日:同樣淨分對應更大 bps、急殺z 砍尾閾值可放寬、部位縮小。2026-10-05從⑤結構/隔夜事實欄搬到訊號輸入區:未進淨分,純風險/情境注記。">今日振幅<span class="sub">÷20日均 x</span></th>
<th title="股價共動(2026-10-03 jack 交辦,描述性提醒,不進分數):用族群判斷進場時,先看這檔跟族群到底連不連動。第一行=近1年日報酬扣掉加權指數後,相關係數最高的公司(跨組會標出,弱/不穩會標灰);第二行=組內黏(與族群平均相關≥.45,族群訊號可信)/組內中等/組內弱/無明顯同伴(別拿族群判斷它)/更像X組(資料上它跟別組走)/與某某無關(同族群卻不連動)/弱反向(扣大盤後偏反向)。滑鼠移上看前5名相關公司與同族群逐檔狀態。名單角色(config/biglot_roles.json):灰『參』=參考列,只看族群脈絡、不進排名/標籤/淨分,下單是同組有期貨的核心檔;黃『獨』=原地標注(無夥伴/掉隊/邊界),別拿族群判斷它;化合物/散熱/CCL 標『群弱連動』。限制:近1年單一行情;大跌日全池相關升高(下跌日.30 vs 上漲日.15)=同族一起殺不代表有獨立訊息;『更像X組』是未經樣本外驗證的假說。資料檔由 scripts/research/sector_comovement_tags.py 產生,需手動重跑更新">股價共動<span class="sub">最像 · 黏/無關</span></th>
<th data-nosort title="故事(2026-10-05):這檔「現在」符合哪個大戶/散戶互動故事的狀態。只描述狀態,不給分數、不進淨分 —— 九個故事經檢定大多「機制合理、狀態存在、但沒有edge」,所以這欄是人工判斷的素材,不是進場訊號。紅=無大戶(唯一4/4天站得住的負向濾網,別進);琥珀=待驗/稀有;綠=大戶在砸。門檻全用同分鐘橫斷面排名(時段中性)。點導航「📚 故事」看每條規則與九個故事的三維判決。">故事<span class="sub">狀態·非訊號</span></th>
<th data-nosort title="訊號合併欄(2026-10-08 精簡為5個計分標籤,其餘移除或降為觀察):【紅=看多】深接=30分跌≥30bps∧大戶佔比>5%(+7.1/t5.1,延遲仍+4.1) · 蓄勢隔夜=全日佔比≥10%∧壓縮<0(隔夜IC t7.1) · 連3買=持續。【綠=看空】噴後過熱=30分漲≥150bps(60分+6.5,OOS同號) · 勿追5m=5分漲≥20∧散戶參與跳升或大戶賣(+9.2/t13.7,逐月穩定;枯量最強) · 同賣=大戶賣∧散戶賣(隔夜−28/t−6) · 破昨防線@價=觸昨日午後低(−125bps/73%貫穿)。【觀察·不計分】機構暗退?/散戶虛拉?:歷史顯著但9月衰減、前瞻7日不足,定義已凍結,只記tag_events。【黃=注記】↓弱開=明日弱開候選。已移除:主力點火(無方向性)、純機構/巨資機構(IS+14/OOS−4.3反號)、勿追30(延遲+1.1)、虛胖接刀、深接5m(延遲後歸零)。【2026-09-24 即時制】盤中格改吃每秒滾動窗,條件連續 10 秒成立才觸發;名稱後數字=觸發後經過分鐘(粗體=≤5分最佳狀態);30分格 30 分後自動熄、5分格 5 分;✗=滾動數已反向(格失效);尾=13:00 後觸發無時距可兌現">訊號<br><span style='font-size:9px;font-weight:400'>紅多綠空黃注記 · 名稱+經過分′</span></th>
<th title="淨分 = 隔夜分(收盤→明開,0/±1/±2)與 盤中分V2.5(未來60分,bps 制,|分|≤40)分開計、不相加。隔夜:大戶佔比≥+10% +2/≤−10% −2 · 大戶買∧散戶佔比≥5% −1 · 大戶買∧壓縮>+1% −1 · 大戶賣∧壓縮>+0.3% −1 · 同賣 −1 · 日線↑多 +1 · 相對強弱>+1∧日線↓空 −1 · 全日量能≥1.5x(大戶買)+1 · 散戶背離(SMFI,尾盤−開盤散戶淨額佔比)≥+10pp +1(2026-09-25 採納,單邊、無對稱負向項)。盤中V2.3(2026-09-24,pit100×127日 IS 聯合OLS×0.7、按日聚類 t<2 歸零、OOS 未參與擬合;各項可加):勿追5m −3 · 噴後過熱 ≥200/≥300/≥400 −5/−8.5/−8.5、≥600 不計 · 急跌≤−600 +40(多方唯一存活項) · 蓄勢 −0.5~0% +4.5 / 蓄勢深 <−0.5% +5(大戶30分 5~40%;≥40% 鉅額不計) / 倒貨(0<壓縮≤0.5%∧≤−10%) −2.5 · 竭盡狀態格(近5分≤−0.2%=急跌;30秒主動賣≤40%=竭盡/≥60%=未竭):真空(竭盡∧末30秒仍跌≥10bps) +12.5 · 大戶接∧未竭 +6 · 賣壓未竭 +3 · 末30秒續跌 +2 · 竭盡∧散戶接 −5.5 · 急拉:末30秒續漲 −3.5 · 權證 ±3(暫) · 突破壓力防回落(2026-09-25,依 Frey&Sandås 2009 iceberg 偵測演算法重建,14日樣本 t-2.2~-2.9、延遲30秒+安慰劑對照皆過關,0.5倍縮水,30分鐘線性淡出)−4.3起。⚠ 單獨的「賣盤竭盡」是負的:賣壓退=反彈已發生。歸零:過熱150–200、急跌200–600、順漲/順跌/逆強、對開盤±3/±5%、散戶接跌(被狀態格吸收)、主力點火/暗退/破昨。V2.5擬合權重:散戶虛拉控過熱階與勿追5m後增量=0(t0.33,被吸收);盤中另有手設點數(sc_in)——深接30 +1、噴後過熱 −2、勿追5m −1(市場30分>0記0)。2026-10-08 稽核:機構暗退/散戶虛拉降為觀察標籤不計分(9月衰減、前瞻7日不足);逆弱≤−100、純機構、急拉·買壓竭盡、主力點火、深接5m、勿追30 無證據移除。09:30 前不計、無時段係數。「峰」= 近60分最極端分與時刻(每5秒取樣的極值,偏大,只當提示)。第二行=成因標籤(下單前必看):處置(disposal_windows.csv)/跌停鎖·觸跌停(MIS 五檔 y·l·z 算跌停價)/族群k/m(同細分產業近30分同向≥2%)/MOPS hh:mm(今日,尚無即時源→顯示 MOPS?)/昨MOPS hh:mm(T-1 重大訊息,TWSE/TPEx OpenAPI 快照,每晚 fetch_mops_today.py);跟盤殺(紅=不做)/自己殺(綠=大盤止跌它還在殺,要的格)/大盤仍跌(黃=等):大盤條件是進場過濾器不計分,因為分數預測超額、你吃原始。hover 看各標籤來源與時間。OOS(07-01~08,V2.5):IC +0.072(V2.4 +0.058);|分|≥15 多 n=427 超額+38/t4.8(延遲1桶 +20/t2.7 首次顯著)、≥20 多 n=108 +78/t5.0;空 ≥15 n=792 +25/t2.5;校準斜率 1.19。覆蓋比 V2.2 少約 40 倍,多數時間為 0 = 無證據不是中性。">淨分<span class="sub">隔夜 · 盤中V2.5 bps · 峰 · 成因</span></th>
<th data-nosort title="每檔自由筆記:點格子輸入,停止輸入 1.5 秒自動儲存(Ctrl/Cmd+S 立即);小字=最後編輯時間。存在資料目錄 stock_notes.json,不進 git。編輯中表格暫停更新,離開格子後恢復。">筆記<br><span style='font-size:9px;font-weight:400'>自動儲存 · 最後編輯</span></th>
</tr></thead><tbody>{''.join(trs)}</tbody></table>"""
