"""biglot dashboard 重構：`_score_v2`（核心 V2.5 盤中計分引擎），從
scripts/research/biglot_dashboard.py 逐字搬移，邏輯不改一行。

依賴分析：
- `TZ`、`WRT`（每日整包重新賦值的危險全域）、`SC_HIST` 都是 biglot_dashboard.py
  自己定義的模組層級全域，一律 `import biglot_dashboard`（模組本身，不指名字），
  在函式本體「呼叫當下」用 `biglot_dashboard.X` 屬性存取——理由同
  biglot/xq_style.py、biglot/pe_and_shadow.py 開頭注解：屬性查找每次都拿到當下
  biglot_dashboard 模組裡最新的值，不會有 stale reference，也不會有循環 import
  在載入期就炸掉的問題。`V23_W` 雖非「整包重新賦值」的 18 個危險全域之一（是
  模組層級的靜態 dict literal），但為了跟危險全域的存取方式保持一致、降低未來
  誤判風險，同樣走 `biglot_dashboard.V23_W` 屬性存取。
- `datetime.now(TZ)` 與 `datetime.fromtimestamp(pk[0], TZ)`——**這是 Bug 1 的
  確切現場**：驗證工具凍結時間是靠 monkey-patch `biglot_dashboard.datetime`，
  如果這裡 `from datetime import datetime` 再呼叫 `.now()`，抓到的會是真實
  牆鐘而不是測試的假時鐘。兩處呼叫都改成
  `biglot_dashboard.datetime.now(biglot_dashboard.TZ)` /
  `biglot_dashboard.datetime.fromtimestamp(pk[0], biglot_dashboard.TZ)`。
- `time.time()`：與「現在時間」無關的 stdlib 呼叫，plain `import time` 即可。
- `_b30n`/`_b5n`：已搬到 biglot/utils.py，直接 import。
- `from collections import deque as _dq`：函式本體內的區域 import，逐字保留
  原樣，不搬到檔案頂層。
"""
from __future__ import annotations

import time

import biglot_dashboard
from biglot.utils import _b30n, _b5n


