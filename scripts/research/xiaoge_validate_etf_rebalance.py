"""小哥 ETF 換股尾盤 (R40-R45/V6) 驗證：本機只有主動式 ETF 持股 -> 代理檢定。唯讀 DB。"""
import sqlite3, os, sys, math, collections, statistics as st
DB = "file:%s/goldenstocks-data/data/stocks.db?mode=ro" % os.path.expanduser("~")
con = sqlite3.connect(DB, uri=True)
COST = 0.0044
H = [1, 5]  # 持有交易日數：T+1 開盤進，T+1+h 開盤出
rows = con.execute("select etf_code,snapshot_date,stock_id from etf_holdings where stock_id glob '[1-9][0-9][0-9][0-9]'").fetchall()
hold = collections.defaultdict(lambda: collections.defaultdict(set))
for e, d, s in rows: hold[e][d].add(s)
px = {}
for sid, d, o, c in con.execute("select stock_id,trade_date,open,close from stock_daily_bars where source='twse_mi_index' and trade_date>='2025-04-01' and open>0"):
    px.setdefault(sid, {})[d] = (o, c)
cal = sorted({d for d in px.get('0050', {})})
idx = {d: i for i, d in enumerate(cal)}
events = {}  # (kind,stock,date)->set(etf)
for e, snaps in hold.items():
    ds = sorted(snaps)
    for i in range(20, len(ds)):
        d, p = ds[i], ds[i - 1]
        if (idx.get(d, 0) - idx.get(p, 0)) not in (1, 2, 3) and d in idx and p in idx and idx[d]-idx[p] > 3: continue
        prev20 = set().union(*[snaps[x] for x in ds[i - 20:i]])
        prev5 = set.intersection(*[snaps[x] for x in ds[i - 5:i]])
        for s in snaps[d] - prev20: events.setdefault(('納入', s, d), set()).add(e)
        for s in prev5 - snaps[d]: events.setdefault(('剔除', s, d), set()).add(e)
def ret(sid, T, h):
    # 訊號日T(snapshot) -> 進 T+1 開盤，出 T+1+h 開盤
    if T not in idx: return None
    i = idx[T]
    if i + 1 + h >= len(cal): return None
    a, b = px.get(sid, {}).get(cal[i + 1]), px.get(sid, {}).get(cal[i + 1 + h])
    m0, m1 = px['0050'].get(cal[i + 1]), px['0050'].get(cal[i + 1 + h])
    if not (a and b and m0 and m1): return None
    return b[0] / a[0] - 1, m1[0] / m0[0] - 1
def tstat(xs):
    n = len(xs)
    if n < 3: return float('nan')
    sd = st.stdev(xs)
    return st.mean(xs) / (sd / math.sqrt(n)) if sd > 0 else float('nan')
out = []
SPLIT = None
alld = sorted({k[2] for k in events})
SPLIT = alld[len(alld) // 2]
out.append("SPLIT(IS<=,OOS>)=%s" % SPLIT)
for kind in ('納入', '剔除'):
    for h in H:
        for part in ('ALL', 'IS', 'OOS'):
            bydate = collections.defaultdict(list); n = 0; raw = []
            for (k, s, d), es in events.items():
                if k != kind: continue
                if part == 'IS' and d > SPLIT: continue
                if part == 'OOS' and d <= SPLIT: continue
                r = ret(s, d, h)
                if not r: continue
                ex = r[0] - r[1] - COST
                bydate[d].append(ex); raw.append(ex); n += 1
            dm = [st.mean(v) for v in bydate.values()]
            if n == 0: out.append(f"{kind} h={h} {part} n=0"); continue
            out.append(f"{kind} h={h} {part} n={n} dates={len(dm)} med={st.median(raw)*100:.2f}% mean={st.mean(raw)*100:.2f}% win={sum(x>0 for x in raw)/n*100:.0f}% t_date={tstat(dm):.2f}")
out.append("events: 納入=%d 剔除=%d" % (sum(k[0]=='納入' for k in events), sum(k[0]=='剔除' for k in events)))
kb = con.execute("select min(trade_date),max(trade_date) from stock_kbar_5m").fetchone()
ov = sum(1 for (k, s, d) in events if kb[0] <= d <= kb[1] and con.execute("select 1 from stock_kbar_5m where stock_id=? and trade_date=? limit 1", (s, d)).fetchone())
out.append(f"kbar_5m {kb} 與事件重疊(含尾盤資料)={ov}")
print("\n".join(out))
