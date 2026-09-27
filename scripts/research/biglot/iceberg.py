"""biglot dashboard 重構：`_iceberg_update` 讀 `NAMES`（危險全域之一，`ingest()` 過日時整包
重新賦值——見 docs/biglot-refactor-roadmap.md）與 `ST`（模組層級單例，never 整包重新賦值，
但為了跟其餘存取方式一致、降低未來誤判風險，一律用同一套屬性存取寫法），還讀六個
`ICEBERG_*` 前綴的模組常數。

比照 `biglot/xq_style.py`／`biglot/pe_and_shadow.py`：只 `import biglot_dashboard`（模組本身，
不指名字），函式本體內用 `biglot_dashboard.X` 屬性存取——這是屬性查找，每次呼叫都會拿到
當下 `biglot_dashboard` 模組裡最新的值，不管 `ingest()` 換過幾次日都不會過期，也避免了
`from biglot_dashboard import X` 在循環 import 情境下可能提早炸掉的問題。

`_iceberg_trade_confirms` 已經搬去 `biglot/scoring_support.py`，直接從那裡 import，
不透過 `biglot_dashboard` 屬性存取。
"""
from __future__ import annotations

import biglot_dashboard
from biglot.scoring_support import _iceberg_trade_confirms


def _iceberg_update(r):
    """隱形大戶守價位·即時串流版,依 Frey & Sandås (2009) 原始演算法重建
    (2026-09-25 jack 交辦:「給你五個小時,你慢慢仔細地完成,請你一字一句的參考文獻的真正
    正確用法」)。文獻:Frey, S. & Sandås, P. (2009) "The Impact of Iceberg Orders in Limit
    Order Books", CFR Working Paper No. 09-06, University of Cologne。

    原文 Appendix A3 逐字引用:"The algorithm assumes an iceberg to be detected after the
    first replenishment. After the detection the algorithm keeps the detection state until
    all visible volume of the quote is cancelled or an expected replenishment has not
    occurred."、"The algorithm remembers the indicator values for multiple prices so if the
    current best quote...is undercut but later becomes the best quote again the algorithm
    assumes that the iceberg order is still there."

    第一版(2026-09-25 稍早)跟原文有三個落差,這版修正:
      落差一(觸發條件):原版「量降≥5張」就算被吃,太寬鬆;原文是「trade EXHAUSTS ALL
        displayed depth」——改成量降到接近零(≤原量15%或≤5張)才算「耗盡」。
      落差二(追蹤對象):原版只追「當下最優價」,排名一換就重置;原文追蹤「固定價位」,
        排名滑動仍持續追蹤——改成五檔全部價位都用價位當鍵追蹤,見 ST.iceberg[sid][side]
        現在是 {price_key: state} 字典,不是單一 cur。
      落差三(耗盡確認):原版只看「當天累計量 v 有沒有動」,不知道打在哪個價位;
        改成交叉比對 ST.recent[sid] 的逐筆真實成交價格是否落在該價位附近。

    2026-09-25 用 14 個交易日重跑(scripts/research/iceberg_frey_sandas_rebuild.py,
    scratch/iceberg_frey_sandas_2026-09-25.txt)结果,跟修正前差很多:
      · 靠山(backing,現在= 當下最優買/賣剛好是已偵測價位):兩側都還是雜訊,|t|<1.5,
        安慰劑對照沒有明顯脫離隨機範圍——沒有復現 Frey&Sandås 原文 Table V 測到的顯著效果,
        可能是 TWSE 五檔快照的解析度不夠(原文用 Xetra 完整逐筆重建),仍是 NULL。
      · 跌破支撐(breakout_bear):即時 t+0.73、延遲30秒後 t-0.01 幾乎完全消失、集中度只
        5%——確認是雜訊,不是訊號。
      · 突破壓力(breakout_bull)——**這次修正後,這個是唯一撐過檢定的**:即時 t-2.90、
        延遲30秒後 t-2.24(沒有像舊版一樣塌陷)、換算成 Table V 原文口徑(後30筆真實成交)
        t-2.93,三種算法都通過 |t|≥2;集中度前5檔55%(不算極端);安慰劑對照真實值(-10.2)
        落在隨機5組範圍(-3.2~-1.6)之外。方向是「突破壓力後回落」(fade),不是使用者原本
        設想的「突破=延續噴出」,但這是這整條研究線第一個通過完整檢定的結果。
    """
    sid = r.get("sym")
    if sid not in biglot_dashboard.NAMES:
        return
    v = r.get("v")
    try:
        v = float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        v = None
    ts = r.get("ts")
    if ts is None:
        return
    st_sid = biglot_dashboard.ST.iceberg[sid]
    for side, price_arr, qty_arr in (("bid", r.get("bp") or [], r.get("bq") or []),
                                      ("ask", r.get("ap") or [], r.get("aq") or [])):
        levels = st_sid[side]
        visible_now = set()
        for p, q in zip(price_arr, qty_arr):
            if p is None or q is None or p <= 0:
                continue
            key = round(p, 4)
            visible_now.add(key)
            st = levels.get(key)
            if st is None:
                levels[key] = {"qty": q, "ref_peak": q, "last_seen": ts, "state": "none",
                                "exhaust_ts": None, "detected": False}
                continue
            prev_qty = st["qty"]; st["last_seen"] = ts
            if st["state"] == "none":
                if prev_qty > 0 and q <= max(biglot_dashboard.ICEBERG_EXHAUST_MIN_ABS, prev_qty * biglot_dashboard.ICEBERG_EXHAUST_FRAC):
                    if _iceberg_trade_confirms(sid, ts - biglot_dashboard.ICEBERG_TRADE_LOOKBACK, ts, p):
                        st["state"] = "exhausted"; st["exhaust_ts"] = ts; st["ref_peak"] = prev_qty
            elif st["state"] == "exhausted":
                if q >= st["ref_peak"] * biglot_dashboard.ICEBERG_REPLENISH_FRAC:
                    st["state"] = "detected"; st["detected"] = True   # 原文:偵測到=第一次補回
                elif ts - st["exhaust_ts"] > biglot_dashboard.ICEBERG_GRACE_SEC:
                    st["state"] = "none"
            elif st["state"] == "detected":
                if prev_qty > 0 and q <= max(biglot_dashboard.ICEBERG_EXHAUST_MIN_ABS, prev_qty * biglot_dashboard.ICEBERG_EXHAUST_FRAC):
                    if _iceberg_trade_confirms(sid, ts - biglot_dashboard.ICEBERG_TRADE_LOOKBACK, ts, p):
                        st["state"] = "exhausted"; st["exhaust_ts"] = ts
            st["qty"] = q
        # 落差二收尾:突破檢查優先於寬限期修剪(先前版本的 bug——見研究腳本同名說明)
        b, _ = (next(((p, q) for p, q in zip(r.get("bp") or [], r.get("bq") or []) if p and q and p > 0), (None, None)))
        a, _ = (next(((p, q) for p, q in zip(r.get("ap") or [], r.get("aq") or []) if p and q and p > 0), (None, None)))
        mid = (b + a) / 2 if (b and a) else None
        for key, st in list(levels.items()):
            if st["detected"] and mid is not None:
                breached = (side == "bid" and mid < key * (1 - biglot_dashboard.ICEBERG_TRADE_TOL)) or \
                           (side == "ask" and mid > key * (1 + biglot_dashboard.ICEBERG_TRADE_TOL))
                if breached:
                    kind = "breakout_bear" if side == "bid" else "breakout_bull"
                    st_sid["last_breakout"] = {"kind": kind, "ts": ts, "price": key}
                    del levels[key]
                    continue
            if key not in visible_now and ts - st["last_seen"] > biglot_dashboard.ICEBERG_GRACE_SEC:
                del levels[key]
