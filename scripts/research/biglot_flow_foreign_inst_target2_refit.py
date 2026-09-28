"""大戶流→外資即時代理：「盤中目標矩陣」子分析前視偏誤修正重跑（2026-09-28 稽核）。

背景
----
`biglot-flow-is-foreign-inst-proxy.md` 記錄的研究分四步（`/tmp/bigvsinst/step{1..5}.py`，
一次性 session heredoc、從未存檔、`/tmp` 早已清空）。主判決（Q5 路徑分解 −1.6bps，
`step4.py`）已由稽核確認乾淨——`step4.py` 的 demean 基準本就是用當天「全部」`bd[d]`
（`g=sorted(bd[d], ...)`，沒有先過濾）算的，鎖死排除只發生在最後「可成交隔夜超額」
那一步，順序本來就對，不需重跑。

本檔重跑的是稽核抓到有前視偏誤（look-ahead bias）的「盤中目標矩陣」子分析
（`step5.py`，程式碼從 session transcript
`~/.claude/projects/-Users-jackm4-goldenstocks/28467891-4af2-58c7-98e3-4238e9f8df01.jsonl`
第 115 行 heredoc 逐字核對回來）。問題出在 `fm(xf, yf, keep, ctrl)`：

    def fm(xf, yf, keep=lambda e: True, ctrl=None):
        co=[]
        for d in days:
            g=[e for e in bd[d] if keep(e)]      # <-- 先用 keep() 把鎖死股濾掉
            ...
            def zr(vals):
                r=rank(vals); ...                # <-- 排名/z-score 在已過濾的 g 上算

targets ②⑤ 傳 `keep=lambda e: not e['lock_close']`、targets ③④ 傳
`keep=lambda e: not e['lock_open1']`——橫斷面排名/z-score 的分母是「當天收盤／次日
開盤沒鎖死的股票」，不是固定完整宇宙，把「今天誰鎖死了」這個要收盤才確定的資訊
洩漏進了排名基準。target①（`keep=lambda e: True`）沒有做鎖死排除，不受影響，本檔
一併重算只作為「無變化」的對照/健檢用。

修正邏輯
--------
用今天新建的共用函式庫 `biglot_cross_sectional_lib.cross_sectional_then_filter`
取代「先 `keep()` 過濾、才對過濾後的子集算 rank/z」的寫法：

1. 排名/z-score 一律對「當天完整 `bd[d]`」（不論鎖不鎖死）計算一次（對全部日期一次
   `groupby` 完成，等價於逐日分別計算）。
2. `lock_close` / `lock_open1`（賽馬那段再加 `has_inst`）只在算完排名/z 之後，才用
   來切出「這筆觀測能不能拿去跑迴歸」的 tradable 子集。
3. 原始 `zr()` 是「先排名、再對排名值做 z-score」（不是直接對原始值 z-score）。這裡
   用兩段式呼叫共用函式庫重現同一個轉換：第一次呼叫算 `{col}_rank`（百分位排名
   0~1），第二次呼叫把 `{col}_rank` 欄位再丟進去算 `{col}_rank_z`。z-score 對任何
   仿射變換不變，原始 `rank()` 回傳的是 0..n-1 平均排名、pandas `rank(pct=True)`
   回傳 0..1 百分位排名，兩者只差一個仿射變換，z 值逐位等價——**唯一**不完全等價
   的地方是標準差分母：原始 `zr()` 用 population std（`/n`），共用函式庫的
   `zscore` 用 pandas 預設 sample std（`/ (n-1)`）。每日样本數 n≈91（11,310 檔日 /
   124 日），兩者比值 sqrt(n/(n-1))≈1.0055，對方向與顯著性判讀無影響，這裡不為了
   逐位對齊而繞過共用函式庫（該函式庫的存在目的就是不要大家各自重寫這段）。
4. 迴歸維持原始「含截距」設計：原始 `ols_slope` 的 `cols=[[1.0]*n]+Xcols` 本來就有
   截距行，這支腳本沒有踩「通過原點迴歸」的雷（那是 `biglot-diff-normalization-
   verdict` 那份的問題），這裡繼續維持、不移除。
5. 其餘邏輯（目標變數定義 `Y_cut2close`/`Y_close2open1`/`Y_open12close1`/
   `Y_open12open2`/`Y_close2close1`、事件建構、控制變數、targets ①~⑤ 各自定義、
   `COST=47.1` 常數、賽馬段的 `has_inst` 篩選）逐字比照原始 `step5.py`，不自創
   新方法。

輸出同時印出「原始寫法重現版」（用來確認能對上筆記 +27.7bps/t=9.2 這類數字）與
「修正版」，方便逐格對照。

用法：
    PYTHONPATH=src .venv/bin/python scripts/research/biglot_flow_foreign_inst_target2_refit.py
"""
from __future__ import annotations

