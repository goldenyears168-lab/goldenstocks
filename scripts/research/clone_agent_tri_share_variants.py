#!/usr/bin/env python3
"""研究用(預註冊,不得事後增減/調門檻):三尺度組合 H0 的「占比版」變體 R0/R1/R2,訊號層級(現貨 raw tape)60/90 分報酬。

共同條件: g0(距近30分高≤−0.5% ∧ 成交額pct≥30) + replay(09:30起、名單 13 檔、20 分鐘冷卻通過才啟動、價格帶)。
  R0 現行 H0:tri()(5分/30分用淨額符號 ≥0/<0,全日用 hits 文字的萬元)。
  R1 三尺度(5分/30分/全日)全用占比:大戶占比>0 且 散戶占比<0(嚴格;5分 amt5==0 者不通過;
     全日占比=Tape.flow(sid, now, None) 的 大戶淨/成交額、散戶淨/成交額,now=卡片 c["time"] 當下,flow 內已擋未來事件)。
  R2 R1 ∧ 各尺度大戶占比在「同 day+time 全部 23 檔卡片池」內排名前 50%(高者在前)∧ 散戶占比排名前 50%(越負越前)。
     排名實作(最簡): rank=1+池內嚴格較佳者數量(僅計該尺度占比有效者),pct=rank/有效檔數,須 ≤0.5。
對照: V0 現行寬門檻(PRED)與 V0★4。進場=卡片限價(假設成交,LAG=5秒),出場=現貨最後成交價,成本=期貨口徑,超額=名單其他標的等權。
用法: PYTHONPATH=src nice -n 10 .venv/bin/python scripts/research/clone_agent_tri_share_variants.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")
sys.path.insert(0, "scripts/research")
import clone_agent_tri_holdout as H  # noqa: E402  (只借 tri/g0/PRED/DAYRE/HOLD_DAYS;其 MODE/DAYS 不用)
import trader_signal_watch as w  # noqa: E402
from clone_agent.rules_version import load_rules, universe_sids  # noqa: E402

m = H.m
S = Path.home() / "goldenstocks-data/data/scratch"
OUT = S / "w5s"
OUT.mkdir(exist_ok=True)
SETS = {"HOLD": (H.HOLD_DAYS, S / "clone_agent_w5_hold"), "IS": (m.DAYS, S / "clone_agent_w5_nogate")}
HOLDS = (60, 90)
LAG = 5
SCALES = ("s5", "s30", "sd")


def ep(day, hms):
    return pd.Timestamp(f"{day} {hms}", tz="Asia/Taipei").timestamp()


def attach_shares(cards, tape, day):
    """為每張卡掛 c['_sh'] = {尺度: (大戶占比, 散戶占比)};無效=None。再掛 c['_rk'] = {(尺度,'b'/'r'): 池內排名百分位}。"""
    for c in cards:
        sid = str(c["sid"])
        f, fl = c["f5"], c["flow"]
        sh = {}
        sh["s5"] = (f["big5_share"], f["ret5_share"]) if f["amt5"] and f["big5_share"] is not None and f["ret5_share"] is not None else (None, None)
        sh["s30"] = (fl["big30"][0] / 100.0, fl["ret30"][0] / 100.0)
        a, b, r, _ = tape.flow(sid, ep(day, c["time"]), None)
        sh["sd"] = (b / a, r / a) if a > 0 else (None, None)
        c["_sh"] = sh
    by_t = defaultdict(list)
    for c in cards:
        by_t[c["time"]].append(c)
    for cs in by_t.values():
        for c in cs:
            c["_rk"] = {}
        for sc in SCALES:
            for j, kind in ((0, "b"), (1, "r")):
                vals = [(c["_sh"][sc][j], c) for c in cs if c["_sh"][sc][j] is not None]
                n = len(vals)
                for v, c in vals:
                    better = sum(1 for u, _ in vals if (u > v if kind == "b" else u < v))
                    c["_rk"][(sc, kind)] = (1 + better) / n
                for c in cs:
                    c["_rk"].setdefault((sc, kind), None)


def r1_fail_reasons(c):
    out = []
    for sc in SCALES:
        b, r = c["_sh"][sc]
        if b is None or r is None:
            out.append(f"{sc}:無效(amt=0)")
            continue
        if not b > 0:
            out.append(f"{sc}:大戶{'=0' if b == 0 else '<0'}")
        if not r < 0:
            out.append(f"{sc}:散戶{'=0' if r == 0 else '>0'}")
    return out


def r1(c):
    return not r1_fail_reasons(c)


def r2(c):
    if not r1(c):
        return False
    return all(c["_rk"][(sc, k)] is not None and c["_rk"][(sc, k)] <= 0.5 for sc in SCALES for k in ("b", "r"))


PREDS = {
    "V0 現行寬門檻": H.PRED["V0 現行寬門檻"],
    "R0 H0(淨額符號)": lambda c: H.tri(c) and H.g0(c),
    "R1 三尺度占比(嚴格)": lambda c: H.g0(c) and r1(c),
    "R2 R1∧池內前50%": lambda c: H.g0(c) and r2(c),
}


def main():
    R = load_rules()
    members = universe_sids(R)
    band = tuple(R["universe"]["price_band"])
    rng = np.random.default_rng(5)
    rows, fired_log, chk = [], [], []
    for mode, (days, src) in SETS.items():
        for d in days:
            cards = [json.loads(x) for x in (src / f"candidates_{d}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            tape = w.Tape(d)
            tape.poll()
            attach_shares(cards, tape, d)
            chk += [abs(c["_sh"]["sd"][0] * 100 - c["flow"]["big_day"][0]) for c in cards if c["_sh"]["sd"][0] is not None][:200]
            close = ep(d, "13:30:00")
            fired = {}
            for v, p in PREDS.items():
                m.old_ok = lambda c: True
                m.new_ok = lambda c, tag, p=p: p(c)
                fired[v] = m.replay(cards, ("X",), band, members)
            r1k = {(c["time"], str(c["sid"])) for c in fired["R1 三尺度占比(嚴格)"]}
            r0k = {(c["time"], str(c["sid"])) for c in fired["R0 H0(淨額符號)"]}
            r2k = {(c["time"], str(c["sid"])) for c in fired["R2 R1∧池內前50%"]}
            for c in fired["R0 H0(淨額符號)"]:
                k = (c["time"], str(c["sid"]))
                fired_log.append(dict(mode=mode, day=d, time=c["time"], sid=str(c["sid"]), in_R1_fired=k in r1k, in_R2_fired=k in r2k,
                                      R1_pred=r1(c), R2_pred=r2(c), reasons="|".join(r1_fail_reasons(c))))
            for k in r1k - r0k:
                fired_log.append(dict(mode=mode, day=d, time=k[0], sid=k[1], in_R1_fired=True, in_R2_fired=k in r2k, R1_pred=True,
                                      R2_pred=None, reasons="R1有R0無"))
            for v, f in fired.items():
                for tag, sel in ((v, f), (v + "★4" if v.startswith("V0") else None, [c for c in f if c["stars"] == 4])):
                    if tag is None:
                        continue
                    for c in sel:
                        for r in spot_rets(tape, members, d, close, c):
                            rows.append(dict(mode=mode, var=tag, day=d, sid=str(c["sid"]), time=c["time"], **r))
            # R0 預測式為真的全部卡(不經冷卻)的 60/90 報酬,供「同母體保留 vs 剔除」
            for c in fired["R0 H0(淨額符號)"]:
                for r in spot_rets(tape, members, d, close, c):
                    rows.append(dict(mode=mode, var="__R0pop__" + ("keep" if r1(c) else "drop"), day=d, sid=str(c["sid"]), time=c["time"], **r))
            print("done", mode, d, flush=True)
    print(f"[驗證] tape 全日大戶占比 vs 卡片 big_day 占比 平均絕對差 {np.mean(chk):.4f} pp(0 = 完全一致)")
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "share_variants_signal.csv", index=False)
    pd.DataFrame(fired_log).to_csv(OUT / "share_variants_r0_fired.csv", index=False)

    def ci(g, col):
        cl = (g.day + "_" + g.sid).values
        t = pd.DataFrame({"v": g[col].values, "c": cl}).groupby("c").v.agg(["sum", "count"])
        i = rng.integers(0, len(t), (2000, len(t)))
        mu = t["sum"].values[i].sum(1) / t["count"].values[i].sum(1)
        return np.percentile(mu, [5, 95])

    pd.set_option("display.width", 260, "display.max_columns", 40)
    allout = []
    for hold in HOLDS:
        for mode in SETS:
            out = []
            for v in dict.fromkeys(df["var"]):
                if v.startswith("__"):
                    continue
                g = df[(df["mode"] == mode) & (df["var"] == v) & (df.hold == hold)]
                if not len(g):
                    out.append(dict(變體=v, n=0))
                    continue
                lo, hi = ci(g, "gross")
                ge = g.dropna(subset=["excess"])
                elo, ehi = ci(ge, "excess") if len(ge) else (np.nan, np.nan)
                n, cl = len(g), (g.day + g.sid).nunique()
                verdict = "不足" if n < 30 else ("CI下界>0" if lo > 0 else "CI含0")
                out.append(dict(變體=v, n=n, 天=g.day.nunique(), 簇=cl, 勝率=round(100 * (g.net > 0).mean()), 毛bps=round(g.gross.mean(), 1),
                                毛CI=f"{lo:.0f}~{hi:.0f}", 淨bps=round(g.net.mean(), 1), 超額bps=round(ge.excess.mean(), 1) if len(ge) else np.nan,
                                超額CI=f"{elo:.0f}~{ehi:.0f}", 判=verdict))
                allout.append(dict(mode=mode, hold=hold, **out[-1]))
            print(f"\n===== {mode} 持有{hold}分(現貨訊號層級,假設以限價成交) =====")
            print(pd.DataFrame(out).to_string(index=False))
    pd.DataFrame(allout).to_csv(OUT / "share_variants_table.csv", index=False)

    fl = pd.DataFrame(fired_log)
    print("\n===== 觸發卡片重疊(以 replay 觸發集合) =====")
    for mode in SETS:
        f = fl[fl["mode"] == mode]
        r0 = f[f.reasons != "R1有R0無"]
        print(f"{mode}: R0觸發 {len(r0)} | R0∩R1觸發 {int(r0.in_R1_fired.sum())} | R0∩R2觸發 {int(r0.in_R2_fired.sum())} "
              f"| R1有而R0無 {int((f.reasons == 'R1有R0無').sum())}")
    print("\n===== R1 剔除原因(R0 觸發卡中 R1 謂詞不通過者;一卡可多因) =====")
    for mode in SETS:
        f = fl[(fl["mode"] == mode) & (fl.reasons != "R1有R0無")]
        drop = f[~f.R1_pred]
        cnt = defaultdict(int)
        for s in drop.reasons:
            for x in s.split("|"):
                cnt[x] += 1
        print(f"{mode}: R0 {len(f)} 張,R1謂詞剔除 {len(drop)} 張;原因 {dict(sorted(cnt.items(), key=lambda kv: -kv[1]))}")
    print("\n===== 同母體(R0 觸發卡)保留 vs 剔除:毛 bps =====")
    for hold in HOLDS:
        for mode in SETS:
            res = []
            for tag in ("keep", "drop"):
                g = df[(df["mode"] == mode) & (df["var"] == "__R0pop__" + tag) & (df.hold == hold)]
                res.append(f"{tag} n={len(g)} 毛{g.gross.mean():.1f}" if len(g) else f"{tag} n=0")
            print(f"{mode} 持有{hold}: " + " | ".join(res))


def spot_rets(tape, members, d, close, c):
    t0 = ep(d, c["time"]) + LAG
    L = c["price"]
    out = []
    for hold in HOLDS:
        t1 = min(t0 + hold * 60, close)
        px1, _ = tape.px_at(str(c["sid"]), t1)
        if px1 is None or t0 >= close:
            continue
        gross = 1e4 * (px1 / L - 1)
        notion = L * c["shares"]
        cost = (2 * 176 + 2e-5 * 2 * notion) / notion * 1e4
        bench = []
        for s in members:
            if s == str(c["sid"]):
                continue
            a, _ = tape.px_at(s, t0)
            b, _ = tape.px_at(s, t1)
            if a and b:
                bench.append(1e4 * (b / a - 1))
        out.append(dict(hold=hold, trunc=(t0 + hold * 60) > close, gross=gross, net=gross - cost, excess=gross - np.mean(bench) if bench else np.nan))
    return out


if __name__ == "__main__":
    main()
