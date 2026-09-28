"""大戶−散戶淨買正規化：前視偏誤修正重驗（獨立重跑 v2）。

背景
----
稽核發現原始一次性分析（session heredoc，未存檔）在做橫斷面正規化
（z-score / demean）時，樣本基準是「當天收盤已經剔除鎖漲停股票之後
剩下的股票」，而不是固定的完整原始宇宙——基準組成取決於「今天誰鎖死
了」這個要收盤才確定的資訊，構成前視偏誤（look-ahead bias）。

原始程式碼（從 session transcript
`~/.claude/projects/-Users-jackm4-goldenstocks/c1eb2de7-71ee-517d-870a-128c1b9bc0db.jsonl`
第 14826 行逐字核對回來，未經轉述）的關鍵瑕疵位置：

    if abs(chg) >= 0.095: continue   # chg = 今天收盤/昨收 - 1

這行在建立 `recs`（逐檔逐日觀測）時，直接把「今天觸及漲跌停」的股票整
筆丟掉——不只是不能拿來當交易標的，而是連當天用來算其他股票 z-score
的「同儕母體」都被排除掉了。既然母體會隨著「誰鎖死」而變動，z-score
的均值/標準差基準就吃到了收盤後才知道的資訊。

修正邏輯（本檔案）
------------------
1. 母體（用來算 z-score / demean 的橫斷面）＝固定原始宇宙（
   `_live_calib.json` 的 42 檔個股期貨標的）當天 tot_amt>0 的「全部」
   股票，不論當天鎖不鎖死。
2. 鎖死股（|chg|>=9.5%）只在最後決定「這筆觀測能不能真的拿來估係數
   （當作可交易的訊號-報酬配對）」那一步才剔除；z-score 的均值/標準
   差、以及應變數（隔夜／1日報酬）demean 的基準，一律用剔除鎖死股「之
   前」的完整當日母體計算。
3. 次日跳空 >15%（|ov|>1500bps）過濾維持原始程式碼寫法（在建立
   `recs` 時就丟棄），因為稽核指出的 bug 明確是「今天鎖死」這一項，
   這個過濾器是另一個獨立的資料品質過濾，不在稽核範圍內、不自創新方
   法去動它，以確保新舊版本只有「一個」受控變因（鎖死股要不要留在
   z-score 母體），其餘完全一致，才能做乾淨的 A/B 對照。

除數候選清單、目標變數定義（隔夜 ov、1 日 d1）、回歸設計（逐日橫斷面
z-score 後跑 OLS，取每日係數再對日期做 t 檢定；控制變數用同樣方式
z-score 後一起塞進設計矩陣）完全比照原始程式碼，未新增或刪除除數候選。

用法：
    PYTHONPATH=src .venv/bin/python scripts/research/biglot_diff_normalization_refit_v2.py
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from stock_db import DATA_DIR  # noqa: E402

BASE = "/Users/jackm4/goldenstocks-data/scratch/biglot_panels"

# 原始一次性分析跑於 2026-09-24T04:37（session transcript 第 14826/14830 行），
# 當時 `_live_calib.json` 的個股期貨宇宙是 36 檔。該檔案已於 2026-09-25 00:03
# 被覆寫成 42 檔（新增 3008/3653/6223/2383/3017/3081，檔名 add6），覆寫前的快照
# 備份在 `_live_calib.json.bak-20260925-add6`。用「現在」的 42 檔宇宙重跑會把
# 「方法論修正」的效果和「宇宙組成漂移」的效果混在一起，無法乾淨對照，所以這裡
# 一律用凍結的 36 檔快照——這也已實測驗證：用這個快照重放「原始寫法」可以
# 逐位數重現筆記/session 報告過的所有數字（14.9/12.3/4.3/15.5/10.0/14.4 與
# -41.4/8.7/9.0/13.8），確認就是原始分析當時吃到的宇宙。
FROZEN_CALIB = Path(
    "/Users/jackm4/goldenstocks-data/data/cache/pit_universe_tick/"
    "_live_calib.json.bak-20260925-add6"
)
LIVE_CALIB = DATA_DIR / "cache" / "pit_universe_tick" / "_live_calib.json"


def load_recs(calib_path: Path = FROZEN_CALIB):
    """重建 session 中的逐檔逐日觀測，保留所有欄位供新舊兩種正規化基準使用。"""
    cal = json.loads(calib_path.read_text())
    sids = {r["sid"] for r in cal["universe"]}

    big1000 = defaultdict(float)
    for r in csv.DictReader(open(f"{BASE}/pit100_bucket5_dual_2026-03_08.csv")):
        big1000[(r["sid"], r["date"])] += float(r["big1000"])

    day = {}
    for r in csv.DictReader(open(f"{BASE}/pit100_daily_panel_2026-03_08.csv")):
        k = (r["sid"], r["date"])
        tot = float(r["tot_amt"])
        if r["sid"] not in sids or tot <= 0 or k not in big1000:
            continue
        day[k] = dict(
            big=big1000[k],
            ret=float(r["ret_net"]),
            tot=tot,
            o=float(r["open_px"]),
            c=float(r["close_px"]),
        )

    by = defaultdict(list)
    for (s, d) in day:
        by[s].append(d)

    recs = []
    for s, ds in by.items():
        ds = sorted(ds)
        for i, d in enumerate(ds):
            if i < 20 or i + 1 >= len(ds):
                continue
            v = day[(s, d)]
            n = day[(s, ds[i + 1])]
            pc = day[(s, ds[i - 1])]["c"]
            if not (v["o"] and v["c"] and n["o"] and pc):
                continue
            chg = v["c"] / pc - 1
            avg20 = np.mean([day[(s, x)]["tot"] for x in ds[i - 20:i]])
            ov = (n["o"] / v["c"] - 1) * 1e4
            # 次日跳空過濾：與原始程式碼一致，維持在 recs 建立階段就丟棄
            # （非本次稽核修正範圍，見檔頭說明）。
            if abs(ov) > 1500:
                continue
            diff = v["big"] - v["ret"]
            recs.append(dict(
                d=d,
                ov=ov,
                d1=(n["c"] / v["c"] - 1) * 1e4,
                bigp=v["big"] / v["tot"],
                diffp=diff / v["tot"],
                retp=v["ret"] / v["tot"],
                big_avg=v["big"] / avg20,
                diff_avg=diff / avg20,
                diff_abs=diff / 1e8,
                tot_rel=v["tot"] / avg20,
                chg=chg,
                locked_today=abs(chg) >= 0.095,  # 鎖漲跌停旗標，只在最後一步用
            ))
    return recs, sids


def zc(x):
    x = np.array(x, float)
    sd = x.std()
    return (x - x.mean()) / (sd or 1)


def fm_original(recs, sig, target, ctrl=()):
    """原始（有 bug）寫法：鎖死股在建立當日母體「之前」就已被排除。

    等價於：先把 recs 依 `not locked_today` 過濾，才分日算 z-score。
    """
    filtered = [r for r in recs if not r["locked_today"]]
    byd = defaultdict(list)
    for r in filtered:
        byd[r["d"]].append(r)
    cs = []
    for d, rows in byd.items():
        if len(rows) < 12:
            continue
        Y = np.array([r[target] for r in rows])
        Y = Y - Y.mean()
        X = np.column_stack(
            [zc([r[sig] for r in rows])] + [zc([r[c] for r in rows]) for c in ctrl]
        )
        b, *_ = np.linalg.lstsq(X, Y, rcond=None)
        cs.append(b[0])
    a = np.array(cs)
    return a.mean(), a.mean() / (a.std(ddof=1) / math.sqrt(len(a))), len(a)


def fm_fixed(recs, sig, target, ctrl=()):
    """修正寫法：z-score / demean 基準＝當天完整母體（含鎖死股）；

    鎖死股只在最後組回歸樣本（X, Y 配對）那一步才剔除。
    """
    byd = defaultdict(list)
    for r in recs:
        byd[r["d"]].append(r)
    cs = []
    for d, rows in byd.items():
        if len(rows) < 12:
            continue
        # 完整母體（含鎖死股）算 z-score 與 demean 基準
        z_sig_full = zc([r[sig] for r in rows])
        z_ctrl_full = [zc([r[c] for r in rows]) for c in ctrl]
        y_full = np.array([r[target] for r in rows])
        y_mean_full = y_full.mean()

        usable_idx = [i for i, r in enumerate(rows) if not r["locked_today"]]
        if len(usable_idx) < 12:
            continue
        Y = y_full[usable_idx] - y_mean_full
        X = np.column_stack(
            [z_sig_full[usable_idx]] + [zc_c[usable_idx] for zc_c in z_ctrl_full]
        )
        b, *_ = np.linalg.lstsq(X, Y, rcond=None)
        cs.append(b[0])
    a = np.array(cs)
    return a.mean(), a.mean() / (a.std(ddof=1) / math.sqrt(len(a))), len(a)


MAIN_SIGNALS = (
    ("大戶淨÷成交額(現欄 大戶佔比)", "bigp"),
    ("(大戶−散戶)÷成交額", "diffp"),
    ("散戶淨÷成交額", "retp"),
    ("大戶淨÷20日均額", "big_avg"),
    ("(大戶−散戶)÷20日均額", "diff_avg"),
    ("大戶−散戶 絕對額(億)", "diff_abs"),
)

CTRL_SIGNALS = (
    ("(大戶−散戶)÷成交額 | 控大戶佔比", "diffp"),
    ("散戶淨÷成交額 | 控大戶佔比", "retp"),
    ("大戶÷20日均額 | 控大戶佔比", "big_avg"),
    ("成交÷20日均額(量能) | 控大戶佔比", "tot_rel"),
)


def sig_flag(t):
    return "顯著|t|>=2" if abs(t) >= 2 else "不顯著"


def run_report(calib_path: Path, label: str):
    recs, sids = load_recs(calib_path)
    print(f"[事實] {label}：固定宇宙股數 = {len(sids)}（來源 {calib_path.name}）")
    print(f"[事實] {label}：股-日觀測數（去掉次日跳空>15%，未剔除鎖死股）= {len(recs)}")
    n_locked = sum(1 for r in recs if r["locked_today"])
    print(f"[事實] {label}：其中「當天鎖漲跌停」(|chg|>=9.5%) 的股-日數 = {n_locked} "
          f"({n_locked/len(recs)*100:.2f}%)")
    print()

    print("=" * 100)
    print("表 1：主效果 —— 原始寫法（bug：鎖死股先剔除再算z-score） vs 修正寫法（全母體z-score，最後才剔鎖死股）")
    print("=" * 100)
    header = (
        f"{'訊號(z,日切面)':30s}"
        f"{'原始 隔夜bps':>13s}{'t':>7s}{'原始 1日bps':>12s}{'t':>7s}   |   "
        f"{'修正 隔夜bps':>13s}{'t':>7s}{'修正 1日bps':>12s}{'t':>7s}   顯著性變化"
    )
    print(header)
    rows_summary = []
    for nm, sig in MAIN_SIGNALS:
        m_o, t_o, n_o = fm_original(recs, sig, "ov")
        m1_o, t1_o, _ = fm_original(recs, sig, "d1")
        m_f, t_f, n_f = fm_fixed(recs, sig, "ov")
        m1_f, t1_f, _ = fm_fixed(recs, sig, "d1")
        flip = "***翻號***" if (m_o * m_f < 0) else ""
        cross = "***跨越顯著邊界***" if (sig_flag(t_o) != sig_flag(t_f)) else ""
        note = " ".join(x for x in (flip, cross) if x) or "一致"
        print(
            f"{nm:30s}{m_o:+13.1f}{t_o:+7.2f}{m1_o:+12.1f}{t1_o:+7.2f}   |   "
            f"{m_f:+13.1f}{t_f:+7.2f}{m1_f:+12.1f}{t1_f:+7.2f}   {note}"
        )
        rows_summary.append((nm, sig, m_o, t_o, m_f, t_f))
    print(f"(原始版日數 n≈{n_o}；修正版日數 n≈{n_f})")
    print()

    print("=" * 100)
    print("表 2：增量檢定（控制大戶佔比 bigp 後）—— 目標＝隔夜報酬 ov")
    print("=" * 100)
    header2 = (
        f"{'訊號 | 控制':40s}{'原始 bps':>10s}{'t':>7s}{'n':>5s}   |   "
        f"{'修正 bps':>10s}{'t':>7s}{'n':>5s}   顯著性變化"
    )
    print(header2)
    for nm, sig in CTRL_SIGNALS:
        m_o, t_o, n_o = fm_original(recs, sig, "ov", ctrl=("bigp",))
        m_f, t_f, n_f = fm_fixed(recs, sig, "ov", ctrl=("bigp",))
        flip = "***翻號***" if (m_o * m_f < 0) else ""
        cross = "***跨越顯著邊界***" if (sig_flag(t_o) != sig_flag(t_f)) else ""
        note = " ".join(x for x in (flip, cross) if x) or "一致"
        print(
            f"{nm:40s}{m_o:+10.1f}{t_o:+7.2f}{n_o:5d}   |   "
            f"{m_f:+10.1f}{t_f:+7.2f}{n_f:5d}   {note}"
        )

    print()
    print("=" * 100)
    print(f"[事實] {label}：重點回答（見上方表 1/表 2 的『顯著性變化』欄，判定門檻 |t|>=2）")
    print("=" * 100)
    print()


def main():
    print("#" * 100)
    print("# 主報告：凍結宇宙快照(36檔) —— 與原始 2026-09-24 分析同一份宇宙，乾淨 A/B 對照")
    print("#" * 100)
    run_report(FROZEN_CALIB, "凍結36檔宇宙(原始分析當時的宇宙)")

    print()
    print("#" * 100)
    print("# 附錄：現行宇宙快照(42檔，_live_calib.json 已於2026-09-25被覆寫新增6檔)")
    print("# 僅作穩健性檢查——此表的原始vs修正差異同時混雜了宇宙組成漂移，")
    print("# 不應拿來跟筆記原始數字逐位比對，只看『顯著性有沒有跨越邊界』這個方向是否一致。")
    print("#" * 100)
    run_report(LIVE_CALIB, "現行42檔宇宙(穩健性檢查)")


if __name__ == "__main__":
    main()