import math
import pickle
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from biglot_cross_sectional_lib import cross_sectional_then_filter  # noqa: E402

CUTS = ["1100", "1200", "1300"]
CUT_LABEL = {"1100": "11:00", "1200": "12:00", "1300": "13:00"}
COST = 47.1  # bps，雙邊（比照原始 step5.py：手續費6折雙邊+證交稅，與進場時點無關）


# --------------------------------------------------------------------------
# Step 1（比照原始 step1.py）：符號校準 + 法人資料（單源 twse_t86）
# --------------------------------------------------------------------------
def _corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return float("nan")
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def load_sign_and_inst(rows: list[dict]) -> tuple[float, dict]:
    cal_a, cal_r = [], []
    for r in rows:
        v = r.get("vol_13:00") or 0
        if v > 0 and r.get("open") and r.get("close"):
            cal_a.append(r["big500_13:00"] / v)
            cal_r.append(r["close"] / r["open"] - 1)
    sign = -1.0 if _corr(cal_a, cal_r) < 0 else 1.0
    print(f"[事實] 符號校準 corr(big500_13:00/vol, 當日 open→close) = {_corr(cal_a, cal_r):+.3f} "
          f"→ SIGN={sign:+.0f}")

    conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    sids = sorted({r["sid"] for r in rows})
    ds = sorted({r["date"] for r in rows})
    ph = ",".join("?" * len(sids))
    inst = {}
    for sid, d, fn, it, dl, tn in conn.execute(
        f"""select stock_id, trade_date, foreign_net, investment_trust_net, dealer_self_net,
                   three_institution_net
            from stock_institutional_daily where source='twse_t86'
            and trade_date between ? and ? and stock_id in ({ph})""",
        [ds[0], ds[-1], *sids],
    ):
        inst[(sid, d)] = (fn, it, dl, tn)
    conn.close()
    print(f"[事實] 法人 twse_t86 命中 {len(inst)} 檔日；panel 交集 "
          f"{sum(1 for r in rows if (r['sid'], r['date']) in inst)}")
    return sign, inst


# --------------------------------------------------------------------------
# Step 5 事件建構（逐字比照原始 step5.py，只是把輸出從 dict-of-dict 改存成
# 攤平的 list[dict]，方便後面轉成 pandas DataFrame）
# --------------------------------------------------------------------------
def _tick(p: float) -> float:
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1.0 if p < 1000 else 5.0


def _limit_up(prev: float) -> float:
    t = _tick(prev)
    return math.floor(round(prev * 1.1 / t, 6)) * t


