import json,re,glob,collections,sys
from pypinyin import lazy_pinyin
S='/private/tmp/claude-501/-Users-jackm4-goldenstocks/2c515d88-c5d7-5669-979c-aefadf5261d9/scratchpad/'
code2name=json.load(open(S+'code2name.json'))
def clean(n): return re.sub(r'[\*＊\s]|-?KY|\*','',n)
names={}
for c,n in code2name.items():
    k=clean(n)
    if 2<=len(k)<=5 and re.fullmatch(r'[一-鿿]+',k): names.setdefault(k,[]).append(c)
py=collections.defaultdict(list)
for n in names: py[''.join(lazy_pinyin(n))].append(n)
def cands(tok):
    k=''.join(lazy_pinyin(tok)); out=[]
    for n in py.get(k,[]):
        if n!=tok: out.append((n,names[n]))
    return out
if __name__=="__main__":
    if sys.argv[1]=='tok':
        for t in sys.argv[2:]: print(t,cands(t))
        sys.exit()
    files=sys.argv[1:]
    cue=re.compile(r'[買賣空多漲跌股檔張分點公司標的跟]')
    cnt=collections.Counter(); ctx={}
    for f in files:
        for ln_no,ln in enumerate(open(f,encoding='utf-8').read().split("\n"),1):
            for m in re.finditer(r'[一-鿿]{2,4}',ln):
                tok=m.group(0)
                for L in (4,3,2):
                    for i in range(0,len(tok)-L+1):
                        g=tok[i:i+L]
                        if g in names: continue
                        if g in cnt or True:
                            cs=cands(g)
                            if cs:
                                a=ln[max(0,m.start()+i-6):m.start()+i+L+6]
                                if cue.search(a):
                                    cnt[(g,)]+=1; ctx.setdefault((g,),(f.split('/')[-1],ln_no,a))
    for (g,),n in cnt.most_common(400):
        print(g,n,cands(g)[:3],ctx[(g,)])
