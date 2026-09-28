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


def _score_rows(rows, mkt30, nts, mkt30_r=None):
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
        if dt.get("above_ma5"):
            ov += 1; ovi.append(("日線↑多", +1))
        elif r.get("rs_live") is not None and r["rs_live"] > 1 and dt:
            ov -= 1; ovi.append(("相對強弱>+1∧日線↓空", -1))
        # 散戶版SMFI背離(2026-09-25 採納;scratch/smfi_score_design_2026-09-25.txt):尾盤(12:55-13:20)散戶淨額佔比 −
        # 開盤(09:00-09:25)散戶淨額佔比。127日精確重建 ov 控制後 IS ret_smfi 係數 t+2.95(次日跳空)/t+1.70(收對收);
        # 門檻掃描發現正負不對稱——正向(≥10pp)IS/OOS 單調一致(OOS t+2.11~+3.15 隨門檻走強),負向(≤−門檻)OOS 在
        # ≥15pp 反號(t−0.66~−1.29),故**只計正向、不設負向對稱項**。單邊規則 vs 基準 ov:IS IC 0.155→0.163(跳空)、
        # 0.063→0.069(收對收);OOS 0.068→0.074、−0.012→−0.007;觸發時勝率 IS 64%→71%、OOS 52%→58%,兩期同向提升。
        # IS 樣本本身邊緣(單變量控制後 IS t 未必 ≥2 於所有口徑),依使用者明確指示採納,非嚴格 IS/OOS 雙關口徑通過。
        if r.get("ret_smfi") is not None and r["ret_smfi"] >= 10:
            ov += 1; ovi.append(("散戶背離(SMFI)≥+10pp", +1))
        # ---- 盤中分 ----
        act = _active_tags(sid, nts)
        sc, sci = 0, []
        # 同源不累加(2026-09-24 jack 定案):同一筆大戶買會同時點亮 主力點火/純機構/深接30/深接5m → 取最大值一次;
        # 散戶側 散戶虛拉/勿追 同源 → 取一次 −1;機構暗退、噴後過熱、破昨防線 各自獨立來源。
        # 時間衰減(2026-09-24 jack 要求):標籤價值 = 權重 × (1 − 經過/時距),基準率是「首次觸發起未來30分」,越晚看剩越少;小數一位
        # 2026-09-28 移除「純機構」:跟 score_v2.py 的同名項幾乎重複定義(共線性診斷 phi 高度重疊),
        # V2.5 版多了 hm>=10:00 閘門更精確,不在這裡重複計分(tag 本身仍會觸發、仍會在其他地方顯示徽章)。
        bull_src = [(t, p * act[t]) for t, p in (("主力點火", 2), ("深接30", 1), ("深接5m", 1)) if t in act]
        if bull_src:
            best = max(bull_src, key=lambda x: x[1])
            sc += best[1]; sci.append(("大戶買[" + "·".join(f"{t}×{act[t]:.2f}" for t, _ in bull_src) + "]取最大", round(best[1], 1)))
        for tag, pts in (("機構暗退", -2), ("噴後過熱", -2)):
            if tag in act:
                sc += pts * act[tag]; sci.append((f"{tag}×{act[tag]:.2f}", round(pts * act[tag], 1)))
        ret_src = [t for t in ("散戶虛拉", "勿追30", "勿追5m") if t in act]
        if ret_src:
            if ("散戶虛拉" not in ret_src) and mkt30 > 0:
                sci.append(("勿追(市場30分>0,記0)", 0))
            else:
                w_ = max(act[t] for t in ret_src)
                sc -= w_; sci.append(("散戶側[" + "·".join(ret_src) + f"]×{w_:.2f}", round(-w_, 1)))
        if r.get("pmlow_warn"):
            sc -= 1; sci.append(("破昨防線", -1))
        sc_nowrt = sc
        # 2026-09-28 移除權證三分支(原本 sh>=0.6∧r30>0 / sh>=0.6∧b30<=-3e7 / sh<=0.4∧b30>=3e7,
        # 各±1未驗證):判斷式跟 score_v2.py 的「權證」項逐字相同,那邊已有 IS 擬合權重(±3.0),
        # 留在這裡只是重複計分同一件事,不是獨立驗證(2026-09-28 共線性診斷發現)。
        # 委託簿竭盡候選(尚非策略,jack 要求先計分;定義來自 09-23/24 tick 案例,未驗證):
        #   急殺中(近5分 ≤−0.20%)∧ 近30秒主動賣 ≤40% ∧ 買深 ≥3 分 → +1;急拉中 ∧ 主動買 ≤40%(sell≥60%)∧ 賣深 ≥3 分 → −1
        sp = r.get("sell30s_r"); w5 = r.get("w_ret_r") or 0
        bm, am = r.get("bid_min"), r.get("ask_min")
        if sp is not None:
            if w5 <= -20 and sp <= 0.40 and (bm or 0) >= 3:
                sc += 1; sci.append((f"賣盤竭盡候選(30s主動賣{sp*100:.0f}%·買深{bm:.1f}分,未驗證)", +1))
            elif w5 >= 20 and sp >= 0.60 and (am or 0) >= 3:
                sc -= 1; sci.append((f"買盤竭盡候選(30s主動買{(1-sp)*100:.0f}%·賣深{am:.1f}分,未驗證)", -1))
        r["sc_ov"], r["sc_in"], r["sc_in_nowrt"] = ov, sc, sc_nowrt
        try:
            _hm = biglot_dashboard.datetime.now(biglot_dashboard.TZ).strftime("%H:%M")
            r["sc_v2"], r["sc_v2_items"] = _score_v2(r, mkt30 if mkt30_r is None else mkt30_r, _hm)
        except Exception as _e:  # noqa: BLE001
            r["sc_v2"], r["sc_v2_items"] = None, [(f"計分失敗:{type(_e).__name__}", 0)]
        r["sc_ov_items"], r["sc_in_items"] = ovi, sci