def build_events(raw: list[dict], sign: float, inst: dict) -> list[dict]:
    byd: dict = defaultdict(dict)
    for r in raw:
        byd[r["sid"]][r["date"]] = r

    events = []
    for sid, m in byd.items():
        ds = sorted(m)
        for i in range(1, len(ds) - 2):
            d0, d1, d2 = ds[i], ds[i + 1], ds[i + 2]
            r0, r1, r2 = m[d0], m[d1], m[d2]
            c_1 = m[ds[i - 1]].get("close")
            c0 = r0.get("close")
            o1 = r1.get("open")
            c1 = r1.get("close")
            o2 = r2.get("open")
            if not all([c_1, c0, o1, c1, o2]):
                continue
            e = {
                "d": d0,
                "sid": sid,
                "ret0": (c0 / r0["open"] - 1) if r0.get("open") else 0.0,
                "lock_close": bool(c0 >= _limit_up(c_1) - 1e-9),
                "lock_open1": bool(o1 >= _limit_up(c0) - 1e-9),
                "inst": (inst.get((sid, d0), (0, 0, 0, 0))[3] or 0) / 1000.0 / (r0.get("vol_day") or 1),
                "has_inst": (sid, d0) in inst,
            }
            ok = True
            for c in CUTS:
                lbl = CUT_LABEL[c]
                v = r0.get(f"vol_{lbl}") or 0
                px = r0.get(f"px_{lbl}")
                if v <= 0 or not px:
                    ok = False
                    break
                e[f"A_{c}"] = sign * r0[f"big500_{lbl}"] / v
                e[f"Y_cut2close_{c}"] = (c0 / px - 1) * 1e4
            if not ok:
                continue
            e["Y_close2open1"] = (o1 / c0 - 1) * 1e4
            e["Y_open12close1"] = (c1 / o1 - 1) * 1e4
            e["Y_open12open2"] = (o2 / o1 - 1) * 1e4
            e["Y_close2close1"] = (c1 / c0 - 1) * 1e4
            events.append(e)
    return events


# --------------------------------------------------------------------------
# 迴歸：原始寫法重現（bug：keep() 先過濾，rank/z 在過濾後子集上算）
# --------------------------------------------------------------------------
def _zr_buggy(arr: np.ndarray) -> np.ndarray:
    """逐位比照原始 `zr()`：pandas rank(pct=True)（與原始 0..n-1 排名只差仿射變換，
    z 值等價）之後，用 population std（分母 n，比照原始 `math.sqrt(.../n)`）。"""
    r = pd.Series(arr).rank(method="average", pct=True).to_numpy(dtype=float)
    m = r.mean()
    s = math.sqrt(((r - m) ** 2).sum() / len(r)) or 1.0
    return (r - m) / s


def fm_buggy(df: pd.DataFrame, days: list, xcol: str, ycol: str,
             lock_col: str | None, ctrl_cols: tuple[str, ...] = (),
             extra_filter_col: str | None = None):
    co = []
    for d in days:
        day = df[df["d"] == d]
        if lock_col is not None:
            day = day[~day[lock_col].astype(bool)]
        if extra_filter_col is not None:
            day = day[day[extra_filter_col].astype(bool)]
        if len(day) < 15:
            continue
        y = day[ycol].to_numpy(dtype=float)
        xcols = [_zr_buggy(day[xcol].to_numpy(dtype=float))]
        xcols += [_zr_buggy(day[c].to_numpy(dtype=float)) for c in ctrl_cols]
        X = np.column_stack([np.ones(len(y))] + xcols)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        co.append(beta[1])
    return _summarize(co)


def _summarize(co: list[float]):
    if len(co) < 30:
        return float("nan"), float("nan"), len(co)
    co = np.array(co)
    m = co.mean()
    t = m / (co.std(ddof=1) / math.sqrt(len(co)))
    return m, t, len(co)


