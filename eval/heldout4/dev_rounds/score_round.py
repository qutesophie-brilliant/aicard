"""라운드 결과(pid, claim_id, basis, cond, text 앞부분)를 개발용 정답과 글자 겹침으로 짝지어 채점한다."""
import csv, sys, collections

def norm(s): return "".join(ch for ch in s if ch.isalnum())
def grams(s):
    s=norm(s); return {s[i:i+2] for i in range(len(s)-1)} or {s}

gold=[r for r in csv.DictReader(open("eval/heldout4/gold_final.csv",encoding="utf-8-sig"))]
G=collections.defaultdict(list)
for r in gold: G[r["pid"]].append(r)

rows=[l.rstrip("\n").split("\t") for l in open(sys.argv[1],encoding="utf-8") if l.strip()]
matched=[]; unmatched=[]
used=set()
for pid,cid,basis,cond,text in rows:
    tg=grams(text); best=None; bs=0
    for g in G.get(pid,[]):
        if (pid,g["claim_id"]) in used: continue
        gg=grams(g["claim_text"][:34])
        sc=len(tg&gg)/max(1,min(len(tg),len(gg)))
        if sc>bs: bs, best = sc, g
    if best and bs>=0.6:
        used.add((pid,best["claim_id"])); matched.append((best, basis, cond=="1"))
    else:
        unmatched.append((pid,cid,text[:24]))

rows2=[(g,b,c) for g,b,c in matched if g["decision"]=="include"]
n=len(rows2)
ok=sum(g["basis"]==b for g,b,c in rows2)
both=[(g,c) for g,b,c in rows2 if g["basis"]=="있음" and b=="있음"]
cok=sum((g["conditions_noted"]=="예")==c for g,c in both)
over=[g["pid"]+":"+g["claim_id"] for g,b,c in rows2 if g["basis"]=="없음" and b=="있음"]
under=[g["pid"]+":"+g["claim_id"] for g,b,c in rows2 if g["basis"]=="있음" and b=="없음"]
print(f"짝지은 문구 {len(matched)} (새 문구 {len(unmatched)}, 짝 못 찾은 정답 {len(gold)-len(used)})")
print(f"채점 대상(include) {n}")
print(f"축1 근거상태 {ok}/{n} = {ok/n*100:.1f}%")
if both: print(f"축2 조건표시 {cok}/{len(both)} = {cok/len(both)*100:.1f}%")
print(f"과대(없는데 있다고) {len(over)} {over}")
print(f"보수(있는데 못 찾음) {len(under)} {under}")
if unmatched: print("정답에 없던 새 문구:", unmatched)