def _score_v2(r, mkt30, hm=None):
    """盤中分 V2.3(bps;2026-09-24):權重表 V23_W(IS 聯合 OLS×0.7 收縮,t<2 歸零),各項**可加**、無同源取一次
    (聯合係數已是條件增量)、無時段係數(池化擬合;09:30 前仍不計)、|分| 上限 40。
    mkt30 應傳滾動版(與個股 r30_r 同鐘)。IS/OOS 對照見 scratch/v23_fit_2026-09-24.txt。"""
    hm = hm or biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%H:%M")
    if hm < "09:30":
        return 0.0, [("09:30 前不計分", 0)]
    sc, it = 0.0, []
    def add(k, name=None, sign=1):
        nonlocal sc
        v = round(sign * biglot_dashboard.V23_W[k], 1); sc += v; it.append((name or k, v))
    w5 = r.get("w_ret_r"); r30 = r.get("r30_r"); rb5 = r.get("rbuy5_r"); unm = r["unm"]
    b5n = _b5n(r)
    if w5 is not None and w5 > 20 and rb5 is not None and rb5 >= 5 and not unm:
        add("散戶虛拉")
    if w5 is not None and w5 > 20 and (((r.get("dshare5_r") or 0) > 5 and not unm) or (b5n is not None and b5n < -5)):
        add("勿追5m")
    if r30 is not None:
        if r30 >= 600:
            it.append(("噴後過熱≥600 近漲停,不計", 0))
        elif r30 >= 400: add("過熱400-600", f"噴後過熱≥400({r30:.0f})")
        elif r30 >= 300: add("過熱300-400", "噴後過熱≥300")
        elif r30 >= 200: add("過熱200-300", "噴後過熱≥200")
        if r30 <= -600: add("急跌≤−600", f"急跌≤−600({r30:.0f})")
        if mkt30 >= 5:
            if r30 <= -100: add("逆弱≤−100")
            elif r30 <= -50: add("逆弱50-100")
            elif r30 <= -20: add("逆弱20-50")
    if (hm >= "10:00" and b5n is not None and b5n > 10 and (r.get("tot5_r") or 0) > 0 and r.get("share5_r") is not None and r["share5_r"] < 5 and not unm
            and (r.get("bigp30_r") if r.get("bigp30_r") is not None else 0) < 0 and (r.get("big5p_r") if r.get("big5p_r") is not None else 0) < 0):
        add("純機構", "巨資機構" if (r.get("big5_r") or 0) >= 3e7 else "純機構")
    # 壓縮(現價 ÷ 近12桶均價 −1,需≥8桶)× 近30分大戶佔比:大戶買而價被壓著 = 蓄勢;大戶倒而價仍在均價上 = 倒貨。
    # 單獨的大戶30分佔比(主力點火)在60分尺度為 0,增量只在與壓縮的交互。
    cmp_ = r.get("cmp1h"); b30n = _b30n(r)
    if cmp_ is not None and b30n is not None:
        if 5 <= b30n < 40 and cmp_ < -0.5: add("蓄勢深", f"蓄勢深(壓縮{cmp_:+.1f}%∧大戶30分{b30n:+.0f}%)")
        elif 5 <= b30n < 40 and cmp_ < 0: add("蓄勢", f"蓄勢(壓縮{cmp_:+.1f}%∧大戶30分{b30n:+.0f}%)")
        elif b30n >= 40 and cmp_ < 0: it.append((f"鉅額吸(大戶30分{b30n:+.0f}%,≥40% 不計)", 0))
        elif 0 < cmp_ <= 0.5 and b30n <= -10: add("倒貨", f"倒貨(壓縮{cmp_:+.1f}%∧大戶30分{b30n:+.0f}%)")
    w = biglot_dashboard.WRT.get(r["sid"]) if isinstance(biglot_dashboard.WRT.get(r["sid"]), dict) else None
    if w:
        wt = (w.get("call_30") or 0) + (w.get("put_30") or 0); b_, s_ = (w.get("bull_30") or 0), (w.get("bear_30") or 0)
        sh = b_ / (b_ + s_) if (b_ + s_) > 0 else None; b30 = r.get("big30_r") or 0
        if wt >= 1e6 and sh is not None:
            if sh >= 0.6 and (r30 or 0) > 0: add("權證", "權證偏多∧價漲(暫)", -1)
            elif sh >= 0.6 and b30 <= -3e7: add("權證", "權證偏多∧大戶賣(暫)", -1)
            elif sh <= 0.4 and b30 >= 3e7: add("權證", "權證偏空∧大戶買(暫)", +1)
    # 竭盡狀態格(V2.5):賣壓還在→買;賣壓退了∧散戶在接→晚了;沒人主動賣價卻掉→真空(最強);急拉對稱
    sp = r.get("sell30s_r"); r30s = r.get("r30s_r")
    if sp is not None and w5 is not None:
        exh, notexh = sp <= 0.40, sp >= 0.60
        if w5 <= -20:
            if exh and r30s is not None and r30s <= -10: add("急跌·真空", f"急跌·真空(30s主動賣{sp*100:.0f}%∧末30秒{r30s:+.0f}bps)")
            if notexh: add("急跌·賣壓未竭", f"急跌·賣壓未竭(30s主動賣{sp*100:.0f}%)")
            if exh and rb5 is not None and rb5 >= 5 and not unm: add("急跌·竭盡散戶接", f"急跌·竭盡∧散戶接(散買{rb5:.0f}%)")
            if b5n is not None and b5n > 5 and not exh: add("急跌·大戶接∧未竭", f"急跌·大戶接∧未竭(大戶5分{b5n:+.0f}%)")
            if r30s is not None and r30s <= -10 and not exh: add("急跌·末30秒續跌", f"急跌·末30秒續跌({r30s:+.0f}bps)")
            if exh and r30s is not None and r30s > 0: it.append((f"急跌·竭盡已止跌(30s主動賣{sp*100:.0f}%,反彈已發生,0)", 0))
        elif w5 >= 20:
            if notexh and r30s is not None and r30s < 0: add("急拉·買壓竭盡", f"急拉·買壓竭盡(30s主動賣{sp*100:.0f}%∧末30秒{r30s:+.0f})")
            if r30s is not None and r30s >= 10: add("急拉·末30秒續漲", f"急拉·末30秒續漲({r30s:+.0f}bps)")
    if abs(sc) > 40:
        it.append((f"上限 ±40(原 {sc:+.0f})", 0)); sc = 40.0 if sc > 0 else -40.0
    # 近 60 分極端分(⚠ 每 5 秒取樣的極值統計量,系統性大於桶級分數;只當「剛剛出現過」提示)
    from collections import deque as _dq
    hq = biglot_dashboard.SC_HIST.setdefault(r["sid"], _dq()); nts = time.time(); hq.append((nts, round(sc, 1)))
    while hq and hq[0][0] < nts - 3600:
        hq.popleft()
    pk = max(hq, key=lambda x: abs(x[1])) if hq else (nts, sc)
    r["sc_v2_peak"] = (pk[1], biglot_dashboard.datetime.fromtimestamp(pk[0], biglot_dashboard.TZ).strftime("%H:%M"))
    return round(sc, 1), it
