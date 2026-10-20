"""biglot dashboard 重構：`_score_rows`（隔夜分/盤中分組裝，逐檔呼叫，主表渲染迴圈
每個 refresh cycle 每檔都會呼叫一次），從 scripts/research/biglot_dashboard.py 逐字搬移，
邏輯不改一行。

依賴分析：
- `ST`、`WRT`（`WRT` 是路線圖列出的「每日整包重新賦值」危險全域之一；`ST` 是高 fan-out
  的共用狀態物件）、`TZ` 都是 biglot_dashboard.py 自己定義的模組層級全域，一律
  `import biglot_dashboard`（模組本身，不指名字），在函式本體「呼叫當下」用
  `biglot_dashboard.X` 屬性存取——理由同 biglot/xq_style.py、biglot/pe_and_shadow.py、
  biglot/score_v2.py 開頭注解：屬性查找每次都拿到當下 biglot_dashboard 模組裡最新的值，
  不會有 stale reference，也不會有循環 import 在載入期就炸掉的問題。
- `datetime.now(TZ)`——**這是 Bug 1 的同一個地雷**：驗證工具凍結時間是靠 monkey-patch
  `biglot_dashboard.datetime`，如果這裡 `from datetime import datetime` 再呼叫 `.now()`，
  抓到的會是真實牆鐘而不是測試的假時鐘。已改成
  `biglot_dashboard.datetime.now(biglot_dashboard.TZ)`。
- `_active_tags`：已搬到 biglot/scoring_support.py，直接 import。
- `_score_v2`：已搬到 biglot/score_v2.py，直接 import。
"""
from __future__ import annotations

import biglot_dashboard
from biglot.score_v2 import _score_v2
from biglot.scoring_support import _active_tags


