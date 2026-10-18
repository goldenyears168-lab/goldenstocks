"""連次/連量 重建（反向工程「盤中當沖神器」）— 共用函式。

連次 = 連續 5 秒窗內「外盤量 > 內盤量」(正) / 「內盤量 > 外盤量」(負) 的窗數；平手或反向中斷；無成交窗跳過。
連量 = 該連續段內主導方向的成交量（張），再除以該股 PIT 參考「每分鐘均量」→ 換算成「幾分鐘的成交量」。
內外盤判定：成交價 ≥ ask → 外盤；≤ bid → 內盤；其餘用 tick rule（對前一筆連續盤成交價），同價承襲前一方向。
"""
from __future__ import annotations
import pickle
from pathlib import Path
import numpy as np

BIN = 5.0
OPEN_T, CLOSE_T = 9 * 3600, 13 * 3600 + 25 * 60   # 排除收盤競價前 5 分


def classify(a: np.ndarray) -> np.ndarray:
    """a: [t,price,dv,bid,ask,flag] → side(+1 外 / -1 內 / 0 未知)，只對連續盤有意義。"""
    px, bid, ask = a[:, 1], a[:, 3].copy(), a[:, 4].copy()
    bid[bid <= 0] = np.nan; ask[ask <= 0] = np.nan   # 0 = 無報價（鎖漲跌停/試撮），不是價格
    # forward-fill 最近已知的 bid/ask（單邊報價缺值）
    for q in (bid, ask):
        m = np.isnan(q)
        if m.any():
            idx = np.where(~m, np.arange(len(q)), 0)
            np.maximum.accumulate(idx, out=idx)
            q[:] = np.where(np.isnan(q[idx]) & m, np.nan, q[idx])
    side = np.zeros(len(a), dtype=np.int8)
    side[px >= ask] = 1
    side[(px <= bid) & (side == 0)] = -1
    # tick rule for the rest
    prev_px, prev_side = np.nan, 0
    for i in range(len(a)):
        if a[i, 5] == 1:
            continue
        if side[i] == 0:
            if px[i] > prev_px:
                side[i] = 1
            elif px[i] < prev_px:
                side[i] = -1
            else:
                side[i] = prev_side
        prev_px = px[i]
        if side[i] != 0:
            prev_side = side[i]
    side[a[:, 5] == 1] = 0
    return side, bid, ask


def load_day(panel_dir: Path, date: str):
    pan = pickle.load(open(Path(panel_dir) / f"{date}.pkl", "rb"))
    for a in pan.values():
        a[0, 5] = 1     # 當日第一筆 Δvolume＝開盤競價彙總量（常不帶 isOpen 旗標）→ 視同競價，與 trade_ingest 的 first_done 一致
    return pan


def bins(a: np.ndarray, side: np.ndarray):
    """→ DataFrame-like arrays per 5s bin（只含有連續盤成交的窗）。"""
    m = (a[:, 5] == 0) & (a[:, 0] >= OPEN_T) & (a[:, 0] <= CLOSE_T)
    t, px, dv, sd = a[m, 0], a[m, 1], a[m, 2], side[m]
    if len(t) == 0:
        return None
    b = np.floor(t / BIN).astype(np.int64)
    ub, first = np.unique(b, return_index=True)
    out = {"b": ub, "o": [], "i": [], "last": [], "hi": [], "lo": [], "first": [], "n": []}
    ends = list(first[1:]) + [len(t)]
    for s, e in zip(first, ends):
        w = slice(s, e)
        out["o"].append(dv[w][sd[w] == 1].sum()); out["i"].append(dv[w][sd[w] == -1].sum())
        out["last"].append(px[e - 1]); out["hi"].append(px[w].max()); out["lo"].append(px[w].min())
        out["first"].append(px[s]); out["n"].append(e - s)
    return {k: np.asarray(v) for k, v in out.items()}


def streaks(bn: dict):
    """逐窗滾動連次/連量。回傳 per-bin：dir、n、vol_dom、px_start（段起點價）、px_peak（段內最高/最低價）。"""
    n_b = len(bn["b"])
    dr = np.sign(bn["o"] - bn["i"]).astype(int)
    S = {k: np.zeros(n_b) for k in ("n", "vol", "px0", "ext")}
    cur_d, n, vol, px0, ext = 0, 0, 0.0, np.nan, np.nan
    for k in range(n_b):
        d = dr[k]
        if d != 0 and d == cur_d:
            n += 1; vol += bn["o"][k] if d > 0 else bn["i"][k]
            ext = max(ext, bn["hi"][k]) if d > 0 else min(ext, bn["lo"][k])
        elif d != 0:
            cur_d, n = d, 1
            vol = bn["o"][k] if d > 0 else bn["i"][k]
            px0 = bn["first"][k]; ext = bn["hi"][k] if d > 0 else bn["lo"][k]
        else:
            cur_d, n, vol = 0, 0, 0.0
        S["n"][k], S["vol"][k], S["px0"][k], S["ext"][k] = n * (d if d else 0), vol, px0, ext
    return dr, S
