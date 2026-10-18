"""小哥放空3步驟(R64)驗證。唯讀DB。定義見報告開頭(預註冊)。PYTHONPATH=src。"""
import sqlite3, sys, json
import numpy as np, pandas as pd
DB = "file:/Users/jackm4/goldenstocks-data/data/stocks.db?mode=ro"
COST = 0.0044
con = sqlite3.connect(DB, uri=True)
bars = pd.read_sql("""select stock_id,trade_date d,open,high,low,close,volume,source from stock_daily_bars
 where trade_date>='2024-11-01' and source in ('twse_mi_index','tpex_daily','finmind')""", con)
pri = {'twse_mi_index':0,'tpex_daily':1,'finmind':2}
bars['p'] = bars.source.map(pri)
bars = bars.sort_values(['stock_id','d','p']).drop_duplicates(['stock_id','d'])
sh = pd.read_sql("select stock_id,trade_date d,shares_issued s from stock_shareholding_daily where shares_issued>0 and trade_date>='2024-01-01'", con)
sh = sh.sort_values(['stock_id','d']).drop_duplicates(['stock_id','d'])
bench = bars[bars.stock_id=='0050'].set_index('d')
bars = bars[bars.stock_id.str.fullmatch(r'[1-9]\d{3}')]
bars = bars[(bars.volume>0)&bars.open.notna()&(bars.open>0)]
out=[]
for sid,g in bars.groupby('stock_id'):
    g=g.sort_values('d').reset_index(drop=True)
    if len(g)<40: continue
    g['pc']=g.close.shift(1); g['ret']=g.close/g.pc-1
    g['red']=(g.ret>=0.05)&(g.close>g.open)           # 大紅K
    g['lu']=g.ret>=0.095                               # 近漲停
    g['pressure']=(g.red&g.red.shift(1,fill_value=False)&(g.red.shift(2,fill_value=False)==False)|False)
    # 壓力: T-2,T-1 皆大紅K (T-3 不限)
    g['pressure']=g.red.shift(1,fill_value=False)&g.red.shift(2,fill_value=False)
    g['pressure_lu']=g.lu.shift(1,fill_value=False)&g.lu.shift(2,fill_value=False)
    g['weak']=(g.close<g.open)&(g.close<g.low.shift(1))
    ma=g.close.rolling(20).mean(); sd=g.close.rolling(20).std()
    ub=ma+2*sd
    above=(g.close>ub)
    # 布林第一根: T-2(第一根大紅K)是過去10日內第一次收破上軌
    g['bb_first']=above.shift(2,fill_value=False)&(above.shift(3).rolling(10).sum().shift(0)==0)
    g['bb_first']=g['bb_first'].fillna(False)
    # T+1 資訊(進場)
    g['o1']=g.open.shift(-1); g['pc1']=g.close
    g['o1_lu']=g.o1>=g.close*1.095   # 隔日開盤漲停→無法放空
    g['o1_ld']=g.o1<=g.close*0.905   # 開盤跌停→不得放空(平盤以下不能空/跌停)
    for h in (1,3,5): g[f'x{h}']=g.close.shift(-h)
    g['sid']=sid
    out.append(g)
df=pd.concat(out)
df=df[df.d>='2025-01-02'].copy()
df['d']=pd.to_datetime(df.d); sh['d']=pd.to_datetime(sh.d)
# shares as-of <=T (strict)
df=df.sort_values('d'); sh=sh.sort_values('d')
strict=pd.merge_asof(df,sh.rename(columns={'s':'sh_strict'})[['stock_id','d','sh_strict']].rename(columns={'stock_id':'sid'}),on='d',by='sid',direction='backward')
first=sh.sort_values('d').groupby('stock_id').s.first().rename('sh_first')
strict=strict.merge(first,left_on='sid',right_index=True,how='left')
strict['sh_bf']=strict.sh_strict.fillna(strict.sh_first)
strict['to_s']=strict.volume/strict.sh_strict
strict['to_b']=strict.volume/strict.sh_bf
b0=bench.close.rename('b_c'); bo=bench.open.rename('b_o')
bd=pd.DataFrame({'b_o':bench.open,'b_c':bench.close}).sort_index(); bd.index=pd.to_datetime(bd.index)
for h in (1,3,5): bd[f'bx{h}']=bd.b_c.shift(-h)
bd['bo1']=bd.b_o.shift(-1)
strict=strict.merge(bd[['bo1','bx1','bx3','bx5']],left_on='d',right_index=True,how='left')
for h in (1,3,5):
    r=strict[f'x{h}']/strict.o1-1
    br=strict[f'bx{h}']/strict.bo1-1
    strict[f'short{h}']=-r-COST          # 絕對
    strict[f'ex{h}']=-r+br-COST          # 對沖0050多單後