def _score_rows(rows, mkt30, nts):
    """隔夜分 / 盤中分:各項權重只用 0/±1/±2,依 127 日基準率;權證依 jack 要求納入盤中分(±1,未驗證)。
    寫入 r["sc_ov"], r["sc_in"], r["sc_ov_items"], r["sc_in_items"], r["sc_in_nowrt"]。"""
    for r in rows:
        sid = r["sid"]
        ds = biglot_dashboard.ST.day.get(sid) or {}
        tot = ds.get("tot") or 0
        bs = r.get("bigsh_d")                      # 全日大戶佔比 %
        rp = (r["retday"] / tot * 100) if (tot and r.get("retday") is not None and not r["unm"]) else None
        cmp_ = r.get("cmp1h")
        ov, ovi = 0, []
        if bs is not None and bs >= 10:
            ov += 2; ovi.append(("大戶佔比≥+10%", +2))
            if rp is not None and rp >= 5:
                ov -= 1; ovi.append(("散戶佔比≥5%(B格)", -1))
            if cmp_ is not None and cmp_ > 1.0:
                ov -= 1; ovi.append(("壓縮>+1% 彈開", -1))
            if r.get("rvol_day") is not None and r["rvol_day"] >= 1.5:
                ov += 1; ovi.append(("全日量能≥1.5x", +1))
        elif bs is not None and bs <= -10:
            ov -= 2; ovi.append(("大戶佔比≤−10%", -2))
            if cmp_ is not None and cmp_ > 0.3:
                ov -= 1; ovi.append(("大戶賣∧壓縮>+0.3%", -1))
        if "同賣" in (r.get("stamp") or ""):
            ov -= 1; ovi.append(("同賣", -1))
        dt = r.get("dtrend") or {}
        # 2026-09-28 移除「日線↑多」計分:正式對隔夜報酬擬合 IS t=-0.38、OOS t=-0.60,純雜訊;
        # 跟既有 biglot-ta-daily-filter-no-value 發現一致,且合乎 Sullivan-Timmermann-White (1999)
        # 對簡單均線規則資料窺探校正後失效的文獻方向。tag 本身仍可能用於顯示,只是不計分。
        if dt.get("above_ma5"):
            ovi.append(("日線↑多(正式擬合無顯著性,不計)", 0))
        elif r.get("rs_live") is not None and r["rs_live"] > 1 and dt:
            ov -= 1; ovi.append(("相對強弱>+1∧日線↓空", -1))
        # 散戶背離(SMFI,2026-09-25 採納;scratch/smfi_score_design_2026-09-25.txt):原始版本是
        # 尾盤(12:55-13:20)散戶淨額佔比 − 開盤(09:00-09:25)散戶淨額佔比,127日精確重建 ov 控制後
        # IS ret_smfi 係數 t+2.95(次日跳空)/t+1.70(收對收);門檻掃描發現正負不對稱——正向(≥10pp)
        # IS/OOS 單調一致(OOS t+2.11~+3.15 隨門檻走強),負向(≤−門檻)OOS 在≥15pp 反號(t−0.66~−1.29),
        # 故**只計正向、不設負向對稱項**。單邊規則 vs 基準 ov:IS IC 0.155→0.163(跳空)、0.063→0.069
        # (收對收);OOS 0.068→0.074、−0.012→−0.007;觸發時勝率 IS 64%→71%、OOS 52%→58%,兩期同向提升。
        # IS 樣本本身邊緣(單變量控制後 IS t 未必 ≥2 於所有口徑),依使用者明確指示採納,非嚴格 IS/OOS 雙關口徑通過。
        # 2026-10-04 jack 交辦「盤中隨時取得、即時變化較好」:r["ret_smfi"] 的「現在」這一側已經在
        # render_main.py 改成近30分滾動窗(≥biglot_dashboard.SMFI_ACTIVE_FROM 起算,之前為 None),
        # 本欄的 ≥10pp 判準照舊沿用、未針對滾動窗版本重新優化閾值,但 scripts/research/
        # retail_smfi_rolling30_timing.py 的47點時段回測證實該時間點起的連續性/強度不輸原尾盤版本
        # (12:05~12:30 甚至更強:跳空 OOS t+2.9~+4.6,收對收 t+2.0~+3.4)。
        if r.get("ret_smfi") is not None and r["ret_smfi"] >= 10:
            ov += 1; ovi.append(("散戶背離(SMFI)≥+10pp", +1))
        # ---- 盤中分 ----
        act = _active_tags(sid, nts)
        sc, sci = 0, []
        # 同源不累加;時間衰減 權重×(1−經過/時距)。2026-10-08 盤點(144 日面板、日聚類、延遲 1 桶、IS/OOS):
        # 只留三項有穩定證據者:深接30 +1(+7.1/延遲+4.1)、噴後過熱 −1(+6.5/延遲+3.1)、勿追5m −1(+9.2/延遲+2.7,逐月皆正)。
        # 機構暗退/散戶虛拉降為觀察標籤(不計分):144 日整體顯著,但 9 月衰減(+0.6/+1.3)、前瞻 7 日方向未確認;
        # 勿追30(延遲+1.1)/主力點火/深接5m/純機構/竭盡候選 無證據,已刪。
        deep = act.get("深接30")
        if deep:
            sc += deep; sci.append((f"深接30×{deep:.2f}", round(deep, 1)))
        pump = act.get("噴後過熱")
        if pump:
            sc -= pump; sci.append((f"價漲過頭[噴後過熱]×{pump:.2f}", round(-pump, 1)))
        chase = act.get("勿追5m")
        if chase:
            if mkt30 > 0:
                sci.append(("勿追5m(市場30分>0,記0)", 0))
            else:
                sc -= chase; sci.append((f"散戶側[勿追5m]×{chase:.2f}", round(-chase, 1)))
        # 2026-09-28 移除「破昨防線」計分:正式擬合(控制急跌類格子後)IS t=+1.9996(差0.0004沒過
        # 門檻)、方向翻正而非現行的利空-1,OOS t=+0.77不顯著。文獻(短期反轉/52週低點)+內部已驗證
        # 的zscore-normalized-spike-reversion都支持「跌深後短期反彈」,現行-1的方向本身可能是
        # 跟已上線的「急跌≤−600 +40」邏輯互相矛盾;但翻正證據不夠穩健(OOS不顯著),故歸零而非翻轉。
        if r.get("pmlow_warn"):
            sci.append(("破昨防線(正式擬合方向相反且OOS不顯著,不計)", 0))
        sc_nowrt = sc
        r["sc_ov"], r["sc_in"], r["sc_in_nowrt"] = ov, sc, sc_nowrt
        try:
            _hm = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%H:%M")
            r["sc_v2"], r["sc_v2_items"] = _score_v2(r, _hm)
        except Exception as _e:  # noqa: BLE001
            r["sc_v2"], r["sc_v2_items"] = None, [(f"計分失敗:{type(_e).__name__}", 0)]
        r["sc_ov_items"], r["sc_in_items"] = ovi, sci
