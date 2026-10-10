"""후보 생성 회귀 점검: 정답(라벨러) 근거 문장이 후보에 드는 비율과, 그 후보를 LLM이 인용했다고 가정했을 때 규칙 후처리가 받아 주는 비율.
사용: python eval/heldout5/recall_check.py <exports.json> <gold.csv>"""
import csv, json, re, sys
sys.path.insert(0, ".")
sys.path.insert(0, "eval/heldout5")
import harness as H
from app.pipeline.retrieve import ChunkIndex

def grams(s):
    s = re.sub(r"\W", "", s); return {s[i:i+2] for i in range(len(s)-1)} or {s}

exp_path, gold_path = sys.argv[1], sys.argv[2]
d = json.load(open(exp_path, encoding="utf-8"))
gold = list(csv.DictReader(open(gold_path, encoding="utf-8-sig")))
n_inc = n_hit = n_in = n_ok = 0; misses = []
for pid, exp in d.items():
    docs = H.docs_of(exp); H.INDEX = index = ChunkIndex(docs)
    claims = H.claims_of(exp, index); cmap = H.candidates(claims, index)
    used = set()
    for cl in claims:
        best, bs = None, 0
        for g in gold:
            if id(g) in used: continue
            sc = len(grams(cl["text"]) & grams(g["claim_text"][:34])) / max(1, min(len(grams(cl["text"])), len(grams(g["claim_text"][:34]))))
            if sc > bs: bs, best = sc, g
        if not best or bs < 0.6: continue
        used.add(id(best)); g = best
        if g["decision"] != "include" or g["basis"] != "있음" or not g["gold_evidence_quote"].strip(): continue
        n_inc += 1
        hit = H.locate(index, g["gold_evidence_quote"])
        if not hit: continue
        n_hit += 1
        cands = cmap[cl["id"]]
        hq = re.sub(r"\s", "", hit["quote"])[:20]
        ec = next((c for c in cands if (c["doc_id"], c["chunk"]) == (hit["doc_id"], hit["chunk"]) or (hq and hq in re.sub(r"\s", "", c["quote"]))), None)
        if not ec:
            misses.append((pid, cl["id"], cl["text"][:40], "후보없음")); continue
        n_in += 1
        sim = H.simulate(cl, cands, [ec])
        if sim["basis_state"] == "있음": n_ok += 1
        else: misses.append((pid, cl["id"], cl["text"][:40], "규칙거부: " + (sim.get("note") or "")[:50]))
print(f"정답 '있음'(인용 있음) {n_inc}건 / 인용 문장 찾음 {n_hit} / 후보에 포함 {n_in} / 후처리 통과 {n_ok}")
for m in misses: print("  -", m)
