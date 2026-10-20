"""隔日沖慣性：階段 1 的「事前已知」計數（searchsorted＋cumsum）與暴力法逐筆比對；定義邊界釘樁。"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import xiaoge_flip_habit_events as m  # noqa: E402


def make_cal(n=260):
    d, out = date(2025, 1, 2), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class FlipHabitTest(unittest.TestCase):
    def test_prior_counts_match_bruteforce(self):
        rng = np.random.default_rng(7)
        cal = make_cal()
        rows = []
        for sid in ("1101", "2330"):
            for br in ("A", "B", "C"):
                for i in sorted(rng.choice(len(cal) - 2, 60, replace=False)):
                    net = float(rng.integers(100, 1000))
                    rows.append(dict(stock_id=sid, br=br, idx=int(i), net=net, vol=net * 20, rank=1.0,
                                     buy_n=0.0, sell_n=net * float(rng.choice([0.0, 0.5, 1.0, 1.5]))))
        ev = pd.DataFrame(rows).sort_values(["stock_id", "br", "idx"]).reset_index(drop=True)
        out = m.features(ev, cal)
        tord = {i: date.fromisoformat(d).toordinal() for i, d in enumerate(cal)}
        for k in rng.choice(len(out), 200, replace=False):
            r = out.iloc[k]
            same = out[(out.stock_id == r.stock_id) & (out.br == r.br) & out.in_p]
            known = same[(same.idx <= r.idx - 2) & (same.idx.map(tord) >= tord[r.idx] - 90)]
            if not r.in_p:
                continue
            self.assertEqual(int(r["n90_p"]), len(known))
            self.assertEqual(int(r["A70_90_p"]), int(known["fA70"].sum()))
            self.assertEqual(int(r["B70_90_p"]), int(known["fB70"].sum()))

    def test_flip_definitions(self):
        cal = make_cal(10)
        ev = pd.DataFrame([
            dict(stock_id="1", br="A", idx=1, net=100.0, vol=1000.0, rank=1.0, buy_n=300.0, sell_n=400.0),    # 真的轉賣超
            dict(stock_id="1", br="B", idx=1, net=100.0, vol=1000.0, rank=1.0, buy_n=1000.0, sell_n=1000.0),  # 買賣都大、淨額 0
            dict(stock_id="1", br="C", idx=1, net=100.0, vol=1000.0, rank=1.0, buy_n=0.0, sell_n=50.0),       # 只賣一半
        ])
        out = m.features(ev, cal).set_index("br")
        self.assertTrue(out.loc["A", "fA70"] and out.loc["A", "fB70"])
        self.assertTrue(out.loc["B", "fA70"] and not out.loc["B", "fB70"])
        self.assertFalse(out.loc["C", "fA70"] or out.loc["C", "fB70"])
        self.assertTrue(out.loc["C", "fA50"])                              # 50 ≥ 0.5×100


if __name__ == "__main__":
    unittest.main()
