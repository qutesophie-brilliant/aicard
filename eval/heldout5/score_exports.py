"""export 묶음(카드)을 다수결 정답으로 채점한다. 사용: python score_exports.py <exports.json> <gold.csv> [--by-text]"""
import csv, json, re, sys
M={'근거 확인':'있음','조건 확인':'있음','자료 부족':'없음','추가 확인 필요':'어긋남'}
def grams(s):
    s=re.sub(r"\W","",s); return {s[i:i+2] for i in range(len(s)-1)} or {s}
d=json.load(open(sys.argv[1],encoding='utf-8')); gold=list(csv.DictReader(open(sys.argv[2],encoding='utf-8-sig')))
by_text='--by-text' in sys.argv
G={(g['pid'],g['claim_id']):g for g in gold}
rows=[]; used=set()
for pid,exp in d.items():
    for c in exp['card']['claims']:
        if by_text:
            best,bs=None,0
            for g in gold:
                if id(g) in used: continue
                sc=len(grams(c['text'])&grams(g['claim_text'][:34]))/max(1,min(len(grams(c['text'])),len(grams(g['claim_text'][:34]))))
                if sc>bs: bs,best=sc,g
            if not best or bs<0.6: continue
            used.add(id(best)); g=best
        else:
            g=G.get((pid,c['id']))
            if not g: continue
        if g['decision']!='include': continue
        rows.append((g,M[c['status']],bool((c.get('conditions') or '').strip()),c))
n=len(rows); ok=sum(g['basis']==b for g,b,cd,c in rows)
both=[(g,cd) for g,b,cd,c in rows if g['basis']=='있음' and b=='있음']; cok=sum((g['conditions_noted']=='예')==cd for g,cd in both)
over=[f"{g['pid']}:{g['claim_id']}" for g,b,cd,c in rows if g['basis']=='없음' and b=='있음']
under=[f"{g['pid']}:{g['claim_id']}" for g,b,cd,c in rows if g['basis']=='있음' and b!='있음']
other=[f"{g['pid']}:{g['claim_id']}({g['basis']}→{b})" for g,b,cd,c in rows if g['basis']!=b and g['basis'] not in('있음','없음')]
print(f"채점 대상 {n} / 축1 {ok}/{n} = {ok/n*100:.1f}% / 축2 {cok}/{len(both)} = {cok/len(both)*100:.1f}% / 과대 {len(over)} {over} / 보수 {len(under)} {under} / 기타 {other}")