strict['ok']=~(strict.o1_lu|strict.o1_ld)&strict.o1.notna()
strict.to_pickle('/private/tmp/claude-501/-Users-jackm4-goldenstocks/2c515d88-c5d7-5669-979c-aefadf5261d9/scratchpad/ev.pkl')

def summ(sub,label,col,h):
    s=sub.dropna(subset=[f'{col}{h}'])
    n=len(s)
    if n==0: return dict(set=label,h=h,col=col,n=0)
    x=s[f'{col}{h}']
    dm=s.groupby('d')[f'{col}{h}'].mean()
    t=dm.mean()/(dm.std(ddof=1)/np.sqrt(len(dm))) if len(dm)>2 and dm.std()>0 else np.nan
    return dict(set=label,h=h,col=col,n=n,dates=len(dm),stocks=s.sid.nunique(),mean=x.mean(),median=x.median(),win=(x>0).mean(),t_date=t)
rows=[]
def run(name,mask_fn,to):
    base=strict[strict.ok]
    m=mask_fn(base,to)
    sub=base[m]
    for h in (1,3,5):
        for col in ('short','ex'):
            rows.append(summ(sub,name,col,h))
    return sub
T='to_s'
specs={}
def full(b,to): return (b[to]>0.30)&b.pressure&b.weak
def full_lu(b,to): return (b[to]>0.30)&b.pressure_lu&b.weak
def full_nobb(b,to): return (b[to]>0.30)&b.pressure&b.weak&~b.bb_first
def full_bb(b,to): return (b[to]>0.30)&b.pressure&b.weak&b.bb_first
def to_only(b,to): return (b[to]>0.30)
def to_p(b,to): return (b[to]>0.30)&b.pressure
def p_only(b,to): return b.pressure
def p_weak(b,to): return b.pressure&b.weak
def to_weak(b,to): return (b[to]>0.30)&b.weak
def weak_only(b,to): return b.weak
def allb(b,to): return b[to].notna()|True
for tag,to in (('STRICT','to_s'),('BACKFILL','to_b')):
    for nm,f in [('①②③全條件(大紅K)',full),('①②③全條件(連2漲停)',full_lu),('全條件且排除布林第一根',full_nobb),('全條件但屬布林第一根',full_bb),
                 ('僅①周轉>30%',to_only),('①+②',to_p),('②+③(不限周轉)',p_weak),('①+③',to_weak),('僅②',p_only),('僅③',weak_only)]:
        if tag=='STRICT' and nm in ('僅②','僅③','②+③(不限周轉)'): pass
        run(f'{tag}|{nm}',f,to)
res=pd.DataFrame(rows)
res.to_csv('/private/tmp/claude-501/-Users-jackm4-goldenstocks/2c515d88-c5d7-5669-979c-aefadf5261d9/scratchpad/res.csv',index=False)
# 全市場基準(ok 全樣本)
base=strict[strict.ok]
rows2=[summ(base,'ALL(基準)',c,h) for h in (1,3,5) for c in ('short','ex')]
pd.DataFrame(rows2).to_csv('/private/tmp/claude-501/-Users-jackm4-goldenstocks/2c515d88-c5d7-5669-979c-aefadf5261d9/scratchpad/base.csv',index=False)
# 覆蓋
print('rows',len(strict),'dates',strict.d.min(),strict.d.max())
print('strict sh avail',strict.sh_strict.notna().mean(), 'bf',strict.sh_bf.notna().mean())
for tag,to in (('STRICT','to_s'),('BACKFILL','to_b')):
    b=strict[strict.ok]
    print(tag,'to>30%',(b[to]>0.30).sum(),'to>30&pressure',((b[to]>0.30)&b.pressure).sum(),'full',full(b,to).sum(),'noopen-excl full',full(strict,to).sum())
print('not ok among full BF', (full(strict,'to_b')&~strict.ok).sum())
print('weak stocks with T+1 open-limit-up among full BF', (full(strict,'to_b')&strict.o1_lu).sum())
pd.set_option('display.width',250,'display.max_rows',500)
print(res[res.col=='ex'].round(4).to_string()); print(pd.DataFrame(rows2).round(4))