# --------------------------------------------------------------------------
# 迴歸：修正版（rank/z 用共用函式庫對「當天完整 bd[d]」算一次，鎖死/has_inst
# 只在算完之後拿來切 tradable 子集）
# --------------------------------------------------------------------------
def add_rank_z(df: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """兩段式呼叫 cross_sectional_then_filter：先算 {col}_rank（全宇宙百分位排名），
    再對 {col}_rank 欄位算一次 zscore，得到 {col}_rank_z —— 逐位等價原始
    `zr(rank(vals))`（只有 std 分母 population vs sample 的可忽略差異，見檔頭說明）。
    """
    full1, _ = cross_sectional_then_filter(df, "d", value_cols, lock_col=None, methods=("rank",))
    rank_cols = [f"{c}_rank" for c in value_cols]
    full2, _ = cross_sectional_then_filter(full1, "d", rank_cols, lock_col=None, methods=("zscore",))
    return full2


def fm_fixed(df_stats: pd.DataFrame, days: list, xcol: str, ycol: str,
             lock_col: str | None, ctrl_cols: tuple[str, ...] = (),
             extra_filter_col: str | None = None):
    co = []
    xz = f"{xcol}_rank_z"
    ctrl_z = [f"{c}_rank_z" for c in ctrl_cols]
    for d in days:
        day = df_stats[df_stats["d"] == d]
        mask = pd.Series(True, index=day.index)
        if lock_col is not None:
            mask &= ~day[lock_col].astype(bool)
        if extra_filter_col is not None:
            mask &= day[extra_filter_col].astype(bool)
        day_use = day[mask]
        if len(day_use) < 15:
            continue
        y = day_use[ycol].to_numpy(dtype=float)
        xcols = [day_use[xz].to_numpy(dtype=float)]
        xcols += [day_use[c].to_numpy(dtype=float) for c in ctrl_z]
        X = np.column_stack([np.ones(len(y))] + xcols)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        co.append(beta[1])
    return _summarize(co)


def sig(t: float) -> str:
    if t != t:
        return "n/a"
    return "顯著|t|>=2" if abs(t) >= 2 else "不顯著"


def main():
    raw = pickle.load(open(DATA_DIR / "cache" / "pit_universe_tick" / "_flow_panel.pkl", "rb"))["rows"]
    sign, inst = load_sign_and_inst(raw)
    events = build_events(raw, sign, inst)
    df = pd.DataFrame(events)

    day_counts = df.groupby("d").size()
    days = sorted(day_counts[day_counts >= 20].index)
    df = df[df["d"].isin(days)].reset_index(drop=True)
    print(f"\n[事實] 樣本：{len(days)} 日 / {len(df):,} 檔日  ({days[0]}~{days[-1]})")
    print(f"[事實] 進場端鎖死率：今日收盤 {df['lock_close'].mean()*100:.1f}% / "
          f"次日開盤 {df['lock_open1'].mean()*100:.1f}%\n")

    value_cols = [f"A_{c}" for c in CUTS] + ["inst", "ret0", "Y_close2open1"]
    df_stats = add_rank_z(df, value_cols)

    TARGETS = [
        ("① 截點→今日收盤", lambda c: f"Y_cut2close_{c}", None, "無"),
        ("② 今日收盤→次日開盤", lambda c: "Y_close2open1", "lock_close", "剔今收鎖死"),
        ("③ 次日開盤→次日收盤", lambda c: "Y_open12close1", "lock_open1", "剔次開鎖死"),
        ("④ 次日開盤→次次日開盤", lambda c: "Y_open12open2", "lock_open1", "剔次開鎖死"),
        ("⑤ 今日收盤→次日收盤（全程）", lambda c: "Y_close2close1", "lock_close", "剔今收鎖死"),
    ]

    print("=" * 118)
    print("表 1：盤中目標矩陣 —— 原始寫法重現（bug：keep() 先過濾才算 rank/z） vs 修正版（全宇宙算 rank/z，最後才剔鎖死）")
    print("=" * 118)
    header = f"{'口徑':<26}{'cut':>6}   {'原始bps':>10}{'t':>7}   |   {'修正bps':>10}{'t':>7}   {'n(原始/修正)':>14}   顯著性/方向變化   濾網"
    print(header)
    rows_for_answer = {}
    for lbl, ycol_fn, lock_col, filt in TARGETS:
        for c in CUTS:
            ycol = ycol_fn(c)
            xcol = f"A_{c}"
            m_o, t_o, n_o = fm_buggy(df, days, xcol, ycol, lock_col)
            m_f, t_f, n_f = fm_fixed(df_stats, days, xcol, ycol, lock_col)
            flip = "翻號" if (m_o == m_o and m_f == m_f and m_o * m_f < 0) else ""
            cross = "跨顯著邊界" if sig(t_o) != sig(t_f) else ""
            note = "/".join(x for x in (flip, cross) if x) or "一致"
            print(f"{lbl:<26}{CUT_LABEL[c]:>6}   {m_o:+10.1f}{t_o:+7.2f}   |   "
                  f"{m_f:+10.1f}{t_f:+7.2f}   {n_o:>6}/{n_f:<6}   {note:<14}   {filt}")
            if lbl.startswith("②") and c == "1300":
                rows_for_answer["target2_1300"] = (m_o, t_o, m_f, t_f)
    print(f"\n成本地板 = {COST} bps（雙邊）。上表為毛值。")

    # -----------------------------------------------------------------
    # 口徑③ 加控制（原始 step5.py 第二段）
    # -----------------------------------------------------------------
    print("\n" + "=" * 118)
    print("表 2：口徑③（唯一避開鎖死的可執行口徑）加控制 —— 原始 vs 修正")
    print("=" * 118)
    ctrl_variants = [
        ("裸", ()),
        ("控當日報酬", ("ret0",)),
        ("控當日報酬＋今晨跳空", ("ret0", "Y_close2open1")),
    ]
    for lbl, ctrl in ctrl_variants:
        m_o, t_o, n_o = fm_buggy(df, days, "A_1300", "Y_open12close1", "lock_open1", ctrl)
        m_f, t_f, n_f = fm_fixed(df_stats, days, "A_1300", "Y_open12close1", "lock_open1", ctrl)
        flip = "翻號" if (m_o == m_o and m_f == m_f and m_o * m_f < 0) else ""
        cross = "跨顯著邊界" if sig(t_o) != sig(t_f) else ""
        note = "/".join(x for x in (flip, cross) if x) or "一致"
        print(f"  {lbl:<24}原始 {m_o:+7.1f} bps (t={t_o:5.2f}, n={n_o})   |   "
              f"修正 {m_f:+7.1f} bps (t={t_f:5.2f}, n={n_f})   {note}")

    # -----------------------------------------------------------------
    # 賽馬：口徑③ 上「大戶流 vs 三大法人」（原始 step5.py 第三段）
    # -----------------------------------------------------------------
    print("\n" + "=" * 118)
    print("表 3：賽馬 —— 口徑③ 上『大戶流 vs 三大法人』誰有增量 —— 原始 vs 修正")
    print("=" * 118)
    race_variants = [
        ("大戶流 單獨", "A_1300", ()),
        ("三大法人 單獨", "inst", ()),
        ("大戶流（控法人）", "A_1300", ("inst",)),
        ("三大法人（控大戶流）", "inst", ("A_1300",)),
    ]
    for lbl, xcol, ctrl in race_variants:
        m_o, t_o, n_o = fm_buggy(df, days, xcol, "Y_open12close1", "lock_open1", ctrl, extra_filter_col="has_inst")
        m_f, t_f, n_f = fm_fixed(df_stats, days, xcol, "Y_open12close1", "lock_open1", ctrl, extra_filter_col="has_inst")
        flip = "翻號" if (m_o == m_o and m_f == m_f and m_o * m_f < 0) else ""
        cross = "跨顯著邊界" if sig(t_o) != sig(t_f) else ""
        note = "/".join(x for x in (flip, cross) if x) or "一致"
        print(f"  {lbl:<24}原始 {m_o:+7.1f} bps (t={t_o:5.2f}, n={n_o})   |   "
              f"修正 {m_f:+7.1f} bps (t={t_f:5.2f}, n={n_f})   {note}")

    # -----------------------------------------------------------------
    # 重點回答：target②（原 +27.7bps/t=9.2，筆記標記「最接近可行」）
    # -----------------------------------------------------------------
    print("\n" + "=" * 118)
    print("[事實] 重點回答：target② @13:00 修正前後對照")
    print("=" * 118)
    if "target2_1300" in rows_for_answer:
        m_o, t_o, m_f, t_f = rows_for_answer["target2_1300"]
        print(f"  原始（重現）：{m_o:+.1f} bps, t={t_o:.2f}")
        print(f"  修正後　　　：{m_f:+.1f} bps, t={t_f:.2f}")
        print(f"  相對成本地板 {COST} bps：原始 {'超過' if abs(m_o) > COST else '未超過'} 成本，"
              f"修正後 {'超過' if abs(m_f) > COST else '未超過'} 成本")


if __name__ == "__main__":
    main()
