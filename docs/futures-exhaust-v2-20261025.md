# 期貨竭盡 v2（大台，探索）— 2026-10-25

使用者決定（「連次≥10 ∧ RVOL≥2 倍」）：大台（TXF）在原有「連次達 10 亮燈／轉色」之外，另標一組探索規則。

## 規則
- 對象：TXF 日盤。**小台／微台／台積電期貨不變**（研究顯示小台／微台無訊號且天天亮燈）。
- 條件：5 秒窗連次 ≥10 被反向窗打斷（轉色）∧ 近 30 秒 RVOL ≥2×。轉色方向＝新顏色；紅連被打斷＝買盤竭盡，綠連被打斷＝賣盤竭盡。
- RVOL＝最近 6 個 5 秒窗（含轉色窗）可判定口數 ÷ 過去 ≤10 個日盤「每 30 秒平均口數」（日盤可判定口數日合計 ÷ 600；PIT，不足 3 日不判）。
- 基準檔：`${DATA}/../cache/futflow_ref30_TXF.json`（`scripts/research/futures_ref30_build.py` 建；頁面收盤後自動補寫）。

## 證據（[事實]，`scripts/research/futures_exhaust_v2_eval.py`）
大台 37 日、含零毛：3/5/15 分 +1.4/+1.6/+3.9 bps，t 約 1.1～1.4 → **未達顯著，非進出場訊號**，僅為描述／前向觀察。

## 不動的東西
- 凍結的 S1（`WindowStreak`，連次達 10 亮燈後反向、持有 15 分）與 `docs/futures-streak-prereg-20261019.md` 的評估照舊，帳本 `futures_streak_events.jsonl` 不變。
- v2 事件另寫 `biglot_forward/futures_exhaust_v2_events.jsonl`（rule=`fut-exh-v2-2026-10-25`，source=live/replay）。

## 頁面
`/futflow` 大台圖上 ★（橘＝買盤竭盡、青＝賣盤竭盡，hover 顯示 RVOL）；TXF 檢查點 schema 升 4，部署後重建一次。
